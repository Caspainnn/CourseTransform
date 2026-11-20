from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Iterable

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph

from .template import META_KEYS, TemplateSpec
from .utils import as_text, fmt_minutes, now_iso, write_text

OFFLINE_NOTE = (
    "【离线演示稿】本稿由规则骨架生成，仅用于验证“教材→教案→DOCX”流程；"
    "配置大模型后重新生成，才是可用于教学的成品教案。"
)


def _meta(plan: dict[str, Any]) -> dict[str, Any]:
    return plan.get("meta") or {}


def _lessons(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [x for x in (plan.get("lessons") or []) if isinstance(x, dict)]


def _text(value: Any) -> str:
    return as_text(value).strip()


def duration_text(plan: dict[str, Any]) -> str:
    meta = _meta(plan)
    try:
        periods = int(meta.get("periods") or len(_lessons(plan)) or 1)
    except (TypeError, ValueError):
        periods = 1
    try:
        minutes = int(meta.get("duration_minutes") or 45)
    except (TypeError, ValueError):
        minutes = 45
    if periods <= 1:
        return f"1课时（{minutes}分钟）"
    return f"{periods}课时（{minutes}分钟/课时）"


def _numbered(items: Iterable[Any]) -> list[str]:
    out = []
    for i, item in enumerate(items, start=1):
        text = _text(item)
        if text:
            out.append(f"{i}. {text}")
    return out


def _objective_lines(plan: dict[str, Any]) -> list[str]:
    lines = []
    for i, obj in enumerate(plan.get("objectives") or [], start=1):
        if not isinstance(obj, dict):
            continue
        tags = "·".join(x for x in [obj.get("domain", ""), obj.get("bloom", "")] if x)
        prefix = f"【{tags}】" if tags else ""
        line = f"{i}. {prefix}{_text(obj.get('statement'))}"
        evidence = _text(obj.get("evidence"))
        if evidence:
            line += f"（达成证据：{evidence}）"
        lines.append(line)
    return lines


def _difficulty_lines(plan: dict[str, Any]) -> list[str]:
    lines = []
    for i, item in enumerate(plan.get("difficulties") or [], start=1):
        if isinstance(item, dict):
            point = _text(item.get("point"))
            strategy = _text(item.get("strategy"))
        else:
            point, strategy = _text(item), ""
        if point:
            line = f"{i}. {point}"
            if strategy:
                line += f"（突破策略：{strategy}）"
            lines.append(line)
    return lines


def _process_blocks(plan: dict[str, Any]) -> list[str]:
    blocks: list[str] = []
    for lesson in _lessons(plan):
        title = _text(lesson.get("title")) or "本课时"
        minutes = lesson.get("duration_minutes") or _meta(plan).get("duration_minutes") or 45
        blocks.append(f"第{lesson.get('period', len(blocks) + 1)}课时：{title}（{fmt_minutes(int(minutes))}）")
        for phase in lesson.get("phases") or []:
            if not isinstance(phase, dict):
                continue
            name = _text(phase.get("name")) or "教学环节"
            pm = phase.get("minutes", "")
            oids = "、".join(str(x) for x in (phase.get("objective_ids") or []) if x)
            head = f"【{name}】（{pm}分钟" + (f"，对应目标 {oids}" if oids else "") + "）"
            blocks.append(head)
            if _text(phase.get("teacher_activity")):
                blocks.append(f"教师活动：{_text(phase.get('teacher_activity'))}")
            if _text(phase.get("student_activity")):
                blocks.append(f"学生活动：{_text(phase.get('student_activity'))}")
            if _text(phase.get("intent")):
                blocks.append(f"设计意图：{_text(phase.get('intent'))}")
            if _text(phase.get("assessment")):
                blocks.append(f"评价方式：{_text(phase.get('assessment'))}")
            resources = phase.get("resources") or []
            if resources:
                blocks.append(f"教学资源：{_text(resources)}")
    return blocks


def _board_lines(plan: dict[str, Any]) -> list[str]:
    board = plan.get("board_design") or {}
    if isinstance(board, str):
        return [board]
    lines: list[str] = []
    style = _text(board.get("style"))
    if style:
        lines.append(f"布局：{style}")
    for item in board.get("content") or []:
        text = _text(item)
        if text:
            lines.append(text)
    return lines


def _homework_lines(plan: dict[str, Any]) -> list[str]:
    lines = []
    for item in plan.get("homework") or []:
        if isinstance(item, dict):
            level = _text(item.get("level")) or "作业"
            task = _text(item.get("task"))
            minutes = item.get("minutes")
            suffix = f"（约{minutes}分钟）" if minutes else ""
            if task:
                lines.append(f"【{level}】{task}{suffix}")
        elif _text(item):
            lines.append(_text(item))
    return lines


def _assessment_lines(plan: dict[str, Any]) -> list[str]:
    lines = []
    for item in plan.get("assessment_plan") or []:
        if not isinstance(item, dict):
            continue
        oid = _text(item.get("objective_id"))
        method = _text(item.get("method"))
        evidence = _text(item.get("evidence"))
        text = f"{oid}｜{method}" if oid else method
        if evidence:
            text += f"｜证据：{evidence}"
        if text.strip("｜"):
            lines.append(text)
    return lines


def _coverage_lines(plan: dict[str, Any]) -> list[str]:
    lines = []
    for item in plan.get("coverage") or []:
        if not isinstance(item, dict):
            continue
        section = _text(item.get("section"))
        where = _text(item.get("where"))
        covered = item.get("covered", True)
        mark = "已覆盖" if covered else "未覆盖"
        lines.append(f"{section}：{mark}" + (f"（{where}）" if where else ""))
    return lines


def _figures(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [f for f in (plan.get("figures") or []) if isinstance(f, dict) and _text(f.get("path"))]


def _phase_name(plan: dict[str, Any], phase_id: str) -> str:
    for lesson in plan.get("lessons") or []:
        for phase in lesson.get("phases") or []:
            if str(phase.get("id")) == phase_id:
                return f"第{lesson.get('period')}课时 {_text(phase.get('name'))}"
    return ""


def _md_figure_lines(figs: list[dict[str, Any]], plan: dict[str, Any], show_phase: bool = False) -> list[str]:
    lines: list[str] = []
    for fig in figs:
        caption = _text(fig.get("caption")) or Path(_text(fig.get("path"))).stem
        lines.append(f"![{caption}]({_text(fig.get('path'))})")
        meta = f"*{caption}*"
        if show_phase:
            where = _phase_name(plan, _text(fig.get("phase_id")))
            if where:
                meta += f"（用于：{where}）"
        lines.append(meta)
        lines.append("")
    return lines


def _figures_for(plan: dict[str, Any], placement: str, lesson: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    out = []
    phase_ids = {str(p.get("id")) for p in (lesson.get("phases") or [])} if lesson else set()
    for fig in _figures(plan):
        if _text(fig.get("placement")) != placement:
            continue
        if placement == "phase" and lesson is not None:
            if str(fig.get("phase_id")) in phase_ids:
                out.append(fig)
        elif placement == "phase" and lesson is None:
            out.append(fig)
        elif placement != "phase":
            out.append(fig)
    return out


def _custom_blocks(plan: dict[str, Any], slot_key: str, label: str) -> list[str]:
    custom = plan.get("custom_slots") or {}
    if not isinstance(custom, dict):
        return []
    value = custom.get(label)
    if value is None:
        value = custom.get(slot_key)
    if value is None:
        # 宽松匹配：忽略标点/空白
        for k, v in custom.items():
            if re.sub(r"\s", "", str(k)) == re.sub(r"\s", "", label):
                value = v
                break
    if value is None:
        return []
    if isinstance(value, list):
        return [_text(v) for v in value if _text(v)]
    text = _text(value)
    return [line.strip() for line in text.splitlines() if line.strip()] if text else []


def slot_blocks(plan: dict[str, Any], key: str, label: str = "") -> list[str]:
    meta = _meta(plan)
    analysis = plan.get("analysis") or {}
    if key == "title":
        return [_text(meta.get("title")) or "教学设计"]
    if key == "meta":
        parts = [f"学科：{_text(meta.get('subject'))}", f"年级：{_text(meta.get('grade'))}"]
        parts.append(f"课时：{duration_text(plan)}")
        if _text(meta.get("lesson_type")):
            parts.append(f"课型：{_text(meta.get('lesson_type'))}")
        return ["　　".join(parts)]
    if key == "subject":
        return [_text(meta.get("subject"))]
    if key == "grade":
        return [_text(meta.get("grade"))]
    if key == "duration":
        return [duration_text(plan)]
    if key == "lesson_type":
        return [_text(meta.get("lesson_type"))]
    if key == "textbook":
        return [_text(meta.get("textbook"))]
    if key == "analysis":
        lines = []
        if _text(analysis.get("content")):
            lines.append(_text(analysis.get("content")))
        for item in analysis.get("curriculum") or []:
            if _text(item):
                lines.append(f"课标依据：{_text(item)}")
        if analysis.get("focus_questions"):
            lines.append("驱动问题：" + "  ".join(_text(x) for x in analysis.get("focus_questions") if _text(x)))
        return lines
    if key == "student":
        return [_text(analysis.get("status_quo"))] if _text(analysis.get("status_quo")) else []
    if key == "objectives":
        return _objective_lines(plan)
    if key == "key_points":
        return _numbered(plan.get("key_points") or [])
    if key == "difficulties":
        return _difficulty_lines(plan)
    if key == "key_difficulties":
        lines = ["【教学重点】"] + _numbered(plan.get("key_points") or [])
        lines.append("【教学难点】")
        lines += _difficulty_lines(plan)
        return lines
    if key == "methods":
        return _numbered(plan.get("methods") or [])
    if key == "materials":
        return _numbered(plan.get("materials") or [])
    if key == "process":
        return _process_blocks(plan)
    if key == "board":
        return _board_lines(plan)
    if key == "homework":
        return _homework_lines(plan)
    if key == "reflection":
        return _numbered(plan.get("reflection") or [])
    if key == "assessment":
        return _assessment_lines(plan)
    if key == "coverage":
        return _coverage_lines(plan)
    if key == "design":
        lines = []
        if _text(meta.get("design_framework")):
            lines.append(f"设计框架：{_text(meta.get('design_framework'))}")
        for q in analysis.get("focus_questions") or []:
            if _text(q):
                lines.append(f"驱动问题：{_text(q)}")
        return lines
    if key.startswith("custom:"):
        return _custom_blocks(plan, key, label)
    return []


def inline_value(plan: dict[str, Any], key: str, label: str = "") -> str:
    blocks = slot_blocks(plan, key, label)
    if not blocks:
        return ""
    if key in META_KEYS:
        return " ".join(blocks)
    return "；".join(b.replace("\n", " ") for b in blocks)


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def _md_cell(value: Any) -> str:
    text = _text(value)
    return text.replace("|", "\\|").replace("\n", "<br>") or "—"


def _md_phase_table(plan: dict[str, Any]) -> list[str]:
    lines = ["| 环节 | 时间 | 教师活动 | 学生活动 | 设计意图 | 评价 |", "| --- | --- | --- | --- | --- | --- |"]
    for lesson in _lessons(plan):
        for phase in lesson.get("phases") or []:
            if not isinstance(phase, dict):
                continue
            name = _text(phase.get("name"))
            minutes = phase.get("minutes", "")
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md_cell(name),
                        _md_cell(f"{minutes}分钟" if minutes else ""),
                        _md_cell(phase.get("teacher_activity")),
                        _md_cell(phase.get("student_activity")),
                        _md_cell(phase.get("intent")),
                        _md_cell(phase.get("assessment")),
                    ]
                )
                + " |"
            )
    return lines


