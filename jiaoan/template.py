from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from docx import Document
from docx.document import Document as _Document
from docx.oxml.ns import qn
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph

from .utils import normalize_label, read_text

PLACEHOLDER_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")

# 规范字段 -> 模板中可能出现的叫法
SLOT_ALIASES: dict[str, list[str]] = {
    "title": ["课题", "课程名称", "课名", "教学主题", "课题名称"],
    "meta": ["基本信息", "授课信息", "课程信息", "教学基本信息", "课程基本信息"],
    "subject": ["学科", "科目"],
    "grade": ["年级", "适用年级", "学段", "授课年级"],
    "duration": ["课时", "授课时长", "教学时长", "课时安排", "课时数"],
    "lesson_type": ["课型", "课程类型", "授课类型"],
    "textbook": ["教材", "教材名称", "教材版本"],
    "analysis": ["教材分析", "内容分析", "教学内容分析", "教材与内容分析", "教学内容", "课程简介", "课程定位", "授课内容定位"],
    "student": ["学情分析", "学习者分析", "学生分析", "学情"],
    "objectives": ["教学目标", "学习目标", "教学目标与要求", "核心素养目标", "素养目标", "课程目标", "本次教学目标"],
    "key_points": ["教学重点", "重点", "教学重点与难点", "重点难点", "教学重点难点", "重难点"],
    "difficulties": ["教学难点", "难点"],
    "key_difficulties": ["教学重点难点", "重点难点", "重难点", "教学重点与难点", "重点与难点"],
    "methods": ["教学方法", "教法学法", "教法与学法", "教学策略", "教法"],
    "materials": ["教学准备", "教学资源", "教具准备", "课前准备", "教学用具", "环境与课前准备"],
    "process": ["教学过程", "教学活动", "教学环节", "课堂教学过程", "学习活动", "教学流程", "案例教学实施过程"],
    "board": ["板书设计", "板书", "板书与关键结论"],
    "homework": ["作业设计", "作业布置", "课后作业", "作业", "分层作业与反馈"],
    "reflection": ["教学反思", "课后反思", "教学反思与改进", "反思", "教学效果及反思"],
    "assessment": ["教学评价", "评价设计", "学习评价", "目标达成", "评价方案", "课堂小结与学习评价", "课堂评价"],
    "design": ["设计思路", "教学设计思路", "设计说明", "设计理念", "教学思路"],
    "coverage": ["教材内容覆盖", "内容覆盖", "教材覆盖"],
}

ALIAS_TO_KEY: dict[str, str] = {}
_ALIAS_PAIRS: list[tuple[str, str]] = []
for _key, _aliases in SLOT_ALIASES.items():
    for _alias in _aliases:
        _norm = normalize_label(_alias)
        if _norm:
            ALIAS_TO_KEY[_norm] = _key
            _ALIAS_PAIRS.append((_norm, _key))
_ALIAS_PAIRS.sort(key=lambda x: len(x[0]), reverse=True)

META_KEYS = {"title", "subject", "grade", "duration", "lesson_type", "textbook"}

PHASE_COLUMN_ALIASES: dict[str, list[str]] = {
    "phase": ["教学环节", "环节", "教学步骤", "活动环节", "教学活动", "教学内容", "学习任务", "步骤"],
    "minutes": ["时间", "时长", "分钟", "时间分配", "用时", "时间（分钟）"],
    "teacher": ["教师活动", "教师行为", "教师指导", "教师操作", "教师", "教师活动与内容"],
    "student": ["学生活动", "学生行为", "学生学习", "学生操作", "学生", "学生活动与内容"],
    "intent": ["设计意图", "设计说明", "教学意图", "意图", "设计理念"],
    "assessment": ["评价方式", "教学评价", "学习评价", "评价", "目标检测", "检测方式"],
    "objective": ["对应目标", "教学目标", "目标", "素养目标", "目标编号"],
    "resources": ["教学资源", "资源", "媒体", "教学媒体", "支撑资源"],
}
_PHASE_ALIAS_PAIRS: list[tuple[str, str]] = []
for _col, _aliases in PHASE_COLUMN_ALIASES.items():
    for _alias in _aliases:
        _PHASE_ALIAS_PAIRS.append((normalize_label(_alias), _col))
