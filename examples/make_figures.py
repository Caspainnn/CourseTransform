"""生成示例教材配图，供图片上传/OCR/排版联调使用。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE = Path(__file__).resolve().parent
OUT = BASE / "figures"


def font(size: int):
    for path in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def figure_types():
    im = Image.new("RGB", (900, 460), "white")
    d = ImageDraw.Draw(im)
    d.text((40, 24), "图 1-1  人工智能的三种类型", fill="#1f2937", font=font(30))
    labels = ["弱人工智能\n特定任务", "通用人工智能\n跨领域迁移", "超级人工智能\n全面超越人类"]
    colors = ["#dbeafe", "#dcfce7", "#fef3c7"]
    for i, (label, color) in enumerate(zip(labels, colors)):
        x = 45 + i * 280
        d.rounded_rectangle((x, 110, x + 240, 340), radius=18, fill=color, outline="#64748b", width=3)
        lines = label.split("\n")
        d.text((x + 36, 165), lines[0], fill="#111827", font=font(24))
        d.text((x + 56, 230), lines[1], fill="#475569", font=font(19))
    d.text((45, 390), "按能力范围划分：当前应用几乎都属于弱人工智能", fill="#475569", font=font(19))
    im.save(OUT / "fig-1-1.png")


def figure_timeline():
    im = Image.new("RGB", (1000, 420), "white")
    d = ImageDraw.Draw(im)
    d.text((40, 24), "图 1-2  人工智能发展关键节点", fill="#1f2937", font=font(30))
    points = [("1950", "图灵测试"), ("1956", "达特茅斯会议"), ("1997", "深蓝"), ("2016", "AlphaGo"), ("2022", "生成式 AI")]
    y = 230
    d.line((60, y, 940, y), fill="#64748b", width=4)
    for i, (year, name) in enumerate(points):
        x = 70 + i * 210
        d.ellipse((x - 12, y - 12, x + 12, y + 12), fill="#c2410c")
        d.text((x - 22, 150), year, fill="#111827", font=font(24))
        d.text((x - 46, 300), name, fill="#475569", font=font(18))
    im.save(OUT / "fig-1-2.png")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    figure_types()
    figure_timeline()
    print(OUT / "fig-1-1.png")
    print(OUT / "fig-1-2.png")
