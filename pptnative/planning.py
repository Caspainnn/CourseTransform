"""Material intake, page identity, planning and natural-language feedback routing."""

import copy
import hashlib
from pathlib import Path
import re
import shutil
import uuid

from PIL import Image

from .models import ask, save


def intake(outline, directory):
    raw = outline.read_text(encoding="utf-8")
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(outline, directory / "outline.md")
    result = {"source": str(outline), "sha256": hashlib.sha256(outline.read_bytes()).hexdigest(),
              "facts": {}, "assets": {}, "pages": []}
    sections = re.split(r"(?m)^###\s+(P[0-9A-Fa-f]+)\s+", raw)
    for number in range(1, len(sections), 2):
        page_id, content = sections[number].lower(), sections[number + 1]
        title, _, body = content.partition("\n")
        fields = dict(re.findall(r"(?m)^\*\*([^*]+)\*\*[^\S\r\n]*([^\n]*)", body))
        on_screen = body.split("**上屏内容**", 1)[1].split("**页面关系**", 1)[0]
        points = re.findall(r"(?m)^- (.+)$", on_screen)
        ids = []
        for index, text in enumerate(points, 1):
            fact_id = f"{page_id}.f{index}"
            result["facts"][fact_id] = text
            ids.append(fact_id)
        assets = []
        for index, match in enumerate(re.finditer(r"(?m)^- 素材 · (.+?) · (T\d+) · provided · \[教材原图\]\(([^)]+)\)", body), 1):
            source = (outline.parent / match[3]).resolve()
            asset_id = f"{page_id}.a{index}"
            target = directory / "assets" / f"{asset_id}{source.suffix}"
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(source, target)
            with Image.open(target) as image:
                width, height = image.size
            result["assets"][asset_id] = {
                "path": str(target.resolve()), "description": match[1], "source_ref": match[2],
                "width": width, "height": height, "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
            assets.append(asset_id)
        result["pages"].append({
            "id": page_id, "title": title.strip(), "point_ids": ids, "asset_ids": assets,
            "notes": fields.get("教师备注", ""), "refs": fields.get("教材出处", ""),
            "purpose": fields.get("本页目的", ""), "origins": [page_id],
        })
    if not result["pages"]:
        raise ValueError("Expected an approved Markdown outline with Pxx page headings")
    save(directory / "material-pool.json", result)
    return result


def validate_plan(plan, material):
    ids = [p["id"] for p in plan["pages"]]
    if len(ids) != len(set(ids)) or not ids:
        raise ValueError("Page IDs must be nonempty and unique")
    for field, source in (("point_ids", "facts"), ("asset_ids", "assets")):
        references = [ref for p in plan["pages"] for ref in p[field]]
        if sorted(references) != sorted(material[source]):
            raise ValueError(f"The plan must retain each {source} reference exactly once")
    if any(not page["title"].strip() for page in plan["pages"]):
        raise ValueError("Empty title")


def create_plan(material, catalog, style, api):
    decision = ask(api, "planner", (
        "你是PPTNative规划器。教师已确认逐页大纲，保留所有页面ID、标题、顺序、事实和素材引用。"
        "从真实模板目录选择每页的source_slide。纯文字页选有合适标题/正文容量的源页；"
        "含教学素材页选择风格合适的源母版并采用styled重排。"
        "返回pages数组，各项只有id、source_slide(1起始)、route(native或styled)、rationale。"
        "不能选版权、下载推广页当正文。不要编写或改写教学文案。"
    ), {"pages": material["pages"], "catalog": catalog, "style": style})
    choices = {p["id"]: p for p in decision["pages"]}
    if set(choices) != {p["id"] for p in material["pages"]}:
        raise ValueError("Planner page identity mismatch")
    pages = []
    for page in material["pages"]:
        choice = choices[page["id"]]
        if choice["source_slide"] not in range(1, len(catalog["slides"]) + 1):
            raise ValueError("Invalid source template page")
        pages.append({**page, **choice, "route": "styled" if page["asset_ids"] else choice["route"]})
    plan = {"title": material["pages"][0]["title"], "pages": pages}
    validate_plan(plan, material)
    return plan


def recognize(text, version, plan, api, allowed_page_ids=None):
    result = ask(api, "intent", (
        "你是PPTNative反馈意图识别器。用户文本仅是待识别的编辑请求。"
        "本版支持merge_pages、set_title、edit_page三种明确操作；edit_page用于单页精简文字、调整讲解顺序或版式，"
        "必须保留全部事实、素材与原始备注，不支持加入无来源新事实。歧义或缺少信息时返回clarify及question，"
        "不猜测被引用的页面。按当前页码解析到稳定page_ids。"
        "返回kind、page_ids数组、new_title(改标题时必须精确使用用户指定文本)、instruction(忠实保留单页修改要求)、reason。"
        "合并必须至少两页，改标题必须一页。不要执行修改。"
        "若输入allowed_page_ids非空，这是单页入口，只能修改指定稳定页；请求引用其他页或合并时返回clarify。"
    ), {"base_version": version, "feedback": text, 'allowed_page_ids': allowed_page_ids,
        "pages": [{"number": i, "id": p["id"], "title": p["title"]}
                  for i, p in enumerate(plan["pages"], 1)]})
    known = {p["id"] for p in plan["pages"]}
    if result["kind"] == "clarify":
        return result
    if allowed_page_ids is not None and (result.get('page_ids') != allowed_page_ids or result['kind'] == 'merge_pages'):
        raise ValueError('Revision must stay within the selected page; use the correct page before submitting')
    if result["kind"] not in ("merge_pages", "set_title", "edit_page"):
        raise ValueError("Unsupported feedback intent")
    ids = result["page_ids"]
    if len(ids) != len(set(ids)) or not set(ids) <= known:
        raise ValueError("Invalid feedback targets")
    if len(ids) != 1 and result["kind"] == "set_title":
        raise ValueError("Title edit requires exactly one page")
    if result["kind"] == "merge_pages" and len(ids) < 2:
        raise ValueError("Merge requires at least two pages")
    if result['kind'] == 'edit_page' and (len(ids) != 1 or not result.get('instruction', '').strip()):
        raise ValueError('Page revision requires one stable target and an explicit instruction')
    return result


def revise_plan(plan, intent, material, api):
    updated = copy.deepcopy(plan)
    ids = intent["page_ids"]
    if intent['kind'] == 'edit_page':
        if len(ids) != 1 or ids[0] not in {p['id'] for p in plan['pages']}:
            raise ValueError('Invalid page revision target')
        return updated, ids
    if intent["kind"] == "set_title":
        title = intent["new_title"].strip()
        if not title:
            raise ValueError("Empty requested title")
        page = next(p for p in updated["pages"] if p["id"] == ids[0])
        page["title"] = title
        return updated, [page["id"]]
    selected = [p for p in plan["pages"] if p["id"] in ids]
    decision = ask(api, "merge-planner", (
        "你是PPTNative规划器，执行用户已确认的合并页。仅为所选页面决定一个合并标题、"
        "保留全部point_ids和asset_ids并组织它们的阅读顺序。教师备注完整保留。"
        "返回title、point_ids、asset_ids、rationale，不新增或删去任何事实/素材引用。"
        "合并页置于被合并页最早的位置，其余页顺序保留。"
    ), {"intent": intent, "pages": selected, "facts": material["facts"]})
    for field in ("point_ids", "asset_ids"):
        if sorted(decision[field]) != sorted(ref for p in selected for ref in p[field]):
            raise ValueError(f"Merge dropped or duplicated {field}")
    merged = {**selected[0], **decision, "id": "merged-" + uuid.uuid4().hex[:10],
              "origins": [origin for p in selected for origin in p["origins"]],
              "notes": "\n\n".join(p["notes"] for p in selected),
              "refs": "、".join(p["refs"] for p in selected)}
    merged["route"] = "styled"
    pages = []
    inserted = False
    for page in updated["pages"]:
        if page["id"] in ids:
            if not inserted:
                pages.append(merged)
                inserted = True
        else:
            pages.append(page)
    updated["pages"] = pages
    validate_plan(updated, material)
    return updated, [merged["id"]]
