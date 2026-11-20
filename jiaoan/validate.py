from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .textbook import Chapter, ground_quote, section_title_set
from .utils import char_count, normalize_label, strip_punct

MEASURABLE_VERBS = (
    "说出|写出|解释|比较|归纳|概括|分析|设计|绘制|演示|运用|完成|评价|判断|提出|"
    "说明|列举|描述|计算|测量|制作|合作|规划|识别|区分|推断|验证|复述|标注|分类|"
    "总结|阐述|讨论|展示|编写|搭建|调试|调查|收集|整理|表达|迁移|应用"
)
BLOOM_LEVELS = {"记忆", "理解", "应用", "分析", "评价", "创造", "理解/应用", "分析/评价", "评价/创造", "应用/分析"}
DOMAINS = {
    "知识",
    "技能",
    "思维",
    "价值",
    "素养",
    "知识与技能",
    "过程与方法",
    "情感态度与价值观",
    "知识/技能",
    "思维/技能",
    "价值/态度",
    "素养/价值",
}
PLACEHOLDER_PATTERNS = ("{{", "}}", "（请填写）", "(请填写)", "待补充", "TODO", "XXX", "xxx")


@dataclass
class Issue:
    code: str
    severity: str
    location: str
    message: str
    fix: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "severity": self.severity,
            "location": self.location,
            "message": self.message,
            "fix": self.fix,
        }


@dataclass
class ValidationResult:
    issues: list[Issue] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    grounded_citations: int = 0
    dropped_citations: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(self, code: str, severity: str, location: str, message: str, fix: str = "") -> None:
        self.issues.append(Issue(code, severity, location, message, fix))

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "counts": {"error": len(self.errors), "warning": len(self.warnings)},
            "issues": [i.to_dict() for i in self.issues],
            "metrics": self.metrics,
            "grounded_citations": self.grounded_citations,
            "dropped_citations": self.dropped_citations,
        }

    def repair_instructions(self, max_warnings: int = 8) -> str:
        lines: list[str] = []
        for issue in self.errors:
            lines.append(f"[必须修复/{issue.code}] {issue.location}：{issue.message}" + (f" 修复建议：{issue.fix}" if issue.fix else ""))
        for issue in self.warnings[:max_warnings]:
            lines.append(f"[建议改进/{issue.code}] {issue.location}：{issue.message}" + (f" 修复建议：{issue.fix}" if issue.fix else ""))
        return "\n".join(lines)


