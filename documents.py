"""Document intake helpers shared by the workbench and the pipeline stages.

Moved verbatim from app.py so the PDF/lesson stages can register their outputs
into the same file/knowledge store without importing the web entrypoint.
"""
import hashlib
import io
from pathlib import Path
import re
import zipfile
import xml.etree.ElementTree as ET

from webapp.runtime_lock import powerpoint_exclusive

ROOT = Path(__file__).resolve().parent


class Problem(Exception):
    def __init__(self, message, status=400, code='E_SCHEMA'):
        self.message, self.status, self.code = message, status, code


def checked_zip(path):
    archive = zipfile.ZipFile(path)
    if len(archive.infolist()) > 20000 or sum(x.file_size for x in archive.infolist()) > 300 * 1024**2:
        archive.close()
        raise Problem('文件解压后过大')
    if any(x.filename.startswith(('/', '\\')) or '..' in Path(x.filename).parts for x in archive.infolist()):
        archive.close()
        raise Problem('文档包含不安全路径')
    return archive


def decode(data):
    for encoding in ('utf-8-sig', 'gb18030'):
        try:
            text = data.decode(encoding)
            if '\x00' in text:
                raise Problem('文件不是文本资料')
            return text
        except UnicodeDecodeError:
            pass
    raise Problem('文本编码无法识别')


def parse_document(path, directory):
    suffix = path.suffix.lower()
    assets = []
    def image(data, name):
        from PIL import Image
        try:
            with Image.open(io.BytesIO(data)) as im:
                im.verify()
        except Exception:
            return
        target = directory / ('image-' + str(len(assets) + 1) + Path(name).suffix.lower())
        target.write_bytes(data)
        assets.append({'path': str(target), 'name': name, 'sha256': hashlib.sha256(data).hexdigest()})
    if suffix in ('.md', '.txt'):
        text = decode(path.read_bytes())
    elif suffix == '.docx':
        with checked_zip(path) as z:
            doc = ET.fromstring(z.read('word/document.xml'))
            ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
            text = '\n'.join(''.join(p.itertext()) for p in doc.iter(ns + 'p'))
            for name in z.namelist():
                if name.startswith('word/media/'):
                    image(z.read(name), Path(name).name)
    elif suffix == '.pdf':
        from pypdf import PdfReader
        pdf = PdfReader(path)
        if pdf.is_encrypted:
            raise Problem('请先解密PDF文件', code='E_DOCUMENT')
        if len(pdf.pages) > 800:
            raise Problem('PDF超过800页')
        text = '\n'.join(page.extract_text() or '' for page in pdf.pages)
        if len(text.strip()) < 20:
            raise Problem('扫描PDF没有可提取文字；当前未配置OCR，请上传文字版PDF或DOCX', code='E_OCR_REQUIRED')
        for page in pdf.pages:
            for img in page.images:
                image(img.data, img.name)
    elif suffix == '.doc':
        if path.read_bytes()[:8] != bytes.fromhex('D0CF11E0A1B11AE1'):
            raise Problem('DOC文件签名不正确')
        # Word converts legacy binary documents, with macros disabled and no UI.
        import subprocess
        script = ROOT / 'scripts/parse_doc.ps1'
        output = directory / 'converted.docx'
        with powerpoint_exclusive():
            result = subprocess.run(['powershell', '-NoProfile', '-File', str(script), str(path), str(output)],
                capture_output=True, timeout=90, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode or not output.exists():
            raise Problem('DOC解析失败，请确认桌面Word可用，或另存为DOCX上传', code='E_DOCUMENT')
        return parse_document(output, directory)
    else:
        raise Problem('仅支持DOC、DOCX、PDF、MD、TXT')
    if not text.strip():
        raise Problem('文档没有可用文字')
    if len(text) > 1500000:
        raise Problem('文档文字超过150万字符，请分章上传')
    return text, assets