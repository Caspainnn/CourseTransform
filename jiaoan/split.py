from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .textbook import Chapter


EXPERIMENT_RE = re.compile(r"实践\s*\d+\s*[-－]\s*\d+|实践\s*\d+")
EXERCISE_RE = re.compile(r"习题|复习|总结|拓展")


@dataclass
class LessonBlueprint:
    title: str
    sections: list[str] = field(default_factory=list)
    kind: str = "theory"  # theory | experiment | exercise | general

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "sections": self.sections, "kind": self.kind}


def _clean_title(title: str) -> str:
    text = re.sub(r"^\s*[\d.]+\s*", "", str(title or "")).strip()
    text = re.sub(r"^实践\s*\d+\s*[-－]\s*\d+\s*", "", text).strip()
    text = re.sub(r"^实践\s*\d+\s*", "", text).strip()
    return text or str(title or "").strip()


def plan_lesson_blueprints(chapter: Chapter, max_lessons: int = 6) -> list[LessonBlueprint]:
    """按“章节里有几个实验”拆学时，而不是按字数平均切。

    - 0 个实验：按核心小节/篇幅，最多 2 个学时；
    - 1 个实验：理论 1 学时 + 实验 1 学时，实验不再拆上/下；
    - 2 个及以上实验：理论 1 学时 + 每个实验 1 学时 + 习题/复习 1 学时。
    """
    keys = chapter.key_sections()
    if not keys:
        return [LessonBlueprint(chapter.title, [s.title for s in chapter.sections], "general")]

    experiments: list[tuple[int, Any]] = []
    for i, section in enumerate(keys):
        if EXPERIMENT_RE.search(section.title or ""):
            experiments.append((i, section))

    if not experiments:
        # 没有“实践 x-y”编号时，不硬拆；按核心小节最多 2 段。
        if len(keys) <= 2 or chapter.chars <= 7000:
            return [LessonBlueprint(_clean_title(keys[0].title), [s.title for s in keys], "general")]
        mid = max(1, len(keys) // 2)
        return [
            LessonBlueprint("基础知识与原理", [s.title for s in keys[:mid]], "theory"),
            LessonBlueprint("实践与综合应用", [s.title for s in keys[mid:]], "experiment"),
        ]

    first_exp = experiments[0][0]
    theory = [s for s in keys[:first_exp] if "目标" not in s.title and s.chars > 0]
    blueprints: list[LessonBlueprint] = []
    if theory:
        theory_title = _clean_title(theory[0].title)
        if not theory_title or theory_title in {"知识要点", "基础知识", "概述"}:
            base = re.sub(r"^\s*第\s*[\d一二三四五六七八九十]+\s*章\s*", "", chapter.title).strip()
            base = re.sub(r"的安全应用$", "", base).strip()
            theory_title = (base + "原理") if base else "基础知识与原理"
        blueprints.append(LessonBlueprint(theory_title, [s.title for s in theory], "theory"))

    for exp_index, (key_index, section) in enumerate(experiments):
        end = keys[key_index + 1].start_line - 1 if key_index + 1 < len(keys) else chapter.end_line
        sections = [
            s.title
            for s in chapter.sections
            if section.start_line <= s.start_line <= end and not EXPERIMENT_RE.search(s.title or "")
        ]
        if not sections:
            sections = [section.title]
        # 子小节很多时只保留有内容的；具体内容仍由教材上下文提供。
        sections = [s for s in sections if s and s != section.title][:40]
        sections.insert(0, section.title)
        blueprints.append(LessonBlueprint(_clean_title(section.title) or f"实验{exp_index + 1}", sections, "experiment"))

    # 只有一个实验：不再把实验拆成上/下，也不额外加习题课时。
    if len(experiments) == 1:
        return blueprints[:max_lessons]

    last_exp = experiments[-1][0]
    after = [s for s in keys[last_exp + 1 :] if s.chars > 0]
    exercise = next((s for s in after if EXERCISE_RE.search(s.title or "")), None)
    if exercise is not None:
        blueprints.append(LessonBlueprint("习题与总结", [exercise.title], "exercise"))
    return blueprints[:max_lessons]


def apply_blueprints(plan: dict[str, Any], blueprints: list[LessonBlueprint] | None) -> dict[str, Any]:
    if not blueprints:
        return plan
    lessons = [x for x in (plan.get("lessons") or []) if isinstance(x, dict)]
    if not lessons:
        return plan
    # 模型/离线可能多给或少给课时：按 blueprint 数量裁剪/补齐，保证“一个实验一节”。
    while len(lessons) < len(blueprints):
        lessons.append({"period": len(lessons) + 1, "title": blueprints[len(lessons)].title, "duration_minutes": (plan.get("meta") or {}).get("duration_minutes", 45), "phase_outline": []})
    lessons = lessons[: len(blueprints)]
    seen_titles: set[str] = set()
    for i, (lesson, bp) in enumerate(zip(lessons, blueprints), start=1):
        lesson["period"] = i
        lesson["sections"] = list(bp.sections)
        # 按实验拆分时，标题由 blueprint 统一控制，避免模型写出
        # “5.1知识要点、5.2实践5-1……”这种把两个学时拼在一起的标题。
        lesson["title"] = bp.title
        seen_titles.add(bp.title)
    plan["lessons"] = lessons
    return plan