def render_markdown(plan: dict[str, Any], spec: TemplateSpec | None = None, offline: bool = False) -> str:
    if spec and spec.kind == "md" and spec.md_text:
        return _render_md_template(plan, spec, offline)
    return _canonical_markdown(plan, offline)


def _canonical_markdown(plan: dict[str, Any], offline: bool = False) -> str:
    meta = _meta(plan)
    title = _text(meta.get("title")) or "教学设计"
    lines: list[str] = [f"# {title} 教学设计", ""]
    if offline:
        lines += [f"> {OFFLINE_NOTE}", ""]
    lines += [
        f"> 学科：{_text(meta.get('subject'))} ｜ 年级：{_text(meta.get('grade'))} ｜ "
        f"课时：{duration_text(plan)} ｜ 课型：{_text(meta.get('lesson_type'))}",
        "",
        "## 一、教材分析",
        "",
    ]
    analysis_blocks = slot_blocks(plan, "analysis")
    lines += ["\n\n".join(analysis_blocks) if analysis_blocks else "（待补充）"]
    analysis_figs = _figures_for(plan, "analysis")
    if analysis_figs:
        lines += ["", "**教材配图**", ""] + _md_figure_lines(analysis_figs, plan)
    lines += ["", "## 二、学情分析", ""]
    student_blocks = slot_blocks(plan, "student")
    lines += ["\n\n".join(student_blocks) if student_blocks else "（待补充）"]
    lines += ["", "## 三、教学目标", ""]
    lines += ["\n".join(_objective_lines(plan)) if _objective_lines(plan) else "（待补充）"]
    lines += ["", "## 四、教学重点与难点", ""]
    lines += ["**教学重点**", ""]
    lines += ["\n".join(_numbered(plan.get("key_points") or [])) if plan.get("key_points") else "（待补充）"]
    lines += ["", "**教学难点**", ""]
    lines += ["\n\n".join(_difficulty_lines(plan)) if plan.get("difficulties") else "（待补充）"]
    lines += ["", "## 五、教学方法与教学准备", ""]
    lines += ["**教学方法**", ""]
    lines += ["\n".join(_numbered(plan.get("methods") or [])) if plan.get("methods") else "（待补充）"]
    lines += ["", "**教学准备**", ""]
    lines += ["\n".join(_numbered(plan.get("materials") or [])) if plan.get("materials") else "（待补充）"]
    lines += ["", "## 六、教学过程", ""]
    for lesson in _lessons(plan):
        title_l = _text(lesson.get("title")) or "本课时"
        minutes = lesson.get("duration_minutes") or meta.get("duration_minutes") or 45
        lines += [f"### 第{lesson.get('period', 1)}课时：{title_l}（{fmt_minutes(int(minutes))}）", ""]
        lines += _md_phase_table({"lessons": [lesson]})
        lines += ["", f"**本课时合计：{int(minutes)}分钟**", ""]
        phase_figs = _figures_for(plan, "phase", lesson)
        if phase_figs:
            lines += ["**教材配图**", ""] + _md_figure_lines(phase_figs, plan, show_phase=True)
    lines += ["## 七、板书设计", ""]
    lines += ["\n".join(_board_lines(plan)) if _board_lines(plan) else "（待补充）"]
    lines += ["", "## 八、作业设计", ""]
    lines += ["\n\n".join(_homework_lines(plan)) if _homework_lines(plan) else "（待补充）"]
    lines += ["", "## 九、教学评价", ""]
    lines += ["\n\n".join(_assessment_lines(plan)) if _assessment_lines(plan) else "（待补充）"]
    if plan.get("differentiation"):
        diff = plan.get("differentiation") or {}
        lines += ["", "## 十、差异化支持", ""]
        if _text(diff.get("support")):
            lines.append(f"- 需要支持的学生：{_text(diff.get('support'))}")
        if _text(diff.get("extension")):
            lines.append(f"- 学有余力的学生：{_text(diff.get('extension'))}")
    lines += ["", "## 十一、教学反思", ""]
    lines += ["\n".join(_numbered(plan.get("reflection") or [])) if plan.get("reflection") else "（课后填写）"]
    appendix_figs = _figures_for(plan, "appendix")
    if appendix_figs:
        lines += ["", "## 十二、教材配图（附录）", ""] + _md_figure_lines(appendix_figs, plan)
    lines += ["", "---", "", f"*生成时间：{now_iso()}*", ""]
    return "\n".join(lines)


def _render_md_template(plan: dict[str, Any], spec: TemplateSpec, offline: bool = False) -> str:
    lines = (spec.md_text or "").splitlines()
    # 标题替换
    title = _text(_meta(plan).get("title")) + " 教学设计"
    for i, line in enumerate(lines):
        m = re.match(r"^#\s+(.+)$", line)
        if m and m.group(1).strip() in {"教学设计", "教案", "教学方案"}:
            lines[i] = f"# {title}"
            break

    # 先处理标题段落，倒序替换，避免行号漂移
    heading_sections = sorted(spec.md_sections, key=lambda s: s["start"], reverse=True)
    for section in heading_sections:
        start = int(section["start"]) - 1
        end = int(section["end"]) - 1
        blocks = slot_blocks(plan, section["key"], section.get("label", ""))
        if section["key"] == "analysis":
            blocks += _md_figure_lines(_figures_for(plan, "analysis"), plan)
        if section["key"] == "process":
            for lesson in _lessons(plan):
                figs = _figures_for(plan, "phase", lesson)
                if figs:
                    blocks += [_text(lesson.get("title")) or f"第{lesson.get('period')}课时"]
                    blocks += _md_figure_lines(figs, plan, show_phase=True)
        if not blocks:
            continue
        heading_line = lines[start]
        new_block = [heading_line, ""]
        for block in blocks:
            new_block.append(str(block))
            new_block.append("")
        lines[start : end + 1] = new_block

    text = "\n".join(lines)

    # 独立成行的占位符：按段落展开，而不是压成一行
    expanded: list[str] = []
    for line in text.splitlines():
        m = re.fullmatch(r"\s*\{\{\s*([^{}]+?)\s*\}\}\s*", line)
        if m:
            key = _key_for_token(m.group(1).strip())
            blocks = slot_blocks(plan, key, m.group(1).strip()) if key else []
            if blocks:
                for block in blocks:
                    expanded.append(str(block))
                    expanded.append("")
                continue
        expanded.append(line)
    text = "\n".join(expanded)

    # 再替换行内占位符
    def repl(m: re.Match[str]) -> str:
        raw = m.group(1).strip()
        key = _key_for_token(raw)
        if not key:
            return ""
        return inline_value(plan, key, raw)

    text = re.sub(r"\{\{\s*([^{}]+?)\s*\}\}", repl, text)
    appendix_figs = _figures_for(plan, "appendix")
    if appendix_figs:
        text += "\n\n## 教材配图（附录）\n\n" + "\n".join(_md_figure_lines(appendix_figs, plan))
    if offline:
        text = text.replace("\n\n", f"\n\n> {OFFLINE_NOTE}\n\n", 1)
    text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n\n---\n\n" + f"*生成时间：{now_iso()}*\n"
    return text


