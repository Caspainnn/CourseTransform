from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm
from docx.table import Table
from docx.text.paragraph import Paragraph

from .nine_section import (
    _asset_caption,
    _fill_table,
    _image_placements,
    _set_cell_text,
    _set_para_text,
    build_nine_section_data,
)
from .template import TemplateSpec
from .textbook import Chapter


SIX_LABELS = ["课程基本信息", "课程简介", "教学内容", "案例教学实施过程", "课堂小结与学习评价", "教学效果及反思"]


def is_six_section(spec: TemplateSpec) -> bool:
    if spec.kind != "docx" or spec.doc is None:
        return False
    from .utils import normalize_label

    labels = []
    for p in spec.doc.paragraphs:
        text = normalize_label(p.text)
        if text in SIX_LABELS or any(text.startswith(lbl) for lbl in SIX_LABELS):
            if text not in labels:
                labels.append(text)
    if len(labels) < 6 or not all(any(x.startswith(lbl) for x in labels) for lbl in SIX_LABELS):
        return False
    if len(spec.doc.tables) != 5:
        return False
    headers = ["|".join(c.text.strip() for c in t.rows[0].cells) for t in spec.doc.tables if t.rows]
    shapes = [(len(t.rows), len(t.columns)) for t in spec.doc.tables]
    return (
        shapes[:5] == [(6, 3), (4, 3), (4, 3), (9, 4), (4, 3)]
        and any(h.startswith("项目|内容|实施安排") for h in headers)
        and any(h.startswith("目标维度|可检测的学习行为|学习证据") for h in headers)
        and any(h.startswith("数据对象|形状或含义|检查要点") for h in headers)
        and any(h.startswith("环节|时间|教师推进|学生任务与检查点") for h in headers)
        and any(h.startswith("观察维度|课后核对依据|实际记录") for h in headers)
    )


def _summary(value: Any, limit: int = 60) -> str:
    """表格单元格只保留必要短句；截断时收在句号/分号，绝不留下省略号。"""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"\[L\d+(?:\s*-\s*L?\d+)?\]", "", text)
    text = re.sub(r"（\s*教材原文[^）]*）", "", text)
    if len(text) <= limit:
        return text
    # 先按完整句/分句累加，保留能放下的完整语义。
    parts = re.split(r"(?<=[。；;])", text)
    out = ""
    for part in parts:
        if len(out) + len(part) <= limit:
            out += part
        else:
            break
    if out.strip():
        out = out.strip()
    else:
        cut = text[:limit]
        pos = max(cut.rfind("。"), cut.rfind("；"), cut.rfind("，"), cut.rfind(" "))
        out = cut[:pos] if pos > 0 else cut
    out = out.rstrip("，；,; ")
    if out and out[-1] not in "。！？":
        out += "。"
    return out


def _table_teacher(name: str, fallback: str = "") -> str:
    n = str(name or "")
    if "复习" in n or "导入" in n:
        return "出示情境，追问本课核心问题"
    if "数据" in n or "核验" in n:
        return "核对数据与标签，指出易混点"
    if "流程" in n or "示范" in n or "讲解" in n:
        return "带读关键步骤，标出参数与边界"
    if "运行" in n or "实践" in n or "操作" in n:
        return "运行短例，记录参数与输出"
    if "对照" in n or "分析" in n or "比较" in n:
        return "组织对照，读图归纳证据"
    if "汇报" in n or "纠错" in n or "展示" in n:
        return "抽组汇报，纠正单点归因"
    if "达标" in n or "检测" in n or "评价" in n:
        return "发退出题，按要点判读"
    if "小结" in n or "迁移" in n or "总结" in n:
        return "总结方法，布置分层作业"
    return _summary(fallback, 30)


