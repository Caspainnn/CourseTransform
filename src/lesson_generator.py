import dataclasses
from pathlib import Path

from src.intermediate import parse_markdown_table, serialize_blocks
from src.llm_client import LLMClient
from src.template_parser import Block, markdown_table, parse_template
from src.utils import setup_logging, write_text

logger = setup_logging()


def build_system_prompt(
    total_minutes: int = 0,
    section_names: list[str] | None = None,
    extra_rules: list[str] | None = None,
) -> str:
    """生成者角色设定：基于教材、不编造数据、时间合计、中文、术语一致。"""
    rules = [
        "所有内容必须基于给定的教材内容，不得编造教材中没有的实验数据、结论或知识点。",
        "使用简体中文，术语表述与教材保持一致。",
    ]
    if total_minutes and total_minutes > 0:
        rules.append(f"教学过程各环节的时间合计必须等于 {total_minutes} 分钟。")
    if section_names:
        rules.append("教案需覆盖以下板块：" + "、".join(section_names))
    if extra_rules:
        rules.extend(extra_rules)
    rules.append("只输出要求填充的内容本身，不要输出解释性文字、前后缀或 Markdown 代码块标记。")
    body = "\n".join(f"{i}. {rule}" for i, rule in enumerate(rules, 1))
    return (
        "你是一名熟悉高校课堂教学设计的课程教师，负责依据给定教材内容撰写教学设计（教案）。"
        "请严格遵守以下规则：\n" + body
    )


def build_block_prompt(
    knowledge_md: str, block: Block, prev_summary: str = ""
) -> str:
    """为某个模板块构造填充提示词；prev_summary 保持上下文连贯。"""
    parts = [f"【教材内容】\n{knowledge_md}"]
    if prev_summary:
        parts.append(f"【前文已生成内容摘要】\n{prev_summary}")
    if block.context_heading:
        parts.append(f"【当前板块】{block.context_heading}")
    if block.type == "table":
        rows = block.rows or []
        n_rows = len(rows)
        n_cols = len(rows[0]) if rows else 0
        parts.append(
            f"【任务】请为教学设计模板中的表格填充内容。原表格共 {n_rows} 行 {n_cols} 列，"
            f"结构如下（Markdown 表格，部分单元格为待填标记）：\n{block.text}\n"
            "要求：1) 直接输出一个 Markdown 表格，行数和列数必须与原表格完全一致；"
            "2) 保留原表格中已有的固定内容（如列标题、环节名称），只填写待填标记和需要补全的内容；"
            "3) 单元格内容如需换行，用分号或顿号连接，不要在单元格内换行；"
            "4) 只输出 Markdown 表格，不要任何其他文字。"
        )
    else:
        original = block.text or "（空段落，请根据所属板块语义撰写）"
        parts.append(
            f"【任务】请为教学设计模板中的段落板块撰写内容。原模板该处文字为：\n{original}\n"
            "要求：直接输出填充后的段落文字，不要任何其他文字。"
        )
    return "\n\n".join(parts)


def normalize_rows(
    rows: list[list[str]], expected_rows: int, expected_cols: int
) -> list[list[str]]:
    """把模型输出的表格按原结构对齐（补空/截断），保证回填不出错。"""
    normalized: list[list[str]] = []
    for r in range(expected_rows):
        row = list(rows[r]) if r < len(rows) else []
        if len(row) < expected_cols:
            row = row + [""] * (expected_cols - len(row))
        normalized.append(row[:expected_cols])
    return normalized


def fill_table_block(
    llm: LLMClient,
    system: str,
    prompt: str,
    expected_rows: list[list[str]],
    retry_on_mismatch: bool = True,
) -> list[list[str]]:
    """整表一次生成：行列数不符时带纠错信息重试一次，仍不符则按原结构对齐。"""
    expected_shape = (len(expected_rows), len(expected_rows[0]) if expected_rows else 0)
    text = llm.chat(system=system, user=prompt)
    rows = parse_markdown_table(text)
    shape = (len(rows) if rows else 0, len(rows[0]) if rows else 0)
    if (rows is None or shape != expected_shape) and retry_on_mismatch:
        retry_prompt = (
            prompt
            + f"\n\n【注意】你之前输出的表格行列数不符合要求（期望 {expected_shape[0]} 行 "
            f"{expected_shape[1]} 列）。请重新输出 Markdown 表格，行列数必须与期望完全一致，只输出表格本身。"
        )
        text = llm.chat(system=system, user=retry_prompt)
        rows = parse_markdown_table(text)
    if rows is None:
        logger.warning("模型未能输出可解析的表格，保留模板原表格")
        return expected_rows
    shape = (len(rows), len(rows[0]) if rows else 0)
    if shape != expected_shape:
        logger.warning(f"表格行列不符（期望 {expected_shape}，实际 {shape}），按原结构对齐")
        rows = normalize_rows(rows, expected_shape[0], expected_shape[1])
    return rows


def fill_template(
    knowledge_md: str, blocks: list[Block], llm: LLMClient, config: dict
) -> list[Block]:
    """逐块调用模型填充：段落块用文字替换，表格块用 Markdown 表格替换，heading 原样保留。"""
    s2 = config.get("stage2", {})
    total_minutes = s2.get("total_minutes", 0)
    section_names = [b.text for b in blocks if b.type == "heading" and b.text]
    system = build_system_prompt(total_minutes, section_names)
    retry_on_mismatch = s2.get("table_retry_on_shape_mismatch", True)
    summary_length = s2.get("summary_length", 300)
    filled: list[Block] = []
    current_heading = ""
    prev_summary = ""
    for block in blocks:
        if block.type in ("paragraph", "table"):
            block.context_heading = current_heading
        new_block = dataclasses.replace(block)
        if block.type in ("paragraph", "table"):
            logger.info(f"正在生成块 {block.index}（{block.type}，所属板块：{current_heading}）")
            prompt = build_block_prompt(knowledge_md, block, prev_summary)
            if block.type == "table":
                new_rows = fill_table_block(
                    llm, system, prompt, block.rows or [], retry_on_mismatch
                )
                new_block.rows = new_rows
                new_block.text = markdown_table(new_rows)
            else:
                new_block.text = llm.chat(system=system, user=prompt).strip()
            prev_summary = new_block.text[:summary_length]
        elif block.type == "heading":
            current_heading = block.text
        filled.append(new_block)
    return filled


def generate(
    knowledge_md: str,
    template_path: str | Path,
    out_md_path: str | Path,
    llm: LLMClient,
    config: dict,
) -> str:
    """串联：解析模板 → 逐块生成 → 输出教案 markdown 中间稿（暂不转 docx）。"""
    s2 = config.get("stage2", {})
    blocks = parse_template(template_path, s2.get("fill_markers"))
    n_tables = sum(1 for b in blocks if b.type == "table")
    logger.info(f"模板解析完成：共 {len(blocks)} 个块（表格 {n_tables} 个）")
    filled = fill_template(knowledge_md, blocks, llm, config)
    md_text = serialize_blocks(filled)
    write_text(out_md_path, md_text)
    logger.info(f"教案中间稿已生成: {out_md_path}")
    return md_text
