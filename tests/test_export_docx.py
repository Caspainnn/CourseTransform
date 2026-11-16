import json

from docx import Document
from docx.oxml.ns import qn

from src.export_docx import export
from src.template_parser import markdown_table
from src.intermediate import serialize_blocks
from src.template_parser import parse_template
from src.validator import generate_with_validation


class ScriptedLLM:
    def __init__(self, responses):
        self.responses = list(responses)

    def chat(self, system=None, user=None, **kwargs):
        return self.responses.pop(0) if self.responses else "默认内容"


def test_export_fills_and_preserves_structure(tmp_path, template_docx):
    blocks = parse_template(template_docx)
    assert len(blocks) == 3
    blocks[1].text = "教师：张三"
    blocks[2].rows = [["环节", "时间"], ["复习诊断", "10分钟"]]
    blocks[2].text = markdown_table(blocks[2].rows)
    md_path = tmp_path / "mid.md"
    md_path.write_text(serialize_blocks(blocks), encoding="utf-8")
    config = {"stage2": {"east_asia_font": "仿宋"}}
    out = export(md_path, template_docx, tmp_path / "out" / "教案.docx", config)
    assert out.exists()
    doc = Document(str(out))
    texts = [p.text for p in doc.paragraphs]
    assert "教师：张三" in texts
    assert "教师：____" not in texts
    table = doc.tables[0]
    assert table.cell(0, 0).text == "环节"
    assert table.cell(1, 0).text == "复习诊断"
    assert table.cell(1, 1).text == "10分钟"
    target_para = [p for p in doc.paragraphs if p.text == "教师：张三"][0]
    run = target_para.runs[0]
    rPr = run._element.find(qn("w:rPr"))
    assert rPr is not None
    rFonts = rPr.find(qn("w:rFonts"))
    assert rFonts is not None
    assert rFonts.get(qn("w:eastAsia")) == "仿宋"


def test_export_keeps_heading_untouched(tmp_path, template_docx):
    blocks = parse_template(template_docx)
    md_path = tmp_path / "mid.md"
    md_path.write_text(serialize_blocks(blocks), encoding="utf-8")
    config = {"stage2": {"east_asia_font": ""}}
    out = export(md_path, template_docx, tmp_path / "out.docx", config)
    doc = Document(str(out))
    assert any(p.text == "二、教学目标" for p in doc.paragraphs)


def test_export_multiline_paragraph(tmp_path, template_docx):
    blocks = parse_template(template_docx)
    blocks[1].text = "第一行内容\n第二行内容"
    md_path = tmp_path / "mid.md"
    md_path.write_text(serialize_blocks(blocks), encoding="utf-8")
    config = {"stage2": {"east_asia_font": ""}}
    out = export(md_path, template_docx, tmp_path / "out.docx", config)
    doc = Document(str(out))
    para = [p for p in doc.paragraphs if "第一行" in p.text][0]
    assert "第二行内容" in para.text


def test_full_pipeline_with_fake_llm(tmp_path, template_docx):
    config = {
        "stage2": {
            "total_minutes": 45,
            "max_validation_rounds": 2,
            "fill_markers": ["____"],
            "table_retry_on_shape_mismatch": True,
            "summary_length": 300,
        },
        "validation": {"checkitems": []},
    }
    fail_raw = json.dumps(
        {
            "results": [
                {"item": "板块齐全", "pass": False, "reason": "教学目标空洞"},
                {"item": "时间合计", "pass": False, "reason": "合计非45分钟"},
                {"item": "教材依据", "pass": False, "reason": "编造数据"},
                {"item": "语言", "pass": True, "reason": "中文"},
            ]
        },
        ensure_ascii=False,
    )
    pass_raw = json.dumps(
        {
            "results": [
                {"item": "a", "pass": True},
                {"item": "b", "pass": True},
                {"item": "c", "pass": True},
                {"item": "d", "pass": True},
            ]
        },
        ensure_ascii=False,
    )
    refined = (
        '<!-- CTBLOCK index=0 type=heading style="Heading 1" -->\n'
        "二、教学目标\n\n"
        '<!-- CTBLOCK index=1 type=paragraph style="Normal" -->\n'
        "修改后的教学目标：掌握生成对抗网络的原理\n\n"
        '<!-- CTBLOCK index=2 type=table style="table" -->\n'
        "| 环节 | 时间 |\n|---|---|\n| 复习诊断 | 45分钟 |\n"
    )
    llm = ScriptedLLM(
        [
            "初版教学目标",
            "| 环节 | 时间 |\n|---|---|\n| 复习诊断 | 45分钟 |",
            fail_raw,
            refined,
            pass_raw,
        ]
    )
    out_dir = tmp_path / "out"
    md_path, reports = generate_with_validation(
        "教材正文：生成对抗网络。", template_docx, out_dir, llm, config
    )
    assert len(reports) == 2
    assert reports[0]["passed"] is False
    assert reports[1]["passed"] is True
    assert (out_dir / "validation_report_round0.json").exists()
    assert (out_dir / "validation_report_round1.json").exists()
    assert "修改后的教学目标" in md_path.read_text(encoding="utf-8")
    docx_path = export(md_path, template_docx, out_dir / "教案.docx", config)
    doc = Document(str(docx_path))
    assert any("修改后的教学目标" in p.text for p in doc.paragraphs)


def test_generate_with_validation_discards_broken_refine(tmp_path, template_docx):
    config = {
        "stage2": {
            "total_minutes": 0,
            "max_validation_rounds": 1,
            "fill_markers": ["____"],
            "table_retry_on_shape_mismatch": True,
            "summary_length": 300,
        },
        "validation": {"checkitems": []},
    }
    fail_raw = json.dumps(
        {"results": [{"item": "a", "pass": False}, {"item": "b", "pass": False}, {"item": "c", "pass": False}]},
        ensure_ascii=False,
    )
    pass_raw = json.dumps(
        {"results": [{"item": "a", "pass": True}, {"item": "b", "pass": True}, {"item": "c", "pass": True}]},
        ensure_ascii=False,
    )
    llm = ScriptedLLM(
        [
            "初版教学目标",
            "| 环节 | 时间 |\n|---|---|\n| 复习诊断 | 45分钟 |",
            fail_raw,
            "模型输出的损坏文本，没有块标记",
            pass_raw,
        ]
    )
    out_dir = tmp_path / "out"
    md_path, reports = generate_with_validation(
        "教材正文", template_docx, out_dir, llm, config
    )
    assert len(reports) == 2
    assert "初版教学目标" in md_path.read_text(encoding="utf-8")