_PHASE_ALIAS_PAIRS.sort(key=lambda x: len(x[0]), reverse=True)


@dataclass
class Slot:
    key: str
    label: str
    kind: str  # placeholder | heading | table_cell | meta_cell | md_placeholder | md_heading
    para: Paragraph | None = None
    cell: _Cell | None = None
    table: Table | None = None
    para_index: int | None = None
    table_index: int | None = None
    row: int | None = None
    col: int | None = None
    line: int | None = None
    inline: bool = False
    raw: str = ""
    custom: bool = False

    def location(self) -> str:
        if self.kind in {"placeholder", "heading"}:
            return f"段落 {self.para_index}"
        if self.kind in {"table_cell", "meta_cell"}:
            return f"表格 {self.table_index} 第 {self.row} 行第 {self.col} 列"
        if self.kind in {"md_placeholder", "md_heading"}:
            return f"第 {self.line} 行"
        return "未知"

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "kind": self.kind,
            "location": self.location(),
            "custom": self.custom,
        }


@dataclass
class PhaseTable:
    table: Table
    table_index: int
    header_row: int
    columns: dict[str, int]
    data_rows: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "table_index": self.table_index,
            "header_row": self.header_row,
            "columns": self.columns,
            "data_rows": self.data_rows,
        }


@dataclass
class TemplateSpec:
    path: str
    kind: str  # docx | md | canonical
    title: str
    slots: list[Slot] = field(default_factory=list)
    phase_table: PhaseTable | None = None
    doc: Any = None
    md_text: str | None = None
    md_sections: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sample_body: Paragraph | None = None
    body_style_name: str = "Normal"
    exemplar: bool = False

    @property
    def custom_labels(self) -> list[str]:
        return [s.label for s in self.slots if s.custom and s.key.startswith("custom:")]

    def slots_by_key(self) -> dict[str, list[Slot]]:
        out: dict[str, list[Slot]] = {}
        for s in self.slots:
            out.setdefault(s.key, []).append(s)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "title": self.title,
            "slots": [s.to_dict() for s in self.slots],
            "custom_labels": self.custom_labels,
            "phase_table": self.phase_table.to_dict() if self.phase_table else None,
            "exemplar": self.exemplar,
            "warnings": self.warnings,
        }


def _iter_blocks(doc: _Document) -> Iterator[tuple[str, Any, int]]:
    """按文档顺序遍历段落与表格，分别维护段落/表格序号。"""
    p_idx = 0
    t_idx = 0
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield "p", Paragraph(child, doc), p_idx
            p_idx += 1
        elif child.tag == qn("w:tbl"):
            yield "t", Table(child, doc), t_idx
            t_idx += 1


def _is_heading_para(p: Paragraph) -> bool:
    try:
        name = (p.style.name or "").lower()
    except Exception:
        name = ""
    if name.startswith("heading") or name.startswith("标题"):
        return True
    ppr = p._p.pPr
    if ppr is not None and ppr.find(qn("w:outlineLvl")) is not None:
        return True
    text = p.text.strip()
    if not text or len(text) > 40:
        return False
    if re.match(r"^(第\s*[一二三四五六七八九十百零〇\d]+\s*[章节]|[一二三四五六七八九十]+\s*[、.．]|\d+(?:\.\d+)+\s+)", text):
        return True
    return False