def _key_for_token(raw: str) -> str | None:
    from .template import _match_key

    key, _ = _match_key(raw, allow_custom=True)
    return key


# ---------------------------------------------------------------------------
# DOCX canonical
# ---------------------------------------------------------------------------


def _set_run_font(run, name: str = "宋体", size: float | None = 12, bold: bool | None = None) -> None:
    run.font.name = name
    rpr = run._r.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    rfonts.set(qn("w:eastAsia"), name)
    if size:
        run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold


def _shade_cell(cell: _Cell, color: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), color)


def _set_cell_margins(cell: _Cell, top: int = 60, bottom: int = 60, left: int = 100, right: int = 100) -> None:
    tc = cell._tc
    tc_pr = tc.get_or_add_tcPr()
    mar = tc_pr.first_child_found_in("w:tcMar")
    if mar is None:
        mar = OxmlElement("w:tcMar")
        tc_pr.append(mar)
    for tag, value in (("w:top", top), ("w:bottom", bottom), ("w:left", left), ("w:right", right)):
        node = mar.find(qn(tag))
        if node is None:
            node = OxmlElement(tag)
            mar.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def _set_repeat_header(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    tbl_header = OxmlElement("w:tblHeader")
    tbl_header.set(qn("w:val"), "true")
    tr_pr.append(tbl_header)


def _set_row_cant_split(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    if tr_pr.find(qn("w:cantSplit")) is None:
        tr_pr.append(OxmlElement("w:cantSplit"))


def _set_table_borders(table: Table, color: str = "BFBFBF", size: int = 4) -> None:
    tbl = table._tbl
    tbl_pr = tbl.tblPr
    borders = tbl_pr.find(qn("w:tblBorders"))
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        tbl_pr.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        node = borders.find(qn(f"w:{edge}"))
        if node is None:
            node = OxmlElement(f"w:{edge}")
            borders.append(node)
        node.set(qn("w:val"), "single")
        node.set(qn("w:sz"), str(size))
        node.set(qn("w:space"), "0")
        node.set(qn("w:color"), color)


def _set_col_widths(table: Table, widths_cm: list[float]) -> None:
    table.autofit = False
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    tbl = table._tbl
    tbl_pr = tbl.tblPr
    # 固定列宽布局，否则 Word/LibreOffice 会按 tblGrid 重新分配
    layout = tbl_pr.find(qn("w:tblLayout"))
    if layout is None:
        layout = OxmlElement("w:tblLayout")
        tbl_pr.append(layout)
    layout.set(qn("w:type"), "fixed")

    tbl_grid = tbl.find(qn("w:tblGrid"))
    if tbl_grid is not None:
        grid_cols = tbl_grid.findall(qn("w:gridCol"))
        for i, width in enumerate(widths_cm):
            if i < len(grid_cols):
                grid_cols[i].set(qn("w:w"), str(int(width * 567)))

    for row in table.rows:
        for i, width in enumerate(widths_cm):
            if i < len(row.cells):
                row.cells[i].width = Cm(width)
    # 表格总宽
    tbl_w = tbl_pr.find(qn("w:tblW"))
    if tbl_w is None:
        tbl_w = OxmlElement("w:tblW")
        tbl_pr.append(tbl_w)
    tbl_w.set(qn("w:type"), "dxa")
    tbl_w.set(qn("w:w"), str(int(sum(widths_cm) * 567)))


def _cell_text(cell: _Cell, text: str, size: float = 10, bold: bool = False, align=None, font: str = "宋体") -> None:
    cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    p = cell.paragraphs[0]
    for child in list(p._p):
        if child.tag != qn("w:pPr"):
            p._p.remove(child)
    p.paragraph_format.space_before = Pt(1)
    p.paragraph_format.space_after = Pt(1)
    p.paragraph_format.line_spacing = 1.15
    if align is not None:
        p.alignment = align
    for i, line in enumerate(str(text).splitlines()):
        if i > 0:
            p = cell.add_paragraph()
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(1)
            p.paragraph_format.line_spacing = 1.15
            if align is not None:
                p.alignment = align
        run = p.add_run(line)
        _set_run_font(run, font, size, bold)


def _add_body(doc: Document, text: str, indent: bool = True, bold: bool = False, size: float = 12, color=None) -> Paragraph:
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.line_spacing = 1.5
    pf.space_before = Pt(0)
    pf.space_after = Pt(3)
    if indent:
        pf.first_line_indent = Pt(size * 2)
    run = p.add_run(text)
    _set_run_font(run, "宋体", size, bold)
    if color:
        run.font.color.rgb = color
    return p


def _add_heading(doc: Document, text: str, level: int = 1) -> Paragraph:
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(14 if level == 1 else 8)
    pf.space_after = Pt(6 if level == 1 else 4)
    pf.keep_with_next = True
    run = p.add_run(text)
    _set_run_font(run, "黑体", 15 if level == 1 else 12, True)
    if level == 1:
        ppr = p._p.get_or_add_pPr()
        pbdr = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        bottom.set(qn("w:val"), "single")
        bottom.set(qn("w:sz"), "6")
        bottom.set(qn("w:space"), "2")
        bottom.set(qn("w:color"), "BFBFBF")
        pbdr.append(bottom)
        ppr.append(pbdr)
    return p


def _add_note(doc: Document, text: str) -> None:
    table = doc.add_table(rows=1, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    _set_table_borders(table, "E7B56B", 4)
    cell = table.cell(0, 0)
    _shade_cell(cell, "FFF4E5")
    _set_cell_margins(cell, 80, 80, 120, 120)
    _cell_text(cell, text, 9.5)
    cell.paragraphs[0].runs[0].font.color.rgb = RGBColor(0x8A, 0x5A, 0x00)


def _add_page_footer(section) -> None:
    footer = section.footer
    p = footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("第 ")
    _set_run_font(run, "宋体", 9)
    _add_field(p, "PAGE")
    run = p.add_run(" 页 / 共 ")
    _set_run_font(run, "宋体", 9)
    _add_field(p, "NUMPAGES")
    run = p.add_run(" 页")
    _set_run_font(run, "宋体", 9)


def _add_field(paragraph: Paragraph, field: str) -> None:
    run_begin = paragraph.add_run()
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    run_begin._r.append(fld_begin)

    run_instr = paragraph.add_run()
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = f" {field} "
    run_instr._r.append(instr)

    run_sep = paragraph.add_run()
    fld_sep = OxmlElement("w:fldChar")
    fld_sep.set(qn("w:fldCharType"), "separate")
    run_sep._r.append(fld_sep)

    run_text = paragraph.add_run("1")
    _set_run_font(run_text, "宋体", 9)

    run_end = paragraph.add_run()
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    run_end._r.append(fld_end)


def _figures_by_id(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(f.get("id")): f for f in _figures(plan)}


def _fig_path(fig: dict[str, Any], base_dir: str | Path | None) -> Path:
    p = Path(_text(fig.get("path")))
    return p if p.is_absolute() else Path(base_dir or ".") / p


def _add_picture_block(doc: Document, fig: dict[str, Any], base_dir: str | Path | None, width_cm: float = 10.0) -> Paragraph:
    path = _fig_path(fig, base_dir)
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(2)
    if not path.exists():
        run = p.add_run(f"（配图缺失：{_text(fig.get('filename'))}）")
        _set_run_font(run, "宋体", 9)
        return p
    run = p.add_run()
    run.add_picture(str(path), width=Cm(width_cm))
    cap = doc.add_paragraph()
    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cap.paragraph_format.space_after = Pt(6)
    caption = _text(fig.get("caption")) or path.stem
    crun = cap.add_run(caption)
    _set_run_font(crun, "宋体", 8.5)
    crun.font.color.rgb = RGBColor(0x59, 0x59, 0x59)
    return cap


def _append_picture_to_cell(cell: _Cell, fig: dict[str, Any], base_dir: str | Path | None, width_cm: float = 4.2) -> None:
    path = _fig_path(fig, base_dir)
    p = cell.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(1)
    if not path.exists():
        run = p.add_run(f"（配图缺失：{_text(fig.get('filename'))}）")
        _set_run_font(run, "宋体", 8)
        return
    run = p.add_run()
    run.add_picture(str(path), width=Cm(width_cm))
    cap = cell.add_paragraph()
    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cap.paragraph_format.space_after = Pt(2)
    crun = cap.add_run(_text(fig.get("caption")) or path.stem)
    _set_run_font(crun, "宋体", 7.5)
    crun.font.color.rgb = RGBColor(0x59, 0x59, 0x59)


def _insert_picture_after(anchor: Paragraph, fig: dict[str, Any], base_dir: str | Path | None, width_cm: float = 9.0) -> Paragraph:
    path = _fig_path(fig, base_dir)
    new_p = OxmlElement("w:p")
    anchor._p.addnext(new_p)
    p = Paragraph(new_p, anchor._parent)
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(4)
    if path.exists():
        run = p.add_run()
        run.add_picture(str(path), width=Cm(width_cm))
    else:
        run = p.add_run(f"（配图缺失：{_text(fig.get('filename'))}）")
        _set_run_font(run, "宋体", 9)
    cap_el = OxmlElement("w:p")
    new_p.addnext(cap_el)
    cap = Paragraph(cap_el, anchor._parent)
    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cap.paragraph_format.space_after = Pt(6)
    crun = cap.add_run(_text(fig.get("caption")) or path.stem)
    _set_run_font(crun, "宋体", 8.5)
    crun.font.color.rgb = RGBColor(0x59, 0x59, 0x59)
    return cap


def render_canonical_docx(plan: dict[str, Any], out_path: str | Path, offline: bool = False, base_dir: str | Path | None = None) -> Path:
    meta = _meta(plan)
    doc = Document()
    section = doc.sections[0]
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2.2)
    section.bottom_margin = Cm(2.0)
    section.left_margin = Cm(2.0)
    section.right_margin = Cm(2.0)

    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(12)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")

    title = _text(meta.get("title")) or "教学设计"
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(2)
    run = p.add_run(title)
    _set_run_font(run, "黑体", 20, True)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(10)
    run = p.add_run("教 学 设 计")
    _set_run_font(run, "黑体", 14, False)
    run.font.color.rgb = RGBColor(0x59, 0x59, 0x59)

    # 基本信息表
    info_rows = [
        ("学科", _text(meta.get("subject")), "年级", _text(meta.get("grade"))),
        ("课时", duration_text(plan), "课型", _text(meta.get("lesson_type"))),
        ("教材", _text(meta.get("textbook")), "", ""),
        ("设计框架", _text(meta.get("design_framework")), "", ""),
    ]
    table = doc.add_table(rows=len(info_rows), cols=4)
    _set_table_borders(table, "D0D0D0", 4)
    _set_col_widths(table, [2.6, 5.9, 2.6, 5.9])
    for r, row in enumerate(info_rows):
        for c, value in enumerate(row):
            cell = table.cell(r, c)
            _set_cell_margins(cell, 60, 60, 100, 100)
            if c in (0, 2):
                _shade_cell(cell, "F2F2F2")
                _cell_text(cell, value, 10, True, WD_ALIGN_PARAGRAPH.CENTER)
            else:
                _cell_text(cell, value, 10)
    # 合并“教材/设计框架”的值列
    for r in (2, 3):
        table.cell(r, 1).merge(table.cell(r, 3))

    if offline:
        _add_note(doc, OFFLINE_NOTE)

    _add_heading(doc, "一、教材分析")
    for line in slot_blocks(plan, "analysis") or ["（待补充）"]:
        _add_body(doc, line)
    for fig in _figures_for(plan, "analysis"):
        _add_picture_block(doc, fig, base_dir, 11.5)

    _add_heading(doc, "二、学情分析")
    for line in slot_blocks(plan, "student") or ["（待补充）"]:
        _add_body(doc, line)

    _add_heading(doc, "三、教学目标")
    for line in _objective_lines(plan) or ["（待补充）"]:
        _add_body(doc, line, indent=False)

    _add_heading(doc, "四、教学重点与难点")
    _add_body(doc, "教学重点", indent=False, bold=True)
    for line in _numbered(plan.get("key_points") or []) or ["（待补充）"]:
        _add_body(doc, line, indent=False)
    _add_body(doc, "教学难点", indent=False, bold=True)
    for line in _difficulty_lines(plan) or ["（待补充）"]:
        _add_body(doc, line, indent=False)

    _add_heading(doc, "五、教学方法与教学准备")
    _add_body(doc, "教学方法", indent=False, bold=True)
    for line in _numbered(plan.get("methods") or []) or ["（待补充）"]:
        _add_body(doc, line, indent=False)
    _add_body(doc, "教学准备", indent=False, bold=True)
    for line in _numbered(plan.get("materials") or []) or ["（待补充）"]:
        _add_body(doc, line, indent=False)

    _add_heading(doc, "六、教学过程")
    for lesson in _lessons(plan):
        lesson_title = _text(lesson.get("title")) or "本课时"
        minutes = lesson.get("duration_minutes") or meta.get("duration_minutes") or 45
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(6)
        p.paragraph_format.space_after = Pt(3)
        p.paragraph_format.keep_with_next = True
        run = p.add_run(f"第{lesson.get('period', 1)}课时：{lesson_title}（{fmt_minutes(int(minutes))}）")
        _set_run_font(run, "黑体", 12, True)
        _add_phase_table(doc, lesson, plan, base_dir)
        total = sum(int(ph.get("minutes") or 0) for ph in lesson.get("phases") or [] if isinstance(ph, dict))
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        p.paragraph_format.space_after = Pt(8)
        run = p.add_run(f"本课时合计：{total}分钟")
        _set_run_font(run, "宋体", 9.5)
        run.font.color.rgb = RGBColor(0x59, 0x59, 0x59)

    _add_heading(doc, "七、板书设计")
    board = _board_lines(plan) or ["（待补充）"]
    table = doc.add_table(rows=1, cols=1)
    _set_table_borders(table, "D9D9D9", 4)
    _set_col_widths(table, [17.0])
    cell = table.cell(0, 0)
    _shade_cell(cell, "FAFAFA")
    _set_cell_margins(cell, 120, 120, 160, 160)
    _cell_text(cell, "\n".join(board), 10.5)

    _add_heading(doc, "八、作业设计")
    for line in _homework_lines(plan) or ["（待补充）"]:
        _add_body(doc, line, indent=False)

    _add_heading(doc, "九、教学评价")
    assessments = plan.get("assessment_plan") or []
    if assessments:
        table = doc.add_table(rows=1, cols=3)
        _set_table_borders(table, "BFBFBF", 4)
        _set_col_widths(table, [3.0, 7.0, 7.0])
        headers = ["对应目标", "评价方式", "达成证据"]
        for c, h in enumerate(headers):
            cell = table.cell(0, c)
            _shade_cell(cell, "E8EEF7")
            _cell_text(cell, h, 9.5, True, WD_ALIGN_PARAGRAPH.CENTER)
        _set_repeat_header(table.rows[0])
        for item in assessments:
            if not isinstance(item, dict):
                continue
            row = table.add_row()
            for c, value in enumerate([item.get("objective_id"), item.get("method"), item.get("evidence")]):
                _cell_text(row.cells[c], _text(value), 9.5)
        _set_col_widths(table, [3.0, 7.0, 7.0])
    else:
        for line in _assessment_lines(plan) or ["（待补充）"]:
            _add_body(doc, line, indent=False)

    diff = plan.get("differentiation") or {}
    if _text(diff.get("support")) or _text(diff.get("extension")):
        _add_heading(doc, "十、差异化支持")
        if _text(diff.get("support")):
            _add_body(doc, f"需要支持的学生：{_text(diff.get('support'))}", indent=False)
        if _text(diff.get("extension")):
            _add_body(doc, f"学有余力的学生：{_text(diff.get('extension'))}", indent=False)

    _add_heading(doc, "十一、教学反思")
    for line in _numbered(plan.get("reflection") or []) or ["（课后填写）"]:
        _add_body(doc, line, indent=False)

    appendix_figs = _figures_for(plan, "appendix")
    if appendix_figs:
        _add_heading(doc, "十二、教材配图（附录）")
        for fig in appendix_figs:
            _add_picture_block(doc, fig, base_dir, 10.5)

    _add_page_footer(section)
    doc.core_properties.title = f"{title} 教学设计"
    doc.core_properties.author = "教案工坊"
    doc.core_properties.subject = _text(meta.get("subject"))

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    return out


def _add_phase_table(doc: Document, lesson: dict[str, Any], plan: dict[str, Any] | None = None, base_dir: str | Path | None = None) -> None:
    phases = [p for p in (lesson.get("phases") or []) if isinstance(p, dict)]
    table = doc.add_table(rows=1, cols=6)
    _set_table_borders(table, "BFBFBF", 4)
    _set_col_widths(table, [2.4, 1.2, 4.6, 4.1, 2.8, 1.9])
    headers = ["环节", "时间", "教师活动", "学生活动", "设计意图", "评价"]
    for c, h in enumerate(headers):
        cell = table.cell(0, c)
        _shade_cell(cell, "E8EEF7")
        _set_cell_margins(cell, 60, 60, 70, 70)
        _cell_text(cell, h, 9.5, True, WD_ALIGN_PARAGRAPH.CENTER)
    _set_repeat_header(table.rows[0])
    for phase in phases:
        row = table.add_row()
        _set_row_cant_split(row)
        oids = "、".join(str(x) for x in (phase.get("objective_ids") or []) if x)
        intent = _text(phase.get("intent"))
        if oids:
            intent = f"{intent}（目标：{oids}）" if intent else f"目标：{oids}"
        values = [
            _text(phase.get("name")),
            str(phase.get("minutes", "")),
            _text(phase.get("teacher_activity")),
            _text(phase.get("student_activity")),
            intent,
            _text(phase.get("assessment")) or "—",
        ]
        for c, value in enumerate(values):
            cell = row.cells[c]
            _set_cell_margins(cell, 50, 50, 70, 70)
            _cell_text(cell, value, 9.0, False, WD_ALIGN_PARAGRAPH.CENTER if c == 1 else None)
        if plan:
            figures = _figures_by_id(plan)
            for fid in phase.get("figure_ids") or []:
                fig = figures.get(str(fid))
                if fig:
                    _append_picture_to_cell(row.cells[2], fig, base_dir, 4.0)
    # 行增加后再统一列宽，避免新行回到等宽
    _set_col_widths(table, [2.4, 1.2, 4.6, 4.1, 2.8, 1.9])


# ---------------------------------------------------------------------------
# DOCX template filling
# ---------------------------------------------------------------------------


def _east_asia(run, default: str = "仿宋") -> str:
    try:
        rpr = run._r.rPr
        if rpr is not None:
            fonts = rpr.find(qn("w:rFonts"))
            if fonts is not None:
                value = fonts.get(qn("w:eastAsia"))
                if value:
                    return value
        return run.font.name or default
    except Exception:
        return default


def _template_style(spec: TemplateSpec) -> dict[str, Any]:
    style = {"body_font": "仿宋", "body_size": 12.0, "heading_font": "黑体", "heading_size": 14.0, "title_size": 18.0}
    sample = spec.sample_body
    if sample is not None and sample.runs:
        run = sample.runs[0]
        style["body_font"] = _east_asia(run, "仿宋")
        if run.font.size:
            style["body_size"] = float(run.font.size.pt)
    heading = next((s for s in spec.slots if s.kind == "heading" and s.para is not None and s.para.runs), None)
    if heading is not None:
        run = heading.para.runs[0]
        style["heading_font"] = _east_asia(run, "黑体")
        if run.font.size:
            style["heading_size"] = float(run.font.size.pt)
    for p in (spec.doc.paragraphs[:4] if spec.doc is not None else []):
        if p.runs and p.runs[0].font.size and p.runs[0].font.size.pt >= 16:
            style["title_size"] = float(p.runs[0].font.size.pt)
            style["title_font"] = _east_asia(p.runs[0], "黑体")
            break
    return style


def _tpl_body(doc: Document, text: str, style: dict[str, Any], indent: bool = True, bold: bool = False, size: float | None = None) -> Paragraph:
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.line_spacing = 1.5
    pf.space_before = Pt(0)
    pf.space_after = Pt(3)
    if indent:
        pf.first_line_indent = Pt((size or style["body_size"]) * 2)
    run = p.add_run(str(text or ""))
    _set_run_font(run, style["body_font"], size or style["body_size"], bold)
    return p


def _tpl_heading(doc: Document, text: str, style: dict[str, Any], level: int = 1) -> Paragraph:
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(12 if level == 1 else 7)
    pf.space_after = Pt(5 if level == 1 else 3)
    pf.keep_with_next = True
    run = p.add_run(text)
    _set_run_font(run, style["heading_font"], style["heading_size"] - (1 if level > 1 else 0), False)
    return p


def _tpl_caption(doc: Document, text: str, style: dict[str, Any]) -> Paragraph:
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.keep_with_next = True
    run = p.add_run(text)
    _set_run_font(run, style["body_font"], style["body_size"] - 1.5, False)
    return p


def _strip_mark(text: Any) -> str:
    s = str(text or "")
    s = re.sub(r"\[L\d+(?:\s*-\s*L?\d+)?\]", "", s)
    s = re.sub(r"（\s*教材原文[^）]*）", "", s)
    return re.sub(r"[ \t]+", " ", s).strip()


def _tpl_table(doc: Document, rows: list[list[str]], widths: list[float], style: dict[str, Any], header: bool = True) -> Table:
    table = doc.add_table(rows=len(rows), cols=len(widths))
    _set_table_borders(table, "C9C9C9", 4)
    _set_col_widths(table, widths)
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            cell = table.cell(r, c)
            _set_cell_margins(cell, 55, 55, 80, 80)
            if header and r == 0:
                _shade_cell(cell, "F2F2F2")
                _cell_text(cell, str(value), 10, True, WD_ALIGN_PARAGRAPH.CENTER, font=style["body_font"])
            else:
                _cell_text(
                    cell,
                    str(value),
                    10,
                    False,
                    WD_ALIGN_PARAGRAPH.CENTER if c == 0 else None,
                    font=style["body_font"],
                )
    if header:
        _set_repeat_header(table.rows[0])
    _set_col_widths(table, widths)
    return table


def render_template_style_docx(
    spec: TemplateSpec,
    plan: dict[str, Any],
    out_path: str | Path,
    offline: bool = False,
    base_dir: str | Path | None = None,
) -> Path:
    """复用上传模板的纸张、字体和表格风格，重建一份干净的本章教案。

    上传的模板如果是“已写满某一课时的样例”（例如第2章 GAN 的完整教案），
    直接在标题后插入内容会把旧的 GAN 文字、旧配图留在成品里。这里只继承
    模板的版式（页边距、仿宋正文、字号、表格线），所有正文和表格都按本章
    教材重新生成，保证不残留旧章节内容。
    """
    meta = _meta(plan)
    style = _template_style(spec)
    doc = Document()
    src_section = spec.doc.sections[0] if spec.doc is not None and spec.doc.sections else None
    section = doc.sections[0]
    if src_section is not None:
        section.page_width = src_section.page_width
        section.page_height = src_section.page_height
        section.top_margin = src_section.top_margin
        section.bottom_margin = src_section.bottom_margin
        section.left_margin = src_section.left_margin
        section.right_margin = src_section.right_margin
    else:
        section.page_width = Cm(21)
        section.page_height = Cm(29.7)
        section.top_margin = Cm(2.2)
        section.bottom_margin = Cm(2.0)
        section.left_margin = Cm(2.6)
        section.right_margin = Cm(2.2)

    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(style["body_size"])
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), style["body_font"])

    title = _text(meta.get("title")) or "教学设计"
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(2)
    run = p.add_run(title)
    _set_run_font(run, style.get("title_font", style["heading_font"]), style["title_size"], True)

    lessons = _lessons(plan)
    if len(lessons) <= 1:
        one_title = _text(lessons[0].get("title")) if lessons else "教学设计"
        one_title = re.sub(r"^第\d+课时[：: ]*", "", one_title)
        one_title = re.sub(r"（第\d+课时）$", "", one_title).strip()
        subtitle = f"第{lessons[0].get('period', 1) if lessons else 1}课时 · {one_title}"
    else:
        subtitle = f"第1-{len(lessons)}课时 · 单元教学设计"
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(8)
    run = p.add_run(subtitle)
    _set_run_font(run, style.get("title_font", style["heading_font"]), style["title_size"] - 2, True)

    if offline:
        _add_note(doc, OFFLINE_NOTE)

    # 一、课程基本信息
    _tpl_heading(doc, "一、课程基本信息", style)
    _tpl_body(doc, "本课时的授课内容、教学对象与实施条件如下表所示。", style)
    _tpl_caption(doc, "表1 课程基本信息与教学对象", style)
    materials = [_strip_mark(x) for x in (plan.get("materials") or []) if _strip_mark(x)]
    info_rows = [
        ["项目", "内容", "实施说明"],
        ["课程名称", _text(meta.get("subject")) or "信息科技", _text(meta.get("textbook")) or ""],
        ["授课章节", title, duration_text(plan)],
        ["授课对象", _text(meta.get("grade")) or "—", _text(meta.get("lesson_type")) or "新授课"],
        ["教学环境", "；".join(materials[:3]) if materials else "多媒体教室 / 机房", "4-6 人一组"],
        ["授课教师", "____________", "时间地点：____________"],
    ]
    _tpl_table(doc, info_rows, [2.6, 7.0, 6.0], style)

    # 二、课程简介
    _tpl_heading(doc, "二、课程简介", style)
    _tpl_heading(doc, "（一）课程定位", style, level=2)
    analysis = plan.get("analysis") or {}
    for line in [_strip_mark(analysis.get("content")), _strip_mark(analysis.get("status_quo"))]:
        if line:
            _tpl_body(doc, line, style)
    questions = [_strip_mark(x) for x in (analysis.get("focus_questions") or []) if _strip_mark(x)]
    if questions:
        _tpl_body(doc, "贯穿本课的问题：" + "；".join(questions), style, bold=False)
    for fig in _figures_for(plan, "analysis"):
        _add_picture_block(doc, fig, base_dir, 11.5)

    _tpl_heading(doc, "（二）课程目标", style, level=2)
    for line in _objective_lines(plan)[:5]:
        _tpl_body(doc, line, style, indent=False)

    # 三、教学内容
    _tpl_heading(doc, "三、教学内容", style)
    _tpl_heading(doc, "（一）授课内容定位", style, level=2)
    covered = [
        _text(x.get("section"))
        for x in (plan.get("coverage") or [])
        if isinstance(x, dict) and x.get("covered", True) and _text(x.get("section"))
    ]
    focus = "；".join(covered[:6]) if covered else "；".join(_text(l.get("title")) for l in lessons if _text(l.get("title")))
    _tpl_body(doc, f"本课聚焦：{focus}。", style)

    _tpl_heading(doc, "（二）本次教学目标", style, level=2)
    _tpl_body(doc, "本次教学目标及对应的学习证据如下表所示。", style)
    _tpl_caption(doc, "表2 教学目标与学习证据", style)
    objective_rows = [["目标维度", "可观察的学习行为", "学习证据"]]
    for obj in plan.get("objectives") or []:
        if not isinstance(obj, dict):
            continue
        objective_rows.append(
            [
                f"{_text(obj.get('domain')) or '知识'}·{_text(obj.get('bloom')) or '理解'}",
                _strip_mark(obj.get("statement")),
                _strip_mark(obj.get("evidence")),
            ]
        )
    if len(objective_rows) == 1:
        objective_rows.append(["—", "（待补充）", "（待补充）"])
    _tpl_table(doc, objective_rows, [2.6, 6.5, 6.5], style)

    _tpl_heading(doc, "（三）重点与难点", style, level=2)
    _tpl_body(doc, "教学重点", style, indent=False, bold=True)
    for line in _numbered(plan.get("key_points") or []) or ["（待补充）"]:
        _tpl_body(doc, line, style, indent=False)
    _tpl_body(doc, "教学难点", style, indent=False, bold=True)
    for line in _difficulty_lines(plan) or ["（待补充）"]:
        _tpl_body(doc, line, style, indent=False)

    _tpl_heading(doc, "（四）环境与课前准备", style, level=2)
    for line in _numbered(materials) or ["（待补充）"]:
        _tpl_body(doc, line, style, indent=False)
    if plan.get("methods"):
        _tpl_body(doc, "教学方法：" + "；".join(_strip_mark(x) for x in plan["methods"]), style, indent=False)

    # 四、案例教学实施过程
    _tpl_heading(doc, "四、案例教学实施过程", style)
    _tpl_body(doc, f"本课共 {len(lessons) or 1} 课时，每课时 {_text(meta.get('duration_minutes')) or 45} 分钟，具体流程如下。", style)
    table_no = 3
    for lesson in lessons:
        lesson_title = _text(lesson.get("title")) or "本课时"
        lesson_title = re.sub(r"^第\d+课时[：: ]*", "", lesson_title)
        lesson_title = re.sub(r"（第\d+课时）$", "", lesson_title).strip() or "本课时"
        minutes = lesson.get("duration_minutes") or meta.get("duration_minutes") or 45
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(7)
        p.paragraph_format.space_after = Pt(3)
        p.paragraph_format.keep_with_next = True
        run = p.add_run(f"第{lesson.get('period', 1)}课时：{lesson_title}（{fmt_minutes(int(minutes))}）")
        _set_run_font(run, style["heading_font"], style["body_size"] + 1, True)
        _tpl_caption(doc, f"表{table_no} 课堂教学过程与时间分配", style)
        _tpl_phase_table(doc, lesson, plan, style, base_dir)
        table_no += 1

    # 五、课堂小结与学习评价
    _tpl_heading(doc, "五、课堂小结与学习评价", style)
    _tpl_heading(doc, "（一）板书与关键结论", style, level=2)
    board = _board_lines(plan) or ["（待补充）"]
    table = doc.add_table(rows=1, cols=1)
    _set_table_borders(table, "D9D9D9", 4)
    _set_col_widths(table, [15.6])
    cell = table.cell(0, 0)
    _shade_cell(cell, "FAFAFA")
    _set_cell_margins(cell, 100, 100, 140, 140)
    _cell_text(cell, "\n".join(board), 10.5, font=style["body_font"])

    _tpl_heading(doc, "（二）课堂评价", style, level=2)
    assessments = [x for x in (plan.get("assessment_plan") or []) if isinstance(x, dict)]
    if assessments:
        assessment_rows = [["对应目标", "评价方式", "达成证据"]]
        for item in assessments:
            assessment_rows.append([_text(item.get("objective_id")), _strip_mark(item.get("method")), _strip_mark(item.get("evidence"))])
        _tpl_table(doc, assessment_rows, [2.6, 6.5, 6.5], style)
    else:
        for line in _assessment_lines(plan) or ["（待补充）"]:
            _tpl_body(doc, line, style, indent=False)

    _tpl_heading(doc, "（三）分层作业与反馈", style, level=2)
    for line in _homework_lines(plan) or ["（待补充）"]:
        _tpl_body(doc, line, style, indent=False)
    diff = plan.get("differentiation") or {}
    if _text(diff.get("support")):
        _tpl_body(doc, "需要支持的学生：" + _strip_mark(diff.get("support")), style, indent=False)
    if _text(diff.get("extension")):
        _tpl_body(doc, "学有余力的学生：" + _strip_mark(diff.get("extension")), style, indent=False)

    # 六、教学效果及反思
    _tpl_heading(doc, "六、教学效果及反思", style)
    _tpl_body(doc, "课后根据以下维度记录实际教学效果，并在右侧“实际记录”栏补充证据。", style)
    _tpl_caption(doc, f"表{table_no} 教学效果观察维度与课后反思", style)
    reflection_rows = [
        ["观察维度", "课后核对问题", "实际记录"],
        ["目标达成", "学生能否用本课概念解释案例，并完成对应任务？", ""],
        ["参与程度", "小组是否人人参与，是否出现“旁观者”？", ""],
        ["时间控制", "实践/讨论环节是否超时，哪个环节需要压缩？", ""],
        ["差异支持", "学困生是否用上支架，学优生是否有挑战任务？", ""],
    ]
    _tpl_table(doc, reflection_rows, [3.2, 8.4, 4.0], style)
    for line in _numbered(plan.get("reflection") or []) or ["（课后填写）"]:
        _tpl_body(doc, line, style, indent=False)

    citations = [c for c in (plan.get("citations") or []) if isinstance(c, dict) and _text(c.get("quote"))]
    if citations:
        _tpl_heading(doc, "附录：教材引用核对", style)
        for item in citations[:6]:
            lines = item.get("source_lines") or ["?", "?"]
            start = lines[0] if len(lines) > 0 else "?"
            end = lines[-1] if len(lines) > 1 else start
            _tpl_body(
                doc,
                f"教材第 {start}-{end} 行：“{_strip_mark(item.get('quote'))}”"
                + (f"（支撑：{_strip_mark(item.get('claim'))}）" if _text(item.get("claim")) else ""),
                style,
                indent=False,
            )

    appendix_figs = _figures_for(plan, "appendix")
    if appendix_figs:
        _tpl_heading(doc, "附录：教材配图", style)
        for fig in appendix_figs:
            _add_picture_block(doc, fig, base_dir, 10.5)

    _add_page_footer(section)
    doc.core_properties.title = f"{title} 教学设计"
    doc.core_properties.author = "教案工坊"
    doc.core_properties.subject = _text(meta.get("subject"))
    doc.core_properties.comments = f"版式复用自模板：{spec.path}"

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    return out


