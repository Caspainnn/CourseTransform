from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph

from .render import _append_picture_to_cell
from .template import TemplateSpec
from .textbook import Chapter, ground_quote
from .utils import normalize_label


NINE_LABELS = [
    "课程基本信息",
    "教学目标",
    "教学重点与难点",
    "学情分析",
    "教学准备",
    "教学过程",
    "板书设计",
    "作业与思考题",
    "教学反思",
]


def is_nine_section(spec: TemplateSpec) -> bool:
    """识别“教学设计_模板版”：9 个一级栏目 + 5 张固定表。"""
    if spec.kind != "docx" or spec.doc is None:
        return False
    labels = [normalize_label(p.text) for p in spec.doc.paragraphs if p.style and p.style.name == "Heading 2"]
    if not all(any(x.startswith(lbl) for x in labels) for lbl in NINE_LABELS):
        return False
    headers = []
    for table in spec.doc.tables:
        if table.rows:
            headers.append("|".join(c.text.strip() for c in table.rows[0].cells))
    return (
        any(h.startswith("项目|内容") for h in headers)
        and any("难点|学生卡在哪|突破手段|评价证据" in h for h in headers)
        and any("环节|时间|主要内容|教师活动|学生活动|设计意图" in h for h in headers)
        and any(h.startswith("区域|板书内容") for h in headers)
        and any(h.startswith("观察点|预设风险与改进线索|实际记录") for h in headers)
    )


def _clean(text: Any) -> str:
    return re.sub(r"[ \t]+", " ", str(text or "")).strip()


def _lines(values: Any, limit: int = 4) -> list[str]:
    out = []
    for item in values or []:
        text = _clean(item)
        if text:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _quote(chapter: Chapter, quote: str) -> dict[str, Any] | None:
    hit = ground_quote(chapter, quote)
    if not hit:
        return None
    source, start, end = hit
    return {"quote": source, "source_lines": [start, end]}


def _focus_section(chapter: Chapter) -> str:
    for section in chapter.key_sections():
        if "实践" in section.title and ("3.2" in section.title or "投毒" in section.title or "实践 3-1" in section.title):
            return section.title
    for section in chapter.key_sections():
        if "实践" in section.title:
            return section.title
    keys = chapter.key_sections()
    return keys[0].title if keys else chapter.title


def _has_cnn_poisoning(chapter: Chapter) -> bool:
    text = chapter.title + " " + " ".join(s.title for s in chapter.sections)
    return "卷积" in text and "投毒" in text


def _asset_caption(asset: Any) -> str:
    category = str(getattr(asset, "category", "") or "")
    ocr_lines = [x.strip() for x in str(getattr(asset, "ocr_text", "") or "").splitlines() if x.strip()]
    if category == "result_grid":
        return "MNIST 识别结果（True/Pred）"
    if category == "chart":
        for line in ocr_lines[:3]:
            if "投毒比例" in line or "epoch" in line.lower() or "准确率" in line:
                return f"{line} 曲线"[:40]
        return "训练准确率/损失曲线"
    for value in (getattr(asset, "alt", ""), getattr(asset, "hint", ""), getattr(asset, "section", "")):
        text = re.sub(r"^\s*(?:第?\d+(?:\.\d+)*)\s*[、.．:：]?\s*", "", str(value or "")).strip()
        text = re.sub(r"^\s*[\d]+\s*[.．、]\s*", "", text).strip()
        if 4 <= len(text) <= 40:
            return text
    if ocr_lines and 4 <= len(ocr_lines[0]) <= 40:
        return ocr_lines[0]
    return "教材配图"


def _image_placements(assets: list[Any] | None) -> list[tuple[int, Any]]:
    """把筛选后的教材图挂到 8 个环节：图/曲线放“对照分析”，流程图放“流程示范”，照片放“数据核验”。"""
    if not assets:
        return []
    buckets = {
        "review": [],
        "flow": [],
        "analysis": [],
        "application": [],
    }
    for asset in assets:
        category = str(getattr(asset, "category", "") or "")
        if category in {"chart", "result_grid"}:
            buckets["analysis"].append(asset)
        elif category in {"diagram"}:
            buckets["flow"].append(asset)
        elif category in {"photo", "result_screenshot"}:
            buckets["application"].append(asset)
        else:
            buckets["review"].append(asset)
    placements: list[tuple[int, Any]] = []
    # 每类最多 1 张；对照分析允许 2 张（曲线 + 结果拼图），总共最多 4 张。
    for idx, bucket in ((2, "flow"), (1, "review"), (4, "analysis"), (5, "application")):
        if buckets[bucket]:
            placements.append((idx, buckets[bucket].pop(0)))
    if buckets["analysis"]:
        placements.append((4, buckets["analysis"].pop(0)))
    # 每个学时至少两张图：同类图还有剩余时继续挂到“流程/对照”环节。
    if len(placements) < 2:
        for phase, name in ((2, "flow"), (2, "application"), (4, "analysis"), (1, "review")):
            while buckets[name] and len(placements) < 2:
                placements.append((phase, buckets[name].pop(0)))
    return placements