def _get_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def normalize_plan(plan: dict[str, Any], chapter: Chapter, brief: dict[str, Any] | None = None) -> dict[str, Any]:
    """补齐字段、统一类型，保证后续校验与渲染稳定。"""
    brief = brief or {}
    defaults = brief.get("defaults") if isinstance(brief.get("defaults"), dict) else brief
    meta = plan.setdefault("meta", {}) if isinstance(plan.get("meta"), dict) else {}
    plan["meta"] = meta
    meta["title"] = _text(meta.get("title")) or chapter.title
    if meta["title"] and meta["title"] in chapter.title and len(meta["title"]) < len(chapter.title):
        meta["title"] = chapter.title
    meta["subject"] = _text(meta.get("subject")) or _text(defaults.get("subject")) or "信息科技"
    meta["grade"] = _text(meta.get("grade")) or _text(defaults.get("grade")) or "八年级"
    meta["textbook"] = _text(meta.get("textbook")) or _text(defaults.get("textbook")) or ""
    meta["lesson_type"] = _text(meta.get("lesson_type")) or _text(defaults.get("lesson_type")) or "新授课"
    meta["design_framework"] = _text(meta.get("design_framework")) or _text(defaults.get("design_framework")) or "教学评一体化"
    duration = _int(meta.get("duration_minutes"), _int(defaults.get("duration_minutes"), 45))
    meta["duration_minutes"] = max(20, min(120, duration))
    lessons = _get_list(plan.get("lessons"))
    periods = _int(meta.get("periods"), _int(defaults.get("periods"), len(lessons) or 1))
    meta["periods"] = max(1, min(6, periods))

    analysis = plan.setdefault("analysis", {}) if isinstance(plan.get("analysis"), dict) else {}
    plan["analysis"] = analysis
    analysis["content"] = _text(analysis.get("content"))
    analysis["status_quo"] = _text(analysis.get("status_quo"))
    analysis["curriculum"] = [_text(x) for x in _get_list(analysis.get("curriculum")) if _text(x)]
    analysis["focus_questions"] = [_text(x) for x in _get_list(analysis.get("focus_questions")) if _text(x)]

    objectives = []
    for i, obj in enumerate(_get_list(plan.get("objectives")), start=1):
        if not isinstance(obj, dict):
            obj = {"statement": _text(obj)}
        obj["id"] = _text(obj.get("id")) or f"O{i}"
        obj["domain"] = re.split(r"[/、,，]", _text(obj.get("domain")))[0].strip() or "知识"
        obj["bloom"] = re.split(r"[/、,，]", _text(obj.get("bloom")))[0].strip() or "理解"
        obj["statement"] = _text(obj.get("statement"))
        obj["evidence"] = _text(obj.get("evidence"))
        objectives.append(obj)
    plan["objectives"] = objectives

    plan["key_points"] = [_text(x) for x in _get_list(plan.get("key_points")) if _text(x)]
    difficulties = []
    for item in _get_list(plan.get("difficulties")):
        if isinstance(item, dict):
            point = _text(item.get("point"))
            strategy = _text(item.get("strategy"))
        else:
            point, strategy = _text(item), ""
        if point:
            difficulties.append({"point": point, "strategy": strategy})
    plan["difficulties"] = difficulties
    plan["methods"] = [_text(x) for x in _get_list(plan.get("methods")) if _text(x)]
    plan["materials"] = [_text(x) for x in _get_list(plan.get("materials")) if _text(x)]

    normalized_lessons = []
    for i, lesson in enumerate(lessons, start=1):
        if not isinstance(lesson, dict):
            continue
        lesson["period"] = _int(lesson.get("period"), i)
        lesson["title"] = _text(lesson.get("title")) or f"第{i}课时"
        lesson["duration_minutes"] = _int(lesson.get("duration_minutes"), meta["duration_minutes"])
        lesson["sections"] = [_text(x) for x in _get_list(lesson.get("sections")) if _text(x)]
        phases = []
        for j, phase in enumerate(_get_list(lesson.get("phases")), start=1):
            if not isinstance(phase, dict):
                continue
            phase["id"] = _text(phase.get("id")) or f"P{j}"
            phase["name"] = _text(phase.get("name")) or f"环节{j}"
            phase["minutes"] = _int(phase.get("minutes"), 0)
            phase["teacher_activity"] = _text(phase.get("teacher_activity"))
            phase["student_activity"] = _text(phase.get("student_activity"))
            phase["intent"] = _text(phase.get("intent"))
            phase["assessment"] = _text(phase.get("assessment"))
            phase["objective_ids"] = [_text(x) for x in _get_list(phase.get("objective_ids")) if _text(x)]
            if not phase["objective_ids"] and any(k in phase["name"] for k in ("总结", "小结", "作业", "回顾")):
                phase["objective_ids"] = [str(o.get("id")) for o in objectives if o.get("id")]
            phase["resources"] = [_text(x) for x in _get_list(phase.get("resources")) if _text(x)]
            phase["figure_ids"] = [_text(x) for x in _get_list(phase.get("figure_ids")) if _text(x)]
            phases.append(phase)
        lesson["phases"] = phases
        normalized_lessons.append(lesson)
    plan["lessons"] = normalized_lessons

    board = plan.get("board_design")
    if isinstance(board, str):
        board = {"style": "", "content": [board] if board.strip() else []}
    if not isinstance(board, dict):
        board = {}
    board["style"] = _text(board.get("style"))
    content = board.get("content")
    if isinstance(content, str):
        content = [line.strip() for line in content.splitlines() if line.strip()]
    board["content"] = [_text(x) for x in _get_list(content) if _text(x)]
    plan["board_design"] = board

    homework = []
    for item in _get_list(plan.get("homework")):
        if isinstance(item, dict):
            item["level"] = _text(item.get("level")) or "基础"
            item["task"] = _text(item.get("task"))
            item["minutes"] = _int(item.get("minutes"), 0)
            if item["task"]:
                homework.append(item)
        elif _text(item):
            homework.append({"level": "基础", "task": _text(item), "minutes": 0})
    plan["homework"] = homework

    diff = plan.get("differentiation") if isinstance(plan.get("differentiation"), dict) else {}
    plan["differentiation"] = {
        "support": _text(diff.get("support")),
        "extension": _text(diff.get("extension")),
    }
    plan["reflection"] = [_text(x) for x in _get_list(plan.get("reflection")) if _text(x)]

    figures: list[dict[str, Any]] = []
    seen_fig_ids: set[str] = set()
    for i, item in enumerate(_get_list(plan.get("figures")), start=1):
        if not isinstance(item, dict):
            continue
        fid = _text(item.get("id")) or _text(item.get("image_id")) or f"fig{i:02d}"
        if fid in seen_fig_ids:
            continue
        seen_fig_ids.add(fid)
        placement = _text(item.get("placement")) or ("phase" if _text(item.get("phase_id")) else "analysis")
        if placement not in {"phase", "analysis", "appendix"}:
            placement = "appendix"
        figures.append(
            {
                "id": fid,
                "image_id": _text(item.get("image_id")) or fid,
                "filename": _text(item.get("filename")),
                "path": _text(item.get("path")),
                "caption": _text(item.get("caption")),
                "placement": placement,
                "period": _int(item.get("period"), 0),
                "phase_id": _text(item.get("phase_id")),
                "section": _text(item.get("section")),
                "reason": _text(item.get("reason")),
            }
        )
    plan["figures"] = figures

    # 把 figure_ids 反向挂到环节上，兼容只给了 phase_id 的模型输出
    figure_ids = {f["id"] for f in figures}
    for lesson in plan.get("lessons") or []:
        for phase in lesson.get("phases") or []:
            phase["figure_ids"] = [x for x in phase.get("figure_ids", []) if x in figure_ids]
    for fig in figures:
        if fig["placement"] != "phase" or not fig["phase_id"]:
            continue
        for lesson in plan.get("lessons") or []:
            for phase in lesson.get("phases") or []:
                if phase.get("id") == fig["phase_id"] and fig["id"] not in phase["figure_ids"]:
                    phase["figure_ids"].append(fig["id"])

    assessment = []
    for item in _get_list(plan.get("assessment_plan")):
        if isinstance(item, dict):
            item["objective_id"] = _text(item.get("objective_id"))
            item["method"] = _text(item.get("method"))
            item["evidence"] = _text(item.get("evidence"))
            if item["objective_id"] or item["method"]:
                assessment.append(item)
    plan["assessment_plan"] = assessment

    plan["coverage"] = _normalize_coverage(plan, chapter)

    citations = []
    for item in _get_list(plan.get("citations")):
        if not isinstance(item, dict):
            continue
        quote = _text(item.get("quote"))
        if not quote:
            continue
        lines = _get_list(item.get("source_lines"))
        start = _int(lines[0], 0) if len(lines) > 0 else 0
        end = _int(lines[1], start) if len(lines) > 1 else start
        citations.append(
            {
                "claim": _text(item.get("claim")),
                "quote": quote,
                "source_lines": [start, end],
            }
        )
    plan["citations"] = citations

    ppt = plan.get("ppt_brief") if isinstance(plan.get("ppt_brief"), dict) else {}
    slides = []
    for item in _get_list(ppt.get("slide_outline")):
        if isinstance(item, dict):
            bullets = [_text(x) for x in _get_list(item.get("bullets")) if _text(x)]
            slides.append(
                {
                    "title": _text(item.get("title")),
                    "bullets": bullets,
                    "visual": _text(item.get("visual")),
                }
            )
        elif _text(item):
            slides.append({"title": _text(item), "bullets": [], "visual": ""})
    plan["ppt_brief"] = {"slide_outline": slides}

    custom = plan.get("custom_slots") if isinstance(plan.get("custom_slots"), dict) else {}
    plan["custom_slots"] = {str(k): v for k, v in custom.items() if _text(v)}
    plan["schema_version"] = "1.0"
    return plan


