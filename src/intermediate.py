import re

from src.template_parser import Block

MARKER_RE = re.compile(
    r'^<!--\s*CTBLOCK\s+index=(\d+)\s+type=(\w+)\s+style="([^"]*)"\s*-->\s*$', re.M
)


def serialize_blocks(blocks: list[Block]) -> str:
    """把块列表序列化为带 CTBLOCK 标记的教案 markdown 中间稿。"""
    parts: list[str] = []
    for block in blocks:
        parts.append(
            f'<!-- CTBLOCK index={block.index} type={block.type} style="{block.style_name}" -->'
        )
        parts.append(block.text)
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def parse_markdown_table(text: str) -> list[list[str]] | None:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    rows: list[list[str]] = []
    for line in lines:
        if re.match(r"^\|?[\s:|-]+\|?$", line) and "-" in line:
            continue
        stripped = line.strip()
        if stripped.startswith("|"):
            stripped = stripped[1:]
        if stripped.endswith("|"):
            stripped = stripped[:-1]
        protected = stripped.replace("\\|", "\x00")
        cells = [cell.strip().replace("\x00", "|") for cell in protected.split("|")]
        rows.append(cells)
    return rows or None


def parse_intermediate(md_text: str) -> list[Block]:
    """解析中间稿回块列表（供 docx 回填使用）。"""
    matches = list(MARKER_RE.finditer(md_text))
    if not matches:
        raise ValueError("中间稿中未找到 CTBLOCK 块标记，无法解析")
    blocks: list[Block] = []
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md_text)
        content = md_text[start:end].strip()
        block_type = match.group(2)
        rows = None
        if block_type == "table":
            rows = parse_markdown_table(content)
        blocks.append(
            Block(
                index=int(match.group(1)),
                type=block_type,
                text=content,
                style_name=match.group(3),
                rows=rows,
            )
        )
    return blocks
