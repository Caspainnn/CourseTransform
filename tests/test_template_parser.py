from docx import Document

from src.template_parser import parse_template


def build_template(tmp_path):
    doc = Document()
    doc.add_heading("一、课程基本信息", level=1)
    doc.add_paragraph("教师：____")
    table = doc.add_table(rows=2, cols=3)
    table.cell(0, 0).text = "环节"
    table.cell(0, 1).text = "时间"
    table.cell(0, 2).text = "主要内容"
    table.cell(1, 0).text = "____"
    table.cell(1, 1).text = "____"
    table.cell(1, 2).text = "____"
    doc.add_heading("二、教学目标", level=1)
    doc.add_paragraph("")
    path = tmp_path / "template.docx"
    doc.save(str(path))
    return path


def test_parse_template_block_order(tmp_path):
    path = build_template(tmp_path)
    blocks = parse_template(path)
    assert [b.type for b in blocks] == ["heading", "paragraph", "table", "heading", "blank"]
    assert blocks[0].text == "一、课程基本信息"
    assert blocks[3].text == "二、教学目标"


def test_parse_template_table_structure(tmp_path):
    path = build_template(tmp_path)
    blocks = parse_template(path)
    table_block = blocks[2]
    assert table_block.type == "table"
    assert len(table_block.rows) == 2
    assert len(table_block.rows[0]) == 3
    assert table_block.rows[0][0] == "环节"
    assert table_block.rows[1][2] == "____"


def test_parse_template_placeholders(tmp_path):
    path = build_template(tmp_path)
    blocks = parse_template(path)
    assert blocks[1].placeholders == ["____"]
    assert "____" in blocks[2].placeholders
    assert blocks[0].placeholders == []


def test_parse_template_heading_style_preserved(tmp_path):
    path = build_template(tmp_path)
    blocks = parse_template(path)
    assert blocks[0].style_name.startswith("Heading")
    assert blocks[0].type == "heading"


def test_custom_fill_markers(tmp_path):
    doc = Document()
    doc.add_paragraph("教师：（待填写）")
    path = tmp_path / "t.docx"
    doc.save(str(path))
    blocks = parse_template(path, fill_markers=["（待填写）"])
    assert blocks[0].placeholders == ["（待填写）"]