def _label_only(text: str) -> bool:
    """判断段落是否只是字段名（而不是“教学目标：能……”这种正文）。"""
    t = re.sub(r"^#{1,6}\s*", "", text.strip())
    if not t:
        return False
    if t.endswith(("。", "！", "？", "；", "，", ".", "!", "?", ";")):
        return False
    if "：" in t or ":" in t:
        left, right = re.split(r"[：:]", t, maxsplit=1)
        if right.strip():
            return False
        t = left.strip()
    t = re.sub(r"^[（(]?[一二三四五六七八九十百零〇]+[）)]?\s*[、.．:：]\s*", "", t)
    t = re.sub(r"^第\s*[一二三四五六七八九十百零〇\d]+\s*[章节部分]\s*", "", t)
    t = re.sub(r"^\d+(?:\.\d+)*\s*[、.．:：]?\s*", "", t)
    t = t.strip(" 　:：")
    return 2 <= len(t) <= 24


def _match_key(text: str, allow_custom: bool = False) -> tuple[str | None, bool]:
    """返回 (规范字段名, 是否自定义)。"""
    raw = str(text or "").strip()
    if not raw:
        return None, False
    if raw in SLOT_ALIASES:
        return raw, False
    norm = normalize_label(raw)
    if not norm:
        return None, False
    if norm in ALIAS_TO_KEY:
        return ALIAS_TO_KEY[norm], False
    for alias, key in _ALIAS_PAIRS:
        if norm.startswith(alias) or alias in norm:
            if len(norm) <= len(alias) + 12:
                return key, False
    if allow_custom:
        return f"custom:{raw}", True
    return None, False


def _match_phase_column(text: str) -> str | None:
    norm = normalize_label(text)
    if not norm:
        return None
    if norm in {a for a, _ in _PHASE_ALIAS_PAIRS}:
        return next(col for a, col in _PHASE_ALIAS_PAIRS if a == norm)
    for alias, col in _PHASE_ALIAS_PAIRS:
        if alias in norm and len(norm) <= len(alias) + 10:
            return col
    return None


def _cell_has_placeholder(cell: _Cell) -> bool:
    return bool(PLACEHOLDER_RE.search(cell.text or ""))


def _blank_cell(cell: _Cell) -> bool:
    text = (cell.text or "").strip()
    if not text:
        return True
    if _cell_has_placeholder(cell):
        return False
    if re.fullmatch(r"[_＿\-—–\s.．。]*", text):
        return True
    return bool(re.search(r"(请填写|请补充|在此填写|点击输入|填写内容)", text))


def _dedupe_slots(slots: list[Slot], warnings: list[str]) -> list[Slot]:
    """同一字段只保留一个填充位置；meta 字段允许多处（如页眉、信息表）。"""
    priority = {"placeholder": 3, "table_cell": 3, "meta_cell": 3, "heading": 1}
    chosen: dict[str, Slot] = {}
    ordered: list[Slot] = []
    duplicates: list[str] = []

    for slot in slots:
        if slot.key.startswith("custom:"):
            ordered.append(slot)
            continue
        existing = chosen.get(slot.key)
        if existing is None:
            chosen[slot.key] = slot
            ordered.append(slot)
            continue
        if slot.key in META_KEYS and slot.kind == "meta_cell" and existing.kind == "meta_cell":
            ordered.append(slot)
            continue
        if priority.get(slot.kind, 0) > priority.get(existing.kind, 0):
            if existing in ordered:
                ordered.remove(existing)
            chosen[slot.key] = slot
            ordered.append(slot)
            # 标题 + 占位符是常见模板写法，不算重复；只提示同类型真重复
            if existing.kind != "heading" and slot.kind != "heading":
                duplicates.append(slot.key)
        else:
            if existing.kind != "heading" and slot.kind != "heading":
                duplicates.append(slot.key)

    if duplicates:
        warnings.append("以下字段在模板中出现多次，已保留最适合填充的一处：" + "、".join(sorted(set(duplicates))))
    return ordered


def parse_template(path: str | Path) -> TemplateSpec:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"模板不存在：{p}")
    suffix = p.suffix.lower()
    if suffix == ".docx":
        return _parse_docx_template(p)
    if suffix in {".md", ".markdown", ".txt"}:
        return _parse_md_template(p)
    raise ValueError(f"不支持的模板格式：{suffix}（请使用 .docx 或 .md）")


