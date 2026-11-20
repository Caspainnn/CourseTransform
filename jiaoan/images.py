from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .image_filter import classify_image, image_digest, is_duplicate
from .textbook import Chapter
from .utils import safe_name, sha256_file, write_text

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+[\"'][^\"']*[\"'])?\)")
HTML_IMAGE_RE = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"'][^>]*>", re.IGNORECASE)
CHAPTER_HINT_RE = re.compile(r"(?:ch(?:apter)?[-_]?|第)([0-9一二三四五六七八九十]+)(?:章)?", re.IGNORECASE)
SECTION_NUM_RE = re.compile(r"(?<!\d)(\d{1,2}\s*[.．\-_]\s*\d{1,2})(?!\d)")
_OCR_ENGINE: Any = None


@dataclass
class ImageAsset:
    id: str
    filename: str
    path: str
    width: int = 0
    height: int = 0
    alt: str = ""
    hint: str = ""
    ocr_text: str = ""
    source_line: int | None = None
    section: str = ""
    output_path: str = ""
    category: str = ""
    keep: bool = True
    filter_reason: str = ""
    filter_confidence: float = 0.0
    filter_metrics: dict[str, Any] = field(default_factory=dict)
    duplicate_of: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "filename": self.filename,
            "path": self.output_path or self.path,
            "width": self.width,
            "height": self.height,
            "alt": self.alt,
            "section": self.section,
            "source_line": self.source_line,
            "ocr_text": self.ocr_text[:600],
            "category": self.category,
            "keep": self.keep,
            "filter_reason": self.filter_reason,
            "filter_confidence": round(self.filter_confidence, 2),
            "filter_metrics": self.filter_metrics,
            "duplicate_of": self.duplicate_of,
        }


def _norm_num(text: str) -> str:
    return re.sub(r"\s+", "", text).replace("．", ".").replace("_", ".").replace("-", ".")


def _ref_map(chapter: Chapter) -> dict[str, dict[str, Any]]:
    refs: dict[str, dict[str, Any]] = {}
    for idx, line in enumerate(chapter.content.splitlines(), start=chapter.start_line):
        section = next((s.title for s in chapter.sections if s.start_line <= idx <= s.end_line), "")
        for m in MD_IMAGE_RE.finditer(line):
            alt, src = m.group(1).strip(), m.group(2).strip()
            name = Path(src.split("?")[0].split("#")[0]).name
            if name:
                refs.setdefault(name.lower(), {"alt": alt, "src": src, "line": idx, "section": section})
        for m in HTML_IMAGE_RE.finditer(line):
            src = m.group(1).strip()
            name = Path(src.split("?")[0].split("#")[0]).name
            if name:
                refs.setdefault(name.lower(), {"alt": "", "src": src, "line": idx, "section": section})
    return refs


def _infer_section(text: str, chapter: Chapter) -> str:
    raw = str(text or "")
    nums = _norm_num(raw)
    for section in chapter.sections:
        m = SECTION_NUM_RE.search(_norm_num(section.title))
        if m and m.group(1) in nums:
            return section.title
    for section in chapter.sections:
        clean = re.sub(r"^第?[\d.一二三四五六七八九十]+\s*[章节]?\s*", "", section.title).strip()
        if clean and clean in raw:
            return section.title
    return ""


def _chapter_matches(text: str, chapter: Chapter) -> bool:
    raw = str(text or "")
    m = CHAPTER_HINT_RE.search(raw)
    if not m:
        return True
    token = m.group(1)
    # 数字章号
    if token.isdigit():
        return chapter.id.lower().endswith(token.zfill(2)) or f"第{token}章" in chapter.title or chapter.id.lower() == f"ch{int(token):02d}"
    return f"第{token}章" in chapter.title or token in chapter.title


def _ocr(path: str) -> str:
    if os.getenv("JIAOAN_OCR", "1").strip() in {"0", "false", "off"}:
        return ""
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    cache = Path(".jiaoan_cache") / "ocr" / f"{digest}.json"
    if cache.exists():
        try:
            return str(json.loads(cache.read_text(encoding="utf-8")).get("text") or "")
        except Exception:
            pass
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore

        global _OCR_ENGINE
        if _OCR_ENGINE is None:
            _OCR_ENGINE = RapidOCR()
        result, _ = _OCR_ENGINE(str(path))
        lines = [str(item[1]).strip() for item in (result or []) if len(item) > 1 and str(item[1]).strip()]
        text = "\n".join(lines)[:1200]
    except Exception:
        text = ""
    try:
        write_text(cache, json.dumps({"text": text}, ensure_ascii=False, indent=2))
    except Exception:
        pass
    return text


