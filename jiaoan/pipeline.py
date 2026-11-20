from __future__ import annotations

import copy
import json
import re
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .acceptance import AcceptanceResult, accept_docx, write_acceptance_report
from .llm import LLMClient, LLMError, LLMSettings
from .images import (
    ImageAsset,
    copy_images_to_output,
    discover_images,
    image_manifest,
    image_summary,
    images_prompt_text,
    write_image_filter_report,
)
from .nine_section import _asset_caption, build_nine_section_data, is_nine_section, render_nine_section
from .six_section import build_six_data, is_six_section, render_six_section
from .split import LessonBlueprint, apply_blueprints, plan_lesson_blueprints
from .offline import build_offline_plan
from .prompts import (
    build_figure_prompt,
    build_finish_prompt,
    build_phase_prompt,
    build_plan_prompt,
    build_repair_prompt,
    build_review_prompt,
    clip_context,
)
from .render import (
    fill_template_docx,
    render_canonical_docx,
    render_markdown,
    render_template_style_docx,
)
from .template import TemplateSpec, describe_template, extract_template_info, load_builtin_canonical, parse_template
from .textbook import Chapter, Textbook, context_for_llm, estimate_periods, ground_quote
from .utils import Console, now_iso, safe_name, split_sentences, write_text
from .validate import (
    Issue,
    ValidationResult,
    ground_citations,
    normalize_phase_minutes,
    normalize_plan,
    summarize_validation,
    validate_plan,
)


@dataclass
class ChapterResult:
    chapter_id: str
    title: str
    status: str
    output_dir: str = ""
    files: dict[str, str] = field(default_factory=dict)
    validation: dict[str, Any] = field(default_factory=dict)
    acceptance: dict[str, Any] = field(default_factory=dict)
    review_scores: dict[str, Any] = field(default_factory=dict)
    tokens: dict[str, Any] = field(default_factory=dict)
    template: str = ""
    warnings: list[str] = field(default_factory=list)
    error: str = ""
    duration_seconds: float = 0.0
    image_summary: dict[str, Any] = field(default_factory=dict)
    docx_files: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in {"ok", "ok_with_warnings"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter_id": self.chapter_id,
            "title": self.title,
            "status": self.status,
            "output_dir": self.output_dir,
            "files": self.files,
            "validation": self.validation,
            "acceptance": self.acceptance,
            "review_scores": self.review_scores,
            "tokens": self.tokens,
            "template": self.template,
            "warnings": self.warnings,
            "error": self.error,
            "duration_seconds": round(self.duration_seconds, 1),
            "image_summary": self.image_summary,
            "docx_files": self.docx_files,
        }


