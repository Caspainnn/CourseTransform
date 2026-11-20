"""Enforced split authoring over the unchanged PPT Master round-trip engine."""
from copy import deepcopy
from pathlib import Path
import json
import hashlib
import math
import re
import shutil
import sys
import threading
import xml.etree.ElementTree as ET

from PIL import Image

from .models import ROOT, ask, parallel, read, save
from .native_repair import run_upstream
from .pipeline import check_cancel

SVG = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG)
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
TEXT = re.compile(r"<(?:[\w]+:)?text\b[^>]*>.*?</(?:[\w]+:)?text\s*>", re.S)
MAX_LAYOUT_REMATCHES = 6
VISION_FIELDS = ("number", "role", "layout_logic", "capacity", "safe_text_areas",
                 "decoration_conflicts", "semantic_neutrality", "text_fill_compatible")


def route_for(page):
    return "local-recompose" if page["asset_ids"] else "template-fill"


def validate_asset_references(nodes, assets):
    expected = {a["workspace_href"] for a in assets.values()}
    images = [n for root in nodes for n in root.iter() if n.tag.rsplit('}', 1)[-1] == 'image']
    actual = {n.get('href', n.get('{http://www.w3.org/1999/xlink}href')) for n in images}
    if actual != expected:
        raise ValueError(f"Fixed image contract: missing={sorted(expected-actual)}, unexpected={sorted(str(v) for v in actual-expected)}. Include every supplied original image using its workspace_href; never omit or replace an image to resolve visual feedback.")
    if any('slice' in n.get('preserveAspectRatio', '') or n.get('preserveAspectRatio') == 'none'
           or n.get('clip-path') for n in images):
        raise ValueError("Fixed images must use contain/meet without clipping or aspect distortion")
    for asset in assets.values():
        minimum = asset.get('minimum_display_width', 0)
        if not minimum:
            continue
        placed = [n for n in images if n.get('href', n.get('{http://www.w3.org/1999/xlink}href')) == asset['workspace_href']]
        widths = [min(float(n.get('width', 0)), float(n.get('height', 0)) * asset['width'] / asset['height']) for n in placed]
        if max(widths, default=0) + 1 < minimum:
            raise ValueError(f"Image readability capacity: {asset['workspace_href']} effective contain width={max(widths, default=0):.1f}px, vision-recommended minimum={minimum}px. The image box must have BOTH width >= {minimum}px AND height >= {minimum * asset['height'] / asset['width']:.2f}px. Increasing box width without enough height does not enlarge the image. Recompose around the original image; never crop, replace or delete it.")


def validate_image_capacity(requirements, assets, canvas=(1280, 720)):
    """Reject impossible upright image minima; never choose a layout or reduce them."""
    for k in range(1, len(requirements) + 1):
        wide = [ref for ref, item in requirements.items() if item['minimum_display_width'] > canvas[0] / (k + 1)]
        height = sum(requirements[ref]['minimum_display_width'] * assets[ref]['height'] / assets[ref]['width'] for ref in wide)
        if height > k * canvas[1] + 1:
            raise ValueError(f'Image minima are geometrically impossible: at most {k} of {wide} fit across '
                             f'a {canvas[0]}px row, but their required heights sum to {height:.1f}px > {k}*{canvas[1]}px, '
                             'even before adding title or body text. Reassess necessary reading detail or return feasible=false; do not assert feasible=true with contradictory minima.')


def accepted_page_reference(job, page, material):
    """Independent registered reading evidence, matched against the complete page contract."""
    template = job / 'input/template.pptx'
    if not template.exists():
        return None
    digest = hashlib.sha256(template.read_bytes()).hexdigest()
    directory = ROOT / '.pptnative-runtime/accepted-pages' / digest
    index = directory / 'reference.json'
    if not index.exists():
        return None
    record = read(index)
    for candidate in record['pages']:
        if (candidate['title'] == page['title'] and candidate['notes'].strip() == page.get('notes', '').strip()
                and candidate['purpose'].strip() == page.get('purpose', '').strip()
                and candidate['facts'] == [material['facts'][r] for r in page['point_ids']]
                and candidate['asset_sha256'] == [material['assets'][r]['sha256'] for r in page['asset_ids']]):
            preview = directory / candidate['preview']
            if preview.exists() and hashlib.sha256(preview.read_bytes()).hexdigest() == candidate['preview_sha256']:
                return {'preview': str(preview), 'basis': record['basis'], 'job': record['source_job'],
                        'version': record['source_version'], 'actual_image_sizes': candidate['actual_image_sizes']}
    return None


