from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from docx import Document

from .utils import now_iso


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class AcceptanceResult:
    passed: bool = False
    checks: list[Check] = field(default_factory=list)
    figures: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks.append(Check(name, passed, detail))

    @property
    def failures(self) -> list[str]:
        return [f"{c.name}：{c.detail}" for c in self.checks if not c.passed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in self.checks],
            "figures": self.figures,
            "metrics": self.metrics,
        }


def _text_of(doc: Document) -> str:
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                parts.append(cell.text)
    return "\n".join(parts)


def _image_hashes(doc: Document) -> set[str]:
    hashes: set[str] = set()
    try:
        for part in doc.part.package.image_parts:
            try:
                hashes.add(hashlib.sha256(part.blob).hexdigest())
            except Exception:
                continue
    except Exception:
        pass
    return hashes


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def accept_docx(
    docx_path: str | Path,
    plan: dict[str, Any],
    base_dir: str | Path | None = None,
) -> AcceptanceResult:
    result = AcceptanceResult()
    path = Path(docx_path)
    result.add("DOCX 文件存在", path.exists() and path.stat().st_size > 0, str(path))
    if not path.exists():
        result.passed = False
        return result

    try:
        doc = Document(str(path))
    except Exception as exc:
        result.add("DOCX 可打开", False, str(exc))
        result.passed = False
        return result
    result.add("DOCX 可打开", True, f"{len(doc.paragraphs)} 段，{len(doc.tables)} 表")

    text = _text_of(doc)
    result.add("无残留占位符", "{{" not in text and "待补充" not in text, "检查 {{...}} / 待补充")
    title = str((plan.get("meta") or {}).get("title") or "")
    result.add("课题出现在文档中", bool(title and title in text), title)
    result.add("正文长度充足", len(text.replace(" ", "")) >= 800, f"{len(text)} 字符")

    phases = [
        p
        for lesson in plan.get("lessons") or []
        for p in lesson.get("phases") or []
        if isinstance(p, dict)
    ]
    missing_phases = [str(p.get("name")) for p in phases if str(p.get("name")) not in text]
    result.add("教学环节完整", not missing_phases, "缺失：" + "、".join(missing_phases) if missing_phases else f"{len(phases)} 个环节")

    expected = [f for f in plan.get("figures") or [] if isinstance(f, dict) and f.get("path")]
    hashes = _image_hashes(doc)
    image_count = len(doc.inline_shapes)
    embedded = 0
    fig_rows: list[dict[str, Any]] = []
    for fig in expected:
        source = Path(str(fig.get("path")))
        if not source.is_absolute():
            source = Path(base_dir or ".") / source
        ok = source.exists() and _file_hash(source) in hashes
        if ok:
            embedded += 1
        caption = str(fig.get("caption") or "")
        fig_rows.append(
            {
                "id": fig.get("id"),
                "caption": caption,
                "placement": fig.get("placement"),
                "phase_id": fig.get("phase_id"),
                "embedded": ok,
                "file": str(source),
            }
        )
    if expected:
        result.add("配图全部嵌入", embedded == len(expected), f"{embedded}/{len(expected)} 张")
        result.add("图片总数匹配", image_count >= len(expected), f"文档图片 {image_count}，计划 {len(expected)}")
        missing_caps = [str(f.get("id")) for f in expected if str(f.get("caption") or "") not in text]
        result.add("图注出现在文档中", not missing_caps, "缺失：" + "、".join(missing_caps) if missing_caps else "全部图注可检索")
    else:
        result.add("配图检查", True, "本任务未上传配图")

    result.figures = fig_rows
    result.metrics = {
        "paragraphs": len(doc.paragraphs),
        "tables": len(doc.tables),
        "chars": len(text),
        "images": image_count,
        "expected_images": len(expected),
        "embedded_images": embedded,
    }
    result.passed = all(c.passed for c in result.checks)
    return result


def write_acceptance_report(
    path: str | Path,
    result: AcceptanceResult,
    plan: dict[str, Any],
    review_scores: dict[str, Any] | None = None,
) -> Path:
    out = Path(path)
    lines = [
        "# 验收报告（规则检查 + AI 复核）" if review_scores else "# 验收报告（规则检查）",
        "",
        f"- 验收时间：{now_iso()}",
        f"- 结论：**{'通过，可交付' if result.passed else '未通过，需要返工'}**",
        "",
        "## 检查项",
        "",
        "| 检查 | 结果 | 说明 |",
        "| --- | --- | --- |",
    ]
    for check in result.checks:
        lines.append(f"| {check.name} | {'通过' if check.passed else '不通过'} | {check.detail} |")
    if result.figures:
        lines += ["", "## 配图验收", "", "| 图片 | 图注 | 位置 | 已嵌入 |", "| --- | --- | --- | --- |"]
        for fig in result.figures:
            lines.append(
                f"| {fig.get('id')} | {fig.get('caption')} | {fig.get('placement')} {fig.get('phase_id') or ''} | {'是' if fig.get('embedded') else '否'} |"
            )
    if review_scores:
        lines += ["", "## AI 教研复核评分", "", "| 维度 | 分数 |", "| --- | --- |"]
        for key, value in review_scores.items():
            lines.append(f"| {key} | {value} |")
    if not result.passed:
        lines += ["", "## 返工建议", ""]
        for failure in result.failures:
            lines.append(f"- {failure}")
    lines += ["", "---", "", "本报告由 DOCX 实际内容检查生成；教师仍需做最终教学复核。"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
