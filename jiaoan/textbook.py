from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .utils import (
    char_count,
    dedupe_keep_order,
    normalize_label,
    read_text,
    rough_tokens,
    split_sentences,
    strip_punct,
    summary_of,
    truncate,
)

CHAPTER_RE = re.compile(
    r"^\s*(?:第\s*[一二三四五六七八九十百零〇\d]+\s*[章篇部]|(?:chapter|unit)\s*\d+)",
    re.IGNORECASE,
)
SECTION_RE = re.compile(r"^\s*第\s*[一二三四五六七八九十百零〇\d]+\s*[节]", re.IGNORECASE)
ATX_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
CN_NUM_RE = re.compile(r"^\s*[一二三四五六七八九十百零〇]+\s*[、.．]\s*\S")
DECIMAL_RE = re.compile(r"^\s*\d+\.\d+(?:\.\d+)?\s+\S")


@dataclass
class Section:
    title: str
    level: int
    start_line: int
    end_line: int
    content: str
    index: int = 0

    @property
    def chars(self) -> int:
        return char_count(self.content)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "level": self.level,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "chars": self.chars,
        }


@dataclass
class Chapter:
    id: str
    title: str
    level: int
    start_line: int
    end_line: int
    content: str
    sections: list[Section] = field(default_factory=list)
    source_path: str = ""

    @property
    def chars(self) -> int:
        return char_count(self.content)

    @property
    def section_titles(self) -> list[str]:
        return [s.title for s in self.sections]

    def key_sections(self, max_items: int = 12) -> list[Section]:
        """返回适合做“章节覆盖”的核心小节，而不是 65 个二级标题。

        教材 md 常用统一的 ## 级别，导致 3.1、3.1.1、3.1.2 和
        “1. 卷积层”都变成同一级小节。覆盖检查只应看 3.1/3.2 这类
        顶层编号小节；如果教材没有编号，再退回按标题级别选择。
        """
        numbered: list[tuple[int, Section]] = []
        for s in self.sections:
            m = re.match(r"^\s*(\d+(?:\.\d+)+)\s*[、.．:：]?\s*\S", s.title or "")
            if m:
                numbered.append((m.group(1).count(".") + 1, s))
        if numbered:
            min_depth = min(depth for depth, _ in numbered)
            chosen = [s for depth, s in numbered if depth == min_depth and s.chars > 0]
            if len(chosen) < 3:
                chosen = [s for depth, s in numbered if depth <= min_depth + 1 and s.chars > 0]
            return chosen[:max_items]
        levels = [s.level for s in self.sections if s.chars > 0]
        if not levels:
            return self.sections[:max_items]
        base = min(levels)
        chosen = [s for s in self.sections if s.level == base and s.chars > 0]
        return (chosen or self.sections)[:max_items]

    @property
    def key_section_titles(self) -> list[str]:
        return [s.title for s in self.key_sections()]

    def to_dict(self, include_sections: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "level": self.level,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "chars": self.chars,
            "sections": [s.to_dict() for s in self.sections] if include_sections else len(self.sections),
        }
        return data


@dataclass
class _Heading:
    line: int
    level: int
    text: str


@dataclass
class Textbook:
    path: str
    title: str
    lines: list[str]
    chapters: list[Chapter]

    @property
    def chars(self) -> int:
        return sum(c.chars for c in self.chapters)

    def find_chapter(self, selector: str | int) -> Chapter:
        s = str(selector).strip()
        if s.isdigit():
            idx = int(s)
            if 1 <= idx <= len(self.chapters):
                return self.chapters[idx - 1]
            raise KeyError(f"章节序号超出范围：{s}（共 {len(self.chapters)} 章）")
        for c in self.chapters:
            if c.id.lower() == s.lower():
                return c
        for c in self.chapters:
            if c.title.strip() == s:
                return c
        matches = [c for c in self.chapters if s and s in c.title]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise KeyError(f"找不到章节：{s}")
        raise KeyError(f"章节名不唯一：{s}，候选：{[c.title for c in matches]}")

    def select(self, spec: str | None) -> list[Chapter]:
        if not spec or spec.strip().lower() in {"all", "*", "全部"}:
            return list(self.chapters)
        selectors = [x.strip() for x in re.split(r"[,，;；]", spec) if x.strip()]
        out: list[Chapter] = []
        for sel in selectors:
            out.append(self.find_chapter(sel))
        return dedupe_chapters(out)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "title": self.title,
            "chars": self.chars,
            "chapters": [c.to_dict() for c in self.chapters],
        }


