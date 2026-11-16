import re
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph

HEADING_STYLE_RE = re.compile(r"^(Heading|标题)", re.IGNORECASE)
DEFAULT_FILL_MARKERS = ["____"]


@dataclass
class Block:
    index: int
    type: str  # heading / paragraph / table / blank
    text: str
    style_name: str = ""
    rows: list[list[str]] | None = None
    placeholders: list[str] = field(default_factory=list)
    context_heading: str = ""


def iter_block_items(document):
    """按文档实际顺序遍历 body 中的段落与表格（p 与 tbl 交错）。

    不能只遍历 document.paragraphs——那样表格会整体丢失且顺序错乱。
    """
    body = document.element.body
    for child in body.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, document)
        elif isinstance(child, CT_Tbl):
            yield Table(child, document)


def table_to_rows(table: Table) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in table.rows:
        rows.append([cell.text.strip() for cell in row.cells])
    return rows


def markdown_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""

    def escape(cell: str) -> str:
        return cell.replace("|", "\\|").replace("\n", "<br>")

    lines = ["| " + " | ".join(escape(c) for c in rows[0]) + " |"]
    lines.append("| " + " | ".join("---" for _ in rows[0]) + " |")
    for row in rows[1:]:
        lines.append("| " + " | ".join(escape(c) for c in row) + " |")
    return "\n".join(lines)


def detect_placeholders(text: str, markers: list[str] | None = None) -> list[str]:
    markers = markers or DEFAULT_FILL_MARKERS
    hits = {marker for marker in markers if marker and marker in text}
    return sorted(hits)


def parse_template(
    docx_path: str | Path, fill_markers: list[str] | None = None
) -> list[Block]:
    """解析教学设计模板 docx，输出按文档实际顺序排列的块列表。"""
    markers = fill_markers or DEFAULT_FILL_MARKERS
    document = Document(str(docx_path))
    blocks: list[Block] = []
    for item in iter_block_items(document):
        if isinstance(item, Paragraph):
            text = item.text.strip()
            style_name = item.style.name if item.style is not None else ""
            if not text:
                block_type = "blank"
            elif HEADING_STYLE_RE.match(style_name or ""):
                block_type = "heading"
            else:
                block_type = "paragraph"
            blocks.append(
                Block(
                    index=len(blocks),
                    type=block_type,
                    text=text,
                    style_name=style_name,
                    placeholders=detect_placeholders(text, markers),
                )
            )
        elif isinstance(item, Table):
            rows = table_to_rows(item)
            placeholders: set[str] = set()
            for row in rows:
                for cell in row:
                    placeholders.update(detect_placeholders(cell, markers))
            blocks.append(
                Block(
                    index=len(blocks),
                    type="table",
                    text=markdown_table(rows),
                    style_name="table",
                    rows=rows,
                    placeholders=sorted(placeholders),
                )
            )
    return blocks
