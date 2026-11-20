from __future__ import annotations

import re
from typing import Any

from .textbook import Chapter, estimate_periods, ground_quote, split_sentences
from .utils import char_count, normalize_label

PHASE_TEMPLATES = {
    "first": [
        ("情境导入", 5, "激活经验"),
        ("新知探究", 12, "建立概念"),
        ("任务实践", 15, "迁移应用"),
        ("交流评价", 8, "外化思维"),
        ("总结作业", 5, "结构化"),
    ],
    "later": [
        ("复习导入", 5, "衔接旧知"),
        ("深入探究", 12, "突破难点"),
        ("综合任务", 18, "综合运用"),
        ("展示评价", 6, "评价改进"),
        ("总结提升", 4, "迁移反思"),
    ],
}


def build_offline_plan(
    chapter: Chapter,
    brief: dict[str, Any],
    periods: int | None = None,
    custom_labels: list[str] | None = None,
    blueprints: list[Any] | None = None,
) -> dict[str, Any]:
    subject = _s(brief.get("subject")) or "信息科技"
    grade = _s(brief.get("grade")) or "八年级"
    textbook = _s(brief.get("textbook")) or ""
    lesson_type = _s(brief.get("lesson_type")) or "新授课"
    duration = _i(brief.get("duration_minutes"), 45)
    periods = int(periods or brief.get("periods") or estimate_periods(chapter))
    if blueprints:
        periods = len(blueprints)
    periods = max(1, min(6, periods))
    sections = chapter.key_sections() or chapter.sections or []
    titles = [s.title for s in sections] or [chapter.title]

    def short_title(text: str) -> str:
        return re.sub(r"^第?[一二三四五六七八九十百零〇\d.]+\s*[章节]?\s*", "", text).strip() or text

    display = [short_title(t) for t in titles]
    objectives = [
        {
            "id": "O1",
            "domain": "知识",
            "bloom": "理解",
            "statement": f"能用自己的话解释“{display[0]}”中的核心概念，并准确说出至少 2 个关键词。",
            "evidence": "课堂提问回答 + 概念图/关键词卡片",
        },
        {
            "id": "O2",
            "domain": "思维",
            "bloom": "分析",
            "statement": f"能结合教材案例，比较“{display[min(1, len(display) - 1)]}”中的关键差异，归纳出一般步骤或规律。",
            "evidence": "小组任务单中的对比表与归纳结论",
        },
        {
            "id": "O3",
            "domain": "价值",
            "bloom": "评价",
            "statement": f"能联系“{display[-1]}”，说明该内容对个人或社会的影响，并提出一条负责任的应用建议。",
            "evidence": "课堂发言与出口条中的建议",
        },
    ]

    lessons: list[dict[str, Any]] = []
    if blueprints:
        by_title = {s.title: s for s in chapter.sections}
        lesson_specs = []
        for bp in blueprints:
            group = [by_title[t] for t in getattr(bp, "sections", []) if t in by_title]
            if not group:
                group = sections
            lesson_specs.append((getattr(bp, "title", ""), list(getattr(bp, "sections", []) or [s.title for s in group]), group))
    else:
        section_groups = _split_sections(sections, periods)
        lesson_specs = []
        for p in range(1, periods + 1):
            group = section_groups[p - 1] if p - 1 < len(section_groups) else sections[-1:]
            if not group:
                group = sections
            if periods == 1:
                title = chapter.title
            else:
                names = "、".join(s.title for s in group[:2])
                title = f"{names}（第{p}课时）" if names else f"{chapter.title}（第{p}课时）"
            lesson_specs.append((title, [s.title for s in group], group))
    for p, (lesson_title, lesson_sections, group) in enumerate(lesson_specs, start=1):
        phase_specs = PHASE_TEMPLATES["first"] if p == 1 else PHASE_TEMPLATES["later"]
        scaled = _scale_minutes([m for _, m, _ in phase_specs], duration)
        phases = []
        for idx, ((name, _minutes, intent_tag), minutes) in enumerate(zip(phase_specs, scaled), start=1):
            sec = group[min(len(group) - 1, max(0, idx - 2))]
            phase = _make_phase(
                idx=idx,
                name=name,
                minutes=minutes,
                chapter=chapter,
                section=sec,
                intent_tag=intent_tag,
                objective_ids=_phase_objectives(idx, name),
                period=p,
            )
            phases.append(phase)
        lessons.append(
            {
                "period": p,
                "title": lesson_title,
                "sections": lesson_sections,
                "duration_minutes": duration,
                "phases": phases,
            }
        )

    citations = _citations(chapter)
    section_groups = [group for _, _, group in lesson_specs]
    board_lines = [
        f"{chapter.title}",
        "一、核心概念",
        f"　　{display[0]}：______（关键词）",
    ]
    if len(display) > 1:
        board_lines.append(f"二、关键过程/差异")
        board_lines.append(f"　　{display[1]}：______ → ______")
    board_lines.append("三、方法提炼：观察 → 比较 → 归纳 → 应用")

    custom_slots = {}
    for label in custom_labels or []:
        custom_slots[label] = _custom_value(label, chapter, display)

    slides = _slides(chapter, objectives, display, duration)

    return {
        "schema_version": "1.0",
        "meta": {
            "title": chapter.title,
            "subject": subject,
            "grade": grade,
            "textbook": textbook,
            "lesson_type": lesson_type,
            "duration_minutes": duration,
            "periods": periods,
            "design_framework": _s(brief.get("design_framework")) or "教学评一体化",
            "notes": "OFFLINE_DEMO",
        },
        "analysis": {
            "content": (
                f"《{chapter.title}》共 {len(sections)} 个小节，围绕"
                + "、".join(display[:4])
                + f"展开，是后续学习与应用的基础。教材先呈现核心概念，再通过案例说明其过程与影响，最后引导学生迁移到真实情境。"
                f"本章内容兼具概念理解与实践应用价值，适合采用情境导入、任务驱动和小组合作的方式组织学习。"
            ),
            "status_quo": _s(brief.get("student_profile"))
            or "学生对该主题有生活经验，但概念理解较零散，容易停留在“知道是什么”而说不清“为什么”和“怎样用”；需要借助具体案例、可视化工具和任务单搭建理解支架。",
            "curriculum": [_s(brief.get("curriculum_standard"))]
            if _s(brief.get("curriculum_standard"))
            else [f"结合课程标准，落实“{chapter.title}”相关的内容要求，发展学生的概念理解、问题解决与负责任应用意识。"],
            "focus_questions": [
                f"“{display[0]}”到底指什么？它有哪些关键特征？",
                f"我们如何用“{display[min(1, len(display) - 1)]}”解释身边的真实现象？",
            ],
        },
        "objectives": objectives,
        "key_points": [
            f"理解“{display[0]}”的核心概念与关键词。",
            f"梳理“{display[min(1, len(display) - 1)]}”的基本过程或关键差异。",
        ],
        "difficulties": [
            {
                "point": f"把“{display[min(1, len(display) - 1)]}”的抽象表述转化为可操作的步骤。",
                "strategy": "用教材案例搭建“读—圈—画—说”支架，先扶后放，再用任务单中的对比表外化思维过程。",
            },
            {
                "point": f"理解“{display[-1]}”与真实生活之间的联系，并作出有依据的判断。",
                "strategy": "提供正反两类情境，组织小组辩论，要求每次表达都引用教材原句或事实依据。",
            },
        ],
        "methods": ["情境教学法", "任务驱动法", "小组合作学习", "追问式讲授"],
        "materials": ["教材", "PPT 课件", "学习任务单", "概念图/对比表模板", "评价量规"],
        "lessons": lessons,
        "assessment_plan": [
            {"objective_id": "O1", "method": "课堂提问 + 概念图", "evidence": "学生能准确说出关键词并建立概念关系。", "verified": False},
            {"objective_id": "O2", "method": "小组任务单 + 展示", "evidence": "对比表完整，归纳结论有教材依据。", "verified": False},
            {"objective_id": "O3", "method": "出口条 + 同伴互评", "evidence": "能提出一条具体、可执行的负责任建议。", "verified": False},
        ],
        "coverage": [
            {
                "section": s.title,
                "covered": True,
                "where": f"第{_lesson_index_for_section(s, section_groups)}课时 新知探究/任务实践",
            }
            for s in sections
        ],
        "board_design": {"style": "纲目式板书，左侧概念、右侧方法", "content": board_lines},
        "homework": [
            {"level": "基础", "task": f"整理《{chapter.title}》的概念图，并用 3 句话向家人解释“{display[0]}”。", "minutes": 10},
            {"level": "提升", "task": "从教材中任选一个案例，补写“如果条件改变，结果会怎样”的推理过程。", "minutes": 15},
        ],
        "differentiation": {
            "support": "提供带关键词提示的任务单和同桌互助，允许用口头表达替代书面长文。",
            "extension": "提供开放情境，要求学生自主查找一个反例并说明它为什么能支持或反驳教材结论。",
        },
        "reflection": [
            "学生对核心概念的哪些前概念没有被充分暴露？下次如何设计诊断性问题？",
            "任务单的支架是否过细或过粗？从学生产出看，哪一步需要增删？",
            "目标 O2 的对比与归纳，有多少学生能独立完成？证据是什么？",
        ],
        "citations": citations,
        "ppt_brief": {"slide_outline": slides},
        "custom_slots": custom_slots,
    }


