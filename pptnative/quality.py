"""Composite quality control includes deterministic checks, rendering and two API reviewers."""

import hashlib
import json
import re
from pathlib import Path
import shutil
import subprocess
from zipfile import ZipFile

from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from .drawing import RENDER_VERSION, walk
from .models import ask, parallel, read, save

REVIEW_VERSION = 4


def unchanged_findings(inherited, fingerprints):
    """Unchanged output cannot silently resolve a previously observed defect."""
    cached = (inherited or {}).get('visual_coverage', {})
    return [issue for issue in (inherited or {}).get('issues', [])
            if issue['page_id'] in fingerprints
            and cached.get(issue['page_id']) == fingerprints[issue['page_id']]]


def visual_fingerprint(page, scene, preview, style, actual_notes=None):
    with Image.open(preview) as image:
        raster = {'size': image.size, 'pixels': hashlib.sha256(image.convert('RGB').tobytes()).hexdigest()}
    contract = {k: v for k, v in page.items() if k not in ('source_slide', 'svg', 'rationale', 'edit_mode', 'route')}
    payload = {'review_version': REVIEW_VERSION, 'renderer': scene.get('_render_version', 2),
               'page': contract, 'scene': scene, 'raster': raster, 'style': style, 'actual_notes': actual_notes}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def render_fit_issues(version, plan, source_previews):
    """Compare real PowerPoint text ranges with original native slot capacity."""
    path = version / "preview/text-metrics.json"
    if not plan.get("native") or not path.exists():
        return []
    parent = read(version / "version.json").get("parent")
    original_path = version.parent / parent / "preview/text-metrics.json" if parent else source_previews / "text-metrics.json"
    if not original_path.exists():
        return []
    original = {p["page"]: {o["id"]: o for o in p["objects"]} for p in read(original_path)["slides"]}
    current = read(path)
    issues = []
    for page, rendered in zip(plan["pages"], current["slides"]):
        if page.get("edit_mode") != "template-fill":
            continue
        old = original.get(page["source_slide"], {})
        for item in rendered["objects"]:
            prior = old.get(item["id"])
            if not prior or prior["text"] == item["text"]:
                continue
            x,y,w,h = item["frame"]
            a,b,c,d = item["rendered_bounds"]
            ox,oy,ow,oh = prior["frame"]
            oa,ob,oc,od = prior["rendered_bounds"]
            excess = b+d-y-h - max(0,ob+od-oy-oh)
            if excess > 4 or b+d > current["height"] + 2:
                issues.append({"page_id":page["id"],"severity":"major","scope":"render-geometry",
                    "evidence":f"Actual PowerPoint text object {item['id']} {item['text']!r} exceeds source slot vertically by {excess:.1f}px beyond source baseline; frame={item['frame']}, actual={item['rendered_bounds']}",
                    "action":"Compress or reflow the edited text inside the existing native slot, or rematch a suitable source layout. SVG horizontal-capacity success does not establish that the actual PowerPoint text fits."})
    return issues