def dedupe_chapters(items: Iterable[Chapter]) -> list[Chapter]:
    seen: set[str] = set()
    out: list[Chapter] = []
    for c in items:
        if c.id not in seen:
            seen.add(c.id)
            out.append(c)
    return out


def _scan_headings(lines: list[str]) -> list[_Heading]:
    in_fence = False
    fence_mark = ""
    atx: list[_Heading] = []
    plain: list[_Heading] = []

    for i, raw in enumerate(lines, start=1):
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            mark = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_mark = mark
            elif mark == fence_mark:
                in_fence = False
                fence_mark = ""
            continue
        if in_fence:
            continue

        m = ATX_RE.match(stripped)
        if m:
            text = re.sub(r"\s+#+$", "", m.group(2)).strip()
            if text:
                atx.append(_Heading(i, len(m.group(1)), text))
            continue

        # 纯文本章节标题（很多 PPT 转 md 后没有 #）
        if len(stripped) <= 80 and CHAPTER_RE.match(stripped):
            plain.append(_Heading(i, 1, stripped))
            continue
        if len(stripped) <= 80 and SECTION_RE.match(stripped):
            plain.append(_Heading(i, 2, stripped))
            continue
        if len(stripped) <= 36 and DECIMAL_RE.match(stripped) and not stripped.endswith(("。", "！", "？", "；", "，")):
            plain.append(_Heading(i, 2, stripped))
            continue
        if len(stripped) <= 30 and CN_NUM_RE.match(stripped) and not stripped.endswith(("。", "！", "？", "；", "，")):
            plain.append(_Heading(i, 2, stripped))

    if not atx:
        # 没有 markdown 标题时，才启用中文序号/小数序号猜测，避免把正文列表当标题
        return _dedupe_headings(plain)

    all_headings = _dedupe_headings(atx + plain)
    all_headings.sort(key=lambda h: h.line)
    return all_headings


def _dedupe_headings(headings: list[_Heading]) -> list[_Heading]:
    seen: set[int] = set()
    out: list[_Heading] = []
    for h in sorted(headings, key=lambda x: x.line):
        if h.line not in seen:
            seen.add(h.line)
            out.append(h)
    return out


def _chapter_candidates(headings: list[_Heading]) -> list[_Heading]:
    return [h for h in headings if CHAPTER_RE.match(h.text)]


def _build_chapter(
    chapter_id: str,
    heading: _Heading | None,
    title: str,
    start_line: int,
    end_line: int,
    lines: list[str],
    source_path: str,
    all_headings: list[_Heading],
) -> Chapter:
    content = "\n".join(lines[start_line - 1 : end_line])
    level = heading.level if heading else 1

    inner = [h for h in all_headings if start_line < h.line <= end_line and (heading is None or h.line != heading.line)]
    if inner:
        section_level = min(h.level for h in inner)
        section_heads = [h for h in inner if h.level == section_level]
    else:
        section_heads = []

    sections: list[Section] = []
    if section_heads:
        for idx, sh in enumerate(section_heads):
            next_line = section_heads[idx + 1].line if idx + 1 < len(section_heads) else end_line + 1
            sec_end = next_line - 1
            # 下一章开始行不纳入
            sec_end = min(sec_end, end_line)
            sec_content = "\n".join(lines[sh.line : sec_end])
            sections.append(
                Section(
                    title=sh.text,
                    level=sh.level,
                    start_line=sh.line,
                    end_line=sec_end,
                    content=sec_content,
                    index=idx + 1,
                )
            )
    else:
        sec_start = (heading.line + 1) if heading else start_line
        sections.append(
            Section(
                title=title,
                level=level + 1,
                start_line=sec_start,
                end_line=end_line,
                content="\n".join(lines[sec_start - 1 : end_line]),
                index=1,
            )
        )

    return Chapter(
        id=chapter_id,
        title=title,
        level=level,
        start_line=start_line,
        end_line=end_line,
        content=content,
        sections=sections,
        source_path=source_path,
    )


