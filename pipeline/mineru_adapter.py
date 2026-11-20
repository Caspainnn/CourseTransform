"""Optional MinerU bridge for scanned PDFs.

MinerU is an external document-parsing toolkit (OpenDataLab) that reconstructs
layout, figures, tables and formulas from scanned pages. It is not vendored in
this repository: when a compatible CLI is installed (``mineru-kit`` >= 4,
``mineru`` 2.x/3.x or ``magic-pdf``), the PDF stage prefers it for image-heavy
scans and falls back to the built-in RapidOCR path when it is missing or fails.

Configure with:
    PDF_OCR_ENGINE=auto|mineru|rapidocr   (default auto)
    MINERU_COMMAND=<executable + args>    (optional override)
    MINERU_TIER=flash|basic|standard|advanced (mineru-kit only, default standard)
    MINERU_TIMEOUT=1800                   (seconds)
"""
from __future__ import annotations

import base64
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

REF_RE = re.compile(r'!\[([^\]]*)\]\(([^)\s]+)\)')
SAFE_NAME = re.compile(r'[^\w.\-]+')
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tif', '.tiff'}


class MineruError(RuntimeError):
    """MinerU is installed but the parse run failed."""


def command():
    raw = os.getenv('MINERU_COMMAND', '').strip()
    if raw:
        quote_chars = '"' + "'"
        parts = [part.strip(quote_chars) for part in shlex.split(raw, posix=False) if part.strip(quote_chars)]
        if parts and (Path(parts[0]).is_file() or shutil.which(parts[0])):
            return parts
        return None
    for name in ('mineru-kit', 'mineru', 'magic-pdf'):
        found = shutil.which(name)
        if found:
            return [found]
    return None


def available():
    return command() is not None


def engine():
    value = (os.getenv('PDF_OCR_ENGINE') or 'auto').strip().lower()
    return value if value in {'auto', 'mineru', 'rapidocr'} else 'auto'


def tier():
    value = (os.getenv('MINERU_TIER') or 'standard').strip().lower()
    return value if value in {'flash', 'basic', 'standard', 'advanced'} else 'standard'


def timeout():
    try:
        return max(60, int(os.getenv('MINERU_TIMEOUT', '1800')))
    except ValueError:
        return 1800


def _safe(name: str) -> str:
    suffix = Path(name).suffix.lower()
    stem = SAFE_NAME.sub('_', Path(name).stem).strip('_') or 'image'
    return (stem[:60] or 'image') + (suffix if suffix in IMAGE_SUFFIXES else '.png')


def _tidy(value: str) -> str:
    return value.strip().strip('"').strip("'").strip()


def _build_args(cli, pdf_path, out_dir):
    name = Path(cli[0]).name.lower()
    if 'mineru-kit' in name:
        # 4.x writes a single Markdown file; figures are embedded as data URIs.
        return [*cli, 'parse', str(pdf_path), '-o', str(Path(out_dir) / 'mineru.md'), '--tier', tier()]
    return [*cli, '-p', str(pdf_path), '-o', str(out_dir)]


def _pick_markdown(root: Path):
    files = [path for path in root.rglob('*.md') if path.is_file() and path.stat().st_size]
    files = [path for path in files if path.name.lower() not in {'readme.md', 'license.md'}]
    if not files:
        return None

    def score(path):
        parent = path.parent
        weight = path.stat().st_size
        if (parent / 'images').is_dir():
            weight += 10**6
        if any(parent.glob('*.json')):
            weight += 10**5
        if 'auto' in parent.name:
            weight += 10**4
        return weight

    return max(files, key=score)


def _collect_images(markdown_text, md_path: Path, out_dir: Path, root: Path):
    """Copy referenced images into out/images and return (text, copied paths)."""
    image_dir = out_dir / 'images'
    image_dir.mkdir(parents=True, exist_ok=True)
    by_name = {}
    for path in root.rglob('*'):
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
            by_name.setdefault(path.name, path)
    copied = {}
    missing = []

    def replace(match):
        alt, src = match.group(1), _tidy(match.group(2))
        if src.startswith('data:image/'):
            header, _, payload = src.partition(',')
            mime = header[5:].split(';')[0].lower()
            suffix = {'image/jpeg': '.jpg', 'image/jpg': '.jpg', 'image/png': '.png',
                      'image/webp': '.webp', 'image/gif': '.gif', 'image/bmp': '.bmp'}.get(mime, '.png')
            try:
                data = base64.b64decode(payload, validate=False)
            except (ValueError, TypeError):
                missing.append('inline image')
                return alt
            name = f'inline-{len(copied) + 1:04d}{suffix}'
            target = image_dir / name
            target.write_bytes(data)
            copied[name] = target
            return f'![{alt}](images/{name})'
        source = (md_path.parent / src).resolve()
        if not source.is_file():
            source = by_name.get(Path(src).name)
        if not source or not Path(source).is_file():
            missing.append(src)
            return alt
        target_name = _safe(Path(src).name)
        target = image_dir / target_name
        counter = 2
        while target.exists() and target.read_bytes() != Path(source).read_bytes():
            target = image_dir / f'{Path(target_name).stem}-{counter}{Path(target_name).suffix}'
            counter += 1
        if not target.exists():
            target.write_bytes(Path(source).read_bytes())
        copied[target.name] = target
        return f'![{alt}](images/{target.name})'

    text = REF_RE.sub(replace, markdown_text)
    return text, sorted(copied.values()), missing


def parse(pdf_path, out_dir, log=None):
    """Run MinerU and normalise its Markdown + figures into the stage layout."""
    cli = command()
    if not cli:
        raise MineruError('未找到 MinerU 命令行（mineru-kit / mineru / magic-pdf）')
    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    note = (lambda msg: log(str(msg))) if log else (lambda msg: None)
    with tempfile.TemporaryDirectory(prefix='ct-mineru-') as temp:
        root = Path(temp)
        args = _build_args(cli, pdf_path, root)
        note(f'启动 MinerU（{Path(cli[0]).name} · {tier()}）…')
        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=timeout(),
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise MineruError(f'MinerU 超时（>{timeout()} 秒）') from error
        except OSError as error:
            raise MineruError(f'MinerU 无法启动：{error}') from error
        if completed.returncode != 0:
            tail = (completed.stderr or completed.stdout or '').strip().splitlines()[-3:]
            raise MineruError(f'MinerU 退出码 {completed.returncode}：' + ' / '.join(tail))
        md_path = _pick_markdown(root)
        if not md_path:
            raise MineruError('MinerU 没有产出 Markdown 文件')
        markdown = md_path.read_text(encoding='utf-8', errors='replace')
        markdown, images, missing = _collect_images(markdown, md_path, out_dir, root)
        if missing:
            note(f'MinerU 引用了 {len(missing)} 张缺失图片，已在正文中降级为题注')
        elapsed = round(time.time() - started, 1)
        note(f'MinerU 完成：{len(markdown)} 字符，{len(images)} 张图，{elapsed} 秒')
    return {
        'markdown': markdown,
        'images': images,
        'tier': tier(),
        'elapsed': elapsed,
        'command': str(cli[0]),
        'missingImages': len(missing),
    }