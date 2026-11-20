"""Read-only acceptance audit of a job's completed version history."""

from collections import Counter
import hashlib
from pathlib import Path
from PIL import Image, ImageChops

from .models import read, save
from .planning import validate_plan
from .quality import extract


def audit(job):
    state = read(job / "job.json")
    if state.get("engine") == "ppt-master-roundtrip":
        from .native import audit as native_audit
        return native_audit(job)
    assert state["current"] and state["history"] and state["current"] == state["history"][-1], "No current completed version"
    material = read(job / "input/material-pool.json")
    checks = []
    for version_id in state["history"]:
        root = job / "versions" / version_id
        manifest, plan = read(root / "version.json"), read(root / "plan.json")
        assert manifest["status"] in ("ready_for_review", "confirmed") and manifest.get("qa_passed"), "Version not reviewed"
        scenes = read(root / "scenes.json")
        validate_plan(plan, material)
        _, errors = extract(root / "deck.pptx", plan, material)
        assert not errors, errors
        qa = read(root / "qa/summary.json")
        assert qa["passed"] and qa["api_executed"], "API composite review required"
        assert set(qa.get("visual_coverage", {})) == {p["id"] for p in plan["pages"]}, "Incomplete visual review coverage"
        check = {"version": version_id, "pages": len(plan["pages"]),
                 "facts": sum(len(p["point_ids"]) for p in plan["pages"]),
                 "images": sum(len(p["asset_ids"]) for p in plan["pages"]),
                 "routes": dict(Counter(s["route"] for s in scenes.values())),
                 "qa_passed": True, "status": manifest["status"]}
        if manifest["parent"]:
            parent = job / "versions" / manifest["parent"]
            old_plan, old_scenes = read(parent / "plan.json"), read(parent / "scenes.json")
            old = {p["id"]: (i, p) for i, p in enumerate(old_plan["pages"], 1)}
            request = manifest["request"]
            unchanged = []
            for i, page in enumerate(plan["pages"], 1):
                if page["id"] not in old or page["id"] in request["page_ids"]:
                    continue
                before_i, before = old[page["id"]]
                assert page == before, "Unrelated page plan mutated"
                assert scenes[page["id"]] == old_scenes[page["id"]], "Unrelated scene mutated"
                before_image = parent / f"preview/deck_p{before_i}.png"
                after_image = root / f"preview/deck_p{i}.png"
                assert hashlib.sha256(before_image.read_bytes()).digest() == hashlib.sha256(after_image.read_bytes()).digest(), "Unrelated rendered page changed"
                unchanged.append(page["id"])
            if request["kind"] == "set_title":
                assert len(plan["pages"]) == len(old_plan["pages"])
                for page in plan["pages"]:
                    before = old[page["id"]][1]
                    assert {k: v for k, v in page.items() if k != "title"} == {k: v for k, v in before.items() if k != "title"}
                check["all_scenes_unchanged"] = scenes == old_scenes
                if scenes == old_scenes:
                    boxes = {}
                    for page in plan["pages"]:
                        if page["id"] not in request["page_ids"]:
                            continue
                        number = next(i for i, p in enumerate(plan["pages"], 1) if p["id"] == page["id"])
                        with Image.open(parent / f"preview/deck_p{number}.png") as before_image, \
                             Image.open(root / f"preview/deck_p{number}.png") as after_image:
                            diff = ImageChops.difference(before_image.convert("RGB"), after_image.convert("RGB")).getbbox()
                        title = next(e for e in scenes[page["id"]]["elements"] if e["kind"] == "title")
                        if "box" in title:
                            x, y, w, h = title["box"]
                        else:
                            template = read(job / "template-catalog.json")
                            slot = next(s for s in template["slides"][page["source_slide"] - 1]["slots"] if s["id"] == title["slot_id"])
                            x, y, w, h = slot["box"]
                        assert diff and diff[0] >= x - 4 and diff[1] >= y - 4 and diff[2] <= x + w + 4 and diff[3] <= y + h + 4, "Title edit changed pixels outside title box"
                        boxes[page["id"]] = list(diff)
                    check["title_only_pixel_diff"] = boxes
            if request["kind"] == "merge_pages":
                assert len(plan["pages"]) == len(old_plan["pages"]) - len(request["page_ids"]) + 1
            check.update(operation=request["kind"], unchanged_pages_verified=unchanged)
        checks.append(check)
    result = {"passed": True, "versions": checks, "current": state["current"],
              "outline_sha256": state["outline_sha256"]}
    save(job / "acceptance.json", result)
    return result


if __name__ == "__main__":
    import json
    import sys
    print(json.dumps(audit(Path(sys.argv[1]).resolve()), ensure_ascii=False, indent=2))
