from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from src.intermediate import parse_intermediate, parse_markdown_table
from src.template_parser import iter_block_items
from src.utils import ensure_dir, read_text, setup_logging

logger = setup_logging()


def set_east_asia(run, font_name: str) -> None:
    """显式设置东亚字体（w:eastAsia），否则中文显示可能退回默认字体。"""
    if not font_name:
        return
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.insert(0, rFonts)
    rFonts.set(qn("w:eastAsia"), font_name)


def replace_paragraph_text(paragraph, text: str, east_asia_font: str = "") -> None:
    """替换段落文字但保留段落/首个 run 的样式（直接给所有 run 赋值会丢样式）。"""
    text = text or ""
    runs = paragraph.runs
    if not runs:
        run = paragraph.add_run(text)
        set_east_asia(run, east_asia_font)
        return
    runs[0].text = text
    set_east_asia(runs[0], east_asia_font)
    for run in runs[1:]:
        run.text = ""


def set_cell_text(cell, text: str, east_asia_font: str = "") -> None:
    lines = (text or "").split("\n")
    paragraphs = cell.paragraphs
    replace_paragraph_text(paragraphs[0], lines[0], east_asia_font)
    for paragraph in paragraphs[1:]:
        replace_paragraph_text(paragraph, "", east_asia_font)
    for extra_line in lines[1:]:
        paragraph = cell.add_paragraph(extra_line)
        if paragraph.runs:
            set_east_asia(paragraph.runs[0], east_asia_font)


def fill_table(table, rows: list[list[str]], east_asia_font: str = "") -> None:
    if not rows:
        return
    for r_idx, docx_row in enumerate(table.rows):
        if r_idx >= len(rows):
            break
        for c_idx, cell in enumerate(docx_row.cells):
            if c_idx >= len(rows[r_idx]):
                break
            set_cell_text(cell, rows[r_idx][c_idx], east_asia_font)


def export(
    md_path: str | Path,
    template_docx_path: str | Path,
    out_docx_path: str | Path,
    config: dict,
) -> Path:
    """以模板 docx 为底稿，把中间稿逐块回填（"模板回填"而非"从零生成"），
    从而保留模板原有字体（仿宋等）、表格边框与页面设置。"""
    s2 = config.get("stage2", {})
    east_asia_font = s2.get("east_asia_font", "")
    blocks = parse_intermediate(read_text(md_path))
    document = Document(str(template_docx_path))
    items = list(iter_block_items(document))
    if len(items) != len(blocks):
        logger.warning(
            f"中间稿块数({len(blocks)})与模板块数({len(items)})不一致，按最小公共数量回填"
        )
    count = min(len(items), len(blocks))
    filled_tables = 0
    for docx_item, block in zip(items[:count], blocks[:count]):
        if block.type in ("heading", "blank"):
            continue
        if block.type == "table":
            rows = block.rows or parse_markdown_table(block.text) or []
            fill_table(docx_item, rows, east_asia_font)
            filled_tables += 1
        else:
            replace_paragraph_text(docx_item, block.text, east_asia_font)
    out_docx_path = Path(out_docx_path)
    ensure_dir(out_docx_path.parent)
    document.save(str(out_docx_path))
    logger.info(f"教案 docx 已导出: {out_docx_path}（回填表格 {filled_tables} 个）")
    return out_docx_path
