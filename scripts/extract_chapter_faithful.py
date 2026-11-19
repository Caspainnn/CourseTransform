"""忠实抽取单章 PDF → Markdown：原文一字不改，只做格式转换 + 图片定位。

处理两类图：
1) 嵌入图（代码截图/结果图）：按 page.get_images 的 bbox 插到正文对应位置；
2) 矢量架构图（流程图，无嵌入图对象）：按"图N-N"题注坐标反推图形区域，
   整块渲染成 PNG，替换掉被 get_text 打散的矢量文字。

用法：
  PYTHONPATH=. .venv/bin/python scripts/extract_chapter_faithful.py \
      <pdf> <out_dir> --start 21 --end 53 \
      --title "第2章 生成对抗网络攻击与防护" --config config.yaml
"""
import argparse
import re
from pathlib import Path

import pymupdf as fitz
import yaml

from src.pdf_parser import format_section_headings, sanitize_filename
from src.utils import ensure_dir, write_text

CAP_RE = re.compile(r"^\s*图\s*\d+\s*[-.]\s*\d+")
# 矢量架构图区域高度（pt）：从题注往上取这么多，覆盖图形主体
VISUAL_H = 185


def clean_line(s: str) -> str:
    return s.rstrip()


def extract_page(page: fitz.Page, page_no: int, img_dir: Path, counter: list[int],
                 vect_cap_pages: set[int], img_fmt: str, quality: int) -> str:
    """把一页转成 md 片段：文字块与嵌入图按 y 坐标排序交替输出。"""
    d = page.get_text("dict")
    items: list[tuple[float, float, str]] = []  # (y0, x0, content)

    for b in d["blocks"]:
        x0, y0, x1, y1 = b["bbox"]
        if b["type"] == 0:  # 文字块
            lines = []
            for line in b["lines"]:
                txt = "".join(s["text"] for s in line["spans"])
                lines.append(clean_line(txt))
            block_text = "\n".join(lines)
            if block_text.strip():
                items.append((y0, x0, block_text))
        elif b["type"] == 1:  # 嵌入图块：用 xref 导出图片本体，不截周围正文
            counter[0] += 1
            dst = img_dir / f"image-{counter[0]:04d}.{img_fmt}"
            xref = b.get("number", 0)
            if xref:
                try:
                    pix = fitz.Pixmap(page.parent, xref)
                    if pix.n > 4:
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    pix.save(str(dst))
                except Exception:
                    clip = fitz.Rect(x0, y0, x1, y1)
                    page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip, alpha=False).save(str(dst))
            else:
                clip = fitz.Rect(x0, y0, x1, y1)
                page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip, alpha=False).save(str(dst))
            items.append((y0, x0, f"![{dst.stem}](images/{dst.name})"))

    # 矢量图处理：页面有题注且被标记为矢量页 → 把题注上方区域整块渲染成图，
    # 并移除该区域内被打散的矢量文字块（保留区域外的嵌入图与正文）。
    if page_no in vect_cap_pages:
        cap_blocks = []
        for b in d["blocks"]:
            if b["type"] == 0:
                txt = "".join(s["text"] for l in b["lines"] for s in l["spans"]).strip()
                if CAP_RE.match(txt):
                    cap_blocks.append(b)
        for cb in cap_blocks:
            cap_y0 = cb["bbox"][1]
            # 图形区域：题注上方。上界取题注前 VISUAL_H，且不越过页顶
            top = max(55, cap_y0 - VISUAL_H)
            clip = fitz.Rect(60, top, page.rect.width - 55, cap_y0 - 3)
            counter[0] += 1
            dst = img_dir / f"image-{counter[0]:04d}.{img_fmt}"
            pm = page.get_pixmap(matrix=fitz.Matrix(3, 3), clip=clip, alpha=False)
            pm.save(str(dst))
            # 移除落在图形区域内的文字块（矢量文字被打散的部分），用渲染图替代
            items = [(y, x, c) for (y, x, c) in items if not (top <= y < cap_y0 - 3)]
            items.append((top, 0, f"![{dst.stem}](images/{dst.name})"))

    items.sort(key=lambda t: (round(t[0], 1), t[1]))
    body = "\n\n".join(c for (_, _, c) in items)
    body = "\n".join(format_section_headings(body.splitlines(), True))
    return body


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("out_dir")
    ap.add_argument("--start", type=int, required=True)
    ap.add_argument("--end", type=int, required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--vect-pages", default="", help="矢量图所在物理页，逗号分隔；留空则自动检测")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    s1 = cfg.get("stage1", {})
    out_dir = Path(args.out_dir)
    img_dir = ensure_dir(out_dir / s1.get("image_dir", "images"))
    fmt = s1.get("image_format", "jpg")

    doc = fitz.open(args.pdf)

    # 自动检测矢量图页：凡有"图N-N"题注的页都做区域渲染。
    # 混合页（同时有嵌入图）中，嵌入图已按 bbox 定位，区域渲染只补题注上方的矢量主体。
    if args.vect_pages:
        vect_pages = {int(x) for x in args.vect_pages.split(",") if x.strip()}
    else:
        vect_pages = set()
        for pno in range(args.start, args.end + 1):
            page = doc[pno - 1]
            for b in page.get_text("dict")["blocks"]:
                if b["type"] == 0:
                    txt = "".join(s["text"] for l in b["lines"] for s in l["spans"]).strip()
                    # 排除"如图所示"这类正文行，只认独立成行的正式题注
                    if CAP_RE.match(txt) and ("图" in txt[:3]):
                        vect_pages.add(pno)
                        break
    print(f"矢量图页(将区域渲染): {sorted(vect_pages)}")

    counter = [0]
    parts: list[str] = []
    for pno in range(args.start, args.end + 1):
        parts.append(extract_page(doc[pno - 1], pno, img_dir, counter, vect_pages, fmt, 70))

    body = "\n\n".join(p for p in parts if p.strip())
    md = f"# {args.title}\n\n{body}\n"
    out_path = out_dir / f"{sanitize_filename(args.title)}.md"
    write_text(out_path, md)
    print(f"[OK] {out_path} | {args.end-args.start+1} 页 | {counter[0]} 图 | {len(md)} 字符")


if __name__ == "__main__":
    main()