def _table_student(name: str, fallback: str = "") -> str:
    n = str(name or "")
    if "复习" in n or "导入" in n:
        return "独立判断，记录关键词"
    if "数据" in n or "核验" in n:
        return "完成标注/配对，同桌互查"
    if "流程" in n or "示范" in n or "讲解" in n:
        return "在任务单标出步骤与更新对象"
    if "运行" in n or "实践" in n or "操作" in n:
        return "运行并记录参数、输出"
    if "对照" in n or "分析" in n or "比较" in n:
        return "比较曲线，填写对照表"
    if "汇报" in n or "纠错" in n or "展示" in n:
        return "解释一项变化和一个局限"
    if "达标" in n or "检测" in n or "评价" in n:
        return "个人作答并提交"
    if "小结" in n or "迁移" in n or "总结" in n:
        return "记录一个待验证问题"
    return _summary(fallback, 28)


def _join(value: Any, sep: str = "；") -> str:
    if isinstance(value, list):
        return sep.join(str(x).strip() for x in value if str(x).strip())
    return str(value or "").strip()


def _textbook_reference(data: dict[str, Any], template_text: str) -> str:
    """“主要资料”必须写教材出处，不能写 Python/PyTorch 环境清单。"""
    book = re.search(r"《([^》]+)》", template_text or "")
    author = re.search(r"([\u4e00-\u9fa5]{2,4}编著)", template_text or "")
    chapter = str(data["meta"].get("chapter_topic", "").splitlines()[0] or data["meta"].get("textbook_range", "")).strip()
    lesson = str(data["meta"].get("lesson_title") or "").strip()
    nums = re.findall(r"\b(\d+\.\d+)\b", " ".join(str(x) for x in (data.get("sections") or [])))
    section = f"{nums[0]}节" if nums else ""
    if book:
        prefix = f"{author.group(1)}" if author else ""
        tail = f"{section}" if section else lesson
        return f"主要资料：{prefix}《{book.group(1)}》，{chapter}，{tail}。"
    return f"主要资料：教材《{chapter}》，{lesson}。"


def _strip_figure_refs(text: str, has_image: bool = True) -> str:
    """正文只保留真实存在的图；不把“教材图3-2/上图2”留成空引用。"""
    text = re.sub(r"教材图\s*\d+\s*[-－]\s*\d+", "教材中的示意图", str(text or ""))
    text = re.sub(r"如?上图\s*\d*\s*所示[，,：:]?", "", text)
    text = re.sub(r"如图\s*\d+\s*所示[，,：:]?", "", text)
    if not has_image:
        text = re.sub(r"(?:出示|展示|播放)[^。；]*(?:示意图|对比图|图片|图像)[^。；]*[。；]", "", text)
    return text