def _normalize_coverage(plan: dict[str, Any], chapter: Chapter, required_sections: list[str] | None = None) -> list[dict[str, Any]]:
    raw = _get_list(plan.get("coverage"))
    by_norm: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        section = _text(item.get("section"))
        if not section:
            continue
        by_norm[normalize_label(section)] = {
            "section": section,
            "covered": bool(item.get("covered", True)),
            "where": _text(item.get("where")),
        }
    out: list[dict[str, Any]] = []
    expected = required_sections if required_sections is not None else chapter.key_section_titles
    for title in expected:
        key = normalize_label(title)
        item = by_norm.pop(key, None)
        if item is None:
            # 宽松匹配：允许模型只写“1.1”或去掉编号
            for k, v in list(by_norm.items()):
                if k and (k in key or key in k):
                    item = by_norm.pop(k)
                    break
        if item is None:
            out.append({"section": title, "covered": False, "where": ""})
        else:
            item["section"] = title
            out.append(item)
    # 保留模型额外列出的内容（例如章末总结）
    for item in by_norm.values():
        out.append(item)
    return out


def ground_citations(plan: dict[str, Any], chapter: Chapter) -> tuple[int, list[str]]:
    """把引用锚定到教材原文，修正行号；无法锚定的引用丢弃。"""
    kept: list[dict[str, Any]] = []
    dropped: list[str] = []
    seen: set[str] = set()
    for item in plan.get("citations") or []:
        if not isinstance(item, dict):
            continue
        quote = _text(item.get("quote"))
        if not quote:
            continue
        hit = ground_quote(chapter, quote)
        if not hit:
            dropped.append(quote)
            continue
        source, start, end = hit
        key = strip_punct(source)
        if key in seen:
            continue
        seen.add(key)
        claim = _text(item.get("claim")) or source[:36]
        kept.append({"claim": claim, "quote": source, "source_lines": [start, end], "verified": True})
    plan["citations"] = kept
    return len(kept), dropped


