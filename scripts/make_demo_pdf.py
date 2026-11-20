"""Generate the repository demo textbook PDF from the synthetic example Markdown.

The example text and figures are project-generated teaching samples (no third
party textbook content). The generated PDF has a real text layer and embedded
images so the PDF→MD stage can be demonstrated without external tools.
"""
from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'examples' / 'textbook_with_images.md'
TARGET = ROOT / 'examples' / 'demo_textbook.pdf'
FONT_NAME = 'china-s'
PAGE = (595, 842)
MARGIN = 54
BODY_SIZE = 11
LINE_HEIGHT = 4.6
MAX_WIDTH = PAGE[0] - MARGIN * 2


def wrap(text, size):
    # CJK-aware wrapping using measured width; falls back to character count.
    return [text[index:index + 38] for index in range(0, len(text), 38)]


def main():
    if not SOURCE.is_file():
        raise SystemExit('missing examples/textbook_with_images.md')
    thumb_dir = Path(tempfile.mkdtemp(prefix='ct-demo-thumb-'))
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE[0], height=PAGE[1])
    page.insert_font(fontname=FONT_NAME)
    y = MARGIN

    def new_page():
        nonlocal page, y
        page = doc.new_page(width=PAGE[0], height=PAGE[1])
        page.insert_font(fontname=FONT_NAME)
        y = MARGIN

    def need(height):
        nonlocal y
        if y + height > PAGE[1] - MARGIN:
            new_page()

    for raw in SOURCE.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line:
            y += 3
            continue
        image = re.match(r'!\[([^\]]*)\]\(([^)]+)\)', line)
        if image:
            path = (SOURCE.parent / image.group(2)).resolve()
            if path.is_file():
                from PIL import Image
                thumb = thumb_dir / (path.stem + '.jpg')
                with Image.open(path) as original:
                    preview = original.convert('RGB')
                    preview.thumbnail((720, 540))
                    preview.save(thumb, 'JPEG', quality=86)
                need(200)
                page.insert_image(pymupdf.Rect(MARGIN, y, MARGIN + 320, y + 180), filename=str(thumb),
                                  keep_proportion=True)
                y += 186
            continue
        heading = re.match(r'^(#{1,3})\s+(.*)$', line)
        if heading:
            level = len(heading.group(1))
            size = 18 if level == 1 else 13
            need(size + 22)
            y += 6
            page.insert_text((MARGIN, y + size), heading.group(2), fontname=FONT_NAME, fontsize=size)
            y += size + 10
            continue
        size = BODY_SIZE
        for chunk in wrap(line, size):
            need(16)
            page.insert_text((MARGIN, y + size), chunk, fontname=FONT_NAME, fontsize=size)
            y += 15
        y += 1
    doc.save(TARGET)
    shutil.rmtree(thumb_dir, ignore_errors=True)
    print(f'wrote {TARGET} ({TARGET.stat().st_size} bytes, {doc.page_count} pages)')
    doc.close()


if __name__ == '__main__':
    main()