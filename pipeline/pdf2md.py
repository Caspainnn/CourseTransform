"""Stage 1: extract a text-layer textbook PDF into per-chapter Markdown + images.

This implements the real stage-1 capability inside the unified workbench:
PyMuPDF text extraction, chapter splitting (第N章), numbered-section heading
normalisation (x.y -> ##, x.y.z -> ###), and original image extraction with
page/caption-aware references. Scanned PDFs are rejected with E_OCR_REQUIRED
instead of returning empty or invented Markdown; the documented path for
scanned books is to run MinerU first and feed its draft Markdown to stage 2.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pymupdf

from documents import Problem

CHAPTER_RE = re.compile(r'^第\s*([0-9一二三四五六七八九十百千零两]+)\s*章\s*(\S.*)?$')
SECTION_RE = re.compile(r'^(\d{1,2}(?:\.\d{1,2})+)\s+\S')
SENTENCE_END_RE = re.compile(r'[。；，,；！？]|\s\d{1,4}\s*$')
CAPTION_RE = re.compile(r'^图\s*\d+(?:[.\-－–—]\d+)*(?:\s|$|[a-zA-Z])')
TOC_RE = re.compile(r'\.{3,}|[…⋯]{2,}|\s{3,}\d{1,4}\s*$')

MIN_TEXT_CHARS = 20
MIN_IMAGE_SIDE = 32


def _safe_name(text, limit=60):
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', '_', str(text).strip()).strip('_')
    return (cleaned[:limit] or 'chapter')


def _log(log, message):
    if log:
        log(message)


def page_texts(doc):
    return [page.get_text('text') or '' for page in doc]


def split_chapters(texts):
    """Assign each page to the latest 第N章 heading; ignore table-of-contents lines.

    The first page of a chapter is trimmed to the actual heading so that a TOC
    or cover block above the heading does not leak into the chapter body.
    """
    chapters = []
    current = None
    for number, text in enumerate(texts, start=1):
        lines = text.splitlines()
        for index, line in enumerate(lines):
            line = line.strip()
            if not line or len(line) > 60 or not CHAPTER_RE.match(line):
                continue
            if TOC_RE.search(line):
                continue
            current = {'title': line, 'pages': [], 'firstPage': number, 'firstLine': index}
            chapters.append(current)
        if current is None:
            current = {'title': '全书', 'pages': [], 'firstPage': number, 'firstLine': 0}
            chapters.append(current)
        start = current['firstLine'] if current['firstPage'] == number else 0
        current['pages'].append((number, text, start))
    # A front-matter-only "全书" block (cover/TOC) would be re-detected as fake
    # chapters by downstream textbook parsing; drop it when real chapters exist.
    if len(chapters) > 1:
        chapters = [c for c in chapters if c['title'] != '全书']
    return chapters


def format_headings(lines):
    out = []
    for line in lines:
        stripped = line.strip()
        match = SECTION_RE.match(stripped)
        if match and len(stripped) <= 60 and not SENTENCE_END_RE.search(stripped):
            depth = match.group(1).count('.') + 1
            out.append('#' * min(depth, 4) + ' ' + stripped)
        else:
            out.append(line)
    return out


def extract_images(doc, image_dir):
    """Export page images as image-0001.jpg … and map page number -> [Path]."""
    image_dir.mkdir(parents=True, exist_ok=True)
    mapping = {}
    counter = 0
    for page_no in range(1, doc.page_count + 1):
        page = doc[page_no - 1]
        for info in page.get_images(full=True):
            xref = info[0]
            pix = None
            try:
                pix = pymupdf.Pixmap(doc, xref)
                if pix.width < MIN_IMAGE_SIDE or pix.height < MIN_IMAGE_SIDE:
                    continue
                if pix.colorspace and pix.colorspace.n > 3:
                    pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                if pix.alpha:
                    pix = pymupdf.Pixmap(pix, 0)
                counter += 1
                target = image_dir / f'image-{counter:04d}.jpg'
                pix.save(str(target))
                mapping.setdefault(page_no, []).append(target)
            except Exception:
                _log(None, f'第 {page_no} 页图片 xref={xref} 无法解码，已跳过')
            finally:
                if pix is not None:
                    del pix
    return mapping


def render_chapter(chapter, images, image_dir_name='images'):
    parts = []
    for page_no, text, start in chapter['pages']:
        lines = format_headings(text.splitlines()[start:])
        refs = [f'![{path.stem}]({image_dir_name}/{path.name})' for path in images.get(page_no, [])]
        if refs:
            caption_indexes = [i for i, line in enumerate(lines) if CAPTION_RE.match(line.strip())]
            for offset, (ref, index) in enumerate(zip(refs, caption_indexes)):
                lines.insert(index + 1 + offset, ref)
            lines.extend(refs[len(caption_indexes):])
        parts.append('\n'.join(lines))
    body = '\n\n'.join(part for part in parts if part.strip())
    return f"# {chapter['title']}\n\n{body}\n"


def extract_pdf(pdf_path, out_dir, log=None):
    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(str(pdf_path)) as doc:
        if doc.page_count == 0:
            raise Problem('PDF没有页面', code='E_DOCUMENT')
        if doc.page_count > 800:
            raise Problem('PDF超过800页，请按章节拆分后再上传', code='E_DOCUMENT')
        texts = page_texts(doc)
        total = sum(len(text.strip()) for text in texts)
        empty_pages = sum(1 for text in texts if len(text.strip()) < MIN_TEXT_CHARS)
        if total < MIN_TEXT_CHARS:
            raise Problem(
                f'扫描版 PDF 没有可提取文字（{doc.page_count} 页中 {empty_pages} 页为空）。'
                '当前服务未配置 OCR，不会生成空 Markdown 冒充成功。'
                '请先用 MinerU 生成草稿 Markdown，再在“教材 MD 转教案 Word”页上传该草稿；'
                '或上传文字版 PDF。',
                code='E_OCR_REQUIRED',
            )
        print(f'[pdf2md] {pdf_path.name}: {doc.page_count} 页，{total} 字符，{empty_pages} 页近空', flush=True)
        images = extract_images(doc, out_dir / 'images')
        chapters = split_chapters(texts)
        _log(log, f'切分出 {len(chapters)} 个章节，导出 {sum(len(v) for v in images.values())} 张图片')
        results = []
        used_titles = set()
        for index, chapter in enumerate(chapters, start=1):
            markdown = render_chapter(chapter, images)
            base = _safe_name(chapter['title'])
            name = base
            suffix = 2
            while name in used_titles:
                name = f'{base}_{suffix}'
                suffix += 1
            used_titles.add(name)
            md_path = out_dir / f'{name}.md'
            md_path.write_text(markdown, encoding='utf-8')
            referenced = sorted({path for page_no in range(1, len(texts) + 1) if page_no in [p[0] for p in chapter['pages']]
                                 for path in images.get(page_no, [])})
            results.append({
                'index': index,
                'title': chapter['title'],
                'md': str(md_path),
                'mdName': md_path.name,
                'chars': len(markdown),
                'pages': [p[0] for p in chapter['pages']],
                'images': [str(path) for path in referenced],
                'imageCount': len(referenced),
            })
            _log(log, f'已生成 {md_path.name}（{len(chapter["pages"])} 页，{len(referenced)} 张图）')
    if not results:
        raise Problem('PDF没有生成任何章节内容', code='E_DOCUMENT')
    return {
        'pdf': str(pdf_path),
        'pdfName': pdf_path.name,
        'pages': len(texts),
        'images': sum(len(v) for v in images.values()),
        'chapters': results,
    }


def run_job(job_dir, log=None):
    job_dir = Path(job_dir)
    out_dir = job_dir / 'out'
    result = extract_pdf(job_dir / 'source.pdf', out_dir, log=log)
    (job_dir / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    # Keep the raw per-page text for diagnosis when a future OCR path is added.
    return result