def _tpl_phase_table(
    doc: Document,
    lesson: dict[str, Any],
    plan: dict[str, Any],
    style: dict[str, Any],
    base_dir: str | Path | None,
) -> None:
    phases = [p for p in (lesson.get("phases") or []) if isinstance(p, dict)]
    table = doc.add_table(rows=1, cols=4)
    _set_table_borders(table, "BFBFBF", 4)
    widths = [2.7, 1.3, 5.8, 5.8]
    _set_col_widths(table, widths)
    headers = ["教学环节", "时间", "教师活动", "学生活动与评价"]
    for c, h in enumerate(headers):
        cell = table.cell(0, c)
        _shade_cell(cell, "F2F2F2")
        _set_cell_margins(cell, 55, 55, 70, 70)
        _cell_text(cell, h, 9.5, True, WD_ALIGN_PARAGRAPH.CENTER, font=style["body_font"])
    _set_repeat_header(table.rows[0])
    total = 0
    figures = _figures_by_id(plan)
    for phase in phases:
        row = table.add_row()
        _set_row_cant_split(row)
        minutes = int(phase.get("minutes") or 0)
        total += minutes
        teacher = _strip_mark(phase.get("teacher_activity"))
        intent = _strip_mark(phase.get("intent"))
        if intent:
            teacher = f"{teacher}\n设计意图：{intent}"
        student = _strip_mark(phase.get("student_activity"))
        assessment = _strip_mark(phase.get("assessment"))
        if assessment:
            student = f"{student}\n评价：{assessment}"
        values = [_text(phase.get("name")), str(minutes), teacher, student]
        for c, value in enumerate(values):
            cell = row.cells[c]
            _set_cell_margins(cell, 45, 45, 65, 65)
            _cell_text(cell, value, 9.0, False, WD_ALIGN_PARAGRAPH.CENTER if c == 1 else None, font=style["body_font"])
        for fid in phase.get("figure_ids") or []:
            fig = figures.get(str(fid))
            if fig:
                _append_picture_to_cell(row.cells[2], fig, base_dir, 4.0)
    _set_col_widths(table, widths)
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    p.paragraph_format.space_after = Pt(8)
    run = p.add_run(f"本课时合计：{total}分钟")
    _set_run_font(run, style["body_font"], 9.5)


