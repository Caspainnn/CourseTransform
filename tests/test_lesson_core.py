from __future__ import annotations

import copy
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from docx import Document

from jiaoan.image_filter import classify_image
from jiaoan.images import discover_images, write_image_filter_report
from jiaoan.llm import LLMClient, LLMError, LLMResponse, LLMSettings
from jiaoan.nine_section import build_nine_section_data, is_nine_section, render_nine_section
from jiaoan.six_section import build_six_data, is_six_section, render_six_section
from jiaoan.split import plan_lesson_blueprints
from jiaoan.offline import build_offline_plan
from jiaoan.pipeline import LessonPipeline, ChapterResult, ensure_plan_minimums, resolve_template
from jiaoan.render import fill_template_docx, render_canonical_docx, render_markdown, render_template_style_docx
from jiaoan.template import extract_template_info, parse_template
from jiaoan.textbook import ground_quote, parse_textbook
from jiaoan.utils import Console
from jiaoan.validate import ground_citations, normalize_plan, validate_plan
from jiaoan.web import _normalize_base_url, _public_api, _settings_for_api

ROOT = Path(__file__).resolve().parents[1]
TEXTBOOK = ROOT / "examples" / "textbook_demo.md"
BRIEF = ROOT / "examples" / "brief_demo.json"
TABLE_TEMPLATE = ROOT / "examples" / "templates" / "教案模板_表格式.docx"
NINE_TEMPLATE = ROOT / "tests" / "fixtures" / "nine_section_template.docx"
SIX_TEMPLATE = ROOT / "tests" / "fixtures" / "six_section_template.docx"
PLACEHOLDER_TEMPLATE = ROOT / "examples" / "templates" / "教案模板_占位符.docx"
MD_TEMPLATE = ROOT / "examples" / "templates" / "教案模板.md"
IMAGE_TEXTBOOK = ROOT / "examples" / "textbook_with_images.md"
IMAGE_DIR = ROOT / "examples" / "figures"
FILTER_IMAGES = ROOT / "tests" / "fixtures" / "images"


def load_brief() -> dict:
    return json.loads(BRIEF.read_text(encoding="utf-8"))


def make_base():
    textbook = parse_textbook(TEXTBOOK)
    chapter = textbook.chapters[0]
    brief = load_brief()
    plan = normalize_plan(build_offline_plan(chapter, brief), chapter, brief)
    ground_citations(plan, chapter)
    return textbook, chapter, brief, plan