def _make_phase(
    idx: int,
    name: str,
    minutes: int,
    chapter: Chapter,
    section: Any,
    intent_tag: str,
    objective_ids: list[str],
    period: int,
) -> dict[str, Any]:
    sec_title = section.title if section else chapter.title
    start = section.start_line if section else chapter.start_line
    end = section.end_line if section else chapter.end_line
    if name in {"情境导入", "复习导入"}:
        teacher_text = (
            f"出示与“{sec_title}”相关的 2 个生活情境（可选用教材中的案例），提问：“你看到了什么？它可能怎样工作？”"
            f"记录学生的前概念，不急于纠正。"
        )
        student_text = "观察情境，结合已有经验说出自己的猜测，与同桌交换 1 次观点，形成小组问题清单。"
        assessment = "倾听并记录 2-3 条典型前概念，用于后续点拨。"
    elif name in {"新知探究", "深入探究"}:
        teacher_text = (
            f"请学生阅读教材第 {start}-{end} 行“{sec_title}”，圈画关键词；"
            f"依次追问“是什么—为什么—怎么看”，并在黑板上形成概念关系图。"
        )
        student_text = "独立阅读并圈画关键词，用一句话概括本节内容；同桌互述后，补全概念图/对比表。"
        assessment = "抽查 2 名学生口头解释，检查关键词与概念关系是否准确。"
    elif name in {"任务实践", "综合任务"}:
        teacher_text = (
            f"发布任务单：围绕“{sec_title}”完成对比表或案例分析，标明依据；"
            f"提供“读—找—比—说”四步支架，巡视并针对共性问题集中点拨。"
        )
        student_text = "小组分工，完成对比表/案例分析，至少写出 1 条教材依据和 1 条自己的解释，准备 1 分钟汇报。"
        assessment = "依据量规检查：依据是否准确、比较是否完整、结论是否有解释。"
    elif name in {"交流评价", "展示评价"}:
        teacher_text = "组织 2 组展示，追问“你的依据在哪一行？如果条件改变，结论还成立吗？”引导同伴互评。"
        student_text = "展示任务单，依据量规给同伴提出 1 条优点和 1 条改进建议，记录需要修改的地方。"
        assessment = "同伴互评 + 教师点评，重点看目标 O2/O3 的达成证据。"
    else:
        teacher_text = f"借助板书回顾“{chapter.title}”的学习路径，用 3 个问题检测目标达成，布置分层作业。"
        student_text = "完成出口条：写出 1 个核心概念、1 个仍存在的疑问、1 条实际应用建议。"
        assessment = "出口条当堂回收，统计目标达成情况并作为下节课调整依据。"

    return {
        "id": f"P{period}-{idx}",
        "name": name,
        "minutes": minutes,
        "teacher_activity": teacher_text,
        "student_activity": student_text,
        "intent": f"通过“{name}”落实目标 {'、'.join(objective_ids)}，{intent_tag}，让学生从经验走向概念，再走向应用。",
        "assessment": assessment,
        "resources": ["教材", "PPT", "学习任务单"] if name not in {"情境导入", "复习导入"} else ["PPT", "情境图片/短视频"],
        "objective_ids": objective_ids,
    }


