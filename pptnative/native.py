"""PPT Master round-trip adapter. All page authoring belongs to the API worker."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import traceback
import uuid
from zipfile import ZipFile

from pptx import Presentation

from .api import ROOT, settings
from .models import read, save
from .pipeline import check_cancel
from .planning import intake, recognize, revise_plan, validate_plan

ENGINE = "ppt-master-roundtrip"
SKILL = Path(os.environ.get("PPTMASTER_SKILL_DIR", ROOT / "runtime/ppt-master"))
HOST = Path(os.environ.get("PPTNATIVE_RUNTIME_HOST", ROOT / "runtime/host"))


def content_contract(plan):
    fields = ("id", "title", "point_ids", "asset_ids", "notes", "refs", "purpose", "origins")
    return {"title": plan["title"], "native": True,
            "pages": [{k: page[k] for k in fields if k in page} for page in plan["pages"]],
            **({"request": plan["request"]} if "request" in plan else {})}


def parse_receipt(text, count):
    pattern = r"Round-trip export summary:\s*output_pages=(\d+)\s+passthrough=(\d+)\s+cloned_passthrough=(\d+)\s+patched=(\d+)\s+rebuilt=(\d+)"
    found = re.findall(pattern, text)
    if not found:
        raise ValueError("Missing original round-trip exporter receipt")
    values = dict(zip(("output_pages", "passthrough", "cloned_passthrough", "patched", "rebuilt"), map(int, found[-1])))
    if values["output_pages"] != count or sum(v for k, v in values.items() if k != "output_pages") != count:
        raise ValueError("Original exporter receipt does not match the output roster")
    return values


def worker_session(version):
    """Recover the tool session even when the parent exited before saving it."""
    session_file = version / "worker-session.json"
    logs = sorted(version.glob("worker-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    for log in logs:
        for line in reversed(log.read_text(encoding="utf-8", errors="replace").splitlines()):
            try:
                session = json.loads(line).get("sessionID")
            except (ValueError, AttributeError):
                continue
            if session:
                save(session_file, {"id": session})
                return session
    return read(session_file)["id"] if session_file.exists() else None


def worker(version, instruction, resume=False):
    """Run the established tool-capable API host; credentials stay in process config."""
    config = settings()
    environment = os.environ.copy()
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        environment.pop(key, None)
    state = ROOT / ".pptnative-runtime"
    for suffix, name in (("DATA", "data"), ("CACHE", "cache"), ("CONFIG", "config"), ("STATE", "state")):
        environment[f"XDG_{suffix}_HOME"] = str(state / name)
    environment.update(OPENCODE_DISABLE_MODELS_FETCH="1", OPENCODE_DISABLE_AUTOUPDATE="1",
                       OPENCODE_DISABLE_DEFAULT_PLUGINS="1", OPENCODE_DISABLE_PROJECT_CONFIG="1",
                       OPENCODE_DISABLE_EXTERNAL_SKILLS="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
                       PYTHONPATH=str(HOST / "python"), PPTNATIVE_API_KEY=config["LLM_API_KEY"])
    model = config["LLM_MODEL"]
    environment["OPENCODE_CONFIG_CONTENT"] = json.dumps({
        "share": "disabled", "provider": {"pptnative": {
            "npm": "@ai-sdk/openai-compatible", "name": "PPTNative API",
            "options": {"baseURL": config["LLM_BASE_URL"].rstrip("/"), "apiKey": "{env:PPTNATIVE_API_KEY}"},
            "models": {model: {"name": model, "tool_call": True,
                               "limit": {"context": 131072, "output": 16384},
                               "options": {"thinking": {"type": "disabled"}}}}}},
        "enabled_providers": ["pptnative"],
        "permission": {"external_directory": {"*": "deny", str(ROOT) + "/**": "allow"},
                       "read": {"*": "allow", "*.env": "deny", "*.env.*": "deny"},
                       "webfetch": "deny", "websearch": "deny", "task": "deny",
                       "bash": {"*": "deny", "python *": "allow", "python3 *": "allow",
                                "powershell *": "allow", "pwsh *": "allow", "Get-ChildItem *": "allow",
                                "Get-Item *": "allow", "Get-Location": "allow", "Test-Path *": "allow"}}})
    run_id = uuid.uuid4().hex[:10]
    task = version / f"worker-task-{run_id}.md"
    task.write_text(instruction, encoding="utf-8")
    executable = os.environ.get("PPTNATIVE_OPENCODE", str(HOST / "node_modules/opencode-ai/bin/opencode.exe"))
    command = [executable, "run", "--pure", "--format", "json", "--model", f"pptnative/{model}"]
    session = worker_session(version) if resume else None
    if session:
        command += ["--session", session]
    command.append(f"Read {task.as_posix()} and execute its authorized task using your tools. Do not start another agent or runner.")
    output, error = version / f"worker-{run_id}.jsonl", version / f"worker-{run_id}.stderr.log"
    try:
        with output.open("wb") as log, error.open("wb") as errors:
            result = subprocess.run(command, cwd=version, env=environment, stdout=log, stderr=errors,
                                    timeout=2400, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    finally:
        worker_session(version)
    if result.returncode:
        raise RuntimeError(f"Native API worker failed; see {error}")
    return output


def task_text(job, version, contract, source, parent=None):
    return f"""You are PPTNative's API production worker, powered by the configured model. You alone author this deck.