def fill_template_docx(
    spec: TemplateSpec,
    plan: dict[str, Any],
    out_path: str | Path,
    offline: bool = False,
    base_dir: str | Path | None = None,
) -> Path:
    """在教师上传的 DOCX 模板上原位填充，保留模板版式。"""
    if spec.kind != "docx" or spec.doc is None:
        return render_canonical_docx(plan, out_path, offline=offline, base_dir=base_dir)

    doc = spec.doc
    if offline:
        _insert_note_top(doc, OFFLINE_NOTE)
    _replace_generic_title(doc, plan)

    para_groups: dict[Any, list[Any]] = {}
    cell_groups: dict[Any, list[Any]] = {}
    heading_slots: list[Any] = []

    for slot in spec.slots:
        if slot.kind == "heading" and slot.para is not None:
            heading_slots.append(slot)
        elif slot.kind == "placeholder" and slot.para is not None:
            para_groups.setdefault(slot.para._p, []).append(slot)
        elif slot.kind in {"table_cell", "meta_cell"} and slot.cell is not None:
            cell_groups.setdefault(slot.cell._tc, []).append(slot)

    # 1. 标题型字段：在标题后插入正文
    for slot in heading_slots:
        blocks = slot_blocks(plan, slot.key, slot.label)
        if slot.key == "process" and spec.phase_table is not None:
            blocks = _lesson_intro_blocks(plan)
        blocks = [b for b in blocks if str(b).strip()]
        if not blocks:
            spec.warnings.append(f"模板字段“{slot.label}”没有生成内容，已留空。")
            continue
        try:
            slot.para.paragraph_format.keep_with_next = True
        except Exception:
            pass
        _remove_placeholder_paragraph_after(slot.para)
        prev = slot.para
        for block in blocks:
            prev = _insert_paragraph_after(
                prev,
                block,
                spec,
                bold=_looks_like_subhead(block),
                tight=(slot.key == "board"),
            )
        if slot.key == "analysis":
            for fig in _figures_for(plan, "analysis"):
                prev = _insert_picture_after(prev, fig, base_dir, 9.5)
        if slot.key == "process" and spec.phase_table is None:
            for fig in _figures_for(plan, "phase"):
                prev = _insert_picture_after(prev, fig, base_dir, 8.0)

    # 2. 段落占位符
    for group in para_groups.values():
        _fill_para_group(group, plan, spec)

    # 3. 表格单元格
    for group in cell_groups.values():
        _fill_cell_group(group, plan, spec)

    # 4. 教学过程表
    if spec.phase_table is not None:
        _fill_phase_table(spec, plan, base_dir)

    appendix_figs = _figures_for(plan, "appendix")
    has_process_heading = any(s.key == "process" and s.kind == "heading" for s in spec.slots)
    if spec.phase_table is None and not has_process_heading:
        # 模板没有教学过程表，也没有教学过程标题：把环节配图统一放到文末
        appendix_figs += [f for f in _figures_for(plan, "phase") if f not in appendix_figs]
    if appendix_figs:
        p = doc.add_paragraph()
        run = p.add_run("教材配图（附录）")
        _set_run_font(run, "黑体", 12, True)
        for fig in appendix_figs:
            _add_picture_block(doc, fig, base_dir, 9.5)

    # 5. 清理未知占位符
    leftovers = _replace_leftover_placeholders(doc)
    if leftovers:
        spec.warnings.append("以下未知占位符已清空：" + "、".join(sorted(set(leftovers))))

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    return out


