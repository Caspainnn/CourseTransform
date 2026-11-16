import json

from docx import Document

from src.llm_client import MockLLMClient
from src.lesson_generator import generate
from src.template_parser import parse_template
from src.validator import validate


def build_template(tmp_path):
    doc = Document()
    doc.add_heading("二、教学目标", level=1)
    doc.add_paragraph("____")
    table = doc.add_table(rows=3, cols=3)
    header = ["环节", "时间", "主要内容"]
    for i, h in enumerate(header):
        table.cell(0, i).text = h
    for r, name in ((1, "复习诊断"), (2, "数据核验")):
        table.cell(r, 0).text = name
        for c in (1, 2):
            table.cell(r, c).text = "____"
    path = tmp_path / "template.docx"
    doc.save(str(path))
    return path


def test_mock_fills_table_and_time_sums_to_45(tmp_path, template_docx):
    config = {
        "stage2": {
            "total_minutes": 45,
            "fill_markers": ["____"],
            "table_retry_on_shape_mismatch": True,
            "summary_length": 300,
        }
    }
    knowledge = "生成对抗网络由生成器与判别器组成，通过对抗训练学习数据分布。"
    out_md = tmp_path / "mid.md"
    generate(knowledge, template_docx, out_md, MockLLMClient(), config)
    text = out_md.read_text(encoding="utf-8")
    assert "____" not in text
    assert "环节" in text
    assert "45分钟" in text
    # 时间合计必须为 45 分钟
    import re

    minutes = [int(m) for m in re.findall(r"(\d+)分钟", text)]
    assert sum(minutes) == 45


def test_mock_judge_returns_matching_items():
    llm = MockLLMClient()
    raw = llm.chat(system="你是评审专家，只输出 JSON", user='【检查表】\n["板块齐全", "时间合计"]')
    data = json.loads(raw)
    assert len(data["results"]) == 2
    assert all(r["pass"] for r in data["results"])
    checkitems = ["板块齐全", "时间合计"]
    report = validate("教案", "教材", checkitems, llm)
    assert report["passed"] is True
