import pytest
from docx import Document


@pytest.fixture
def template_docx(tmp_path):
    doc = Document()
    doc.add_heading("二、教学目标", level=1)
    doc.add_paragraph("____")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "环节"
    table.cell(0, 1).text = "时间"
    table.cell(1, 0).text = "____"
    table.cell(1, 1).text = "____"
    path = tmp_path / "template.docx"
    doc.save(str(path))
    return path
