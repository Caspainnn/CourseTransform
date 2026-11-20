"""Focused API edits in the upstream round-trip workspace, with upstream export."""

import os
import ast
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

from .models import ask, parallel, read, save


def error_context(skill, error):
    """Attach original implementation around the exact reported error, without editing it."""
    if not error:
        return []
    messages = re.findall(r"(?:^|\n)[^\n]*?\bError:\s*([^\n]+)", error)
    if messages:
        error = messages[-1]
    words = re.findall(r"[A-Za-z]{12,}", error)
    excerpts = []
    for path in (skill / "scripts").rglob("*.py"):
        source = path.read_text(encoding="utf-8-sig", errors="replace")
        if words and not any(word in source for word in words):
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                isinstance(part, ast.Constant) and isinstance(part.value, str) and len(part.value) > 35
                and part.value.strip() in error for part in ast.walk(node)):
                excerpts.append({"file": str(path.relative_to(skill)), "function": node.name,
                                 "source": ast.get_source_segment(source, node)[:14000]})
    return sorted(excerpts, key=lambda item: len(item["source"]))[:1]


def apply_replacements(svg, replacements):
    from collections import Counter
    source_refs = Counter(node.get("data-pptx-source-ref") for node in ET.fromstring(svg).iter() if node.get("data-pptx-source-ref"))
    for edit in replacements:
        if not edit["old"] or svg.count(edit["old"]) != 1:
            raise ValueError(f"SVG replacement must match exactly one existing fragment; "
                             f"found {svg.count(edit['old'])} matches for {edit['old'][:500]!r}. "
                             "Copy old literally from the supplied current SVG; omit nonexistent objects.")
        svg = svg.replace(edit["old"], edit["new"], 1)
    try:
        tree = ET.fromstring(svg)
    except ET.ParseError as error:
        raise ValueError(f"API SVG repair is not well-formed XML: {error}") from error
    updated_refs = Counter(node.get("data-pptx-source-ref") for node in tree.iter() if node.get("data-pptx-source-ref"))
    if any(count > source_refs[ref] for ref, count in updated_refs.items()):
        raise ValueError("New elements must not invent or duplicate native source refs")
    return svg


