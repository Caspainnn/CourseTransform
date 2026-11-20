"""Stage 1: extract a text-layer textbook PDF into per-chapter Markdown + images.

This implements the real stage-1 capability inside the unified workbench:
PyMuPDF text extraction, chapter splitting (第N章), numbered-section heading
normalisation (x.y -> ##, x.y.z -> ###), and original image extraction with
page/caption-aware references. Scanned pages prefer an installed MinerU CLI
(layout, figures, formulas); without it the built-in RapidOCR path is used.
Neither path invents text: failures surface as explicit errors.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

import pymupdf

from documents import Problem
from pipeline import mineru_adapter as mineru

CHAPTER_RE = re.compile(r'^第\s*([0-9一二三四五六七八九十百千零两]+)\s*章\s*(\S.*)?$')
SECTION_RE = re.compile(r'^(\d{1,2}(?:\.\d{1,2})+)\s+\S')
SENTENCE_END_RE = re.compile(r'[。；，,；！？]|\s\d{1,4}\s*$')
CAPTION_RE = re.compile(r'^图\s*\d+(?:[.\-－–—]\d+)*(?:\s|$|[a-zA-Z])')
TOC_RE = re.compile(r'\.{3,}|[…⋯]{2,}|\s{3,}\d{1,4}\s*$')

MIN_TEXT_CHARS = 20
MIN_IMAGE_SIDE = 32
OCR_MAX_PAGES = 200
MARKDOWN_CHAPTER_RE = re.compile(r'^#{1,3}\s+(第\s*[0-9一二三四五六七八九十百千零两]+\s*章\s*[^\n]*)$')


def ocr_enabled():
    return os.getenv('PDF_OCR', '1').strip().lower() not in {'0', 'false', 'off', 'no'}


def _ocr_dpi():
    try:
        return max(120, min(300, int(os.getenv('PDF_OCR_DPI', '200'))))
    except ValueError:
        return 200


def ocr_page_texts(doc, page_numbers, log=None):
    """OCR selected pages locally with RapidOCR; return text and honest stats."""
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as error:
        raise Problem(
            '本机未安装 OCR 依赖 rapidocr_onnxruntime，无法识别扫描版 PDF。'
            '请执行 pip install -r requirements.lock.txt 后重试，或改用 MinerU 草稿 Markdown。',
            code='E_OCR_UNAVAILABLE',
        ) from error
    engine = RapidOCR()
    texts = {}
    confidences = []
    dpi = _ocr_dpi()
    with tempfile.TemporaryDirectory(prefix='ct-ocr-') as temp:
        for index, page_no in enumerate(page_numbers, start=1):
            page = doc[page_no - 1]
            pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
            image_path = Path(temp) / f'page-{page_no}.png'
            pix.save(str(image_path))
            del pix
            try:
                result, _ = engine(str(image_path))
            except Exception as error:  # noqa: BLE001
                _log(log, f'OCR 第 {page_no} 页失败：{error}')
                continue
            finally:
                image_path.unlink(missing_ok=True)
            lines = []
            for item in result or []:
                if len(item) < 3:
                    continue
                box, text, score = item[0], str(item[1]).strip(), float(item[2])
                if not text:
                    continue
                top = min(float(point[1]) for point in box)
                left = min(float(point[0]) for point in box)
                right = max(float(point[0]) for point in box)
                lines.append((top, left, text, score, right))
                confidences.append(score)
            texts[page_no] = '\n'.join(_ocr_rows(lines))
            _log(log, f'本机 OCR 第 {index}/{len(page_numbers)} 页：{len(lines)} 行')
    if not texts:
        raise Problem(
            '扫描版 PDF 的本机 OCR 没有产生可用文字。请确认页面清晰度，或改用 MinerU 草稿 Markdown。',
            code='E_OCR_FAILED',
        )
    return texts, {
        'pages': len(texts),
        'dpi': dpi,
        'confidence': round(sum(confidences) / len(confidences), 4) if confidences else None,
        'lines': len(confidences),
    }


def _ocr_rows(lines):
    """Group OCR boxes into visual rows, joining only boxes that continue the text."""
    rows = []
    for top, left, text, height, right in sorted(lines, key=lambda line: (line[0], line[1])):
        if rows and abs(top - rows[-1][0]) <= max(12.0, 0.5 * max(height, rows[-1][1])):
            rows[-1][2].append((left, right, text))
            rows[-1][1] = max(rows[-1][1], height)
        else:
            rows.append([top, height, [(left, right, text)]])
    output = []
    for _, _, cells in rows:
        cells.sort(key=lambda cell: cell[0])
        line, right, width, text = '', None, None, ''
        for left, cell_right, cell_text in cells:
            if line:
                char_width = max(6.0, min(40.0, (width or 20.0) / max(1, len(text))))
                if left - right > 1.6 * char_width:
                    output.append(line)
                    line = ''
                elif line[-1].isascii() and cell_text[0].isascii():
                    line += ' '
            line += cell_text
            right, width, text = cell_right, cell_right - left, cell_text
        if line:
            output.append(line)
    return output


def _safe_name(text, limit=60):
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', '_', str(text).strip()).strip('_')
    return (cleaned[:limit] or 'chapter')


def _log(log, message):
    if log:
        log(message)


def _title_key(text):
    return re.sub(r'\s+', '', str(text or '')).strip()


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
            key = _title_key(line)
            if current is not None and key == current.get('key'):
                continue
            current = {'title': line, 'key': key, 'pages': [], 'firstPage': number, 'firstLine': index}
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


def extract_images(doc, image_dir, skip_pages=None):
    """Export page images as image-0001.jpg … and map page number -> [Path]."""
    image_dir.mkdir(parents=True, exist_ok=True)
    mapping = {}
    counter = 0
    skip_pages = set(skip_pages or ())
    for page_no in range(1, doc.page_count + 1):
        if page_no in skip_pages:
            continue
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
    key = chapter.get('key') or _title_key(chapter.get('title'))
    for page_no, text, start in chapter['pages']:
        lines = text.splitlines()[start:]
        while lines and (not lines[0].strip() or _title_key(lines[0]) == key):
            lines.pop(0)
        lines = format_headings(lines)
        refs = [f'![{path.stem}]({image_dir_name}/{path.name})' for path in images.get(page_no, [])]
        if refs:
            caption_indexes = [i for i, line in enumerate(lines) if CAPTION_RE.match(line.strip())]
            for offset, (ref, index) in enumerate(zip(refs, caption_indexes)):
                lines.insert(index + 1 + offset, ref)
            lines.extend(refs[len(caption_indexes):])
        parts.append('\n'.join(lines))
    body = '\n\n'.join(part for part in parts if part.strip())
    return f"# {chapter['title']}\n\n{body}\n"


def split_markdown_chapters(text, default_title='教材'):
    """Split MinerU Markdown on 第N章 headings, dropping front matter."""
    lines = str(text or '').replace('\r\n', '\n').split('\n')
    hits = []
    for index, line in enumerate(lines):
        match = MARKDOWN_CHAPTER_RE.match(line.strip())
        if match:
            hits.append((index, match.group(1).strip()))
    if not hits:
        title = str(default_title or '').strip() or '教材'
        body = '\n'.join(lines).strip()
        if not body.startswith('#'):
            body = f'# {title}\n\n{body}'
        return [{'title': title, 'markdown': body + '\n'}]
    chapters = []
    for position, (line_index, title) in enumerate(hits):
        end = hits[position + 1][0] if position + 1 < len(hits) else len(lines)
        body = '\n'.join(lines[line_index:end]).strip()
        body = re.sub(r'^#{1,3}\s+', '# ', body, count=1)
        chapters.append({'title': title, 'markdown': body + '\n'})
    return chapters


def _write_mineru_chapters(chapters, image_paths, out_dir, log):
    results = []
    used_titles = set()
    for index, chapter in enumerate(chapters, start=1):
        markdown = chapter['markdown']
        base = _safe_name(chapter['title'])
        name = base
        suffix = 2
        while name in used_titles:
            name = f'{base}_{suffix}'
            suffix += 1
        used_titles.add(name)
        md_path = Path(out_dir) / f'{name}.md'
        md_path.write_text(markdown, encoding='utf-8', newline='\n')
        referenced = {Path(ref).name for ref in re.findall(r'!\[[^\]]*\]\(images/([^)\s]+)\)', markdown)}
        images = [str(path) for path in image_paths if Path(path).name in referenced]
        results.append({
            'index': index,
            'title': chapter['title'],
            'md': str(md_path),
            'mdName': md_path.name,
            'chars': len(markdown),
            'pages': [],
            'images': images,
            'imageCount': len(images),
        })
        _log(log, f'已生成 {md_path.name}（{len(images)} 张图 · MinerU）')
    return results


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
        need_ocr = [number for number, text in enumerate(texts, start=1) if len(text.strip()) < MIN_TEXT_CHARS]
        engine_mode = mineru.engine()
        scanned = total < MIN_TEXT_CHARS or (need_ocr and len(need_ocr) >= max(1, doc.page_count // 2))
        if need_ocr and scanned and engine_mode in ('auto', 'mineru'):
            if not mineru.available():
                if engine_mode == 'mineru':
                    raise Problem(
                        '未找到 MinerU 命令行（mineru-kit / mineru / magic-pdf）。'
                        '请安装 MinerU，或配置 MINERU_COMMAND，或改用 PDF_OCR_ENGINE=rapidocr。',
                        code='E_MINERU_UNAVAILABLE',
                    )
            else:
                try:
                    parsed = mineru.parse(pdf_path, out_dir, log)
                except mineru.MineruError as error:
                    if engine_mode == 'mineru':
                        raise Problem(f'MinerU 解析失败：{error}', code='E_MINERU_FAILED') from error
                    _log(log, f'MinerU 解析失败，回退本机 OCR：{error}')
                else:
                    mineru_chapters = split_markdown_chapters(parsed['markdown'], pdf_path.stem)
                    mineru_results = _write_mineru_chapters(mineru_chapters, parsed['images'], out_dir, log)
                    if not mineru_results:
                        raise Problem('MinerU 没有生成章节内容', code='E_MINERU_FAILED')
                    return {
                        'pdf': str(pdf_path),
                        'pdfName': pdf_path.name,
                        'pages': doc.page_count,
                        'images': len(parsed['images']),
                        'ocr': True,
                        'ocrPages': doc.page_count,
                        'ocrConfidence': None,
                        'ocrDpi': None,
                        'engine': 'mineru',
                        'mineruTier': parsed['tier'],
                        'mineruSeconds': parsed['elapsed'],
                        'chapters': mineru_results,
                    }
        ocr_stats = None
        if need_ocr and ocr_enabled():
            if len(need_ocr) > OCR_MAX_PAGES:
                raise Problem(
                    f'检测到 {len(need_ocr)} 页需要 OCR，超过单次上限 {OCR_MAX_PAGES} 页；请按章节拆分后再上传。',
                    code='E_OCR_TOO_LARGE',
                )
            _log(log, f'检测到 {len(need_ocr)}/{doc.page_count} 页无可用文字，启动本机 OCR（RapidOCR）…')
            ocr_texts, ocr_stats = ocr_page_texts(doc, need_ocr, log)
            for page_no, text in ocr_texts.items():
                texts[page_no - 1] = text
            total = sum(len(text.strip()) for text in texts)
            if total < MIN_TEXT_CHARS:
                raise Problem(
                    '扫描版 PDF 的本机 OCR 没有识别出可用文字。请确认扫描清晰度，或改用 MinerU 草稿 Markdown。',
                    code='E_OCR_FAILED',
                )
        elif total < MIN_TEXT_CHARS:
            reason = '本机 OCR 已被 PDF_OCR=0 关闭' if not ocr_enabled() else '本机 OCR 不可用'
            raise Problem(
                f'扫描版 PDF 没有可提取文字（{doc.page_count} 页中 {len(need_ocr)} 页为空）。'
                f'{reason}，不会生成空 Markdown 冒充成功。'
                '请启用本机 OCR（PDF_OCR=1），或先用 MinerU 生成草稿 Markdown 后在“教材 MD 转教案 Word”页上传；'
                '也可以上传文字版 PDF。',
                code='E_OCR_REQUIRED',
            )
        elif need_ocr:
            _log(log, f'注意：{len(need_ocr)} 页没有文字层，且 OCR 未启用，这些页不会出现在 Markdown 中。')
        empty_pages = sum(1 for text in texts if len(text.strip()) < MIN_TEXT_CHARS)
        print(f'[pdf2md] {pdf_path.name}: {doc.page_count} 页，{total} 字符，{empty_pages} 页近空，'
              f'OCR={bool(ocr_stats)}', flush=True)
        images = extract_images(doc, out_dir / 'images', skip_pages=set(need_ocr) if ocr_stats else set())
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
            md_path.write_text(markdown, encoding='utf-8', newline='\n')
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
        'ocr': bool(ocr_stats),
        'ocrPages': ocr_stats['pages'] if ocr_stats else 0,
        'ocrConfidence': ocr_stats['confidence'] if ocr_stats else None,
        'ocrDpi': ocr_stats['dpi'] if ocr_stats else None,
        'engine': 'rapidocr' if ocr_stats else 'pymupdf',
        'chapters': results,
    }


def run_job(job_dir, log=None):
    job_dir = Path(job_dir)
    out_dir = job_dir / 'out'
    result = extract_pdf(job_dir / 'source.pdf', out_dir, log=log)
    (job_dir / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    # Keep the raw per-page text for diagnosis when a page is later reported as empty.
    return result