def _exit_ticket(data: dict[str, Any]) -> str:
    """按模板“题1/题2/题3 + 要点 + 每题2分”组织本小节退出题。"""
    title = str((data.get("meta") or {}).get("lesson_title") or "")
    if "投毒" in title:
        qa = [
            ("投毒发生在哪个阶段？", "训练阶段，改的是训练集标签"),
            ("改的是像素还是标签？", "标签，测试集保持干净"),
            ("除准确率外还需哪类证据？", "损失曲线、对照条件与结论局限"),
        ]
    elif "人脸" in title or "活体" in title:
        qa = [
            ("活体检测与普通人脸识别有什么区别？", "判断是否为真人活体，防照片、视频和面具攻击"),
            ("本实验用哪些活体线索或数据？", "NUAA 或自建数据，纹理、眨眼、反射等活体线索"),
            ("只看识别准确率够不够？", "不够，还要看假体漏检、误拒和跨数据集表现"),
        ]
    elif "验证码" in title:
        qa = [
            ("验证码识别的输入和输出分别是什么？", "输入验证码图像，输出字符序列"),
            ("为什么不能只用一个 softmax 分类？", "字符长度和位置不固定，需要序列建模或逐字符多标签"),
            ("评价验证码识别看哪些指标？", "整串准确率、字符错误率、不同噪声和长度的鲁棒性"),
        ]
    elif "随机森林" in title and "去噪" in title:
        qa = [
            ("图像去噪的输入和输出分别是什么？", "输入含噪图像，输出去噪后的图像"),
            ("随机森林去噪要调哪些关键参数？", "树数量、最大深度、采样比例和特征数"),
            ("怎样判断去噪效果？", "PSNR/SSIM、残留噪声与是否过度平滑"),
        ]
    elif "随机森林" in title:
        qa = [
            ("随机森林的“随机”体现在哪两处？", "样本 Bagging 与特征随机选择"),
            ("为什么随机森林能降低过拟合？", "多棵树集成，降低单棵树带来的方差"),
            ("随机森林的主要优缺点各是什么？", "准确稳健、能处理高维；可解释性和计算开销是代价"),
        ]
    elif "对抗样本" in title and ("图像对抗" in title or "实践" in title):
        qa = [
            ("本实验的攻击对象和评价对象是什么？", "图像分类模型，比较原图与对抗样本的预测"),
            ("epsilon 变大时攻击效果和图像质量怎样变化？", "攻击成功率上升，扰动更明显、图像质量下降"),
            ("评估防御效果至少看哪两类证据？", "攻击前后准确率与扰动幅度，并说明迁移性和局限"),
        ]
    elif "对抗样本" in title:
        qa = [
            ("什么是对抗样本？", "在原图上加小扰动，使模型误判的输入"),
            ("FGSM 和 PGD 的核心思想是什么？", "沿梯度方向构造扰动，用 epsilon 控制幅度"),
            ("为什么人眼难察觉却能攻击模型？", "扰动幅度小，但落在模型敏感方向上"),
        ]
    elif "习题" in title:
        qa = [
            ("本章三个实验分别解决什么问题？", "数据投毒、人脸活体检测、验证码识别"),
            ("评价 AI 安全实验至少要看哪两类证据？", "指标/曲线，以及对照条件和局限"),
            ("任选一个实验说明它的安全风险？", "如投毒改训练标签，导致模型把错误标签当正确知识"),
        ]
    elif "卷积" in title:
        qa = [
            ("卷积层主要提取什么特征？", "局部特征，依靠局部连接和权值共享"),
            ("池化层的作用是什么？", "降维、减少参数量并增强平移不变性"),
            ("为什么常用多个 3×3 卷积代替大卷积核？", "同等感受野下参数更少、非线性更强"),
        ]
    else:
        qa = [
            ("本课的核心任务是什么？", title or "完成本小节教学任务"),
            ("完成本课任务的关键步骤是什么？", "按教材步骤操作，记录参数和结果"),
            ("怎样说明结果有效？", "用指标、对照条件和局限共同说明"),
        ]
    parts = [f"题{i}：{q}要点：{a}。" for i, (q, a) in enumerate(qa, start=1)]
    return "".join(parts) + "每题2分，4分达标。"


def _reflection_text(data: dict[str, Any]) -> str:
    """按模板“若……，下次课……”句式写反思，不写成一大段追问。"""
    cases = [
        ("前概念", "若前概念暴露不足，下次课先用诊断性问题摸底，再进入新内容"),
        ("支架", "若任务单支架过细或过粗，下次课按学生产出增删提示"),
        ("像素", "若像素与标签仍混淆，下次课用一组样本做标注纠错"),
        ("测试集", "若训练集/测试集边界不清，下次课先做数据边界核对"),
        ("准确率", "若只看准确率，下次课补损失曲线与对照条件"),
        ("更新对象", "若更新对象仍混乱，下次课用一轮参数追踪复盘"),
        ("参数", "若参数变化看不清，下次课用一轮参数追踪复盘"),
        ("证据", "若结论证据不足，下次课增加对照记录与出口题"),
        ("目标", "若目标达成证据不足，下次课增加课堂观察与出口题"),
        ("时间", "若运行占时，下次课缩减短程步骤并保留结果分析时间"),
        ("运行", "若运行占时，下次课缩减短程步骤并保留结果分析时间"),
    ]
    sentences: list[str] = []
    for item in (data.get("reflection") or [])[:4]:
        risk = str(item.get("risk") or "")
        sentence = next((s for key, s in cases if key in risk), "若课堂观察发现目标未达成，下次课调整对应环节并保留复盘记录")
        if sentence not in sentences:
            sentences.append(sentence)
    if not sentences:
        sentences = ["若课堂观察发现目标未达成，下次课调整对应环节并保留复盘记录"]
    return "反思按真实授课情况填写。" + "；".join(sentences[:3]) + "。"


