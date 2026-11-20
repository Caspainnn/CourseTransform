"""生成示例教案模板（DOCX / Markdown），方便直接跑通流程。

运行：python examples/make_templates.py
"""
from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

BASE = Path(__file__).resolve().parent
TEMPLATES = BASE / "templates"


def set_run(run, name="宋体", size=11, bold=False):
    run.font.name = name
    run.font.size = Pt(size)
    run.bold = bold
    rpr = run._r.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    rfonts.set(qn("w:eastAsia"), name)


def set_cell(cell, text, bold=False, shade=None, size=10):
    cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    p = cell.paragraphs[0]
    p.paragraph_format.space_before = Pt(1)
    p.paragraph_format.space_after = Pt(1)
    run = p.add_run(text)
    set_run(run, "宋体", size, bold)
    if shade:
        tc_pr = cell._tc.get_or_add_tcPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:fill"), shade)
        tc_pr.append(shd)


def set_col_widths(table, widths):
    table.autofit = False
    for row in table.rows:
        for i, width in enumerate(widths):
            row.cells[i].width = Cm(width)


def add_heading(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(10)
    p.paragraph_format.space_after = Pt(4)
    run = p.add_run(text)
    set_run(run, "黑体", 13, True)


def add_blank(doc, n=1):
    for _ in range(n):
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(2)
        run = p.add_run("")
        set_run(run, "宋体", 11)


def make_table_template():
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
    normal.font.size = Pt(11)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("教 学 设 计")
    set_run(run, "黑体", 20, True)

    info = doc.add_table(rows=3, cols=4)
    info.style = "Table Grid"
    set_col_widths(info, [2.6, 5.9, 2.6, 5.9])
    values = [
        ("课题", "{{title}}", "学科", "{{subject}}"),
        ("年级", "{{grade}}", "课时", "{{duration}}"),
        ("课型", "{{lesson_type}}", "教材", "{{textbook}}"),
    ]
    for r, row in enumerate(values):
        for c, value in enumerate(row):
            set_cell(info.cell(r, c), value, bold=(c % 2 == 0), shade="F2F2F2" if c % 2 == 0 else None)

    for title in ["一、教材分析", "二、学情分析", "三、教学目标", "四、教学重点与难点", "五、教学准备"]:
        add_heading(doc, title)
        add_blank(doc)

    add_heading(doc, "六、教学过程")
    add_blank(doc)
    table = doc.add_table(rows=2, cols=6)
    table.style = "Table Grid"
    set_col_widths(table, [2.4, 1.2, 4.6, 4.1, 3.0, 1.7])
    headers = ["教学环节", "时间", "教师活动", "学生活动", "设计意图", "评价方式"]
    for c, h in enumerate(headers):
        set_cell(table.cell(0, c), h, bold=True, shade="E8EEF7", size=9.5)
    for c, value in enumerate(["（示例）导入", "5", "（请填写）", "（请填写）", "（请填写）", "（请填写）"]):
        set_cell(table.cell(1, c), value, size=9.5)

    for title in ["七、板书设计", "八、作业设计", "九、教学评价", "十、教学反思"]:
        add_heading(doc, title)
        add_blank(doc)

    footer = section.footer
    fp = footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = fp.add_run("XX学校 教学设计模板（示例）")
    set_run(run, "宋体", 8)
    run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)

    out = TEMPLATES / "教案模板_表格式.docx"
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)
    return out


def make_placeholder_template():
    doc = Document()
    section = doc.sections[0]
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2.2)
    section.bottom_margin = Cm(2.0)
    section.left_margin = Cm(2.2)
    section.right_margin = Cm(2.2)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("{{title}}")
    set_run(run, "黑体", 18, True)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("学科：{{subject}}　年级：{{grade}}　课时：{{duration}}　课型：{{lesson_type}}")
    set_run(run, "宋体", 10.5)

    sections = [
        ("一、教材分析", "{{analysis}}"),
        ("二、学情分析", "{{student}}"),
        ("三、教学目标", "{{objectives}}"),
        ("四、教学重点与难点", "{{key_difficulties}}"),
        ("五、教学方法与准备", "{{methods}}\n{{materials}}"),
        ("六、教学过程", "{{process}}"),
        ("七、板书设计", "{{board}}"),
        ("八、作业设计", "{{homework}}"),
        ("九、教学评价", "{{assessment}}"),
        ("十、教学反思", "{{reflection}}"),
    ]
    for title, placeholder in sections:
        add_heading(doc, title)
        p = doc.add_paragraph()
        p.paragraph_format.line_spacing = 1.5
        run = p.add_run(placeholder)
        set_run(run, "宋体", 11)

    out = TEMPLATES / "教案模板_占位符.docx"
    doc.save(out)
    return out


def make_md_template():
    text = """# {{title}} 教学设计

> 学科：{{subject}} ｜ 年级：{{grade}} ｜ 课时：{{duration}} ｜ 课型：{{lesson_type}}

## 一、教材分析

{{analysis}}

## 二、学情分析

{{student}}

## 三、教学目标

{{objectives}}

## 四、教学重点与难点

{{key_difficulties}}

## 五、教学方法与准备

{{methods}}

{{materials}}

## 六、教学过程

{{process}}

## 七、板书设计

{{board}}

## 八、作业设计

{{homework}}

## 九、教学评价

{{assessment}}

## 十、教学反思

{{reflection}}
"""
    out = TEMPLATES / "教案模板.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="\n")
    return out


if __name__ == "__main__":
    for path in (make_table_template(), make_placeholder_template(), make_md_template()):
        print(path)