def _parse_docx_template(path: Path) -> TemplateSpec:
    doc = Document(str(path))
    warnings: list[str] = []
    slots: list[Slot] = []
    phase_tables: list[PhaseTable] = []

    for block_type, obj, idx in _iter_blocks(doc):
        if block_type == "p":
            para: Paragraph = obj
            text = para.text or ""
            placeholders = list(PLACEHOLDER_RE.finditer(text))
            for m in placeholders:
                key, custom = _match_key(m.group(1), allow_custom=True)
                if not key:
                    continue
                slots.append(
                    Slot(
                        key=key,
                        label=m.group(1).strip(),
                        kind="placeholder",
                        para=para,
                        para_index=idx,
                        inline=bool(re.sub(r"\{\{[^{}]*\}\}", "", text).strip()),
                        raw=m.group(0),
                        custom=custom,
                    )
                )
            if placeholders:
                continue

            if not _label_only(text):
                continue
            key, _ = _match_key(text, allow_custom=False)
            if not key:
                if _is_heading_para(para) and 2 <= len(text.strip()) <= 20:
                    if re.match(r"^第\s*[一二三四五六七八九十百零〇\d]+\s*[章篇部]", text.strip()):
                        continue
                    if text.strip() in {"1. 知识与理解", "2. 方法与能力", "3. 安全判断意识", "重点", "难点与突破策略"}:
                        continue
                    warnings.append(f"未识别的字段标题（不会自动填充）：{text.strip()}")
                continue
            slots.append(
                Slot(
                    key=key,
                    label=text.strip(),
                    kind="heading",
                    para=para,
                    para_index=idx,
                    raw=text,
                )
            )
        else:
            table: Table = obj
            if _table_has_placeholder(table):
                # 有占位符的表格按普通字段处理，不做“教学过程表”识别
                slots.extend(_table_placeholder_slots(table, idx))
                continue
            detected = _detect_phase_table(table, idx)
            if detected:
                phase_tables.append(detected)
                continue
            slots.extend(_table_label_slots(table, idx))

    selected = _dedupe_slots(slots, warnings)
    if len(phase_tables) > 1:
        warnings.append(f"检测到 {len(phase_tables)} 张“教学过程”表，仅使用第 1 张。")
    phase_table = phase_tables[0] if phase_tables else None

    # 如果教学过程表存在，而模板里又有一个 process 的普通占位符，提示可能重复
    if phase_table and any(s.key == "process" and s.kind in {"placeholder", "table_cell"} for s in selected):
        warnings.append("模板同时存在“教学过程”占位符和教学过程表，内容可能重复；建议保留其中一种。")

    sample_body = _find_sample_body(doc, selected)
    title = _find_template_title(doc, selected)
    if not selected and not phase_table:
        warnings.append("没有识别到任何字段。请使用 {{字段名}} 占位符，或使用“教学目标/教学过程”等标准字段标题。")

    exemplar = _looks_like_filled_exemplar(doc, selected, phase_table)

    return TemplateSpec(
        path=str(path),
        kind="docx",
        title=title,
        slots=selected,
        phase_table=phase_table,
        doc=doc,
        warnings=warnings,
        sample_body=sample_body,
        body_style_name=sample_body.style.name if sample_body else "Normal",
        exemplar=exemplar,
    )