def discover_images(
    chapter: Chapter,
    image_dir: str | Path | None,
    hints: dict[str, str] | None = None,
    include_unassigned: bool = True,
    filter_images: bool = True,
    keep_all: bool = False,
) -> list[ImageAsset]:
    """扫描上传图片，和教材 md 中的图片引用/文件名/OCR 做关联，并做教学可用性筛选。"""
    if not image_dir:
        return []
    root = Path(image_dir)
    if not root.exists():
        return []
    hints = hints or {}
    refs = _ref_map(chapter)
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTS)
    assets: list[ImageAsset] = []
    seen_names: set[str] = set()

    for idx, file in enumerate(files, start=1):
        name = file.name
        key = name.lower()
        ref = refs.get(key) or refs.get(file.stem.lower())
        hint = str(hints.get(name) or hints.get(file.stem) or "").strip()
        alt = str((ref or {}).get("alt") or "").strip()
        section = str((ref or {}).get("section") or "") or _infer_section(f"{hint} {alt} {name}", chapter)
        if not _chapter_matches(f"{name} {hint} {alt}", chapter):
            continue
        if not ref and not section and not include_unassigned:
            continue
        if not ref and section and not any(_norm_num(section) == _norm_num(s.title) for s in chapter.sections):
            continue
        try:
            with Image.open(file) as im:
                width, height = im.size
        except Exception:
            width, height = 0, 0
        base = safe_name(file.stem, 40)
        filename = name
        if filename in seen_names:
            filename = f"{base}_{idx}{file.suffix.lower()}"
        seen_names.add(filename)
        # 先做一次不依赖 OCR 的快速判别：代码截图/二维码/纯黑图直接跳过 OCR，
        # 避免上传 90 张时把时间全花在识别源代码小字上。
        quick = classify_image(file, ocr_text="", hint=hint, alt=alt)
        ocr_text = ""
        decision = quick
        need_ocr = quick.category not in {"code_screenshot", "qr_code", "dark_noise", "too_small"}
        if need_ocr and quick.category == "photo":
            # 结果拼图这类“照片外观 + 白底文字”的图，仍需要 OCR 才能认出来。
            metrics = quick.metrics or {}
            need_ocr = float(metrics.get("white_ratio") or 0) >= 0.3 and float(metrics.get("dark_ratio") or 0) >= 0.2
        if need_ocr:
            ocr_text = _ocr(str(file))
            decision = classify_image(file, ocr_text=ocr_text, hint=hint, alt=alt)
        asset = ImageAsset(
            id=f"fig{len(assets) + 1:02d}",
            filename=filename,
            path=str(file),
            width=width,
            height=height,
            alt=alt,
            hint=hint,
            ocr_text=ocr_text,
            source_line=(ref or {}).get("line"),
            section=section,
        )
        decision = classify_image(file, ocr_text=ocr_text, hint=hint, alt=alt)
        asset.category = decision.category
        asset.keep = True if keep_all or not filter_images else decision.keep
        asset.filter_reason = "未启用筛选" if not filter_images else decision.reason
        asset.filter_confidence = decision.confidence
        asset.filter_metrics = decision.metrics
        assets.append(asset)

    # 近重复图：保留分辨率更高/先出现的一张，其余标记为重复。
    if filter_images and not keep_all:
        kept: list[tuple[str, ImageAsset]] = []
        for asset in assets:
            if not asset.keep:
                continue
            try:
                digest = image_digest(asset.path)
            except Exception:
                continue
            for previous_digest, previous in kept:
                if is_duplicate(digest, previous_digest):
                    asset.keep = False
                    asset.category = "duplicate"
                    asset.filter_reason = f"与 {previous.filename} 近似重复"
                    asset.filter_confidence = 0.9
                    asset.duplicate_of = previous.filename
                    break
            else:
                kept.append((digest, asset))
    return assets


def _font(size: int):
    for name in ("msyh.ttc", "simhei.ttf", "simsun.ttc"):
        path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size)
            except Exception:
                continue
    return ImageFont.load_default()


def _wrap(text: str, font, width: int, limit: int = 3) -> list[str]:
    lines, current = [], ""
    for char in str(text or ""):
        if font.getlength(current + char) > width and current:
            lines.append(current)
            current = char
            if len(lines) >= limit:
                break
        else:
            current += char
    if current and len(lines) < limit:
        lines.append(current)
    return lines or [""]


