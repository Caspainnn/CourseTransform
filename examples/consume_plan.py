"""下游示例：只读 lesson_plan.json，生成 PPT 提纲或一个最简 PPTX。

这不是第 2 段（教案生成）的一部分，而是给做第 3 段（PPT 课件）的同学看的
“消费契约”示例：不要解析 DOCX，直接读结构化 JSON。

用法：
    python examples/consume_plan.py demo/ch01_.../lesson_plan.json
    python examples/consume_plan.py demo/ch01_.../lesson_plan.json --pptx out.pptx
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_plan(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def print_outline(plan: dict) -> None:
    meta = plan.get("meta") or {}
    print(f"课题：{meta.get('title')}｜{meta.get('subject')}｜{meta.get('grade')}｜{meta.get('periods')}课时")
    print("\n教学目标：")
    for obj in plan.get("objectives") or []:
        print(f"  {obj.get('id')} [{obj.get('domain')}/{obj.get('bloom')}] {obj.get('statement')}")
    print("\n教学环节：")
    for lesson in plan.get("lessons") or []:
        print(f"  第{lesson.get('period')}课时：{lesson.get('title')}（{lesson.get('duration_minutes')}分钟）")
        for phase in lesson.get("phases") or []:
            print(f"    - {phase.get('name')}（{phase.get('minutes')}分钟）目标 {phase.get('objective_ids')}")
    print("\nPPT 提纲：")
    for i, slide in enumerate((plan.get("ppt_brief") or {}).get("slide_outline") or [], start=1):
        print(f"  {i}. {slide.get('title')}")
        for b in slide.get("bullets") or []:
            print(f"     - {b}")
        if slide.get("visual"):
            print(f"     [图示] {slide.get('visual')}")


def build_pptx(plan: dict, out_path: str) -> None:
    try:
        from pptx import Presentation
        from pptx.dml.color import RGBColor
        from pptx.util import Inches, Pt
    except ImportError as exc:
        raise SystemExit("需要 python-pptx：python -m pip install python-pptx") from exc

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]
    accent = RGBColor(0xC2, 0x41, 0x0C)
    ink = RGBColor(0x1F, 0x29, 0x37)

    def add_slide(title: str, bullets: list[str], subtitle: str = "") -> None:
        slide = prs.slides.add_slide(blank)
        bar = slide.shapes.add_shape(1, 0, 0, prs.slide_width, Inches(0.12))
        bar.fill.solid()
        bar.fill.fore_color.rgb = accent
        bar.line.fill.background()
        tb = slide.shapes.add_textbox(Inches(0.7), Inches(0.45), Inches(11.9), Inches(0.8))
        p = tb.text_frame.paragraphs[0]
        p.text = title
        p.font.size = Pt(28)
        p.font.bold = True
        p.font.color.rgb = ink
        if subtitle:
            st = slide.shapes.add_textbox(Inches(0.7), Inches(1.2), Inches(11.9), Inches(0.4))
            st.text_frame.paragraphs[0].text = subtitle
            st.text_frame.paragraphs[0].font.size = Pt(13)
            st.text_frame.paragraphs[0].font.color.rgb = RGBColor(0x6B, 0x72, 0x80)
        body = slide.shapes.add_textbox(Inches(0.8), Inches(1.8), Inches(11.8), Inches(5.2))
        tf = body.text_frame
        tf.word_wrap = True
        for i, b in enumerate(bullets[:6]):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.text = "• " + str(b)
            p.font.size = Pt(18)
            p.font.color.rgb = ink
            p.space_after = Pt(10)

    meta = plan.get("meta") or {}
    add_slide(meta.get("title") or "教学设计", [f"{meta.get('subject')}｜{meta.get('grade')}", f"{meta.get('periods')}课时，每课时{meta.get('duration_minutes')}分钟"], "由 lesson_plan.json 生成")
    objectives = [f"{o.get('statement')}" for o in plan.get("objectives") or []]
    add_slide("学习目标", objectives)

    slides = (plan.get("ppt_brief") or {}).get("slide_outline") or []
    for item in slides:
        add_slide(str(item.get("title") or ""), [str(b) for b in item.get("bullets") or []], str(item.get("visual") or ""))

    board = plan.get("board_design") or {}
    add_slide("板书设计", [str(x) for x in board.get("content") or []])
    homework = [f"{h.get('level')}：{h.get('task')}" for h in plan.get("homework") or []]
    add_slide("作业", homework)

    prs.save(out_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", help="lesson_plan.json")
    parser.add_argument("--pptx", help="可选：生成一个最简 PPTX")
    args = parser.parse_args()
    plan = load_plan(args.plan)
    print_outline(plan)
    if args.pptx:
        build_pptx(plan, args.pptx)
        print(f"\n已生成：{args.pptx}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
