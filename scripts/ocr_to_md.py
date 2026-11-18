#!/usr/bin/env python3
"""扫描版教材 PDF -> 每章 Markdown（macOS Vision OCR 路径）。

独立于 pdf_parser.py（那个走 get_text，只适用于有文字层的 PDF）。
本脚本处理纯扫描版：逐页渲染 PNG -> Vision OCR -> 按目录页码切章 -> 标题分级 -> 输出 md。
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import pymupdf as fitz

ROOT = Path(__file__).resolve().parent.parent
OCR_BIN = ROOT / "scripts" / "ocr_vision"
OCR_SRC = ROOT / "scripts" / "ocr_vision.swift"


def build_ocr():
    if OCR_BIN.exists():
        return
    print("[*] 编译 Vision OCR 工具...", flush=True)
    r = subprocess.run(["swiftc", "-O", str(OCR_SRC), "-o", str(OCR_BIN)],
                       capture_output=True, text=True)
    if r.returncode:
        print(r.stderr, file=sys.stderr)
        raise SystemExit("OCR 工具编译失败")


def render_pages(pdf, pages, tmpdir):
    """把指定页渲染为 PNG。pages 为 0-based 页码列表。"""
    tmpdir.mkdir(parents=True, exist_ok=True)
    out = []
    with fitz.open(str(pdf)) as doc:
        for pno in pages:
            if pno >= doc.page_count:
                continue
            pix = doc[pno].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            path = tmpdir / f"page-{pno+1:04d}.png"
            pix.save(str(path))
            out.append(path)
    return out


def ocr_batch(paths, batch=12):
    """分批调用 Swift OCR，返回 {文件名: 文本}。"""
    result = {}
    for i in range(0, len(paths), batch):
        chunk = paths[i:i+batch]
        print(f"[*] OCR {i+1}-{i+len(chunk)}/{len(paths)}", flush=True)
        r = subprocess.run([str(OCR_BIN)] + [str(p) for p in chunk],
                           capture_output=True, text=True, timeout=600)
        cur = None
        buf = []
        for line in r.stdout.splitlines():
            if line.startswith("===== ") and line.endswith(" ====="):
                if cur:
                    result[Path(cur).name] = "\n".join(buf).strip()
                cur = line[6:-6]
                buf = []
            elif line == "===== END =====":
                if cur:
                    result[Path(cur).name] = "\n".join(buf).strip()
                    cur = None
            elif cur:
                buf.append(line)
        if cur:
            result[Path(cur).name] = "\n".join(buf).strip()
        if r.stderr.strip():
            print("[warn] OCR stderr:", r.stderr.strip()[:300], file=sys.stderr)
    return result


CHAPTER_RE = re.compile(r"^第\s*([0-9一二三四五六七八九十百]+)\s*章\s*(.+)$")
SECTION_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,2})+)\s+(\S.*)$")


def parse_toc(text_by_page, toc_pages):
    """从目录页解析 章号 -> (标题, 起始页码)。页码为 1-based PDF 页码。"""
    entries = {}
    for pno in toc_pages:
        text = text_by_page.get(f"page-{pno+1:04d}.png", "")
        lines = [l.strip() for l in text.splitlines()]
        for idx, line in enumerate(lines):
            m = CHAPTER_RE.match(line)
            if not m:
                continue
            ch, title = m.group(1), m.group(2).strip()
            # 目录行形如 "第2章 生成对抗网络的安全应用 13"，末尾数字是页码
            nums = re.findall(r"(\d{1,3})\s*$", line)
            page = int(nums[-1]) if nums else None
            if page and ch not in entries:
                entries[ch] = {"title": title, "printed_page": page}
    return entries


def is_code_line(s):
    """判断是否为代码行（含注释行），这些行里的 # 不能转成 Markdown 标题。"""
    st = s.strip()
    if not st:
        return False
    # Python 注释
    if st.startswith("#"):
        return True
    # 常见代码特征：def/class/import/return/赋值/括号调用/缩进
    if re.match(r"^(def|class|import|from|return|for|if|else|elif|while|print)\b", st):
        return True
    if re.search(r"=\s*[^=]", st) and not st.startswith("#"):
        return True
    if re.search(r"\w+\s*\(", st) and ("(" in st and ")" in st):
        return True
    if s.startswith(("    ", "\t")):
        return True
    return False


NOISE_LINES = re.compile(
    r"^(真|假|XFake|XReal|Z[（(]噪声|第[0-9]+步[：:]?$|人工智能安全[：:]?原理与实践|"
    r"[A-Za-z_]+$|图\s*\d+[-.]\d+.*)$"
)