def _assets_for_lesson(assets: list[Any] | None, chapter: Chapter, lesson: dict[str, Any]) -> list[Any]:
    if not assets:
        return []
    titles = [str(x).strip() for x in (lesson.get("sections") or []) if str(x).strip()]
    if not titles:
        return assets
    ranges: list[tuple[int, int]] = []
    keys = chapter.key_sections()
    for title in titles:
        for i, key in enumerate(keys):
            if normalize_label(key.title) == normalize_label(title) or key.title in title or title in key.title:
                start = key.start_line
                end = keys[i + 1].start_line - 1 if i + 1 < len(keys) else chapter.end_line
                ranges.append((start, end))
                break
    if not ranges:
        for title in titles:
            for section in chapter.sections:
                if normalize_label(section.title) == normalize_label(title) or section.title in title or title in section.title:
                    ranges.append((section.start_line, section.end_line))
                    break
    if not ranges:
        return assets
    out = []
    for asset in assets:
        section = str(getattr(asset, "section", "") or "")
        line = getattr(asset, "source_line", None)
        if (line and any(a <= int(line) <= b for a, b in ranges)) or any(
            normalize_label(t) and normalize_label(t) in normalize_label(section) for t in titles
        ):
            out.append(asset)
    return out


def build_nine_section_data(
    plan: dict[str, Any],
    chapter: Chapter,
    assets: list[Any] | None = None,
    lesson_index: int = 0,
    lesson_no: int = 1,
) -> dict[str, Any]:
    """把教案数据映射为模板的 9 栏；CNN 投毒课使用教材 3.2 的专用内容。"""
    meta = plan.get("meta") or {}
    duration = int(meta.get("duration_minutes") or 45)
    lessons = [x for x in (plan.get("lessons") or []) if isinstance(x, dict)]
    lesson = lessons[lesson_index] if lesson_index < len(lessons) else (lessons[0] if lessons else {})
    lesson_text = " ".join(str(x) for x in (lesson.get("sections") or [])) + " " + str(lesson.get("title") or "")
    lesson_sections = [str(x).strip() for x in (lesson.get("sections") or []) if str(x).strip()]
    poison_lesson = "投毒" in lesson_text or any(re.match(r"^3\.2(?:\.|\s|$)", x) for x in lesson_sections)
    if _has_cnn_poisoning(chapter) and poison_lesson:
        data = _cnn_poisoning_data(plan, chapter, duration, lesson_no)
    else:
        data = _generic_data(plan, chapter, duration, lesson_index, lesson_no)
    lesson_assets = list(_assets_for_lesson(assets, chapter, lesson))
    # 每个学时至少两张插图：优先本小节的图；不足时用本章其它教学图补足。
    if assets and len(lesson_assets) < 2:
        for asset in assets:
            if asset not in lesson_assets:
                lesson_assets.append(asset)
            if len(lesson_assets) >= 2:
                break
    data["image_placements"] = _image_placements(lesson_assets)
    return data


