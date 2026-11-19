"""从有文字层的教材 PDF 抽取指定物理页区间，复用 stage1 现成组件生成单章 md。

用法：
  .venv/bin/python scripts/extract_one_chapter.py <pdf> <out_dir> \\
      --start 21 --end 53 --title "第2章 生成对抗网络攻击与防护" --config config.yaml
"""
import argparse
from pathlib import Path

import pymupdf as fitz
import yaml

from src.config import load_config
from src.pdf_parser import Chapter, build_markdown, extract_images, sanitize_filename
from src.utils import ensure_dir, write_text


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("out_dir")
    ap.add_argument("--start", type=int, required=True, help="起始物理页（1-based，含）")
    ap.add_argument("--end", type=int, required=True, help="结束物理页（1-based，含）")
    ap.add_argument("--title", required=True, help="章标题，作为 md 的 H1")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    cfg = load_config(args.config)
    s1 = cfg.get("stage1", {})
    out_dir = Path(args.out_dir)
    image_dir = ensure_dir(out_dir / s1.get("image_dir", "images"))

    # 只导出该章范围内的图片，避免全书几百张图
    images: dict[int, list[Path]] = {}
    counter = 0
    with fitz.open(str(args.pdf)) as doc:
        for page_no in range(args.start, args.end + 1):
            page = doc[page_no - 1]
            for info in page.get_images(full=True):
                xref = info[0]
                try:
                    pix = fitz.Pixmap(doc, xref)
                    if pix.width < s1.get("min_image_size", 32) or pix.height < s1.get("min_image_size", 32):
                        continue
                    if pix.n > 4:
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    counter += 1
                    dst = image_dir / f"{s1.get('image_prefix', 'image-')}{counter:04d}.{s1.get('image_format', 'jpg')}"
                    pix.save(str(dst))
                    images.setdefault(page_no, []).append(dst)
                except Exception:
                    continue
            # 同时把整页渲染图不作为图片导出——正文文字已抽取，仅保留内嵌图

    pages: list[tuple[int, str]] = []
    with fitz.open(str(args.pdf)) as doc:
        for page_no in range(args.start, args.end + 1):
            pages.append((page_no, doc[page_no - 1].get_text("text") or ""))

    chapter = Chapter(title=args.title, pages=pages)
    md = build_markdown(
        chapter,
        images,
        s1.get("image_dir", "images"),
        s1.get("caption_pattern"),
        s1.get("use_vision", False),
        cfg.get("vision"),
        s1.get("section_headings", True),
    )
    out_path = out_dir / f"{sanitize_filename(args.title)}.md"
    write_text(out_path, md)
    print(f"[OK] {out_path} | {len(pages)} 页 | {sum(len(v) for v in images.values())} 图 | {len(md)} 字符")


if __name__ == "__main__":
    main()
