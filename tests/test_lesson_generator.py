from docx import Document

from src.lesson_generator import (
    fill_table_block,
    generate,
    normalize_rows,
)
from src.template_parser import parse_template


class ScriptedLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, system=None, user=None, **kwargs):
        self.calls.append({"system": system, "user": user})
        return self.responses.pop(0) if self.responses else "默认内容"


def test_normalize_rows():
    rows = normalize_rows([["a"], ["b", "c", "d"]], 3, 2)
    assert rows == [["a", ""], ["b", "c"], ["", ""]]


def test_fill_table_block_retry_on_shape_mismatch():
    llm = ScriptedLLM(
        [
            "| a | b |\n|---|---|",
            "| a | b | c |\n|---|---|---|\n| 1 | 2 | 3 |",
        ]
    )
    expected = [["环节", "时间", "内容"], ["复习", "____", "____"]]
    rows = fill_table_block(llm, "sys", "请输出表格", expected, retry_on_mismatch=True)
    assert rows == [["a", "b", "c"], ["1", "2", "3"]]
    assert len(llm.calls) == 2
    assert "行列数不符合要求" in llm.calls[1]["user"]


def test_fill_table_block_no_retry_keeps_original_on_unparsable():
    llm = ScriptedLLM([""])
    expected = [["环节", "时间"], ["复习", "____"]]
    rows = fill_table_block(llm, "sys", "请输出表格", expected, retry_on_mismatch=False)
    assert rows == expected


def test_fill_table_block_mismatch_normalized_without_retry():
    llm = ScriptedLLM(["| 只有 | 一格 |"])
    expected = [["环节", "时间"], ["复习", "____"]]
    rows = fill_table_block(llm, "sys", "请输出表格", expected, retry_on_mismatch=False)
    assert len(rows) == 2
    assert all(len(row) == 2 for row in rows)


def test_generate_end_to_end_with_fake_llm(tmp_path, template_docx):
    config = {
        "stage2": {
            "total_minutes": 45,
            "fill_markers": ["____"],
            "table_retry_on_shape_mismatch": True,
            "summary_length": 300,
        }
    }
    llm = ScriptedLLM(
        [
            "知识目标：理解生成对抗网络的基本原理",
            "| 环节 | 时间 |\n|---|---|\n| 复习诊断 | 45分钟 |",
        ]
    )
    out_md = tmp_path / "out" / "教案中间稿.md"
    generate("教材正文：生成对抗网络由生成器与判别器组成。", template_docx, out_md, llm, config)
    text = out_md.read_text(encoding="utf-8")
    assert "知识目标：理解生成对抗网络的基本原理" in text
    assert "复习诊断" in text and "45分钟" in text
    assert "CTBLOCK" in text
    assert "____" not in text
    assert "45 分钟" in llm.calls[0]["system"]
    assert "二、教学目标" in llm.calls[0]["system"]
    assert "知识目标" in llm.calls[1]["user"]


def test_generate_blocks_count_matches_template(tmp_path, template_docx):
    config = {"stage2": {"total_minutes": 0, "fill_markers": ["____"], "table_retry_on_shape_mismatch": True, "summary_length": 300}}
    llm = ScriptedLLM(["填充段落", "| 环节 | 时间 |\n|---|---|\n| 复习 | 45分钟 |"])
    out_md = tmp_path / "mid.md"
    generate("教材正文", template_docx, out_md, llm, config)
    blocks = parse_template(template_docx)
    from src.intermediate import parse_intermediate

    parsed = parse_intermediate(out_md.read_text(encoding="utf-8"))
    assert len(parsed) == len(blocks)
    assert parsed[0].type == "heading"
    assert parsed[0].text == "二、教学目标"
