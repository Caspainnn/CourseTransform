from __future__ import annotations

import base64
import io
import json
import os
import re
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from .images import IMAGE_EXTS
from .llm import LLMClient, LLMError, LLMSettings, settings_from_env
from .pipeline import ChapterResult, LessonPipeline, merge_brief
from .nine_section import is_nine_section
from .template import extract_template_info, parse_template
from .textbook import parse_textbook
from .utils import Console, now_iso, safe_name, write_text

JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()
MAX_BODY = 60 * 1024 * 1024
SMALL_BODY = 1024 * 1024

LOCAL_BASE_URL = "http://127.0.0.1:11435/v1"
LOCAL_MODEL = "qwen2.5:7b"
LOCAL_KEY = "ollama"
WEB_VERSION = "2026-11-19.25"


def _json_bytes(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def _redact(text: Any, secret: str) -> str:
    s = str(text)
    if secret and len(secret) >= 4:
        s = s.replace(secret, "***")
    return s


def _normalize_base_url(url: str) -> str:
    u = str(url or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/completions"):
        if u.endswith(suffix):
            u = u[: -len(suffix)].rstrip("/")
    return u


def _is_local_base(base_url: str) -> bool:
    try:
        host = (urlparse(base_url).hostname or "").lower()
    except Exception:
        return False
    return host in {"127.0.0.1", "localhost", "::1"}


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _settings_for_api(api: dict[str, Any], *, max_tokens: int | None = None, timeout: int | None = None, no_cache: bool = False) -> LLMSettings:
    base = _normalize_base_url(str(api.get("base_url") or ""))
    model = str(api.get("model") or "").strip()
    key = str(api.get("api_key") or "").strip()
    if key.lower().startswith("bearer "):
        key = key[7:].strip()
    if not base:
        raise ValueError("请填写 Base URL（OpenAI 兼容接口，通常以 /v1 结尾）。")
    parsed = urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Base URL 格式不正确，应以 http:// 或 https:// 开头。")
    if not model:
        raise ValueError("请填写模型名，例如 deepseek-chat。")
    if not key:
        if _is_local_base(base):
            key = LOCAL_KEY
        else:
            raise ValueError("请填写 API Key。")
    return LLMSettings(
        api_key=key,
        base_url=base,
        model=model,
        temperature=0.3,
        max_tokens=max_tokens if max_tokens is not None else _as_int(api.get("max_tokens"), 8192),
        timeout=timeout if timeout is not None else _as_int(api.get("timeout"), 900),
        max_repair=1,
        max_context_chars=_as_int(api.get("max_context_chars"), 12000),
        cache_dir=".jiaoan_cache",
        no_cache=no_cache,
        offline=False,
        disable_thinking=api.get("disable_thinking") if isinstance(api.get("disable_thinking"), bool) else None,
    )


def _public_api(api: dict[str, Any], label: str = "") -> dict[str, Any]:
    return {
        "label": label or str(api.get("label") or "自定义 API"),
        "base_url": _normalize_base_url(str(api.get("base_url") or "")),
        "model": str(api.get("model") or ""),
    }


def _probe_local() -> bool:
    try:
        req = urllib.request.Request("http://127.0.0.1:11435/api/tags", headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        return False


def _http_error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", errors="replace")[:300]
    except Exception:
        return ""


def _fetch_models(base_url: str, api_key: str) -> list[str]:
    base = _normalize_base_url(base_url)
    if not base:
        raise ValueError("Base URL 为空。")
    key = str(api_key or "").strip()
    if key.lower().startswith("bearer "):
        key = key[7:].strip()
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(f"{base}/models", headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        detail = _http_error_detail(exc)
        if _is_local_base(base):
            # Ollama 原生接口兜底
            root = f"{urlparse(base).scheme}://{urlparse(base).netloc}"
            try:
                req2 = urllib.request.Request(f"{root}/api/tags", headers={"Accept": "application/json"}, method="GET")
                with urllib.request.urlopen(req2, timeout=5) as resp2:
                    native = json.loads(resp2.read().decode("utf-8", errors="replace"))
                models = [str(m.get("name")) for m in native.get("models") or [] if isinstance(m, dict) and m.get("name")]
                if models:
                    return sorted(set(models))
            except Exception:
                pass
        raise RuntimeError(f"获取模型失败（HTTP {exc.code}）：{detail or exc.reason}") from exc
    except Exception as exc:
        raise RuntimeError(f"获取模型失败：{exc}") from exc

    models: list[str] = []
    for item in data.get("data") or []:
        if isinstance(item, dict) and item.get("id"):
            models.append(str(item["id"]))
    for item in data.get("models") or []:
        if isinstance(item, dict) and item.get("name"):
            models.append(str(item["name"]))
    return sorted(set(models))


def _template_info(payload: dict[str, Any]) -> dict[str, Any]:
    name = safe_name(str(payload.get("name") or "template.docx"), 80)
    suffix = Path(name).suffix.lower()
    if suffix not in {".docx", ".md", ".markdown"}:
        raise ValueError("只支持 .docx / .md 模板")
    raw = base64.b64decode(str(payload.get("base64") or ""), validate=True)
    if not raw or len(raw) > 20 * 1024 * 1024:
        raise ValueError("模板为空或超过 20MB")
    fd, temp_name = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
        spec = parse_template(temp_name)
        info = extract_template_info(spec)
        return {
            "info": info,
            "template": {
                "title": spec.title,
                "kind": spec.kind,
                "exemplar": spec.exemplar,
                "nine_section": is_nine_section(spec),
                "slots": len(spec.slots),
            },
        }
    finally:
        try:
            os.unlink(temp_name)
        except Exception:
            pass


def _test_api(api: dict[str, Any]) -> dict[str, Any]:
    settings = _settings_for_api(api, max_tokens=256, timeout=60, no_cache=True)
    client = LLMClient(settings, Console(stream=io.StringIO()))
    started = time.time()
    resp = client.chat(
        [
            {"role": "system", "content": "你是接口连通性测试助手，只回复两个汉字。"},
            {"role": "user", "content": "请只回复：正常"},
        ],
        purpose="test",
        max_tokens=256,
        json_mode=False,
    )
    return {
        "ok": True,
        "model": settings.model,
        "base_url": settings.base_url,
        "latency_ms": int((time.time() - started) * 1000),
        "reply": (resp.text or "").strip()[:80],
    }


def _docx_entries(job: dict[str, Any]) -> list[dict[str, Any]]:
    """把每个章节的 1..N 个学时 Word 展平成前端可下载的列表。"""
    entries: list[dict[str, Any]] = []
    for result in job.get("results") or []:
        if not isinstance(result, dict):
            continue
        chapter_id = result.get("chapter_id") or ""
        chapter_title = result.get("title") or ""
        sub = [x for x in (result.get("docx_files") or []) if isinstance(x, dict)]
        if not sub:
            path = (result.get("files") or {}).get("docx") or ""
            if path:
                sub = [{"path": path, "title": chapter_title or "教案"}]
        for item in sub:
            path = str(item.get("path") or "")
            if path and Path(path).exists():
                entries.append(
                    {
                        "index": len(entries) + 1,
                        "chapter_id": chapter_id,
                        "title": str(item.get("title") or chapter_title or "教案"),
                        "url": f"/api/jobs/{job['id']}/docx/{len(entries) + 1}",
                    }
                )
    return entries


def _docx_files(job: dict[str, Any]) -> list[dict[str, Any]]:
    return _docx_entries(job)


def _job_public(job: dict[str, Any]) -> dict[str, Any]:
    docx_files = _docx_files(job)
    log_path = Path(str(job.get("log") or ""))
    return {
        "id": job["id"],
        "status": job["status"],
        "progress": job.get("progress") or {"current": 0, "total": 0},
        "chapters": job.get("chapters") or [],
        "api": job.get("api_public") or {},
        "docx_files": docx_files,
        "error": job.get("error") or "",
        "warnings": job.get("warnings") or [],
        "has_log": bool(str(job.get("log") or "") and log_path.exists()),
        "log_url": f"/api/jobs/{job['id']}/log" if job.get("log") else "",
        "created_at": job.get("created_at"),
        "finished_at": job.get("finished_at"),
    }


def _update(job_id: str, **kwargs: Any) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job:
            job.update(kwargs)


def _job_from_disk(job_dir: Path, job_id: str) -> dict[str, Any] | None:
    """服务重启后，从任务目录恢复任务详情，保证旧 Word/日志仍可打开。"""
    manifest_path = job_dir / "out" / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    results = [r for r in (data.get("chapters") or []) if isinstance(r, dict)]
    if not results:
        return None
    chapters = []
    for idx, r in enumerate(results, start=1):
        files = r.get("files") or {}
        chapters.append(
            {
                "id": r.get("chapter_id") or "",
                "title": r.get("title") or "",
                "status": r.get("status") or "failed",
                "docx": files.get("docx", ""),
                "error": r.get("error") or "",
                "stage": "",
                "warnings": r.get("warnings") or [],
                "validation": r.get("validation") or {},
                "acceptance": r.get("acceptance") or {},
                "output_dir": r.get("output_dir") or "",
                "image_summary": r.get("image_summary") or {},
                "image_report": files.get("image_report", ""),
                "image_report_url": f"/api/jobs/{job_id}/image-report/{idx}" if files.get("image_report") else "",
            }
        )
    ok = all(r.get("status") in {"ok", "ok_with_warnings"} for r in results)
    if ok:
        status = "succeeded"
    elif any(r.get("status") in {"ok", "ok_with_warnings"} for r in results):
        status = "partial_failed"
    elif any(r.get("status") == "quality_failed" for r in results):
        status = "quality_failed"
    else:
        status = "failed"
    errors = [f"{r.get('title')}：{r.get('error')}" for r in results if r.get("error")]
    return {
        "id": job_id,
        "status": status,
        "created_at": data.get("generated_at"),
        "finished_at": data.get("generated_at"),
        "dir": str(job_dir),
        "out_dir": str(job_dir / "out"),
        "api_public": data.get("api") or {},
        "chapters": chapters,
        "results": results,
        "progress": {"current": len(results), "total": len(results)},
        "error": "；".join(errors)[:1200],
        "warnings": [],
        "log": str(job_dir / "run.log"),
    }


def _write_manifest(job: dict[str, Any], textbook: Any, results: list[ChapterResult], settings: Any, client: LLMClient) -> None:
    summary = {
        "total": len(results),
        "ok": sum(1 for r in results if r.ok),
        "quality_failed": sum(1 for r in results if r.status == "quality_failed"),
        "failed": sum(1 for r in results if r.status == "failed"),
    }
    data = {
        "generated_at": now_iso(),
        "tool": "jiaoan-web",
        "textbook": {"path": "textbook.md", "title": textbook.title, "chapters": len(textbook.chapters)},
        "model": "offline" if settings.offline else settings.model,
        "api": job.get("api_public") or {},
        "offline": bool(settings.offline),
        "summary": summary,
        "tokens": client.stats_summary(),
        "chapters": [r.to_dict() for r in results],
    }
    write_text(Path(job["out_dir"]) / "manifest.json", json.dumps(data, ensure_ascii=False, indent=2))


def _zip_output(job: dict[str, Any]) -> Path:
    out_dir = Path(job["out_dir"])
    zip_path = Path(job["dir"]) / "全部附件_可选.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(out_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(out_dir).as_posix())
    return zip_path


def _run_job(job_id: str) -> None:
    job = JOBS.get(job_id)
    if not job:
        return
    _update(job_id, status="running", started_at=now_iso())
    secret = ""
    try:
        offline = bool(job.get("offline"))
        if offline:
            settings = settings_from_env({"offline": True})
            _update(job_id, api_public={"label": "离线演示", "base_url": "", "model": "offline"})
        else:
            api = job.get("api") or {}
            if api:
                settings = _settings_for_api(api)
                _update(job_id, api_public=_public_api(api))
            else:
                settings = settings_from_env({"offline": False})
                _update(
                    job_id,
                    api_public={
                        "label": "服务器 .env",
                        "base_url": settings.base_url,
                        "model": settings.model,
                    },
                )
        # API Key 只用于构造 client；不要留在 job 里，也不要写盘/日志
        secret = settings.api_key
        with JOBS_LOCK:
            if "api" in job:
                job["api"] = {"scrubbed": True}

        if not settings.api_key and not settings.offline:
            raise RuntimeError("没有可用的 API Key；请在页面填写 API Key，或勾选离线演示。")

        console = Console(Path(job["dir"]) / "run.log", verbose=False, stream=io.StringIO())
        client = LLMClient(settings, console)
        pipeline = LessonPipeline(settings, console, client)

        textbook = parse_textbook(Path(job["dir"]) / "input" / "textbook.md")
        chapters = textbook.select(job.get("chapters") or "all")
        if not chapters:
            raise RuntimeError("没有选中任何章节。")
        _update(job_id, progress={"current": 0, "total": len(chapters)}, chapters=[
            {"id": c.id, "title": c.title, "status": "pending"} for c in chapters
        ])

        results: list[ChapterResult] = []
        chapter_states = {
            c.id: {
                "id": c.id,
                "title": c.title,
                "status": "pending",
                "docx": "",
                "error": "",
                "stage": "",
                "warnings": [],
                "validation": {},
                "acceptance": {},
                "output_dir": "",
                "image_summary": {},
                "image_report": "",
                "image_report_url": "",
                "docx_files": [],
            }
            for c in chapters
        }
        for idx, chapter in enumerate(chapters, start=1):
            _update(job_id, progress={"current": idx, "total": len(chapters), "title": chapter.title})
            chapter_states[chapter.id]["status"] = "running"
            chapter_states[chapter.id]["stage"] = "解析模板 / 生成教学方案"
            _update(job_id, chapters=list(chapter_states.values()))
            brief = merge_brief(job.get("brief") or {}, chapter)
            template_arg = job.get("template_arg")
            try:
                result = pipeline.run_chapter(
                    chapter,
                    brief,
                    template_arg,
                    job["out_dir"],
                    offline=offline,
                    enable_review=bool(job.get("review")),
                    image_dir=job.get("image_dir") or None,
                    image_hints=job.get("image_hints") or None,
                    include_unassigned_images=(idx == 1 or len(chapters) == 1),
                    filter_images=bool(job.get("filter_images", True)),
                    keep_all_images=bool(job.get("keep_all_images", False)),
                )
            except Exception as exc:
                result = ChapterResult(chapter_id=chapter.id, title=chapter.title, status="failed", error=f"{type(exc).__name__}: {exc}")
            results.append(result)
            chapter_states[chapter.id].update(
                {
                    "status": result.status,
                    "docx": result.files.get("docx", ""),
                    "error": result.error,
                    "stage": "",
                    "warnings": list(result.warnings),
                    "validation": result.validation,
                    "acceptance": result.acceptance,
                    "output_dir": result.output_dir,
                    "image_summary": result.image_summary,
                    "image_report": result.files.get("image_report", ""),
                    "image_report_url": (
                        f"/api/jobs/{job_id}/image-report/{idx}"
                        if result.files.get("image_report")
                        else ""
                    ),
                    "docx_files": result.docx_files,
                }
            )
            _update(job_id, chapters=list(chapter_states.values()))

        _write_manifest(job, textbook, results, settings, client)
        ok = all(r.ok for r in results)
        if ok:
            final_status = "succeeded"
        elif any(r.status in {"ok", "ok_with_warnings"} for r in results):
            final_status = "partial_failed"
        elif any(r.status == "quality_failed" for r in results):
            final_status = "quality_failed"
        else:
            final_status = "failed"
        failed = [r for r in results if not r.ok]
        summary = ""
        if failed:
            summary = "；".join(f"{r.title}：{(r.error or '质检未通过')[:300]}" for r in failed)
        _update(
            job_id,
            status=final_status,
            error=summary,
            results=[r.to_dict() for r in results],
            finished_at=now_iso(),
        )
    except Exception as exc:
        detail = traceback.format_exc()
        try:
            with (Path(job["dir"]) / "run.log").open("a", encoding="utf-8") as fh:
                fh.write("\n[FATAL] 任务级异常：\n" + detail + "\n")
        except Exception:
            pass
        _update(
            job_id,
            status="failed",
            error=_redact(f"{type(exc).__name__}: {exc}\n请点“查看本次完整运行日志”查看详细堆栈。", secret),
            finished_at=now_iso(),
        )
    finally:
        # 再次确保 Key 不残留
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job:
                job.pop("api", None)


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>教案工坊 · 教师上传端</title>
<style>
:root{--bg:#f5f6f8;--card:#fff;--ink:#1f2937;--muted:#6b7280;--line:#e5e7eb;--accent:#c2410c;--accent2:#ea580c;--ok:#0f766e;--warn:#b45309;--err:#b91c1c;--blue:#1d4ed8}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}
header{padding:28px 32px 18px;border-bottom:1px solid var(--line);background:#fff}
h1{margin:0 0 4px;font-size:24px;letter-spacing:.5px}
.sub{color:var(--muted)}
.wrap{max-width:1180px;margin:24px auto 60px;padding:0 24px;display:grid;grid-template-columns:minmax(0,1fr) 380px;gap:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px;box-shadow:0 2px 10px rgba(15,23,42,.04)}
h2{font-size:16px;margin:0 0 14px}
label{display:block;font-weight:600;margin:14px 0 6px}
.hint{color:var(--muted);font-size:12px;font-weight:400;margin-left:6px}
input[type=text],input[type=number],input[type=password],select{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:9px;background:#fafafa;font:inherit;outline:none}
input:focus,select:focus{border-color:#fdba74;background:#fff}
input:disabled,select:disabled,button:disabled{opacity:.55;cursor:not-allowed}
.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.drop{border:1.5px dashed #d1d5db;border-radius:12px;padding:18px;background:#fafafa;text-align:center;cursor:pointer;transition:.15s}
.drop:hover{border-color:var(--accent2);background:#fff7ed}
.drop.dragover{border-color:var(--accent);background:#ffedd5}
.drop .big{font-size:15px;font-weight:600;margin-bottom:4px}
.file-list{margin-top:8px;font-size:12px;color:var(--muted);text-align:left;word-break:break-all}
.image-list{margin-top:8px}
.image-item{display:flex;gap:8px;align-items:center;margin:6px 0}
.image-item .name{font-size:12px;color:var(--muted);width:38%;word-break:break-all}
.image-item input{flex:1;padding:6px 9px;font-size:12px;border:1px solid var(--line);border-radius:7px;background:#fff}
button{width:100%;margin-top:20px;padding:12px 16px;border:0;border-radius:10px;background:linear-gradient(135deg,var(--accent),var(--accent2));color:#fff;font-size:15px;font-weight:700;cursor:pointer}
button.secondary{background:#fff;color:var(--ink);border:1px solid var(--line)}
button.mini{width:auto;margin:0;padding:8px 12px;font-size:12px;border-radius:8px;font-weight:600}
.api-box{border:1px solid var(--line);border-radius:12px;padding:14px;background:#fafafa;margin:6px 0 10px}
.api-box label{margin-top:8px}
.api-status{font-size:12px;min-height:18px;margin-top:8px}
.api-status.ok{color:var(--ok)}.api-status.err{color:var(--err)}.api-status.wait{color:var(--muted)}
.inline-actions{display:flex;align-items:center;gap:10px;margin-top:8px;flex-wrap:wrap}
.model-row{display:flex;gap:8px;align-items:stretch}
.model-row input{flex:1}
.model-row button{flex:0 0 auto;margin:0;padding:0 14px}
.key-row{display:flex;gap:8px;align-items:stretch}
.key-row input{flex:1}
.key-row button{flex:0 0 auto;margin:0;padding:0 12px;background:#fff;color:var(--ink);border:1px solid var(--line)}
.api-note{font-size:11px;color:var(--muted);line-height:1.7;margin-top:10px}
.notice{padding:10px 12px;border-radius:9px;background:#fff7ed;color:#9a3412;font-size:12px;margin-top:12px;display:none}
.err{color:var(--err);font-size:12px;margin-top:8px}
.status{font-size:13px;color:var(--muted);min-height:22px}
.progress{height:8px;background:#eef2f7;border-radius:99px;overflow:hidden;margin:10px 0}
.progress>i{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--accent),#f59e0b);transition:width .3s}
.chapter{border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin:8px 0;font-size:13px}
.chapter-head{display:flex;justify-content:space-between;gap:8px;align-items:center}
.chapter-title{font-weight:600;word-break:break-word}
.chapter-reason{margin-top:6px;color:var(--muted);font-size:12px;white-space:pre-wrap;word-break:break-word}
.chapter-reason.err{color:var(--err)}
.chapter details{margin-top:7px;font-size:12px}
.chapter summary{cursor:pointer;color:var(--blue);font-weight:600}
.diag-block{white-space:pre-wrap;word-break:break-word;background:#fafafa;border:1px solid var(--line);border-radius:8px;padding:8px 10px;margin-top:6px;max-height:260px;overflow:auto;line-height:1.6}
.diag-block ol{margin:4px 0 0 18px;padding:0}
.diag-block li{margin:2px 0}
.log-link{display:inline-block;margin-top:8px;font-size:12px;color:var(--blue);text-decoration:none}
.log-link:hover{text-decoration:underline}
.tag.quality_failed{background:#fef3c7;color:#b45309}.tag.failed{background:#fee2e2;color:#b91c1c}.tag.partial_failed{background:#fef3c7;color:#b45309}
.tag{padding:2px 8px;border-radius:99px;font-size:11px;white-space:nowrap}
.tag.pending{background:#f3f4f6;color:#6b7280}.tag.running{background:#dbeafe;color:#1d4ed8}.tag.ok,.tag.ok_with_warnings{background:#ccfbf1;color:#0f766e}.tag.quality_failed{background:#fef3c7;color:#b45309}.tag.failed{background:#fee2e2;color:#b91c1c}
.actions{display:flex;gap:10px;margin-top:14px}
.actions a{flex:1;text-align:center;text-decoration:none;padding:10px;border-radius:9px;font-weight:700;font-size:13px}
.docx-links{display:flex;flex-direction:column;gap:8px;width:100%}
.docx-links a{width:100%}
a.primary{background:var(--accent);color:#fff}
a.ghost{border:1px solid var(--line);color:var(--ink);background:#fff}
pre{white-space:pre-wrap;word-break:break-word;background:#0f172a;color:#e2e8f0;border-radius:10px;padding:14px;max-height:520px;overflow:auto;font-size:12px;line-height:1.7}
@media(max-width:900px){.wrap{grid-template-columns:1fr}header{padding:20px}}
</style>
</head>
<body>
<header>
  <h1>教案工坊 <span style="font-size:13px;color:#9ca3af;font-weight:400">teacher upload</span></h1>
  <div class="sub">上传教材全本 Markdown 与每章教案模板，选择模型 API，生成可交付的教案（DOCX / Markdown / 结构化 JSON）。</div>
</header>
<div class="wrap">
  <section class="card">
    <h2>1. 模型 API</h2>
    <label>接口来源</label>
    <select id="apiPreset">
      <option value="local">本机 Ollama（qwen2.5:7b，免 Key）</option>
      <option value="deepseek">DeepSeek 官方（api.deepseek.com）</option>
      <option value="school">学校 / 自建 DeepSeek（OpenAI 兼容）</option>
      <option value="custom">自定义 OpenAI 兼容接口</option>
    </select>
    <div class="api-box">
      <label>Base URL <span class="hint">通常以 /v1 结尾；也可填完整 /chat/completions 地址</span></label>
      <input type="text" id="apiBase" placeholder="https://your-school.example.com/v1" autocomplete="off"/>
      <label>API Key <span class="hint">只用于本次生成，不写入任务文件/日志</span></label>
      <div class="key-row">
        <input type="password" id="apiKey" placeholder="sk-..." autocomplete="off"/>
        <button class="mini" id="toggleKey" type="button">显示</button>
      </div>
      <label>模型名</label>
      <div class="model-row">
        <input type="text" id="apiModel" list="modelList" placeholder="deepseek-chat" autocomplete="off"/>
        <button class="mini secondary" id="fetchModels" type="button">获取模型</button>
      </div>
      <datalist id="modelList"></datalist>
      <div class="row" style="margin-top:8px">
        <div>
          <label style="margin-top:0">最大输出 token <span class="hint">思考模型建议 8192</span></label>
          <input type="number" id="maxTokens" value="8192" min="1024" max="32768" step="512"/>
        </div>
        <div>
          <label style="margin-top:0">连接超时（秒）</label>
          <input type="number" id="apiTimeout" value="900" min="60" max="3600" step="30"/>
        </div>
      </div>
      <label style="display:flex;align-items:center;gap:8px;margin-top:8px;font-weight:400;font-size:12px;color:#6b7280">
        <input type="checkbox" id="disableThinking" checked style="width:auto"/>
        关闭思考模式（Qwen3 / R1 等推理模型建议勾选；普通模型不支持时会自动降级）
      </label>
      <div class="inline-actions">
        <button class="mini secondary" id="testApi" type="button">测试连接</button>
        <span class="api-status" id="apiStatus">尚未测试</span>
      </div>
      <div class="api-note">
        学校接口请确认是 OpenAI 兼容格式（Bearer Key + <code>/chat/completions</code>）。
        Key 只在服务端内存中用于本次任务；默认不保存到浏览器，勾选下方可记住。
        本页面默认仅监听 127.0.0.1，请勿直接暴露公网。
      </div>
      <label style="display:flex;align-items:center;gap:8px;margin-top:10px;font-weight:400;font-size:12px;color:#6b7280">
        <input type="checkbox" id="rememberKey" style="width:auto"/> 在本机浏览器记住 Key（公用电脑请勿勾选）
      </label>
    </div>

    <h2 style="margin-top:22px">2. 上传与配置</h2>
    <label>教材全本 Markdown <span class="hint">.md / .txt，需保留章/节标题</span></label>
    <div class="drop" id="dropText">
      <div class="big">点击选择，或拖拽文件到这里</div>
      <div style="color:#6b7280;font-size:12px">推荐：# 第一章 … / ## 1.1 …</div>
      <div class="file-list" id="textFile">未选择</div>
    </div>
    <input type="file" id="textInput" accept=".md,.markdown,.txt" hidden/>

    <label>教案模板 <span class="hint">可多选；建议命名为 ch01.docx / ch02.docx / default.docx</span></label>
    <div class="drop" id="dropTpl">
      <div class="big">点击选择模板，或拖拽多个文件</div>
      <div style="color:#6b7280;font-size:12px">.docx / .md，留空则使用内置版式</div>
      <div class="file-list" id="tplFile">未选择</div>
    </div>
    <input type="file" id="tplInput" accept=".docx,.md,.markdown" multiple hidden/>
    <div class="status" id="tplInfo" style="font-size:12px;margin-top:4px">上传模板后会自动读取学科、年级、课时时长、章内课时数</div>

    <label>教材配图 <span class="hint">可多选；会 OCR 识别并自动放到对应教学过程</span></label>
    <div class="drop" id="dropImages">
      <div class="big">点击选择教材截图/配图，或拖拽多个图片</div>
      <div style="color:#6b7280;font-size:12px">.png / .jpg / .jpeg / .webp；可给每张图写图注/章节提示</div>
      <div class="file-list" id="imageFile">未选择</div>
    </div>
    <input type="file" id="imageInput" accept=".png,.jpg,.jpeg,.webp,.bmp" multiple hidden/>
    <div class="image-list" id="imageList"></div>
    <label style="display:flex;align-items:center;gap:8px;margin-top:8px;font-weight:400;font-size:12px;color:#6b7280">
      <input type="checkbox" id="filterImages" checked style="width:auto"/>
      智能筛选教材图：自动剔除二维码、源代码截图、重复图和空白图，并生成筛选说明（推荐）
    </label>

    <div class="row">
      <div><label>学科</label><input type="text" id="subject" value="信息科技"/></div>
      <div><label>年级</label><input type="text" id="grade" value="八年级"/></div>
    </div>
    <div class="row">
      <div><label>每课时时长（分钟） <span class="hint">通常 40-45；连堂填 90，不是总时长</span></label><input type="number" id="duration" value="45" min="20" max="120"/></div>
      <div><label>章内课时数 <span class="hint">自动读模板；留空按教材篇幅拆分</span></label><input type="number" id="periods" min="1" max="8" placeholder="自动"/></div>
    </div>
    <div class="row">
      <div><label>课型</label><input type="text" id="lessonType" value="新授课"/></div>
      <div><label>章节范围 <span class="hint">all / ch01 / 1,2</span></label><input type="text" id="chapters" value="all"/></div>
    </div>
    <label>学情（可选）</label>
    <input type="text" id="students" placeholder="例：学生用过语音助手，但说不清机器学习过程"/>
    <label style="display:flex;align-items:center;gap:8px;margin-top:16px">
      <input type="checkbox" id="review" style="width:auto"/> 启用教研复核（多一次模型调用）
    </label>
    <label style="display:flex;align-items:center;gap:8px;margin-top:10px">
      <input type="checkbox" id="offline" style="width:auto"/> 离线演示（不调用大模型，只生成规则骨架）
    </label>
    <div class="notice" id="notice"></div>
    <div class="err" id="err"></div>
    <button id="go">生成教案</button>
  </section>

  <section class="card">
    <h2>3. 生成进度</h2>
    <div class="status" id="status">等待上传</div>
    <div class="status" id="jobApi" style="font-size:12px"></div>
    <div class="progress"><i id="bar"></i></div>
    <div id="chapterList"></div>
    <div id="jobDiag"></div>
    <a class="log-link" id="logLink" href="#" target="_blank" rel="noopener" style="display:none">查看本次完整运行日志 →</a>
    <div class="actions" id="actions" style="display:none;flex-direction:column">
      <div class="docx-links" id="docxLinks"></div>
      <a class="ghost" id="previewBtn" href="#">预览教案</a>
    </div>
    <pre id="preview" style="display:none"></pre>
    <div class="err" id="jobErr" style="white-space:pre-wrap"></div>
  </section>
</div>
<script>
const $ = (id) => document.getElementById(id);
const PRESETS = {
  local: {label:'本机 Ollama', base_url:'http://127.0.0.1:11435/v1', api_key:'ollama', model:'qwen2.5:7b'},
  deepseek: {label:'DeepSeek 官方', base_url:'https://api.deepseek.com/v1', api_key:'', model:'deepseek-chat'},
  school: {label:'学校 DeepSeek', base_url:'', api_key:'', model:'deepseek-chat'},
  custom: {label:'自定义 API', base_url:'', api_key:'', model:''}
};
let textFile = null, tplFiles = [], imageFiles = [], imageCaptions = {}, pollTimer = null, currentJob = null;

function saveApiPrefs(){
  try{
    const data = {
      preset: $('apiPreset').value,
      base_url: $('apiBase').value,
      model: $('apiModel').value,
      max_tokens: $('maxTokens').value,
      timeout: $('apiTimeout').value,
      disable_thinking: $('disableThinking').checked,
      api_key: $('rememberKey').checked ? $('apiKey').value : ''
    };
    localStorage.setItem('jiaoan_api', JSON.stringify(data));
  }catch(e){}
}
function restoreApiPrefs(){
  try{
    const s = JSON.parse(localStorage.getItem('jiaoan_api') || 'null');
    if(!s || !s.base_url) return false;
    if(PRESETS[s.preset]) $('apiPreset').value = s.preset;
    $('apiBase').value = s.base_url || '';
    $('apiModel').value = s.model || '';
    $('apiKey').value = s.api_key || (s.preset === 'local' ? 'ollama' : '');
    $('maxTokens').value = s.max_tokens || '8192';
    $('apiTimeout').value = s.timeout || '900';
    $('disableThinking').checked = s.disable_thinking !== false;
    $('rememberKey').checked = !!s.api_key;
    return true;
  }catch(e){ return false; }
}

function fmtSize(n){ return n > 1024*1024 ? (n/1024/1024).toFixed(1)+' MB' : Math.ceil(n/1024)+' KB'; }
function bindDrop(drop, input, onFiles){
  drop.addEventListener('click', ()=>input.click());
  drop.addEventListener('dragover', e=>{e.preventDefault();drop.classList.add('dragover')});
  drop.addEventListener('dragleave', ()=>drop.classList.remove('dragover'));
  drop.addEventListener('drop', e=>{e.preventDefault();drop.classList.remove('dragover');onFiles([...e.dataTransfer.files]);});
  input.addEventListener('change', ()=>onFiles([...input.files]));
}
bindDrop($('dropText'), $('textInput'), files=>{
  if(!files.length) return; textFile = files[0];
  $('textFile').textContent = textFile.name + ' · ' + fmtSize(textFile.size);
});
bindDrop($('dropTpl'), $('tplInput'), files=>{
  tplFiles = files.filter(f=>/\.(docx|md|markdown)$/i.test(f.name));
  $('tplFile').textContent = tplFiles.length ? tplFiles.map(f=>f.name).join('、') : '未选择';
  if(tplFiles.length) readTemplateInfo(tplFiles[0]);
});
async function readTemplateInfo(file){
  if(!file) return;
  $('tplInfo').textContent = '正在读取模板信息…';
  try{
    const data = await postJson('/api/template-info', {name:file.name, base64:await toB64(file)});
    const info = data.info || {};
    if(info.subject) $('subject').value = info.subject;
    if(info.grade) $('grade').value = info.grade;
    if(info.duration_minutes) $('duration').value = info.duration_minutes;
    if(info.periods){
      $('periods').value = info.periods;
    }else{
      $('periods').value = '';
    }
    const parts = [];
    if(info.course_name) parts.push(`课程：${info.course_name}`);
    if(info.grade) parts.push(`对象：${info.grade}`);
    if(info.duration_minutes) parts.push(`单课时：${info.duration_minutes} 分钟`);
    if(info.periods) parts.push(`章内课时数：${info.periods}`);
    else parts.push('章内课时数：模板未写，按教材篇幅自动拆分');
    $('tplInfo').textContent = '已从模板读取 · ' + parts.join('｜');
  }catch(e){
    $('tplInfo').textContent = '模板信息读取失败：' + (e.message || e);
  }
}
bindDrop($('dropImages'), $('imageInput'), files=>{
  imageFiles = files.filter(f=>/\.(png|jpe?g|webp|bmp)$/i.test(f.name)).slice(0, 30);
  $('imageFile').textContent = imageFiles.length ? imageFiles.map(f=>f.name).join('、') : '未选择';
  renderImageList();
});
function renderImageList(){
  const box = $('imageList'); box.innerHTML = '';
  imageFiles.forEach((f, idx)=>{
    const row = document.createElement('div'); row.className = 'image-item';
    const name = document.createElement('span'); name.className='name'; name.textContent = f.name;
    const input = document.createElement('input');
    input.type = 'text';
    input.placeholder = '图注/章节提示（可留空，如：1.2 图灵测试示意）';
    input.value = imageCaptions[f.name] || '';
    input.addEventListener('input', ()=>{ imageCaptions[f.name] = input.value; });
    row.appendChild(name); row.appendChild(input); box.appendChild(row);
  });
}

function toB64(file){
  return new Promise((resolve,reject)=>{
    const r = new FileReader();
    r.onload = ()=>resolve(String(r.result).split(',')[1] || '');
    r.onerror = reject;
    r.readAsDataURL(file);
  });
}
function setStatus(s){ $('status').textContent = s; }
function setApiStatus(text, cls='wait'){
  const el=$('apiStatus'); el.textContent=text; el.className='api-status ' + cls;
}
function tag(status){
  const map = {pending:'等待',running:'生成中',ok:'通过',ok_with_warnings:'通过(建议)',quality_failed:'质检未过',failed:'失败',dry_run:'预览'};
  return `<span class="tag ${status}">${map[status]||status}</span>`;
}
function esc(value){
  return String(value == null ? '' : value)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}
function chapterDiagnostics(c){
  const parts = [];
  if(c.error) parts.push(`<div class="diag-block"><strong>失败原因</strong>\n${esc(c.error)}</div>`);
  const warnings = (c.warnings || []).filter(Boolean);
  if(warnings.length){
    const items = warnings.slice(0,12).map(w=>`<li>${esc(w)}</li>`).join('');
    parts.push(`<div class="diag-block"><strong>警告（${warnings.length} 条）</strong><ol>${items}</ol>${warnings.length>12?'<div>……仅显示前 12 条，完整内容见运行日志</div>':''}</div>`);
  }
  const issues = ((c.validation || {}).issues || []);
  if(issues.length){
    const items = issues.slice(0,20).map(i=>{
      const level = i.severity === 'error' ? '错误' : '建议';
      const fix = i.fix ? `；建议：${esc(i.fix)}` : '';
      return `<li><b>[${level}]</b> ${esc(i.location || '')}：${esc(i.message || '')}${fix}</li>`;
    }).join('');
    parts.push(`<div class="diag-block"><strong>质检问题（${issues.length} 条）</strong><ol>${items}</ol>${issues.length>20?'<div>……仅显示前 20 条，完整内容见“质检报告.md”</div>':''}</div>`);
  }
  const checks = ((c.acceptance || {}).checks || []).filter(x=>x && x.passed === false);
  if(checks.length){
    const items = checks.map(x=>`<li>${esc(x.name || '')}：${esc(x.detail || '')}</li>`).join('');
    parts.push(`<div class="diag-block"><strong>Word 验收未通过</strong><ol>${items}</ol></div>`);
  }
  const im = c.image_summary || {};
  if(im.total){
    const cats = Object.entries(im.rejected_by_category || {}).map(([k,v])=>`${esc(k)} ${v}`).join('、');
    const link = c.image_report_url ? `<div style="margin-top:4px"><a href="${c.image_report_url}" target="_blank" rel="noopener">查看《教材配图筛选说明》</a></div>` : '';
    const used = im.used != null ? `，实际入 Word ${im.used} 张` : '';
    parts.push(`<div class="diag-block"><strong>教材配图筛选</strong>\n扫描 ${im.total} 张，通过质量筛选 ${im.kept} 张，剔除 ${im.rejected} 张${used}${cats?'（'+cats+'）':''}${link}</div>`);
  }
  if(c.output_dir) parts.push(`<div class="diag-block"><strong>中间文件</strong>\n${esc(c.output_dir)}</div>`);
  return parts.join('');
}
function renderJob(j){
  currentJob = j;
  const p = j.progress || {};
  $('bar').style.width = p.total ? Math.round(100*(p.current||0)/p.total)+'%' : '0%';
  if(j.status === 'queued') setStatus('排队中…');
  if(j.status === 'running') setStatus(`生成中：${p.current||0}/${p.total||0} ${p.title ? '· '+p.title : ''}`);
  if(j.status === 'succeeded') setStatus('全部生成完成');
  if(j.status === 'partial_failed') setStatus('部分章节未通过，失败原因已列在下方');
  if(j.status === 'quality_failed') setStatus('Word 已生成，但验收未通过；原因已列在下方');
  if(j.status === 'failed') setStatus('生成失败，原因已列在下方');
  if(j.api && j.api.model){ $('jobApi').textContent = `模型：${j.api.model}｜${j.api.label || ''}｜${j.api.base_url || ''}`; }
  $('jobErr').textContent = j.error || '';
  if(j.has_log && j.log_url){
    $('logLink').style.display = 'inline-block';
    $('logLink').href = j.log_url;
  }else{
    $('logLink').style.display = 'none';
  }
  const box = $('chapterList'); box.innerHTML = '';
  (j.chapters||[]).forEach(c=>{
    const div = document.createElement('div'); div.className='chapter';
    const detail = chapterDiagnostics(c);
    let hint = '';
    if(c.status === 'running' && c.stage) hint = c.stage;
    else if(c.status === 'failed') hint = c.error ? String(c.error).slice(0, 260) : '未生成 Word，展开查看原因。';
    else if(c.status === 'quality_failed') hint = c.error ? String(c.error).slice(0, 260) : 'Word 已生成，但 DOCX 验收未通过；仍可下载后按原因返工。';
    else if((c.warnings||[]).length) hint = `已生成，有 ${c.warnings.length} 条警告。`;
    div.innerHTML = `<div class="chapter-head"><span class="chapter-title">${esc(c.id)} · ${esc(c.title)}</span>${tag(c.status)}</div>` +
      (hint ? `<div class="chapter-reason ${['failed','quality_failed'].includes(c.status)?'err':''}">${esc(hint)}</div>` : '') +
      (c.image_report_url ? `<div class="chapter-reason">教材配图：扫描 ${c.image_summary?.total || 0} 张，通过 ${c.image_summary?.kept || 0} 张，剔除 ${c.image_summary?.rejected || 0} 张${c.image_summary?.used != null ? `，入 Word ${c.image_summary.used} 张` : ''} · <a href="${c.image_report_url}" target="_blank" rel="noopener">查看筛选说明</a></div>` : '') +
      (detail ? `<details${c.status==='failed'?' open':''}><summary>查看原因 / 质检详情</summary>${detail}</details>` : '');
    box.appendChild(div);
  });
  $('jobDiag').innerHTML = '';
  const docs = j.docx_files || [];
  if(docs.length){
    $('actions').style.display='flex';
    const box = $('docxLinks'); box.innerHTML = '';
    docs.forEach(d=>{
      const a = document.createElement('a');
      a.className = 'primary';
      a.href = d.url;
      a.textContent = docs.length > 1 ? `下载 Word：${d.title}` : '下载 Word 教案';
      box.appendChild(a);
    });
    $('previewBtn').style.display = 'inline-block';
    $('previewBtn').onclick = (e)=>{e.preventDefault(); loadPreview(j.id);};
  }else{
    $('actions').style.display='none';
  }
  if(['succeeded','partial_failed','quality_failed','failed'].includes(j.status)) clearInterval(pollTimer);
}
function applyPreset(name){
  const p = PRESETS[name];
  if(!p) return;
  $('apiBase').value = p.base_url;
  $('apiModel').value = p.model;
  $('apiKey').value = (name === 'local') ? 'ollama' : '';
  $('apiTypeLabel') && ($('apiTypeLabel').textContent = p.label);
  setApiStatus(name === 'local' ? '本机接口，无需云端 Key' : '请填写 Base URL 和 Key 后点“测试连接”');
  updateDisabled();
}
function apiPayload(){
  return {
    label: (PRESETS[$('apiPreset').value] || {}).label || '自定义 API',
    base_url: $('apiBase').value.trim(),
    api_key: $('apiKey').value.trim(),
    model: $('apiModel').value.trim(),
    max_tokens: Number($('maxTokens').value) || 8192,
    timeout: Number($('apiTimeout').value) || 900,
    disable_thinking: $('disableThinking').checked
  };
}
function updateDisabled(){
  const off = $('offline').checked;
  ['apiPreset','apiBase','apiKey','apiModel','fetchModels','testApi','toggleKey','rememberKey','maxTokens','apiTimeout','disableThinking'].forEach(id=>{ if($(id)) $(id).disabled = off; });
  $('apiStatus').style.opacity = off ? '.45' : '1';
}
async function postJson(url, data){
  const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
  let body = {};
  try{ body = await r.json(); }catch(e){}
  if(!r.ok) throw new Error(body.error || ('HTTP '+r.status));
  return body;
}
async function fetchModels(){
  const api = apiPayload();
  if(!api.base_url){ setApiStatus('请先填写 Base URL','err'); return; }
  setApiStatus('正在获取模型…','wait');
  try{
    const data = await postJson('/api/models', {base_url:api.base_url, api_key:api.api_key});
    const list = data.models || [];
    const dl = $('modelList'); dl.innerHTML = '';
    list.forEach(m=>{ const o=document.createElement('option'); o.value=m; dl.appendChild(o); });
    if(list.length && !api.model){ $('apiModel').value = list[0]; }
    if(list.length){ setApiStatus(`找到 ${list.length} 个模型，可下拉选择`,'ok'); }
    else { setApiStatus(data.error || '接口未返回模型列表，请手动填写模型名','err'); }
    saveApiPrefs();
  }catch(e){ setApiStatus(e.message || String(e),'err'); }
}
async function testApi(){
  const api = apiPayload();
  if(!api.base_url || !api.model){ setApiStatus('请先填写 Base URL 和模型名','err'); return; }
  setApiStatus('正在测试（可能要加载本地模型）…','wait');
  try{
    const data = await postJson('/api/test', api);
    setApiStatus(`连接成功 · ${data.model} · ${data.latency_ms}ms · 回复：${(data.reply||'').slice(0,20)}`,'ok');
    saveApiPrefs();
  }catch(e){ setApiStatus(`连接失败：${e.message || e}`,'err'); }
}
$('apiPreset').onchange = ()=>applyPreset($('apiPreset').value);
$('offline').onchange = updateDisabled;
$('toggleKey').onclick = ()=>{
  const k=$('apiKey'); k.type = k.type === 'password' ? 'text' : 'password';
  $('toggleKey').textContent = k.type === 'password' ? '显示' : '隐藏';
};
$('fetchModels').onclick = fetchModels;
$('testApi').onclick = testApi;

async function poll(id){
  try{
    const r = await fetch(`/api/jobs/${id}`);
    const j = await r.json();
    renderJob(j);
  }catch(e){ setStatus('轮询失败：'+e); }
}
async function loadPreview(id){
  try{
    const r = await fetch(`/api/jobs/${id}/preview`);
    const j = await r.json();
    if(j.markdown){
      $('preview').style.display='block';
      $('preview').textContent = j.markdown.slice(0, 12000);
    } else { $('preview').style.display='block'; $('preview').textContent = j.error || '没有可预览的 Markdown'; }
  }catch(e){ $('preview').style.display='block'; $('preview').textContent = '预览失败：'+e; }
}
$('go').onclick = async ()=>{
  $('err').textContent=''; $('jobErr').textContent=''; $('preview').style.display='none';
  if(!textFile){ $('err').textContent='请先选择教材 Markdown 文件。'; return; }
  const offline = $('offline').checked;
  const api = apiPayload();
  if(!offline){
    if(!api.base_url || !api.model){ $('err').textContent='请填写模型 Base URL 和模型名，或先点“测试连接”。'; return; }
    if(!api.api_key && !/^https?:\/\/(127\.0\.0\.1|localhost)(:|\/)/i.test(api.base_url)){
      $('err').textContent='学校/云端接口需要填写 API Key。'; return;
    }
  }
  $('go').disabled = true; setStatus('上传中…');
  saveApiPrefs();
  try{
    const textbook_b64 = await toB64(textFile);
    const templates = [];
    for(const f of tplFiles){ templates.push({name:f.name, base64: await toB64(f)}); }
    const images = [];
    for(const f of imageFiles){ images.push({name:f.name, base64: await toB64(f), caption: imageCaptions[f.name] || ''}); }
    const payload = {
      textbook_name: textFile.name,
      textbook_base64: textbook_b64,
      templates,
      images,
      subject: $('subject').value.trim(),
      grade: $('grade').value.trim(),
      duration: Number($('duration').value)||45,
      periods: $('periods').value ? Number($('periods').value) : null,
      lesson_type: $('lessonType').value.trim(),
      chapters: $('chapters').value.trim() || 'all',
      student_profile: $('students').value.trim(),
      offline,
      review: $('review').checked,
      filter_images: $('filterImages').checked,
      api: offline ? null : api
    };
    const data = await postJson('/api/generate', payload);
    setStatus('任务已创建'); $('actions').style.display='none';
    pollTimer = setInterval(()=>poll(data.id), 1500);
    poll(data.id);
  }catch(e){
    $('err').textContent = e.message || String(e);
    setStatus('未开始');
  }finally{
    $('go').disabled = false;
  }
};

const restored = restoreApiPrefs();
if(!restored) applyPreset('local'); else updateDisabled();
fetch('/api/config').then(r=>r.json()).then(c=>{
  const localOk = c.local && c.local.available;
  const isLocal = $('apiPreset').value === 'local';
  if(localOk && isLocal){
    setApiStatus('本地模型服务可用；也可以切换到学校 DeepSeek','ok');
  }else if(restored && !isLocal){
    setApiStatus('已恢复上次接口配置，可点“测试连接”','wait');
  }else if(!localOk && isLocal){
    const n=$('notice'); n.style.display='block';
    n.textContent='没有检测到本机 11435 模型服务。可以运行 scripts\\start_ollama_16k.cmd，或选择“学校 / 自建 DeepSeek”并填写 API。';
    setApiStatus('本地服务未启动，请启动 Ollama 或改用学校 API','err');
  }else{
    setApiStatus('请填写学校 API 后点“测试连接”','wait');
  }
}).catch(()=>{});
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "JiaoanWeb/0.2"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _send(self, code: int, body: bytes, content_type: str = "application/json; charset=utf-8", extra: dict[str, str] | None = None) -> None:
        # 浏览器刷新/关闭页面/取消轮询时，客户端会主动断开连接；
        # Windows 上表现为 ConnectionAbortedError [WinError 10053]。
        # 这是正常的 HTTP 客户端行为，不应打印整段 traceback 吓到使用者。
        try:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionError, BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
            self.close_connection = True
        except OSError as exc:
            # WinError 10053/10054 有时会以裸 OSError 形式抛出
            if getattr(exc, "winerror", None) in {10053, 10054, 10038}:
                self.close_connection = True
            else:
                raise

    def _json(self, code: int, data: Any) -> None:
        self._send(code, _json_bytes(data))

    def finish(self) -> None:
        try:
            super().finish()
        except (ConnectionError, TimeoutError):
            pass
        except OSError as exc:
            if getattr(exc, "winerror", None) not in {10053, 10054, 10038}:
                raise

    def _find_job(self, job_id: str) -> dict[str, Any] | None:
        job = JOBS.get(job_id)
        if job:
            return job
        root = Path(getattr(self.server, "out_root", "web_jobs"))
        return _job_from_disk(root / job_id, job_id)

    def _read_json(self, limit: int = SMALL_BODY) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > limit:
            raise ValueError("请求体为空或过大")
        raw = self.rfile.read(length)
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return data

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path == "/":
            self._send(200, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/api/config":
            settings = settings_from_env()
            self._json(
                200,
                {
                    "web_version": WEB_VERSION,
                    "has_key": bool(settings.api_key),
                    "model": settings.model,
                    "local": {"base_url": LOCAL_BASE_URL, "model": LOCAL_MODEL, "available": _probe_local()},
                },
            )
            return
        m = re.match(r"^/api/jobs/([a-f0-9]+)$", path)
        if m:
            job = self._find_job(m.group(1))
            if not job:
                self._json(404, {"error": "任务不存在"})
                return
            self._json(200, _job_public(job))
            return
        m = re.match(r"^/api/jobs/([a-f0-9]+)/docx/(\d+)$", path)
        if m:
            job = self._find_job(m.group(1))
            if not job:
                self._json(404, {"error": "任务不存在"})
                return
            idx = int(m.group(2))
            entries = _docx_entries(job)
            if idx < 1 or idx > len(entries):
                self._json(404, {"error": "Word 文件不存在"})
                return
            entry = entries[idx - 1]
            # 从 job 结果里找真实路径（entry 只带序号和标题）
            all_entries = []
            for result in job.get("results") or []:
                for item in result.get("docx_files") or []:
                    if item.get("path") and Path(str(item.get("path"))).is_file():
                        all_entries.append(item)
                if not result.get("docx_files") and (result.get("files") or {}).get("docx"):
                    p = (result.get("files") or {}).get("docx")
                    if p and Path(str(p)).is_file():
                        all_entries.append({"path": p, "title": result.get("title")})
            raw_path = str((all_entries[idx - 1] or {}).get("path") or "")
            docx_path = Path(raw_path)
            if not raw_path or not docx_path.is_file():
                self._json(404, {"error": "Word 文件尚未生成"})
                return
            title = str(entry.get("title") or f"第{idx}章")
            ascii_name = f"jiaoan_{job['id']}_{idx}.docx"
            utf8_name = quote(f"{title}_教案.docx")
            self._send(
                200,
                docx_path.read_bytes(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                {"Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_name}"},
            )
            return
        m = re.match(r"^/api/jobs/([a-f0-9]+)/download$", path)
        if m:
            job = self._find_job(m.group(1))
            if not job or not Path(job.get("out_dir") or "").exists():
                self._json(404, {"error": "文件尚未生成"})
                return
            zip_path = _zip_output(job)
            data = zip_path.read_bytes()
            self._send(
                200,
                data,
                "application/zip",
                {"Content-Disposition": f'attachment; filename="jiaoan_{job["id"]}_attachments.zip"'},
            )
            return
        m = re.match(r"^/api/jobs/([a-f0-9]+)/image-report/(\d+)$", path)
        if m:
            job = self._find_job(m.group(1))
            if not job:
                self._json(404, {"error": "任务不存在"})
                return
            idx = int(m.group(2))
            results = job.get("results") or []
            if idx < 1 or idx > len(results):
                self._json(404, {"error": "配图说明不存在"})
                return
            raw = str((results[idx - 1].get("files") or {}).get("image_report") or "")
            report_path = Path(raw)
            if not raw or not report_path.is_file():
                self._json(404, {"error": "该章节没有配图筛选说明（可能未上传图片）"})
                return
            text = report_path.read_text(encoding="utf-8", errors="replace")
            self._send(200, text.encode("utf-8"), "text/markdown; charset=utf-8")
            return
        m = re.match(r"^/api/jobs/([a-f0-9]+)/log$", path)
        if m:
            job = self._find_job(m.group(1))
            if not job:
                self._json(404, {"error": "任务不存在"})
                return
            log_path = Path(str(job.get("log") or ""))
            if not str(job.get("log") or "") or not log_path.is_file():
                self._send(404, "运行日志尚未生成".encode("utf-8"), "text/plain; charset=utf-8")
                return
            text = log_path.read_text(encoding="utf-8", errors="replace")
            text = re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "sk-***", text)
            text = re.sub(r"Bearer\s+[^\s,;]+", "Bearer ***", text)
            if len(text) > 60000:
                text = "……（前文省略）\n" + text[-60000:]
            self._send(200, text.encode("utf-8"), "text/plain; charset=utf-8")
            return
        m = re.match(r"^/api/jobs/([a-f0-9]+)/preview$", path)
        if m:
            job = self._find_job(m.group(1))
            if not job:
                self._json(404, {"error": "任务不存在"})
                return
            out = Path(job["out_dir"])
            candidates = sorted(out.glob("*/教案.md"))
            if not candidates:
                candidates = sorted(out.glob("*/质检报告.md"))
            if not candidates:
                self._json(404, {"error": "还没有可预览的 Markdown"})
                return
            text = candidates[0].read_text(encoding="utf-8", errors="replace")
            self._json(200, {"markdown": text, "file": candidates[0].name})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path.rstrip("/")
        if path == "/api/models":
            payload: dict[str, Any] = {}
            try:
                payload = self._read_json()
                models = _fetch_models(str(payload.get("base_url") or ""), str(payload.get("api_key") or ""))
                self._json(200, {"models": models})
            except Exception as exc:
                self._json(200, {"models": [], "error": _redact(exc, str(payload.get("api_key") or ""))})
            return
        if path == "/api/test":
            payload: dict[str, Any] = {}
            try:
                payload = self._read_json()
                result = _test_api(payload)
                self._json(200, result)
            except (ValueError, LLMError, RuntimeError) as exc:
                self._json(400, {"error": _redact(exc, str(payload.get("api_key") or ""))[:500]})
            except Exception as exc:
                self._json(400, {"error": _redact(f"{type(exc).__name__}: {exc}", str(payload.get("api_key") or ""))[:500]})
            return
        if path == "/api/template-info":
            try:
                payload = self._read_json()
                self._json(200, _template_info(payload))
            except Exception as exc:
                self._json(400, {"error": f"模板读取失败：{exc}"})
            return
        if path != "/api/generate":
            self._json(404, {"error": "not found"})
            return

        try:
            payload = self._read_json(MAX_BODY)
        except Exception as exc:
            self._json(400, {"error": f"请求解析失败：{exc}"})
            return

        job_id = uuid.uuid4().hex[:12]
        job_dir = Path(getattr(self.server, "out_root", "web_jobs")) / job_id
        input_dir = job_dir / "input"
        tpl_dir = input_dir / "templates"
        input_dir.mkdir(parents=True, exist_ok=True)
        tpl_dir.mkdir(parents=True, exist_ok=True)

        try:
            textbook_b64 = str(payload.get("textbook_base64") or "")
            if not textbook_b64:
                raise ValueError("缺少教材文件")
            textbook_bytes = base64.b64decode(textbook_b64, validate=True)
            if len(textbook_bytes) > MAX_BODY:
                raise ValueError("教材文件过大")
            (input_dir / "textbook.md").write_bytes(textbook_bytes)

            template_paths: list[Path] = []
            for idx, item in enumerate(payload.get("templates") or []):
                if not isinstance(item, dict):
                    continue
                name = safe_name(str(item.get("name") or f"template_{idx}.docx"), 80)
                suffix = Path(name).suffix.lower()
                if suffix not in {".docx", ".md", ".markdown"}:
                    raise ValueError(f"不支持的模板格式：{name}")
                data = base64.b64decode(str(item.get("base64") or ""), validate=True)
                target = tpl_dir / name
                target.write_bytes(data)
                template_paths.append(target)

            image_dir = input_dir / "images"
            image_dir.mkdir(parents=True, exist_ok=True)
            image_hints: dict[str, str] = {}
            image_count = 0
            for idx, item in enumerate(payload.get("images") or []):
                if not isinstance(item, dict):
                    continue
                raw_name = str(item.get("name") or f"image_{idx}.png")
                name = safe_name(raw_name, 80)
                suffix = Path(name).suffix.lower()
                if suffix not in IMAGE_EXTS:
                    raise ValueError(f"不支持的图片格式：{name}")
                data = base64.b64decode(str(item.get("base64") or ""), validate=True)
                if len(data) > 20 * 1024 * 1024:
                    raise ValueError(f"图片过大：{name}")
                target = image_dir / name
                if target.exists():
                    target = image_dir / f"{Path(name).stem}_{idx}{suffix}"
                target.write_bytes(data)
                caption = str(item.get("caption") or "").strip()
                if caption:
                    image_hints[target.name] = caption
                    image_hints[Path(target.name).stem] = caption
                image_hints.setdefault(raw_name, caption)
                image_count += 1

            brief = {
                "subject": str(payload.get("subject") or "信息科技").strip(),
                "grade": str(payload.get("grade") or "八年级").strip(),
                "duration_minutes": int(payload.get("duration") or 45),
                "lesson_type": str(payload.get("lesson_type") or "新授课").strip(),
                "student_profile": str(payload.get("student_profile") or "").strip(),
            }
            if payload.get("periods"):
                brief["periods"] = int(payload["periods"])
                brief["periods_explicit"] = True
            write_text(input_dir / "brief.json", json.dumps(brief, ensure_ascii=False, indent=2))

            if len(template_paths) == 1:
                template_arg: str | None = str(template_paths[0])
            elif len(template_paths) > 1:
                template_arg = str(tpl_dir)
            else:
                template_arg = None

            offline = bool(payload.get("offline"))
            api_in = payload.get("api") if isinstance(payload.get("api"), dict) else {}
            api: dict[str, Any] = {}
            api_public: dict[str, Any] = {}
            if offline:
                api_public = {"label": "离线演示", "base_url": "", "model": "offline"}
            elif api_in:
                base = _normalize_base_url(str(api_in.get("base_url") or ""))
                model = str(api_in.get("model") or "").strip()
                key = str(api_in.get("api_key") or "").strip()
                if not base or not model:
                    raise ValueError("请填写模型 Base URL 和模型名。")
                parsed = urlparse(base)
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    raise ValueError("Base URL 格式不正确。")
                if not key and not _is_local_base(base):
                    raise ValueError("学校/云端接口需要填写 API Key。")
                api = {
                    "base_url": base,
                    "api_key": key or LOCAL_KEY,
                    "model": model,
                    "label": str(api_in.get("label") or "自定义 API"),
                    "timeout": int(api_in.get("timeout") or 900),
                    "max_tokens": int(api_in.get("max_tokens") or 8192),
                    "max_context_chars": int(api_in.get("max_context_chars") or 12000),
                    "disable_thinking": api_in.get("disable_thinking") if isinstance(api_in.get("disable_thinking"), bool) else None,
                }
                api_public = _public_api(api)

            job = {
                "id": job_id,
                "status": "queued",
                "created_at": now_iso(),
                "dir": str(job_dir),
                "out_dir": str(job_dir / "out"),
                "offline": offline,
                "review": bool(payload.get("review")),
                "brief": brief,
                "chapters": str(payload.get("chapters") or "all").strip() or "all",
                "template_arg": template_arg,
                "image_dir": str(image_dir) if image_count else "",
                "image_hints": image_hints,
                "image_count": image_count,
                "filter_images": bool(payload.get("filter_images", True)),
                "keep_all_images": bool(payload.get("keep_all_images", False)),
                "api": api,
                "api_public": api_public,
                "log": str(job_dir / "run.log"),
                "progress": {"current": 0, "total": 0},
            }
            with JOBS_LOCK:
                JOBS[job_id] = job
            thread = threading.Thread(target=_run_job, args=(job_id,), daemon=True)
            thread.start()
            self._json(200, {"id": job_id})
        except Exception as exc:
            self._json(400, {"error": f"创建任务失败：{exc}"})


class QuietHTTPServer(ThreadingHTTPServer):
    """浏览器刷新/关闭页面时的 10053/10054 不打印 traceback。"""

    def handle_error(self, request: Any, client_address: Any) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError)):
            return
        if isinstance(exc, OSError) and getattr(exc, "winerror", None) in {10038, 10053, 10054}:
            return
        super().handle_error(request, client_address)


def serve(host: str = "127.0.0.1", port: int = 8765, out_root: str = "web_jobs", open_browser: bool = False) -> int:
    root = Path(out_root)
    root.mkdir(parents=True, exist_ok=True)
    server = QuietHTTPServer((host, port), Handler)
    server.out_root = str(root)  # type: ignore[attr-defined]
    url = f"http://{host}:{port}"
    print(f"教案工坊 Web：{url}")
    print(f"任务目录：{root.resolve()}")
    print("按 Ctrl+C 停止。")
    if open_browser:
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()
    return 0