def _looks_like_filled_exemplar(doc: _Document, slots: list[Slot], phase_table: PhaseTable | None) -> bool:
    """判断模板是不是“已经写满某一课时的样例”，而不是空白模板。

    样例模板不能只在标题后插入新内容，否则旧课时的 GAN/案例文字会全部留在
    Word 里。识别出来后改用“复用版式、重建内容”的渲染方式。
    """
    if any(s.raw.startswith("{{") for s in slots if s.kind in {"placeholder", "table_cell"}):
        return False
    heading_count = sum(1 for s in slots if s.kind == "heading")
    if heading_count < 4:
        return False
    slot_paras = {s.para._p for s in slots if s.para is not None}
    body_chars = 0
    for p in doc.paragraphs:
        if p._p in slot_paras:
            continue
        text = (p.text or "").strip()
        if len(text) >= 25 and not _is_heading_para(p):
            body_chars += len(text)
    if body_chars < 600:
        return False
    if phase_table is None:
        return True
    # 教学过程表里已有 2 行以上真实内容，说明这是填过的样例。
    pt = phase_table
    filled_rows = 0
    for r in range(pt.header_row + 1, len(pt.table.rows)):
        text = "".join(c.text for c in pt.table.rows[r].cells).strip()
        if len(text) >= 20:
            filled_rows += 1
    return filled_rows >= 2


def _table_has_placeholder(table: Table) -> bool:
    for row in table.rows:
        for cell in row.cells:
            if _cell_has_placeholder(cell):
                return True
    return False


def _table_placeholder_slots(table: Table, table_index: int) -> list[Slot]:
    slots: list[Slot] = []
    seen_tc: set[Any] = set()
    for r, row in enumerate(table.rows):
        for c, cell in enumerate(row.cells):
            tc = cell._tc
            if tc in seen_tc:
                continue
            seen_tc.add(tc)
            text = cell.text or ""
            matches = list(PLACEHOLDER_RE.finditer(text))
            if not matches:
                continue
            # 一个单元格有多个占位符时，退化为自定义合并字段，避免相互覆盖
            if len({m.group(1).strip() for m in matches}) > 1:
                key = f"custom:{text.strip()}"
                slots.append(
                    Slot(
                        key=key,
                        label=text.strip(),
                        kind="table_cell",
                        cell=cell,
                        table=table,
                        table_index=table_index,
                        row=r,
                        col=c,
                        raw=text,
                        custom=True,
                    )
                )
                continue
            m = matches[0]
            key, custom = _match_key(m.group(1), allow_custom=True)
            if not key:
                continue
            slots.append(
                Slot(
                    key=key,
                    label=m.group(1).strip(),
                    kind="table_cell",
                    cell=cell,
                    table=table,
                    table_index=table_index,
                    row=r,
                    col=c,
                    raw=m.group(0),
                    custom=custom,
                )
            )
    return slots


def _table_label_slots(table: Table, table_index: int) -> list[Slot]:
    slots: list[Slot] = []
    seen_tc: set[Any] = set()
    for r, row in enumerate(table.rows):
        cells = row.cells
        for c, cell in enumerate(cells):
            tc = cell._tc
            if tc in seen_tc:
                continue
            seen_tc.add(tc)
            text = (cell.text or "").strip()
            if not text or len(text) > 24:
                continue
            key, _ = _match_key(text, allow_custom=False)
            if not key:
                continue
            target: _Cell | None = None
            target_coord: tuple[int, int] | None = None
            if c + 1 < len(cells) and _blank_cell(cells[c + 1]):
                target = cells[c + 1]
                target_coord = (r, c + 1)
            elif r + 1 < len(table.rows) and c < len(table.rows[r + 1].cells):
                below = table.rows[r + 1].cells[c]
                if _blank_cell(below):
                    target = below
                    target_coord = (r + 1, c)
            if target is None or target_coord is None:
                continue
            slots.append(
                Slot(
                    key=key,
                    label=text,
                    kind="meta_cell" if key in META_KEYS else "table_cell",
                    cell=target,
                    table=table,
                    table_index=table_index,
                    row=target_coord[0],
                    col=target_coord[1],
                    raw=text,
                )
            )
    return slots


