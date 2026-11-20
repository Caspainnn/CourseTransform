from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

CST = timezone(timedelta(hours=8))

TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "utf-16")


def now_iso() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


def read_text(path: str | Path) -> str:
    """按常见中文编码读取文本。"""
    p = Path(path)
    data = p.read_bytes()
    for enc in TEXT_ENCODINGS:
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def write_text(path: str | Path, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_name(text: str, maxlen: int = 48) -> str:
    """生成 Windows 安全文件名，保留中文。"""
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", str(text)).strip(" ._")
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"_+", "_", s)
    return (s or "untitled")[:maxlen].strip(" ._") or "untitled"


def normalize_label(text: str) -> str:
    """把“一、教学目标（核心素养）”归一化为“教学目标核心素养”。

    用于模板字段识别，不用于正文比较。
    """
    s = str(text or "").strip().splitlines()[0] if str(text or "").strip() else ""
    s = re.sub(r"^\s*#{1,6}\s*", "", s)
    s = re.sub(r"^\s*[（(]?[一二三四五六七八九十百零〇]+[）)]?\s*[、.．:：]\s*", "", s)
    s = re.sub(r"^\s*第\s*[一二三四五六七八九十百零〇\d]+\s*[章节单元部分]\s*", "", s)
    s = re.sub(r"^\s*\d+(?:\.\d+)*\s*[、.．:：]?\s*", "", s)
    s = re.sub(r"[\s\u3000:：\-—–·、，,。.．;；!！?？()（）\[\]【】<>《》\"'“”‘’]", "", s)
    return s


def char_count(text: str) -> int:
    return len(re.sub(r"\s", "", str(text or "")))


def truncate(text: str, limit: int, suffix: str = "……") -> str:
    text = str(text or "")
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(suffix))] + suffix


def split_sentences(text: str, limit: int | None = None) -> list[str]:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return []
    parts = re.split(r"(?<=[。！？!?；;])\s*", text)
    out = [p.strip() for p in parts if p.strip()]
    if limit is not None:
        out = out[:limit]
    return out


def summary_of(text: str, limit: int = 180) -> str:
    sents = split_sentences(text)
    out = ""
    for s in sents:
        if len(out) + len(s) > limit and out:
            break
        out += s
    return truncate(out or text, limit)


def dedupe_keep_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = str(item).strip()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def extract_json(text: str) -> dict[str, Any]:
    """从模型输出中稳健地提取第一个 JSON 对象。"""
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("模型没有返回内容")

    fenced = re.match(r"^```(?:json|JSON)?\s*(.*?)\s*```$", raw, re.S)
    if fenced:
        raw = fenced.group(1).strip()

    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for m in re.finditer(r"\{", raw):
        candidate = raw[m.start() :]
        try:
            obj, _ = decoder.raw_decode(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    raise ValueError(f"无法从模型输出中解析 JSON（前 300 字符）：{raw[:300]}")


def strip_punct(text: str) -> str:
    return re.sub(r"[\s\u3000，,。.．；;：:！!？?、“”‘’\"'（）()《》【】\[\]\-—–]", "", str(text or ""))


def fuzzy_ratio(a: str, b: str) -> float:
    """轻量相似度，避免依赖 rapidfuzz。"""
    from difflib import SequenceMatcher

    return SequenceMatcher(None, strip_punct(a), strip_punct(b)).ratio()


def jaccard_chars(a: str, b: str) -> float:
    sa = set(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", a))
    sb = set(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def rough_tokens(text: str) -> int:
    """粗略 token 估算：中文约 1 字 1 token，英文按 4 字符 1 token。"""
    text = str(text or "")
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    other = len(re.sub(r"[\u4e00-\u9fff]", "", text))
    return cjk + max(1, other // 4)


def fmt_minutes(minutes: int) -> str:
    return f"{int(minutes)}分钟"


class Console:
    """统一控制台输出，同时写入日志文件。"""

    def __init__(self, log_path: str | Path | None = None, verbose: bool = False, stream=None):
        self.verbose = verbose
        self.stream = stream or sys.stdout
        self._fh = None
        if log_path:
            p = Path(log_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            self._fh = p.open("a", encoding="utf-8", newline="\n")

    def _emit(self, text: str) -> None:
        print(text, file=self.stream, flush=True)
        if self._fh:
            self._fh.write(text + "\n")
            self._fh.flush()

    def step(self, index: int, total: int, message: str) -> None:
        self._emit(f"[{index}/{total}] {message}")

    def info(self, message: str) -> None:
        self._emit(f"  {message}")

    def detail(self, message: str) -> None:
        if self.verbose:
            self._emit(f"    {message}")

    def warn(self, message: str) -> None:
        self._emit(f"  [WARN] {message}")

    def error(self, message: str) -> None:
        self._emit(f"  [ERROR] {message}")

    def ok(self, message: str) -> None:
        self._emit(f"  [OK] {message}")

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None


def ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def as_text(value: Any, sep: str = "；") -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        return sep.join(f"{k}：{as_text(v)}" for k, v in value.items())
    if isinstance(value, Sequence):
        return sep.join(as_text(v) for v in value)
    return str(value)
