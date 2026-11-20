"""API-selected merge skeleton and original upstream object adoption."""

from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET

from .models import ask, read, save
from .native_repair import run_upstream


def prepare(job, version, contract, source):
    from .native import SKILL
    if (version / "native-workspace.json").exists():
        return
    workspace = version / "workspace"
    run_upstream(version, "attribution_guard.py", [], "merge-attribution.log")
    if not (workspace / "authoring-svg-flat").exists():
        run_upstream(version, "pptx_to_svg.py", [source, "-o", workspace, "--inheritance-mode", "both", "--roundtrip"], "merge-import.log")
    roster = {page["id"]: page for page in read(version / "parent-roster.json")}
    selected = contract["request"]["page_ids"]
    merged = [page for page in contract["pages"] if page["id"] not in roster]
    if len(merged) != 1:
        raise ValueError("Native merge requires exactly one new merged page")
    page = merged[0]
    directory = workspace / "authoring-svg-flat"
    candidates = {roster[page_id]["current_source_slide"]: (directory / f"slide_{roster[page_id]['current_source_slide']:02}.svg")
                  for page_id in selected}
    choice = ask(version / "api", "merge-skeleton", (
        "按所附原版PPT Master Edit Native PPTX流程执行已批准合并。"
        "只选择一个当前源页作为骨架，并选择需通过原版--adopt-object迁移的对象。"
        "返回skeleton_slide（候选当前页号之一）、adoptions数组（source_slide、element_id）、rationale、limitations数组。"
        "骨架选择与对象选择由你决定；优先保留有用布局和教材图片。不能迁移继承代理，不复制原始SVG跨页身份。"
        "后续你将获得采用对象后的SVG并完成文字布局，所有事实、原图及备注必须保留。"
        "用户已批准本轮模拟合并，不再等待确认；不新增动画或旁白。"
    ), {"workflow": (SKILL / "workflows/edit-native-pptx.md").read_text(encoding="utf-8"),
        "merged_contract": page, "request": contract["request"],
        "source_svgs": {str(number): path.read_text(encoding="utf-8") for number, path in candidates.items()},
        "previous_error": read(version / "qa/export-validation.json") if (version / "qa/export-validation.json").exists() else None},
        required=("skeleton_slide", "adoptions", "rationale", "limitations"), timeout=600)
    skeleton = choice["skeleton_slide"]
    if skeleton not in candidates:
        raise ValueError("API selected a skeleton outside the requested merge pages")
    target = directory / ("merge_" + re.sub(r"[^A-Za-z0-9_-]", "_", page["id"]) + ".svg")
    shutil.copyfile(candidates[skeleton], target)
    mapping = []
    for item in contract["pages"]:
        changed = item["id"] == page["id"]
        number = skeleton if changed else roster[item["id"]]["current_source_slide"]
        mapping.append({"id": item["id"], "source_slide": number,
            "svg": target.name if changed else f"slide_{number:02}.svg",
            "edit_mode": "template-fill" if changed else "referenced",
            "rationale": choice["rationale"] if changed else "Unchanged delivered source page referenced natively."})
    # The original adopt command validates extra SVG copies against this plan.
    save(workspace / "page_plan.json", {"schema": "ppt-master.roundtrip-page-plan.v1",
        "pages": [{"source_slide": item["source_slide"], "svg": item["svg"]} for item in mapping]})
    for index, adoption in enumerate(choice["adoptions"]):
        number, element_id = adoption["source_slide"], adoption["element_id"]
        if number not in candidates or number == skeleton:
            raise ValueError("Invalid API object-adoption source")
        if element_id not in {node.get("id") for node in ET.parse(candidates[number]).iter()}:
            raise ValueError("API selected an unknown source object")
        run_upstream(version, "svg_authoring_view.py", [directory, "--adopt-object",
            candidates[number].name + ":" + element_id, "--into", target.name], f"merge-adopt-{index + 1}.log")
    notes = workspace / "notes"
    notes.mkdir(exist_ok=True)
    source_notes = notes / (candidates[skeleton].stem + ".md")
    (notes / (target.stem + ".md")).write_text(source_notes.read_text(encoding="utf-8") if source_notes.exists() else "", encoding="utf-8")
    material = read(job / "input/material-pool.json")
    for asset in material["assets"].values():
        destination = workspace / "images" / Path(asset["path"]).name
        if not destination.exists():
            shutil.copyfile(asset["path"], destination)
    save(version / "native-workspace.json", {"workspace": str(workspace), "pages": mapping, "limitations": choice["limitations"]})
    save(version / "authoring-request.json", {"kind": "authorized-merge-authoring", "issues": [{
        "page_id": page["id"], "severity": "major", "scope": "authoring",
        "evidence": "Complete the approved merged page on the API-selected skeleton and adopted objects. Preserve every fact, original image, notes and the contract title; arrange the combined content using the original workflow."}]})