def _cnn_poisoning_data(plan: dict[str, Any], chapter: Chapter, duration: int, lesson_no: int = 1) -> dict[str, Any]:
    meta = plan.get("meta") or {}
    textbook = _clean(meta.get("textbook")) or "李剑编著《人工智能安全：原理与实践》"
    course = "人工智能安全原理与实践"
    chapter_title = _clean(meta.get("title")) or chapter.title
    lesson_title = "基于CNN的数据投毒攻击实践"
    citations = []
    for quote in (
        "投毒攻击是一种对机器学习模型的安全攻击方法，特别是在模型训练阶段进行攻击。",
        "本实践主要是在 AlexNet 模型训练过程中对所使用的训练集进行投毒攻击（Poisoning Attack），即更改原始标签，从而降低模型检测的准确率。",
        "要求修改投毒样本的比例, 分别为 0%、50%、100%, 绘制相应的折线图。",
        "训练集有 60000 条数据，测试集有 10000 条数据，每一条数据都是由 785 个数字组成，数值大小 0~255，第一个数字代表该条数据所表示的数字。",
    ):
        item = _quote(chapter, quote)
        if item:
            citations.append({**item, "claim": "支撑本课对投毒攻击、实验比例和 MNIST 数据的讲解"})
    if not citations:
        citations = [c for c in (plan.get("citations") or []) if isinstance(c, dict)][:4]

    return {
        "meta": {
            "course_name": course,
            "chapter_topic": f"{chapter_title}\n第{lesson_no}学时 {lesson_title}",
            "audience": "具备Python和机器学习入门基础的相关专业本科生",
            "hours": f"本次1学时，{duration}分钟",
            "textbook_range": f"{textbook}\n3.2 实践3-1，重点3.2.1—3.2.6",
            "teacher_place": "教师：____________　地点：____________",
            "lesson_title": lesson_title,
            "lesson_no": lesson_no,
            "lesson_type": "实践与结果研讨",
            "bridge": "第2学时 卷积神经网络原理",
            "duration_minutes": duration,
        },
        "objectives": {
            "knowledge": [
                "能解释卷积层、池化层、全连接层的作用，并说明 AlexNet 简化版为何适合 28×28 的 MNIST 图像。",
                "能用自己的话说明数据投毒攻击发生在训练阶段，攻击对象是训练数据及其标签。",
                "能区分 MNIST 样本的 784 个像素与 0–9 类别标签。",
            ],
            "method": [
                "能按教材步骤完成训练集标签修改，设置 0%、50%、100% 三种投毒比例并运行短程训练。",
                "能绘制并比较准确率、损失随 epoch 变化的曲线，记录随机种子、学习率等条件。",
                "能用“条件—现象—解释—局限”写出一条可核查的实验结论。",
            ],
            "safety": [
                "能说明“准确率高”不等于“模型安全”，评价投毒影响至少需要两类证据。",
                "能指出训练数据来源与标签管理中的安全责任，并提出一条可操作的数据核验建议。",
            ],
        },
        "key_points": [
            "投毒攻击的攻击面：训练集标签，而不是测试集或模型结构。",
            "0%、50%、100% 三种比例的对照实验与控制变量。",
            "用准确率和损失曲线共同判断影响，结论必须写明条件与局限。",
        ],
        "difficulties": [
            {"point": "数据与标签", "stuck": "把 28×28 像素矩阵当成标签，或把标签当成图像内容", "strategy": "并列展示像素矩阵与标签字段，做一次标签翻转", "evidence": "能正确指出标签位置并说明改标签的含义"},
            {"point": "投毒比例", "stuck": "看到准确率下降就直接归因于投毒比例", "strategy": "固定随机种子、学习率、epoch，只改比例", "evidence": "能说出对照条件与结果之间的对应关系"},
            {"point": "训练波动", "stuck": "忽略随机种子、学习率和 epoch 的影响", "strategy": "记录参数表，重复一次实验", "evidence": "能指出至少一个可能影响结果的变量"},
            {"point": "安全结论", "stuck": "由单次实验外推“模型不安全”", "strategy": "标注数据来源、运行次数和适用范围", "evidence": "能写出一条带边界的安全结论"},
        ],
        "student": {
            "prior": "已学卷积神经网络与 AlexNet，能使用 Python/PyTorch 运行简单训练脚本。",
            "difficulty": "容易混淆像素与标签；倾向只看准确率；对随机种子、学习率等控制变量不敏感。",
            "diagnosis": "用 0/50/100% 对照和标签翻转追问，观察能否说出“改了什么、控制了什么、证据是什么”。",
            "support": "提供半填代码、预跑缓存和参数表；两人轮换操作；基础组先完成一组对照，进阶组增加学习率比较。",
        },
        "preparation": {
            "materials": "教材3.2节、已调试 Notebook、MNIST 数据集、0/50/100% 三组缓存结果、参数记录表、退出条。",
            "environment": "Python 3.9+、PyTorch 1.7.0、PyCharm、NumPy/Matplotlib/Torchvision；机房两人一组，课前完成环境自检。",
        },
        "phases": [
            {"name": "一 复习诊断", "minutes": 4, "main_content": "CNN/AlexNet 结构与 MNIST 识别；判别率高是否等于模型安全", "teacher_activity": "出示一张被误判的 MNIST 样本，追问：模型在测试集上准确率高，是否说明它对训练数据污染也安全？引导学生回顾卷积层、池化层、全连接层和 softmax 输出。", "student_activity": "独立回答，指出准确率只反映当前测试集；在任务单上写下“数据—模型—评价”三个环节中可能被攻击的位置。", "intent": "暴露“准确率=安全”的前概念，为投毒攻击制造认知冲突。"},
            {"name": "二 数据与标签核验", "minutes": 6, "main_content": "MNIST 的 28×28 像素、60000/10000 划分、0–9 标签；图像与标签的区别", "teacher_activity": "打开一条 MNIST 样本，分别指出 784 个像素与标签字段；演示把标签 0 改为 1，强调改变的是训练集标签而不是像素。", "student_activity": "检查数组形状与标签位置，完成“像素/标签”配对练习，说明投毒改的是哪一部分。", "intent": "先建立数据与标签的准确表征，避免把“改图”与“改标签”混为一谈。"},
            {"name": "三 投毒流程示范", "minutes": 8, "main_content": "训练集投毒流程：选样本、改标签、按 0/50/100% 比例混合、训练、测试", "teacher_activity": "带读教材3.2.4的投毒代码，标出 poison_ratio、标签映射和训练集/测试集边界；追问：为什么只改训练集，不改测试集？", "student_activity": "在代码上标注“选中样本—修改标签—混合—训练—评估”五步，预测比例变化对准确率的影响。", "intent": "把投毒攻击落到可执行的代码流程，明确攻击面在训练数据。"},
            {"name": "四 短程运行", "minutes": 7, "main_content": "运行预调试 Notebook 的 5 个 epoch；超时切换同条件缓存", "teacher_activity": "课前已跑通短程脚本；课堂只改投毒比例，强调固定随机种子、batch size 和学习率；超时直接读取缓存结果，不等待完整训练。", "student_activity": "两人一组，一人操作一人记录；保存 accuracy/loss 曲线和运行参数，标注本机/缓存来源。", "intent": "用最小运行打通流程，把课堂时间留给证据分析而不是等待训练。"},
            {"name": "五 对照分析", "minutes": 10, "main_content": "比较 0%、50%、100% 投毒比例下的准确率/损失曲线；控制学习率等变量", "teacher_activity": "发对照表，示范读图：先看整体趋势，再看拐点与离群；追问“准确率下降能否直接证明投毒成功？”", "student_activity": "填表记录三种比例的最终准确率、曲线形态和异常点；用“条件—现象—解释—局限”写一条结论。", "intent": "训练控制变量和多证据推理，避免单点准确率归因。"},
            {"name": "六 汇报纠错", "minutes": 5, "main_content": "小组汇报与纠错：把“准确率下降”与“标签被污染”建立证据链", "teacher_activity": "抽两组各1分钟汇报，针对“比例越高一定越差”“损失下降说明模型更好”等说法纠错，要求引用曲线和参数。", "student_activity": "其他组用评价量规追问：结论是否有对照？是否排除了学习率和随机种子？记录一条修正。", "intent": "外化思维，纠正单指标推断，形成有边界的结论。"},
            {"name": "七 达标检测", "minutes": 3, "main_content": "3 道出口题：投毒位置、标签变化、证据边界", "teacher_activity": "发退出条，按“位置1分、变化1分、证据1分”快速判读；统计达标情况。", "student_activity": "独立作答：①投毒发生在训练还是测试？②改的是像素还是标签？③除准确率外还需什么证据？", "intent": "检测独立理解，给下一课时提供补偿依据。"},
            {"name": "八 小结迁移", "minutes": 2, "main_content": "数据—标签—训练—评价的安全链条；迁移到人脸活体/验证码", "teacher_activity": "总结“数据可信、标签可信、评价多证据”三条；布置基础/拓展作业，预告3.3人脸活体检测。", "student_activity": "写下一个待验证假设（如改变标签映射或学习率会怎样），提交任务单。", "intent": "结构化本课，衔接下一实践。"},
        ],
        "board": {
            "left": "数据：28×28 像素；标签 0–9\n训练集 60000 / 测试集 10000",
            "middle": "投毒：只改训练集标签\n比例 0% / 50% / 100%\n训练→测试→准确率/损失",
            "right": "证据：准确率曲线 + 损失曲线\n对照：比例、学习率、随机种子",
            "bottom": "准确率下降只是现象；结论要写清条件、证据和局限。",
        },
        "homework": {
            "discussion": [
                "为什么投毒攻击改标签，而不是直接改测试集？",
                "0%、50%、100% 三种比例对照，能说明什么、不能说明什么？",
                "如果准确率下降但损失曲线异常，你会先检查哪三个条件？",
            ],
            "exit_ticket": "3题，每题2分，4分达标：①投毒发生在哪个阶段？②改的是像素还是标签？③除准确率外还需哪类证据？",
            "practice": [
                "基础：复现0%、50%、100%三组实验，保存参数、准确率和损失曲线，写一条带条件的结论。",
                "拓展：改变标签映射（0→1…9→0）或学习率，比较结果；说明单次运行的局限。",
            ],
            "scoring": "评分：数据与标签2分、训练流程3分、图表解释3分、复现边界2分；6分达标。",
        },
        "reflection": [
            {"observation": "数据理解", "risk": "是否混淆像素与标签；能否指出标签在数组中的位置"},
            {"observation": "投毒流程", "risk": "能否说明只改训练集、不改测试集"},
            {"observation": "实践节奏", "risk": "短程运行是否超时；缓存是否支持同等分析"},
            {"observation": "证据判断", "risk": "是否用准确率+损失+对照条件共同下结论"},
        ],
        "citations": citations,
    }


