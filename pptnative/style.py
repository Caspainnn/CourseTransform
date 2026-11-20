"""Extract a reusable visual contract from any PPTX and its rendered source pages."""

import argparse
from collections import Counter
import colorsys
import hashlib
import json
from pathlib import Path
import re
from zipfile import ZipFile

from lxml import etree
from PIL import Image, ImageDraw

from .api import call, settings, image_content

RULES = Path(__file__).with_name("hybrid-style-workflow.md")
STYLE_VERSION = 2


def extract(template, previews, output):
    template, previews, output = template.resolve(), previews.resolve(), output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    files = sorted(previews.glob("*.png"), key=lambda p: int(re.findall(r"\d+", p.stem)[-1]))
    if not files:
        raise ValueError(f"No rendered template slides in {previews}")
    fills, fonts = Counter(), Counter()
    themes = []
    visible_hues = Counter()
    for path in files:
        with Image.open(path) as image:
            image = image.convert("RGB").resize((160, 90))
            for pixel in image.getdata():
                hue, saturation, value = colorsys.rgb_to_hsv(*(channel / 255 for channel in pixel))
                if saturation >= 0.35 and value >= 0.25:
                    visible_hues[int(hue * 36) % 36] += 1
    with ZipFile(template) as package:
        for name in package.namelist():
            if re.fullmatch(r"ppt/(slides/slide|theme/theme)\d+\.xml", name):
                root = etree.fromstring(package.read(name))
                fills.update(root.xpath('//*[local-name()="srgbClr"]/@val'))
                fonts.update(root.xpath('//*[local-name()="latin" or local-name()="ea"]/@typeface'))
                if "/theme/" in name:
                    for color in root.xpath('//*[local-name()="clrScheme"]/*'):
                        themes.append({"role": etree.QName(color).localname,
                                       "color": color[0].get("val", color[0].get("lastClr"))})
    evidence = {"template_sha256": hashlib.sha256(template.read_bytes()).hexdigest(),
                "theme_colors": themes, "frequent_colors": fills.most_common(20),
                "fonts": fonts.most_common(12), "rendered_pages": [p.name for p in files],
                "visible_saturated_hues": [{"hue_degrees": key * 10 + 5, "pixel_count": count}
                                          for key, count in visible_hues.most_common(8)]}
    # Contact sheets are evidence for the vision API; they never enter the PPTX.
    contacts = []
    for start in range(0, len(files), 16):
        batch = files[start:start + 16]
        canvas = Image.new("RGB", (1280, 202 * ((len(batch) + 3) // 4)), "#eef0f3")
        pen = ImageDraw.Draw(canvas)
        for index, path in enumerate(batch):
            x, y = index % 4 * 320, index // 4 * 202
            page_number = int(re.findall(r"\d+", path.stem)[-1])
            pen.text((x + 6, y + 4), f"Slide {page_number}", fill="black")
            with Image.open(path) as page:
                page.thumbnail((308, 174))
                canvas.paste(page, (x + 6, y + 23))
        contact = output / f"template-contact-{start // 16 + 1}.jpg"
        canvas.save(contact, quality=90)
        contacts.append(contact)
    prompt = (
        "从给定PPT模板实图及XML颜色字体证据提取可执行的风格合同，供另一个文本模型设计图片页。"
        "不得套用固定黄黑或蓝白配色。Office主题色可能是默认值，要以可见源页的主导风格为准。"
        "输出纯JSON，含 identity(简述)、palette(键background,surface,primary,secondary,accent,text,"
        "muted_text,soft_surface，各值含hex与use与source_pages数组)、typography、composition、"
        "color_distribution、motifs、avoid、page_patterns。每个颜色必须#RRGGBB。"
        "明确主色/辅色/中性色如何组合成视觉层次，给出来自模板的实色区域、浅色衬底、"
        "深色对比区和强调色用法。不要把所有页面都处理成白底灰卡片加细色线。"
        "允许从模板主色派生低饱和浅色，明确标注派生。若模板本来低饱和，应保留其克制。"
        "page_patterns给出流程图、代码讲解、多图比较三种内容可采用的不同空间结构与用色逻辑，"
        "保护图像及坐标轴完整性。不要写具体课程文案。为避免所有模板结果雷同，每种风格建议"
        "须注明可见依据页码。字体优先实际模板字体，颜色分配写建议范围而非硬性占比。"
        "visible_saturated_hues来自真实渲染像素，primary/accent必须与可见主导色相相符。"
        "例如描述为黄黑时不能把XML默认红橙色选作主色。默认XML主题红色可能从未在页面使用。\n"
        + json.dumps(evidence, ensure_ascii=False)
    )
    content = [{"type": "text", "text": prompt}] + [image_content(p) for p in contacts]
    message = call("LLM_OUTLINE_JSON_MODEL", [{"role": "user", "content": content}],
                   max_tokens=6500, response_format={"type": "json_object"},
                   chat_template_kwargs={"enable_thinking": False})
    raw = message.get("content") or ""
    (output / "style-profile.raw.txt").write_text(raw, encoding="utf-8")
    profile = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
    for role in ("background", "surface", "primary", "secondary", "accent", "text", "muted_text", "soft_surface"):
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", profile["palette"][role]["hex"]):
            raise ValueError(f"Invalid palette role {role}")
    # A colorful theme contract must actually match the dominant rendered hue.
    total_saturated = sum(visible_hues.values())
    dominant = visible_hues.most_common(1)
    if dominant and dominant[0][1] / total_saturated > 0.30:
        hue = dominant[0][0] * 10 + 5
        role_hues = []
        for role in ("primary", "accent"):
            color = profile["palette"][role]["hex"].lstrip("#")
            rgb = [int(color[index:index + 2], 16) / 255 for index in (0, 2, 4)]
            h, s, _ = colorsys.rgb_to_hsv(*rgb)
            if s >= 0.25:
                role_hues.append(h * 360)
        if not any(min(abs(h - hue), 360 - abs(h - hue)) <= 25 for h in role_hues):
            raise ValueError("Vision palette contradicts the dominant rendered hue; regenerate the contract")
    for field in ("identity", "typography", "composition", "color_distribution", "motifs", "avoid", "page_patterns"):
        if not profile.get(field):
            raise ValueError(f"Missing style contract field {field}")
    profile["provenance"] = {**evidence, "contract_version": STYLE_VERSION,
                             "vision_model": settings()["LLM_OUTLINE_JSON_MODEL"],
                             "template": str(template), "source_previews": str(previews)}
    (output / "style-profile.json").write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Style contract saved: {output / 'style-profile.json'}", flush=True)
    return output / "style-profile.json"


def instructions(profile):
    contract = json.loads(profile.read_text(encoding="utf-8"))
    if contract.get("provenance", {}).get("contract_version") != STYLE_VERSION:
        raise ValueError("Regenerate the style contract with the current extraction checks")
    return (f"Read the template-specific visual contract {profile.resolve().as_posix()} and shared workflow "
            f"{RULES.resolve().as_posix()}. Apply the palette roles and composition to every rebuilt image page. "
            "The contract supersedes prior template-specific color or layout defaults. "
            "Write style-decisions.json with the implemented color roles, hierarchy and image-safe areas for each rebuilt page.")


def ensure_profile(template, previews, output):
    profile = output / "style-profile.json"
    if profile.exists():
        data = json.loads(profile.read_text(encoding="utf-8"))
        provenance = data.get("provenance", {})
        fingerprint = hashlib.sha256(template.read_bytes()).hexdigest()
        if provenance.get("contract_version") == STYLE_VERSION and provenance.get("template_sha256") == fingerprint:
            return profile
    return extract(template, previews, output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", type=Path)
    parser.add_argument("previews", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    extract(args.template, args.previews, args.output)
