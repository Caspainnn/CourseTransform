from __future__ import annotations

import json
from typing import Any

from .template import TemplateSpec
from .textbook import Chapter
from .utils import truncate

SYSTEM_PROMPT = """你是一位有 20 年一线经验的中文教学设计专家，熟悉中国义务教育/普通高中课程标准、教材分析、学情分析和“教学评一体化”设计。你擅长把教材原文转化为教师可以直接照此上课的详案。

你必须遵守以下硬性规则：
1. 忠实教材：概念、事实、数据、案例必须来自【教材原文】。教材没有的内容不要编造；需要补充生活例子时，必须与教材概念一致，并保持谨慎表述。
2. 目标可测：每条教学目标使用可观察的行为动词，写清学生“能做什么”，并给出可见的达成证据。
3. 教学评一致：每个目标至少被一个教学活动和一个评价任务覆盖；评价任务要能产出作品、回答、操作或任务单等证据。
4. 时间精确：每个环节的分钟数为正整数，单课时合计必须精确等于规定时长。
5. 以学生为中心：写清教师具体说什么/做什么、学生具体做什么、为什么这样设计；避免“教师讲解第 X 段”“学生认真听讲”这类空话。
6. 只输出合法 JSON，不要输出 Markdown 代码块、解释、注释或前后缀。所有字符串使用中文（专业英文缩写可保留）。"""


def brief_text(brief: dict[str, Any]) -> str:
    if not brief:
        return "（未提供，按常规八年级信息科技课堂处理）"
    lines = []
    mapping = [
        ("subject", "学科"),
        ("grade", "年级"),
        ("textbook", "教材"),
        ("lesson_type", "课型"),
        ("duration_minutes", "单课时时长（分钟）"),
        ("periods", "课时数"),
        ("design_framework", "设计框架"),
        ("curriculum_standard", "课标依据"),
        ("student_profile", "学情"),
    ]
    for key, label in mapping:
        if brief.get(key) not in (None, "", []):
            lines.append(f"- {label}：{brief[key]}")
    if brief.get("constraints"):
        lines.append("- 教学条件/限制：" + "；".join(str(x) for x in brief["constraints"]))
    if brief.get("focus"):
        lines.append("- 教师特别要求：" + str(brief["focus"]))
    return "\n".join(lines) if lines else "（未提供，按常规课堂处理）"


def template_text(spec: TemplateSpec | None) -> str:
    if spec is None or spec.kind == "canonical":
        return "（使用系统内置标准教案结构，不需要适配额外字段）"
    lines = [f"模板类型：{spec.kind}，识别到以下字段（key -> 模板中的叫法）："]
    for s in spec.slots:
        if s.key.startswith("custom:"):
            lines.append(f"- 自定义字段 → “{s.label}”")
        else:
            lines.append(f"- {s.key} → “{s.label}”")
    if spec.phase_table:
        cols = "、".join(spec.phase_table.columns.keys())
        lines.append(f"- 模板含“教学过程”表格，列：{cols}；系统会自动把 phases 填入该表。")
    custom = spec.custom_labels
    if custom:
        lines.append("必须填写这些自定义字段（键名用模板中的原叫法）：" + "、".join(custom))
    return "\n".join(lines)


def section_list(chapter: Chapter, key_only: bool = False) -> str:
    sections = chapter.key_sections() if key_only else chapter.sections
    return "\n".join(f"- {s.title}（第 {s.start_line}-{s.end_line} 行）" for s in sections)


def plan_schema() -> str:
    return """{
  "meta": {
    "title": "章节/课时标题",
    "subject": "学科",
    "grade": "年级",
    "textbook": "教材名称",
    "lesson_type": "新授课/复习课/项目课",
    "duration_minutes": 45,
    "periods": 1,
    "design_framework": "教学评一体化"
  },
  "analysis": {
    "content": "教材分析，120-250字：内容地位、结构、核心概念、育人价值",
    "status_quo": "学情分析，80-180字：已有基础、前概念、可能困难、差异",
    "curriculum": ["课标依据1", "课标依据2"],
    "focus_questions": ["贯穿本课时的驱动问题1", "驱动问题2"]
  },
  "objectives": [
    {
      "id": "O1",
      "domain": "知识/技能/思维/价值",
      "bloom": "记忆/理解/应用/分析/评价/创造",
      "statement": "能……（可观察的行为动词，包含具体内容）",
      "evidence": "学生用什么回答/作品/操作证明达成"
    }
  ],
  "key_points": ["教学重点1", "教学重点2"],
  "difficulties": [
    {"point": "学生具体会在哪里卡住", "strategy": "用什么支架/活动/表征突破"}
  ],
  "methods": ["教学方法1", "教学方法2"],
  "materials": ["教学准备1", "教学准备2"],
  "lessons": [
    {
      "period": 1,
      "title": "本课时主题（写清覆盖的小节，不要 4 个课时同名）",
      "sections": ["本课时覆盖的核心小节标题"],
      "duration_minutes": 45,
      "phase_outline": [
        {"name": "情境导入", "minutes": 5, "purpose": "激活经验，暴露前概念", "objective_ids": ["O1"]}
      ]
    }
  ],
  "assessment_plan": [
    {"objective_id": "O1", "method": "评价方式", "evidence": "可见证据"}
  ],
  "coverage": [
    {"section": "必须与教材小节标题完全一致", "covered": true, "where": "第1课时 新知探究"}
  ]
}"""