def _phase_objectives(idx: int, name: str) -> list[str]:
    if name in {"情境导入", "复习导入"}:
        return ["O1"]
    if name in {"新知探究", "深入探究"}:
        return ["O1", "O2"]
    if name in {"任务实践", "综合任务"}:
        return ["O2"]
    if name in {"交流评价", "展示评价"}:
        return ["O2", "O3"]
    return ["O1", "O2", "O3"]


def _scale_minutes(base: list[int], total: int) -> list[int]:
    if not base:
        return []
    raw = [max(1, round(m * total / sum(base))) for m in base]
    diff = total - sum(raw)
    raw[-1] = max(1, raw[-1] + diff)
    # 若调整后仍不相等（极端小课时），从最长环节继续借/还
    guard = 0
    while sum(raw) != total and guard < 20:
        guard += 1
        idx = max(range(len(raw)), key=lambda i: raw[i])
        if sum(raw) > total and raw[idx] > 1:
            raw[idx] -= 1
        elif sum(raw) < total:
            raw[idx] += 1
        else:
            break
    return raw


def _split_sections(sections: list[Any], periods: int) -> list[list[Any]]:
    if not sections:
        return [[] for _ in range(periods)]
    groups: list[list[Any]] = []
    for p in range(periods):
        start = round(p * len(sections) / periods)
        end = round((p + 1) * len(sections) / periods)
        group = sections[start:end]
        groups.append(group or [sections[min(len(sections) - 1, start)]])
    return groups