def parse_textbook(
    path: str | Path,
    manifest: dict[str, Any] | None = None,
    default_title: str | None = None,
) -> Textbook:
    p = Path(path)
    text = read_text(p)
    lines = text.splitlines()
    if not lines:
        raise ValueError(f"教材文件为空：{p}")

    source_path = str(p)
    headings = _scan_headings(lines)
    book_title = default_title or p.stem
    first_h1 = next((h for h in headings if h.level == 1), None)
    if first_h1 and first_h1.line <= 5:
        book_title = first_h1.text

    if manifest and manifest.get("chapters"):
        chapters = _chapters_from_manifest(manifest, lines, headings, source_path)
        return Textbook(path=source_path, title=str(manifest.get("title") or book_title), lines=lines, chapters=chapters)

    candidates = _chapter_candidates(headings)
    chapter_heads: list[_Heading] = []
    if candidates:
        chapter_heads = candidates
    else:
        h1 = [h for h in headings if h.level == 1]
        if len(h1) >= 2:
            chapter_heads = h1
        elif len(h1) == 1:
            # 一个总标题 + 若干二级标题：把总标题当“全书”，二级标题当章节
            h2 = [h for h in headings if h.level == 2]
            if len(h2) >= 2:
                chapter_heads = h2
            else:
                chapter_heads = h1

    chapters: list[Chapter] = []
    if chapter_heads:
        chapter_heads.sort(key=lambda h: h.line)
        for i, h in enumerate(chapter_heads):
            start = h.line
            end = (chapter_heads[i + 1].line - 1) if i + 1 < len(chapter_heads) else len(lines)
            # 跳过章标题之前的内容；若第一个章标题很靠后，也不单独成章
            chapters.append(
                _build_chapter(
                    chapter_id=f"ch{i + 1:02d}",
                    heading=h,
                    title=h.text,
                    start_line=start,
                    end_line=end,
                    lines=lines,
                    source_path=source_path,
                    all_headings=headings,
                )
            )
    else:
        chapters.append(
            _build_chapter(
                chapter_id="ch01",
                heading=None,
                title=book_title,
                start_line=1,
                end_line=len(lines),
                lines=lines,
                source_path=source_path,
                all_headings=headings,
            )
        )

    return Textbook(path=source_path, title=book_title, lines=lines, chapters=chapters)


def _chapters_from_manifest(
    manifest: dict[str, Any],
    lines: list[str],
    headings: list[_Heading],
    source_path: str,
) -> list[Chapter]:
    chapters: list[Chapter] = []
    seen_ids: set[str] = set()
    for i, entry in enumerate(manifest["chapters"], start=1):
        try:
            start = int(entry.get("start_line", entry.get("start", 1)))
            end = int(entry.get("end_line", entry.get("end", len(lines))))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"manifest 第 {i} 章的行号不是整数：{entry}") from exc
        start = max(1, min(start, len(lines)))
        end = max(start, min(end, len(lines)))
        title = str(entry.get("title") or "").strip()
        if not title:
            title = lines[start - 1].strip() or f"第{i}章"
            title = re.sub(r"^#{1,6}\s*", "", title)
        cid = str(entry.get("id") or f"ch{i:02d}").strip()
        if cid in seen_ids:
            cid = f"{cid}_{i:02d}"
        seen_ids.add(cid)
        heading = next((h for h in headings if h.line == start), None)
        chapters.append(
            _build_chapter(
                chapter_id=cid,
                heading=heading,
                title=title,
                start_line=start,
                end_line=end,
                lines=lines,
                source_path=source_path,
                all_headings=headings,
            )
        )
    return chapters


