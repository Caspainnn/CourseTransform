import json
import re
from pathlib import Path

from src.intermediate import parse_intermediate
from src.template_parser import parse_template
from src.utils import setup_logging, write_text

logger = setup_logging()

# 裁判使用与生成者不同的 system prompt，并要求引用教材原文证据，
# 以降低"验证者和生成者犯同样错误"的概率。
JUDGE_SYSTEM_PROMPT = (
    "你是一名严格的教学设计评审专家，负责对照教材原文逐项检查教案质量。"
    "只输出 JSON，不要输出任何其他文字。"
)

REFINE_SYSTEM_PROMPT = (
    "你是一名熟悉高校课堂教学设计的课程教师。请根据评审意见修改教案。"
    "只输出修改后的完整教案中间稿（必须保持原有的 CTBLOCK 块标记注释行和格式不变），不要输出解释。"
)


def build_checkitems(config: dict, template_blocks=None) -> list[str]:
    """检查表：板块齐全 / 时间合计45分钟 / 内容有教材依据 / 中文与术语一致。"""
    custom = (config.get("validation") or {}).get("checkitems") or []
    if custom:
        return list(custom)
    s2 = config.get("stage2", {})
    items: list[str] = []
    if template_blocks:
        headings = [b.text for b in template_blocks if b.type == "heading" and b.text]
        if headings:
            items.append("教案必须完整包含以下板块标题：" + "、".join(headings))
    total_minutes = s2.get("total_minutes", 0)
    if total_minutes and total_minutes > 0:
        items.append(f"教学过程表中各环节的时长合计必须等于 {total_minutes} 分钟。")
    items.append(
        "教学目标、重点难点、作业等核心内容必须能在教材原文中找到依据，"
        "不得出现教材未提及的实验数据或结论。"
    )
    items.append("全文使用简体中文，术语与教材一致，无空话套话堆砌。")
    return items


def extract_json(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def validate(md_text: str, knowledge_md: str, checkitems: list[str], llm) -> dict:
    """让模型做裁判，按检查表逐项判定 pass/fail 并给理由。"""
    items_json = json.dumps(checkitems, ensure_ascii=False, indent=2)
    user_prompt = (
        f"【检查表（JSON 数组，逐项判定）】\n{items_json}\n\n"
        f"【教材原文】\n{knowledge_md}\n\n"
        f"【待审教案】\n{md_text}\n\n"
        "请对检查表中每一项逐项判定，判定理由需引用教材原文证据。"
        '输出 JSON，格式：{"results": [{"item": "检查项原文", "pass": true, "reason": "判定理由"}]}。'
    )
    raw = llm.chat(system=JUDGE_SYSTEM_PROMPT, user=user_prompt)
    data = extract_json(raw) or {}
    results = data.get("results") or []
    passed = len(results) == len(checkitems) and all(bool(r.get("pass")) for r in results)
    return {"passed": passed, "results": results}


def refine(md_text: str, failures: list[dict], knowledge_md: str, llm) -> str:
    """把失败项连同原文交给模型重写。"""
    failures_json = json.dumps(failures, ensure_ascii=False, indent=2)
    user_prompt = (
        f"【教材原文】\n{knowledge_md}\n\n"
        f"【当前教案（含块标记）】\n{md_text}\n\n"
        f"【未通过的评审意见】\n{failures_json}\n\n"
        "请针对以上未通过项修改教案。必须保持中间稿的 CTBLOCK 块标记注释行完整（数量与顺序不变），"
        "输出修改后的完整教案。"
    )
    return llm.chat(system=REFINE_SYSTEM_PROMPT, user=user_prompt)


def generate_with_validation(
    knowledge_md: str,
    template_path: str | Path,
    out_dir: str | Path,
    llm,
    config: dict,
) -> tuple[Path, list[dict]]:
    """生成 → 验证 → fail 则 refine，最多 N 轮；输出最终稿与每轮验证报告（JSON）。"""
    from src.lesson_generator import generate as generate_lesson

    s2 = config.get("stage2", {})
    max_rounds = s2.get("max_validation_rounds", 2)
    out_dir = Path(out_dir)
    template_blocks = parse_template(template_path, s2.get("fill_markers"))
    checkitems = build_checkitems(config, template_blocks)
    logger.info(f"验证检查表共 {len(checkitems)} 项")
    md_text = generate_lesson(
        knowledge_md, template_path, out_dir / "教案中间稿.md", llm, config
    )
    final_path = out_dir / "教案中间稿.md"
    reports: list[dict] = []
    for round_no in range(max_rounds + 1):
        report = validate(md_text, knowledge_md, checkitems, llm)
        report["round"] = round_no
        reports.append(report)
        report_path = out_dir / f"validation_report_round{round_no}.json"
        write_text(report_path, json.dumps(report, ensure_ascii=False, indent=2))
        logger.info(f"第 {round_no} 轮验证: {'PASS' if report['passed'] else 'FAIL'}（报告: {report_path.name}）")
        if report["passed"]:
            break
        if round_no < max_rounds:
            failures = [r for r in report["results"] if not r.get("pass")]
            logger.info(f"第 {round_no + 1} 次重生成开始（{len(failures)} 项未通过）")
            new_text = refine(md_text, failures, knowledge_md, llm)
            try:
                new_blocks = parse_intermediate(new_text)
            except ValueError:
                new_blocks = []
            if len(new_blocks) == len(template_blocks):
                md_text = new_text
                write_text(final_path, md_text)
            else:
                logger.warning("重生成结果破坏了块标记结构，已丢弃该轮修改")
    write_text(final_path, md_text)
    return final_path, reports