def _detect_phase_table(table: Table, table_index: int) -> PhaseTable | None:
    rows = table.rows
    if len(rows) < 2:
        return None
    for r in range(min(3, len(rows))):
        columns: dict[str, int] = {}
        for c, cell in enumerate(rows[r].cells):
            col = _match_phase_column(cell.text or "")
            if col and col not in columns:
                columns[col] = c
        if "teacher" in columns and "student" in columns and len(columns) >= 2:
            data_start = r + 1
            return PhaseTable(
                table=table,
                table_index=table_index,
                header_row=r,
                columns=columns,
                data_rows=max(0, len(rows) - data_start),
            )
    return None


def _find_sample_body(doc: _Document, slots: list[Slot]) -> Paragraph | None:
    slot_paras = {s.para._p for s in slots if s.para is not None}
    for idx, p in enumerate(doc.paragraphs):
        if p._p in slot_paras:
            continue
        text = (p.text or "").strip()
        if len(text) < 15 or _is_heading_para(p):
            continue
        # 跳过封面标题：居中、大字号或前 3 段
        if idx < 3:
            continue
        try:
            size = p.runs[0].font.size.pt if p.runs and p.runs[0].font.size else None
        except Exception:
            size = None
        if p.alignment == 1 or (size and size >= 16):
            continue
        return p
    return None


def _find_template_title(doc: _Document, slots: list[Slot]) -> str:
    # 样例教案封面通常前两段就是“第X章 …”“第X学时 …”，优先识别，避免把“三、教学内容”当标题。
    for p in doc.paragraphs[:5]:
        text = (p.text or "").strip()
        if 4 <= len(text) <= 60 and re.match(r"^第\s*[一二三四五六七八九十百零〇\d]+\s*[章篇部]", text):
            return text
    for s in slots:
        if s.key == "title" and s.para is not None:
            return (s.para.text or "").strip()
    for p in doc.paragraphs[:10]:
        text = (p.text or "").strip()
        if text and len(text) <= 40 and ("设计" in text or "教案" in text):
            return text
    try:
        if doc.core_properties.title:
            return str(doc.core_properties.title)
    except Exception:
        pass
    return "教学设计"


def _parse_md_template(path: Path) -> TemplateSpec:
    text = read_text(path)
    lines = text.splitlines()
    slots: list[Slot] = []
    sections: list[dict[str, Any]] = []
    headings: list[tuple[int, int, str]] = []

    for i, line in enumerate(lines, start=1):
        m = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if m:
            headings.append((i, len(m.group(1)), m.group(2).strip()))

    for i, line in enumerate(lines, start=1):
        for m in PLACEHOLDER_RE.finditer(line):
            key, custom = _match_key(m.group(1), allow_custom=True)
            if key:
                slots.append(
                    Slot(
                        key=key,
                        label=m.group(1).strip(),
                        kind="md_placeholder",
                        line=i,
                        raw=m.group(0),
                        inline=bool(re.sub(r"\{\{[^{}]*\}\}", "", line).strip()),
                        custom=custom,
                    )
                )

    for idx, (line_no, level, title) in enumerate(headings):
        key, _ = _match_key(title, allow_custom=False)
        if not key:
            continue
        end = len(lines)
        for nxt_line, nxt_level, _ in headings[idx + 1 :]:
            if nxt_level <= level:
                end = nxt_line - 1
                break
        sections.append({"key": key, "label": title, "level": level, "start": line_no, "end": end})
        slots.append(Slot(key=key, label=title, kind="md_heading", line=line_no, raw=title))

    warnings: list[str] = []
    # 标题段落已经覆盖的范围内，不再重复保留 {{字段}} 占位符
    covered_ranges = [(int(s["start"]), int(s["end"])) for s in sections]
    slots = [
        s
        for s in slots
        if not (s.kind == "md_placeholder" and s.line is not None and any(a <= s.line <= b for a, b in covered_ranges))
    ]
    if not slots and not sections:
        warnings.append("Markdown 模板中没有识别到 {{字段}} 或标准字段标题。")
    title = next((h[2] for h in headings if h[2] and ("设计" in h[2] or "教案" in h[2])), path.stem)
    return TemplateSpec(
        path=str(path),
        kind="md",
        title=title,
        slots=slots,
        md_text=text,
        md_sections=sections,
        warnings=warnings,
    )