def estimate_periods(chapter: Chapter, minutes_per_period: int = 45) -> int:
    """按篇幅粗略估计课时，教师可用 --periods 覆盖。"""
    chars = max(1, chapter.chars)
    estimate = max(1, math.ceil(chars / 3200))
    if len(chapter.sections) >= 5 and estimate < 2:
        estimate = 2
    return min(estimate, 4)


def _tokenize(text: str) -> list[str]:
    t = str(text or "").lower()
    cjk = re.findall(r"[\u4e00-\u9fff]", t)
    bigrams = ["".join(cjk[i : i + 2]) for i in range(max(0, len(cjk) - 1))]
    words = re.findall(r"[a-z0-9]{2,}", t)
    return bigrams + words


def _bm25_scores(query: str, docs: list[str], k1: float = 1.5, b: float = 0.75) -> list[float]:
    q_terms = _tokenize(query)
    if not q_terms or not docs:
        return [0.0] * len(docs)
    doc_tokens = [_tokenize(d) for d in docs]
    n = len(docs)
    avgdl = sum(len(d) for d in doc_tokens) / max(1, n)
    df: dict[str, int] = {}
    for toks in doc_tokens:
        for term in set(toks):
            df[term] = df.get(term, 0) + 1
    scores: list[float] = []
    for toks in doc_tokens:
        tf: dict[str, int] = {}
        for term in toks:
            tf[term] = tf.get(term, 0) + 1
        dl = len(toks)
        score = 0.0
        for term in q_terms:
            if term not in tf:
                continue
            idf = math.log(1 + (n - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5))
            score += idf * (tf[term] * (k1 + 1)) / (tf[term] + k1 * (1 - b + b * dl / max(1.0, avgdl)))
        scores.append(score)
    return scores


def select_sections(chapter: Chapter, query: str, top_k: int = 3) -> list[Section]:
    if not chapter.sections:
        return []
    docs = [f"{s.title}\n{s.content}" for s in chapter.sections]
    scores = _bm25_scores(query, docs)
    ranked = sorted(range(len(docs)), key=lambda i: scores[i], reverse=True)
    picked = [chapter.sections[i] for i in ranked[: max(1, top_k)] if scores[i] > 0]
    if not picked:
        picked = chapter.sections[: min(top_k, len(chapter.sections))]
    return sorted(picked, key=lambda s: s.index)


def context_for_llm(
    chapter: Chapter,
    budget_chars: int = 24000,
    query: str = "",
    include_full: bool = True,
) -> str:
    """构建带行号锚点的教材上下文；超长时按相关度节选。"""
    head = [
        f"【教材章节】{chapter.title}",
        f"【源文件】{Path(chapter.source_path).name}（第 {chapter.start_line}-{chapter.end_line} 行）",
        "【章节结构】",
    ]
    for s in chapter.sections:
        head.append(f"- [L{s.start_line}-L{s.end_line}] {s.title}（约 {s.chars} 字）")
    head.append("")

    body: list[str] = ["【教材原文】"]
    if include_full and len(chapter.content) <= budget_chars:
        for s in chapter.sections:
            body.append(f"### [L{s.start_line}-L{s.end_line}] {s.title}")
            body.append(s.content.strip())
            body.append("")
        return "\n".join(head + body).strip()

    picked = select_sections(chapter, query, top_k=3) if query else chapter.sections[:2]
    picked_ids = {s.index for s in picked}
    per_section_budget = max(900, int(budget_chars / max(1, len(picked))))
    used = 0
    body.append("（本章较长，以下为与任务相关的节选；未展开部分请依据章节结构和小节摘要把握。）")
    body.append("")
    for s in chapter.sections:
        if s.index in picked_ids and used < budget_chars:
            alloc = max(500, min(per_section_budget, budget_chars - used))
            text = truncate(s.content.strip(), alloc)
            body.append(f"### [L{s.start_line}-L{s.end_line}] {s.title}")
            body.append(text)
            body.append("")
            used += len(text)
        else:
            summ = summary_of(s.content, 220) or "（本节内容较短，见原文。）"
            body.append(f"### [L{s.start_line}-L{s.end_line}] {s.title}（摘要）")
            body.append(summ)
            body.append("")
            used += len(summ) + 30
        if used > budget_chars * 1.1:
            if s.index != chapter.sections[-1].index:
                body.append("（后续小节从略，已超出上下文预算。）")
            break
    return "\n".join(head + body).strip()