def run_upstream(version, script, arguments, log):
    from .native import HOST, SKILL
    environment = os.environ.copy()
    environment.update(PYTHONPATH=str(HOST / "python"), PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    result = subprocess.run([sys.executable, str(SKILL / "scripts" / script), *map(str, arguments)],
        cwd=version, env=environment, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=240, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    (version / log).write_text(result.stdout, encoding="utf-8")
    (version / (log + ".stderr")).write_text(result.stderr, encoding="utf-8")
    if result.returncode:
        raise ValueError(f"Upstream {script} failed:\n{result.stdout[-14000:]}\n{result.stderr[-3000:]}")
    return result.stdout


def repair(job, version, contract):
    from .native import HOST, SKILL
    workspace = version / "workspace"
    mapping = read(version / "native-workspace.json" if (version / "native-workspace.json").exists() else version / "native-result.json")
    choices = {p["id"]: p for p in mapping["pages"]}
    material = read(job / "input/material-pool.json")
    qa = read(version / "qa/summary.json" if (version / "qa/summary.json").exists() else version / "authoring-request.json")
    error_path = version / "qa/export-validation.json"
    upstream_error = read(error_path) if error_path.exists() else None
    if upstream_error and upstream_error.get("resolved"):
        upstream_error = None
    export_errors = (version / "export.log").read_text(encoding="utf-8", errors="replace") if (version / "export.log").exists() else ""
    implementation = error_context(SKILL, (upstream_error or {}).get("error", ""))
    if not implementation and not upstream_error:
        implementation = error_context(SKILL, export_errors)
    targets = {issue["page_id"] for issue in qa["issues"] if issue["severity"] == "major"}
    failed_svgs = set(re.findall(r"\[ERROR\] ([^\s]+\.svg) - Failed", (upstream_error or {}).get("error", "")))
    failed_svgs.update(re.findall(r"\bError:\s+([^\s]+\.svg)", (upstream_error or {}).get("error", "")))
    failed_svgs.update(re.findall(r"\]\s+([^\s]+\.svg)\s+- Error:", (upstream_error or {}).get("error", "")))
    if failed_svgs:
        targets &= {page_id for page_id, choice in choices.items() if choice["svg"] in failed_svgs}
    request_ids = set(contract.get("request", {}).get("page_ids", []))
    if request_ids:
        targets &= {p["id"] for p in contract["pages"] if p["id"] in request_ids or request_ids.intersection(p["origins"])}
    references = {name: (SKILL / name).read_text(encoding="utf-8") for name in (
        "SKILL.md", "workflows/edit-native-pptx.md", "references/shared-standards-core.md")}
    animations = read(workspace / "animations.json")
    instruction = (
        "你是PPTNative的原版PPT Master SVG编辑器，当前任务仅修复一页。遵守所附原版skill、"
        "Edit Native PPTX流程与标准。页面与模板已导入且页计划已确认，直接修复所列问题，"
        "不要重新规划、扫描目录或要求再次确认。所有设计与文案修改由你决定。"
        "只输出JSON：replacements数组，每项old/new是对当前SVG的唯一精确字符串替换；"
        "notes为完整备注原文字符串；edit_mode为template-fill或local-recompose；rationale字符串；limitations数组。"
        "可选animation_row为当前页面animations.json条目的完整替换对象，不需要时null。"
        "若导出提示removed source animation target，按原版工作流为该页显式设置animation.effect=none并移除失效groups，"
        "在limitations记录无法保留的源动画。不得新增源文件以外的动画或旁白。"
        "old必须在原SVG恰好出现一次。可以添加规范SVG文本/图片节点，保留未改对象身份与继承代理。"
        "新增文本/图片节点不能添加或复制data-pptx-source-ref；该身份只属于原始源对象，不能编造新编号。"
        "普通shape内嵌image可能被导出丢弃，新增图片须使用原版支持的规范对象结构，不能伪造source-ref。"
        "同理，data-pptx-object=shape的导入包装器只应承载原版语义几何与text；"
        "新增背景rect等独立对象不可随意塞进该包装器，否则可能不会编译。实际导出对象清单才是生效证据。"
        "只处理实际major缺陷，不新增装饰。标题必须可见且与合同逐字一致；正文可等义压缩。"
        "notes必须包含输入facts的每一个完整原始VALUE字符串和page.notes的完整原文，不能仅放ID或等义改写。"
        "若问题只是事实原文或教师备注保留，只修改notes，不能为此扩写上屏正文。"
        "若upstream_error列出本页容量/导出错误，优先仅修复这些错误；已修复的标题和图片不再重复调整。"
        "upstream_error是最新一次真实阻断，不能宣称估算误报或仅解释不修改；必须修改后由原版重新验证。"
        "存在upstream_error时旧deck的结构findings和历史导出日志不代表当前SVG状态，不要据此反复改已修内容。"
        "SVG不是HTML，不会按文本框自动换行。规范多行text必须显式给各行tspan的x与y/dy，"
        "或完整保留原版data-pptx-text-model=paragraphs/lines和对应的逐行嵌套结构；"
        "不能去掉text-model却仅用无坐标tspan期待换行，也不能把多行合并为一行后宣称修复溢出。"
        "视觉报告中的残缺代码可能是长标识符在实际PPTX中自动换行/裁切后的子串，"
        "不能因SVG没有独立的同名节点就宣称已解决；须检查包含子串的完整text、换行和容器高度。"
        "若PowerPoint文字实际边界已在框内，但视觉只显示词尾/片段，还要检查较后绘制的不透明图形或继承对象遮挡，"
        "不能只反复调整换行。previous_export_text_objects包含实际绘制顺序、对象位置与填充。"
        "native_group_transforms提供源PPTX与当前PPTX组变换诊断。若源组与当前组的chOff/chExt不同，"
        "未改的原生子对象恢复后可能沿用源组坐标，造成偏移/遮挡；不要把这种情况误判为文本自动换行。"
        "请按原版对象身份与局部重组规则处理受影响的组/子对象，全部页面修改仍由你决定。"
        "教学原图像素、原始路径和出处固定，不得替换、裁掉内容或重绘。"
        "若SVG已修复而旧导出仍报错，可不再改变相应节点；最终由原版工具重新检查与导出。"
    )

    def edit(page):
        choice = choices[page["id"]]
        path = (workspace / "authoring-svg-flat" / choice["svg"]).resolve()
        if not path.is_relative_to((workspace / "authoring-svg-flat").resolve()):
            raise ValueError("Invalid authoring SVG path")
        svg = path.read_text(encoding="utf-8")
        facts = {ref: material["facts"][ref] for ref in page["point_ids"]}
        exported_objects = []
        group_transforms = []
        page_index = None
        if (version / "deck.pptx").exists() and (version / "plan.json").exists():
            from pptx import Presentation
            from .drawing import walk
            delivered = read(version / "plan.json")["pages"]
            page_index = next((i for i, item in enumerate(delivered) if item["id"] == page["id"]), None)
            if page_index is not None:
                deck = Presentation(version / "deck.pptx")
                scale = float(ET.fromstring(svg).get("viewBox", "0 0 1280 720").split()[2]) / deck.slide_width
                for draw_order, shape in enumerate(walk(deck.slides[page_index].shapes)):
                    item = {"id": shape.shape_id, "name": shape.name, "kind": str(shape.shape_type), "draw_order": draw_order,
                        "box": [round(value * scale, 2) for value in (shape.left, shape.top, shape.width, shape.height)]}
                    if hasattr(shape, "fill"):
                        item["fill_type"] = str(shape.fill.type)
                        if shape.fill.type == 1 and shape.fill.fore_color.type == 1:
                            item["solid_fill"] = str(shape.fill.fore_color.rgb)
                    if shape.has_text_frame and shape.text.strip():
                        item.update(text=shape.text, word_wrap=shape.text_frame.word_wrap,
                            fonts=[{"name": run.font.name, "points": run.font.size.pt if run.font.size else None}
                                   for paragraph in shape.text_frame.paragraphs for run in paragraph.runs])
                    exported_objects.append(item)
                source_path = workspace / "sources/source.pptx"
                if source_path.exists():
                    original = Presentation(source_path)
                    source_groups = {shape.shape_id: shape for shape in walk(original.slides[choice["source_slide"] - 1].shapes)
                                     if shape.shape_type == 6}
                    for shape in walk(deck.slides[page_index].shapes):
                        if shape.shape_type == 6 and shape.shape_id in source_groups:
                            source_group = source_groups[shape.shape_id]
                            group_transforms.append({"id": shape.shape_id,
                                "source_transform": source_group._element.grpSpPr.xml,
                                "exported_transform": shape._element.grpSpPr.xml,
                                "source_children": [child.shape_id for child in source_group.shapes],
                                "exported_children": [child.shape_id for child in shape.shapes]})

        def validate_edit(data):
            updated = apply_replacements(svg, data["replacements"])
            note_text = "".join(data["notes"].split())
            if any("".join(value.split()) not in note_text for value in [*facts.values(), page.get("notes", "")]):
                raise ValueError(f"{page['id']}: API repair must retain exact original fact VALUES and teacher notes in notes")
            if data["edit_mode"] not in ("template-fill", "local-recompose"):
                raise ValueError("Invalid API edit mode")
            animation_changed = data.get("animation_row") is not None and data["animation_row"] != animations.get("slides", {}).get(path.stem)
            needs_visible_change = upstream_error or any(issue["severity"] == "major" and
                issue.get("scope") in ("visual", "content", "authoring") for issue in qa["issues"] if issue["page_id"] == page["id"])
            if needs_visible_change and updated == svg and not animation_changed:
                raise ValueError("The latest upstream check is still blocking, but replacements change nothing "
                                 "(old equals new). Return actual SVG changes that address the reported error, "
                                 "not just a rationale claiming reflow. Preserve facts in notes; visible text may be concise.")

        result = ask(version / "api/repair", page["id"], instruction, {
            "upstream_references": references, "page": page, "facts": facts,
            "assets": {ref: {**material["assets"][ref],
                "workspace_href": "../images/" + Path(material["assets"][ref]["path"]).name}
                for ref in page["asset_ids"]},
            "svg": svg, "existing_notes": (workspace / "notes" / (path.stem + ".md")).read_text(encoding="utf-8"),
            "previous_export_text_objects": exported_objects,
            "native_group_transforms": group_transforms,
            "previous_powerpoint_text_metrics": [row for row in read(version / "preview/text-metrics.json")["slides"]
                if row["page"] == page_index + 1] if (version / "preview/text-metrics.json").exists() and page_index is not None else [],
            "findings": [] if upstream_error else [i for i in qa["issues"] if i["page_id"] == page["id"]],
            "animation_row": animations.get("slides", {}).get(path.stem),
            "upstream_error": upstream_error,
            "upstream_error_implementation": implementation,
            "export_errors": "" if upstream_error else export_errors,
        }, required=("replacements", "notes", "edit_mode", "rationale", "limitations"), timeout=600,
            validate=validate_edit, reasoning=True)
        updated = apply_replacements(svg, result["replacements"])
        return page["id"], path, updated, result

    results = parallel([page for page in contract["pages"] if page["id"] in targets], edit)
    for page_id, path, svg, result in results:
        path.write_text(svg, encoding="utf-8")
        (workspace / "notes" / (path.stem + ".md")).write_text(result["notes"], encoding="utf-8")
        choices[page_id].update(edit_mode=result["edit_mode"], rationale=result["rationale"])
        mapping["limitations"].extend(result["limitations"])
        if result.get("animation_row") is not None:
            if not isinstance(result["animation_row"], dict):
                raise ValueError("Invalid API animation sidecar row")
            animations.setdefault("slides", {})[path.stem] = result["animation_row"]
    save(workspace / "animations.json", animations)
    save(version / "native-workspace.json", {key: value for key, value in mapping.items() if key != "output_pptx"})

    def run(script, arguments, log):
        return run_upstream(version, script, arguments, log)

    run("attribution_guard.py", [], "repair-attribution.log")
    run("svg_authoring_view.py", [workspace / "authoring-svg-flat", "--refresh-summary"], "repair-summary.log")
    run("svg_quality_checker.py", [workspace, "--roundtrip", "--json"], "repair-capacity.log")
    stdout = run("svg_to_pptx.py", [workspace, "--roundtrip"], "export.log")
    paths = re.findall(r"^\s*\[PPTX\]\s+(.+)$", stdout, re.MULTILINE)
    if not paths:
        raise ValueError("Original exporter did not report its actual PPTX path")
    output = Path(paths[-1].strip())
    run("pptx_delivery_check.py", [output], "repair-delivery.json")
    run("source_to_md/ppt_to_md.py", [output, "-o", workspace / "validation/repair-readback.md"], "repair-readback.log")
    mapping.update(output_pptx=str(output), workspace=str(workspace))
    save(version / "native-result.json", mapping)