def _replace_generic_title(doc: Document, plan: dict[str, Any]) -> None:
    """模板封面常写“教学设计”，若没有 {{title}} 占位符，则换成章节标题。"""
    title = _text(_meta(plan).get("title"))
    if not title:
        return
    generic = {"教学设计", "教案", "教学方案", "教学设计模板", "教 学 设 计"}
    for p in doc.paragraphs[:8]:
        text = (p.text or "").strip()
        if text in generic:
            sample_run = p.runs[0] if p.runs else None
            _set_para_text(p, title, sample_run)
            return


def _lesson_intro_blocks(plan: dict[str, Any]) -> list[str]:
    out: list[str] = []
    lessons = _lessons(plan)
    if len(lessons) <= 1:
        for lesson in lessons:
            minutes = lesson.get("duration_minutes") or _meta(plan).get("duration_minutes") or 45
            title = _text(lesson.get("title")) or "本课时"
            out.append(f"第{lesson.get('period', 1)}课时：{title}（{fmt_minutes(int(minutes))}）")
        return out
    duration = _meta(plan).get("duration_minutes") or 45
    parts = [f"第{lesson.get('period', i + 1)}课时 {(_text(lesson.get('title')) or '本课时')}" for i, lesson in enumerate(lessons)]
    out.append("课时安排：" + "；".join(parts) + f"（每课时{fmt_minutes(int(duration))}）")
    return out