def phases_schema() -> str:
    return """{
  "phases": [
    {
      "id": "P1",
      "name": "环节名称",
      "minutes": 5,
      "teacher_activity": "教师具体说什么、做什么、如何巡视/追问（80-220字，含关键提问原话）",
      "student_activity": "学生具体做什么、产出什么、如何交流（50-160字）",
      "intent": "为什么这样设计，支撑哪条目标（30-100字）",
      "assessment": "本环节如何判断目标达成（20-80字，写观察点/任务单/互评规则）",
      "resources": ["本环节用到的资源"],
      "objective_ids": ["O1"]
    }
  ]
}"""


def finish_schema() -> str:
    return """{
  "board_design": {
    "style": "板书布局说明",
    "content": ["主板书第1行", "第2行", "第3行", "第4行"]
  },
  "homework": [
    {"level": "基础/提升/拓展", "task": "具体任务与要求", "minutes": 10}
  ],
  "differentiation": {
    "support": "对学习困难学生的具体支持",
    "extension": "对学有余力学生的拓展"
  },
  "reflection": ["课后反思问题1", "反思问题2", "反思问题3"],
  "citations": [
    {
      "claim": "这条引用支撑的教学判断",
      "quote": "教材原文逐字摘录，不得改写",
      "source_lines": [12, 13]
    }
  ],
  "ppt_brief": {
    "slide_outline": [
      {"title": "页面标题", "bullets": ["要点1", "要点2"], "visual": "建议的图示/图片/动画"}
    ]
  },
  "custom_slots": {
    "模板自定义字段原叫法": "对应内容"
  }
}"""