Read {SKILL.as_posix()}/SKILL.md, run its attribution guard, select Edit Native PPTX, and follow its workflow and required references. Use the original scripts and preserve their source files. Use Python on Windows with absolute paths.
The complete source PPTX is {source.as_posix()}. The approved teaching outline is {job.as_posix()}/input/outline.md. Its resolved original images, complete facts, notes and provenance are in {job.as_posix()}/input/material-pool.json. The immutable requested output roster is {version.as_posix()}/authoring-contract.json. Template preview images and its style profile are in {job.as_posix()}/source-preview and {job.as_posix()}/style.
The user explicitly delegated routine template selection and authorized this task. Record that authorization in your page plan and continue without asking another confirmation. Keep the roster, IDs and titles from the contract. Choose suitable real source layouts by message logic and capacity, not merely textbox count. You may shorten visible phrasing without changing teaching meaning; each fact's core concept must be visibly represented. Keep the original complete facts and teacher notes in speaker notes, keyed to the page's point_ids. Preserve all provided teaching images exactly; do not substitute or modify their pixels.
Every contract title must appear verbatim in visible editable slide text. Copy each referenced material-pool facts value verbatim into speaker notes, along with the contract's teacher notes; a paraphrase or point_id alone does not satisfy original-fact retention. Before finishing, check the ACTUAL exported PPTX for every expected title, fact and image, not just their presence in SVG or workspace files.
Text-only pages MUST attempt native template filling with existing geometry and object identity. Do not impose a new universal font-size floor or immediately replace a page when a slot fails. Follow the upstream overflow sequence: concise phrasing, appropriate selected layouts, then bounded typography changes as last resort. The source design is authoritative. Image pages may use a fresh slide-local composition only where needed, inside the original round-trip workspace, preserving masters, layouts and useful source artwork. Document any full local recomposition and its reason. Do not use python-pptx to generate the deck or replace the original exporter. Do not use PPTNative's legacy scene compiler. No svg_output directory or Generate route.
{"This is a revision. Import the previous DELIVERED PPTX as a NEW round-trip workspace. Its current page-number mapping is " + str(version / 'parent-roster.json') + ". Use current_source_slide from that roster, never an original template page number. Only pages affected by the contract request may be edited. Unchanged output pages must reference their existing source page without opening them for writing. Merge using one existing skeleton and the upstream --adopt-object command for any transferred objects. Keep all facts, images and notes; do not alter unrelated pages. Preserve source motion when possible; follow upstream documented animation-target recovery only if changed targets require it and report the exception." if parent else "This is the initial deck. Produce the approved seven teaching pages in order from the template library. Most pages must preserve a genuine source structure; do not redraw all text pages."}
Write all generated files under {version.as_posix()}; source inputs and all upstream scripts are immutable. Store the round-trip workspace under {version.as_posix()}/workspace and the actual exporter plan at workspace/page_plan.json (not its parent). If resuming, inspect existing files and continue the existing edits rather than importing again. Use UTF-8 explicitly for all text reads/writes and Python stdout; prefer your read/edit/write tools or Python over nested PowerShell output conversions. Run upstream refresh-summary, svg_quality_checker.py --roundtrip --json, svg_to_pptx.py --roundtrip, pptx_delivery_check.py, and readback. Errors must be fixed; warnings require reasons. Save the actual final exporter stdout in {version.as_posix()}/export.log. Do not fabricate its receipt. No narration or newly requested animations.
On successful original export write {version.as_posix()}/native-result.json with ONLY these top-level keys: output_pptx (absolute actual output path), workspace (absolute path), pages (ordered objects containing id, source_slide, svg, edit_mode=template-fill/local-recompose/referenced, rationale), limitations (array). Page IDs and roster must exactly match the contract. The source_slide and svg entries must match workspace/page_plan.json. Original notes may include the full fixed facts in addition to concise visible teaching text. Never inspect credentials, other trials, launcher code or session logs. Do not start another agent. End with exact output path and export receipt.
"""


def repair_task(version):
    return ("Continue the existing authorized Edit Native PPTX workspace and correct the current QA failures. "
            f"Read {version.as_posix()}/qa/summary.json, authoring-contract.json and the original material-pool.json. "
            "Structural findings are deterministic checks on the exported package, not semantic opinions: "
            "fact retention requires the exact original facts VALUES and teacher notes in notes; IDs or paraphrases do not pass. "
            "Contract titles must be visible and every original image must actually export with unchanged bytes. "
            "Use the existing diagnostics and original workflow to implement the repairs now; do not repeat source intake "
            "or broad exporter-code research. Inspect implementation only to resolve a concrete new export error. "
            "Keep the approved roster, unrelated objects and immutable source image pixels. "
            "Refresh the original summary/checker, rerun the original round-trip exporter/delivery check/readback, "
            "then overwrite export.log and native-result.json with actual final evidence. Do not start another worker.")


def verify_export(version, source, contract):
    result = read(version / "native-result.json")
    output = Path(result["output_pptx"]).resolve()
    workspace = Path(result["workspace"]).resolve()
    if not output.is_relative_to(version) or not workspace.is_relative_to(version):
        raise ValueError("Worker output must remain within its version directory")
    mapping = result["pages"]
    if [p["id"] for p in mapping] != [p["id"] for p in contract["pages"]]:
        raise ValueError("Native worker changed the approved roster")
    page_plan = read(workspace / "page_plan.json")["pages"]
    if len(page_plan) != len(mapping) or any(a["source_slide"] != b["source_slide"] or
            a["svg"] != b.get("svg", f"slide_{b['source_slide']:02}.svg") for a, b in zip(mapping, page_plan)):
        raise ValueError("Worker manifest differs from original round-trip page plan")
    receipt = parse_receipt((version / "export.log").read_text(encoding="utf-8-sig"), len(mapping))
    if len(Presentation(output).slides) != len(mapping):
        raise ValueError("Native output page count mismatch")
    # Verify preservation on the package itself, not a worker's fidelity claim.
    with ZipFile(source) as old, ZipFile(output) as new:
        preserved = [n for n in old.namelist() if re.match(r"ppt/(slideMasters|slideLayouts)/.*\.xml$", n)]
        changed = [n for n in preserved if n not in new.namelist() or old.read(n) != new.read(n)]
        if changed:
            raise ValueError(f"Original master/layout XML changed: {changed}")
    save(version / "native-engine-evidence.json", {"engine": ENGINE, "receipt": receipt,
         "preserved_master_layout_parts": len(preserved), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest()})
    plan = {**contract, "native": True, "pages": [{**page, **{k: choice[k] for k in
            ("source_slide", "svg", "edit_mode", "rationale")}} for page, choice in zip(contract["pages"], mapping)]}
    save(version / "plan.json", plan)
    save(version / "native-limitations.json", result.get("limitations") or [])
    shutil.copyfile(output, version / "deck.pptx")
    deck = Presentation(version / "deck.pptx")
    scenes = {p["id"]: {"engine": ENGINE, "native_slide_sha256": hashlib.sha256(s._element.xml.encode()).hexdigest(),
                          "elements": []} for p, s in zip(plan["pages"], deck.slides)}
    save(version / "scenes.json", scenes)
    return plan


def record_review_failure(version, plan, error):
    from .quality import REVIEW_VERSION
    import time
    target = version / 'qa/summary.json'
    if target.exists():
        archive = version / 'qa/history' / f'{time.time_ns()}-summary.json'
        archive.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(target,archive)
    mentioned = [p['id'] for p in plan['pages'] if re.search(r'\b(?:visual-)?'+re.escape(p['id'])+r'\b',str(error))]
    report = {'review_version':REVIEW_VERSION,'passed':False,'api_executed':False,
        'review_error':str(error),'issues':[{'page_id':pid,'severity':'major','scope':'api-review',
        'evidence':str(error),'action':'Retry composite API review of the actual exported deck; invalid reviewer output is not a page-edit instruction.'}
        for pid in (mentioned or [p['id'] for p in plan['pages']])]}
    save(target,report)
    return report


def build(job, contract, parent=None, request=None, version_id=None, review_existing=False):
    from .pipeline import event, show_review
    from .quality import REVIEW_VERSION, composite
    state = read(job / "job.json")
    version_id = version_id or f"v{len(list((job / 'versions').glob('v[0-9]*'))) + 1:03}"
    version = (job / "versions" / version_id).resolve()
    version.mkdir(parents=True, exist_ok=True)
    save(version / "authoring-contract.json", contract)
    save(version / "version.json", {"id": version_id, "parent": parent, "engine": ENGINE,
                                    "status": "building", "request": request})
    source = job / "versions" / parent / "deck.pptx" if parent else job / "input/template.pptx"
    parent_dir = job / "versions" / parent if parent else None
    if parent:
        save(version / "parent-roster.json", [{"current_source_slide": i, "id": p["id"], "title": p["title"]}
             for i, p in enumerate(read(parent_dir / "plan.json")["pages"], 1)])
    material = read(job / "input/material-pool.json")
    validate_plan(contract, material)
    instruction = task_text(job, version, contract, source, parent_dir)
    resuming_failed_review = (version / "qa/summary.json").exists() and not read(version / "qa/summary.json")["passed"]
    if resuming_failed_review and read(version / 'qa/summary.json').get('review_error'):
        workspace_manifest=version/'native-workspace.json'
        needs_migration=state.get('authoring_mode')=='split-native-v2' and workspace_manifest.exists() and read(workspace_manifest).get('text_frame_contract_version')!=1
        review_existing = not needs_migration
    if resuming_failed_review:
        instruction = repair_task(version)
    try:
        for attempt in range(3):
            check_cancel(job)
            event(job, "native-api-worker", version=version_id, attempt=attempt + 1)
            # A fresh final context prevents repeated failed self-diagnoses from anchoring repairs.
            fresh_context = attempt == 2 or (attempt == 0 and resuming_failed_review)
            if fresh_context:
                instruction = task_text(job, version, contract, source, parent_dir) + "\nCURRENT REPAIR TASK:\n" + instruction
            try:
                if state.get("authoring_mode") == "split-native-v2" and not (review_existing and attempt == 0):
                    from .native_author import run as author_native
                    event(job, "native-enforced-page-routing", version=version_id)
                    author_native(job, version, contract, source, parent)
                elif review_existing and attempt == 0:
                    pass
                elif parent and request and request["kind"] == "merge_pages" and not (version / "native-result.json").exists():
                    from .native_merge import prepare as prepare_merge
                    prepare_merge(job, version, contract, source)
                if state.get("authoring_mode") == "split-native-v2" or (review_existing and attempt == 0):
                    pass
                elif (version / "native-workspace.json").exists() or ((version / "native-result.json").exists() and (version / "qa/summary.json").exists() and not read(version / "qa/summary.json")["passed"]):
                    from .native_repair import repair
                    event(job, "native-scoped-api-repair", version=version_id)
                    repair(job, version, contract)
                else:
                    worker(version, instruction, resume=not fresh_context and bool(worker_session(version)))
                check_cancel(job)
                plan = verify_export(version, source, contract)
                if (version / "qa/export-validation.json").exists():
                    save(version / "qa/export-validation.json", {**read(version / "qa/export-validation.json"), "resolved": True})
            except (RuntimeError, ValueError, KeyError, FileNotFoundError) as error:
                phase='authoring' if isinstance(error,RuntimeError) else 'export-validation'
                save(version / "qa/export-validation.json", {"attempt": attempt + 1, "phase":phase,"error": str(error)})
                if attempt == 2:
                    raise
                instruction = ("Continue your original Edit Native PPTX task. Export verification failed. "
                               f"Read {version.as_posix()}/qa/export-validation.json and authoring-contract.json. "
                               "Correct the original workspace/page_plan.json and manifest so the actual output exactly matches "
                               "the contract roster and order; an unselected template page must not appear. "
                               "Keep existing authorized edits. Refresh the summary and quality report, rerun the original "
                               "exporter and delivery check/readback, and save actual export.log and native-result.json. "
                               "Do not fabricate a receipt, change upstream scripts or use a different authoring engine.")
                event(job, "native-export-validation", version=version_id, error=str(error))
                continue
            event(job, "native-roundtrip-export", version=version_id)
            try:
                previous = version / 'qa/summary.json'
                if not previous.exists() and parent:
                    previous = job / 'versions' / parent / 'qa/summary.json'
                inherited = read(previous) if previous.exists() else None
                if inherited and inherited.get('review_version') != 4:
                    inherited = None
                report = composite(version, plan, material, read(job / "style/style-profile.json"), job / "source-preview",
                                   checked_ids=[] if inherited else None, inherited=inherited)
            except Exception as review_error:
                record_review_failure(version,plan,review_error)
                raise
            save(version / f"qa/round-{attempt + 1}.json", report)
            save(version / "qa/summary.json", report)
            event(job, "composite-review", version=version_id, passed=report["passed"])
            if report["passed"]:
                break
            instruction = repair_task(version)
        else:
            raise RuntimeError("Original-engine output still has major API QA findings after three rounds")
        manifest = read(version / "version.json")
        from .scope import verify_revision_scope
        verify_revision_scope(job, version, plan, parent, request)
        manifest.update(status="ready_for_review", qa_passed=True, slide_count=len(plan["pages"]),
                        source="simulation" if request and request.get("simulation") else "user")
        save(version / "version.json", manifest)
        state["current"] = version_id
        if version_id not in state["history"]:
            state["history"].append(version_id)
        save(job / "job.json", state)
        show_review(job, version_id)
        event(job, "product-confirmation", version=version_id)
        return version_id
    except Exception as error:
        (version / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
        manifest = read(version / "version.json")
        save(version / "version.json", {**manifest, "status": "failed", "error": str(error)})
        event(job, "failed", version=version_id, error=str(error))
        raise


def create(job, template, outline):
    from .pipeline import locked, prepare
    with locked(job):
        if (job / "job.json").exists():
            raise ValueError("Job already exists")
        material, _, _ = prepare(job, template, outline)
        state = read(job / "job.json")
        save(job / "job.json", {**state, "engine": ENGINE, "authoring_mode": "split-native-v2"})
        contract = {"title": material["pages"][0]["title"], "native": True, "pages": material["pages"]}
        return build(job, contract)


def revise(job, text, base_version, simulation=False, allowed_page_ids=None):
    from .pipeline import locked, event
    with locked(job):
        state = read(job / "job.json")
        if state["current"] != base_version:
            raise ValueError("Stale feedback version")
        base = job / "versions" / base_version
        api = job / "feedback" / f"r{len(list((job / 'feedback').glob('r*'))) + 1:03}"
        plan = read(base / "plan.json")
        intent = recognize(text, base_version, plan, api, allowed_page_ids=allowed_page_ids)
        intent["simulation"] = simulation
        save(api / "request.json", {"text": text, "base_version": base_version, "simulation": simulation, "intent": intent})
        if intent["kind"] == "clarify":
            return {"status": "needs_input", **intent}
        contract, _ = revise_plan(plan, intent, read(job / "input/material-pool.json"), api)
        contract["request"] = intent
        contract = content_contract(contract)
        event(job, "native-feedback-routing", kind=intent["kind"])
        result = build(job, contract, base_version, intent)
        save(api / "result.json", {"version": result})
        return {"version": result, "intent": intent}


def resume(job):
    from .pipeline import locked
    with locked(job):
        drafts = sorted(p for p in (job / "versions").glob("v[0-9]*")
                        if read(p / "version.json")["status"] in ("building", "failed")
                        and not read(p / "version.json").get("trial"))
        if not drafts:
            raise ValueError("No interrupted native version")
        draft = drafts[-1]
        manifest = read(draft / "version.json")
        if manifest["parent"] != read(job / "job.json")["current"]:
            raise ValueError("Draft parent is stale")
        return build(job, read(draft / "authoring-contract.json"), manifest["parent"], manifest["request"], draft.name)


def recheck(job, version_id):
    from .pipeline import locked
    with locked(job):
        state = read(job / "job.json")
        if state["current"] != version_id:
            raise ValueError("Recheck the current native version")
        version = job / "versions" / version_id
        manifest = read(version / "version.json")
        state.update(current=manifest["parent"], history=[v for v in state["history"] if v != version_id])
        save(job / "job.json", state)
        return build(job, read(version / "authoring-contract.json"), manifest["parent"], manifest["request"], version_id,
                     review_existing=True)


def audit(job):
    """Check native outputs and the actual untouched-page preservation contract."""
    from .quality import extract
    state = read(job / "job.json")
    assert state["history"] and state["current"] == state["history"][-1], "No accepted native version history; experimental candidates are not promoted"
    material = read(job / "input/material-pool.json")
    results = []
    for version_id in state["history"]:
        version = job / "versions" / version_id
        manifest, plan = read(version / "version.json"), read(version / "plan.json")
        validate_plan(plan, material)
        _, errors = extract(version / "deck.pptx", plan, material)
        assert not errors, errors
        qa, engine = read(version / "qa/summary.json"), read(version / "native-engine-evidence.json")
        assert manifest["status"] in ("ready_for_review", "confirmed") and qa["passed"] and qa["api_executed"]
        assert set(qa["visual_coverage"]) == {p["id"] for p in plan["pages"]}
        item = {"version": version_id, "pages": len(plan["pages"]), "qa_passed": True,
                "facts": sum(len(p["point_ids"]) for p in plan["pages"]),
                "images": sum(len(p["asset_ids"]) for p in plan["pages"]),
                "edit_modes": {mode: sum(p["edit_mode"] == mode for p in plan["pages"])
                               for mode in sorted({p["edit_mode"] for p in plan["pages"]})},
                "original_export_receipt": engine["receipt"],
                "preserved_master_layout_parts": engine["preserved_master_layout_parts"]}
        if manifest["parent"]:
            from PIL import Image, ImageChops
            parent = job / "versions" / manifest["parent"]
            old_plan = read(parent / "plan.json")
            old_deck, new_deck = Presentation(parent / "deck.pptx"), Presentation(version / "deck.pptx")
            old = {p["id"]: (i, p) for i, p in enumerate(old_plan["pages"])}
            unchanged = []
            for index, page in enumerate(plan["pages"]):
                if page["id"] not in old or page["id"] in manifest["request"]["page_ids"]:
                    continue
                old_index, old_page = old[page["id"]]
                for field in ("title", "point_ids", "asset_ids", "notes", "origins"):
                    assert page[field] == old_page[field], f"Unrelated page {field} changed"
                assert page["source_slide"] == old_index + 1, "Unrelated page changed skeleton"
                assert old_deck.slides[old_index]._element.xml == new_deck.slides[index]._element.xml, "Unrelated slide XML changed"
                with Image.open(parent / f"preview/deck_p{old_index + 1}.png") as before, \
                     Image.open(version / f"preview/deck_p{index + 1}.png") as after:
                    assert ImageChops.difference(before.convert("RGB"), after.convert("RGB")).getbbox() is None, "Unrelated slide pixels changed"
                unchanged.append(page["id"])
            item["unchanged_xml_and_pixels"] = unchanged
            if manifest["request"]["kind"] == "merge_pages":
                assert len(plan["pages"]) == len(old_plan["pages"]) - len(manifest["request"]["page_ids"]) + 1
        results.append(item)
    report = {"passed": True, "engine": ENGINE, "current": state["current"], "versions": results,
              "outline_sha256": state["outline_sha256"]}
    save(job / "acceptance.json", report)
    return report