def generate_fallback_diagrams(out_dir: str | Path, plan: dict[str, Any], chapter: Chapter, count: int = 2) -> list[ImageAsset]:
    """Create two simple teaching diagrams when the textbook has no usable figures."""
    out = Path(out_dir) / "images"
    out.mkdir(parents=True, exist_ok=True)
    lessons = [x for x in (plan.get("lessons") or []) if isinstance(x, dict)]
    phases = [str(p.get("name") or "").strip() for p in ((lessons[0].get("phases") if lessons else []) or []) if str(p.get("name") or "").strip()]
    phases = (phases or ["情境导入", "新知探究", "实践操作", "总结评价"])[:6]
    objectives = [str(o.get("statement") or "").strip() for o in (plan.get("objectives") or []) if str(o.get("statement") or "").strip()]
    objectives = (objectives or [str(x) for x in (plan.get("key_points") or []) if str(x).strip()])[:4]
    assets: list[ImageAsset] = []
    specs = [
        ("gen01", "本课教学流程", phases, "flow"),
        ("gen02", "本课知识结构", objectives, "structure"),
    ]
    for idx, (ident, title, items, kind) in enumerate(specs[:count], start=1):
        canvas = Image.new("RGB", (1200, 600), "white")
        draw = ImageDraw.Draw(canvas)
        title_font, body_font = _font(38), _font(26)
        draw.rounded_rectangle((20, 20, 1180, 580), radius=16, outline="#9fb3c8", width=3)
        draw.text((60, 48), title, font=title_font, fill="#1f4e79")
        if kind == "flow":
            box_w, gap = 190, 28
            start_x, top, height = 60, 190, 250
            for i, name in enumerate(items):
                x = start_x + i * (box_w + gap)
                draw.rounded_rectangle((x, top, x + box_w, top + height), radius=12, outline="#4f81bd", width=3, fill="#eef4fb")
                lines = _wrap(name, body_font, box_w - 30, 4)
                y = top + 24
                for line in lines:
                    draw.text((x + 15, y), line, font=body_font, fill="#1f2d3d")
                    y += 36
                if i < len(items) - 1:
                    ax = x + box_w + 4
                    draw.line((ax, top + height // 2, ax + gap - 8, top + height // 2), fill="#4f81bd", width=3)
                    draw.polygon([(ax + gap - 8, top + height // 2), (ax + gap - 20, top + height // 2 - 7), (ax + gap - 20, top + height // 2 + 7)], fill="#4f81bd")
            note = "依据本章教学环节自动生成，可在 Word 中替换为真实课堂照片或教材图。"
        else:
            draw.rounded_rectangle((60, 160, 1140, 520), radius=12, outline="#4f81bd", width=3, fill="#fbfdff")
            y = 190
            for item in items:
                draw.ellipse((90, y + 10, 104, y + 24), fill="#4f81bd")
                for line in _wrap(item, body_font, 940, 2):
                    draw.text((125, y), line, font=body_font, fill="#1f2d3d")
                    y += 34
                y += 18
            note = "依据教学目标与知识要点自动生成，可在 Word 中替换为教材结构图。"
        draw.text((60, 520), note, font=_font(20), fill="#6b7a8a")
        filename = f"{ident}_{title}.png"
        path = out / filename
        canvas.save(path)
        assets.append(ImageAsset(
            id=ident, filename=filename, path=str(path), width=1200, height=600,
            alt=title, hint=title, category="diagram", keep=True,
            filter_reason="教材无可用配图，已自动生成教学示意图",
            output_path=f"images/{filename}",
        ))
    return assets


def copy_images_to_output(assets: list[ImageAsset], out_dir: str | Path) -> list[ImageAsset]:
    out = Path(out_dir) / "images"
    out.mkdir(parents=True, exist_ok=True)
    for asset in assets:
        suffix = Path(asset.path).suffix.lower()
        target = out / f"{asset.id}_{safe_name(Path(asset.filename).stem, 36)}{suffix}"
        shutil.copy2(asset.path, target)
        asset.output_path = f"images/{target.name}"
    return assets


def image_manifest(assets: list[ImageAsset]) -> dict[str, Any]:
    kept = [a for a in assets if a.keep]
    rejected = [a for a in assets if not a.keep]
    by_category: dict[str, int] = {}
    for a in assets:
        by_category[a.category or "unknown"] = by_category.get(a.category or "unknown", 0) + 1
    return {
        "summary": {
            "total": len(assets),
            "kept": len(kept),
            "rejected": len(rejected),
            "by_category": by_category,
        },
        "images": [a.to_dict() for a in assets],
        "kept": [a.to_dict() for a in kept],
        "rejected": [a.to_dict() for a in rejected],
    }


def image_summary(assets: list[ImageAsset]) -> dict[str, Any]:
    kept = [a for a in assets if a.keep]
    by_category: dict[str, int] = {}
    for a in assets:
        if not a.keep:
            by_category[a.category or "unknown"] = by_category.get(a.category or "unknown", 0) + 1
    return {
        "total": len(assets),
        "kept": len(kept),
        "rejected": len(assets) - len(kept),
        "rejected_by_category": by_category,
    }


def write_image_filter_report(
    out_dir: str | Path,
    assets: list[ImageAsset],
    chapter: Chapter,
    used_assets: list[ImageAsset] | None = None,
) -> Path:
    """教材图“判别器”的说明书：哪些保留、哪些剔除、为什么。"""
    out = Path(out_dir)
    kept = [a for a in assets if a.keep]
    rejected = [a for a in assets if not a.keep]
    categories: dict[str, int] = {}
    for a in rejected:
        categories[a.category or "unknown"] = categories.get(a.category or "unknown", 0) + 1
    category_label = {
        "diagram": "流程图/结构图",
        "chart": "准确率/损失曲线",
        "result_grid": "识别结果拼图",
        "result_screenshot": "结果界面",
        "photo": "应用照片",
        "qr_code": "二维码/扫码链接",
        "code_screenshot": "源代码/终端截图",
        "duplicate": "重复图片",
        "too_small": "尺寸过小",
        "blank_or_decorative": "空白/装饰图",
        "dark_noise": "纯黑/噪声",
        "unknown": "无法判断用途",
        "out_of_scope": "不属于本课时小节",
        "user_reject": "用户指定不放",
    }
    lines = [
        "# 教材配图筛选说明",
        "",
        f"- 教材章节：{chapter.title}",
        f"- 扫描图片：{len(assets)} 张",
        f"- 通过质量筛选：{len(kept)} 张",
        f"- 剔除：{len(rejected)} 张",
    ]
    if used_assets is not None:
        lines.append(
            f"- 实际放入 Word：{len(used_assets)} 张（按教学环节必要性挑选，不是把通过筛选的 {len(kept)} 张全塞进去）"
        )
    if categories:
        lines.append(
            "- 剔除构成：" + "、".join(f"{category_label.get(k, k)} {v} 张" for k, v in sorted(categories.items()))
        )
    lines += [
        "",
        "## 判别规则",
        "",
        "- 保留：流程图/结构图、准确率与损失曲线、识别结果拼图、应用照片、能说明结果的操作界面；",
        "- 剔除：二维码/扫码链接、源代码与终端截图、纯黑/空白图、尺寸过小、近似重复图；",
        "- 用户在图注里写“保留/需要”会强制保留，写“不要/删除”会强制剔除；",
        "- OCR 与图像特征只用于判断“是否适合放进教案”，不判断图中知识点的对错。",
        "",
        "## 保留图片",
        "",
        "| 文件 | 尺寸 | 类型 | 所属小节 | 图注/OCR 摘要 | 理由 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for a in kept:
        summary = (a.alt or a.hint or a.ocr_text or "").replace("\n", " ").replace("|", "\\|")[:70]
        lines.append(
            f"| {a.filename} | {a.width}×{a.height} | {category_label.get(a.category, a.category)} | {a.section or '未判断'} | {summary or '—'} | {a.filter_reason} |"
        )
    if used_assets is not None:
        lines += [
            "",
            "## 实际放入 Word 的图片",
            "",
            "| 文件 | 尺寸 | 类型 | 所属小节 | 理由 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for a in used_assets:
            lines.append(
                f"| {a.filename} | {a.width}×{a.height} | {category_label.get(a.category, a.category)} | {a.section or '未判断'} | 与所在环节内容直接相关，保留一张避免图册化 |"
            )
    lines += [
        "",
        "## 剔除图片",
        "",
        "| 文件 | 尺寸 | 判定 | 所属小节 | OCR 摘要 | 原因 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for a in rejected:
        summary = (a.ocr_text or a.alt or "").replace("\n", " ").replace("|", "\\|")[:70]
        lines.append(
            f"| {a.filename} | {a.width}×{a.height} | {category_label.get(a.category, a.category)} | {a.section or '未判断'} | {summary or '—'} | {a.filter_reason} |"
        )
    lines += [
        "",
        "---",
        "",
        "这份说明由规则判别器生成，保留/剔除结果会同时写入 `图片清单.json`；教师可在上传时用图注覆盖。",
    ]
    path = out / "教材配图筛选说明.md"
    write_text(path, "\n".join(lines))
    return path


def images_prompt_text(assets: list[ImageAsset], chapter: Chapter) -> str:
    lines = [f"【教材章节】{chapter.title}", "【可用配图】"]
    for a in assets:
        lines.append(
            f"- image_id={a.id}；文件名={a.filename}；尺寸={a.width}x{a.height}；"
            f"教材图注={a.alt or '无'}；用户提示={a.hint or '无'}；"
            f"可能所属小节={a.section or '未判断'}"
        )
        if a.ocr_text:
            lines.append(f"  图片文字识别：{a.ocr_text[:300].replace(chr(10), ' / ')}")
    return "\n".join(lines)
