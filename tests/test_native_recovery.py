"""Model-free regression checks for the two kernel recovery defects found in the studio runs.

1. Animation targets: the exporter rejects each edited page whose source timing
   targets disappeared; recovering one page per build attempt exhausts the budget.
2. Blank pages: a page whose authoring never completed stayed an empty canvas and
   was exported as a blank slide instead of blocking the export.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from pptnative import native_author

ROOT = Path(__file__).resolve().parents[1]
THREE_PAGE_JOB = ROOT / "runs/studio/20261118-234636-8fb61f/versions/v001"


def _svg(*nodes: str) -> str:
    return ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            'viewBox="0 0 1280 720">' + "".join(nodes) + "</svg>")


class BlankPageGuard(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.material = {"assets": {"p06.a1": {"path": str(self.root / "a1.jpg")},
                                    "p06.a2": {"path": str(self.root / "a2.jpg")}}}
        self.choice = {"id": "p06", "svg": "p06.svg", "edit_mode": "local-recompose",
                       "asset_ids": ["p06.a1", "p06.a2"]}

    def tearDown(self):
        self._temp.cleanup()

    def test_empty_canvas_is_not_exportable(self):
        canvas = _svg('<rect x="0" y="0" width="1280" height="720" fill="#ffffff"/>')
        self.assertFalse(native_author.workspace_page_content(canvas, self.choice, self.material))

    def test_missing_fixed_image_is_not_exportable(self):
        svg = _svg('<image href="../images/a1.jpg" x="0" y="0" width="400" height="300"/>',
                   '<text x="40" y="60" font-size="30">标题</text>')
        self.assertFalse(native_author.workspace_page_content(svg, self.choice, self.material))

    def test_complete_page_is_exportable(self):
        svg = _svg('<image href="../images/a1.jpg" x="0" y="0" width="400" height="300"/>',
                   '<image href="../images/a2.jpg" x="420" y="0" width="400" height="300"/>',
                   '<text x="40" y="60" font-size="30">标题</text>')
        self.assertTrue(native_author.workspace_page_content(svg, self.choice, self.material))

    def test_template_fill_pages_are_never_blank(self):
        choice = dict(self.choice, edit_mode="template-fill", asset_ids=[])
        self.assertTrue(native_author.workspace_page_content("<svg xmlns='http://www.w3.org/2000/svg'/>",
                                                             choice, self.material))

    def test_page_needs_authoring_reads_the_workspace_copy(self):
        workspace = self.root / "workspace"
        (workspace / "authoring-svg-flat").mkdir(parents=True)
        (workspace / "authoring-svg-flat" / "p06.svg").write_text(
            _svg('<rect x="0" y="0" width="1280" height="720" fill="#fff"/>'), encoding="utf-8")
        self.assertTrue(native_author.page_needs_authoring(workspace, self.choice, self.material))
        self.assertTrue(native_author.page_needs_authoring(
            workspace, dict(self.choice, svg="absent.svg"), self.material))


class AnimationRecovery(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.version = Path(self._temp.name)
        self.workspace = self.version / "workspace"
        self.workspace.mkdir()
        self.mapping = {"pages": [
            {"id": "p01", "svg": "p01.svg", "edit_mode": "template-fill"},
            {"id": "p02", "svg": "p02.svg", "edit_mode": "template-fill"},
            {"id": "p03", "svg": "p03.svg", "edit_mode": "template-fill"},
            {"id": "p09", "svg": "p09.svg", "edit_mode": "referenced"},
        ], "limitations": []}
        (self.workspace / "animations.json").write_text(
            json.dumps({"slides": {"slide_10": {"groups": {}}}}), encoding="utf-8")

    def tearDown(self):
        self._temp.cleanup()

    def _animations(self):
        return json.loads((self.workspace / "animations.json").read_text(encoding="utf-8"))["slides"]

    def test_only_the_rejected_page_is_disabled_and_recorded(self):
        message = ("  [Slide 2/3] p02.svg - Error: Edited slide removed source animation target(s); "
                   "update animations.json explicitly: 12, 13\n"
                   "Error: Edited slide removed source animation target(s); update animations.json explicitly: 12, 13\n")
        changed = native_author.reconcile_animation_targets(self.version, self.workspace, self.mapping, message)
        self.assertTrue(changed)
        animations = self._animations()
        self.assertEqual(animations["p02"], {"animation": {"effect": "none"}})
        self.assertNotIn("p03", animations)
        self.assertNotIn("p09", animations)  # referenced pages keep their source animation
        self.assertEqual(len(self.mapping["limitations"]), 1)
        self.assertIn("p02", self.mapping["limitations"][0])
        self.assertTrue((self.version / "native-workspace.json").exists())

    def test_second_pass_reports_no_change(self):
        message = "[Slide 3/3] p03.svg - Error: Edited slide removed source animation target(s)"
        self.assertTrue(native_author.reconcile_animation_targets(self.version, self.workspace, self.mapping, message))
        self.assertFalse(native_author.reconcile_animation_targets(self.version, self.workspace, self.mapping, message))

    def test_unrelated_export_error_is_not_recovered(self):
        message = "Upstream svg_to_pptx.py failed: Error: invalid nested SVG crop wrapper"
        self.assertFalse(native_author.reconcile_animation_targets(self.version, self.workspace, self.mapping, message))
        self.assertNotIn("p02", self._animations())


@unittest.skipUnless(THREE_PAGE_JOB.exists(), "failed 3-page workspace is not present")
class RealWorkspaceExport(unittest.TestCase):
    """Replays the exact failed workspace through the new recovery path."""

    def test_export_recovers_all_rejected_pages_in_one_call(self):
        temp = tempfile.mkdtemp()
        try:
            version = Path(temp)
            shutil.copytree(THREE_PAGE_JOB / "workspace", version / "workspace")
            workspace = version / "workspace"
            animations = json.loads((workspace / "animations.json").read_text(encoding="utf-8"))
            animations["slides"] = {k: v for k, v in animations["slides"].items()
                                    if k.startswith("slide_")}
            (workspace / "animations.json").write_text(
                json.dumps(animations, ensure_ascii=False, indent=2), encoding="utf-8")
            mapping = {"pages": [
                {"id": "p01", "svg": "p01.svg", "edit_mode": "template-fill", "asset_ids": []},
                {"id": "p02", "svg": "p02.svg", "edit_mode": "template-fill", "asset_ids": []},
                {"id": "p03", "svg": "p03.svg", "edit_mode": "template-fill", "asset_ids": []},
            ], "limitations": []}
            stdout = native_author.export_roundtrip(version, workspace, mapping)
            self.assertIn("output_pages=3", stdout)
            slides = json.loads((workspace / "animations.json").read_text(encoding="utf-8"))["slides"]
            self.assertEqual(slides["p02"], {"animation": {"effect": "none"}})
            self.assertEqual(slides["p03"], {"animation": {"effect": "none"}})
            self.assertEqual(len(mapping["limitations"]), 2)
        finally:
            shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