def build_plan_prompt(
    chapter: Chapter,
    context: str,
    brief: dict[str, Any],
    spec: TemplateSpec | None,
    periods: int,
    duration: int,
) -> list[dict[str, str]]:
    user = f"""【任务】为下列教材章节设计“教学方案骨架”。这一步只做目标、重难点、课时分配和环节大纲，不展开师生活动细节。

【章节】{chapter.title}
【核心小节】（只对这些小节做覆盖检查，不要为三级小标题逐条建 coverage）
{section_list(chapter, key_only=True)}

【教学简报】
{brief_text(brief)}

【教案模板字段】
{template_text(spec)}

【硬性约束】
- 必须正好设计 {periods} 个课时，每课时 {duration} 分钟；lessons 数组长度必须等于 {periods}。
- 按【核心小节】的教学顺序分配课时，每个 lesson 的 title 要写清本课时主题，sections 列出本课时覆盖的核心小节；不同课时的 title 不能重复。
- 每个课时的 phase_outline 为 4-6 个环节，分钟数之和必须精确等于 {duration}。
- 教学目标 3 条左右（2-4 条），覆盖知识、思维/技能、价值三个维度；每条都要有 evidence。
- 每个目标至少被一个环节的 objective_ids 引用，且 assessment_plan 必须覆盖全部目标。
- coverage 只列【核心小节】中的全部条目（通常 3-8 条），section 必须与标题完全一致；covered 为 true 或 false，并写明 where。不要把 3.1.1 这类子标题也列进去。
- 教材分析、学情分析必须具体到本章内容，不能写套话。
- 输出要精简：每个字段只写必要内容，优先保证 JSON 完整闭合。

【教材原文（含行号锚点，供引用核对；不要把 [Lx-Ly] 写进教案正文）】
{context}

【输出 JSON 结构】
{plan_schema()}

【自检后再输出】
1. lessons 数量、每课时环节分钟数之和是否正确？
2. 每个目标是否都有对应环节和评价？
3. coverage 是否覆盖全部【核心小节】、没有把三级小标题也列进去？
只输出 JSON。"""
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def build_phase_prompt(
    plan: dict[str, Any],
    lesson: dict[str, Any],
    context: str,
    spec: TemplateSpec | None,
    duration: int,
) -> list[dict[str, str]]:
    lesson_json = json.dumps(lesson, ensure_ascii=False, indent=2)
    objective_json = json.dumps(plan.get("objectives") or [], ensure_ascii=False, indent=2)
    user = f"""【任务】把第 {lesson.get('period')} 课时的“环节大纲”展开成教师可以直接照此上课的师生活动详案。

【本课时已确认的方案】
{lesson_json}

【本课时覆盖的核心小节】
{chr(10).join('- ' + str(s) for s in (lesson.get('sections') or [])) or '（按课时标题判断）'}

【本课时教学目标】
{objective_json}

【教材原文（含行号锚点）】
{context}

【教案模板字段】
{template_text(spec)}

【写作要求】
- 环节数量、名称、顺序原则上沿用 phase_outline；如确有必要可微调，但分钟数之和必须精确等于 {duration}。
- 每个环节必须写清：教师的关键提问/指令（尽量给原话）、学生的具体动作与产出、设计意图、评价方式、资源。
- 师生活动要“可上课”：有过渡语、有预设的学生回答/错误、有巡视与追问策略；不要写“教师讲解”“学生讨论”就结束。
- 所有知识内容必须能在【教材原文】中找到依据；需要举例时，用教材案例或学生熟悉的生活情境，不得编造数据、人物、机构。
- 评价方式要可观察：提问、任务单、演示、互评量规、出口条等，写清判断标准。
- 每个环节至少关联一个 objective_ids；尽量让 60% 以上环节带评价。
- 只输出 JSON，结构如下：
{phases_schema()}

【自检】phases 的 minutes 之和是否等于 {duration}？每个环节的师生活动是否具体到可以上课？
只输出 JSON。"""
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def build_figure_prompt(
    plan: dict[str, Any],
    chapter: Chapter,
    images_text: str,
) -> list[dict[str, str]]:
    phases = []
    for lesson in plan.get("lessons") or []:
        for phase in lesson.get("phases") or []:
            phases.append(
                {
                    "period": lesson.get("period"),
                    "phase_id": phase.get("id"),
                    "name": phase.get("name"),
                    "minutes": phase.get("minutes"),
                    "objective_ids": phase.get("objective_ids") or [],
                    "teacher_activity": truncate(str(phase.get("teacher_activity") or ""), 90),
                    "student_activity": truncate(str(phase.get("student_activity") or ""), 60),
                }
            )
    phase_json = json.dumps(phases, ensure_ascii=False, indent=2)
    schema = """{
  "figures": [
    {
      "image_id": "fig01",
      "caption": "8-30字图注，尽量依据图片文字/教材图注/用户提示，不编造",
      "placement": "phase | analysis | appendix",
      "period": 1,
      "phase_id": "P1-2",
      "section": "对应教材小节标题，可空",
      "reason": "为什么放在这里"
    }
  ]
}"""
    user = f"""【任务】把教材配图分配到教案的具体位置，并写图注。

【章节】{chapter.title}

【可用图片】
{images_text}

【教案的课时与环节】
{phase_json}

【硬性要求】
- 每张图片必须恰好出现一次；不能新增图片，image_id 只能从【可用图片】里选。
- 优先放到讲解该图片内容的教学环节：placement="phase"，并填写 period 和 phase_id。
- 如果图片是整章概念总览/教材分析用图，放 placement="analysis"。
- 如果图片只是装饰或无法判断内容，放 placement="appendix"。
- caption 8-30 字，必须依据“教材图注 / 用户提示 / 图片文字识别 / 文件名”，不要凭空编造图中没有的内容。
- OCR 文字可能不完整；不确定时用“教材配图：<文件名>”这种保守图注。
- 只输出 JSON，不要 Markdown 代码块：
{schema}

【自检】图片数量是否与可用图片数量一致？image_id 是否全部来自列表？
只输出 JSON。"""
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def build_finish_prompt(
    plan: dict[str, Any],
    chapter: Chapter,
    context: str,
    spec: TemplateSpec | None,
) -> list[dict[str, str]]:
    plan_json = json.dumps(plan, ensure_ascii=False, indent=2)
    custom = spec.custom_labels if spec else []
    custom_note = (
        "必须填写这些模板自定义字段（custom_slots 的键用模板中的原叫法）：" + "、".join(custom)
        if custom
        else "模板没有额外自定义字段，custom_slots 返回空对象 {}。"
    )
    user = f"""【任务】为一节已经设计好教学过程的教案补齐：板书设计、作业、差异化支持、课后反思问题、教材引用、PPT 提纲。

【章节】{chapter.title}
【教案主体（已含目标、重难点、课时与环节）】
{plan_json}

【教材原文（引用必须逐字来自这里）】
{context}

【模板字段】
{template_text(spec)}

【写作要求】
- 板书设计：给出布局说明 + 4-8 行主板书内容，体现知识结构，不要写成散文。
- 作业：2-3 个层级（基础/提升/拓展），写清任务、要求、预计分钟数；总时长控制在 20-40 分钟，不得超量。
- 差异化：分别写对学习困难学生和学有余力学生的具体支持，不要空话。
- 反思：写 3-4 个“课后可以用来复盘”的问题，不要编造“本节课学生表现如何”的既定事实。
- 引用：3-5 条，quote 必须是【教材原文】中的原句（逐字，不要改写、不要省略号），source_lines 写原文行号；claim 说明这条引用支撑什么教学判断。
- PPT 提纲：7-12 页，与教学环节对应；每页 2-5 条要点，visual 给出图示/图片/动画建议；不要把整段教案复制到幻灯片。
- {custom_note}
- 只输出 JSON，结构如下：
{finish_schema()}

【自检】quote 是否逐字来自教材？PPT 页数是否在 7-12？自定义字段是否全部填写？
只输出 JSON。"""
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def build_repair_prompt(
    plan: dict[str, Any],
    issues: str,
    chapter: Chapter,
    context: str,
    spec: TemplateSpec | None,
) -> list[dict[str, str]]:
    plan_json = json.dumps(plan, ensure_ascii=False, indent=2)
    user = f"""【任务】下面这份教案 JSON 没有通过质检。请逐条修复问题，输出修复后的完整 JSON（不是补丁、不是解释）。

【章节】{chapter.title}

【质检问题清单】
{issues}

【原教案 JSON】
{plan_json}

【教材原文（修改时必须以此为依据）】
{context}

【模板字段】
{template_text(spec)}

【修复要求】
- 必须修复所有标注“必须修复”的问题；建议项尽量修复。
- 保持原有的正确内容、结构和风格，不要整篇重写；不要删掉模板需要的字段。
- 时间类问题必须精确：每课时 phases 分钟数之和等于该课时 duration_minutes；lessons 数量等于 meta.periods。
- 目标—活动—评价必须一一对应；每个目标至少有一个环节和一个评价任务。
- 引用必须逐字来自教材原文，并给出行号；找不到依据就删掉该条引用，不要编造。
- 输出结构与原 JSON 完全一致；custom_slots 的键保持模板原叫法。
只输出修复后的完整 JSON。"""
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def build_review_prompt(plan: dict[str, Any], chapter: Chapter) -> list[dict[str, str]]:
    plan_json = json.dumps(plan, ensure_ascii=False, indent=2)
    user = f"""【任务】你是教研组长，请按下面的量表审查这份教案，只输出审查意见，不要重写教案。

【章节】{chapter.title}
【教案 JSON】
{plan_json}

【评审量表】
1. 目标可测：行为动词是否可观察？是否有达成证据？
2. 教学评一致：每个目标是否都有活动和评价覆盖？评价是否能产出证据？
3. 教材忠实：知识内容是否有教材依据？是否有编造或超出教材的断言？
4. 认知负荷：环节容量是否适合规定课时？是否有连续讲授过久、任务过大？
5. 学生参与：是否有真实的学生思考、交流、产出？是否只是教师提问、学生齐答？
6. 差异化：是否照顾到学习困难与学有余力的学生？
7. 语言可执行：师生活动是否具体到教师可以直接照此上课？

【输出 JSON】
{{
  "issues": [
    {{"severity": "error 或 warning", "location": "objectives[0] / lessons[0].phases[2]", "problem": "具体问题", "fix": "可操作的修改建议"}}
  ],
  "scores": {{"目标可测": 1-5, "教学评一致": 1-5, "教材忠实": 1-5, "认知负荷": 1-5, "学生参与": 1-5, "差异化": 1-5, "语言可执行": 1-5}},
  "verdict": "一句话结论"
}}

只输出 JSON；问题要具体到位置，不要泛泛而谈。"""
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def clip_context(text: str, limit: int = 16000) -> str:
    return truncate(text, limit, "\n……（上下文过长，已截断）")