def _generic_data(
    plan: dict[str, Any],
    chapter: Chapter,
    duration: int,
    lesson_index: int = 0,
    lesson_no: int = 1,
) -> dict[str, Any]:
    meta = plan.get("meta") or {}
    lessons = [x for x in (plan.get("lessons") or []) if isinstance(x, dict)]
    lesson = lessons[lesson_index] if lesson_index < len(lessons) else (lessons[0] if lessons else {})
    lesson_sections = [str(x) for x in (lesson.get("sections") or []) if str(x).strip()]
    focus = lesson_sections[0] if lesson_sections else _focus_section(chapter)
    title = _clean(lesson.get("title")) or _clean(meta.get("title")) or chapter.title
    title = re.sub(r"（第[一二三四五六七八九十\d]+课时）\s*$", "", title).strip()
    title = re.sub(r"^第[一二三四五六七八九十\d]+课时[：:\s]*", "", title).strip()
    if len(lessons) > 1 and (not _clean(lesson.get("title")) or re.search(r"第[一二三四五六七八九十\d]+课时$", title)):
        title = focus
    objectives = [o for o in (plan.get("objectives") or []) if isinstance(o, dict)]

    def group(domains: tuple[str, ...]) -> list[str]:
        return [_clean(o.get("statement")) for o in objectives if any(d in _clean(o.get("domain")) for d in domains) and _clean(o.get("statement"))]

    knowledge = group(("知识", "理解"))
    method = group(("技能", "思维", "应用", "分析"))
    safety = group(("价值", "评价"))
    if not knowledge:
        knowledge = [_clean(o.get("statement")) for o in objectives[:2] if _clean(o.get("statement"))]
    if not method:
        method = [_clean(o.get("statement")) for o in objectives[2:4] if _clean(o.get("statement"))] or ["能按教材步骤完成实践任务，并记录关键参数与结果。"]
    if not safety:
        safety = ["能说明技术应用的边界，并对数据来源和结论适用范围负责。"]

    difficulties = []
    for item in plan.get("difficulties") or []:
        if isinstance(item, dict):
            difficulties.append({
                "point": _clean(item.get("point")),
                "stuck": _clean(item.get("stuck")) or "容易停留在操作步骤，说不清判断依据。",
                "strategy": _clean(item.get("strategy")) or "通过对照任务和追问暴露思维过程。",
                "evidence": _clean(item.get("evidence")) or "课堂追问与任务单。",
            })
    while len(difficulties) < 4:
        difficulties.append({"point": "证据判断", "stuck": "由单次结果直接外推结论。", "strategy": "记录条件并做对照。", "evidence": "能说出条件与局限。"})

    phases = [p for p in (lesson.get("phases") or []) if isinstance(p, dict)]
    if len(phases) < 8:
        src = phases or [{}]
        expanded = []
        for i in range(8):
            base = src[min(len(src) - 1, int(i * len(src) / 8))]
            expanded.append({
                "name": f"{['一','二','三','四','五','六','七','八'][i]} {('复习诊断','数据核验','流程示范','短程运行','对照分析','汇报纠错','达标检测','小结迁移')[i]}",
                "minutes": [4, 6, 8, 7, 10, 5, 3, 2][i],
                "main_content": _clean(base.get("name")) or "本环节核心任务",
                "teacher_activity": _clean(base.get("teacher_activity")) or "教师组织任务、巡视并追问。",
                "student_activity": _clean(base.get("student_activity")) or "学生完成任务并记录结果。",
                "intent": _clean(base.get("intent")) or "支撑本环节目标。",
            })
        phases = expanded
    phases = phases[:8]
    total = sum(int(p.get("minutes") or 0) for p in phases)
    if total != duration:
        phases[0]["minutes"] = max(1, int(phases[0].get("minutes") or 0) + duration - total)

    board = plan.get("board_design") or {}
    board_content = [_clean(x) for x in (board.get("content") or []) if _clean(x)]
    if isinstance(board, dict) and isinstance(board.get("regions"), dict):
        regions = {k: _clean(v) for k, v in board["regions"].items()}
    else:
        regions = {
            "left": "\n".join(board_content[:2]),
            "middle": "\n".join(board_content[2:4]),
            "right": "\n".join(board_content[4:6]),
            "bottom": board_content[-1] if board_content else "结论要写明条件、证据和局限。",
        }
    board = {
        "left": regions.get("left") or "核心概念",
        "middle": regions.get("middle") or "关键流程",
        "right": regions.get("right") or "证据与评价",
        "bottom": regions.get("bottom") or "结论要写明条件、证据和局限。",
    }

    hw = plan.get("homework") or []
    practice = []
    for item in hw[:4]:
        if isinstance(item, dict):
            practice.append(f"{_clean(item.get('level')) or '作业'}：{_clean(item.get('task'))}")
    homework = {
        "discussion": [
            "本课的核心概念是什么？它解决了什么问题？",
            "哪些证据可以支持你的结论？还缺少什么对照？",
            "如果换一个条件，结果可能怎样变化？",
        ],
        "exit_ticket": "用 3 句话写出：本课的核心概念、一个证据、一个仍未解决的问题。",
        "practice": practice or ["完成教材对应练习，并记录关键步骤与结果。"],
        "scoring": "评分：概念准确2分、证据充分3分、表达清楚2分、边界说明1分；6分达标。",
    }

    reflections = []
    for item in plan.get("reflection") or []:
        text = _clean(item)
        if text:
            reflections.append({"observation": text[:12], "risk": text})
    while len(reflections) < 4:
        reflections.append({"observation": "证据判断", "risk": "是否用多类证据下结论，并说明局限。"})
    return {
        "meta": {
            "course_name": _clean(meta.get("subject")) or "信息科技",
            "chapter_topic": f"{_clean(meta.get('title')) or chapter.title}\n第{lesson_no}学时 {title}",
            "audience": _clean(meta.get("grade")) or "本科生",
            "hours": f"本次1学时，{duration}分钟",
            "textbook_range": _clean(meta.get("textbook")) or chapter.title,
            "teacher_place": "教师：____________　地点：____________",
            "lesson_title": title,
            "lesson_no": lesson_no,
            "lesson_type": _clean(meta.get("lesson_type")) or "新授课",
            "bridge": "上一学时相关内容",
            "duration_minutes": duration,
        },
        "objectives": {"knowledge": knowledge, "method": method, "safety": safety},
        "key_points": [_clean(x) for x in (plan.get("key_points") or []) if _clean(x)][:3] or ["本课核心概念", "关键方法", "证据判断"],
        "difficulties": difficulties[:4],
        "student": {
            "prior": _clean((plan.get("analysis") or {}).get("status_quo")) or "学生已具备本课前的基础知识。",
            "difficulty": "容易停留在操作步骤，说不清判断依据。",
            "diagnosis": "通过追问和任务单观察学生的证据意识。",
            "support": _clean((plan.get("differentiation") or {}).get("support")) or "提供半填任务单和同伴支持。",
        },
        "preparation": {
            "materials": "；".join(_clean(x) for x in (plan.get("materials") or []) if _clean(x)) or "教材、课件、任务单。",
            "environment": "多媒体机房，课前完成环境自检。",
        },
        "phases": phases,
        "board": board,
        "homework": homework,
        "reflection": reflections[:4],
        "citations": [c for c in (plan.get("citations") or []) if isinstance(c, dict)][:4],
    }