def semantic_coverage(version, plan, material, evidence):
    if not plan.get("native"):
        return [], []
    def inspect(pair):
        page, export = pair
        ids = page.get("point_ids", [])
        if not ids:
            return {"page_id":page["id"],"coverage":[]}
        def validate(data):
            if len(data["coverage"]) != len(ids) or {c["fact_id"] for c in data["coverage"]} != set(ids):
                raise ValueError("Coverage must judge every required fact exactly once")
            for item in data["coverage"]:
                if item["status"] not in ("fully_visible","partially_visible","notes_only","missing"):
                    raise ValueError("Invalid semantic coverage status")
                if item["status"] != "fully_visible" and not item.get("missing_information"):
                    raise ValueError(f"coverage item {item['fact_id']} status={item['status']} requires a nonempty missing_information field explaining exactly what meaning is missing")
                if item["status"] == "fully_visible":
                    proof = item.get("visible_evidence")
                    proof = [proof] if isinstance(proof, str) else proof
                    visible = "".join("".join(export["texts"]).split())
                    if not proof or any(not isinstance(p,str) or "".join(p.split()) not in visible for p in proof):
                        raise ValueError("Fully visible coverage requires verbatim evidence substrings from actual visible_texts")
        result = ask(version / "qa/api/coverage", page["id"], (
            "逐条核对本页固定事实与实际PPTX上屏文本。返回coverage数组，含fact_id、status、visible_evidence、missing_information。"
            "每项必须输出这四个字段；非fully_visible时missing_information必须非空，准确说明缺失内容。"
            "status仅fully_visible/partially_visible/notes_only/missing。允许等义表达，但数字、否定条件、对象角色和关键操作不得省掉。"
            "代码/API/函数名是关键操作的身份，泛称其用途不能替代指定调用；操作顺序、适用范围、量词与目的同样属于事实。"
            "先逐分句核对，再判断整条完整性；只满足主题、标签或结果，不代表调用方式和附带条件完整。"
            "visible_evidence为数组，每项必须是实际visible_texts的原文子串，不添加引号标签或改写解释。"
            "必须在本页上屏体现，不能用其他页或备注替代，不因总体概念相似就判完整；特别核对多分句事实中是否只保留前半句。"
            "不改写课件，不评价美观。证据仅引用实际visible_texts，不引用计划或备注。"
        ), {"facts":{i:material["facts"][i] for i in ids},"visible_texts":export["texts"]},
            required=("coverage",),validate=validate,timeout=600)
        visible = ''.join(''.join(export['texts']).split())
        for item in result['coverage']:
            identifiers = set(re.findall(r'\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+\b', material['facts'][item['fact_id']]))
            missing = sorted(name for name in identifiers if name not in visible)
            if missing:
                item.update(status='partially_visible', missing_information='Explicit source code identifiers absent from visible text: ' + ', '.join(missing),
                            deterministic_identifier_check=True)
        return {'page_id':page['id'], **result}
    reviews = parallel(list(zip(plan["pages"], evidence)), inspect)
    issues = [{"page_id":r["page_id"],"severity":"major","scope":"content-coverage",
               "evidence":f"{c['fact_id']}: {c['status']}: {c['missing_information']}",
               "action":"Restore the missing meaning visibly in this page's proper text slots; notes or content on another slide cannot replace this page's planned facts."}
              for r in reviews for c in r["coverage"] if c["status"] != "fully_visible"]
    return reviews, issues


def adjudicate(findings, decisions):
    indexed = {}
    for decision in decisions:
        index = decision.get("index")
        if (type(index) is not int or index not in range(len(findings)) or index in indexed
                or decision.get("decision") not in ("uphold", "major", "minor", "source_limitation")
                or not decision.get("reason")):
            raise ValueError("Invalid visual adjudication contract: index must be a unique integer in "
                             f"0..{len(findings) - 1}; decision must be uphold, major, minor, or source_limitation; "
                             "reason is required.")
        indexed[index] = decision
    issues, limitations = [], []
    for index, item in enumerate(findings):
        decision = indexed.get(index, {})
        if decision.get("decision") == "source_limitation":
            limitations.append({"page_id": item["page_id"], "description": item["evidence"],
                                "adjudication": decision["reason"]})
        else:
            if decision.get("decision") in ("major", "minor"):
                item = {**item, "severity": decision["decision"], "adjudication": decision["reason"]}
            issues.append(item)
    return issues, limitations