def _looks_like_subhead(text: str) -> bool:
    t = str(text).strip()
    if not t:
        return False
    if re.match(r"^第[一二三四五六七八九十\d]+课时", t):
        return True
    if t.startswith("【") and t.endswith("）") and "分钟" in t:
        return True
    return False


def _insert_note_top(doc: Document, text: str) -> None:
    if doc.paragraphs:
        p = doc.paragraphs[0].insert_paragraph_before()
    else:
        p = doc.add_paragraph()
        first_tbl = doc.tables[0]._tbl if doc.tables else None
        if first_tbl is not None:
            first_tbl.addprevious(p._p)
    p.paragraph_format.space_after = Pt(6)
    ppr = p._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), "FFF4E5")
    ppr.append(shd)
    run = p.add_run(text)
    _set_run_font(run, "宋体", 9)
    run.font.color.rgb = RGBColor(0x8A, 0x5A, 0x00)


def _insert_paragraph_after(
    anchor: Paragraph,
    text: str,
    spec: TemplateSpec,
    bold: bool = False,
    tight: bool = False,
) -> Paragraph:
    new_p = OxmlElement("w:p")
    anchor._p.addnext(new_p)
    p = Paragraph(new_p, anchor._parent)
    sample = spec.sample_body
    if sample is not None:
        try:
            p.style = sample.style
        except Exception:
            pass
        if sample._p.pPr is not None:
            p._p.insert(0, copy.deepcopy(sample._p.pPr))
            # 避免继承模板里的自动编号
            for numpr in p._p.pPr.findall(qn("w:numPr")):
                p._p.pPr.remove(numpr)
    if tight:
        p.paragraph_format.line_spacing = 1.15
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(2)
        p.paragraph_format.first_line_indent = Pt(0)
        p.paragraph_format.left_indent = Pt(0)
    run = p.add_run(text)
    sample_run = sample.runs[0] if sample is not None and sample.runs else None
    if sample_run is not None and sample_run._r.rPr is not None:
        run._r.insert(0, copy.deepcopy(sample_run._r.rPr))
    else:
        _set_run_font(run, "宋体", 12, False)
    if bold:
        run.bold = True
        run.font.name = "黑体"
        rpr = run._r.get_or_add_rPr()
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            rfonts = OxmlElement("w:rFonts")
            rpr.insert(0, rfonts)
        rfonts.set(qn("w:eastAsia"), "黑体")
    if re.fullmatch(r"【[^】]{1,24}】", text.strip()):
        p.paragraph_format.keep_with_next = True
    return p


def _remove_placeholder_paragraph_after(anchor: Paragraph) -> None:
    nxt = anchor._p.getnext()
    if nxt is None or nxt.tag != qn("w:p"):
        return
    try:
        p = Paragraph(nxt, anchor._parent)
    except Exception:
        return
    text = (p.text or "").strip()
    if not text:
        return
    if re.fullmatch(r"[（(]?(请填写|请补充|在此填写|填写内容|略|待补充)[）)]?[。.]?", text) or re.fullmatch(
        r"[_＿\-—–\s.．。]{3,}", text
    ):
        nxt.getparent().remove(nxt)