def estimate_chapter_lessons(chapter: Chapter, duration_minutes: int = 45) -> int:
    """一章应该拆成几个 45 分钟学时：优先看核心小节数，再用字数兜底。"""
    keys = chapter.key_sections()
    by_sections = max(1, len(keys))
    # 45 分钟一学时，按 6000-7000 字教材内容一学时估算；长实践章节可适当多拆。
    by_chars = max(1, (chapter.chars + 6999) // 7000)
    return max(1, min(6, max(by_sections, by_chars)))


def merge_brief(base: dict[str, Any], chapter: Chapter, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    base = base or {}
    defaults = base.get("defaults") if isinstance(base.get("defaults"), dict) else base
    merged = {k: v for k, v in defaults.items() if k not in {"chapters", "defaults"}}
    chapters = base.get("chapters") if isinstance(base.get("chapters"), dict) else {}
    chapter_over: dict[str, Any] = {}
    if chapter.id in chapters:
        chapter_over = chapters[chapter.id] or {}
    else:
        for key, value in chapters.items():
            if key and (key in chapter.title or chapter.title in key):
                chapter_over = value or {}
                break
    if isinstance(chapter_over, dict):
        merged.update(chapter_over)
    for k, v in (overrides or {}).items():
        if v is not None and v != "":
            merged[k] = v
    try:
        merged["duration_minutes"] = int(merged.get("duration_minutes") or 45)
    except (TypeError, ValueError):
        merged["duration_minutes"] = 45
    if "_periods_generated" in base:
        explicit_periods = bool(base.get("periods_explicit"))
    else:
        explicit_periods = bool(base.get("periods_explicit") or "periods" in base)
    merged["periods_explicit"] = explicit_periods
    try:
        merged["periods"] = int(merged.get("periods") or estimate_periods(chapter, merged["duration_minutes"]))
    except (TypeError, ValueError):
        merged["periods"] = estimate_periods(chapter, merged["duration_minutes"])
    merged["_periods_generated"] = True
    return merged


def resolve_template(chapter: Chapter, template_arg: str | None) -> str | None:
    if not template_arg:
        return None
    p = Path(template_arg)
    if p.is_file():
        return str(p)
    if not p.is_dir():
        raise FileNotFoundError(f"模板路径不存在：{p}")
    candidates = sorted([x for x in p.iterdir() if x.is_file() and x.suffix.lower() in {".docx", ".md", ".markdown"}])
    if not candidates:
        raise FileNotFoundError(f"模板目录中没有 .docx/.md 文件：{p}")
    # 1. 文件名精确匹配章节 id 或标题
    for f in candidates:
        stem = f.stem
        if stem.lower() == chapter.id.lower() or stem == chapter.title:
            return str(f)
    # 2. 文件名包含章节 id/标题
    for f in candidates:
        if chapter.id.lower() in f.stem.lower() or chapter.title in f.stem:
            return str(f)
    # 3. 默认模板
    for f in candidates:
        if f.stem.lower() in {"default", "template", "模板", "默认", "默认模板", "教案模板"}:
            return str(f)
    if len(candidates) == 1:
        return str(candidates[0])
    raise ValueError(
        f"模板目录中有多个文件，无法确定“{chapter.title}”用哪一个：{[f.name for f in candidates]}。"
        "请把文件命名为章节 id（如 ch01.docx）、章节标题，或使用 default.docx。"
    )


class LessonPipeline:
    def __init__(
        self,
        settings: LLMSettings,
        console: Console,
        client: LLMClient | None = None,
    ):
        self.settings = settings
        self.console = console
        self.client = client or LLMClient(settings, console)

    def run_chapter(
        self,
        chapter: Chapter,
        brief: dict[str, Any],
        template_path: str | None,
        out_root: str | Path,
        offline: bool = False,
        dry_run: bool = False,
        enable_review: bool = True,
        strict_coverage: bool = False,
        image_dir: str | Path | None = None,
        image_hints: dict[str, str] | None = None,
        include_unassigned_images: bool = True,
        filter_images: bool = True,
        keep_all_images: bool = False,
    ) -> ChapterResult:
        started = time.time()
        result = ChapterResult(chapter_id=chapter.id, title=chapter.title, status="running")
        brief = merge_brief(brief, chapter)
        token_before = self._token_snapshot()
        out_dir = Path(out_root) / f"{chapter.id}_{safe_name(chapter.title, 32)}"
        result.output_dir = str(out_dir)
        self.console.info(f"章节：{chapter.title}（{chapter.chars} 字，{len(chapter.sections)} 节）")

        try:
            spec = parse_template(template_path) if template_path else load_builtin_canonical()
            result.template = spec.path
        except Exception as exc:
            result.status = "failed"
            result.error = f"模板解析失败：{exc}"
            self.console.error(result.error)
            result.duration_seconds = time.time() - started
            return result

        if spec.warnings:
            result.warnings.extend(spec.warnings)
            for w in spec.warnings:
                self.console.warn(w)
        self.console.info(f"模板：{spec.path}（{len(spec.slots)} 个字段{'，含教学过程表' if spec.phase_table else ''}）")

        structured_nine = is_nine_section(spec)
        structured_six = False if structured_nine else is_six_section(spec)
        structured_template = structured_nine or structured_six
        blueprints = plan_lesson_blueprints(chapter)
        if structured_template:
            info = extract_template_info(spec)
            template_duration = int(info.get("duration_minutes") or 45)
            if template_duration != int(brief.get("duration_minutes") or 45):
                result.warnings.append(f"已从模板读取单课时时长：{template_duration} 分钟。")
                self.console.warn(result.warnings[-1])
            brief["duration_minutes"] = template_duration
            if not brief.get("periods_explicit"):
                # 模板若明确写了章内课时数，以模板为准；否则按“实验个数”拆，而不是按字数平均切。
                brief["periods"] = int(info.get("periods") or len(blueprints))
                result.warnings.append(
                    f"模板是单学时教案；本章按实验/栏目拆分为 {brief['periods']} 个学时，分别输出 Word。"
                )
                self.console.warn(result.warnings[-1])
            else:
                result.warnings.append(f"按页面设置拆分为 {brief.get('periods')} 个学时。")
        self.console.info(f"课时：{brief.get('periods')} × {brief.get('duration_minutes')} 分钟")

        query = " ".join([s.label for s in spec.slots] + [str(brief.get("focus", "")), str(brief.get("curriculum_standard", ""))])
        context = context_for_llm(chapter, self.settings.max_context_chars, query=query)
        duration = int(brief["duration_minutes"])
        periods = int(brief["periods"])
        use_blueprints = blueprints if (not brief.get("periods_explicit") or len(blueprints) == periods) else None
        out_dir.mkdir(parents=True, exist_ok=True)

        if dry_run:
            prompt_dir = out_dir / "prompts"
            prompt_dir.mkdir(parents=True, exist_ok=True)
            write_text(out_dir / "教材上下文.md", context)
            if offline:
                write_text(prompt_dir / "说明.md", "离线模式不需要调用大模型；去掉 --dry-run 可直接生成离线演示稿。")
            else:
                sample_plan = normalize_plan(
                    build_offline_plan(chapter, brief, periods=periods, custom_labels=spec.custom_labels, blueprints=use_blueprints),
                    chapter,
                    brief,
                )
                msgs = build_plan_prompt(chapter, context, brief, spec, periods, duration)
                write_text(prompt_dir / "01_方案骨架.md", _format_messages(msgs))
                if sample_plan.get("lessons"):
                    msgs = build_phase_prompt(
                        sample_plan,
                        sample_plan["lessons"][0],
                        clip_context(context, 12000),
                        spec,
                        duration,
                    )
                    write_text(prompt_dir / "02_课时详案示例.md", _format_messages(msgs))
                msgs = build_finish_prompt(sample_plan, chapter, clip_context(context, 14000), spec)
                write_text(prompt_dir / "03_板书作业评价.md", _format_messages(msgs))
            result.status = "dry_run"
            result.files = {"context": str(out_dir / "教材上下文.md")}
            result.duration_seconds = time.time() - started
            self.console.ok(f"已输出 Prompt 预览：{out_dir / 'prompts'}")
            return result

        guide_path = write_template_guide(spec, out_dir)
        all_assets = discover_images(
            chapter,
            image_dir,
            hints=image_hints,
            include_unassigned=include_unassigned_images,
            filter_images=filter_images,
            keep_all=keep_all_images,
        )
        _apply_scope_filter(
            all_assets,
            chapter,
            [] if (keep_all_images or not filter_images) else _focus_ranges(chapter, structured_template, periods),
        )
        result.image_summary = image_summary(all_assets)
        image_report_path: str = ""
        if all_assets:
            write_text(out_dir / "图片清单.json", json.dumps(image_manifest(all_assets), ensure_ascii=False, indent=2))
            report_path = write_image_filter_report(out_dir, all_assets, chapter)
            image_report_path = str(report_path)
            summary = result.image_summary
            self.console.info(
                f"教材配图：{summary['total']} 张，保留 {summary['kept']} 张，剔除 {summary['rejected']} 张"
            )
            if summary["rejected"]:
                detail = "、".join(f"{k} {v}" for k, v in summary.get("rejected_by_category", {}).items())
                result.warnings.append(
                    f"教材配图已自动筛选：保留 {summary['kept']} 张，剔除 {summary['rejected']} 张"
                    + (f"（{detail}）" if detail else "")
                    + "，详见《教材配图筛选说明.md》。"
                )
            if summary["total"] and not summary["kept"]:
                result.warnings.append("所有上传图片都被判为不适合放进教案，Word 未插入配图；可在图注中写“保留”覆盖。")
        assets = [a for a in all_assets if a.keep]
        assets = copy_images_to_output(assets, out_dir)

        try:
            if offline:
                plan = build_offline_plan(chapter, brief, periods=periods, custom_labels=spec.custom_labels, blueprints=use_blueprints)
                plan = normalize_plan(plan, chapter, brief)
                plan = apply_blueprints(plan, use_blueprints)
                _, dropped = ground_citations(plan, chapter)
                if dropped:
                    result.warnings.append(f"{len(dropped)} 条离线引用未能锚定，已丢弃。")
            else:
                plan, review_data = self._generate_with_llm(chapter, brief, spec, context, periods, duration, enable_review, result, use_blueprints)
                result.review_scores = (review_data or {}).get("scores") or {}
        except LLMError as exc:
            if not structured_template:
                result.status = "failed"
                result.error = str(exc)
                self.console.error(result.error)
                result.duration_seconds = time.time() - started
                self._write_failure(result, out_dir)
                return result
            result.warnings.append(f"模型调用失败，已按教材规则骨架继续生成单学时模板：{exc}")
            self.console.warn(result.warnings[-1])
            plan = normalize_plan(build_offline_plan(chapter, brief, periods=periods, custom_labels=spec.custom_labels, blueprints=use_blueprints), chapter, brief)
            plan = apply_blueprints(plan, use_blueprints)
            plan.setdefault("meta", {})["periods"] = periods
            plan["meta"]["duration_minutes"] = duration
            ground_citations(plan, chapter)
        except Exception as exc:
            if structured_template:
                result.warnings.append(f"模型阶段内部错误，已按教材规则骨架继续生成单学时模板：{type(exc).__name__}: {exc}")
                self.console.warn(result.warnings[-1])
                self.console.error(traceback.format_exc(limit=6))
                plan = normalize_plan(build_offline_plan(chapter, brief, periods=periods, custom_labels=spec.custom_labels, blueprints=use_blueprints), chapter, brief)
                plan = apply_blueprints(plan, use_blueprints)
                plan.setdefault("meta", {})["periods"] = periods
                plan["meta"]["duration_minutes"] = duration
                ground_citations(plan, chapter)
            else:
                result.status = "failed"
                result.error = (
                    f"生成失败（内部错误）：{type(exc).__name__}: {exc}\n"
                    f"请点网页上的“查看本次完整运行日志”定位；中间文件在 {out_dir}。"
                )
                self.console.error(f"{type(exc).__name__}: {exc}")
                self.console.error(traceback.format_exc(limit=8))
                result.duration_seconds = time.time() - started
                self._write_failure(result, out_dir)
                return result

        # 配图：先让模型根据 OCR/图注/章节把图挂到环节，失败则用规则兜底
        if assets:
            plan = self._attach_figures(plan, assets, chapter, offline, result)
            plan = normalize_plan(plan, chapter, brief)
        else:
            plan["figures"] = []

        # 结构保底：模型偶尔只给 1 条目标或 0 条引用，直接判失败对教师没有帮助。
        # 这里只补“结构完整度”，内容仍来自教材原文/本章目标，并在报告里标注需复核。
        plan = ensure_plan_minimums(plan, chapter, result)

        # 每学时至少两张教学图：教材没有可用图时生成教学流程/知识结构示意图。
        if structured_nine and len(assets) < 2:
            from .images import generate_fallback_diagrams
            generated = generate_fallback_diagrams(out_dir, plan, chapter, 2 - len(assets))
            assets.extend(generated)
            figures = plan.setdefault("figures", [])
            known = {str(x.get("id")) for x in figures if isinstance(x, dict)}
            lessons = [x for x in (plan.get("lessons") or []) if isinstance(x, dict)]
            phases = (lessons[0].get("phases") if lessons else []) or []
            phase_id = str((phases[min(1, len(phases) - 1)] if phases else {}).get("id") or "")
            for asset in generated:
                if asset.id in known:
                    continue
                figure = {
                    "id": asset.id,
                    "path": asset.output_path or asset.path,
                    "caption": asset.alt,
                    "placement": "phase" if phase_id else "section",
                }
                if phase_id:
                    figure["phase_id"] = phase_id
                figures.append(figure)
            if result.image_summary is not None:
                result.image_summary["generated"] = len(generated)


        # 渲染 Markdown 后再校验篇幅（同时保留中间稿）
        for adjustment in normalize_phase_minutes(plan):
            result.warnings.append(f"时间归一：{adjustment}")
            self.console.warn(result.warnings[-1])
        md = render_markdown(plan, spec, offline=offline)
        v = validate_plan(
            plan,
            chapter,
            required_custom_labels=spec.custom_labels,
            strict_coverage=strict_coverage,
            rendered_chars=len(re.sub(r"\s", "", md)),
            required_image_ids=[a.id for a in assets],
        )
        if not offline and v.errors and self.settings.max_repair > 0:
            plan, md, v = self._repair_loop(chapter, plan, spec, context, v, md, result, brief, periods, duration, strict_coverage, [a.id for a in assets])
        if enable_review and not offline and v.ok:
            plan, v = self._llm_review(chapter, plan, v, result)
            if v.errors and self.settings.max_repair > 0:
                md = render_markdown(plan, spec, offline=False)
                plan, md, v = self._repair_loop(chapter, plan, spec, context, v, md, result, brief, periods, duration, strict_coverage, [a.id for a in assets])

        # 写文件
        files: dict[str, str] = {}
        write_text(out_dir / "lesson_plan.json", json.dumps(plan, ensure_ascii=False, indent=2))
        files["plan"] = str(out_dir / "lesson_plan.json")
        files["template_guide"] = str(guide_path)
        if all_assets:
            files["image_manifest"] = str(out_dir / "图片清单.json")
            if image_report_path:
                files["image_report"] = image_report_path
        write_text(out_dir / "教案.md", md)
        files["markdown"] = str(out_dir / "教案.md")

        docx_path = out_dir / "教案.docx"
        nine_data: dict[str, Any] | None = None
        accept_plan = plan
        all_acceptance: list[AcceptanceResult] = []
        used_assets_all: list[ImageAsset] = []
        try:
            if structured_nine:
                lessons = [x for x in (plan.get("lessons") or []) if isinstance(x, dict)]
                if not lessons:
                    lessons = [{"period": 1, "title": chapter.title, "duration_minutes": duration}]
                docx_entries: list[dict[str, Any]] = []
                for idx, lesson in enumerate(lessons):
                    nine_data = build_nine_section_data(plan, chapter, assets, lesson_index=idx, lesson_no=idx + 1)
                    lesson_title = str(nine_data["meta"]["lesson_title"] or lesson.get("title") or f"第{idx + 1}学时").strip()
                    if len(lessons) == 1:
                        lesson_path = docx_path
                    else:
                        lesson_path = out_dir / f"第{idx + 1:02d}学时_{safe_name(lesson_title, 30)}_教案.docx"
                    render_nine_section(spec, nine_data, lesson_path, base_dir=out_dir)
                    used = [asset for _, asset in nine_data.get("image_placements", [])]
                    used_assets_all.extend(used)
                    accept_plan = copy.deepcopy(plan)
                    accept_plan["figures"] = []
                    for phase_idx, asset in nine_data.get("image_placements", []):
                        accept_plan["figures"].append(
                            {
                                "id": asset.id,
                                "path": asset.output_path or asset.path,
                                "caption": _asset_caption(asset),
                                "placement": "phase",
                                "phase_id": f"P{phase_idx + 1}",
                            }
                        )
                    accept_plan["lessons"] = [
                        {
                            "period": 1,
                            "title": lesson_title,
                            "duration_minutes": duration,
                            "phases": [
                                {
                                    "id": f"P{i + 1}",
                                    "name": p["name"],
                                    "minutes": p["minutes"],
                                    "teacher_activity": p["teacher_activity"],
                                    "student_activity": p["student_activity"],
                                    "intent": p["intent"],
                                    "objective_ids": [],
                                    "figure_ids": [],
                                }
                                for i, p in enumerate(nine_data["phases"])
                            ],
                        }
                    ]
                    acc = accept_docx(lesson_path, accept_plan, out_dir)
                    all_acceptance.append(acc)
                    if len(lessons) > 1:
                        write_acceptance_report(out_dir / f"验收报告_第{idx + 1:02d}学时.md", acc, accept_plan)
                    docx_entries.append(
                        {
                            "path": str(lesson_path),
                            "title": f"第{idx + 1}学时 {lesson_title}",
                            "lesson_no": idx + 1,
                        }
                    )
                result.docx_files = docx_entries
                files["docx"] = docx_entries[0]["path"] if docx_entries else ""
                docx_path = Path(files["docx"]) if files["docx"] else docx_path
                if result.image_summary:
                    result.image_summary["used"] = len({a.id for a in used_assets_all})
                if result.validation:
                    metrics = result.validation.setdefault("metrics", {})
                    metrics["figures"] = len({a.id for a in used_assets_all})
                    metrics["periods"] = len(lessons)
                    metrics["lessons"] = len(lessons)
                    metrics["phases"] = sum(len(build_nine_section_data(plan, chapter, [], lesson_index=i, lesson_no=i + 1).get("phases", [])) for i in range(len(lessons)))
                    metrics["total_minutes"] = duration * len(lessons)
                if all_assets and image_report_path:
                    write_image_filter_report(out_dir, all_assets, chapter, used_assets_all)
                if all_acceptance:
                    combined = AcceptanceResult(passed=all(a.passed for a in all_acceptance))
                    for i, acc in enumerate(all_acceptance, start=1):
                        for check in acc.checks:
                            combined.add(f"第{i}学时 {check.name}", check.passed, check.detail)
                    combined.metrics = {"lessons": len(all_acceptance), "passed": sum(1 for a in all_acceptance if a.passed)}
                    acceptance = combined
            elif structured_six:
                lessons = [x for x in (plan.get("lessons") or []) if isinstance(x, dict)]
                if not lessons:
                    lessons = [{"period": 1, "title": chapter.title, "duration_minutes": duration}]
                docx_entries: list[dict[str, Any]] = []
                for idx, lesson in enumerate(lessons):
                    six_data = build_six_data(plan, chapter, assets, lesson_index=idx, lesson_no=idx + 1)
                    lesson_title = str(six_data["meta"]["lesson_title"] or lesson.get("title") or f"第{idx + 1}学时").strip()
                    if len(lessons) == 1:
                        lesson_path = docx_path
                    else:
                        lesson_path = out_dir / f"第{idx + 1:02d}学时_{safe_name(lesson_title, 30)}_教案.docx"
                    render_six_section(spec, six_data, lesson_path, base_dir=out_dir)
                    used = [asset for _, asset in six_data.get("image_placements", [])]
                    used_assets_all.extend(used)
                    accept_plan = copy.deepcopy(plan)
                    accept_plan["figures"] = []
                    for phase_idx, asset in six_data.get("image_placements", []):
                        accept_plan["figures"].append(
                            {
                                "id": asset.id,
                                "path": asset.output_path or asset.path,
                                "caption": _asset_caption(asset),
                                "placement": "phase",
                                "phase_id": f"P{phase_idx + 1}",
                            }
                        )
                    accept_plan["lessons"] = [
                        {
                            "period": 1,
                            "title": lesson_title,
                            "duration_minutes": duration,
                            "phases": [
                                {
                                    "id": f"P{i + 1}",
                                    "name": p["name"],
                                    "minutes": p["minutes"],
                                    "teacher_activity": p["teacher_activity"],
                                    "student_activity": p["student_activity"],
                                    "intent": p["intent"],
                                    "objective_ids": [],
                                    "figure_ids": [],
                                }
                                for i, p in enumerate(six_data["phases"])
                            ],
                        }
                    ]
                    acc = accept_docx(lesson_path, accept_plan, out_dir)
                    all_acceptance.append(acc)
                    if len(lessons) > 1:
                        write_acceptance_report(out_dir / f"验收报告_第{idx + 1:02d}学时.md", acc, accept_plan)
                    docx_entries.append(
                        {
                            "path": str(lesson_path),
                            "title": f"第{idx + 1}学时 {lesson_title}",
                            "lesson_no": idx + 1,
                        }
                    )
                result.docx_files = docx_entries
                files["docx"] = docx_entries[0]["path"] if docx_entries else ""
                docx_path = Path(files["docx"]) if files["docx"] else docx_path
                if result.image_summary:
                    result.image_summary["used"] = len({a.id for a in used_assets_all})
                if result.validation:
                    metrics = result.validation.setdefault("metrics", {})
                    metrics["figures"] = len({a.id for a in used_assets_all})
                    metrics["periods"] = len(lessons)
                    metrics["lessons"] = len(lessons)
                    metrics["total_minutes"] = duration * len(lessons)
                if all_assets and image_report_path:
                    write_image_filter_report(out_dir, all_assets, chapter, used_assets_all)
                if all_acceptance:
                    combined = AcceptanceResult(passed=all(a.passed for a in all_acceptance))
                    for i, acc in enumerate(all_acceptance, start=1):
                        for check in acc.checks:
                            combined.add(f"第{i}学时 {check.name}", check.passed, check.detail)
                    combined.metrics = {"lessons": len(all_acceptance), "passed": sum(1 for a in all_acceptance if a.passed)}
                    acceptance = combined
            elif spec.kind == "docx" and spec.exemplar:
                render_template_style_docx(spec, plan, docx_path, offline=offline, base_dir=out_dir)
            elif spec.kind == "docx":
                fill_template_docx(spec, plan, docx_path, offline=offline, base_dir=out_dir)
            else:
                render_canonical_docx(plan, docx_path, offline=offline, base_dir=out_dir)
            files["docx"] = str(docx_path)
        except Exception as exc:
            result.warnings.append(f"按模板填充 DOCX 失败，已改用内置版式：{exc}")
            self.console.warn(result.warnings[-1])
            try:
                render_canonical_docx(plan, docx_path, offline=offline, base_dir=out_dir)
                files["docx"] = str(docx_path)
            except Exception as exc2:
                result.warnings.append(f"DOCX 渲染失败：{exc2}")
                self.console.error(result.warnings[-1])

        report = build_review_report(chapter, plan, spec, v, result.review_scores, self.settings.model, offline=offline)
        write_text(out_dir / "质检报告.md", report)
        files["review"] = str(out_dir / "质检报告.md")

        if (structured_nine or structured_six) and all_acceptance:
            acceptance = combined
            acc_path = write_acceptance_report(out_dir / "验收报告.md", acceptance, accept_plan, result.review_scores)
            files["acceptance"] = str(acc_path)
            result.acceptance = acceptance.to_dict()
            if acceptance.passed:
                self.console.ok(f"DOCX 验收通过（{len(all_acceptance)} 个学时）。")
            else:
                self.console.error("DOCX 验收未通过，需要返工；详见 " + str(acc_path))
        else:
            acceptance = None
            if docx_path.exists():
                acceptance = accept_docx(docx_path, accept_plan, out_dir)
                acc_path = write_acceptance_report(out_dir / "验收报告.md", acceptance, accept_plan, result.review_scores)
                files["acceptance"] = str(acc_path)
                result.acceptance = acceptance.to_dict()
                if not acceptance.passed:
                    self.console.error("DOCX 验收未通过，需要返工；详见 " + str(acc_path))
                else:
                    self.console.ok("DOCX 验收通过。")

        result.validation = v.to_dict()
        ok = v.ok and (acceptance is None or acceptance.passed)
        result.status = "ok" if ok and not result.warnings else ("ok_with_warnings" if ok else "quality_failed")
        if v.errors:
            self.console.error(f"质检未通过：{len(v.errors)} 个错误；详见 {out_dir / '质检报告.md'}")
        elif ok:
            self.console.ok(f"完成：{docx_path}（{summarize_validation(v)}）")
        if spec.warnings:
            for w in spec.warnings:
                if w not in result.warnings:
                    result.warnings.append(w)

        files["run"] = str(out_dir / "run.json")
        result.files = files
        result.duration_seconds = time.time() - started
        result.tokens = self._token_delta(token_before)
        write_text(out_dir / "run.json", json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return result

    def _write_failure(self, result: ChapterResult, out_dir: Path) -> None:
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            run_path = out_dir / "run.json"
            write_text(run_path, json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
            result.files["run"] = str(run_path)
        except Exception:
            pass

    def _token_snapshot(self) -> dict[str, int]:
        try:
            return dict(self.client.stats_summary().get("total") or {})
        except Exception:
            return {}

    def _token_delta(self, before: dict[str, int]) -> dict[str, int]:
        after = self._token_snapshot()
        return {k: int(after.get(k, 0)) - int(before.get(k, 0)) for k in set(before) | set(after)}

    def _attach_figures(
        self,
        plan: dict[str, Any],
        assets: list[ImageAsset],
        chapter: Chapter,
        offline: bool,
        result: ChapterResult,
    ) -> dict[str, Any]:
        if not assets:
            plan["figures"] = []
            return plan
        placements: dict[str, dict[str, Any]] = {}
        if not offline:
            try:
                data, _ = self.client.chat_json(
                    build_figure_prompt(plan, chapter, images_prompt_text(assets, chapter)),
                    purpose="figures",
                    temperature=0.2,
                    max_tokens=2048,
                )
                for item in data.get("figures") or []:
                    if not isinstance(item, dict):
                        continue
                    image_id = str(item.get("image_id") or "").strip()
                    if image_id:
                        placements[image_id] = item
            except LLMError as exc:
                result.warnings.append(f"配图分配调用失败，使用规则兜底：{exc}")
                self.console.warn(result.warnings[-1])

        figures: list[dict[str, Any]] = []
        for asset in assets:
            item = placements.get(asset.id) or {}
            chosen = self._figure_placement(plan, asset, chapter)
            placement = str(item.get("placement") or chosen["placement"])
            if placement not in {"phase", "analysis", "appendix"}:
                placement = chosen["placement"]
            phase_id = str(item.get("phase_id") or chosen.get("phase_id") or "")
            period = int(item.get("period") or chosen.get("period") or 1)
            if placement == "phase" and not self._phase_exists(plan, phase_id):
                placement, phase_id = chosen["placement"], chosen.get("phase_id", "")
                period = int(chosen.get("period") or period)
            # 模型选的环节如果明显没覆盖这张图所属小节，改用按小节匹配的兜底位置
            if (
                placement == "phase"
                and asset.section
                and chosen.get("placement") == "phase"
                and chosen.get("phase_id")
                and not self._phase_mentions_section(plan, phase_id, asset.section)
                and self._phase_mentions_section(plan, str(chosen.get("phase_id")), asset.section)
            ):
                phase_id = str(chosen.get("phase_id"))
                period = int(chosen.get("period") or period)
            # 导入/总结环节可以放图，但概念图优先放到概念讲解/探究环节
            llm_name = self._phase_display_name(plan, phase_id)
            chosen_name = self._phase_display_name(plan, str(chosen.get("phase_id") or ""))
            if (
                placement == "phase"
                and chosen.get("placement") == "phase"
                and any(k in llm_name for k in ("导入", "情境", "反思", "总结", "作业"))
                and chosen_name
                and not any(k in chosen_name for k in ("导入", "情境", "反思", "总结", "作业"))
            ):
                phase_id = str(chosen.get("phase_id"))
                period = int(chosen.get("period") or period)
            caption = _clean_caption(str(item.get("caption") or ""), chosen["caption"], asset)
            figures.append(
                {
                    "id": asset.id,
                    "image_id": asset.id,
                    "filename": asset.filename,
                    "path": asset.output_path,
                    "caption": caption[:80],
                    "placement": placement,
                    "period": period,
                    "phase_id": phase_id,
                    "section": str(item.get("section") or asset.section or ""),
                    "reason": str(item.get("reason") or chosen.get("reason") or ""),
                }
            )
        plan["figures"] = figures
        return plan

    def _phase_exists(self, plan: dict[str, Any], phase_id: str) -> bool:
        if not phase_id:
            return False
        return any(
            str(p.get("id")) == phase_id
            for lesson in plan.get("lessons") or []
            for p in lesson.get("phases") or []
        )

    def _phase_display_name(self, plan: dict[str, Any], phase_id: str) -> str:
        for lesson in plan.get("lessons") or []:
            for p in lesson.get("phases") or []:
                if str(p.get("id")) == phase_id:
                    return str(p.get("name") or "")
        return ""

    def _phase_mentions_section(self, plan: dict[str, Any], phase_id: str, section: str) -> bool:
        clean = re.sub(r"^第?[\d.一二三四五六七八九十]+\s*[章节]?\s*", "", section).strip()
        m = re.search(r"(\d+\.\d+)", section)
        for lesson in plan.get("lessons") or []:
            for phase in lesson.get("phases") or []:
                if str(phase.get("id")) != phase_id:
                    continue
                text = " ".join(str(phase.get(k) or "") for k in ("name", "teacher_activity", "student_activity", "intent"))
                return bool((clean and clean in text) or (m and m.group(1) in text))
        return False

    def _figure_placement(self, plan: dict[str, Any], asset: ImageAsset, chapter: Chapter) -> dict[str, Any]:
        phases: list[dict[str, Any]] = []
        for lesson in plan.get("lessons") or []:
            for phase in lesson.get("phases") or []:
                phases.append({"period": lesson.get("period"), **phase})
        caption = ""
        for candidate in (asset.alt, asset.hint, (asset.ocr_text.splitlines()[0] if asset.ocr_text else "")):
            text = str(candidate or "").strip()
            if len(text) >= 3:
                caption = text
                break
        if not caption:
            caption = f"教材配图：{Path(asset.filename).stem}"
        section = asset.section
        phase = None
        if section:
            clean = re.sub(r"^第?[\d.一二三四五六七八九十]+\s*[章节]?\s*", "", section).strip()
            num_match = re.search(r"(\d+\.\d+)", section)
            scored: list[tuple[int, dict[str, Any]]] = []
            for p in phases:
                text = " ".join(str(p.get(k) or "") for k in ("name", "teacher_activity", "student_activity", "intent"))
                score = 0
                if (clean and clean in text) or (num_match and num_match.group(1) in text):
                    score += 5
                name = str(p.get("name") or "")
                if any(k in name for k in ("概念", "讲解", "新知", "探究", "案例", "分析", "任务", "实践", "实验", "操作")):
                    score += 2
                if any(k in name for k in ("导入", "情境", "反思", "总结", "作业")):
                    score -= 4
                if score > 0:
                    scored.append((score, p))
            if scored:
                phase = max(scored, key=lambda x: x[0])[1]
        if phase is None:
            for p in phases:
                if any(k in str(p.get("name") or "") for k in ("概念", "讲解", "新知", "探究", "案例", "分析", "任务", "实践")):
                    phase = p
                    break
        if phase is None and phases:
            phase = phases[1] if len(phases) > 1 else phases[0]
        if phase is not None:
            return {
                "placement": "phase",
                "period": phase.get("period") or 1,
                "phase_id": phase.get("id") or "",
                "caption": caption,
                "reason": "根据小节/环节内容匹配",
            }
        return {"placement": "analysis", "caption": caption, "reason": "无匹配环节，放入教材分析"}

    def _max_tokens(self, default: int = 6144) -> int:
        """思考模型需要更多输出预算；普通模型保持默认。"""
        configured = int(getattr(self.settings, "max_tokens", 0) or 0)
        return max(default, min(16384, configured or 0))

    def _generate_with_llm(
        self,
        chapter: Chapter,
        brief: dict[str, Any],
        spec: TemplateSpec,
        context: str,
        periods: int,
        duration: int,
        enable_review: bool,
        result: ChapterResult,
        blueprints: list[LessonBlueprint] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        self.console.info("阶段 1/3：生成教学目标、重难点与课时大纲…")
        data, _ = self.client.chat_json(
            build_plan_prompt(chapter, context, brief, spec, periods, duration),
            purpose="plan",
            temperature=0.3,
            max_tokens=self._max_tokens(6144),
        )
        plan = normalize_plan(data, chapter, brief)
        plan.setdefault("meta", {})["periods"] = periods
        plan["meta"]["duration_minutes"] = duration
        plan = ensure_lesson_outlines(plan, chapter, periods, duration)
        plan = apply_blueprints(plan, blueprints)

        lessons = plan.get("lessons") or []
        for i, lesson in enumerate(lessons, start=1):
            self.console.info(f"阶段 2/3：展开第 {lesson.get('period', i)} 课时师生活动（{i}/{len(lessons)}）…")
            lesson_context = context_for_llm(
                chapter,
                min(14000, self.settings.max_context_chars),
                query=str(lesson.get("title", "")) + " " + " ".join(str(s) for s in (lesson.get("sections") or [])[:3]),
            )
            phase_data, _ = self.client.chat_json(
                build_phase_prompt(plan, lesson, clip_context(lesson_context, 14000), spec, duration),
                purpose=f"phases_p{i}",
                temperature=0.35,
                max_tokens=self._max_tokens(6144),
            )
            phases = phase_data.get("phases") if isinstance(phase_data, dict) else None
            if isinstance(phases, list):
                lesson["phases"] = phases
            else:
                self.console.warn(f"第 {lesson.get('period')} 课时没有返回 phases 数组。")
        plan = normalize_plan(plan, chapter, brief)
        for adjustment in normalize_phase_minutes(plan):
            result.warnings.append(f"时间归一：{adjustment}")

        self.console.info("阶段 3/3：补齐板书、作业、评价、引用与 PPT 提纲…")
        finish_context = context_for_llm(chapter, min(16000, self.settings.max_context_chars), query="板书 作业 评价 引用")
        finish_data, _ = self.client.chat_json(
            build_finish_prompt(plan, chapter, clip_context(finish_context, 16000), spec),
            purpose="finish",
            temperature=0.25,
            max_tokens=self._max_tokens(6144),
        )
        for key in ("board_design", "homework", "differentiation", "reflection", "citations", "ppt_brief", "custom_slots"):
            if key in finish_data and finish_data[key] not in (None, "", [], {}):
                plan[key] = finish_data[key]
        plan = normalize_plan(plan, chapter, brief)
        _, dropped = ground_citations(plan, chapter)
        if dropped:
            result.warnings.append(f"{len(dropped)} 条引用无法在教材中定位，已丢弃并等待修复。")
            self.console.warn(result.warnings[-1])

        review_data: dict[str, Any] | None = None
        return plan, review_data

    def _repair_loop(
        self,
        chapter: Chapter,
        plan: dict[str, Any],
        spec: TemplateSpec,
        context: str,
        v: ValidationResult,
        md: str,
        result: ChapterResult,
        brief: dict[str, Any] | None = None,
        expected_periods: int | None = None,
        expected_duration: int | None = None,
        strict_coverage: bool = False,
        required_image_ids: list[str] | None = None,
    ) -> tuple[dict[str, Any], str, ValidationResult]:
        best_plan = plan
        best_md = md
        best_v = v
        best_errors = len(v.errors)
        for attempt in range(1, self.settings.max_repair + 1):
            self.console.info(f"自动修复第 {attempt}/{self.settings.max_repair} 轮…")
            try:
                data, _ = self.client.chat_json(
                    build_repair_prompt(best_plan, best_v.repair_instructions(), chapter, clip_context(context, 14000), spec),
                    purpose=f"repair{attempt}",
                    temperature=0.2,
                    max_tokens=6144,
                )
            except LLMError as exc:
                result.warnings.append(f"自动修复失败：{exc}")
                self.console.warn(result.warnings[-1])
                break
            plan = normalize_plan(data, chapter, brief)
            plan.setdefault("meta", {})
            if expected_periods:
                plan["meta"]["periods"] = expected_periods
            if expected_duration:
                plan["meta"]["duration_minutes"] = expected_duration
            periods = int((plan.get("meta") or {}).get("periods") or 1)
            duration = int((plan.get("meta") or {}).get("duration_minutes") or 45)
            plan = ensure_lesson_outlines(plan, chapter, periods, duration)
            _, dropped = ground_citations(plan, chapter)
            if dropped:
                result.warnings.append(f"修复稿中 {len(dropped)} 条引用无法锚定，已丢弃。")
            for adjustment in normalize_phase_minutes(plan):
                result.warnings.append(f"修复稿时间归一：{adjustment}")
            md = render_markdown(plan, spec, offline=False)
            v = validate_plan(
                plan,
                chapter,
                required_custom_labels=spec.custom_labels,
                strict_coverage=strict_coverage,
                rendered_chars=len(re.sub(r"\s", "", md)),
                required_image_ids=required_image_ids,
            )
            if len(v.errors) < best_errors:
                best_plan, best_md, best_v, best_errors = plan, md, v, len(v.errors)
                self.console.info(f"修复有效：错误 {best_errors} 个")
            else:
                result.warnings.append(
                    f"第 {attempt} 轮修复没有减少错误（{len(v.errors)} >= {best_errors}），已保留修复前版本。"
                )
                self.console.warn(result.warnings[-1])
            if best_v.ok:
                break
        return best_plan, best_md, best_v

    def _llm_review(
        self,
        chapter: Chapter,
        plan: dict[str, Any],
        v: ValidationResult,
        result: ChapterResult,
    ) -> tuple[dict[str, Any], ValidationResult]:
        self.console.info("教研组长复核（LLM review）…")
        try:
            data, _ = self.client.chat_json(
                build_review_prompt(plan, chapter),
                purpose="review",
                temperature=0.1,
                max_tokens=2048,
            )
        except LLMError as exc:
            result.warnings.append(f"LLM 复核失败，已跳过：{exc}")
            self.console.warn(result.warnings[-1])
            return plan, v
        result.review_scores = data.get("scores") or {}
        issues = data.get("issues") or []
        added = 0
        for item in issues:
            if not isinstance(item, dict):
                continue
            severity = "error" if str(item.get("severity", "")).lower() == "error" else "warning"
            v.add(
                "REVIEW",
                severity,
                str(item.get("location") or "review"),
                str(item.get("problem") or "教研复核问题"),
                str(item.get("fix") or ""),
            )
            added += 1
        if added:
            result.warnings.append(f"教研复核提出 {added} 条意见。")
        return plan, v


def _focus_ranges(chapter: Chapter, structured: bool, periods: int) -> list[tuple[int, int]]:
    keys = chapter.key_sections()
    if not keys:
        return []
    chosen_idx: list[int] = []
    if structured and periods == 1:
        chosen_idx.append(0)
        for i, section in enumerate(keys):
            if "实践" in section.title:
                if i not in chosen_idx:
                    chosen_idx.append(i)
                break
    if not chosen_idx:
        return []
    ranges: list[tuple[int, int]] = []
    for idx in sorted(set(chosen_idx)):
        start = keys[idx].start_line
        end = keys[idx + 1].start_line - 1 if idx + 1 < len(keys) else chapter.end_line
        ranges.append((start, end))
    return ranges


def _apply_scope_filter(assets: list[ImageAsset], chapter: Chapter, ranges: list[tuple[int, int]]) -> None:
    if not ranges:
        return
    for asset in assets:
        if not asset.keep or not asset.source_line:
            continue
        if any(start <= asset.source_line <= end for start, end in ranges):
            continue
        asset.keep = False
        asset.category = "out_of_scope"
        asset.filter_reason = "不属于本课时覆盖的教材小节，未放入教案"
        asset.filter_confidence = 0.9


def ensure_plan_minimums(plan: dict[str, Any], chapter: Chapter, result: ChapterResult) -> dict[str, Any]:
    """补齐“至少 2 条目标、至少 2 条教材引用”的结构底线。

    这不是替代模型生成教学内容：新增目标只描述本章已有的学习结果，新增引用
    逐字取自教材并通过 ground_quote 定位。补了什么会写入 warnings，教师能看到。
    """
    objectives = plan.get("objectives")
    if not isinstance(objectives, list):
        objectives = []
    if len(objectives) < 2:
        keys = chapter.key_sections()
        topic = keys[0].title if keys else chapter.title
        existing = {str(o.get("statement") or "") for o in objectives if isinstance(o, dict)}
        candidate = {
            "id": "O2",
            "domain": "思维",
            "bloom": "分析",
            "statement": f"能结合“{topic}”的教材案例，分析关键过程或差异，并在任务单中写出判断依据。",
            "evidence": "小组任务单中的对比表、实验记录或口头分析",
        }
        if candidate["statement"] not in existing:
            if any(str(o.get("id")) == "O2" for o in objectives if isinstance(o, dict)):
                candidate["id"] = "O3"
            objectives.append(candidate)
            result.warnings.append("模型只返回了 1 条教学目标，已按本章内容补足 1 条结构目标，请教师复核表述。")
            plan["objectives"] = objectives
            # 补的目标也要挂到一个环节和一条评价上，否则会立刻被质检判为“未覆盖”。
            oid = candidate["id"]
            linked = False
            for lesson in plan.get("lessons") or []:
                for phase in lesson.get("phases") or []:
                    if not isinstance(phase, dict):
                        continue
                    ids = [str(x) for x in (phase.get("objective_ids") or [])]
                    if not ids:
                        phase["objective_ids"] = [oid]
                        linked = True
                        break
                    if not linked:
                        phase["objective_ids"] = ids + [oid]
                        linked = True
                        break
                if linked:
                    break
            assessment = plan.get("assessment_plan")
            if not isinstance(assessment, list):
                assessment = []
            if not any(str(a.get("objective_id")) == oid for a in assessment if isinstance(a, dict)):
                assessment.append(
                    {
                        "objective_id": oid,
                        "method": "任务单/课堂观察",
                        "evidence": candidate["evidence"],
                    }
                )
                plan["assessment_plan"] = assessment

    citations = plan.get("citations")
    if not isinstance(citations, list):
        citations = []
    if len(citations) < 2:
        seen = {str(c.get("quote") or "") for c in citations if isinstance(c, dict)}
        for section in chapter.key_sections():
            for sentence in split_sentences(section.content):
                quote = sentence.strip()
                if not (30 <= len(quote) <= 160) or quote in seen:
                    continue
                hit = ground_quote(chapter, quote)
                if not hit:
                    continue
                source, start, end = hit
                citations.append(
                    {
                        "claim": f"教材对“{section.title}”的原始表述",
                        "quote": source,
                        "source_lines": [start, end],
                        "verified": True,
                    }
                )
                seen.add(source)
                if len(citations) >= 3:
                    break
            if len(citations) >= 3:
                break
        if len(citations) >= 2:
            plan["citations"] = citations
            result.warnings.append("模型未给出足够引用，已从教材原文逐字补足引用并回查行号，请教师核对。")
    return plan


def ensure_lesson_outlines(plan: dict[str, Any], chapter: Chapter, periods: int, duration: int) -> dict[str, Any]:
    lessons = plan.get("lessons")
    if not isinstance(lessons, list):
        lessons = []
    lessons = [x for x in lessons if isinstance(x, dict)]
    if not lessons:
        sections = chapter.sections or []
        for p in range(periods):
            start = round(p * len(sections) / max(1, periods))
            end = round((p + 1) * len(sections) / max(1, periods))
            group = sections[start:end] or sections[-1:]
            title = group[0].title if group else chapter.title
            lessons.append({"period": p + 1, "title": title, "duration_minutes": duration, "phase_outline": []})
    for i, lesson in enumerate(lessons, start=1):
        lesson.setdefault("period", i)
        lesson.setdefault("duration_minutes", duration)
        lesson.setdefault("title", f"第{i}课时")
        if not isinstance(lesson.get("phase_outline"), list) or not lesson["phase_outline"]:
            lesson["phase_outline"] = [
                {"name": "情境导入", "minutes": 5, "purpose": "激活经验", "objective_ids": ["O1"]},
                {"name": "新知探究", "minutes": 12, "purpose": "建立概念", "objective_ids": ["O1"]},
                {"name": "任务实践", "minutes": 15, "purpose": "迁移应用", "objective_ids": ["O2"]},
                {"name": "交流评价", "minutes": 8, "purpose": "外化思维", "objective_ids": ["O2"]},
                {"name": "总结作业", "minutes": 5, "purpose": "结构化", "objective_ids": ["O1"]},
            ]
    # 模型没给 sections 时，按核心小节顺序均分到各课时，避免 4 个课时都讲同一段。
    keys = chapter.key_sections()
    if keys:
        for p, lesson in enumerate(lessons):
            if lesson.get("sections"):
                continue
            start = round(p * len(keys) / max(1, len(lessons)))
            end = round((p + 1) * len(keys) / max(1, len(lessons)))
            group = keys[start:end] or [keys[min(len(keys) - 1, p)]]
            lesson["sections"] = [s.title for s in group]
    plan["lessons"] = lessons
    return plan


def build_review_report(
    chapter: Chapter,
    plan: dict[str, Any],
    spec: TemplateSpec,
    v: ValidationResult,
    review_scores: dict[str, Any],
    model: str,
    offline: bool = False,
) -> str:
    meta = plan.get("meta") or {}
    lines = [
        f"# 教案质检报告：{chapter.title}",
        "",
        f"- 生成时间：{now_iso()}",
        f"- 生成方式：{'离线骨架（仅联调）' if offline else '大模型 + 规则校验'}",
        f"- 模型：{model if not offline else 'offline'}",
        f"- 模板：{spec.path}",
        f"- 结论：**{summarize_validation(v)}**",
        "",
        "## 一、硬性指标",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
    ]
    metrics = v.metrics or {}
    labels = {
        "objectives": "教学目标数",
        "periods": "课时数",
        "phases": "教学环节数",
        "total_minutes": "环节总分钟",
        "assessed_ratio": "带评价环节占比",
        "coverage_ratio": "教材小节覆盖率",
        "citations": "教材引用数",
        "grounded_citations": "可定位引用数",
        "slides": "PPT 提纲页数",
        "rendered_chars": "成稿字数（去空白）",
    }
    for key, label in labels.items():
        if key in metrics:
            value = metrics[key]
            if key == "assessed_ratio" or key == "coverage_ratio":
                value = f"{float(value):.0%}"
            lines.append(f"| {label} | {value} |")
    if review_scores:
        lines += ["", "## 二、教研复核评分", "", "| 维度 | 分数（1-5） |", "| --- | --- |"]
        for k, val in review_scores.items():
            lines.append(f"| {k} | {val} |")
    lines += ["", "## 三、问题清单", ""]
    if not v.issues:
        lines.append("未发现结构性问题。")
    else:
        lines += ["| 级别 | 位置 | 问题 | 修复建议 |", "| --- | --- | --- | --- |"]
        for issue in v.issues:
            lines.append(
                f"| {'错误' if issue.severity == 'error' else '建议'} | {issue.location} | {issue.message} | {issue.fix or '—'} |"
            )

    lines += ["", "## 四、目标—活动—评价对齐", "", "| 目标 | 覆盖环节 | 评价任务 |", "| --- | --- | --- |"]
    for obj in plan.get("objectives") or []:
        if not isinstance(obj, dict):
            continue
        oid = str(obj.get("id"))
        phases = []
        for lesson in plan.get("lessons") or []:
            for phase in lesson.get("phases") or []:
                if oid in [str(x) for x in (phase.get("objective_ids") or [])]:
                    phases.append(f"第{lesson.get('period')}课时 {phase.get('name')}")
        assessment = [
            str(a.get("method"))
            for a in plan.get("assessment_plan") or []
            if isinstance(a, dict) and str(a.get("objective_id")) == oid
        ]
        lines.append(f"| {oid} | {'；'.join(phases) or '—'} | {'；'.join(assessment) or '—'} |")

    lines += ["", "## 五、教材覆盖", "", "| 小节 | 是否覆盖 | 位置 |", "| --- | --- | --- |"]
    for item in plan.get("coverage") or []:
        if isinstance(item, dict):
            lines.append(f"| {item.get('section')} | {'是' if item.get('covered', True) else '否'} | {item.get('where') or '—'} |")

    lines += ["", "## 六、教材引用核对", ""]
    citations = plan.get("citations") or []
    if not citations:
        lines.append("没有引用。")
    for i, c in enumerate(citations, start=1):
        if isinstance(c, dict):
            lines.append(f"{i}. 第 {c.get('source_lines', ['?', '?'])[0]}-{c.get('source_lines', ['?', '?'])[-1]} 行：“{c.get('quote')}”")
            lines.append(f"   - 支撑判断：{c.get('claim')}")

    lines += [
        "",
        "## 七、教师复核清单",
        "",
        "1. 核对教学目标与班级学情是否匹配，必要时删减或降低认知层级。",
        "2. 核对教材引用与知识点表述，尤其是在教材版本不同的情况下。",
        "3. 按学校实际课时、机房/教具条件调整环节分钟数与任务规模。",
        "4. 检查作业总量与分层是否适合本班学生。",
        "5. 试讲后记录“学生实际卡点”，更新学情分析与难点突破策略。",
        "",
        "---",
        "",
        "本报告由教案工坊自动生成，不能替代教研组人工审核。",
    ]
    return "\n".join(lines)


def _clean_caption(model_caption: str, fallback: str, asset: ImageAsset) -> str:
    """图注控制在 4-50 字，避免把整段 OCR 塞进去。"""
    candidates = [model_caption, fallback, asset.alt, asset.hint]
    for text in candidates:
        t = re.sub(r"\s+", " ", str(text or "")).strip()
        if 4 <= len(t) <= 50 and "/" not in t:
            return t
    first_ocr = (asset.ocr_text.splitlines()[0] if asset.ocr_text else "").strip()
    if 4 <= len(first_ocr) <= 50:
        return first_ocr
    return f"教材配图：{Path(asset.filename).stem}"[:40]


def write_template_guide(spec: TemplateSpec, out_dir: str | Path) -> Path:
    """把“模板 word -> AI 理解 -> 说明书”落成一个可检查的文件。"""
    out = Path(out_dir)
    lines = [
        "# 模板说明书",
        "",
        f"- 模板文件：{spec.path}",
        f"- 模板类型：{spec.kind}",
        f"- 模板标题：{spec.title}",
        f"- 识别字段：{len(spec.slots)} 个",
    ]
    if is_nine_section(spec):
        lines += [
            "",
            "> 检测结果：这是**9 栏单学时模板**（课程基本信息 / 教学目标 / 重点与难点 / 学情分析 / 教学准备 / 教学过程 / 板书设计 / 作业与思考题 / 教学反思）。",
            "> 系统不会重建栏目，而是原位保留这 9 个 `Heading 2` 栏目、5 张固定表、6 列教学过程表、Heading 1/2/3 样式和页边距，只替换其中的内容。",
            "> 输出固定为 1 学时 × 45 分钟。",
        ]
    elif is_six_section(spec):
        lines += [
            "",
            "> 检测结果：这是**6 栏正文表格仿宋版**（课程基本信息 / 课程简介 / 教学内容 / 案例教学实施过程 / 课堂小结与学习评价 / 教学效果及反思）。",
            "> 系统原位保留 6 个栏目、5 张固定表、4 列教学过程表（环节/时间/教师推进/学生任务与检查点）和正文内嵌图位置，只替换内容。",
            "> 每个学时一份独立 Word，图片按模板放在正文内，不塞进表格单元格。",
        ]
    elif spec.exemplar:
        lines += [
            "",
            "> 检测结果：这是一个**已经写满某一课时的样例教案**，不是空白模板。",
            "> 如果只在标题后插入新内容，旧课时的正文、表格和配图会残留在成品里。",
            "> 因此系统采用“复用版式、重建内容”的方式：保留纸张、页边距、仿宋字体、字号和表格线，",
            "> 但所有标题、正文、表格、配图都按本次上传的教材重新生成。",
        ]
    lines += [
        "",
        "## 字段映射",
        "",
        "| 规范字段 | 模板叫法 | 位置 | 类型 |",
        "| --- | --- | --- | --- |",
    ]
    for slot in spec.slots:
        lines.append(f"| {slot.key} | {slot.label} | {slot.location()} | {slot.kind} |")
    if spec.phase_table:
        cols = "、".join(spec.phase_table.columns.keys())
        lines += [
            "",
            "## 教学过程表",
            "",
            f"- 表格序号：{spec.phase_table.table_index}",
            f"- 表头行：{spec.phase_table.header_row + 1}",
            f"- 列：{cols}",
            "- 系统会清空示例数据行，按教案 phases 逐行生成。",
        ]
    if spec.custom_labels:
        lines += ["", "## 自定义字段", ""]
        for label in spec.custom_labels:
            lines.append(f"- {label}")
    if spec.warnings:
        lines += ["", "## 警告", ""]
        for w in spec.warnings:
            lines.append(f"- {w}")
    lines += [
        "",
        "## 使用约定",
        "",
        "- 标题 + 段落式字段：内容插在标题下方；",
        "- {{字段}} 占位符：直接替换；",
        "- 表格字段：只填空单元格/占位符，不覆盖模板已有固定文字；",
        "- 教学过程表：按 phases 生成行，表头跨页重复，行不跨页断开。",
    ]
    path = out / "模板说明书.md"
    write_text(path, "\n".join(lines))
    write_text(out / "template_spec.json", json.dumps(spec.to_dict(), ensure_ascii=False, indent=2))
    return path


def _format_messages(messages: list[dict[str, str]]) -> str:
    lines = []
    for m in messages:
        lines.append(f"## {m['role']}\n\n{m['content']}\n")
    return "\n".join(lines)