def confirm_visual_downgrades(version, plan, source_previews, findings, decisions, provenance, metrics, assets=None):
    """Text metadata cannot overrule pixels without a fresh visual confirmation."""
    if not any(findings[d['index']]['severity'] == 'major' and d['decision'] in ('minor', 'source_limitation')
               for d in decisions):
        return decisions
    pages = {page['id']: (i, page) for i, page in enumerate(plan['pages'], 1)}
    parent = read(version / 'version.json').get('parent') if (version / 'version.json').exists() else None
    def inspect(decision):
        finding = findings[decision['index']]
        if finding['severity'] != 'major' or decision['decision'] not in ('minor', 'source_limitation'):
            return decision
        number, page = pages[finding['page_id']]
        images = [version / 'preview' / f'deck_p{number}.png']
        labels = ['CURRENT OUTPUT: actual exported PowerPoint render, authoritative for visibility']
        source = ((version.parent / parent / 'preview' / f"deck_p{page['source_slide']}.png" if parent
                   else source_previews / f"template_p{page['source_slide']}.png") if page.get('source_slide') else None)
        if source and source.exists():
            images.append(source)
            labels.append('ACTUAL SOURCE before the edit; compare introduced versus inherited defects')
        for ref in page.get('asset_ids', []):
            original = Path((assets or {}).get(ref, {}).get('path', ''))
            if original.is_file():
                images.append(original)
                labels.append(f'FIXED ORIGINAL ASSET {ref}: compare intrinsic source quality with new scaling/occlusion')
        def validate(data):
            if data['decision'] not in ('uphold', 'major', 'minor', 'source_limitation') or not data['reason']:
                raise ValueError('Visual confirmation requires an allowed decision and a concrete pixel-based reason')
        result = ask(version / 'qa/api/visual-confirmation', f"{page['id']}-{decision['index']}",
            '复核视觉模型与文字模型之间的争议，只看当前实际导出画面是否有阅读障碍。'
            '返回decision(uphold/major/minor/source_limitation)和reason。'
            'XML含有完整文字、COM文字框/BoundWidth正常，都不能证明文字实际可见：遮罩、裁切、透明、绘制顺序仍可能隐藏文字。'
            '若当前画面确有核心文字不可见、截断或遮挡，必须uphold或major，不能仅因文字对象存在而降级。'
            '若是源图固有问题或原报告误读，须指出具体画面证据后才能minor/source_limitation。'
            '这是复核，不制作或改写课件。',
            {'finding': finding, 'proposed_downgrade': decision, 'page': page,
             'source_export_provenance': provenance.get(page['id']),
             'powerpoint_text_metrics': metrics['slides'][number - 1]
                 if metrics and len(metrics.get('slides', [])) >= number else None},
            visual=True, images=images, image_labels=labels, required=('decision', 'reason'), validate=validate)
        return {**decision, **result, 'visual_confirmation': True}
    return parallel(decisions, inspect)


def render(pptx, destination):
    destination.mkdir(parents=True, exist_ok=True)
    shell = shutil.which("pwsh") or shutil.which("powershell")
    deck = Presentation(pptx)
    image_height = round(1280 * deck.slide_height / deck.slide_width)
    command = [shell, "-NoProfile", "-File", str(Path(__file__).with_name("render_pptx.ps1")),
               "-Pptx", str(pptx), "-OutDir", str(destination), "-Width", "1280", "-Height", str(image_height)]
    with (destination / "render.log").open("w", encoding="utf-8") as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=180,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise RuntimeError(f"PowerPoint rendering failed; see {destination / 'render.log'}")
    count = len(Presentation(pptx).slides)
    pages = [destination / f"{pptx.stem}_p{n}.png" for n in range(1, count + 1)]
    if not all(p.exists() for p in pages):
        raise RuntimeError("Renderer did not produce all page images")
    return pages