def _set_para_text(p: Paragraph, text: str, sample_run=None, bold: bool = False) -> None:
    for child in list(p._p):
        if child.tag != qn("w:pPr"):
            p._p.remove(child)
    run = p.add_run(text)
    if sample_run is not None and sample_run._r.rPr is not None:
        run._r.insert(0, copy.deepcopy(sample_run._r.rPr))
    else:
        _set_run_font(run, "宋体", 12, False)
    if bold:
        run.bold = True
        run.font.name = "黑体"
        rpr = run._r.get_or_add_rPr()
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            rfonts = OxmlElement("w:rFonts")
            rpr.insert(0, rfonts)
        rfonts.set(qn("w:eastAsia"), "黑体")


def _fill_para_group(group: list[Any], plan: dict[str, Any], spec: TemplateSpec) -> None:
    p = group[0].para
    if p is None:
        return
    sample_run = spec.sample_body.runs[0] if spec.sample_body is not None and spec.sample_body.runs else None

    if len(group) > 1 or any(s.inline for s in group):
        text = p.text or ""
        residual = re.sub(r"\{\{[^{}]*\}\}", "", text).strip()
        for slot in group:
            value = inline_value(plan, slot.key, slot.label)
            if not residual:
                blocks = slot_blocks(plan, slot.key, slot.label)
                value = "\n".join(str(b) for b in blocks) if blocks else ""
            if slot.raw:
                text = text.replace(slot.raw, value)
        _set_para_text(p, text.strip(), sample_run)
        return

    slot = group[0]
    blocks = slot_blocks(plan, slot.key, slot.label)
    if not blocks:
        _set_para_text(p, "", sample_run)
        spec.warnings.append(f"模板字段“{slot.label}”没有生成内容，已留空。")
        return
    _set_para_text(p, str(blocks[0]), sample_run, bold=_looks_like_subhead(blocks[0]))
    prev = p
    for block in blocks[1:]:
        prev = _insert_paragraph_after(prev, str(block), spec, bold=_looks_like_subhead(block))


def _clear_cell(cell: _Cell) -> Paragraph:
    # 保留第一个段落及其段落格式，删除其余段落
    paragraphs = cell.paragraphs
    for p in paragraphs[1:]:
        p._p.getparent().remove(p._p)
    p = cell.paragraphs[0]
    for child in list(p._p):
        if child.tag != qn("w:pPr"):
            p._p.remove(child)
    return p


def _fill_cell_group(group: list[Any], plan: dict[str, Any], spec: TemplateSpec) -> None:
    cell = group[0].cell
    if cell is None:
        return
    slot = group[0]
    if len(group) > 1 or slot.inline:
        text = cell.text or ""
        residual = re.sub(r"\{\{[^{}]*\}\}", "", text).strip()
        for s in group:
            value = inline_value(plan, s.key, s.label)
            if not residual:
                blocks = slot_blocks(plan, s.key, s.label)
                value = "\n".join(str(b) for b in blocks) if blocks else ""
            if s.raw:
                text = text.replace(s.raw, value)
        p = _clear_cell(cell)
        lines = [line for line in text.strip().splitlines() if line.strip()] or [""]
        for i, line in enumerate(lines):
            target = p if i == 0 else cell.add_paragraph()
            if i > 0:
                target.paragraph_format.space_before = Pt(0)
                target.paragraph_format.space_after = Pt(1)
                target.paragraph_format.line_spacing = 1.2
            run = target.add_run(line)
            _set_run_font(run, "宋体", 10, False)
        cell.vertical_alignment = WD_ALIGN_VERTICAL.TOP
        return

    blocks = slot_blocks(plan, slot.key, slot.label)
    if not blocks:
        blocks = ["（待补充）"]
        spec.warnings.append(f"模板字段“{slot.label}”没有生成内容，已写“（待补充）”。")
    p = _clear_cell(cell)
    cell.vertical_alignment = WD_ALIGN_VERTICAL.TOP
    first = True
    for block in blocks:
        lines = str(block).splitlines() or [""]
        for line in lines:
            if first:
                target = p
                first = False
            else:
                target = cell.add_paragraph()
                target.paragraph_format.space_before = Pt(0)
                target.paragraph_format.space_after = Pt(1)
                target.paragraph_format.line_spacing = 1.2
            run = target.add_run(line)
            _set_run_font(run, "宋体", 10, bool(_looks_like_subhead(line)))


def _fill_phase_table(spec: TemplateSpec, plan: dict[str, Any], base_dir: str | Path | None = None) -> None:
    pt = spec.phase_table
    assert pt is not None
    table = pt.table
    lessons = _lessons(plan)
    items: list[tuple[int, dict[str, Any]]] = []
    for lesson in lessons:
        for phase in lesson.get("phases") or []:
            if isinstance(phase, dict):
                items.append((int(lesson.get("period") or 1), phase))
    if not items:
        spec.warnings.append("教学过程中没有可用环节，教学过程表未填充。")
        return

    trs = table._tbl.tr_lst
    data_trs = list(trs[pt.header_row + 1 :])
    template_tr = None
    for tr in reversed(data_trs):
        row_text = "".join(node.text or "" for node in tr.iter(qn("w:t")))
        if "合计" in row_text or "备注" in row_text:
            continue
        template_tr = copy.deepcopy(tr)
        break
    for tr in data_trs:
        tr.getparent().remove(tr)
    if pt.header_row < len(table.rows):
        _set_repeat_header(table.rows[pt.header_row])

    for period, phase in items:
        if template_tr is not None:
            new_tr = copy.deepcopy(template_tr)
            table._tbl.append(new_tr)
        else:
            table.add_row()
        row = table.rows[-1]
        _set_row_cant_split(row)
        seen: set[Any] = set()
        values: dict[str, str] = {
            "phase": (_text(phase.get("name")) if len(lessons) <= 1 else f"第{period}课时 {_text(phase.get('name'))}"),
            "minutes": str(phase.get("minutes", "")),
            "teacher": _text(phase.get("teacher_activity")),
            "student": _text(phase.get("student_activity")),
            "intent": _text(phase.get("intent")),
            "assessment": _text(phase.get("assessment")) or "—",
            "objective": "、".join(str(x) for x in (phase.get("objective_ids") or []) if x),
            "resources": _text(phase.get("resources") or []),
        }
        if "minutes" not in pt.columns and values["minutes"]:
            values["phase"] = f"{values['phase']}（{values['minutes']}分钟）"
        if "resources" not in pt.columns and values["resources"]:
            values["teacher"] = (values["teacher"] + "\n资源：" + values["resources"]).strip()
        if "objective" not in pt.columns and values["objective"]:
            values["intent"] = (values["intent"] + f"（目标：{values['objective']}）").strip()

        for col, ci in pt.columns.items():
            if ci >= len(row.cells):
                continue
            cell = row.cells[ci]
            if cell._tc in seen:
                continue
            seen.add(cell._tc)
            _cell_text(cell, values.get(col, ""), 9.0)

        teacher_ci = pt.columns.get("teacher")
        if teacher_ci is not None and teacher_ci < len(row.cells):
            figures = _figures_by_id(plan)
            for fid in phase.get("figure_ids") or []:
                fig = figures.get(str(fid))
                if fig:
                    _append_picture_to_cell(row.cells[teacher_ci], fig, base_dir, 4.0)

    total = sum(int(ph.get("minutes") or 0) for _, ph in items)
    _set_col_widths(table, [2.4, 1.2, 4.6, 4.1, 2.8, 1.9])
    _append_total_after_table(table, f"合计：{total}分钟（{len(lessons)}课时）")


def _append_total_after_table(table: Table, text: str) -> None:
    nxt = table._tbl.getnext()
    if nxt is not None and nxt.tag == qn("w:p"):
        try:
            if Paragraph(nxt, table._parent).text.strip().startswith("合计"):
                return
        except Exception:
            pass
    p_el = OxmlElement("w:p")
    table._tbl.addnext(p_el)
    p = Paragraph(p_el, table._parent)
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = p.add_run(text)
    _set_run_font(run, "宋体", 9)
    run.font.color.rgb = RGBColor(0x59, 0x59, 0x59)


def _replace_leftover_placeholders(doc: Document) -> list[str]:
    leftovers: list[str] = []
    paragraphs: list[Paragraph] = list(doc.paragraphs)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                paragraphs.extend(cell.paragraphs)
    seen: set[Any] = set()
    for p in paragraphs:
        if p._p in seen:
            continue
        seen.add(p._p)
        text = p.text or ""
        matches = list(re.finditer(r"\{\{\s*([^{}]+?)\s*\}\}", text))
        if not matches:
            continue
        for m in matches:
            leftovers.append(m.group(1).strip())
        new_text = re.sub(r"\{\{\s*[^{}]+?\s*\}\}", "", text).strip()
        for child in list(p._p):
            if child.tag != qn("w:pPr"):
                p._p.remove(child)
        if new_text:
            run = p.add_run(new_text)
            _set_run_font(run, "宋体", 10)
    return leftovers