def normalize_phase_minutes(plan: dict[str, Any]) -> list[str]:
    """把每课时环节分钟数强制归一到课时时长。

    这是结构修复，不改教学内容：少了加到最长环节，多了从最长环节扣。
    """
    adjustments: list[str] = []
    meta = plan.get("meta") or {}
    default_duration = _int(meta.get("duration_minutes"), 45)
    for lesson in plan.get("lessons") or []:
        if not isinstance(lesson, dict):
            continue
        phases = [p for p in (lesson.get("phases") or []) if isinstance(p, dict)]
        if not phases:
            continue
        duration = _int(lesson.get("duration_minutes"), default_duration)
        total = sum(max(1, _int(p.get("minutes"), 1)) for p in phases)
        if total == duration:
            continue
        diff = duration - total
        if diff > 0:
            target = max(phases, key=lambda p: _int(p.get("minutes"), 1))
            target["minutes"] = _int(target.get("minutes"), 1) + diff
            adjustments.append(f"第{lesson.get('period')}课时增加 {diff} 分钟到“{target.get('name')}”")
        else:
            need = -diff
            guard = 0
            while need > 0 and guard < 100:
                guard += 1
                target = max(phases, key=lambda p: _int(p.get("minutes"), 1))
                current = _int(target.get("minutes"), 1)
                if current <= 1:
                    break
                take = min(need, current - 1)
                target["minutes"] = current - take
                need -= take
            adjustments.append(f"第{lesson.get('period')}课时从各环节扣减 {-diff} 分钟")
        # 保险：四舍五入后仍不一致时，直接改最后一个环节
        new_total = sum(_int(p.get("minutes"), 1) for p in phases)
        if new_total != duration and phases:
            phases[-1]["minutes"] = max(1, _int(phases[-1].get("minutes"), 1) + (duration - new_total))
    return adjustments


def _walk_strings(value: Any, path: str = "") -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _walk_strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _walk_strings(v, f"{path}[{i}]")