def extract(pptx, plan, material):
    deck = Presentation(pptx)
    if len(deck.slides) != len(plan["pages"]):
        raise ValueError("Exported slide count differs from approved plan")
    evidence, errors = [], []
    with ZipFile(pptx) as package:
        if package.testzip():
            errors.append("Corrupted PPTX package")
    for page, slide in zip(plan["pages"], deck.slides):
        items = list(walk(slide.shapes))
        texts = [s.text for s in items if s.has_text_frame and s.text.strip()]
        notes = slide.notes_slide.notes_text_frame.text if slide.has_notes_slide else ""
        joined = "".join("".join(texts).split())
        fact_carrier = joined + ("".join(notes.split()) if plan.get("native") else "")
        teacher_notes = "".join(page.get("notes", "").split())
        if plan.get("native") and teacher_notes and teacher_notes not in "".join(notes.split()):
            errors.append(f"{page['id']}: teacher notes absent from export")
        for ref in page["point_ids"]:
            if "".join(material["facts"][ref].split()) not in fact_carrier:
                errors.append(f"{page['id']}: required fact {ref} absent from exported {'text and notes' if plan.get('native') else 'text'}; expected original string: {material['facts'][ref]}")
        if "".join(page["title"].split()) not in joined:
            errors.append(f"{page['id']}: title absent from export")
        images = [hashlib.sha256(s.image.blob).hexdigest() for s in items if s.shape_type == MSO_SHAPE_TYPE.PICTURE]
        for ref in page["asset_ids"]:
            if material["assets"][ref]["sha256"] not in images:
                errors.append(f"{page['id']}: image {ref} absent from export")
        if plan.get('native') and page['asset_ids']:
            from .geometry import regions
            scale = 1280 / deck.slide_width
            height = deck.slide_height * scale
            boxes = {r['id']: r for r in regions(slide, scale)}
            fixed = {material['assets'][ref]['sha256'] for ref in page['asset_ids']}
            for shape in items:
                if shape.shape_type != MSO_SHAPE_TYPE.PICTURE or hashlib.sha256(shape.image.blob).hexdigest() not in fixed:
                    continue
                region = boxes[shape.shape_id]
                if any(region['crop']):
                    errors.append(f"{page['id']}: fixed image object {shape.shape_id} is cropped in exported PPTX")
                if not region['approximate']:
                    x, y, w, h = region['frame_box']
                    if x < -1 or y < -1 or x + w > 1281 or y + h > height + 1:
                        errors.append(f"{page['id']}: fixed image object {shape.shape_id} is outside the slide canvas; "
                                      f"frame={region['frame_box']}, canvas=1280x{height:g}. "
                                      'Retaining image bytes and zero crop does not prevent off-canvas clipping.')
        evidence.append({"id": page["id"], "title": page["title"], "texts": texts, "notes": notes,
                         "editable_text_objects": len(texts), "image_objects": len(images),
                         "image_regions": [{"box": [round(value * 1280 / deck.slide_width, 2)
                             for value in (shape.left, shape.top, shape.width, shape.height)],
                             "shape_id": shape.shape_id,
                             "crop": [shape.crop_left,shape.crop_top,shape.crop_right,shape.crop_bottom],
                             "original_pixel_size": list(shape.image.size),
                             "original_asset_ids": [ref for ref in page["asset_ids"]
                                 if material["assets"][ref]["sha256"] == hashlib.sha256(shape.image.blob).hexdigest()]}
                             for shape in items if shape.shape_type == MSO_SHAPE_TYPE.PICTURE]})
    return evidence, errors