# ---------------------------------------------------------------------------
# Render: keep the uploaded template's exact sections, styles and tables
# ---------------------------------------------------------------------------


def _body_children(doc: Document) -> list[Any]:
    out = []
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            out.append(Paragraph(child, doc))
        elif child.tag == qn("w:tbl"):
            out.append(Table(child, doc))
    return out


def _section_map(doc: Document) -> list[dict[str, Any]]:
    blocks = _body_children(doc)
    sections: list[dict[str, Any]] = []
    current = None
    for block in blocks:
        if isinstance(block, Paragraph) and block.style and block.style.name == "Heading 2":
            if current:
                sections.append(current)
            current = {"heading": block, "body": []}
        elif current is not None:
            current["body"].append(block)
    if current:
        sections.append(current)
    return sections


def _set_para_text(p: Paragraph, text: str) -> None:
    """清空文字但保留段落样式、编号和首个 run 的字符格式。"""
    rpr = None
    for r in p.runs:
        if r._r.rPr is not None:
            rpr = copy.deepcopy(r._r.rPr)
            break
    for child in list(p._p):
        if child.tag != qn("w:pPr"):
            p._p.remove(child)
    paragraphs = str(text).split("\n")
    for i, line in enumerate(paragraphs):
        if i:
            p.add_run().add_break()
        run = p.add_run(line)
        if rpr is not None:
            run._r.insert(0, copy.deepcopy(rpr))