def load_builtin_canonical() -> TemplateSpec:
    return TemplateSpec(
        path="<builtin:canonical>",
        kind="canonical",
        title="教学设计",
        slots=[],
        warnings=[],
    )


def extract_template_info(spec: TemplateSpec) -> dict[str, Any]:
    """从模板自身读取学科/课程、年级/对象、课时时长、章内课时数。

    优先级：表格字段 > 正文关键词 > 占位符默认值。读不到的字段明确返回 None，
    由前端/管线回退到教材篇幅估计，不假装是模板里写的。
    """
    table_rows: list[tuple[str, str]] = []
    for table in (spec.doc.tables if spec.doc is not None else []):
        for row in table.rows:
            cells = [re.sub(r"\s+", " ", c.text or "").strip() for c in row.cells]
            if len(cells) >= 2:
                table_rows.append((cells[0], cells[1]))
    paragraphs = [p.text or "" for p in (spec.doc.paragraphs if spec.doc is not None else [])]
    flat = "\n".join(paragraphs + ["\n".join(x) for x in table_rows])

    def table_value(label: str) -> str:
        for key, value in table_rows:
            if label in key:
                return value
        return ""

    course = table_value("课程名称") or table_value("课程") or ""
    subject = table_value("学科") or course
    grade = table_value("授课对象") or table_value("教学对象") or table_value("年级") or ""
    grade = re.sub(r"^具备.*?的", "", grade).strip() or grade
    if not grade:
        m = re.search(r"(大学[一二三四]年级|高[一二三]|初[一二三]|七|八|九年级|本科生|研究生)", flat)
        grade = m.group(1) if m else ""
    duration = None
    for pattern in (
        r"本次\s*\d+\s*学时[，,、\s]*(\d+)\s*分钟",
        r"课时(?:时长)?[：:\s]*(\d+)\s*分钟",
        r"每课时\s*(\d+)\s*分钟",
        r"(\d+)\s*分钟",
    ):
        m = re.search(pattern, flat)
        if m:
            duration = int(m.group(1))
            break
    periods = None
    for pattern in (
        r"章内课时数[^\d]{0,8}(\d+)",
        r"本章共\s*(\d+)\s*学时",
        r"本章\s*(\d+)\s*课时",
    ):
        m = re.search(pattern, flat)
        if m:
            periods = int(m.group(1))
            break
    total_hours = None
    m = re.search(r"总计\s*(\d+)\s*学时", flat)
    if m:
        total_hours = int(m.group(1))
    lesson_no = None
    m = re.search(r"第\s*(\d+)\s*学时", flat)
    if m:
        lesson_no = int(m.group(1))
    notes = []
    if periods is None:
        notes.append("模板未写“章内课时数/本章共X学时”，将按教材篇幅和单课时时长自动拆分。")
    return {
        "subject": subject,
        "grade": grade,
        "course_name": course,
        "duration_minutes": duration,
        "periods": periods,
        "lesson_no": lesson_no,
        "total_hours": total_hours,
        "notes": notes,
    }


def describe_template(spec: TemplateSpec) -> str:
    lines = [
        f"模板：{spec.path}",
        f"类型：{spec.kind}",
        f"标题：{spec.title}",
    ]
    if spec.phase_table:
        cols = "、".join(f"{k}->第{v + 1}列" for k, v in spec.phase_table.columns.items())
        lines.append(f"教学过程表：表格 {spec.phase_table.table_index}，表头第 {spec.phase_table.header_row + 1} 行，{cols}")
    lines.append(f"识别字段：{len(spec.slots)} 个")
    for s in spec.slots:
        custom = "（自定义）" if s.custom else ""
        lines.append(f"  - {s.key:<16} {s.location():<22} {s.label}{custom}")
    if spec.warnings:
        lines.append("警告：")
        for w in spec.warnings:
            lines.append(f"  - {w}")
    return "\n".join(lines)
