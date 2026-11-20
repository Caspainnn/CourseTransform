"""Render a DOCX into safe, editable HTML that mirrors Word's page layout.

The renderer is intentionally text-first: paragraphs and table cells keep the
same block ids used by the inline editor, images are embedded as data URIs and
the original page margins/styles are approximated in CSS. Complex formatting
stays in the downloaded DOCX.
"""
from __future__ import annotations

import base64
import html
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

EMU_PER_PX = 9525

STYLE_CLASS = {
    'Heading 1': 'docx-h1',
    'Heading 2': 'docx-h2',
    'Heading 3': 'docx-h3',
    'Heading 4': 'docx-h4',
    'List Paragraph': 'docx-li',
}


def iter_blocks(doc):
    """Yield (block_id, paragraph, kind) in document order."""
    counter = 0
    table_index = -1
    for child in doc.element.body.iterchildren():
        if child.tag == qn('w:p'):
            counter += 1
            yield f'p{counter:04d}', Paragraph(child, doc), 'paragraph'
        elif child.tag == qn('w:tbl'):
            table_index += 1
            table = Table(child, doc)
            for r_index, row in enumerate(table.rows):
                previous = None
                for c_index, cell in enumerate(row.cells):
                    if previous is not None and cell._tc is previous:
                        continue
                    previous = cell._tc
                    for paragraph in cell.paragraphs:
                        counter += 1
                        yield f't{table_index}r{r_index}c{c_index}p{counter:04d}', paragraph, 'cell'


def _esc(value):
    return html.escape(str(value or ''), quote=True)


def _images(paragraph, doc):
    out = []
    for run in paragraph.runs:
        for blip in run._element.xpath('.//a:blip'):
            rid = blip.get(qn('r:embed'))
            part = doc.part.related_parts.get(rid) if rid else None
            if not part:
                continue
            payload = base64.b64encode(part.blob).decode('ascii')
            out.append(f'<img class="docx-img" alt="" src="data:{part.content_type};base64,{payload}">')
    return ''.join(out)


def _inline(paragraph, doc):
    parts = []
    for run in paragraph.runs:
        text = _esc(run.text).replace('\n', '<br>')
        if not text:
            continue
        if run.bold:
            text = f'<strong>{text}</strong>'
        if run.italic:
            text = f'<em>{text}</em>'
        if run.underline:
            text = f'<u>{text}</u>'
        parts.append(text)
    if not parts and paragraph.text:
        parts.append(_esc(paragraph.text).replace('\n', '<br>'))
    parts.append(_images(paragraph, doc))
    return ''.join(parts)


ALIGN = {0: 'left', 1: 'center', 2: 'right', 3: 'justify'}


def _paragraph(paragraph, doc, block_id):
    style = paragraph.style.name if paragraph.style else ''
    css = []
    align = ALIGN.get(paragraph.alignment)
    if align:
        css.append(f'text-align:{align}')
    classes = ['docx-p']
    mapped = STYLE_CLASS.get(style)
    if mapped:
        classes.append(mapped)
    style_attr = f' style="{";".join(css)}"' if css else ''
    return (f'<p data-block-id="{block_id}" class="{" ".join(classes)}"{style_attr}>'
            f'{_inline(paragraph, doc)}</p>')


def _table(table, doc, table_index, counter):
    rows = []
    for r_index, row in enumerate(table.rows):
        cells = []
        previous = None
        for c_index, cell in enumerate(row.cells):
            # Horizontal merges repeat the same <w:tc> inside one row; keep the
            # first occurrence and let its colspan cover the rest.
            if previous is not None and cell._tc is previous:
                continue
            previous = cell._tc
            span = 1
            grid = cell._tc.tcPr.gridSpan if cell._tc.tcPr is not None else None
            if grid is not None and grid.val:
                span = max(1, int(grid.val))
            shade = ''
            shd = cell._tc.tcPr.find(qn('w:shd')) if cell._tc.tcPr is not None else None
            if shd is not None and shd.get(qn('w:fill')):
                shade = f' style="background:#{shd.get(qn("w:fill"))}"'
            parts = []
            for paragraph in cell.paragraphs:
                counter += 1
                parts.append(_paragraph(paragraph, doc, f't{table_index}r{r_index}c{c_index}p{counter:04d}'))
            span_attr = f' colspan="{span}"' if span > 1 else ''
            cells.append(f'<td{span_attr}{shade}>{"".join(parts)}</td>')
        rows.append('<tr>' + ''.join(cells) + '</tr>')
    return '<table class="docx-table">' + ''.join(rows) + '</table>', counter


def render_docx_html(path):
    doc = Document(str(path))
    body = []
    counter = 0
    table_index = -1
    for child in doc.element.body.iterchildren():
        if child.tag == qn('w:p'):
            counter += 1
            body.append(_paragraph(Paragraph(child, doc), doc, f'p{counter:04d}'))
        elif child.tag == qn('w:tbl'):
            table_index += 1
            markup, counter = _table(Table(child, doc), doc, table_index, counter)
            body.append(markup)
    section = doc.sections[0] if doc.sections else None
    padding = ''
    if section is not None:
        left = int(section.left_margin / EMU_PER_PX) if section.left_margin else 96
        right = int(section.right_margin / EMU_PER_PX) if section.right_margin else 96
        top = int(section.top_margin / EMU_PER_PX) if section.top_margin else 96
        bottom = int(section.bottom_margin / EMU_PER_PX) if section.bottom_margin else 96
        padding = f'padding:{top}px {right}px {bottom}px {left}px;'
    return (f'<div class="docx-page" style="{padding}">' + ''.join(body) + '</div>')