class FakeClient:
    def __init__(self, base: dict, bad_phase_minutes: bool = False, bad_phase_content: bool = False, review_error: bool = False):
        self.base = base
        self.bad_phase_minutes = bad_phase_minutes
        self.bad_phase_content = bad_phase_content
        self.review_error = review_error
        self.calls: list[str] = []

    def chat_json(self, messages, purpose="json", **kwargs):
        self.calls.append(purpose)
        if purpose == "plan":
            return (
                {
                    "meta": self.base["meta"],
                    "analysis": self.base["analysis"],
                    "objectives": self.base["objectives"],
                    "key_points": self.base["key_points"],
                    "difficulties": self.base["difficulties"],
                    "methods": self.base["methods"],
                    "materials": self.base["materials"],
                    "lessons": [
                        {
                            "period": lesson["period"],
                            "title": lesson["title"],
                            "duration_minutes": lesson["duration_minutes"],
                            "phase_outline": [
                                {
                                    "name": p["name"],
                                    "minutes": p["minutes"],
                                    "purpose": p["intent"],
                                    "objective_ids": p["objective_ids"],
                                }
                                for p in lesson["phases"]
                            ],
                        }
                        for lesson in self.base["lessons"]
                    ],
                    "assessment_plan": self.base["assessment_plan"],
                    "coverage": self.base["coverage"],
                },
                None,
            )
        if purpose.startswith("phases_p"):
            idx = int(purpose.split("_p")[-1]) - 1
            phases = copy.deepcopy(self.base["lessons"][idx]["phases"])
            if self.bad_phase_minutes:
                phases[-1]["minutes"] += 5
            if self.bad_phase_content:
                phases[0]["teacher_activity"] = ""
            return ({"phases": phases}, None)
        if purpose == "finish":
            return ({k: self.base[k] for k in ("board_design", "homework", "differentiation", "reflection", "citations", "ppt_brief", "custom_slots")}, None)
        if purpose.startswith("repair"):
            return (self.base, None)
        if purpose == "review":
            if self.review_error:
                return (
                    {
                        "issues": [
                            {
                                "severity": "error",
                                "location": "lessons[0].phases[0]",
                                "problem": "导入与目标关联不够直接。",
                                "fix": "把导入问题改为指向 O1 的具体问题。",
                            }
                        ],
                        "scores": {"目标可测": 4},
                        "verdict": "需修改",
                    },
                    None,
                )
            return ({"issues": [], "scores": {"目标可测": 5}, "verdict": "通过"}, None)
        raise AssertionError(f"unexpected purpose: {purpose}")

    def stats_summary(self):
        return {"total": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": len(self.calls)}}


class CoreTests(unittest.TestCase):
    def test_textbook_structure_and_grounding(self):
        textbook = parse_textbook(TEXTBOOK)
        self.assertEqual(len(textbook.chapters), 2)
        ch = textbook.chapters[0]
        self.assertEqual(len(ch.sections), 3)
        self.assertIn("走进人工智能", ch.title)
        hit = ground_quote(ch, "人工智能是研究如何让机器表现出类似人类智能行为的一门学科")
        self.assertIsNotNone(hit)
        self.assertIn("Artificial Intelligence", hit[0])

    def test_template_detection(self):
        spec = parse_template(TABLE_TEMPLATE)
        keys = {s.key for s in spec.slots}
        self.assertTrue({"title", "subject", "grade", "lesson_type", "textbook", "objectives", "process"} <= keys)
        self.assertIsNotNone(spec.phase_table)
        self.assertEqual(spec.phase_table.columns["teacher"], 2)
        md = parse_template(MD_TEMPLATE)
        self.assertEqual(len(md.md_sections), 10)

    def test_nine_section_template_preserves_structure(self):
        textbook = parse_textbook(TEXTBOOK)
        chapter = textbook.chapters[0]
        brief = load_brief()
        plan = normalize_plan(build_offline_plan(chapter, brief, periods=1), chapter, brief)
        spec = parse_template(NINE_TEMPLATE)
        self.assertTrue(is_nine_section(spec))
        data = build_nine_section_data(plan, chapter)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "教案.docx"
            render_nine_section(spec, data, out)
            doc = Document(out)
            h2 = [p.text for p in doc.paragraphs if p.style.name == "Heading 2"]
            self.assertEqual(len(h2), 9)
            self.assertEqual(len(doc.tables), 5)
            self.assertEqual(len(doc.tables[2].rows), 9)
            self.assertEqual(len(doc.tables[2].columns), 6)
            self.assertEqual([c.text for c in doc.tables[2].rows[0].cells], ["环节", "时间", "主要内容", "教师活动", "学生活动", "设计意图"])
            self.assertEqual([round(x.cm, 2) for x in (doc.sections[0].top_margin, doc.sections[0].left_margin)], [2.54, 3.17])
            all_text = "\n".join([p.text for p in doc.paragraphs] + [c.text for t in doc.tables for r in t.rows for c in r.cells])
            self.assertNotIn("GAN", all_text)
            self.assertNotIn("sin曲线", all_text)

    def test_manifest_override(self):
        manifest = {
            "title": "示例教材",
            "chapters": [
                {"id": "c1", "title": "第一章 走进人工智能", "start_line": 1, "end_line": 28},
                {"id": "c2", "title": "第二章 机器如何学习", "start_line": 29, "end_line": 55},
            ],
        }
        textbook = parse_textbook(TEXTBOOK, manifest=manifest)
        self.assertEqual([c.id for c in textbook.chapters], ["c1", "c2"])
        self.assertEqual(textbook.chapters[0].start_line, 1)
        self.assertEqual(textbook.chapters[1].start_line, 29)

    def test_template_directory_resolution(self):
        textbook = parse_textbook(TEXTBOOK)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copy(TABLE_TEMPLATE, root / "ch01.docx")
            shutil.copy(TABLE_TEMPLATE, root / "default.docx")
            self.assertEqual(Path(resolve_template(textbook.chapters[0], str(root))).name, "ch01.docx")
            self.assertEqual(Path(resolve_template(textbook.chapters[1], str(root))).name, "default.docx")

    def test_web_api_settings(self):
        self.assertEqual(_normalize_base_url("https://school.example.com/v1/chat/completions"), "https://school.example.com/v1")
        local = _settings_for_api({"base_url": "http://127.0.0.1:11435/v1", "api_key": "", "model": "qwen2.5:7b"})
        self.assertEqual(local.api_key, "ollama")
        self.assertEqual(local.model, "qwen2.5:7b")
        custom = _settings_for_api({"base_url": "https://school.example.com/v1", "api_key": "sk-test", "model": "deepseek-chat"})
        self.assertEqual(custom.api_key, "sk-test")
        public = _public_api({"base_url": "https://school.example.com/v1", "api_key": "sk-test", "model": "deepseek-chat", "label": "学校"})
        self.assertNotIn("api_key", public)
        self.assertEqual(public["label"], "学校")
        with self.assertRaises(ValueError):
            _settings_for_api({"base_url": "https://school.example.com/v1", "api_key": "", "model": "deepseek-chat"})

    def test_image_discovery_and_offline_embedding(self):
        textbook = parse_textbook(IMAGE_TEXTBOOK)
        chapter = textbook.chapters[0]
        assets = discover_images(chapter, IMAGE_DIR)
        self.assertEqual(len(assets), 2)
        self.assertTrue(any("三种类型" in a.alt for a in assets))
        self.assertTrue(all(a.ocr_text for a in assets))
        with tempfile.TemporaryDirectory() as tmp:
            settings = LLMSettings(offline=True)
            pipeline = LessonPipeline(settings, Console(stream=io.StringIO()))
            result = pipeline.run_chapter(
                chapter,
                load_brief(),
                str(TABLE_TEMPLATE),
                tmp,
                offline=True,
                image_dir=IMAGE_DIR,
            )
            self.assertIn(result.status, {"ok", "ok_with_warnings"}, result.error)
            self.assertEqual(result.validation.get("metrics", {}).get("figures"), 2)
            self.assertTrue(result.acceptance.get("passed"), result.acceptance)
            doc = Document(result.files["docx"])
            self.assertGreaterEqual(len(doc.inline_shapes), 2)

    def test_image_filter_rejects_qr_and_code(self):
        decisions = {
            name: classify_image(FILTER_IMAGES / name)
            for name in ("image-0001.jpg", "image-0008.jpg", "image-0002.jpg", "image-0024.jpg", "image-0026.jpg")
        }
        self.assertFalse(decisions["image-0001.jpg"].keep)
        self.assertEqual(decisions["image-0001.jpg"].category, "qr_code")
        self.assertFalse(decisions["image-0008.jpg"].keep)
        self.assertEqual(decisions["image-0008.jpg"].category, "code_screenshot")
        self.assertTrue(decisions["image-0002.jpg"].keep)
        self.assertTrue(decisions["image-0024.jpg"].keep)
        self.assertTrue(decisions["image-0026.jpg"].keep)

        textbook = parse_textbook(TEXTBOOK)
        assets = discover_images(textbook.chapters[0], FILTER_IMAGES)
        self.assertEqual(sum(1 for a in assets if a.keep), 3)
        self.assertEqual(sum(1 for a in assets if not a.keep), 2)
        with tempfile.TemporaryDirectory() as tmp:
            report = write_image_filter_report(tmp, assets, textbook.chapters[0])
            text = report.read_text(encoding="utf-8")
            self.assertIn("教材配图筛选说明", text)
            self.assertIn("二维码", text)
            self.assertIn("源代码", text)

    def test_offline_plan_passes_validation(self):
        textbook, chapter, brief, plan = make_base()
        result = validate_plan(plan, chapter, rendered_chars=3000)
        self.assertTrue(result.ok, [i.message for i in result.errors])
        for lesson in plan["lessons"]:
            self.assertEqual(sum(p["minutes"] for p in lesson["phases"]), lesson["duration_minutes"])

    def test_validation_catches_bad_time(self):
        textbook, chapter, brief, plan = make_base()
        plan["lessons"][0]["phases"][-1]["minutes"] += 5
        result = validate_plan(plan, chapter)
        self.assertFalse(result.ok)
        self.assertIn("PHASE_SUM", [i.code for i in result.errors])

    def test_canonical_docx(self):
        textbook, chapter, brief, plan = make_base()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "教案.docx"
            render_canonical_docx(plan, out, offline=True)
            doc = Document(out)
            text = "\n".join(p.text for p in doc.paragraphs)
            self.assertIn("第一章 走进人工智能", text)
            self.assertIn("一、教材分析", text)
            self.assertNotIn("{{", text)
            self.assertGreaterEqual(len(doc.tables), 3)

    def test_fill_table_template(self):
        textbook, chapter, brief, plan = make_base()
        with tempfile.TemporaryDirectory() as tmp:
            spec = parse_template(TABLE_TEMPLATE)
            out = Path(tmp) / "教案.docx"
            fill_template_docx(spec, plan, out, offline=True)
            doc = Document(out)
            all_text = "\n".join([p.text for p in doc.paragraphs] + [c.text for t in doc.tables for r in t.rows for c in r.cells])
            self.assertIn("第一章 走进人工智能", all_text)
            self.assertIn("情境导入", all_text)
            self.assertNotIn("{{", all_text)
            self.assertGreaterEqual(len(doc.tables[1].rows), 6)

    def test_md_template_render(self):
        textbook, chapter, brief, plan = make_base()
        spec = parse_template(MD_TEMPLATE)
        md = render_markdown(plan, spec, offline=False)
        self.assertIn("# 第一章 走进人工智能 教学设计", md)
        self.assertIn("\n\n## 二、学情分析", md)
        self.assertNotIn("{{", md)

    def test_fake_llm_pipeline_and_repair(self):
        textbook, chapter, brief, base = make_base()
        with tempfile.TemporaryDirectory() as tmp:
            settings = LLMSettings(api_key="fake", model="fake", offline=False, max_repair=1)
            console = Console(stream=io.StringIO())
            client = FakeClient(base, bad_phase_content=True)
            pipeline = LessonPipeline(settings, console, client)
            result = pipeline.run_chapter(
                chapter,
                brief,
                str(TABLE_TEMPLATE),
                tmp,
                offline=False,
                enable_review=False,
            )
            self.assertIn(result.status, {"ok", "ok_with_warnings"}, result.error)
            self.assertIn("repair1", client.calls)
            self.assertTrue(Path(result.files["docx"]).exists())

    def test_fake_llm_review_repair(self):
        textbook, chapter, brief, base = make_base()
        with tempfile.TemporaryDirectory() as tmp:
            settings = LLMSettings(api_key="fake", model="fake", offline=False, max_repair=1)
            console = Console(stream=io.StringIO())
            client = FakeClient(base, review_error=True)
            pipeline = LessonPipeline(settings, console, client)
            result = pipeline.run_chapter(
                chapter,
                brief,
                str(PLACEHOLDER_TEMPLATE),
                tmp,
                offline=False,
                enable_review=True,
            )
            self.assertEqual(result.status, "ok_with_warnings", result.error)
            self.assertIn("review", client.calls)


    def test_filled_exemplar_template_does_not_leak_old_content(self):
        textbook, chapter, brief, plan = make_base()
        with tempfile.TemporaryDirectory() as tmp:
            template = Path(tmp) / "filled.docx"
            doc = Document()
            doc.add_paragraph("第2章 旧章节标题")
            doc.add_paragraph("一、课程基本信息")
            doc.add_paragraph("旧内容说明。" * 30)
            doc.add_paragraph("二、课程简介")
            doc.add_paragraph("旧课程简介。" * 40)
            doc.add_paragraph("（二）本次教学目标")
            doc.add_paragraph("旧目标内容。" * 40)
            doc.add_paragraph("四、案例教学实施过程")
            table = doc.add_table(rows=4, cols=4)
            for c, text in enumerate(["教学环节", "时间", "教师活动", "学生活动"]):
                table.cell(0, c).text = text
            for r in range(1, 4):
                table.cell(r, 0).text = f"旧环节{r}"
                table.cell(r, 1).text = "10"
                table.cell(r, 2).text = "旧教师活动内容，属于第2章 GAN 样例。"
                table.cell(r, 3).text = "旧学生活动内容，属于第2章 GAN 样例。"
            doc.save(template)

            spec = parse_template(template)
            self.assertTrue(spec.exemplar, spec.warnings)
            out = Path(tmp) / "clean.docx"
            render_template_style_docx(spec, plan, out, base_dir=tmp)
            rendered = Document(out)
            all_text = "\n".join(
                [p.text for p in rendered.paragraphs]
                + [c.text for t in rendered.tables for r in t.rows for c in r.cells]
            )
            self.assertIn("第一章 走进人工智能", all_text)
            self.assertNotIn("旧内容", all_text)
            self.assertNotIn("GAN", all_text)
            self.assertNotIn("{{", all_text)

    def test_lesson_split_uses_experiment_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            one = Path(tmp) / "one.md"
            one.write_text(
                "# 第1章 示例\n\n## 1.1 知识要点\n\n"
                + "基础内容。" * 20
                + "\n\n## 1.2 实践 1-1 图像去噪\n\n"
                + "实验内容。" * 40
                + "\n\n## 1.3 习题\n\n"
                + "习题。" * 5,
                encoding="utf-8",
            )
            chapter = parse_textbook(one).chapters[0]
            blueprints = plan_lesson_blueprints(chapter)
            self.assertEqual(len(blueprints), 2)
            self.assertEqual(blueprints[0].kind, "theory")
            self.assertEqual(blueprints[1].kind, "experiment")
            self.assertNotIn("上", blueprints[1].title)
            self.assertNotIn("下", blueprints[1].title)

            two = Path(tmp) / "two.md"
            two.write_text(
                "# 第2章 示例\n\n## 2.1 知识要点\n\n"
                + "基础。" * 20
                + "\n\n## 2.2 实践 2-1 实验一\n\n"
                + "实验一。" * 30
                + "\n\n## 2.3 实践 2-2 实验二\n\n"
                + "实验二。" * 30
                + "\n\n## 2.4 习题\n\n"
                + "习题。" * 5,
                encoding="utf-8",
            )
            chapter2 = parse_textbook(two).chapters[0]
            blueprints2 = plan_lesson_blueprints(chapter2)
            self.assertEqual(len(blueprints2), 4)
            self.assertEqual([b.kind for b in blueprints2], ["theory", "experiment", "experiment", "exercise"])

    def test_template_info_reads_course_duration(self):
        spec = parse_template(NINE_TEMPLATE)
        info = extract_template_info(spec)
        self.assertIn("人工智能安全", info["course_name"])
        self.assertEqual(info["duration_minutes"], 45)
        self.assertIsNone(info["periods"])
        self.assertIn("本科", info["grade"])

    def test_chapter_split_outputs_one_docx_per_lesson(self):
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "textbook.md"
            lines = ["# 第1章 示例章", ""]
            for i in range(1, 5):
                lines += [f"## 1.{i} 小节{i}", "", f"这是小节{i}的正文。" * 40, ""]
            md.write_text("\n".join(lines), encoding="utf-8")
            chapter = parse_textbook(md).chapters[0]
            brief = {"subject": "信息科技", "grade": "八年级", "duration_minutes": 45, "periods": 3, "periods_explicit": True}
            pipeline = LessonPipeline(LLMSettings(offline=True), Console(stream=io.StringIO()))
            result = pipeline.run_chapter(
                chapter,
                brief,
                str(NINE_TEMPLATE),
                tmp,
                offline=True,
                enable_review=False,
            )
            self.assertEqual(len(result.docx_files), 3, result.error)
            for item in result.docx_files:
                doc = Document(item["path"])
                self.assertEqual(len([p for p in doc.paragraphs if p.style.name == "Heading 2"]), 9)
                self.assertEqual(len(doc.tables), 5)
                self.assertEqual(len(doc.tables[2].rows), 9)
                self.assertEqual(len(doc.tables[2].columns), 6)

    def test_six_section_template_preserves_four_column_table(self):
        textbook = parse_textbook(TEXTBOOK)
        chapter = textbook.chapters[0]
        brief = load_brief()
        plan = normalize_plan(build_offline_plan(chapter, brief, periods=2), chapter, brief)
        spec = parse_template(SIX_TEMPLATE)
        self.assertTrue(is_six_section(spec))
        with tempfile.TemporaryDirectory() as tmp:
            for idx in range(2):
                data = build_six_data(plan, chapter, [], lesson_index=idx, lesson_no=idx + 1)
                out = Path(tmp) / f"lesson{idx}.docx"
                render_six_section(spec, data, out)
                doc = Document(out)
                all_text = "\n".join(p.text for p in doc.paragraphs)
                self.assertIn("一、课程基本信息", all_text)
                self.assertIn("六、教学效果及反思", all_text)
                self.assertEqual(len(doc.tables), 5)
                self.assertEqual(len(doc.tables[3].rows), 9)
                self.assertEqual(len(doc.tables[3].columns), 4)
                self.assertEqual([c.text for c in doc.tables[3].rows[0].cells], ["环节", "时间", "教师推进", "学生任务与检查点"])
                self.assertNotIn("GAN", all_text)

    def test_minimums_add_objective_and_citation(self):
        textbook, chapter, brief, plan = make_base()
        plan["objectives"] = plan["objectives"][:1]
        plan["citations"] = []
        result = ChapterResult("ch01", chapter.title, "running")
        ensured = ensure_plan_minimums(plan, chapter, result)
        self.assertGreaterEqual(len(ensured["objectives"]), 2)
        self.assertGreaterEqual(len(ensured["citations"]), 2)
        new_id = ensured["objectives"][1]["id"]
        linked = [
            p
            for lesson in ensured["lessons"]
            for p in lesson["phases"]
            if new_id in (p.get("objective_ids") or [])
        ]
        self.assertTrue(linked)
        self.assertTrue(any(a.get("objective_id") == new_id for a in ensured["assessment_plan"]))
        self.assertTrue(result.warnings)

    def test_reasoning_model_null_content_is_clear_error(self):
        """qwen3.8 把 4096 token 全用在 reasoning、content=null 时，不能抛 TypeError。"""
        with tempfile.TemporaryDirectory() as tmp:
            settings = LLMSettings(
                api_key="fake",
                model="qwen3.8-27b",
                cache_dir=tmp,
                no_cache=True,
                disable_thinking=True,
            )
            client = LLMClient(settings, Console(stream=io.StringIO()))
            client._request_with_retry = lambda payload, purpose: {
                "choices": [
                    {
                        "message": {"content": None, "reasoning_content": "先分析题目……"},
                        "finish_reason": "length",
                    }
                ],
                "usage": {
                    "prompt_tokens": 11527,
                    "completion_tokens": 4096,
                    "completion_tokens_details": {"reasoning_tokens": 4096},
                },
            }
            with self.assertRaises(LLMError) as ctx:
                client.chat([{"role": "user", "content": "生成教案"}], purpose="plan")
            message = str(ctx.exception)
            self.assertIn("思考过程", message)
            self.assertIn("8192", message)
            self.assertNotIn("NoneType", message)

    def test_key_sections_ignores_deep_headings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "textbook.md"
            lines = ["# 第3章 卷积神经网络", "", "## 3.1 知识要点", "", "正文" * 40]
            lines += ["", "## 3.1.1 神经网络", "", "正文" * 40]
            lines += ["", "## 3.2 实践一", "", "正文" * 40]
            lines += ["", "## 3.3 实践二", "", "正文" * 40]
            path.write_text("\n".join(lines), encoding="utf-8")
            chapter = parse_textbook(path).chapters[0]
            self.assertGreaterEqual(len(chapter.sections), 4)
            self.assertEqual([s.title for s in chapter.key_sections()], ["3.1 知识要点", "3.2 实践一", "3.3 实践二"])


if __name__ == "__main__":
    unittest.main()
