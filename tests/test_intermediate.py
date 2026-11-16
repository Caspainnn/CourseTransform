import pytest

from src.template_parser import Block, markdown_table
from src.intermediate import parse_intermediate, parse_markdown_table, serialize_blocks


def test_serialize_and_parse_roundtrip():
    blocks = [
        Block(index=0, type="heading", text="一、课程基本信息", style_name="Heading 1"),
        Block(index=1, type="paragraph", text="教师：张三", style_name="Normal"),
        Block(
            index=2,
            type="table",
            text="| a | b |\n|---|---|\n| 1 | 2 |",
            style_name="table",
            rows=[["a", "b"], ["1", "2"]],
        ),
        Block(index=3, type="blank", text="", style_name="Normal"),
    ]
    md_text = serialize_blocks(blocks)
    parsed = parse_intermediate(md_text)
    assert len(parsed) == 4
    assert [b.type for b in parsed] == ["heading", "paragraph", "table", "blank"]
    assert parsed[0].text == "一、课程基本信息"
    assert parsed[0].style_name == "Heading 1"
    assert parsed[1].text == "教师：张三"
    assert parsed[2].rows == [["a", "b"], ["1", "2"]]
    assert parsed[3].type == "blank"


def test_parse_intermediate_rejects_missing_markers():
    with pytest.raises(ValueError):
        parse_intermediate("# 没有块标记的普通 markdown")


def test_parse_markdown_table_variants():
    assert parse_markdown_table("| a | b |\n| --- | --- |\n| 1 | 2 |") == [
        ["a", "b"],
        ["1", "2"],
    ]
    assert parse_markdown_table("a | b\n1 | 2") == [["a", "b"], ["1", "2"]]
    assert parse_markdown_table("") is None


def test_parse_markdown_table_keeps_pipes():
    rows = parse_markdown_table("| a\\|b | c |\n|---|---|\n| 1 | 2 |")
    assert rows[0][0] == "a|b"


def test_markdown_table_escapes():
    text = markdown_table([["a|b"], ["c"]])
    assert "a\\|b" in text