def composite(version, plan, material, style, source_previews, checked_ids=None, inherited=None):
    pptx = version / "deck.pptx"
    evidence, errors = extract(pptx, plan, material)
    save(version / "qa/structural.json", {"passed": not errors, "errors": errors, "evidence": evidence})
    if errors:
        issues = []
        global_errors = [error for error in errors if not any(error.startswith(p["id"] + ":") for p in plan["pages"])]
        for page in plan["pages"]:
            page_errors = global_errors + [error for error in errors if error.startswith(page["id"] + ":")]
            if page_errors:
                issues.append({"page_id": page["id"], "severity": "major", "scope": "structure",
                    "evidence": "; ".join(page_errors),
                    "action": "Retain every exact original fact value and the contract teacher notes in notes; fact IDs or paraphrases alone do not satisfy retention. Keep contract titles visible and original image bytes in the actual PPTX."})
        return {"passed": False, "issues": issues, "api_executed": False}
    previews = render(pptx, version / "preview")
    fit_issues = render_fit_issues(version, plan, source_previews)
    coverage, coverage_issues = semantic_coverage(version, plan, material, evidence)
    actual_metrics = read(version / 'preview/text-metrics.json') if (version / 'preview/text-metrics.json').exists() else None
    actual_deck = Presentation(pptx) if pptx.exists() else None
    referenced = sorted(source_previews.glob("*.png"))
    source_sheet = version / "qa/template-reference.jpg"
    chosen = [referenced[n] for n in sorted({0, len(referenced) // 3, len(referenced) // 2, max(0, len(referenced) - 3)})]
    sheet = Image.new("RGB", (1280, 720), "white")
    for i, path in enumerate(chosen[:4]):
        with Image.open(path) as image:
            image.thumbnail((640, 360))
            sheet.paste(image, (i % 2 * 640, i // 2 * 360))
    sheet.save(source_sheet, quality=90)
    instruction = (
        "你是PPTNative视觉质检器。第一张为模板风格参考，第二张为当前成品高清页。"
        "若payload含source_page_reference=true，第三张为此页实际复用的源模板/父版本页面，随后是固定教材原图；"
        "否则第三张起为教材原图。必须用对应源页区分源问题和填充后新增的问题。"
        "图片页允许按相同风格重新构图。检查图片/坐标轴/代码完整性、可读性、文字遮挡、配色"
        "和视觉层次。原图的内容和原色固定，不要求重绘教材素材。"
        "必须对照教材原图：原图内部原有的边框、箭头颜色和分辨率限制不属于生成错误，"
        "不得要求修改原图内部配色、删除教材图或用生成图/新代码替换教材图。"
        "此类问题记录到source_limitations数组；生成时缩得过小导致原图可读性下降才作为布局问题。"
        "对图内模糊或低对比度必须明确对比原图，说明是源图已有还是本页缩放新增，不能只凭模糊印象判major。"
        "只报告可定位的实际缺陷。客观阅读障碍或严重不协调用major，微小审美建议用minor，"
        "不要把偏好差异作为major。输出issues数组，每项含page_id、severity(major/minor)、"
        "每幅图前有身份标签，不得混淆原始模板、教材图和CURRENT OUTPUT。"
        "actual_export_texts为实际PPTX文字对象，文字可能被遮挡，但不得臆造其中不存在的占位文案。"
        "报告裁剪/遮字须引用该对象真实文字及可见缺失的部位，不能仅凭卡片较小推断截断。"
        "scope=visual、location、evidence、action。没有问题则空数组。"
        "每个问题另给location_box=[x,y,width,height]，按第二张成品图1280px宽坐标定位，供编辑器匹配实际对象。"
        "每个major必须含defect_type(overlap/crop/contrast/content/other)及object_ids，引用actual_objects的真实ID。"
        "overlap必须引用两个实际相交的对象；若几何边界不相交，不得把靠近当遮挡。裁切须区分PPT新增crop和原图固有边缘。"
    )

    def inspect(pair):
        i, page = pair
        from .geometry import regions, possible_overlaps, validate_visual_geometry
        objects = regions(actual_deck.slides[i],1280/actual_deck.slide_width,
                          actual_metrics['slides'][i]['objects'] if actual_metrics else ()) if actual_deck else []
        source_page = None
        if plan.get("native") and (version / "version.json").exists():
            parent = read(version / "version.json").get("parent")
            source_page = (version.parent / parent / "preview" / f"deck_p{page['source_slide']}.png"
                           if parent else source_previews / f"template_p{page['source_slide']}.png")
            if not source_page.exists():
                source_page = None
        result = ask(version / "qa/api", f"visual-{page['id']}", instruction,
                   {"page_id": page["id"], "expected_title": page["title"], "style": style,
                    "source_page_reference": source_page is not None,
                    "actual_export_texts": evidence[i]['texts'], 'actual_objects':objects,
                    'possible_overlap_pairs':possible_overlaps(objects)},
                   visual=True, images=[source_sheet, previews[i],
                                        *([source_page] if source_page else []),
                                        *[Path(material["assets"][ref]["path"]) for ref in page["asset_ids"]]],
                   image_labels=['TEMPLATE STYLE OVERVIEW', 'CURRENT OUTPUT — judge defects ONLY on this page',
                                 *(['ACTUAL SOURCE PAGE before editing'] if source_page else []),
                                 *[f'FIXED ORIGINAL MATERIAL {ref}' for ref in page['asset_ids']]],
                   validate=(lambda data: validate_visual_geometry(data,objects)) if objects else None)
        for issue in result.get("issues", []):
            issue.setdefault("page_id", page["id"])
            issue.setdefault("scope", "visual")
        return result

    scenes = read(version / "scenes.json")
    fingerprints = {p['id']: visual_fingerprint(p, scenes[p['id']], previews[i], style, evidence[i]['notes'])
                    for i, p in enumerate(plan['pages'])}
    cached = (inherited or {}).get("visual_coverage", {})
    checked = set(checked_ids) if checked_ids is not None else set(fingerprints)
    checked |= {page_id for page_id, digest in fingerprints.items() if cached.get(page_id) != digest}
    checked |= {i['page_id'] for i in (inherited or {}).get('issues', []) if i['severity'] == 'major'}
    visual = parallel([(i, p) for i, p in enumerate(plan["pages"]) if p["id"] in checked], inspect)
    findings = [item for review in visual for item in review.get("issues", [])]
    provenance = {p["id"]: {"image_regions": [e for e in scenes[p["id"]]["elements"] if e["kind"] == "image"],
                             "generated_colors": sorted({str(e[key]) for e in scenes[p["id"]]["elements"]
                                                          for key in ("fill", "line", "color") if key in e})}
                  for p in plan["pages"]}
    source_deck = version / "workspace/sources/source.pptx"
    if plan.get("native") and source_deck.exists():
        original = Presentation(source_deck)
        provenance = {page["id"]: {"image_regions": actual["image_regions"],
            "source_texts": [shape.text for shape in walk(original.slides[page["source_slide"] - 1].shapes)
                             if shape.has_text_frame and shape.text.strip()],
            "exported_texts": actual["texts"], "route": page.get("edit_mode"),
            "evidence_policy": "Compare source_texts with exported_texts. New text overflow is introduced even when its frame is inherited. Missing color metadata does not establish a source limitation."}
            for page, actual in zip(plan["pages"], evidence)}

    def validate_review(data):
        adjudicate(findings, data["visual_decisions"])
        for decision in data["visual_decisions"]:
            if findings[decision["index"]]["severity"] == "major" and decision["decision"] in ("minor", "source_limitation"):
                if any(text in decision["reason"] for text in ("旧版", "计划已", "应无", "无法确认")):
                    raise ValueError("Unsupported downgrade: visual_findings come from the CURRENT exported render. "
                                     "Planned edits, assumptions of stale images, or uncertainty cannot establish a fix. "
                                     f"Use actual source/export evidence. Invalid decision: {decision}")
        for issue in data["issues"]:
            explanation = "".join((issue.get("evidence", "") + issue.get("action", "")).lower().split())
            if issue.get("severity") == "major" and any(text in explanation for text in
                    ("无major", "不算major", "不构成major", "notamajor", "无需修改")):
                raise ValueError("Contradictory review: an issue marked major explicitly says it is not major "
                                 "or needs no change. Return only actual defects with consistent severity; "
                                 f"omit passing checks rather than labeling them major. Invalid issue: {issue}")

    content = ask(version / "qa/api", "content", (
        "你是PPTNative内容质检器。比较确认页计划及固定事实与实际导出正文/备注，"
        "检查事实遗漏、无依据参数、作业偏移、图片与图注对应。输入原有的争议要单独标注"
        "source_limitation，不能当成制作错误。"
        + ("本任务使用原版原生编辑：上屏允许等义压缩，完整原始事实保留在备注。"
           "必须逐条检查核心教学信息在上屏表达中是否有对应，不能因为备注有原文就放过关键概念的上屏遗漏；"
           "但不能要求所有原句逐字上屏或以字号不足20px为由否定原模板。" if plan.get("native") else
           "fact文本由编译器照原文输出，可以通过换行组成。") +
        "只有生成时引入且影响教学的错误使用major，轻微建议使用minor。"
        "issues只列真实缺陷，不列通过项；若evidence结论是不算major或无需修改，不得标major。"
        "输出issues数组，含page_id、severity(major/minor)、scope=content、evidence、action，"
        "和source_limitations数组。对用户已确认的标题修改按当前标题验收。不要改写课件。"
        "同时复核visual_findings的归因和严重性，返回visual_decisions数组，每项含index(0起始)、"
        "decision(uphold/major/minor/source_limitation)、reason。uphold保留原严重性，major明确判重大。必须逐条判断，不直接批准所有意见。"
        "生成对象颜色和原图边界由编译器提供；不可编辑教材原图内部的原色和细节是源限制，"
        "不得要求改色/替换原图。仅空间未填满、装饰较少、视觉冲击力等审美偏好应为minor。"
        "原图内部固有的低对比度和压缩模糊同样属于source_limitation；"
        "如果视觉证据仅描述颜色/对比度且要求替换原图，不能直接以影响阅读为由维持major，"
        "必须有生成时新增的缩放/遮挡/裁切证据才能归为制作错误。"
        "实际遮挡、裁切、文字遗漏、由于布局缩小而无法阅读仍为major，不得因为源图固定而放过。"
        "visual_findings来自本次当前PPTX刚生成的渲染，绝不是旧版；不能凭制作理由或计划声称已修复。"
        "源背景/文本框/装饰被沿用不等于当前填入的新文字遮挡是源限制；必须对照source_texts与当前上屏。"
        "actual_powerpoint_text_metrics给出文字的真实frame和rendered_bounds。遮挡指控必须与实际文字边界相符，"
        "不得仅凭节点靠近文本就认定相交；若所谓被遮挡词与相邻数字的实际边界分离，应驳回该指控。"
        "export.image_regions中的crop四项均为0表示保留完整图片像素；原图已有的底边/末行紧贴边缘不能说成新增裁切。"
        "只有实际越界或被其他对象遮挡才支持新增裁切；必须对照固定原图，不能因'若存在下文'的猜测维持major。"
    ), {"plan": {"title": plan.get("title"), "native": plan.get("native", False),
                  "pages": [{key: value for key, value in page.items()
                             if key not in ("rationale", "edit_mode", "route", "svg")} for page in plan["pages"]]},
        "facts": material["facts"], "assets": material["assets"], "export": evidence,
        "visual_findings": findings, "object_provenance": provenance,
        "actual_powerpoint_text_metrics": actual_metrics},
        required=("issues", "source_limitations", "visual_decisions"),
        validate=validate_review, reasoning=True, timeout=600)
    content['visual_decisions'] = confirm_visual_downgrades(
        version, plan, source_previews, findings, content.get('visual_decisions', []), provenance, actual_metrics, material['assets'])
    adjudicated, limitations = adjudicate(findings, content.get("visual_decisions", []))
    known = {p["id"] for p in plan["pages"]}
    reused = unchanged_findings(inherited, fingerprints)
    issues = adjudicated + content.get("issues", []) + reused + fit_issues + coverage_issues
    for issue in issues:
        if issue.get("page_id") not in known or issue.get("severity") not in ("major", "minor"):
            raise ValueError("Invalid API review issue target or severity")
    report = {"review_version": REVIEW_VERSION, "passed": not any(i["severity"] == "major" for i in issues),
              "issues": issues, "api_executed": True,
              "visual_checked_ids": sorted(checked),
              "visual_coverage": fingerprints,
              "visual_reused_ids": [p["id"] for p in plan["pages"] if p["id"] not in checked],
              "visual_adjudication": content.get("visual_decisions", []),
              "semantic_coverage": coverage,
              "source_limitations": content.get("source_limitations", []) + limitations +
                  [limitation for review in visual for limitation in review.get("source_limitations", [])]}
    save(version / "qa/summary.json", report)
    return report