def build_six_data(
    plan: dict[str, Any],
    chapter: Chapter,
    assets: list[Any] | None = None,
    lesson_index: int = 0,
    lesson_no: int = 1,
) -> dict[str, Any]:
    base = build_nine_section_data(plan, chapter, assets, lesson_index, lesson_no)
    m = base["meta"]
    objectives = base["objectives"]
    rows = []
    for label, key in (("知识目标", "knowledge"), ("能力目标", "method"), ("价值目标", "safety")):
        rows.append([label, _summary("；".join(objectives.get(key, [])[:2]), 48), "课堂追问、任务单与可追溯记录"])
    difficulties = base["difficulties"]
    first = difficulties[0] if difficulties else {}
    data_rows = [
        ["输入与样本", f"{m['chapter_topic'].splitlines()[0]}的数据/案例", "核对来源、形状与数量"],
        ["标签与目标", "真实标签、预测标签或任务目标", "区分特征、标签与预测结果"],
        ["输出与评价", "准确率、损失、图表或任务作品", "至少两类证据，写明条件与局限"],
    ]
    if "投毒" in m.get("lesson_title", ""):
        data_rows = [
            ["MNIST 样本", "(60000, 785) / (10000, 785)；784 像素 + 1 标签", "像素范围 0–255；标签 0–9"],
            ["投毒样本与标签", "按 0% / 50% / 100% 修改训练集标签", "只改训练集；测试集保持干净"],
            ["模型输出与评价", "10 类得分、准确率、损失曲线", "对照比例/学习率，记录参数与曲线"],
        ]
    phases = base["phases"][:8]
    placements = list(base.get("image_placements", []))[:3]
    images = [asset for _, asset in placements]
    lessons = [x for x in (plan.get("lessons") or []) if isinstance(x, dict)]
    sections = [str(x) for x in ((lessons[lesson_index] if lesson_index < len(lessons) else {}).get("sections") or [])]
    return {
        **base,
        "meta": m,
        "course_name": m["course_name"],
        "content": m["chapter_topic"].replace("\n", " / "),
        "audience": m["audience"],
        "method": m.get("lesson_type") or "示范、短程运行、对照研讨",
        "info": m.get("teacher_place") or "教师：____________　时间地点：____________",
        "objective_rows": rows,
        "data_rows": data_rows,
        "phases": phases,
        "images": images,
        "sections": sections,
        "image_placements": placements,
        "first_difficulty": first,
    }


def _set_cell_text_preserve(cell, text: str) -> None:
    _set_cell_text(cell, text)


def _fill_table_rows(table: Table, rows: list[list[str]]) -> None:
    for r, values in enumerate(rows, start=1):
        if r >= len(table.rows):
            break
        for c, value in enumerate(values):
            if c < len(table.rows[r].cells):
                _set_cell_text_preserve(table.rows[r].cells[c], value)


def _replace_picture(p: Paragraph, asset: Any, caption_p: Paragraph | None, base_dir: str | Path | None, number: int = 1) -> None:
    from docx.oxml.ns import qn

    for child in list(p._p):
        if child.tag != qn("w:pPr"):
            p._p.remove(child)
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    try:
        run = p.add_run()
        path = Path(str(getattr(asset, "output_path", "") or getattr(asset, "path", "")))
        if base_dir and not path.is_absolute():
            path = Path(base_dir) / path
        if path.is_file():
            try:
                from PIL import Image

                with Image.open(path) as im:
                    iw, ih = im.size
                max_w, max_h = 10.5, 8.0
                if iw / max(1, ih) > max_w / max_h:
                    width, height = max_w, max_w * ih / max(1, iw)
                else:
                    width, height = max_h * iw / max(1, ih), max_h
                run.add_picture(str(path), width=Cm(width), height=Cm(height))
            except Exception:
                run.add_picture(str(path), width=Cm(9.0))
        else:
            p.add_run(f"（配图缺失：{getattr(asset, 'filename', '')}）")
    except Exception as exc:
        p.add_run(f"（配图插入失败：{exc}）")
    if caption_p is not None:
        _set_para_text(caption_p, f"图{number} {_asset_caption(asset)}")