def chapter_brief(chapter: Chapter) -> str:
    lines = [f"章节：{chapter.title}（{chapter.chars} 字，{len(chapter.sections)} 节）"]
    for s in chapter.sections:
        first = split_sentences(s.content, 1)
        lines.append(f"- {s.title}：{first[0] if first else '（无正文）'}")
    return "\n".join(lines)


def ground_quote(chapter: Chapter, quote: str) -> tuple[str, int, int] | None:
    """把模型引用的句子锚定回教材原文行号。

    先做标点无关的精确匹配，再逐句模糊匹配。返回 (原文片段, 起始行, 结束行)。
    找不到则返回 None。
    """
    q = strip_punct(quote)
    if len(q) < 6:
        return None
    line_offset = chapter.start_line - 1
    sentences = _sentences_with_lines(chapter)

    # 1) 精确匹配（忽略标点与括号补充说明）
    for sent, line_no in sentences:
        if q in _clean_for_match(sent):
            return sent, line_no, line_no

    # 2) 单句模糊匹配（允许模型漏掉括号内的补充说明）
    from difflib import SequenceMatcher

    best: tuple[float, str, int] | None = None
    for sent, line_no in sentences:
        ratio = SequenceMatcher(None, q, _clean_for_match(sent)).ratio()
        if ratio >= 0.7 and (best is None or ratio > best[0]):
            best = (ratio, sent, line_no)
    if best:
        return best[1], best[2], best[2]

    # 3) 相邻两句合并匹配（引用跨句时）
    for i in range(len(sentences) - 1):
        (s1, l1), (s2, l2) = sentences[i], sentences[i + 1]
        if l2 - l1 > 1:
            continue
        merged = s1 + s2
        ratio = SequenceMatcher(None, q, _clean_for_match(merged)).ratio()
        if ratio >= 0.78 and (best is None or ratio > best[0]):
            best = (ratio, merged, l1)
    if best:
        return best[1], best[2], best[2]
    return None


def _clean_for_match(text: str) -> str:
    text = re.sub(r"（[^）]{0,100}）", "", str(text or ""))
    text = re.sub(r"\([^)]{0,100}\)", "", text)
    return strip_punct(text)


def _sentences_with_lines(chapter: Chapter) -> list[tuple[str, int]]:
    sentences: list[tuple[str, int]] = []
    for idx, line in enumerate(chapter.content.splitlines(), start=chapter.start_line):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("|"):
            continue
        parts = re.findall(r"[^。！？!?；;]+[。！？!?；;]?", stripped)
        for part in parts:
            part = part.strip()
            if len(strip_punct(part)) >= 6:
                sentences.append((part, idx))
    return sentences


def find_quote_line(chapter: Chapter, quote: str) -> tuple[int, int] | None:
    hit = ground_quote(chapter, quote)
    if not hit:
        return None
    return hit[1], hit[2]


def section_title_set(chapter: Chapter) -> list[str]:
    return dedupe_keep_order([normalize_label(s.title) for s in chapter.sections if normalize_label(s.title)])


def section_display_titles(chapter: Chapter) -> list[str]:
    return [s.title for s in chapter.sections]
