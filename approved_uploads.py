"""Upload pre-flight for the studio: template, approved Markdown outline, images.

An approved outline is plain Markdown whose image links come from the upstream
conversion step, e.g. ``[教材原图](../../textbook-repeat/textbook_files/image_5.jpg)``.
Uploaded browsers lose that folder layout, so this module matches every link
against the files the user actually uploaded and writes a *path-only* normalised
copy of the outline next to them. Teaching text, facts and notes are never edited.
"""
from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path

from PIL import Image

ASSET_LINK = re.compile(r"\[教材原图\]\(([^)\n]+)\)")
PAGE_HEADING = re.compile(r"(?m)^###\s+(P\d+)\s")
REQUIRED_SECTIONS = ("**上屏内容**", "**页面关系**")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"}
REMOTE_LINK = re.compile(r"^(?:[a-z]+:)?//", re.IGNORECASE)


class PreflightError(Exception):
    """A blocking upload problem with an explicit code for the UI."""

    def __init__(self, code: str, message: str, details: list | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or []


def _decode(data: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise PreflightError("E_OUTLINE", "大纲文件不是 UTF-8 或 GB18030 编码的文本")


def _safe_relative(name: str) -> Path | None:
    raw = (name or "").replace("\\", "/").strip()
    if not raw or REMOTE_LINK.match(raw) or re.match(r"^[A-Za-z]:", raw) or raw.startswith("/"):
        return None
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return None
    return Path(*parts)


def validate_template(path: Path) -> None:
    if path.suffix.lower() != ".pptx":
        raise PreflightError("E_TEMPLATE", "模板必须是 .pptx（.ppt 请先在 PowerPoint 里另存为 .pptx）")
    if not zipfile.is_zipfile(path):
        raise PreflightError("E_TEMPLATE", "模板不是有效的 pptx 压缩包（可能已损坏或被加密）")
    try:
        with zipfile.ZipFile(path) as package:
            names = set(package.namelist())
        if "ppt/presentation.xml" not in names or "[Content_Types].xml" not in names:
            raise PreflightError("E_TEMPLATE", "模板缺少 PowerPoint 核心结构")
        from pptx import Presentation

        if len(Presentation(str(path)).slides) == 0:
            raise PreflightError("E_TEMPLATE", "模板里没有任何幻灯片，无法作为来源页库")
    except PreflightError:
        raise
    except Exception as error:  # noqa: BLE001
        raise PreflightError("E_TEMPLATE", f"python-pptx 打不开该模板：{error}")


def _save_assets(source_root: Path, files) -> tuple[dict[str, Path], list[str]]:
    saved: dict[str, Path] = {}
    warnings: list[str] = []
    skipped: list[str] = []
    for storage in files:
        relative = _safe_relative(storage.filename or "")
        if relative is None:
            skipped.append(storage.filename or "?")
            continue
        target = source_root / relative
        key = relative.as_posix()
        if key in saved:
            warnings.append(f"同名素材重复上传，已忽略后一个：{key}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        storage.save(str(target))
        if target.stat().st_size == 0:
            target.unlink(missing_ok=True)
            skipped.append(key)
            continue
        saved[key] = target
    if skipped:
        warnings.append("以下文件因路径非法或为空被跳过：" + "、".join(skipped))
    return saved, warnings


def _image_basename(link: str) -> str:
    return re.sub(r"[?#].*$", "", link.replace("\\", "/").rstrip("/")).rsplit("/", 1)[-1]


def _resolve_link(link: str, saved: dict[str, Path]) -> tuple[str | None, str | None, list[str]]:
    """Match one Markdown image link to an uploaded file.

    Returns ``(relative_path, error_code, candidates)``. ``relative_path`` is the
    uploaded location that the normalised outline will point at.
    """
    raw = link.strip().replace("\\", "/")
    if REMOTE_LINK.match(raw):
        return None, "E_ASSET_REMOTE", []
    cleaned = re.sub(r"^\./+", "", re.sub(r"[?#].*$", "", raw))
    if cleaned in saved:
        return cleaned, None, []
    lowered = cleaned.lower()
    for key in saved:
        if key.lower() == lowered:
            return key, None, []
    suffix_match = [key for key in saved if key.lower().endswith("/" + lowered)]
    if len(suffix_match) == 1:
        return suffix_match[0], None, []
    if len(suffix_match) > 1:
        return None, "E_ASSET_AMBIGUOUS", suffix_match
    basename = _image_basename(raw).lower()
    same_name = [key for key in saved if key.rsplit("/", 1)[-1].lower() == basename]
    if len(same_name) == 1:
        return same_name[0], None, []
    if len(same_name) > 1:
        return None, "E_ASSET_AMBIGUOUS", same_name
    return None, "E_ASSET_MISSING", []


def _asset_inventory(assets: list[dict]) -> list[dict]:
    for item in assets:
        try:
            with Image.open(item["path"]) as image:
                item["width"], item["height"] = image.size
        except Exception as error:  # noqa: BLE001
            raise PreflightError(
                "E_ASSET_INVALID", f"素材无法作为图片读取：{item['relative']}（{error}）",
                details=[{"code": "E_ASSET_INVALID", "path": item["relative"], "detail": str(error)}])
        item["sha256"] = hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest()
        item["bytes"] = Path(item["path"]).stat().st_size
    return assets


def _validate_outline(text: str) -> int:
    headings = PAGE_HEADING.findall(text)
    if not headings:
        raise PreflightError(
            "E_OUTLINE", "大纲里没有找到逐页标题（格式：### P01　标题），请确认这是已确认的逐页大纲")
    if len(headings) != len(set(headings)):
        raise PreflightError("E_OUTLINE", "大纲里存在重复页码标题：" + "、".join(sorted(
            {h for h in headings if headings.count(h) > 1})))
    sections = re.split(r"(?m)^###\s+(P\d+)\s+", text)
    problems = []
    for index in range(1, len(sections), 2):
        body = sections[index + 1]
        for marker in REQUIRED_SECTIONS:
            if marker not in body:
                problems.append(f"{sections[index]} 缺少「{marker.strip('*')}」段")
    if problems:
        raise PreflightError("E_OUTLINE", "大纲结构不完整：" + "；".join(problems[:6]),
                             details=[{"code": "E_OUTLINE", "detail": item} for item in problems])
    return len(headings)


def prepare_upload(job_dir: Path, template_file, outline_file, asset_files) -> dict:
    """Store the upload, resolve every image link, return a pre-flight report."""
    source_root = job_dir / "input" / "source"
    source_root.mkdir(parents=True, exist_ok=True)

    template_path = source_root / "template.pptx"
    template_file.save(str(template_path))
    if template_path.stat().st_size == 0:
        raise PreflightError("E_TEMPLATE", "模板文件为空")
    validate_template(template_path)

    raw_bytes = outline_file.read()
    raw_text, encoding = _decode(raw_bytes)
    original_digest = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
    page_count = _validate_outline(raw_text)
    # Keep the uploaded outline byte-for-byte so the job's recorded hash and the
    # accepted baseline stay comparable; only the normalised copy is rewritten.
    (source_root / "outline.md").write_bytes(raw_bytes)

    saved, warnings = _save_assets(source_root, asset_files)

    assets: list[dict] = []
    used: set[str] = set()
    missing: list[dict] = []
    rewrites: list[dict] = []

    def replace(match: re.Match) -> str:
        link = match.group(1).strip()
        resolved, code, candidates = _resolve_link(link, saved)
        if resolved is None:
            detail = {
                "E_ASSET_MISSING": "上传的文件里没有匹配的图片",
                "E_ASSET_AMBIGUOUS": "上传了多个同名图片，无法判断用哪一个：" + "、".join(candidates[:6]),
                "E_ASSET_REMOTE": "大纲引用的是网络图片，制作链只能使用本地上传的原图",
            }.get(code or "", "无法解析该图片引用")
            missing.append({"code": code or "E_ASSET_MISSING", "path": link,
                            "detail": detail, "candidates": candidates[:6]})
            return match.group(0)
        used.add(resolved)
        rewrites.append({"original": link, "resolved": resolved})
        return f"[教材原图]({resolved})"

    normalised = ASSET_LINK.sub(replace, raw_text)

    ambiguous = [item for item in missing if item["code"] == "E_ASSET_AMBIGUOUS"]
    remote = [item for item in missing if item["code"] == "E_ASSET_REMOTE"]
    absent = [item for item in missing if item["code"] == "E_ASSET_MISSING"]
    if ambiguous:
        raise PreflightError(
            "E_ASSET_AMBIGUOUS",
            f"有 {len(ambiguous)} 处图片链接对应多个同名上传文件，请只保留需要的那个：" +
            "、".join(f"{item['path']} → {', '.join(item['candidates'])}" for item in ambiguous[:3]),
            details=ambiguous,
        )
    if remote:
        raise PreflightError(
            "E_ASSET_REMOTE",
            f"大纲里有 {len(remote)} 处网络图片链接，请把图片文件一起上传后再提交：" +
            "、".join(item["path"] for item in remote[:5]),
            details=remote,
        )
    if absent:
        names = "、".join(sorted(saved)[:12]) or "（本次没有上传任何素材）"
        raise PreflightError(
            "E_ASSET_MISSING",
            f"大纲引用了 {len(absent)} 张图片，但没有对应的上传文件：" +
            "、".join(item["path"] for item in absent[:5]) + f"。已上传：{names}",
            details=absent,
        )

    for relative in sorted(used):
        path = source_root / relative
        assets.append({"relative": relative, "path": str(path.resolve())})
    _asset_inventory(assets)

    unused = sorted(set(saved) - used)
    if unused:
        warnings.append("以下上传图片未被大纲引用：" + "、".join(unused[:8]))
    if encoding not in ("utf-8", "utf-8-sig"):
        warnings.append(f"大纲原编码为 {encoding}，已统一保存为 UTF-8")

    normalised_path = source_root / "outline-normalised.md"
    normalised_path.write_text(normalised, encoding="utf-8")

    return {
        "job_dir": str(job_dir),
        "template_path": str(template_path.resolve()),
        "outline_path": str(normalised_path.resolve()),
        "source_outline_path": str((source_root / "outline.md").resolve()),
        "original_outline_sha256": original_digest,
        "normalised_outline_sha256": hashlib.sha256(normalised.encode("utf-8")).hexdigest(),
        "encoding": encoding,
        "page_count": page_count,
        "asset_count": len(assets),
        "assets": assets,
        "warnings": warnings,
        "rewrites": rewrites,
    }


__all__ = ["PreflightError", "prepare_upload", "validate_template"]