def format_tail(text):
    """标题分级 + 过滤页码/图内孤立标注/页眉 + 代码行不转标题。"""
    out = []
    for line in text.splitlines():
        s = line.rstrip()
        st = s.strip()
        if re.fullmatch(r"\d{1,4}", st):          # 纯页码
            continue
        if NOISE_LINES.match(st) and not is_code_line(s):
            continue
        if st.startswith("#"):                     # 代码注释：转义 #，避免被当 Markdown 标题
            out.append("\\" + s)
            continue
        if is_code_line(s):                        # 其它代码行原样保留
            out.append(s)
            continue
        m = SECTION_RE.match(st)
        if m and len(st) <= 60:
            depth = m.group(1).count(".") + 1
            out.append("#" * min(depth, 4) + " " + st)
        else:
            out.append(s)
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("out_dir")
    ap.add_argument("--sample-pages", default=None,
                    help="仅处理指定页，如 1-45；用于先做样本")
    ap.add_argument("--chapter", default=None,
                    help="仅输出指定章，如 2")
    ap.add_argument("--cache", default=None, help="OCR 结果缓存 json")
    args = ap.parse_args()

    build_ocr()
    pdf = Path(args.pdf)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache = Path(args.cache) if args.cache else out_dir / "_ocr_cache.json"

    with fitz.open(str(pdf)) as doc:
        total = doc.page_count
    if args.sample_pages:
        a, b = args.sample_pages.split("-")
        pages = list(range(int(a) - 1, min(int(b), total)))
    else:
        pages = list(range(total))

    tmpdir = out_dir / "_pages"
    pngs = render_pages(pdf, pages, tmpdir)
    png_by_name = {p.name: p for p in pngs}

    if cache.exists():
        print(f"[*] 读取 OCR 缓存 {cache}")
        text_by_page = json.loads(cache.read_text(encoding="utf-8"))
        missing = [p for p in pngs if p.name not in text_by_page]
        if missing:
            text_by_page.update(ocr_batch(missing))
            cache.write_text(json.dumps(text_by_page, ensure_ascii=False), encoding="utf-8")
    else:
        text_by_page = ocr_batch(pngs)
        cache.write_text(json.dumps(text_by_page, ensure_ascii=False), encoding="utf-8")

    # 找目录页：正文前 20 页里 OCR 含"目录"的页
    toc_pages = [i for i in range(min(20, total))
                 if "目录" in text_by_page.get(f"page-{i+1:04d}.png", "")[:40]]
    print(f"[*] 目录页（1-based）: {[p+1 for p in toc_pages]}")
    toc = parse_toc(text_by_page, toc_pages)
    print(f"[*] 目录识别出 {len(toc)} 章")

    # 按 OCR 中出现的章标题行切分（目录页不算）
    chapters = {}
    cur = "全书"
    for pno in pages:
        text = text_by_page.get(f"page-{pno+1:04d}.png", "")
        if pno in toc_pages:
            continue
        first = text.splitlines()[0].strip() if text.splitlines() else ""
        m = CHAPTER_RE.match(first)
        if m:
            cur = m.group(1)
            chapters.setdefault(cur, {"title": m.group(2).strip(), "pages": []})
        chapters.setdefault(cur, {"title": "", "pages": []})["pages"].append((pno, text))

    print(f"[*] 切分出 {len(chapters)} 章: {list(chapters)}")
    img_root = out_dir / "images"
    img_root.mkdir(parents=True, exist_ok=True)
    for ch, info in chapters.items():
        if args.chapter and ch != args.chapter:
            continue
        title = info["title"] or toc.get(ch, {}).get("title", "")
        ch_img_dir = img_root / f"第{ch}章"
        ch_img_dir.mkdir(parents=True, exist_ok=True)
        blocks = []
        cap_idx = 0
        for pno, t in info["pages"]:
            blocks.append(format_tail(t))
            # 该页扫描图作为图插回，紧接该页文字之后
            png = png_by_name.get(f"page-{pno+1:04d}.png")
            if png and png.exists():
                import shutil
                cap_idx += 1
                dst = ch_img_dir / f"image-{cap_idx:04d}.jpg"
                # 用 PyMuPDF 把 PNG 转成 jpg，不引入新依赖
                with fitz.open(str(png)) as imdoc:
                    pix = imdoc[0].get_pixmap(alpha=False)
                    pix.save(str(dst), jpg_quality=70)
                blocks.append(f"![第{pno+1}页扫描图](../images/第{ch}章/{dst.name})")
        body = "\n\n".join(b for b in blocks if b.strip())
        md = f"# 第{ch}章 {title}\n\n{body}\n"
        out = out_dir / f"第{ch}章.md"
        out.write_text(md, encoding="utf-8")
        print(f"[*] 输出 {out}（{len(info['pages'])} 页, {len(md)} 字符, {cap_idx} 图）")


if __name__ == "__main__":
    main()