def validate_plan(
    plan: dict[str, Any],
    chapter: Chapter,
    required_custom_labels: list[str] | None = None,
    strict_coverage: bool = False,
    rendered_chars: int = 0,
    required_image_ids: list[str] | None = None,
) -> ValidationResult:
    result = ValidationResult()
    meta = plan.get("meta") or {}
    duration = _int(meta.get("duration_minutes"), 0)
    periods = _int(meta.get("periods"), 0)
    lessons = _get_list(plan.get("lessons"))
    objectives = _get_list(plan.get("objectives"))

    if not _text(meta.get("title")):
        result.add("META_TITLE", "error", "meta.title", "缺少课题名称。", "填写本课时/本章课题。")
    if not _text(meta.get("subject")):
        result.add("META_SUBJECT", "error", "meta.subject", "缺少学科。", "填写学科，如“信息科技”。")
    if not _text(meta.get("grade")):
        result.add("META_GRADE", "error", "meta.grade", "缺少年级。", "填写适用年级。")
    if not (20 <= duration <= 120):
        result.add("META_DURATION", "error", "meta.duration_minutes", f"单课时时长 {duration} 分钟不合理。", "设置为 40-45 分钟。")
    if not (1 <= periods <= 6):
        result.add("META_PERIODS", "error", "meta.periods", f"课时数 {periods} 不合理。", "设置为 1-6。")
    if len(lessons) != periods:
        result.add(
            "LESSON_COUNT",
            "error",
            "lessons",
            f"meta.periods={periods}，但 lessons 有 {len(lessons)} 个课时。",
            "补齐或合并 lessons，使课时数一致。",
        )

    analysis = plan.get("analysis") or {}
    if len(_text(analysis.get("content"))) < 40:
        result.add("ANALYSIS_CONTENT", "error", "analysis.content", "教材分析过短，无法支撑教学设计。", "结合教材原文写 120-250 字，说明内容地位、结构与育人价值。")
    if len(_text(analysis.get("status_quo"))) < 15:
        result.add("ANALYSIS_STUDENT", "error", "analysis.status_quo", "学情分析缺失或过短。", "写出学生已有基础、可能困难和差异化需求。")
    if not _get_list(analysis.get("focus_questions")):
        result.add("ANALYSIS_QUESTIONS", "warning", "analysis.focus_questions", "缺少贯穿课堂的驱动问题。", "补充 1-3 个能引发探究的问题。")

    if not (2 <= len(objectives) <= 5):
        result.add("OBJ_COUNT", "error", "objectives", f"教学目标有 {len(objectives)} 条，应为 2-5 条。", "合并或补充目标。")
    obj_ids: list[str] = []
    for i, obj in enumerate(objectives, start=1):
        loc = f"objectives[{i - 1}]"
        if not isinstance(obj, dict):
            result.add("OBJ_TYPE", "error", loc, "教学目标不是对象。", "使用 {id,domain,bloom,statement,evidence} 结构。")
            continue
        oid = _text(obj.get("id"))
        if not oid:
            result.add("OBJ_ID", "error", loc, "教学目标缺少编号。", "填写 O1/O2…")
        elif oid in obj_ids:
            result.add("OBJ_DUP", "error", loc, f"教学目标编号重复：{oid}。", "使用唯一编号。")
        else:
            obj_ids.append(oid)
        statement = _text(obj.get("statement"))
        if not (8 <= len(statement) <= 180):
            result.add("OBJ_STATEMENT", "error", loc + ".statement", f"目标表述长度 {len(statement)} 不合适。", "用“能……”写清可观察的学习结果。")
        elif not re.search(MEASURABLE_VERBS, statement):
            result.add("OBJ_VERB", "warning", loc + ".statement", f"目标“{statement[:30]}…”缺少可观察行为动词。", "加入“说出/比较/设计/解释”等行为动词。")
        if not _text(obj.get("evidence")):
            result.add("OBJ_EVIDENCE", "error", loc + ".evidence", "教学目标缺少达成证据。", "写出学生用什么作品/回答/操作证明目标达成。")
        if _text(obj.get("bloom")) not in BLOOM_LEVELS:
            result.add("OBJ_BLOOM", "warning", loc + ".bloom", f"认知层级“{obj.get('bloom')}”不在常用六层级中。", "使用记忆/理解/应用/分析/评价/创造。")
        if _text(obj.get("domain")) not in DOMAINS:
            result.add("OBJ_DOMAIN", "warning", loc + ".domain", f"目标维度“{obj.get('domain')}”不常见。", "使用知识/技能/思维/价值。")

    key_points = _get_list(plan.get("key_points"))
    difficulties = _get_list(plan.get("difficulties"))
    if not (1 <= len(key_points) <= 4):
        result.add("KEYPOINT_COUNT", "error", "key_points", f"教学重点有 {len(key_points)} 条，应为 1-4 条。", "聚焦本章最核心的 1-3 个重点。")
    if not (1 <= len(difficulties) <= 4):
        result.add("DIFF_COUNT", "error", "difficulties", f"教学难点有 {len(difficulties)} 条，应为 1-4 条。", "补充学生真实困难点。")
    if not _get_list(plan.get("methods")):
        result.add("METHODS", "error", "methods", "缺少教学方法。", "补充本课时采用的教学方法。")
    if not _get_list(plan.get("materials")):
        result.add("MATERIALS", "error", "materials", "缺少教学准备/资源。", "列出教材、课件、任务单、教具等。")
    for i, item in enumerate(difficulties):
        if isinstance(item, dict):
            if not _text(item.get("point")):
                result.add("DIFF_POINT", "error", f"difficulties[{i}]", "难点缺少描述。", "填写难点内容。")
            if len(_text(item.get("strategy"))) < 8:
                result.add("DIFF_STRATEGY", "warning", f"difficulties[{i}]", "难点缺少突破策略。", "写出具体支架、活动或表征方式。")

    align: dict[str, list[str]] = {oid: [] for oid in obj_ids}
    total_phase_minutes = 0
    phase_count = 0
    assessed = 0
    for li, lesson in enumerate(lessons):
        if not isinstance(lesson, dict):
            continue
        loc = f"lessons[{li}]"
        lesson_duration = _int(lesson.get("duration_minutes"), duration)
        if lesson_duration != duration:
            result.add("LESSON_DURATION", "error", loc + ".duration_minutes", f"课时时长为 {lesson_duration}，与设置的 {duration} 不一致。", "统一为规定时长。")
        phases = _get_list(lesson.get("phases"))
        if not (3 <= len(phases) <= 9):
            result.add("PHASE_COUNT", "error", loc + ".phases", f"本课时有 {len(phases)} 个环节，应为 3-9 个。", "合并或拆分教学环节。")
        phase_sum = 0
        for pi, phase in enumerate(phases):
            if not isinstance(phase, dict):
                continue
            ploc = f"{loc}.phases[{pi}]"
            phase_count += 1
            minutes = _int(phase.get("minutes"), 0)
            phase_sum += minutes
            if minutes <= 0:
                result.add("PHASE_MINUTES", "error", ploc + ".minutes", "环节时间必须为正整数。", "分配具体分钟数。")
            if not _text(phase.get("name")):
                result.add("PHASE_NAME", "error", ploc + ".name", "环节缺少名称。", "如“情境导入”“任务一：……”。")
            if len(_text(phase.get("teacher_activity"))) < 15:
                result.add("PHASE_TEACHER", "error", ploc + ".teacher_activity", "教师活动缺失或过于笼统。", "写清教师说什么、做什么、如何巡视指导。")
            if len(_text(phase.get("student_activity"))) < 10:
                result.add("PHASE_STUDENT", "error", ploc + ".student_activity", "学生活动缺失或过于笼统。", "写清学生做什么、产出什么。")
            if len(_text(phase.get("intent"))) < 8:
                result.add("PHASE_INTENT", "error", ploc + ".intent", "缺少设计意图。", "说明该环节为什么这样设计、支撑哪条目标。")
            oids = [_text(x) for x in _get_list(phase.get("objective_ids")) if _text(x)]
            if not oids:
                result.add("PHASE_ALIGN", "warning", ploc + ".objective_ids", "环节没有关联教学目标。", "至少关联一个目标编号。")
            for oid in oids:
                if oid not in align:
                    result.add("ALIGN_UNKNOWN", "error", ploc + ".objective_ids", f"引用了不存在的目标编号：{oid}。", "改为已定义的目标编号。")
                else:
                    align[oid].append(f"P{li + 1}-{pi + 1}")
            if _text(phase.get("assessment")):
                assessed += 1
            elif phase_count and phase_name_is_assessable(_text(phase.get("name"))):
                result.add("PHASE_ASSESS", "warning", ploc + ".assessment", "该环节没有评价方式。", "补充观察点、提问、任务单或互评规则。")
        total_phase_minutes += phase_sum
        if phases and phase_sum != lesson_duration:
            result.add(
                "PHASE_SUM",
                "error",
                loc + ".phases",
                f"环节时间合计 {phase_sum} 分钟，与本课时 {lesson_duration} 分钟不一致。",
                f"调整环节分钟数，使合计精确等于 {lesson_duration}。",
            )

    if phase_count:
        ratio = assessed / phase_count
        if ratio < 0.6:
            result.add("ASSESS_RATIO", "error", "lessons[*].phases[*].assessment", f"只有 {ratio:.0%} 的环节有评价方式。", "让至少 60% 的环节包含可观察的评价任务。")

    for oid in obj_ids:
        if not align.get(oid):
            result.add("OBJ_UNCOVERED", "error", f"objectives[{oid}]", f"教学目标 {oid} 没有被任何教学环节覆盖。", "增加对应活动或在环节中关联该目标。")
    assessment_plan = _get_list(plan.get("assessment_plan"))
    assessed_ids = {_text(x.get("objective_id")) for x in assessment_plan if isinstance(x, dict)}
    for oid in obj_ids:
        if oid not in assessed_ids:
            result.add("ASSESS_UNCOVERED", "error", "assessment_plan", f"教学目标 {oid} 没有对应的评价任务。", "补充评价方式与达成证据。")

    board = plan.get("board_design") or {}
    if len(_get_list(board.get("content"))) < 3:
        result.add("BOARD", "error", "board_design.content", "板书设计内容不足 3 行。", "写出主板书的结构化内容。")
    homework = _get_list(plan.get("homework"))
    if not homework:
        result.add("HOMEWORK", "error", "homework", "缺少作业设计。", "布置基础/提升/拓展作业。")
    for i, item in enumerate(homework):
        if not _text(item.get("task") if isinstance(item, dict) else item):
            result.add("HOMEWORK_TASK", "error", f"homework[{i}]", "作业缺少任务描述。", "写清作业内容与要求。")
    homework_minutes = sum(_int(item.get("minutes"), 0) for item in homework if isinstance(item, dict))
    if homework_minutes > 60:
        result.add(
            "HOMEWORK_LOAD",
            "warning",
            "homework",
            f"作业预计总时长 {homework_minutes} 分钟，可能加重学生负担。",
            "把总量控制在 20-40 分钟，拓展作业改为选做。",
        )

    diff = plan.get("differentiation") or {}
    if len(_text(diff.get("support"))) < 10:
        result.add("DIFF_SUPPORT", "warning", "differentiation.support", "缺少对学习困难学生的支持策略。", "补充支架、同伴互助或分层任务。")
    if len(_text(diff.get("extension"))) < 10:
        result.add("DIFF_EXTENSION", "warning", "differentiation.extension", "缺少对学有余力学生的拓展。", "补充挑战任务或开放问题。")

    reflections = _get_list(plan.get("reflection"))
    if len(reflections) < 2:
        result.add("REFLECTION", "warning", "reflection", "教学反思要点少于 2 条。", "补充可课后自评的反思问题。")

    citations = _get_list(plan.get("citations"))
    grounded = 0
    for i, item in enumerate(citations):
        if not isinstance(item, dict):
            continue
        quote = _text(item.get("quote"))
        lines = _get_list(item.get("source_lines"))
        start = _int(lines[0], 0) if len(lines) > 0 else 0
        end = _int(lines[1], start) if len(lines) > 1 else start
        if start < chapter.start_line or end > chapter.end_line or start > end:
            result.add("CITE_RANGE", "error", f"citations[{i}].source_lines", f"引用行号 {start}-{end} 超出本章范围 {chapter.start_line}-{chapter.end_line}。", "改为教材中的真实行号。")
        hit = ground_quote(chapter, quote) if quote else None
        if not hit:
            result.add("CITE_UNVERIFIED", "error", f"citations[{i}].quote", "引用无法在教材原文中定位，可能是改写或编造。", "逐字摘录教材原句，并给出行号。")
        else:
            grounded += 1
            src, s, e = hit
            if [start, end] != [s, e]:
                result.add("CITE_LINES", "warning", f"citations[{i}].source_lines", f"行号 {start}-{end} 与原文位置 {s}-{e} 不一致。", "按原文位置修正行号。")
    if len(citations) < 2:
        result.add("CITE_COUNT", "error", "citations", f"教材引用只有 {len(citations)} 条，至少需要 2 条。", "补充能支撑核心知识点的原文引用。")

    coverage = _get_list(plan.get("coverage"))
    expected = chapter.key_section_titles or [s.title for s in chapter.sections if s.title]
    covered_norm = {normalize_label(_text(x.get("section"))) for x in coverage if isinstance(x, dict) and x.get("covered", True)}
    missing = [t for t in expected if normalize_label(t) not in covered_norm]
    ratio = (len(expected) - len(missing)) / max(1, len(expected))
    if missing:
        sev = "error" if strict_coverage else "warning"
        result.add("COVERAGE", sev, "coverage", f"有 {len(missing)} 个小节未标记覆盖：{'、'.join(missing)}。", "补充覆盖说明，或调整教学内容。")
    if ratio < 0.8 and not missing:
        result.add("COVERAGE_RATIO", "warning", "coverage", f"教材小节覆盖率仅 {ratio:.0%}。", "检查是否遗漏核心内容。")

    slides = _get_list((plan.get("ppt_brief") or {}).get("slide_outline"))
    if not (5 <= len(slides) <= 16):
        result.add("PPT_SLIDES", "warning", "ppt_brief.slide_outline", f"PPT 提纲有 {len(slides)} 页，建议 6-14 页。", "按教学环节补充或合并幻灯片。")
    for i, slide in enumerate(slides):
        if not isinstance(slide, dict):
            continue
        if not _text(slide.get("title")):
            result.add("PPT_TITLE", "warning", f"ppt_brief.slide_outline[{i}]", "幻灯片缺少标题。", "补充页面标题。")
        if len(_get_list(slide.get("bullets"))) < 2:
            result.add("PPT_BULLETS", "warning", f"ppt_brief.slide_outline[{i}]", "幻灯片要点少于 2 条。", "补充 2-5 条要点。")

    required = required_custom_labels or []
    custom = plan.get("custom_slots") or {}
    for label in required:
        if not _text(custom.get(label)):
            result.add("CUSTOM_SLOT", "error", f"custom_slots.{label}", f"模板自定义字段“{label}”没有内容。", "按模板要求补充该字段。")

    for path, text in _walk_strings(plan):
        for pat in PLACEHOLDER_PATTERNS:
            if pat in text:
                result.add("PLACEHOLDER", "error", path, f"内容中残留占位符/未完成标记：{pat}。", "替换为真实教学内容。")
                break

    if rendered_chars and rendered_chars < 1200 * max(1, periods):
        result.add("LENGTH", "warning", "document", f"成稿约 {rendered_chars} 字，内容可能偏薄。", "补充师生活动细节与评价证据。")

    figures = _get_list(plan.get("figures"))
    figure_ids = {_text(f.get("id")) for f in figures if isinstance(f, dict) and _text(f.get("id"))}
    all_phase_ids = {
        _text(p.get("id"))
        for lesson in lessons
        for p in (lesson.get("phases") or [])
        if isinstance(p, dict)
    }
    for i, fig in enumerate(figures):
        if not isinstance(fig, dict):
            continue
        floc = f"figures[{i}]"
        fid = _text(fig.get("id"))
        if not fid:
            result.add("FIG_ID", "error", floc, "配图缺少 id。", "image_id 使用 fig01 这种编号。")
        if len(_text(fig.get("caption"))) < 3:
            result.add("FIG_CAPTION", "error", floc + ".caption", "配图缺少图注。", "写 8-30 字的图注。")
        elif len(_text(fig.get("caption"))) > 80:
            result.add("FIG_CAPTION_LONG", "error", floc + ".caption", "配图图注过长，像整段 OCR。", "压缩为 8-30 字。")
        if not _text(fig.get("path")):
            result.add("FIG_PATH", "error", floc + ".path", "配图缺少文件路径。", "重新上传图片。")
        if _text(fig.get("placement")) == "phase" and _text(fig.get("phase_id")) not in all_phase_ids:
            result.add("FIG_PHASE", "error", floc + ".phase_id", f"配图指向不存在的环节：{fig.get('phase_id')}。", "改为已有 phase_id。")
    if required_image_ids:
        missing = [x for x in required_image_ids if x not in figure_ids]
        if missing:
            result.add("FIG_MISSING", "error", "figures", f"有 {len(missing)} 张上传图片没有放进教案：{'、'.join(missing)}。", "每张图恰好使用一次。")
        extra = [x for x in figure_ids if x not in set(required_image_ids)]
        if extra:
            result.add("FIG_EXTRA", "warning", "figures", f"教案引用了未上传的图片 id：{'、'.join(extra)}。", "检查图片是否都上传。")
    for lesson in lessons:
        for phase in lesson.get("phases") or []:
            if not isinstance(phase, dict):
                continue
            for fid in phase.get("figure_ids") or []:
                if fid not in figure_ids:
                    result.add("FIG_PHASE_ID", "error", f"lessons[{lesson.get('period')}].phases", f"环节引用了不存在的配图：{fid}。", "检查 figures 与 figure_ids 是否一致。")

    result.metrics = {
        "objectives": len(objectives),
        "periods": periods,
        "lessons": len(lessons),
        "phases": phase_count,
        "total_minutes": total_phase_minutes,
        "assessed_ratio": round(assessed / max(1, phase_count), 2),
        "coverage_ratio": round(ratio, 2),
        "citations": len(citations),
        "grounded_citations": grounded,
        "slides": len(slides),
        "figures": len(figures),
        "rendered_chars": rendered_chars,
    }
    result.grounded_citations = grounded
    return result


def phase_name_is_assessable(name: str) -> bool:
    return any(k in name for k in ("练习", "任务", "探究", "展示", "检测", "应用", "巩固", "小结", "总结"))


def summarize_validation(result: ValidationResult) -> str:
    if result.ok:
        return f"通过（{len(result.warnings)} 条建议）"
    return f"未通过（{len(result.errors)} 个错误，{len(result.warnings)} 条建议）"