def _clone_para(sample: Paragraph, text: str) -> Any:
    p = copy.deepcopy(sample._p)
    rpr = None
    for r in sample.runs:
        if r._r.rPr is not None:
            rpr = copy.deepcopy(r._r.rPr)
            break
    for child in list(p):
        if child.tag != qn("w:pPr"):
            p.remove(child)
    p_obj = Paragraph(p, sample._parent)
    for i, line in enumerate(str(text).split("\n")):
        if i:
            p_obj.add_run().add_break()
        run = p_obj.add_run(line)
        if rpr is not None:
            run._r.insert(0, copy.deepcopy(rpr))
    return p


def _set_cell_text(cell: _Cell, text: str) -> None:
    paragraphs = cell.paragraphs
    sample_p = paragraphs[0]
    rpr = None
    if sample_p.runs and sample_p.runs[0]._r.rPr is not None:
        rpr = copy.deepcopy(sample_p.runs[0]._r.rPr)
    for extra in paragraphs[1:]:
        extra._p.getparent().remove(extra._p)
    p = cell.paragraphs[0]
    for child in list(p._p):
        if child.tag != qn("w:pPr"):
            p._p.remove(child)
    lines = str(text).split("\n")
    for i, line in enumerate(lines):
        target = p if i == 0 else cell.add_paragraph()
        if i > 0:
            target.paragraph_format.space_before = None
            target.paragraph_format.space_after = None
        run = target.add_run(line)
        if rpr is not None:
            run._r.insert(0, copy.deepcopy(rpr))