def _lesson_index_for_section(section: Any, groups: list[list[Any]]) -> int:
    for i, group in enumerate(groups, start=1):
        if section in group:
            return i
    return 1


def _citations(chapter: Chapter) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for section in chapter.sections[:4]:
        for sentence in split_sentences(section.content, 4):
            if char_count(sentence) < 8:
                continue
            hit = ground_quote(chapter, sentence)
            if not hit:
                continue
            source, start, end = hit
            out.append(
                {
                    "claim": f"{section.title}的关键表述",
                    "quote": source,
                    "source_lines": [start, end],
                    "verified": True,
                }
            )
            break
        if len(out) >= 3:
            break
    return out


def _slides(chapter: Chapter, objectives: list[dict[str, Any]], display: list[str], duration: int) -> list[dict[str, Any]]:
    slides = [
        {
            "title": chapter.title,
            "bullets": [f"学科：信息科技｜课时：{duration} 分钟", "主讲：____"],
            "visual": "章节主图/生活情境拼图",
        },
        {
            "title": "学习目标",
            "bullets": [o["statement"] for o in objectives[:3]],
            "visual": "三条目标图标",
        },
        {
            "title": "情境导入",
            "bullets": ["你身边有哪些类似的应用？", "它为什么能“看懂/听懂/猜中”？", "今天我们从教材中找答案"],
            "visual": "2 张生活场景图",
        },
        {
            "title": display[0],
            "bullets": ["核心概念：______", "关键词：______、______", "教材依据：第 __ 行"],
            "visual": "概念关系图",
        },
    ]
    if len(display) > 1:
        slides.append(
            {
                "title": display[1],
                "bullets": ["关键过程/差异：______", "案例：______", "容易混淆之处：______"],
                "visual": "对比表/流程图",
            }
        )
    slides += [
        {
            "title": "课堂任务",
            "bullets": ["任务：完成对比表/案例分析", "要求：每条结论写出教材依据", "时间：15 分钟"],
            "visual": "任务单截图 + 计时器",
        },
        {
            "title": "展示与评价",
            "bullets": ["依据是否准确", "比较是否完整", "建议是否可执行"],
            "visual": "三点量规",
        },
        {
            "title": "总结与作业",
            "bullets": ["一条学习路径：观察→比较→归纳→应用", "一个仍存在的疑问", "分层作业：基础/提升"],
            "visual": "板书结构图",
        },
    ]
    return slides


def _custom_value(label: str, chapter: Chapter, display: list[str]) -> str:
    norm = normalize_label(label)
    if any(k in norm for k in ("评价", "检测")):
        return "采用课堂提问、任务单量规和出口条三种方式，分别检测概念理解、比较归纳与迁移应用。"
    if any(k in norm for k in ("资源", "准备", "材料")):
        return "教材、PPT 课件、学习任务单、概念图/对比表模板、评价量规。"
    if any(k in norm for k in ("思路", "理念", "说明")):
        return f"以“{display[0]}”的生活情境切入，用教材案例搭建支架，通过任务单外化思维，最后迁移到真实问题。"
    if any(k in norm for k in ("作业",)):
        return "基础：整理概念图并向家人解释；提升：补写案例的条件变化推理。"
    if any(k in norm for k in ("板书",)):
        return f"{chapter.title}：核心概念 → 关键过程 → 方法提炼 → 应用建议"
    if any(k in norm for k in ("反思",)):
        return "前概念是否被充分暴露？支架是否适度？目标达成的证据是否充分？"
    return f"围绕《{chapter.title}》的“{label}”，结合教材第 {chapter.start_line}-{chapter.end_line} 行的内容完成设计，并在课堂中根据学生反馈调整。"


def _s(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _i(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