def asset_readability(job, material, feedback=None):
    """Ask the vision API about all images on a page together, without fixing layout."""
    def inspect(page):
        assets = {ref: material['assets'][ref] for ref in page['asset_ids']}
        payload = {'page': page, 'canvas': {'width': 1280, 'height': 720},
                   'facts': {ref: material['facts'][ref] for ref in page['point_ids']},
                   'assets': assets}
        reference = accepted_page_reference(job, page, material)
        if reference:
            payload['user_accepted_reference'] = reference
        directory = job / 'input/asset-analysis-v4'
        if not reference and (feedback or {}).get(page['id']):
            payload['actual_capacity_failure'] = feedback[page['id']]
            prior = sorted(directory.glob(f"readability-{page['id']}-*.response.json"),
                           key=lambda p: p.stat().st_mtime, reverse=True)
            if prior:
                payload['previous_assessment'] = read(prior[0])
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
        stage = f"readability-{page['id']}-{digest}"
        cached = directory / f'{stage}.response.json'
        images = [Path(asset['path']) for asset in assets.values()]
        labels = [f"Fixed original asset {ref}; {asset.get('width')}x{asset.get('height')} pixels"
                  for ref, asset in assets.items()]
        if (len(images) > 1 or reference) and not cached.exists():
            # A labelled inventory is evidence for the vision API, never a deck
            # layout. Some multimodal backends merge separate image attachments
            # into the first image's identity; one explicit inventory avoids it.
            from PIL import Image, ImageDraw
            directory.mkdir(parents=True, exist_ok=True)
            columns = 2
            rows = math.ceil(len(images) / columns)
            inventory_height = rows * 540
            sheet = Image.new('RGB', (1280, inventory_height + (760 if reference else 0)), 'white')
            draw = ImageDraw.Draw(sheet)
            for i, (ref, asset) in enumerate(assets.items()):
                x, y = (i % columns) * 640, (i // columns) * 540
                draw.text((x + 20, y + 12), f"ASSET {ref} | {asset.get('width')} x {asset.get('height')} px", fill='black')
                with Image.open(asset['path']) as source:
                    preview = source.convert('RGB')
                    preview.thumbnail((600, 490))
                    sheet.paste(preview, (x + (640 - preview.width) // 2, y + 40 + (490 - preview.height) // 2))
            if reference:
                draw.text((20, inventory_height + 8), 'USER-CONFIRMED OUTPUT: identical facts and original assets; reading-quality reference only, NOT a new original asset.', fill='black')
                with Image.open(reference['preview']) as accepted:
                    accepted = accepted.convert('RGB')
                    accepted.thumbnail((1280, 720))
                    sheet.paste(accepted, (0, inventory_height + 40))
            inventory = directory / 'inventories' / f'{stage}.jpg'
            inventory.parent.mkdir(parents=True, exist_ok=True)
            sheet.save(inventory, quality=95)
            images = [inventory]
            labels = [f"ASSET INVENTORY: {len(assets)} independent original images, each labelled with its asset_id. This is NOT a slide layout and NOT a composite original image."]
        def validate(data):
            if type(data['feasible']) is not bool or not isinstance(data['reason'], str):
                raise ValueError('feasible must be boolean and reason must explain the page capacity judgment')
            if not data['feasible']:
                return
            if set(data['assets']) != set(assets):
                raise ValueError('Assess every fixed image exactly once using the original asset IDs')
            for item in data['assets'].values():
                width = item['minimum_display_width']
                if type(width) not in (int, float) or not 1 <= width <= 1160:
                    raise ValueError('minimum_display_width must be numeric from 1 to 1160')
                if not isinstance(item['guidance'], str) or not isinstance(item['source_limitations'], list):
                    raise ValueError('Each asset requires guidance as a JSON string and source_limitations as a JSON ARRAY of strings; use [] when none, never a single string')
            validate_image_capacity(data['assets'], assets)
        if cached.exists():
            try:
                validate(read(cached))
            except ValueError as invalid:
                payload['invalid_cached_assessment'] = read(cached)
                payload['capacity_validation_error'] = str(invalid)
                cached.rename(cached.with_name(f'{stage}.{hashlib.sha256(str(invalid).encode()).hexdigest()[:8]}.invalid.json'))
        result = read(cached) if cached.exists() else ask(directory, stage, (
            '同时查看本页全部固定原图和全部教学事实，判断在1280×720一页中的实际阅读要求。'
            '返回feasible布尔值、reason说明、assets对象（键为asset_id）。每图含minimum_display_width、guidance、source_limitations。'
            'minimum_display_width必须为数字；guidance必须为字符串；source_limitations必须为字符串数组，无限制返回[]，不能返回单个字符串。'
            '每幅图之前有明确的asset_id标记，严格按标记逐图判断，不把多幅附件误当成第一张图的多个子图。'
            '多图输入是一张带ASSET编号的素材清单：每个编号格是一张独立原图。格子的排列仅用于核对身份，不是课件布局要求。'
            '如附USER-CONFIRMED OUTPUT，它是用户已验收的同一标题、同一事实、同一原图成品，代表本次实际阅读目标，'
            '不是新增素材或必须照抄的布局。结合这张真实页面和actual_image_sizes校准“必要”的阅读尺寸，'
            '不能把更大更清晰的偏好或逐刻度精读强加为教学目的；若认为仍有实质缺陷须具体解释。'
            '相同教学目的的已验收阅读目标保持稳定：新候选布局不好应修候选，不以设计错误反推已验收参考稿不可行或增加新要求。'
            '区分代码截图、带文字流程图、坐标图和装饰照片。根据真实文字密度与需要读出的细节，给出合理最小完整显示宽度（1-1160像素）。'
            '该宽度针对图片按原比例contain后的实际可见宽度，不是外框宽度。不要改写或替换原图。'
            '必须结合本页教学目的、全部图片数量和正文判断，不把每张多图比较面板当成独占全页。'
            '若任务仅比较曲线趋势，重点保护趋势与坐标完整，不要求读出每个微小数据点；代码讲解则保留必要代码可辨性。'
            '原图已有的模糊记录为source_limitations，不假设放大会增加原图细节。'
            '布局由后续制作API自由决定，不预设网格、左右栏或卡片。先判断所有要求能否同时成立；'
            '若给出actual_capacity_failure，前次尺寸建议已在真实候选校验中遇到容量冲突。结合previous_assessment、'
            '每张图的真实宽高比、全部正文重新判断必要阅读尺寸；必须同时容纳标题、完整原图和全部事实。'
            '不要将“能看更细”误当教学目的的最低阅读要求，也不要给出相互冲突的最低尺寸却声称可行。'
            '如果保留全部事实与原图并达到必要可读性确实无法一页容纳，返回feasible=false并说明冲突，不虚构可行值、不删图、不擅自拆页。'
        ), payload, visual=True, images=images, image_labels=labels,
            required=('feasible', 'reason', 'assets'), validate=validate)
        validate(result)
        if not result['feasible']:
            raise ValueError(f"[CAPACITY] {page['id']}: {result['reason']}")
        return page['id'], result['assets']
    result = {}
    for page_id, assessed in parallel([p for p in material['pages'] if p['asset_ids']], inspect):
        result[page_id] = assessed
    return result


def workspace_page_content(svg_text, choice, material):
    """Deterministic completeness of one authored workspace page.

    A local-recompose page starts from a cleared canvas, so it is only exportable
    once the fixed images and some visible text are actually present. Returning an
    empty canvas must never reach the exporter (it would become a blank slide).
    """
    if choice["edit_mode"] == "referenced":
        return True
    if choice["edit_mode"] == "template-fill":
        return True
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError:
        return False
    images = [n for n in root.iter() if n.tag.rsplit("}", 1)[-1] == "image"]
    hrefs = {n.get("href", n.get("{http://www.w3.org/1999/xlink}href")) for n in images}
    expected = {"../images/" + Path(material["assets"][ref]["path"]).name
                for ref in choice.get("asset_ids", [])}
    texts = "".join("".join(n.itertext()) for n in root.iter()
                    if n.tag.rsplit("}", 1)[-1] == "text").strip()
    return expected <= hrefs and bool(texts)


def page_needs_authoring(workspace, choice, material):
    path = workspace / "authoring-svg-flat" / choice["svg"]
    if not path.exists() or choice.get('pending_author') is True:
        return True
    current = path.read_text(encoding='utf-8')
    if choice['edit_mode'] == 'template-fill' and choice.get('pending_author') is not False:
        # Legacy workspaces have no completion flag. An untouched source SVG is
        # not an authored text page, even though it visibly contains template text.
        base = workspace.parent / 'authoring-bases' / choice['svg']
        if not base.exists() or current == base.read_text(encoding='utf-8'):
            return True
    return not workspace_page_content(current, choice, material)


def failed_page_ids(pages, message):
    """A global transport/filesystem error does not invalidate every authored page."""
    return {p['id'] for p in pages if re.search(
        re.escape(p['svg']) + r'(?::|\s+-\s+(?:Error|Failed)\b)', message, re.I) or re.search(
        r'\bError:\s*(?:[^\s]*[/\\])?' + re.escape(p['svg']) + r'\b', message, re.I) or re.search(
        r'\b' + re.escape(p['id']) + r'(?:-[0-9a-f]{8,64})?(?=\s+failed\b|:|\.a\d)', message)}


ANIMATION_TARGET_ERROR = "removed source animation target(s)"


def reconcile_animation_targets(version, workspace, mapping, message):
    """Disable source animation only for the output pages the exporter rejected.

    The upstream exporter stops at the first slide whose edited SVG no longer
    carries the source timing targets, so recovering one page per build attempt
    exhausts the retry budget. This applies the same documented recovery to the
    rejected pages and reports success, leaving every other page's animation intact.
    """
    failed = {name for name in re.findall(r"\[Slide \d+/\d+\]\s+([^\s]+\.svg)\s+-\s+Error", message)}
    if not failed:
        failed = set(re.findall(r"\b([A-Za-z0-9_-]+\.svg)\b", message))
    pages = [p for p in mapping["pages"] if p["svg"] in failed and p["edit_mode"] != "referenced"]
    if not pages:
        return False
    animations = read(workspace / "animations.json")
    changed = []
    for page in pages:
        stem = Path(page["svg"]).stem
        current = (animations.setdefault("slides", {}).get(stem) or {}).get("animation") or {}
        if current.get("effect") == "none":
            continue
        animations["slides"][stem] = {"animation": {"effect": "none"}}
        changed.append(page["id"])
        limitation = (f"{page['id']}: source animation build disabled because the edited native copy no longer "
                      "carries the source timing targets (original round-trip recovery policy; text, images and "
                      "geometry are unchanged).")
        if limitation not in mapping["limitations"]:
            mapping["limitations"].append(limitation)
    if not changed:
        return False
    save(workspace / "animations.json", animations)
    save(version / "native-workspace.json", mapping)
    return True


def export_roundtrip(version, workspace, mapping):
    """Run the unchanged exporter, reconciling animation targets page by page."""
    budget = max(1, len(mapping["pages"]))
    for _ in range(budget):
        try:
            return run_upstream(version, "svg_to_pptx.py", [workspace, "--roundtrip"], "export.log")
        except ValueError as error:
            if ANIMATION_TARGET_ERROR not in str(error):
                raise
            if not reconcile_animation_targets(version, workspace, mapping, str(error)):
                raise
    raise ValueError(f"Upstream svg_to_pptx.py failed: animation targets still unresolved after {budget} attempts")


def fresh_canvas(svg):
    root = ET.fromstring(svg)
    def clear(parent):
        for node in list(parent):
            ref = node.get("data-pptx-source-ref", "")
            if ref.startswith(("master:", "layout:")):
                continue
            if ref.startswith("slide:") or (not ref and node.tag.rsplit("}", 1)[-1] == "text"):
                parent.remove(node)
            else:
                clear(node)
    clear(root)
    return ET.tostring(root, encoding="unicode")


def normalize_svg_paint(svg):
    """Normalise SVG attributes that are valid per spec but rejected by the exporter.

    ``stroke-dasharray="4"`` (a single dash length) is legal SVG and means a 4/4
    pattern; the native converter only accepts "none" or two numbers. Duplicating
    the single value keeps the exact painted result and avoids a false export error.
    """
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return svg
    changed = False
    for node in root.iter():
        value = node.get("stroke-dasharray")
        if not value:
            continue
        parts = [part for part in re.split(r"[\s,]+", value.strip()) if part]
        if len(parts) == 1 and re.fullmatch(r"\d*\.?\d+", parts[0]):
            node.set("stroke-dasharray", f"{parts[0]} {parts[0]}")
            changed = True
    return ET.tostring(root, encoding="unicode") if changed else svg


def runtime_ids(root):
    """Supply invisible converter IDs; never change authored paint or geometry."""
    bounds = list(map(float, root.get("viewBox", "0 0 1280 720").split()))
    backgrounds = [n for n in root if n.tag.rsplit("}", 1)[-1] == "rect"
                   and not n.get("data-pptx-source-ref") and not n.get("transform")
                   and not float(n.get("rx", 0)) and not float(n.get("ry", 0))
                   and float(n.get("opacity", 1)) == 1 and float(n.get("fill-opacity", 1)) == 1
                   and re.fullmatch(r"#[0-9A-Fa-f]{6}", n.get("fill", ""))
                   and [float(n.get(k, 0)) for k in ("x", "y", "width", "height")] == bounds]
    # Earlier opaque canvases are completely occluded: removing them preserves pixels.
    for node in backgrounds[:-1]:
        root.remove(node)
    if backgrounds:
        # The native converter treats a full-canvas solid rect as a slide background
        # and rejects it when the authored order puts it after other slide content.
        # Re-inserting it behind the content keeps the rendered pixels identical.
        background = backgrounds[-1]
        rest = list(root)
        position = 0
        for index, node in enumerate(rest):
            ref = node.get("data-pptx-source-ref", "")
            if ref.startswith(("master:", "layout:")) or node.tag.rsplit("}", 1)[-1] in (
                    "defs", "metadata", "title", "desc"):
                position = index + 1
        if rest.index(background) != position:
            root.remove(background)
            root.insert(position, background)
    used = {n.get("id") for n in root.iter() if n.get("id")}
    for index, node in enumerate(root):
        if node.tag.rsplit("}", 1)[-1] in ("defs", "metadata", "title", "desc") or node.get("id"):
            continue
        name = f"pptnative-node-{index}"
        while name in used:
            name += "x"
        node.set("id", name)
        used.add(name)
    return root


def content_nodes(content, base):
    wrapped = f'<svg xmlns="{SVG}" xmlns:xlink="http://www.w3.org/1999/xlink">{content}</svg>'
    try:
        wrapper = ET.fromstring(wrapped)
    except ET.ParseError as error:
        line, column = error.position
        excerpt = wrapped.splitlines()[line-1][max(0,column-150):column+150]
        raise ValueError(f"content_svg malformed XML: {error}. Near: {excerpt!r}. SVG rect/image/path are geometry, not containers: self-close them and put text beside them within a g container. Return a complete corrected fragment.") from error
    viewbox = ET.fromstring(base).get("viewBox")
    for node in wrapper:
        if node.tag == f"{{{SVG}}}svg":
            if node.get("viewBox", viewbox) != viewbox or float(node.get("x", 0)) or float(node.get("y", 0)):
                raise ValueError(f"Authored content must use canonical groups, not nested SVG crop viewports. "
                                 f"Return ONLY the inner elements, without an outer <svg>. Source viewBox={viewbox!r}; "
                                 f"received outer attributes={dict(node.attrib)!r}. Do not round the source canvas or add a new viewport. "
                                 "Remove the outer svg wrapper and return its children with their original coordinates.")
            node.tag = f"{{{SVG}}}g"
            for key in ("viewBox", "width", "height", "version"):
                node.attrib.pop(key, None)
    if any(n.tag == f"{{{SVG}}}svg" for node in wrapper for n in node.iter()):
        raise ValueError("Return inner canonical SVG content; nested SVG viewports are unsupported for new authoring")
    return list(wrapper)


def apply_text_edits(svg, edits):
    fragments = TEXT.findall(svg)
    original_slots = slots(svg)
    replacements = {e['index']:e['xml'] for e in edits}
    frame_errors=[]
    for slot in original_slots:
        indices = slot.get('shared_native_frame_indices', [slot['index']])
        if slot['index'] != min(indices) or len(indices)<2 or not any(i in replacements for i in indices):
            continue
        active=[]
        for index in indices:
            xml=replacements.get(index,fragments[index])
            if xml and re.sub('<[^>]+>','',xml).strip():
                active.append(index)
        if len(active)>1:
            frame_errors.append(f'Text fragments {indices} are the same native textbox, not independent boxes. Keep one active text root at first_fragment_index={slot["first_fragment_index"]} and explicitly clear all other sibling fragments. Put all paragraphs/tspan lines inside that root. Active={active}')
    if frame_errors:
        raise ValueError('; '.join(frame_errors))
    used = set()
    for edit in edits:
        index = edit["index"]
        if type(index) is not int or index not in range(len(fragments)) or index in used:
            raise ValueError("Text edit index must identify one original slot exactly once")
        used.add(index)
        replacement = edit["xml"]
        if replacement:
            try:
                wrapped = ET.fromstring(f'<svg xmlns="{SVG}" xmlns:xlink="http://www.w3.org/1999/xlink">{replacement}</svg>')
            except ET.ParseError as error:
                raise ValueError(f'Text fragment index {index} has malformed XML: {error}; replacement={replacement!r}. Return balanced text/tspan markup for this fragment.') from error
            if len(wrapped) != 1 or wrapped[0].tag.rsplit("}", 1)[-1] != "text":
                raise ValueError("Template fill can replace only the existing text node; no hiding panels or new objects")
            original = ET.fromstring(f'<svg xmlns="{SVG}">{fragments[index]}</svg>')[0]
            node = wrapped[0]
            if any(n.tag.rsplit("}", 1)[-1] not in ("text", "tspan") for n in node.iter()):
                raise ValueError("Template text can contain only text/tspan, not new graphics")
            for key in ("x", "y", "transform", "text-anchor"):
                if node.get(key) != original.get(key):
                    raise ValueError(f"Text slot index {index}: immutable {key} must remain {original.get(key)!r}, received {node.get(key)!r}. Copy the exact original root attribute; only text/tspan content may reflow.")
            old_size, new_size = original.get("font-size"), node.get("font-size")
            if old_size and new_size:
                old_size, new_size = float(old_size), float(new_size)
                if not old_size * .9 <= new_size <= old_size * 1.05:
                    raise ValueError(
                        f"Template font adjustment exceeds the bounded source-relative range: "
                        f"slot {index} source font-size={old_size:g}, requested={new_size:g}; "
                        f"allowed {old_size * .9:.2f}-{old_size * 1.05:.2f}. Keep the source size or "
                        "reflow inside it; do not shrink below 90% of the source slot.")
                shared = original_slots[index].get('shared_native_frame_indices',[index])
                source_sizes = {float(n.get('font-size')) for i in shared for n in ET.fromstring(f'<svg xmlns="{SVG}">{fragments[i]}</svg>').iter() if n.get('font-size')}
                for child in node.iter():
                    if child.get("font-size") and not any(s * .9 <= float(child.get("font-size")) <= s * 1.05 for s in source_sizes):
                        allowed = sorted((round(s * .9, 2), round(s * 1.05, 2)) for s in source_sizes)
                        raise ValueError(
                            f"Nested text cannot bypass the bounded source font range: slot {index} "
                            f"requested {child.get('font-size')}, allowed ranges {allowed}.")
        if svg.count(fragments[index]) != 1:
            raise ValueError("Original text fragment is not unique")
        svg = svg.replace(fragments[index], replacement, 1)
    ET.fromstring(svg)
    return svg


def slots(svg):
    root = ET.fromstring(svg)
    parents = {id(child): node for node in root.iter() for child in node}
    text_nodes = [n for n in root.iter() if n.tag.rsplit("}", 1)[-1] == "text"]
    fragments = TEXT.findall(svg)
    result = []
    for i, node in enumerate(text_nodes):
        owner = parents.get(id(node))
        ancestors = []
        while owner is not None:
            ancestors.append({k: v for k, v in owner.attrib.items() if k in
                              ("id", "transform", "data-pptx-frame", "data-pptx-bounds", "data-pptx-source-ref")})
            owner = parents.get(id(owner))
        result.append({"index": i, "text": "".join(node.itertext()), "attributes": dict(node.attrib),
                       "ancestors": ancestors, "xml": fragments[i]})
    grouped = {}
    for slot in result:
        frame = next((a for a in slot['ancestors'] if a.get('data-pptx-frame')), None)
        if frame:
            key = frame.get('data-pptx-source-ref', frame['data-pptx-frame'])
            grouped.setdefault(key, []).append(slot)
    for group in grouped.values():
        indices = [s['index'] for s in group]
        first = min(group, key=lambda s:float(s['attributes'].get('y', 0)))['index']
        for slot in group:
            slot.update(shared_native_frame_indices=indices, first_fragment_index=first)
    return result


def retained_notes(authored, page, material):
    return authored.rstrip() + "\n\n原始事实（固定来源）\n" + "\n".join(
        material["facts"][ref] for ref in page["point_ids"]) + "\n\n教师原备注\n" + page["notes"]


def merge_local_edits(previous, patch, allowed):
    updated=deepcopy(previous)
    changes={e['index']:e['xml'] for e in patch['patch_edits']}
    if not changes or set(changes)-allowed:
        raise ValueError(f'Capacity patch edits outside allowed indices {sorted(allowed)}')
    by_index={e['index']:e for e in updated['edits']}
    for index,xml in changes.items():
        by_index[index]={'index':index,'xml':xml}
    updated['edits']=list(by_index.values())
    return updated


def title_slots(svg, title):
    from .native import SKILL
    if str(SKILL / "scripts") not in sys.path:
        sys.path.insert(0, str(SKILL / "scripts"))
    from text_measure import measure_text
    available = slots(svg)
    # Page numbers and single decorative glyphs are drawn larger than real
    # headings; letting them set the reference size filtered out every genuine
    # title slot and burned the whole rematch budget.
    display = [s for s in available
               if len((s["text"] or "").strip()) > 1 and any(ch.isalpha() for ch in s["text"])]
    max_size = max((float(s["attributes"].get("font-size", 0)) for s in display), default=0)
    result = []
    for s in available:
        size = float(s["attributes"].get("font-size", 0))
        owner = next((a for a in s["ancestors"] if a.get("data-pptx-frame")), None)
        inherited = any((a.get("data-pptx-source-ref") or "").startswith(("master:", "layout:")) for a in s["ancestors"])
        if not owner or inherited or size < max_size * .75:
            continue
        width = float(owner["data-pptx-frame"].split()[2])
        height = float(owner["data-pptx-frame"].split()[3])
        measured = measure_text(title, size=size * .9, family="Microsoft YaHei", weight=s["attributes"].get("font-weight", "normal"))
        lines = math.ceil(measured / width) if width else 99
        rows = int(height // (size * 1.15))
        # A heading may wrap to two lines when its native frame is tall enough;
        # long Chinese titles never fit one line on these 16:9 templates.
        if lines <= 1 or (lines <= 2 and rows >= lines):
            result.append(s["index"])
    return result


def fit_text_to_frames(svg, indices=None):
    """Shrink edited text (within the 90% native floor) until it fits its frame.

    The model reliably lands 1-4% over the frame even after a retry, and both
    our gate and the upstream quality checker reject that. Fitting here keeps the
    result deterministic instead of rematching the whole page.
    """
    from .native import SKILL
    if str(SKILL / "scripts") not in sys.path:
        sys.path.insert(0, str(SKILL / "scripts"))
    from svg_quality.checker import SVGQualityChecker
    from svg_to_pptx.drawingml.text_properties import resolve_project_font_sizes, resolve_project_letter_spacings
    root = ET.fromstring(svg)
    parents = {id(child): node for node in root.iter() for child in node}
    for _ in range(3):
        sizes = resolve_project_font_sizes(root)
        spacing = resolve_project_letter_spacings(root, sizes)
        changed = False
        for index, node in enumerate(root.iter(f"{{{SVG}}}text")):
            if indices is not None and index not in indices:
                continue
            estimated = SVGQualityChecker._estimated_text_bounds(node, parents, sizes, spacing, include_headroom=True)
            _, frame, _, _ = SVGQualityChecker._roundtrip_text_frame(node, parents)
            metrics = SVGQualityChecker._bounds_overflow_metrics(estimated, frame) if estimated and frame else None
            if not metrics or metrics[1] <= 0:
                continue
            width = estimated[2] - estimated[0]
            allowed = frame[2] - frame[0]
            if width <= 0 or allowed <= 0:
                continue
            scale = max(.9, min(.995, allowed / width * .99))
            for target in (node, *[n for n in node.iter() if n is not node]):
                size = target.get("font-size")
                if size:
                    target.set("font-size", f"{float(size) * scale:.4f}")
                    changed = True
        if not changed:
            break
    return ET.tostring(root, encoding="unicode")


def strip_canvas_backgrounds(svg):
    """New full-canvas rects conflict with master/layout paint order; keep master art."""
    root = ET.fromstring(svg)
    try:
        bounds = [float(x) for x in root.get("viewBox", "0 0 1280 720").split()]
    except ValueError:
        return svg
    removed = False
    for node in list(root):
        if node.tag.rsplit("}", 1)[-1] != "rect" or node.get("data-pptx-source-ref"):
            continue
        try:
            box = [float(node.get(k, 0)) for k in ("x", "y", "width", "height")]
        except (TypeError, ValueError):
            continue
        if box == bounds and node.get("fill"):
            root.remove(node)
            removed = True
    return ET.tostring(root, encoding="unicode") if removed else svg


def fit_images_to_canvas(svg, margin=12):
    """Keep newly composed images inside the slide canvas without changing bytes."""
    root = ET.fromstring(svg)
    try:
        x0, y0, width, height = map(float, root.get("viewBox", "0 0 1280 720").split())
    except ValueError:
        return svg
    parents = {id(child): node for node in root.iter() for child in node}
    changed = False
    for image in root.iter(f"{{{SVG}}}image"):
        if image.get("data-pptx-source-ref"):
            continue
        parent = parents.get(id(image))
        if parent is not None and parent.tag == f"{{{SVG}}}svg":
            # Imported crop wrappers require the inner image at 0/0/1/1; clamp the
            # wrapper below instead of the crop content.
            continue
        try:
            x, y = float(image.get("x", 0)), float(image.get("y", 0))
            w, h = float(image.get("width", 0)), float(image.get("height", 0))
        except (TypeError, ValueError):
            continue
        if w <= 0 or h <= 0:
            continue
        ratio = min(1.0, (width - 2 * margin) / w, (height - 2 * margin) / h)
        if ratio < 1.0:
            w, h = w * ratio, h * ratio
            image.set("width", f"{w:.2f}")
            image.set("height", f"{h:.2f}")
            changed = True
        nx, ny = min(max(x, margin), width - w - margin), min(max(y, margin), height - h - margin)
        if (nx, ny) != (x, y):
            image.set("x", f"{nx:.2f}")
            image.set("y", f"{ny:.2f}")
            changed = True
    for wrapper in root.iter(f"{{{SVG}}}svg"):
        if wrapper is root or wrapper.get("data-pptx-source-ref"):
            continue
        try:
            x, y = float(wrapper.get("x", 0)), float(wrapper.get("y", 0))
            w, h = float(wrapper.get("width", 0)), float(wrapper.get("height", 0))
        except (TypeError, ValueError):
            continue
        if w <= 0 or h <= 0:
            continue
        ratio = min(1.0, (width - 2 * margin) / w, (height - 2 * margin) / h)
        if ratio < 1.0:
            w, h = w * ratio, h * ratio
            wrapper.set("width", f"{w:.2f}"); wrapper.set("height", f"{h:.2f}"); changed = True
        nx, ny = min(max(x, margin), width - w - margin), min(max(y, margin), height - h - margin)
        if (nx, ny) != (x, y):
            wrapper.set("x", f"{nx:.2f}"); wrapper.set("y", f"{ny:.2f}"); changed = True
    return ET.tostring(root, encoding="unicode") if changed else svg


def validate_text_capacity(svg, title, strict_vertical=False, baseline=None, indices=None, check_canvas=False, tolerance=0.0):
    from .native import SKILL
    if str(SKILL / "scripts") not in sys.path:
        sys.path.insert(0, str(SKILL / "scripts"))
    from svg_quality.checker import SVGQualityChecker
    from svg_to_pptx.drawingml.text_properties import resolve_project_font_sizes, resolve_project_letter_spacings
    root = ET.fromstring(svg)
    parents = {id(child): node for node in root.iter() for child in node}
    sizes = resolve_project_font_sizes(root)
    spacing = resolve_project_letter_spacings(root, sizes)
    original_vertical = {}
    if baseline:
        old_root = ET.fromstring(baseline)
        old_parents = {id(c): n for n in old_root.iter() for c in n}
        old_sizes = resolve_project_font_sizes(old_root)
        old_spacing = resolve_project_letter_spacings(old_root, old_sizes)
        for n in old_root.iter(f"{{{SVG}}}text"):
            bounds = SVGQualityChecker._estimated_text_bounds(n, old_parents, old_sizes, old_spacing)
            _, frame, _, _ = SVGQualityChecker._roundtrip_text_frame(n, old_parents)
            m = SVGQualityChecker._bounds_overflow_metrics(bounds, frame) if bounds and frame else None
            if m:
                original_vertical[SVGQualityChecker._roundtrip_frame_source_ref(n, old_parents)] = m[2]
    found = False
    errors = []
    for index, node in enumerate(root.iter(f"{{{SVG}}}text")):
        if indices is not None and index not in indices:
            continue
        text = "".join(node.itertext())
        if "".join(title.split()) in "".join(text.split()):
            found = True
        if not text.strip():
            continue
        estimated = SVGQualityChecker._estimated_text_bounds(node, parents, sizes, spacing, include_headroom=True)
        label, frame, _, _ = SVGQualityChecker._roundtrip_text_frame(node, parents)
        if check_canvas and estimated:
            owner, inherited = node, False
            while owner is not None:
                inherited |= owner.get('data-pptx-source-ref', '').startswith(('master:', 'layout:'))
                owner = parents.get(id(owner))
            x, y, w, h = map(float, root.get('viewBox', '0 0 1280 720').split())
            a, b, c, d = estimated
            if not inherited and (a < x - 2 or b < y - 2 or c > x + w + 2 or d > y + h + 2):
                errors.append(f'Object {label} text {text!r} is outside the canvas; bounds={estimated}, canvas={(x,y,w,h)}')
        metrics = SVGQualityChecker._bounds_overflow_metrics(estimated, frame) if estimated and frame else None
        if metrics and metrics[1] > tolerance:
            errors.append(f"Object {label} text {text!r} exceeds its native frame horizontally by {metrics[1]:.1%}; bounds={estimated}, frame={frame}")
        baseline_vertical = original_vertical.get(SVGQualityChecker._roundtrip_frame_source_ref(node, parents), 0)
        if metrics and strict_vertical and metrics[2] > baseline_vertical + .05:
            errors.append(f"Object {label} text {text!r} exceeds its authored frame vertically by {metrics[2]:.1%}; bounds={estimated}, frame={frame}")
    if not found:
        errors.append("Complete requested page title is missing from visible text; do not replace it with a generic label")
    if errors:
        raise ValueError("[LAYOUT] " + "; ".join(errors) + ". Horizontal overflow: shorten or split into lines that fit. "
                         "Vertical overflow: reduce tspan line count, using one line when width permits, or distribute meaning among other existing slots. "
                         "Do not preserve a two-line pattern inside a one-line source frame. Preserve all core meaning; rematch if incompatible. "
                         "The COMPLETE requested title must occupy a suitable heading slot, not a small label.")


def edit_title(version, page_id, base, old_title, new_title, notes):
    """Let the API edit the original heading only; all other SVG stays byte-identical."""
    fragments = slots(base)
    groups = {tuple(s.get('shared_native_frame_indices', [s['index']])) for s in fragments}
    matching = [group for group in groups if ''.join(
        ''.join(fragments[i]['text'].split()) for i in group) == ''.join(old_title.split())]
    if len(matching) != 1:
        raise ValueError(f'{page_id}: cannot uniquely locate the original title frame; no whole-page rewrite allowed')
    allowed = set(matching[0])
    def validate(data):
        if not data['edits'] or any(e['index'] not in allowed for e in data['edits']):
            raise ValueError(f'title edits must stay inside original title indices {sorted(allowed)}')
        candidate = apply_text_edits(base, data['edits'])
        visible = [''.join(n.itertext()) for n in ET.fromstring(candidate).iter(f'{{{SVG}}}text')]
        if new_title not in visible:
            raise ValueError('The exact new title must occupy the original heading')
        # Removing sibling fragments changes subsequent text indices. Check the
        # retained heading by its resulting index, never an unrelated body slot.
        title_indices = {i for i, text in enumerate(visible) if text == new_title}
        validate_text_capacity(candidate, new_title, strict_vertical=True, baseline=base, indices=title_indices)
    result = ask(version / 'api/title-edit', page_id,
        '只修改原生课件的标题。返回edits数组（index、xml）和rationale。'
        '仅可编辑给出的原标题碎片，按新标题逐字替换，保留根属性、原字体、颜色和几何。'
        '同一标题框只保留一个活跃text根，其余同框碎片清空。不得编辑正文、图片、背景、备注或其他页面。',
        {'old_title': old_title, 'new_title': new_title,
         'text_fragments': [fragments[i] for i in sorted(allowed)]},
        required=('edits', 'rationale'), validate=validate, timeout=600)
    return apply_text_edits(base, result['edits']), {**result, 'notes': notes, 'limitations': []}


def body_capacity(text_slots):
    """Approximate source paragraph capacity in full-width character units."""
    total = 0
    seen = set()
    for slot in text_slots:
        attributes = slot['attributes']
        if attributes.get('font-weight') in ('bold','700') or len(slot['text'].strip()) < 6:
            continue
        frame = next((a['data-pptx-frame'] for a in slot['ancestors'] if a.get('data-pptx-frame')), None)
        size = float(attributes.get('font-size', 0))
        if not frame or not size or frame in seen:
            continue
        seen.add(frame)
        _,_,width,height = map(float, frame.split())
        total += max(0,width-20) / size * max(0,math.floor((height-12)/(size*1.3)))
    return round(total, 1)


def catalog(version):
    directory = version / "workspace/authoring-svg-flat"
    result = []
    for path in sorted(directory.glob("slide_*.svg")):
        number = int(path.stem.split("_")[-1])
        svg = path.read_text(encoding="utf-8")
        item = {"number": number, "text_slots": [
            {"index": s["index"], "text": s["text"][:70], "attributes": s["attributes"], "ancestors": s["ancestors"]}
            for s in slots(svg)]}
        item['estimated_body_character_capacity'] = body_capacity(item['text_slots'])
        result.append(item)
    return result


def approved_template_catalog(template, pages=None, origin=None):
    """Reuse template evidence from a confirmed job, keyed by its exact PPTX bytes."""
    digest = hashlib.sha256(Path(template).read_bytes()).hexdigest()
    path = ROOT / '.pptnative-runtime/template-catalogs' / f'{digest}.json'
    if pages is not None:
        save(path, {'schema': 1, 'template_sha256': digest, 'origin': origin, 'pages': pages})
    if not path.exists():
        return None
    data = read(path)
    return data if data.get('schema') == 1 and data.get('template_sha256') == digest else None


def validated_vision(pages, numbers):
    """Reject a structurally broken vision batch.

    A model can emit a flattened page (its fields as loose strings) or drop the
    braces around an object; that still parses as JSON, so the batch must be
    checked here or the strings reach the layout matcher and crash it.
    """
    if not isinstance(pages, list) or len(pages) != len(numbers):
        raise ValueError(f"Vision pages must be a JSON array of {len(numbers)} objects")
    for expected, page in zip(numbers, pages):
        if not isinstance(page, dict):
            raise ValueError(f"Vision page {expected} must be a JSON object, got {type(page).__name__}")
        missing = [field for field in VISION_FIELDS if field not in page]
        if missing:
            raise ValueError(f"Vision page {expected} is missing fields: {missing}")
        if page["number"] != expected:
            raise ValueError(f"Vision page order mismatch: expected {expected}, got {page['number']!r}")
        if page["capacity"] not in ("low", "medium", "high") or not isinstance(page["text_fill_compatible"], bool):
            raise ValueError(f"Vision page {expected} has invalid capacity/text_fill_compatible")
    return pages


def complete_vision(pages, items):
    return (isinstance(pages, list) and all(isinstance(p, dict) for p in pages)
            and {p.get('number') for p in pages} == {p['number'] for p in items})


def visual_catalog(job, version, items):
    approved = approved_template_catalog(job / 'input/template.pptx')
    if approved and complete_vision(approved.get('pages'), items):
        save(version / 'template-vision-v2.json', approved['pages'])
        save(version / 'template-vision-origin.json', {k: v for k, v in approved.items() if k != 'pages'})
        return approved['pages']
    if (version / "template-vision-v2.json").exists():
        cached = read(version / "template-vision-v2.json")
        if complete_vision(cached, items):
            return cached
        # A cache written from a broken batch would replay the same crash; rebuild it.
        shutil.move(str(version / "template-vision-v2.json"), str(version / "template-vision-invalid.json"))
    batches = [items[i:i + 5] for i in range(0, len(items), 5)]
    def inspect(batch):
        numbers = [p["number"] for p in batch]
        return ask(version / "api/template-vision", f"pages-{numbers[0]}", (
            "观察输入的真实PPT模板页，按输入顺序输出pages数组，每项number、role、layout_logic、"
            "capacity(low/medium/high)、safe_text_areas、decoration_conflicts、semantic_neutrality。"
            "另含text_fill_compatible布尔值：判断仅能替换现有文字、不能移动或修改图形的编辑器，"
            "能否用此页承载多条普通解释性要点。若只能放短章节标题、主要依赖指标数值/图表数据、"
            "或非文字图形具有强烈且不通用的业务含义，则为false；常规说明/列表/流程正文布局为true。"
            "这是供文字模型匹配模板的视觉证据，描述真实可用文字区域、遮挡风险和图文关系，"
            "不要拟本次课件内容。不以文本框数量代替实际容量；指出艺术封面是否不适合多条正文。"
        ), {"source_pages": batch}, visual=True,
            images=[job / "source-preview" / f"template_p{n}.png" for n in numbers], required=("pages",),
            validate=lambda data: validated_vision(data["pages"], numbers))
    result = [p for batch in parallel(batches, inspect) for p in batch["pages"]]
    if not complete_vision(result, items):
        raise ValueError("Vision catalog lost or duplicated template pages")
    result.sort(key=lambda page: page["number"])
    save(version / "template-vision-v2.json", result)
    return result


def preview_histogram(job, number):
    """Coarse colour histogram of a template page, used to keep one deck look."""
    path = job / "source-preview" / f"template_p{number}.png"
    if not path.exists():
        return None
    with Image.open(path) as source:
        image = source.convert("RGB").resize((64, 36))
    histogram = [0] * 64
    for red, green, blue in image.getdata():
        histogram[(red // 64) * 16 + (green // 64) * 4 + (blue // 64)] += 1
    total = sum(histogram) or 1
    return [value / total for value in histogram]


def style_cluster(job, numbers):
    """Largest group of source layouts that share the deck's dominant palette.

    Template sets mix unrelated designs; authoring several pages from different
    colour families produces a deck whose pages do not look alike. Pick the
    biggest visually coherent cluster instead of trusting body capacity alone.
    """
    histograms = {n: h for n in numbers if (h := preview_histogram(job, n)) is not None}
    if len(histograms) < 4:
        return set(numbers)

    def similarity(a, b):
        return sum(min(x, y) for x, y in zip(a, b))

    cluster = set()
    for reference, reference_hist in histograms.items():
        group = {n for n, h in histograms.items() if similarity(reference_hist, h) >= .6}
        if len(group) > len(cluster):
            cluster = group
    # Never starve routing: a small or atypical template keeps every eligible page.
    return cluster if len(cluster) >= max(4, len(numbers) * .4) else set(numbers)


def layout_catalog(items):
    """Compact text-slot evidence for the layout matcher.

    The raw catalog keeps every SVG ancestor and the full text fragment for each
    slot. An 81-slide template turns that into a ~620 KB payload which the model
    gateway rejects with HTTP 400, so send only what page selection needs.
    """
    result = []
    for item in items:
        slots_ = []
        for slot in item["text_slots"][:8]:
            frame = next((a.get("data-pptx-frame") for a in slot.get("ancestors", [])
                          if a.get("data-pptx-frame")), "")
            slots_.append({"text": (slot.get("text") or "")[:40],
                           "font_size": (slot.get("attributes") or {}).get("font-size", ""),
                           "frame": frame})
        result.append({"number": item["number"], "slot_count": len(item["text_slots"]),
                       "estimated_body_character_capacity": item.get("estimated_body_character_capacity"),
                       "text_slots": slots_})
    return result


def stage_fixed_assets(workspace, assets):
    directory = workspace / 'images'
    directory.mkdir(parents=True, exist_ok=True)
    for asset in assets.values():
        target = directory / Path(asset['path']).name
        if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest() != asset['sha256']:
            shutil.copyfile(asset['path'], target)
        if hashlib.sha256(target.read_bytes()).hexdigest() != asset['sha256']:
            raise ValueError('Fixed original-image SHA256 mismatch during staging')


def prepare(job, version, contract, source, parent=None):
    from .native import SKILL
    material = read(job / 'input/material-pool.json')
    if (version / "native-workspace.json").exists() and read(version / "native-workspace.json").get("authoring_schema") == 2:
        stage_fixed_assets(version / 'workspace', material['assets'])
        return
    workspace = version / "workspace"
    run_upstream(version, "attribution_guard.py", [], "author-attribution.log")
    if not (workspace / "authoring-svg-flat/authoring_summary.json").exists():
        run_upstream(version, "pptx_to_svg.py", [source, "-o", workspace, "--inheritance-mode", "both", "--roundtrip"], "author-import.log")
    directory = workspace / "authoring-svg-flat"
    items = catalog(version)
    parent_roster = read(version / "parent-roster.json") if parent else []
    old = {p["id"]: p["current_source_slide"] for p in parent_roster}
    changed = [p for p in contract["pages"] if p["id"] not in old or p["id"] in contract.get("request", {}).get("page_ids", [])]
    vision = visual_catalog(job, version, items) if not parent else []
    allowed = {p["number"] for p in vision if p.get("text_fill_compatible") is True and p.get("capacity") != "low"}
    if not parent and not allowed:
        raise ValueError("Qwen found no compatible native text layouts; cannot silently recompose text pages")
    if not parent:
        # Keep every text page inside the template's dominant colour family; a
        # capacity-only pick can land on an unrelated dark business page.
        consistent = allowed & style_cluster(job, sorted(allowed))
        if consistent:
            allowed = consistent
    def heading_fits(number, title):
        return bool(title_slots((directory / f"slide_{number:02}.svg").read_text(encoding="utf-8"), title))
    # Text pages can only use a layout whose real heading slot holds the full title;
    # selecting on body capacity alone lands on pages without any heading and burns
    # the rematch budget.
    title_eligible = {p["id"]: sorted(n for n in allowed if heading_fits(n, p["title"]))
                      for p in changed if not p["asset_ids"]} if not parent else {}
    # A template may have headings too small for any full lesson title (calligraphic
    # decks often cap them at ~26px). Rather than failing the deck, that page is
    # composed inside the template style and the limitation is recorded.
    forced_recompose = {p["id"] for p in changed if not p["asset_ids"] and not title_eligible.get(p["id"])} if not parent else set()
    def validate_choices(data):
        pages = data.get("pages")
        if not isinstance(pages, list) or not all(isinstance(p, dict) for p in pages):
            raise ValueError("Layout selection must be a JSON array of page objects")
        if any(not isinstance(p.get("id"), str) or type(p.get("source_slide")) is not int for p in pages):
            raise ValueError("Each layout selection needs a string id and an integer source_slide")
        by_id = {p["id"]: p for p in changed}
        if {p["id"] for p in data["pages"]} != set(by_id):
            raise ValueError("Layout selection must cover each requested page exactly once")
        for p in data["pages"]:
            if not parent and not by_id[p["id"]]["asset_ids"] and p["source_slide"] not in allowed:
                raise ValueError(f"{p['id']}: source layout is incompatible with constrained text filling; choose from {sorted(allowed)}")
    title_only = parent and contract.get('request', {}).get('kind') == 'set_title'
    choice = {'pages': [{'id': p['id'], 'source_slide': old[p['id']],
                         'rationale': 'Title-only revision uses the original delivered page'} for p in changed]} if title_only else ask(version / "api", "native-layout-match", (
        "你是PPTNative规划器，在原版PPT Master已导入的真实页面库中选择骨架。"
        "返回pages数组，每项id、source_slide、rationale。仅为requested_pages选页。"
        "图片页已被流程强制独立重构，选风格兼容的源骨架即可；文字页必须沿用真实文字槽位和原图形几何。"
        "综合Qwen实际视觉证据与槽位宽高/字号，按内容逻辑及容量选页，不能仅匹配框数。"
        "正文多时避免仅能承载短标题的艺术封面，避免内容与装饰语义不相关、正文遮挡的布局。"
        "可以等义压缩上屏表达，核心事实不得丢；不足时另选较大真实版式，不靠硬塞或重排文字页解决。"
        "修订只选择本次改动页，保持其他已交付页不动。不要编写SVG。"
    ), {"requested_pages": changed, "facts": material["facts"], "catalog": layout_catalog(items), "visual_evidence": vision,
        "eligible_text_source_slides": sorted(allowed), "title_eligible_source_slides": title_eligible,
        "parent_roster": parent_roster, "request": contract.get("request")}, required=("pages",), timeout=600,
        validate=validate_choices)
    selected = {p["id"]: p for p in choice["pages"]}
    if set(selected) != {p["id"] for p in changed}:
        raise ValueError("Layout match changed the requested page identities")
    # The matcher may ignore the heading constraint and pick a layout that cannot
    # hold the title; correcting it here beats failing the whole deck after retries.
    capacity = {p["number"]: p.get("estimated_body_character_capacity") or 0 for p in items}
    for page in changed:
        options = title_eligible.get(page["id"]) or []
        pick = selected[page["id"]]
        if options and pick["source_slide"] not in options:
            best = max(options, key=lambda n: capacity.get(n, 0))
            pick.update(source_slide=best, rationale=(pick.get("rationale", "") +
                        f" [corrected to source {best}: only layouts whose heading holds the full title are usable]").strip())
    (version / "authoring-bases").mkdir(exist_ok=True)
    mapping, routes = [], []
    for page in contract["pages"]:
        if page["id"] in old and page["id"] not in selected:
            number = old[page["id"]]
            mapping.append({"id": page["id"], "source_slide": number, "svg": f"slide_{number:02}.svg",
                            "edit_mode": "referenced", "rationale": "Unchanged delivered page referenced natively"})
            continue
        number = selected[page["id"]]["source_slide"]
        if type(number) is not int or number not in range(1, len(items) + 1):
            raise ValueError("Invalid real template page selection")
        mode = 'template-fill' if title_only else ('local-recompose' if page["id"] in forced_recompose else route_for(page))
        svg = (directory / f"slide_{number:02}.svg").read_text(encoding="utf-8")
        if mode == "local-recompose":
            svg = fresh_canvas(svg)
        name = re.sub(r"[^A-Za-z0-9_-]", "_", page["id"]) + ".svg"
        (directory / name).write_text(svg, encoding="utf-8")
        (version / "authoring-bases" / name).write_text(svg, encoding="utf-8")
        rationale = selected[page["id"]]["rationale"]
        if page["id"] in forced_recompose:
            rationale = (rationale + " [本地重排：模板标题槽放不下完整标题]").strip()
        mapping.append({"id": page["id"], "source_slide": number, "svg": name, "edit_mode": mode,
                        "rationale": rationale, "pending_author": True})
        routes.append({"id": page["id"], "mode": mode, "asset_ids": page["asset_ids"],
                       "source_slide": number, "empty_slide_local_canvas": mode == "local-recompose"})
    save(version / "page-routing.json", routes)
    save(workspace / "page_plan.json", {"schema": "ppt-master.roundtrip-page-plan.v1", "pages": [
        {"source_slide": p["source_slide"], "svg": p["svg"]} for p in mapping]})
    notes = workspace / "notes"
    notes.mkdir(exist_ok=True)
    for page in contract["pages"]:
        match = next(p for p in mapping if p["id"] == page["id"])
        if match["edit_mode"] != "referenced":
            (notes / (Path(match["svg"]).stem + ".md")).write_text("", encoding="utf-8")
    stage_fixed_assets(workspace, material['assets'])
    if (version / "native-workspace.json").exists():
        save(version / "prior-routing.json", read(version / "native-workspace.json"))
    save(version / "native-workspace.json", {"workspace": str(workspace), "pages": mapping, "limitations": [], "authoring_schema": 2})


def failure_text_indices(svg, error):
    owners = set(re.findall(r'Object <g id="([^"]+)"', str(error)))
    fragments = slots(svg)
    indices = {s['index'] for s in fragments if any(a.get('id') in owners for a in s['ancestors'])}
    indices |= {int(i) for i in re.findall(r'\bslot(?: index)? (\d+)', str(error)) if int(i) < len(fragments)}
    return indices


def repair_local_text(version, page, material, svg, notes, findings, assets):
    """Model-owned text repair on an already rendered composition; fixed images never move."""
    def validate(data):
        updated = apply_text_edits(svg, data['edits'])
        validate_asset_references([ET.fromstring(updated)], assets)
        validate_text_capacity(updated, page['title'], strict_vertical=True, baseline=svg, check_canvas=True)
        from .quality import semantic_coverage
        texts = [''.join(n.itertext()) for n in ET.fromstring(updated).iter() if n.tag.rsplit('}', 1)[-1] == 'text']
        _, missing = semantic_coverage(version / 'candidate-checks' / page['id'],
            {'native': True, 'pages': [page]}, material, [{'texts': texts}])
        if missing:
            raise ValueError('Text repair must preserve every teaching fact visibly: ' + '; '.join(x['evidence'] for x in missing))
    result = ask(version / 'api/local-text-repair', page['id'],
        '当前图片页已真实渲染，图片身份、几何与可读尺寸已经通过。本轮仅修复所列文案问题。'
        '返回edits数组（index、xml）、rationale、limitations。只能替换输入现有text节点；'
        '原根x/y/transform/anchor保持，不能新增图形、移动图片、改变图片尺寸或重构整页。'
        '保留完整合同标题与全部事实的核心含义、数字、否定条件和调用身份；若需改换行，必须在现有文字组边界内。'
        '对评审提到的枚举与量词以原事实为准，不能自行要求原文没有的重复字样。原备注由系统原样保留。',
        {'page': page, 'facts': {r: material['facts'][r] for r in page['point_ids']},
         'text_fragments': slots(svg), 'findings': findings},
        required=('edits', 'rationale', 'limitations'), validate=validate, timeout=600)
    result['notes'] = notes
    return apply_text_edits(svg, result['edits']), result


def run(job, version, contract, source, parent=None):
    from .native import SKILL
    check_cancel(job)
    prepare(job, version, contract, source, parent)
    check_cancel(job)
    workspace = version / "workspace"
    if not parent:
        # A resumed task may have been planned using a later, contradictory vision
        # classification. Keep that evidence, then use the confirmed template's
        # catalog for subsequent capacity rematching; valid authored pages stay.
        approved = approved_template_catalog(job / 'input/template.pptx')
        if approved:
            previous = version / 'template-vision-v2.json'
            if previous.exists() and read(previous) != approved['pages']:
                save(version / 'template-vision-before-approved-cache.json', read(previous))
            visual_catalog(job, version, catalog(version))
    mapping = read(version / "native-workspace.json")
    choices = {p["id"]: p for p in mapping["pages"]}
    material = read(job / "input/material-pool.json")
    title_only = parent and contract.get('request', {}).get('kind') == 'set_title'
    error = read(version / 'qa/export-validation.json') if (version / 'qa/export-validation.json').exists() else None
    capacity_feedback = {}
    if error and not error.get('resolved') and any(text in error.get('error', '') for text in
                                                  ('Image readability capacity', '[LAYOUT]', '[CAPACITY]', 'viewBox')):
        capacity_feedback = {pid: error['error'] for pid in failed_page_ids(mapping['pages'], error['error'])}
    asset_requirements = {} if title_only else asset_readability(job, {**material, 'pages': [
        p for p in contract['pages'] if choices[p['id']]['edit_mode'] != 'referenced']}, capacity_feedback)
    qa = read(version / "qa/summary.json") if (version / "qa/summary.json").exists() else None
    targets = {i["page_id"] for i in qa["issues"] if i["severity"] == "major"} if qa else {
        p["id"] for p in mapping["pages"] if p["edit_mode"] != "referenced"}
    if any(choices[p]["edit_mode"] == "referenced" for p in targets):
        raise ValueError("QA findings outside requested revision scope; no unrelated edit permitted")
    if error and error.get("resolved"):
        error = None
    if error and not error.get("resolved"):
        failed_files = set(re.findall(r"\[ERROR\]\s+([^\s]+\.svg)\s+-\s+Failed", error.get("error", "")))
        failed_files.update(re.findall(r"\bError:\s*([^\s:]+\.svg)", error.get("error", "")))
        failed_files.update(re.findall(r"\]\s+([^\s]+\.svg)\s+-\s+Error:", error.get("error", "")))
        failed_ids = failed_page_ids(mapping['pages'], error.get('error', ''))
        targets = failed_ids if failed_ids else (targets if qa else set())
        if any(choices[p]['edit_mode'] == 'referenced' for p in targets):
            raise ValueError('Export failure outside requested revision scope; no unrelated edit permitted')
        if ANIMATION_TARGET_ERROR in error.get("error", ""):
            reconcile_animation_targets(version, workspace, mapping, error.get("error", ""))
            targets = {p["id"] for p in mapping["pages"] if p["edit_mode"] != "referenced"
                       and p["svg"] in failed_files and p["id"] in targets}
    # A page whose workspace copy is still an empty canvas (authoring failed or was
    # never reached) must be authored, never exported as a blank slide.
    targets |= {p["id"] for p in mapping["pages"]
                if p["edit_mode"] != "referenced" and page_needs_authoring(workspace, p, material)}
    if qa and error is None and (version / 'plan.json').exists():
        reviewed_sources = {p['id']:p.get('source_slide') for p in read(version / 'plan.json')['pages']}
        for choice in mapping['pages']:
            if choice.get('pending_author') is False and reviewed_sources.get(choice['id']) != choice['source_slide']:
                # This replacement skeleton has already passed candidate checks;
                # the old rendered finding is not evidence about the new page.
                targets.discard(choice['id'])
    for choice in mapping['pages']:
        if choice['edit_mode']!='template-fill':
            continue
        current=(workspace/'authoring-svg-flat'/choice['svg']).read_text(encoding='utf-8')
        fragments=slots(current)
        for slot in fragments:
            indices=slot.get('shared_native_frame_indices',[slot['index']])
            source_ref=next((a.get('data-pptx-source-ref','') for a in slot['ancestors'] if a.get('data-pptx-frame')),'')
            if source_ref.startswith('slide:') and len(indices)>1 and sum(bool(fragments[i]['text'].strip()) for i in indices)>1:
                targets.add(choice['id'])
                break
    references = {name: (SKILL / name).read_text(encoding="utf-8") for name in (
        "SKILL.md", "workflows/edit-native-pptx.md", "references/shared-standards-core.md")}
    metadata_lock = threading.Lock()
    run_source_history = {}

    def switch_to_recompose(page, reason):
        choice = choices[page["id"]]
        source_svg = (workspace / "authoring-svg-flat" / f"slide_{choice['source_slide']:02}.svg").read_text(encoding="utf-8")
        base_svg = fresh_canvas(source_svg)
        with metadata_lock:
            (version / "authoring-bases" / choice["svg"]).write_text(base_svg, encoding="utf-8")
            (workspace / "authoring-svg-flat" / choice["svg"]).write_text(base_svg, encoding="utf-8")
            choice.update(edit_mode="local-recompose", pending_author=True,
                          rationale=(choice.get("rationale", "") + f" [本地重排：{reason}]").strip())
            mapping["limitations"] = sorted(set(mapping.get("limitations") or []) |
                                            {f"{page['id']}: {reason}；改用页内重排"})
            save(version / "native-workspace.json", mapping)
            save(workspace / "page_plan.json", {"schema": "ppt-master.roundtrip-page-plan.v1", "pages": [
                {"source_slide": p["source_slide"], "svg": p["svg"]} for p in mapping["pages"]]})
            routes = read(version / "page-routing.json")
            for route in routes:
                if route["id"] == page["id"]:
                    route.update(mode="local-recompose", empty_slide_local_canvas=True)
            save(version / "page-routing.json", routes)

    def rematch(page, reason):
        choice = choices[page["id"]]
        tried = run_source_history.setdefault(page['id'], {choice['source_slide']})
        if len(tried) > MAX_LAYOUT_REMATCHES:
            raise ValueError(f"{page['id']}: native layout rematch budget exhausted: {reason}")
        history = set(choice.get("source_history", []) + [choice["source_slide"]])
        if parent:
            allowed = {p["current_source_slide"] for p in read(version / "parent-roster.json")
                       if p["id"] in contract.get("request", {}).get("page_ids", [])}
        else:
            allowed = {p["number"] for p in read(version / "template-vision-v2.json")
                       if p.get("text_fill_compatible") is True and p.get("capacity") != "low"}
        allowed -= tried
        if not parent:
            # Only layouts whose real heading can hold this page's title are usable.
            allowed = {n for n in allowed
                       if title_slots((workspace / "authoring-svg-flat" / f"slide_{n:02}.svg")
                                      .read_text(encoding="utf-8"), page["title"])}
        if not allowed:
            if not parent and choice.get("edit_mode") == "template-fill":
                switch_to_recompose(page, "所有可用版式的标题槽都放不下完整标题")
                return
            raise ValueError(f"{page['id']}: no untried compatible native layout remains")
        candidates = [x for x in catalog(version) if x['number'] in allowed]
        if not parent:
            # After a failed fit, shortlist real paragraph capacity before asking
            # the model to choose semantic correspondence among decorative layouts.
            candidates.sort(key=lambda x:x['estimated_body_character_capacity'], reverse=True)
            candidates = candidates[:3]
            allowed = {x['number'] for x in candidates}
        decision = ask(version / "api", f"rematch-{page['id']}-{len(history)}", (
            "当前纯文字页在原模板槽位内等义压缩后仍无法通过容量检查。返回规划器重新选择一个真实原生版式，"
            "不能改走整页重排，也不能丢失核心事实。仅返回source_slide、rationale。source_slide必须是allowed中的整数模板页码，绝不是p05之类的课件page id。"
            "根据实际失败原因选择更能容纳本页标题和正文、图形语义相符的版式。"
            "逐个比较catalog中的真实宽高、字号和正文槽数，优先足量的多行正文区域；不能把长事实匹配到图标的小标签，不能只依据版式名称或卡片数量推断容量。"
        ), {"page": page, "facts": {r: material["facts"][r] for r in page["point_ids"]},
            "reason": reason, "allowed": sorted(allowed), "catalog": candidates},
            required=("source_slide", "rationale"), validate=lambda d: None if type(d["source_slide"]) is int and d["source_slide"] in allowed else
            (_ for _ in ()).throw(ValueError(f"source_slide must be an INTEGER template number from {sorted(allowed)}, received {d.get('source_slide')!r}. Return the selected source's numeric catalog.number, not the requested page id.")), timeout=600)
        number = decision["source_slide"]
        tried.add(number)
        with metadata_lock:
            source_svg = (workspace / "authoring-svg-flat" / f"slide_{number:02}.svg").read_text(encoding="utf-8")
            (version / "authoring-bases" / choice["svg"]).write_text(source_svg, encoding="utf-8")
            (workspace / "authoring-svg-flat" / choice["svg"]).write_text(source_svg, encoding="utf-8")
            choice.update(source_slide=number, rationale=decision["rationale"], source_history=sorted(history | {number}),
                          rematch_count=choice.get("rematch_count", 0) + 1, pending_author=True)
            save(version / "native-workspace.json", mapping)
            save(workspace / "page_plan.json", {"schema": "ppt-master.roundtrip-page-plan.v1", "pages": [
                {"source_slide": p["source_slide"], "svg": p["svg"]} for p in mapping["pages"]]})
            routes = read(version / "page-routing.json")
            for route in routes:
                if route["id"] == page["id"]:
                    route["source_slide"] = number
            save(version / "page-routing.json", routes)

    def author(page):
        choice = choices[page["id"]]
        if title_only:
            from pptx import Presentation
            old_page = next(p for p in read(job / 'versions' / parent / 'plan.json')['pages'] if p['id'] == page['id'])
            original = Presentation(source).slides[choice['source_slide'] - 1]
            notes = original.notes_slide.notes_text_frame.text if original.has_notes_slide else ''
            base = (version / 'authoring-bases' / choice['svg']).read_text(encoding='utf-8')
            svg, result = edit_title(version, page['id'], base, old_page['title'], page['title'], notes)
            return page['id'], svg, result
        page_findings = [i for i in (qa or {}).get('issues', []) if i['page_id'] == page['id'] and i['severity'] == 'major']
        if (choice['edit_mode'] == 'local-recompose' and page_findings
                and all(i['scope'] in ('content', 'content-coverage') for i in page_findings)
                and (not error or error.get('phase') == 'authoring')):
            current_path = workspace / 'authoring-svg-flat' / choice['svg']
            current = current_path.read_text(encoding='utf-8')
            fixed = {ref: {**material['assets'][ref], **asset_requirements[page['id']][ref],
                          'workspace_href': '../images/' + Path(material['assets'][ref]['path']).name}
                     for ref in page['asset_ids']}
            try:
                validate_asset_references([ET.fromstring(current)], fixed)
                validate_text_capacity(current, page['title'], strict_vertical=True, check_canvas=True)
            except ValueError:
                pass  # Existing composition itself is invalid; use the full model repair below.
            else:
                notes = (workspace / 'notes' / (Path(choice['svg']).stem + '.md')).read_text(encoding='utf-8')
                svg, result = repair_local_text(version, page, material, current, notes, page_findings, fixed)
                return page['id'], svg, result
        visual_blockers = [i for i in (qa or {}).get('issues', [])
                           if i['page_id'] == page['id'] and i['severity'] == 'major' and i['scope'] == 'visual']
        if (version / 'plan.json').exists():
            rendered_source = next((p.get('source_slide') for p in read(version / 'plan.json')['pages'] if p['id'] == page['id']), None)
            if rendered_source != choice['source_slide']:
                visual_blockers = []
        if (choice.get("edit_mode") or route_for(page)) == 'template-fill' and visual_blockers:
            decision = ask(version / 'api/repair-strategy', page['id'], (
                '判断原生文字填充页的视觉缺陷能否仅通过文字替换、换行和90%-105%原字号解决。'
                '返回strategy=keep_source/rematch_source与reason。非文字图形、图标和背景几何不可移动。'
                '若实际成品的源分组错位、图标遮字或非文字对象需要移动，必须rematch_source，交规划器换真实原生页型。'
            ), {'findings':visual_blockers,'current_source_slide':choice['source_slide']},
                required=('strategy','reason'), validate=lambda d: None if d['strategy'] in ('keep_source','rematch_source') else
                (_ for _ in ()).throw(ValueError('Invalid native repair strategy')))
            if decision['strategy'] == 'rematch_source':
                rematch(page, decision['reason'])
        base = (version / "authoring-bases" / choice["svg"]).read_text(encoding="utf-8")
        base_digest = hashlib.sha256(base.encode('utf-8')).hexdigest()
        author_stage = f"{page['id']}-{base_digest[:12]}"
        page_findings_all = [i for i in (qa or {}).get('issues', []) if i['page_id'] == page['id']]
        if page_findings_all:
            # A repaired page must not reuse the rejected response: the same stage key
            # would replay the old text and the reviewer would reject it again.
            digest = hashlib.sha256(json.dumps(page_findings_all, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            author_stage += f"-repair-{digest[:8]}"
        facts = {ref: material["facts"][ref] for ref in page["point_ids"]}
        mode = choice.get("edit_mode") or route_for(page)
        instruction = (
            "你是PPTNative的API页面编辑器，使用所附原版PPT Master原生往返契约制作一页。"
            "内容、布局与SVG由你决定，编排程序不代写页面。保持合同标题逐字可见；核心事实必须在上屏表达体现。"
            "允许等义压缩原句，不把完整长句硬塞进小框；每条事实的数字、否定条件、对象角色与关键操作必须上屏保留。"
            "容量不足先用明确多行tspan或其他已有正文槽，仍不够应报告版式不兼容，不能删关键条件以通过容量检查。"
            "程序把原文附入备注仅用于来源追溯，不构成任何上屏遗漏的豁免。"
            "notes为完整字符串，rationale字符串，limitations数组。清除所有源模板的占位正文、旧日期/主讲人/LOGO等残留，"
            "JSON说明字符串中的英文双引号必须转义，说明文字优先使用中文引号。rationale简短，不重复描述所有样式细节。"
            "不要编造缺失信息。不新增来源外的事实或图片。SVG必须明确分行，不依靠HTML式自动换行。"
        )
        if parent and contract.get('request', {}).get('kind') == 'edit_page':
            instruction += '\n教师对本页的修改要求：' + contract['request']['instruction'] + '。保留完整事实合同、素材身份与教师备注。'
        if page_findings_all:
            instruction += ('\n本轮必须修复质检问题：' + '；'.join(i['evidence'] for i in page_findings_all) +
                            '。缺失的对象、主语、条件与关键操作必须逐字出现在本页上屏文本中，不能只写在备注里。')
        tone_request = (contract.get('request') or {}).get('instruction') or ''
        if re.search(r'深色|暗色|深蓝|墨绿|炭黑|黑色|蓝黑', tone_request):
            instruction += ('\n本页整体改为深色系：背景用深海军蓝 #1B3B88（或更深的实色），'
                            '标题与正文用白色/浅灰 #E8ECF5，强调色 #157A9E；不得保留白底或浅色卡片。')
        elif re.search(r'浅色|亮色|白底', tone_request):
            instruction += ('\n本页整体改为浅色系：白色 #FFFFFF 背景，深蓝 #1B3B88 标题，'
                            '卡片用 #F5F6FA，强调色 #157A9E。')
        if mode == "template-fill":
            eligible_titles = title_slots(base, page["title"])
            with metadata_lock:
                heading_path = version / "api/template-headings" / f"source-{choice['source_slide']}.response.json"
                if heading_path.exists():
                    heading = read(heading_path)
                else:
                    reference = (job / "versions" / parent / "preview" / f"deck_p{choice['source_slide']}.png") if parent else (
                        job / "source-preview" / f"template_p{choice['source_slide']}.png")
                    heading = ask(heading_path.parent, f"source-{choice['source_slide']}", (
                        "识别真实模板页中全页主标题或顶部章节标签对应的text索引。返回indices数组和reason。"
                        "不要选正文小标题、图内标签、角落细目。PPTNative会用合同完整标题替换主标题，不能留旧章节字样而把新标题塞进正文。"
                    ), {"slots": slots(base)}, visual=True, images=[reference], required=("indices", "reason"))
            eligible_titles = sorted(set(eligible_titles).intersection(heading["indices"])) or eligible_titles
            if not eligible_titles:
                rematch(page, "Selected source has no heading slot that can contain the full title")
                return author(page)
            instruction += (
                "本页强制template-fill，只返回edits数组（index、xml），以及notes、rationale、limitations。"
                "每个index对应输入text_fragments的原始text节点，xml是完整替换后的单个text元素或空字符串。"
                "不能增加背景遮盖，不移动/重排原几何或组，不新增图形和文字框。保留原text根节点的x/y/transform/text-anchor。"
                "保留原字体、颜色、字号层次，字号变化限原值90%-105%；保留或正确重写逐行tspan行模型，"
                "沿用原槽位宽高。核心事实可分到多个现有槽位，不要把每条原句机械复制一遍。"
                "事实与槽位不是一一对应：一个足够大的正文槽可以组合多条facts，一个事实也可分布在多个相关槽位；不要把长事实硬配给图内小标签。"
                "shared_native_frame_indices表示多个text根属于同一个原生文字框的连续段，不是多个独立框。"
                "被编辑的同一原生框必须只保留一个活跃text根：在first_fragment_index承载全部段落/tspan行，其他shared_native_frame_indices必须显式清空，不能保留多个独立根，否则原生恢复会把多个新文字框叠在同一个位置。不要把多行新内容放在最底部残片上。"
                "多余占位文本清空，需要的章节标签改为本课内容；不要把不足归为保留源模板的理由。"
                "完整合同标题必须独占title_eligible_indices中的一个原文字节点，不加前缀或换成泛称，不能放入较小的正文标签。"
            )
        else:
            instruction += (
                "本页强制local-recompose，输入canvas已经清空源页Slide-local内容。"
                "返回content_svg（规范SVG片段，不含svg根标签）、notes、rationale、limitations。"
                "从零设计图文分区，按style的主色/辅色/浅底形成层次，参考模板调性，避免只有灰白空框。"
                "标题和正文使用可读字体，通常正文24-30px；教材图完整contain显示，不能用原模板图文框硬套，"
                "不得带入源模板编号、无关图标、旧章节文字或装饰遮挡正文。"
                "新增规范g/text/image/rect不得带任何data-pptx-source-ref或伪造原生身份。"
                "所有图片用assets给出的workspace_href且保留像素，不裁切、不用其他图替换。"
                "文字与图轴/代码分开留安全距离。源图固有颜色保持，页面自身配色参考模板。"
                "所有图形、图片与文字必须完整落在1280×720画布内，四周至少留16px边距，禁止贴边或越界。"
                "每个文字组自身的外框必须完整包住该组所有tspan行（行高×行数+上下各6px余量），"
                "data-pptx-bounds与g的几何不能小于文字实际范围；单行注释同样要留余量，不要用20px以下的小框承载注释。"
                "assets的minimum_display_width是Qwen判断的实际可见最小宽度；contain同时受外框宽高限制。"
                "若不满足可读尺寸，必须重新安排全页主要区域，为图片让出空间，取消重复说明和装饰卡片；"
                "不能只增大框宽却保留过小高度，也不能一直沿用原来的左右双栏。核心文字须与背景有充分对比。"
            )
        if mode != "template-fill":
            instruction += ("\n图片只能引用 assets 中列出的 workspace_href；不得引用 ../images/imageN 等模板原图，"
                            "也不得新增未提供的图片；没有素材时请只用文字、色块与矢量图形。")
        payload = {"page": page, "facts": facts, "mode": mode, "canvas": base,
                   "source_slide": choice['source_slide'], "base_sha256": base_digest,
                   "text_fragments": slots(base) if mode == "template-fill" else [],
                   "assets": {ref: {**material["assets"][ref], **asset_requirements[page['id']][ref], "workspace_href": "../images/" + Path(material["assets"][ref]["path"]).name}
                              for ref in page["asset_ids"]}, "style": read(job / "style/style-profile.json"),
                   "upstream_references": references, "latest_error": error,
                   "findings": [i for i in (qa or {}).get("issues", []) if i["page_id"] == page["id"]]}
        if (version / "preview/text-metrics.json").exists() and (version / "plan.json").exists():
            prior = read(version / "plan.json")["pages"]
            index = next((i for i, p in enumerate(prior, 1) if p["id"] == page["id"]), None)
            prior_page = next((p for p in prior if p['id'] == page['id']), {})
            if prior_page.get('source_slide') == choice['source_slide']:
                payload["actual_powerpoint_text_metrics"] = [p for p in read(version / "preview/text-metrics.json")["slides"] if p["page"] == index]
                instruction += "实际PowerPoint文字边界是成品证据；若文字超过原槽位，须压缩表达或修正换行，不以SVG已通过为由忽略。"
        if mode == "template-fill":
            payload["canvas"] = {k: v for k, v in ET.fromstring(base).attrib.items()}
            payload["title_eligible_indices"] = eligible_titles
        for asset in payload['assets'].values():
            asset['minimum_display_height'] = round(asset['minimum_display_width'] * asset['height'] / asset['width'], 2)
        inherited_slots = {s["index"]: s["text"] for s in slots(base)
                           if any((a.get("data-pptx-source-ref") or "").startswith(("master:", "layout:"))
                                  for a in s["ancestors"])} if mode == "template-fill" else {}
        semantic_text_ids = {n.get("id") for n in ET.fromstring(base).iter()
                             if n.get("data-pptx-semantic-object")
                             and any(t.tag.rsplit("}", 1)[-1] == "text" and "".join(t.itertext()).strip()
                                     for t in n.iter())} if mode == "template-fill" else set()
        def validate(data):
            if mode == "template-fill":
                # Master/layout text (page number, footer) is shared structure; editing it
                # makes the exporter reject the whole page as an inherited-object change.
                changed_inherited = []
                for edit in data["edits"]:
                    if edit["index"] not in inherited_slots:
                        continue
                    text = "".join(ET.fromstring(
                        f'<svg xmlns="{SVG}">{edit["xml"]}</svg>')[0].itertext()) if edit.get("xml") else ""
                    if text != inherited_slots[edit["index"]]:
                        changed_inherited.append(edit["index"])
                if changed_inherited:
                    raise ValueError(f"[LAYOUT] Slots {changed_inherited} belong to the master/layout and cannot be "
                                     "edited or cleared. Put titles and body text in slide-local slots only; keep "
                                     "page numbers and footers untouched.")
                candidate_svg = fit_text_to_frames(apply_text_edits(base, data['edits']),
                                                  {e["index"] for e in data["edits"] if e.get("xml")})
                emptied = [node.get("id") for node in ET.fromstring(candidate_svg).iter()
                           if node.get("id") in semantic_text_ids
                           and not any("".join(t.itertext()).strip() for t in node.iter()
                                       if t.tag.rsplit("}", 1)[-1] == "text")]
                if emptied:
                    raise ValueError(f"[LAYOUT] Shapes {emptied} are native semantic text shapes; an empty body "
                                     "cannot be exported. Keep a short visible label or move the meaning there, "
                                     "never clear these slots. Clear only decorative non-semantic placeholders.")
                title_edits = [e for e in data["edits"] if e["xml"] and "".join(ET.fromstring(
                    f'<svg xmlns="{SVG}">{e["xml"]}</svg>')[0].itertext()).strip() == page["title"]]
                if not any(e["index"] in eligible_titles for e in title_edits):
                    raise ValueError(f"[LAYOUT] The complete exact title must replace a suitable heading slot from {eligible_titles}, not a generic PART label or a small text label")
                validate_text_capacity(candidate_svg, page["title"], strict_vertical=True, baseline=base,
                                       indices={e["index"] for e in data["edits"] if e.get("xml")}, tolerance=0)
            else:
                fragment = content_nodes(data["content_svg"], base)
                validate_asset_references(fragment, payload["assets"])
                # Local paint servers (gradients/patterns) in appended content lose their
                # definitions during round-trip materialization and fail export.
                if any("url(#" in (n.get("fill") or "") or "url(#" in (n.get("stroke") or "")
                       or "url(#" in (n.get("style") or "")
                       for top in fragment for n in top.iter()):
                    raise ValueError("New compositions must use solid #RRGGBB fills (opacity is allowed), "
                                     "not gradient defs or url(#id) paint references; flat color blocks export reliably.")
                if any(n.get("data-pptx-source-ref") for top in fragment for n in top.iter()):
                    raise ValueError("New image compositions must not invent native source identity")
                if any(0x1F000 <= ord(c) <= 0x1FAFF for top in fragment for c in ''.join(top.itertext())):
                    raise ValueError("Emoji text is not portable in native PowerPoint; use editable canonical SVG vector icons instead")
                root = ET.fromstring(base)
                root.extend(fragment)
                runtime_ids(root)
                fitted = fit_images_to_canvas(strip_canvas_backgrounds(ET.tostring(root, encoding="unicode")))
                root = ET.fromstring(fitted)
                validate_text_capacity(ET.tostring(root, encoding="unicode"), page["title"], strict_vertical=True, check_canvas=True)
            # Reject semantic deletion before exporting; actual PPTX coverage is
            # independently checked again after PowerPoint renders the result.
            from .quality import semantic_coverage
            candidate = apply_text_edits(base, data["edits"]) if mode == "template-fill" else ET.tostring(root, encoding="unicode")
            texts = [''.join(n.itertext()) for n in ET.fromstring(candidate).iter()
                     if n.tag.rsplit('}', 1)[-1] == 'text']
            _, missing = semantic_coverage(version / "candidate-checks" / page["id"],
                {"native": True, "pages": [page]}, material, [{"texts": texts}])
            if missing:
                raise ValueError("[LAYOUT] Native slot semantic capacity failed: " + '; '.join(i['evidence'] for i in missing) +
                                 ". Restore all required meaning within native slots; never move it only to notes.")
            if (error and not error.get("resolved") and error.get('phase') != 'authoring'
                    and page['id'] in failed_ids):
                updated = apply_text_edits(base, data["edits"]) if mode == "template-fill" else ET.tostring(root, encoding="unicode")
                current = (workspace / "authoring-svg-flat" / choice["svg"]).read_text(encoding="utf-8")
                if ET.canonicalize(updated, strip_text=True) == ET.canonicalize(current, strip_text=True):
                    raise ValueError("The latest original checker is still blocking this page, but the SVG is unchanged. Return an actual correction to the reported bounds/wrapping, not a rationale claiming it is already fixed.")
        cached = version / "api/author" / f"{author_stage}.response.json"
        result = None
        metadata_only = error and any(message in error.get("error", "") for message in
                                      ("requires an id", "at most one slide solid background", "invalid nested SVG crop wrapper"))
        cache_allowed = not qa and not choice.get("rematch_count") and (not (error and not error.get("resolved")) or metadata_only)
        if cached.exists() and not cache_allowed and not page_findings_all and not (error and not error.get("resolved")):
            payload["previous_invalid_result"] = read(cached)
        recent_raw = sorted([*(version / 'api/author').glob(f'{author_stage}.*.raw.txt'),
                             *(version / 'api/author-fresh').glob(f'{author_stage}.*.raw.txt')],
                            key=lambda p:p.stat().st_mtime, reverse=True)
        if recent_raw:
            recent = recent_raw[0]
            retry = recent.parent / f'{author_stage}.retry.json'
            if retry.exists() and retry.stat().st_mtime >= recent.stat().st_mtime:
                raw = recent.read_text(encoding='utf-8')
                try:
                    feedback = read(retry)['error']
                    if 'image contract' not in feedback.lower():
                        payload['previous_invalid_result'] = json.loads(raw[raw.index('{'):raw.rindex('}')+1])
                    payload['validation_feedback'] = feedback
                    payload['current_candidate_is_unexported'] = True
                    instruction += 'previous_invalid_result为同一底稿上最近尚未导出的候选；优先准确修复validation_feedback，保留已满足的图片尺寸、核心语义和其他结构，不要重新退回旧成品的小图布局。旧成品视觉意见不代表新候选已经渲染。'
                except (ValueError, KeyError):
                    pass
        if cache_allowed and cached.exists():
            candidate = read(cached)
            try:
                validate(candidate)
                result = candidate
            except (ValueError, KeyError, ET.ParseError):
                pass
        if cache_allowed and result is None:
            old_raw = sorted((version / "api/author").glob(f"{author_stage}.*.raw.txt"), key=lambda p:p.stat().st_mtime, reverse=True)
            for raw_path in old_raw[:1]:
                try:
                    raw = raw_path.read_text(encoding="utf-8")
                    candidate = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
                    validate(candidate)
                    result = candidate
                except (ValueError, KeyError, ET.ParseError) as validation_error:
                    payload["previous_invalid_result"] = candidate if 'candidate' in locals() else raw
                    payload["validation_feedback"] = str(validation_error)
        if result is None:
            try:
                result = ask(version / "api/author", author_stage, instruction, payload,
                             required=(("edits",) if mode == "template-fill" else ("content_svg",)) + ("notes", "rationale", "limitations"),
                             validate=validate, timeout=600)
            except RuntimeError as api_error:
                local_indices = failure_text_indices(base, api_error)
                if mode=='template-fill' and local_indices:
                    raw_path=sorted((version/'api/author').glob(f'{author_stage}.*.raw.txt'),key=lambda p:p.stat().st_mtime)[-1]
                    raw=raw_path.read_text(encoding='utf-8')
                    previous=json.loads(raw[raw.index('{'):raw.rindex('}')+1])
                    def validate_patch(patch):
                        validate(merge_local_edits(previous,patch,local_indices))
                    try:
                        patch=ask(version/'api/local-capacity-repair',author_stage,
                            '你是原生文字局部修复API。只修复allowed_indices中的容量错误；返回patch_edits数组，每项index、xml，以及rationale。'
                            '原文案核心意义、原根x/y/anchor和字号范围必须保留。优先纠正换行、行数、同框首段位置，必要时在允许范围内调整字号。'
                            '不要重做整页，不要触碰其他槽。将长行正确拆开；增加行数前确认该原框的高度。共享原生框仅保留一个活跃text根。',
                            {'previous_candidate':previous,'allowed_indices':sorted(local_indices),
                             'error':str(api_error),'original_fragments':[s for s in slots(base) if s['index'] in local_indices]},
                            required=('patch_edits','rationale'),validate=validate_patch,timeout=600)
                        result=merge_local_edits(previous,patch,local_indices)
                    except RuntimeError as patch_error:
                        rematch(page,str(patch_error))
                        return author(page)
                elif mode == 'local-recompose' and any(reason in str(api_error) for reason in
                                                       ('Image readability capacity', '[LAYOUT]')):
                    restarted = {k:v for k,v in payload.items() if k not in
                                 ('previous_invalid_result','actual_powerpoint_text_metrics','validation_feedback')}
                    restarted['incompatible_previous_layout'] = str(api_error)
                    result = ask(version / 'api/author-fresh', author_stage, instruction +
                        '上一种构图无法同时满足图片阅读空间、文字容量和画布边界。放弃那种容器排列，从给定空白canvas重新规划整页。',
                        restarted, required=('content_svg','notes','rationale','limitations'), validate=validate, timeout=600,
                        reasoning=True)
                elif mode != "template-fill" or '[LAYOUT]' not in str(api_error):
                    raise
                else:
                    rematch(page, str(api_error))
                    return author(page)
        if mode == "template-fill":
            svg = fit_text_to_frames(apply_text_edits(base, result["edits"]),
                                     {e["index"] for e in result["edits"] if e.get("xml")})
        else:
            root = ET.fromstring(base)
            root.extend(content_nodes(result["content_svg"], base))
            runtime_ids(root)
            svg = fit_images_to_canvas(strip_canvas_backgrounds(ET.tostring(root, encoding="unicode")))
        result["notes"] = retained_notes(result["notes"], page, material)
        return page["id"], svg, result

    def persist(page):
        check_cancel(job)
        page_id, svg, result = author(page)
        check_cancel(job)
        with metadata_lock:
            choice = choices[page_id]
            svg = normalize_svg_paint(svg)
            (workspace / "authoring-svg-flat" / choice["svg"]).write_text(svg, encoding="utf-8")
            stem = Path(choice["svg"]).stem
            (workspace / "notes" / f"{stem}.md").write_text(result["notes"], encoding="utf-8")
            choice.update(rationale=result['rationale'], pending_author=False)
            mapping["limitations"].extend(result["limitations"])
            save(version / 'native-workspace.json', mapping)
    targets |= {p['id'] for p in mapping['pages'] if p.get('pending_author')}
    parallel([p for p in contract["pages"] if p["id"] in targets], persist)
    incomplete = [p["id"] for p in mapping["pages"]
                  if p["edit_mode"] != "referenced" and page_needs_authoring(workspace, p, material)]
    if incomplete:
        raise ValueError(
            f"{incomplete[0]}: authored page missing before export; return the complete composition for "
            f"{', '.join(incomplete)} (every fixed image plus visible teaching text), never an empty canvas")
    mapping['text_frame_contract_version']=1
    animations = read(workspace / "animations.json")
    for choice in mapping['pages']:
        if choice["edit_mode"] == "local-recompose":
            animations.setdefault("slides", {})[Path(choice['svg']).stem] = {"animation": {"effect": "none"}}
    save(workspace / "animations.json", animations)
    save(version / "native-workspace.json", mapping)
    for choice in mapping["pages"]:
        if choice["edit_mode"] == "local-recompose":
            path = workspace / "authoring-svg-flat" / choice["svg"]
            root = runtime_ids(ET.parse(path).getroot())
            path.write_text(ET.tostring(root, encoding="unicode"), encoding="utf-8")
    check_cancel(job)
    run_upstream(version, "svg_authoring_view.py", [workspace / "authoring-svg-flat", "--refresh-summary"], "author-summary.log")
    run_upstream(version, "svg_quality_checker.py", [workspace, "--roundtrip", "--json"], "author-capacity.log")
    stdout = export_roundtrip(version, workspace, mapping)
    paths = re.findall(r"^\s*\[PPTX\]\s+(.+)$", stdout, re.M)
    if not paths:
        raise ValueError("Original round-trip exporter did not report its output path")
    output = Path(paths[-1].strip())
    run_upstream(version, "pptx_delivery_check.py", [output], "author-delivery.json")
    run_upstream(version, "source_to_md/ppt_to_md.py", [output, "-o", workspace / "validation/author-readback.md"], "author-readback.log")
    save(version / "native-result.json", {**mapping, "output_pptx": str(output)})
