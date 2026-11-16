import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path

try:
    import pymupdf as fitz
except ImportError:
    import fitz

from src.config import load_config
from src.utils import ensure_dir, setup_logging, write_text
from src.vision_client import VisionClient

logger = setup_logging()

NO_CHAPTER_KEY = "全书"


@dataclass
class Chapter:
    title: str
    pages: list[tuple[int, str]] = field(default_factory=list)


def is_chapter_title(line: str, pattern: str, ignore_toc: bool = True) -> bool:
    """判断行是否为章标题；忽略目录页形如"第N章 …… 123"的误判行。"""
    line = line.strip()
    if not line or len(line) > 60:
        return False
    if not re.match(pattern, line):
        return False
    if ignore_toc and (
        re.search(r"\.{3,}|[…⋯]{2,}", line) or re.search(r"\s{3,}\d+\s*$", line)
    ):
        return False
    return True


def extract_chapter_pages(
    pdf_path: str | Path, chapter_pattern: str, ignore_toc: bool = True
) -> list[Chapter]:
    """按"第N章"标题切分页面，返回章节列表（含每页页码与文本）。"""
    chapters: list[Chapter] = []
    current: Chapter | None = None
    with fitz.open(str(pdf_path)) as doc:
        for page_no in range(1, doc.page_count + 1):
            page_text = doc[page_no - 1].get_text("text") or ""
            for line in page_text.splitlines():
                if is_chapter_title(line, chapter_pattern, ignore_toc):
                    current = Chapter(title=line.strip())
                    chapters.append(current)
            if current is None:
                current = Chapter(title=NO_CHAPTER_KEY)
                chapters.append(current)
            current.pages.append((page_no, page_text))
    return chapters


def extract_images(
    pdf_path: str | Path,
    out_dir: str | Path,
    image_prefix: str = "image-",
    image_format: str = "jpg",
    min_image_size: int = 32,
) -> dict[int, list[Path]]:
    """导出每页图片到 out_dir，命名 image-0001.jpg 递增；过滤装饰性小图。"""
    out_dir = ensure_dir(out_dir)
    images: dict[int, list[Path]] = {}
    counter = 0
    with fitz.open(str(pdf_path)) as doc:
        for page_no in range(1, doc.page_count + 1):
            page = doc[page_no - 1]
            for info in page.get_images(full=True):
                xref = info[0]
                try:
                    pix = fitz.Pixmap(doc, xref)
                except Exception:
                    logger.warning(f"第 {page_no} 页图片 xref={xref} 无法解码，已跳过")
                    continue
                try:
                    if pix.width < min_image_size or pix.height < min_image_size:
                        continue
                    if pix.colorspace and pix.colorspace.n > 3:
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    if pix.alpha:
                        pix = fitz.Pixmap(pix, 0)
                    counter += 1
                    path = out_dir / f"{image_prefix}{counter:04d}.{image_format}"
                    pix.save(str(path))
                    images.setdefault(page_no, []).append(path)
                finally:
                    del pix
    return images


def insert_image_refs(
    chapter: Chapter,
    images: dict[int, list[Path]],
    image_dir_name: str,
    caption_pattern: str | None = None,
    descriptions: dict[str, str] | None = None,
) -> str:
    """把图片引用插入 md：优先放在题注行下方，保持图与正文位置对应。"""
    caption_re = re.compile(caption_pattern) if caption_pattern else None
    parts: list[str] = []
    for page_no, page_text in chapter.pages:
        lines = page_text.splitlines()
        page_images = images.get(page_no, [])
        refs = []
        for img in page_images:
            desc = (descriptions or {}).get(str(img), "")
            alt = f"图片描述：{desc}" if desc else img.stem
            refs.append(f"![{alt}]({image_dir_name}/{img.name})")
        new_lines = list(lines)
        caption_indexes = [
            i for i, line in enumerate(new_lines) if caption_re and caption_re.match(line.strip())
        ]
        if refs and caption_indexes:
            for offset, (ref, cap_idx) in enumerate(zip(refs, caption_indexes)):
                new_lines.insert(cap_idx + 1 + offset, ref)
            remaining = refs[len(caption_indexes):]
            new_lines.extend(remaining)
        elif refs:
            new_lines.extend(refs)
        parts.append("\n".join(new_lines))
    return "\n\n".join(part for part in parts if part)


def sanitize_filename(name: str, max_len: int = 50) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", name.strip()).strip("_")
    return cleaned[:max_len] or "chapter"


def build_markdown(
    chapter: Chapter,
    images: dict[int, list[Path]],
    image_dir_name: str,
    caption_pattern: str | None,
    use_vision: bool = False,
    vision_config: dict | None = None,
) -> str:
    descriptions: dict[str, str] = {}
    if use_vision and vision_config and vision_config.get("enabled"):
        client = VisionClient(vision_config)
        for paths in images.values():
            for path in paths:
                descriptions[str(path)] = client.describe_image(path)
    body = insert_image_refs(chapter, images, image_dir_name, caption_pattern, descriptions)
    return f"# {chapter.title}\n\n{body}\n"


def pdf_to_markdown(pdf_path: str | Path, out_dir: str | Path, config: dict) -> list[Path]:
    """stage1 主流程：抽图 → 切章 → 拼装 md（每章一个文件）。"""
    s1 = config.get("stage1", {})
    pattern = s1.get("chapter_pattern", NO_CHAPTER_KEY)
    ignore_toc = s1.get("ignore_toc_lines", True)
    image_dir_name = s1.get("image_dir", "images")
    image_dir = ensure_dir(Path(out_dir) / image_dir_name)
    logger.info(f"stage1 开始：{pdf_path} -> {out_dir}")
    images = extract_images(
        pdf_path,
        image_dir,
        s1.get("image_prefix", "image-"),
        s1.get("image_format", "jpg"),
        s1.get("min_image_size", 32),
    )
    logger.info(f"共导出 {sum(len(v) for v in images.values())} 张图片")
    chapters = extract_chapter_pages(pdf_path, pattern, ignore_toc)
    logger.info(f"共切分出 {len(chapters)} 个章节")
    written: list[Path] = []
    for chapter in chapters:
        md = build_markdown(
            chapter,
            images,
            image_dir_name,
            s1.get("caption_pattern"),
            s1.get("use_vision", False),
            config.get("vision"),
        )
        out_path = Path(out_dir) / f"{sanitize_filename(chapter.title)}.md"
        write_text(out_path, md)
        written.append(out_path)
        logger.info(f"已生成 {out_path.name}（{len(chapter.pages)} 页）")
    return written


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="stage1：教材 PDF 转 Markdown")
    parser.add_argument("pdf", help="教材 PDF 路径")
    parser.add_argument("out_dir", help="输出目录（每章一个 md + images/ 图片目录）")
    parser.add_argument("--config", default=None, help="配置文件路径")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    pdf_to_markdown(args.pdf, args.out_dir, config)


if __name__ == "__main__":
    main()
