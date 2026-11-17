try:
    import pymupdf as fitz
except ImportError:
    import fitz

from src.config import load_config
from src.pdf_parser import (
    extract_chapter_pages,
    extract_images,
    format_section_headings,
    is_chapter_title,
    pdf_to_markdown,
    sanitize_filename,
)


def test_format_section_headings_levels():
    lines = [
        "2.1 生成对抗网络的原理",
        "2.2.5 实践2-1 sin曲线样本模拟",
        "正文提到 0.618 是黄金比例的数值示例。",
        "2.1 可视化的作用与图表选择 15",
        "普通段落文字",
    ]
    out = format_section_headings(lines)
    assert out[0] == "## 2.1 生成对抗网络的原理"
    assert out[1] == "### 2.2.5 实践2-1 sin曲线样本模拟"
    assert out[2] == lines[2]  # 正文小数开头不改写
    assert out[3] == lines[3]  # 行尾带页码（目录/页脚）不改写
    assert out[4] == lines[4]


def test_format_section_headings_disabled():
    lines = ["2.1 概述"]
    assert format_section_headings(lines, enabled=False) == lines


def make_pdf(path):
    doc = fitz.open()
    page1 = doc.new_page()
    page1.insert_text((72, 100), "Chapter 1 Basics", fontsize=18, fontname="helv")
    page1.insert_text((72, 140), "GAN is a generative model.", fontsize=11, fontname="helv")
    page2 = doc.new_page()
    page2.insert_text((72, 80), "Table of Contents", fontsize=14, fontname="helv")
    page2.insert_text((72, 110), "Chapter 9 Misc ......... 200", fontsize=11, fontname="helv")
    page3 = doc.new_page()
    page3.insert_text((72, 80), "Chapter 2 Advanced", fontsize=18, fontname="helv")
    rect = fitz.Rect(72, 120, 172, 220)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 100, 100))
    pix.clear_with(160)
    page3.insert_image(rect, pixmap=pix)
    doc.save(str(path))
    doc.close()


def test_is_chapter_title_ignores_toc_lines():
    pattern = r"^Chapter\s+\d+"
    assert is_chapter_title("Chapter 1 Basics", pattern)
    assert not is_chapter_title("Chapter 9 Misc ......... 200", pattern)
    assert not is_chapter_title("Chapter 9 Misc            200", pattern)
    assert not is_chapter_title("This mentions Chapter 1 in text", pattern)
    assert not is_chapter_title("", pattern)


def test_sanitize_filename():
    assert sanitize_filename("Chapter 2: Advanced?") == "Chapter_2_Advanced"
    assert sanitize_filename("  ") == "chapter"
    assert sanitize_filename("a" * 100) == "a" * 50


def test_extract_chapter_pages_ignores_toc(tmp_path):
    pdf = tmp_path / "book.pdf"
    make_pdf(pdf)
    chapters = extract_chapter_pages(pdf, r"^Chapter\s+\d+", ignore_toc=True)
    titles = [c.title for c in chapters]
    assert titles == ["Chapter 1 Basics", "Chapter 2 Advanced"]
    assert len(chapters[0].pages) == 2
    assert chapters[0].pages[0][0] == 1


def test_extract_images(tmp_path):
    pdf = tmp_path / "book.pdf"
    make_pdf(pdf)
    images = extract_images(pdf, tmp_path / "images", min_image_size=32)
    all_images = [p for paths in images.values() for p in paths]
    assert len(all_images) == 1
    assert all_images[0].exists()
    assert all_images[0].stat().st_size > 0
    assert all_images[0].name == "image-0001.jpg"
    assert images == {3: all_images}


def test_pdf_to_markdown_end_to_end(tmp_path):
    pdf = tmp_path / "book.pdf"
    make_pdf(pdf)
    out = tmp_path / "out"
    config = load_config()
    config["stage1"]["chapter_pattern"] = r"^Chapter\s+\d+"
    written = pdf_to_markdown(pdf, out, config)
    assert len(written) == 2
    md2 = out / "Chapter_2_Advanced.md"
    text = md2.read_text(encoding="utf-8")
    assert "# Chapter 2 Advanced" in text
    assert "![image-0001](images/image-0001.jpg)" in text
    assert (out / "images" / "image-0001.jpg").exists()
    md1 = out / "Chapter_1_Basics.md"
    text1 = md1.read_text(encoding="utf-8")
    assert "# Chapter 1 Basics" in text1
    assert "GAN is a generative model." in text1
    assert "![](" not in text1