def _fill_table(sample: Table, rows: list[list[str]], header_rows: int = 1) -> Any:
    tbl = copy.deepcopy(sample._tbl)
    trs = list(tbl.tr_lst)
    if len(trs) <= header_rows:
        return tbl
    data_template = copy.deepcopy(trs[header_rows])
    for tr in trs[header_rows:]:
        tbl.remove(tr)
    for row in rows:
        tr = copy.deepcopy(data_template)
        tbl.append(tr)
        tr_obj = next((x for x in Table(tbl, sample._parent).rows if x._tr is tr), None)
        if tr_obj is None:
            continue
        for i, value in enumerate(row):
            if i < len(tr_obj.cells):
                _set_cell_text(tr_obj.cells[i], value)
    return tbl


def render_nine_section(
    spec: TemplateSpec,
    data: dict[str, Any],
    out_path: str | Path,
    base_dir: str | Path | None = None,
) -> Path:
    doc = Document(str(spec.path))
    blocks = _body_children(doc)

    # 标题：模板是一个 Heading 1 段落，中间用换行分“第3学时 …”和“教学设计”。
    title_p = next((p for p in doc.paragraphs if p.style and p.style.name == "Heading 1"), None)
    if title_p is not None:
        lesson_no = data["meta"].get("lesson_no", 1)
        _set_para_text(title_p, f"第{lesson_no}学时 {data['meta']['lesson_title']}\n教学设计")
    subtitle = next((p for p in doc.paragraphs if "课型" in p.text), None)
    if subtitle is not None:
        _set_para_text(
            subtitle,
            f"课型：{data['meta']['lesson_type']}　　课时：{data['meta']['duration_minutes']}分钟　　"
            f"衔接：{data['meta']['bridge']}",
        )

    sections = _section_map(doc)
    for sec in sections:
        heading = sec["heading"]
        body = sec["body"]
        label = normalize_label(heading.text)
        h3_sample = next((b for b in body if isinstance(b, Paragraph) and b.style and b.style.name == "Heading 3"), None)
        bullet_sample = next((b for b in body if isinstance(b, Paragraph) and b.style and b.style.name == "List Paragraph"), None)
        normal_sample = next((b for b in body if isinstance(b, Paragraph) and not (b.style and b.style.name.startswith("Heading"))), None)
        table_sample = next((b for b in body if isinstance(b, Table)), None)
        if bullet_sample is None:
            bullet_sample = normal_sample
        if normal_sample is None:
            normal_sample = bullet_sample
        if label.startswith(("教学目标", "教学重点与难点")) and h3_sample is None:
            continue
        if table_sample is None and normal_sample is None:
            continue
        items: list[Any] = []
        if label.startswith("课程基本信息") and table_sample is not None:
            m = data["meta"]
            rows = [
                ["课程名称", m["course_name"]],
                ["章节与主题", m["chapter_topic"]],
                ["授课对象", m["audience"]],
                ["课时安排", m["hours"]],
                ["教材与范围", m["textbook_range"]],
                ["授课教师／地点", m["teacher_place"]],
            ]
            items.append(("table", table_sample, rows))
        elif label.startswith("教学目标"):
            for h3, values in (
                ("1. 知识与理解", data["objectives"]["knowledge"]),
                ("2. 方法与能力", data["objectives"]["method"]),
                ("3. 安全判断意识", data["objectives"]["safety"]),
            ):
                items.append(("para", h3_sample, h3))
                for value in values[:3]:
                    items.append(("para", bullet_sample, value))
        elif label.startswith("教学重点与难点"):
            items.append(("para", h3_sample, "重点"))
            for value in data["key_points"][:3]:
                items.append(("para", bullet_sample, value))
            items.append(("para", h3_sample, "难点与突破策略"))
            if table_sample is not None:
                items.append(("table", table_sample, [[d["point"], d["stuck"], d["strategy"], d["evidence"]] for d in data["difficulties"][:4]]))
        elif label.startswith("学情分析"):
            s = data["student"]
            for prefix, key in (("已有基础：", "prior"), ("困难：", "difficulty"), ("诊断：", "diagnosis"), ("支持：", "support")):
                items.append(("para", bullet_sample, prefix + s[key]))
        elif label.startswith("教学准备"):
            p = data["preparation"]
            items.append(("para", bullet_sample, "材料：" + p["materials"]))
            items.append(("para", bullet_sample, "环境：" + p["environment"]))
        elif label.startswith("教学过程"):
            items.append(("para", normal_sample, "本课共45分钟。教学流程、师生活动与设计意图如下表所示。"))
            if table_sample is not None:
                items.append(("table", table_sample, [[p["name"], f"{p['minutes']}分钟", p["main_content"], p["teacher_activity"], p["student_activity"], p["intent"]] for p in data["phases"][:8]]))
        elif label.startswith("板书设计"):
            b = data["board"]
            if table_sample is not None:
                items.append(("table", table_sample, [["左侧", b["left"]], ["中间", b["middle"]], ["右侧", b["right"]], ["底部一行", b["bottom"]]]))
        elif label.startswith("作业与思考题"):
            hw = data["homework"]
            items.append(("para", normal_sample, "课堂讨论与检测："))
            for i, q in enumerate(hw["discussion"][:3], start=1):
                items.append(("para", bullet_sample, f"{i}. {q}"))
            items.append(("para", normal_sample, "退出题：" + hw["exit_ticket"]))
            items.append(("para", normal_sample, "课后实践与评价："))
            for value in hw["practice"][:3]:
                items.append(("para", bullet_sample, value))
            items.append(("para", bullet_sample, hw["scoring"]))
        elif label.startswith("教学反思"):
            if table_sample is not None:
                items.append(("table", table_sample, [[r["observation"], r["risk"], ""] for r in data["reflection"][:4]]))
            if normal_sample is not None:
                items.append(("para", normal_sample, ""))

        # 删除原栏目正文，按模板样式重建
        for old in body:
            old._element.getparent().remove(old._element)
        anchor = heading._p
        for kind, sample, value in items:
            if sample is None:
                continue
            el = _clone_para(sample, value) if kind == "para" else _fill_table(sample, value)
            anchor.addnext(el)
            anchor = el
            if label.startswith("教学过程") and kind == "table" and data.get("image_placements"):
                table_obj = Table(el, doc)
                for phase_idx, asset in data["image_placements"]:
                    row_idx = 1 + int(phase_idx)
                    if row_idx >= len(table_obj.rows):
                        continue
                    fig = asset.to_dict() if hasattr(asset, "to_dict") else dict(asset)
                    fig["caption"] = _asset_caption(asset)
                    _append_picture_to_cell(table_obj.rows[row_idx].cells[3], fig, base_dir, 3.2)

    # 教材引用核对：不追加新栏目，避免改变模板结构；引用只用于内部质检。
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    return out