def _remove_para(p: Paragraph | None) -> None:
    if p is not None:
        try:
            p._p.getparent().remove(p._p)
        except Exception:
            pass


def render_six_section(
    spec: TemplateSpec,
    data: dict[str, Any],
    out_path: str | Path,
    base_dir: str | Path | None = None,
) -> Path:
    """严格按“6 栏正文表格仿宋版”模板原位替换，保留 4 列教学过程表与正文内嵌图。"""
    doc = Document(str(spec.path))
    paras = doc.paragraphs
    tables = doc.tables
    if len(paras) < 59 or len(tables) < 5:
        raise ValueError("模板段落/表格数量与预期不一致，无法按 6 栏模板渲染")

    # 标题与副标题
    _set_para_text(paras[0], data["meta"]["chapter_topic"].splitlines()[0])
    _set_para_text(paras[1], f"第{data['meta'].get('lesson_no', 1)}学时 {data['meta']['lesson_title']}")

    # 表1 课程基本信息（3 列）
    _fill_table_rows(
        tables[0],
        [
            ["课程名称", data["course_name"], "总计32学时"],
            ["授课内容", _summary(data["content"], 46), f"本次1学时，{data['meta'].get('duration_minutes', 45)}分钟"],
            ["教学对象", data["audience"], "已完成先修内容"],
            ["教学方式", data["method"], "两人一组，操作与核查交换"],
            ["授课信息", "教师：____________", "时间地点：____________"],
        ],
    )
    # 表2 教学目标（3 列）
    _fill_table_rows(tables[1], data["objective_rows"])
    # 表3 数据对象（3 列）
    _fill_table_rows(tables[2], data["data_rows"])
    # 表5 反思（保留模板的“目标达成/实践实施/后续改进”三行）
    _fill_table_rows(
        tables[4],
        [
            ["目标达成", "退出题达标情况、核心概念与标签误解", ""],
            ["实践实施", "任务完成、短程运行耗时与个人参与", ""],
            ["后续改进", "单指标误判、对照条件与证据类型", ""],
        ],
    )
    # 表4 教学过程（4 列：环节/时间/教师推进/学生任务与检查点）
    phase_rows = []
    for p in data["phases"][:8]:
        phase_rows.append([
            _summary(p["name"], 14),
            f"{p['minutes']}分钟",
            _table_teacher(p["name"], p["teacher_activity"]),
            _table_student(p["name"], p["student_activity"]),
        ])
    _fill_table_rows(tables[3], phase_rows)

    # 6 栏正文：保留模板的栏目/子标题，只替换正文
    image_count = len(data.get("images") or [])
    p23 = _strip_figure_refs(f"提问：本课的核心对象、标签和评价指标分别是什么？先核验数据，再运行流程。{data['phases'][0]['teacher_activity']}", image_count >= 1)
    p30 = _strip_figure_refs(data["phases"][2]["teacher_activity"] + data["phases"][2]["student_activity"], image_count >= 2)
    p36 = _strip_figure_refs(data["phases"][4]["teacher_activity"] + data["phases"][4]["student_activity"], image_count >= 3)
    if image_count < 3:
        p36 = p36.replace("示范读图", "示范读数").replace("读图", "分析")
    p26 = (
        f"如上图1所示，先确认数据来源、形状与标签，再进入模型流程。{data['phases'][1]['intent']}"
        if image_count >= 1
        else f"先核对数据来源、形状与标签，再进入模型流程。{data['phases'][1]['intent']}"
    )
    p33 = (
        f"如上图2所示，一轮流程的关键是分清每一步更新了谁、保留了哪些量。{data['phases'][2]['intent']}"
        if image_count >= 2
        else f"一轮流程的关键是分清每一步更新了谁、保留了哪些量。{data['phases'][2]['intent']}"
    )
    p34 = (
        f"读图追问：{data['phases'][3]['teacher_activity']} 参考要点：先看输入输出，再看参数更新与评价证据。"
        if image_count >= 2
        else f"结合流程追问：{data['phases'][3]['teacher_activity']} 参考要点：先看输入输出，再看参数更新与评价证据。"
    )
    p39 = (
        f"如上图3所示，结果图要结合数据条件、参数和评价口径一起读；单次结果不能直接外推。{data['phases'][4]['intent']}"
        if image_count >= 3
        else f"结果要结合数据条件、参数和评价口径一起读；单次结果不能直接外推。{data['phases'][4]['intent']}"
    )
    p40 = (
        f"读图要点：{data['phases'][5]['student_activity']}"
        if image_count >= 3
        else f"分析要点：{data['phases'][5]['student_activity']}"
    )
    replacements = {
        3: f"本课的授课内容、教学对象及实施安排如下表1所示；本学时聚焦“{data['meta']['lesson_title']}”。",
        7: f"本课承接前一时段的先修内容，以教材“{data['meta']['chapter_topic'].splitlines()[0]}”为载体，完成“数据—模型—训练—评价”的完整实践，重点放在读懂流程、核对数据与解释结果。",
        9: " ".join(
            [
                "知识：" + "；".join(data["objectives"]["knowledge"][:1]),
                "能力：" + "；".join(data["objectives"]["method"][:1]),
                "价值：" + "；".join(data["objectives"]["safety"][:1]),
            ]
        ),
        10: _textbook_reference(data, paras[10].text),
        13: f"围绕“{data['meta']['lesson_title']}”组织教学：先核对数据与标签，再走通一次最小流程，最后用对照结果说明结论边界。{data['preparation']['environment']}",
        15: "本次教学目标及对应的学习证据如下表2所示，可据此检查知识、能力和价值目标的达成情况。",
        18: (f"重点：{_join(data['key_points'][:3])}。难点：{data['first_difficulty'].get('point', '')}——{data['first_difficulty'].get('stuck', '')}；突破手段：{data['first_difficulty'].get('strategy', '')}。").replace("。；", "；"),
        20: f"{data['preparation']['environment']} 课前完成环境自检，准备已调试 Notebook、同条件记录和离线缓存。",
        22: "1. 问题引入与数据核验",
        23: p23,
        26: p26,
        27: "本课涉及的数据对象、形状或含义及检查要点如下表3所示。",
        29: "2. 关键流程的讲解",
        30: p30,
        33: p33,
        34: p34,
        36: p36,
        39: p39,
        40: p40,
        41: f"课堂任务：{data['phases'][5]['teacher_activity']}",
        44: f"两人一组，按任务单完成操作、记录与交换核查；短运行超时改用同条件缓存，并标明来源。{data['phases'][6]['student_activity']}",
        46: f"本学时共{data['meta'].get('duration_minutes', 45)}分钟，各环节的时间分配、教师活动和学生任务如下表4所示。",
        50: _join(list(data["board"].values()), "；"),
        52: _exit_ticket(data),
        54: f"{_join(data['homework']['practice'])} 评分：{data['homework']['scoring']}",
        56: "课后需要核对的观察维度和记录要求如下表5所示，实际记录应依据真实授课情况填写。",
        58: _reflection_text(data),
    }
    for idx, text in replacements.items():
        if idx < len(paras):
            text = str(text).replace("。。", "。").replace("。；", "；").replace("评分：评分：", "评分：").replace("…", "至")
            _set_para_text(paras[idx], text)

    # 正文内嵌图：模板的三处图位（24/25、31/32、37/38）
    slots = [(24, 25), (31, 32), (37, 38)]
    for i, (img_idx, cap_idx) in enumerate(slots):
        img_p = paras[img_idx] if img_idx < len(paras) else None
        cap_p = paras[cap_idx] if cap_idx < len(paras) else None
        if i < len(data["images"]):
            if img_p is not None:
                _replace_picture(img_p, data["images"][i], cap_p, base_dir, i + 1)
        else:
            _remove_para(img_p)
            _remove_para(cap_p)

    # 模板里部分栏目标题带“段前分页”；生成内容长短不一，保留会留下整页空白。
    # 分页是排版细节，不是教案结构；这里统一取消段前分页，让内容自然接排。
    for p in doc.paragraphs:
        try:
            p.paragraph_format.page_break_before = False
        except Exception:
            pass

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    return out
