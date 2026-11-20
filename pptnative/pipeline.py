"""Versioned orchestration of the PPTNative V1.0 architecture."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import shutil
import sys

from . import __version__
from .drawing import catalog
from .models import ask, ensure_profile, parallel, read, save
from .planning import create_plan, intake, recognize, revise_plan, validate_plan
from .quality import REVIEW_VERSION, composite, render


class Cancelled(Exception):
    """Raised cooperatively when the user cancels a long-running task."""


def cancel_path(job):
    return Path(job) / "cancel.requested"


def check_cancel(job):
    if cancel_path(job).exists():
        raise Cancelled("任务已取消")


def clear_cancel(job):
    cancel_path(job).unlink(missing_ok=True)


def event(job, stage, **data):
    record = {"time": datetime.now(timezone.utc).isoformat(), "stage": stage, **data}
    with (job / "events.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(stage, data.get("version", ""), flush=True)


def pid_active(pid):
    if os.name == "nt":
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        try:
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


@contextmanager
def locked(job):
    job.mkdir(parents=True, exist_ok=True)
    lock = job / ".operation.lock"
    if lock.exists():
        if pid_active(int(lock.read_text())):
            raise RuntimeError("This job already has an active operation")
        lock.unlink()
    handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(handle, str(os.getpid()).encode())
    os.close(handle)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def prepare(job, template, outline):
    material = intake(outline, job / "input")
    target = job / "input/template.pptx"
    shutil.copyfile(template, target)
    templates = catalog(target)
    save(job / "template-catalog.json", templates)
    render(target, job / "source-preview")
    profile = ensure_profile(target, job / "source-preview", job / "style")
    save(job / "job.json", {"product": "PPTNative", "version": __version__,
                           "current": None, "history": [], "input_outline": str(outline),
                           "outline_sha256": material["sha256"], "template": str(target)})
    event(job, "conversion-intake", pages=len(material["pages"]))
    event(job, "template-parser", source_pages=len(templates["slides"]))
    event(job, "style-extractor", profile=str(profile))
    return material, templates, read(profile)


def show_review(job, version_id):
    version = job / "versions" / version_id
    manifest, plan = read(version / "version.json"), read(version / "plan.json")
    pages = []
    for number, page in enumerate(plan["pages"], 1):
        image = f"preview/deck_p{number}.png"
        pages.append(f'<section><h2>{number}. {html.escape(page["title"])}</h2>'
                     f'<p>{html.escape(page["id"])}</p><img src="{image}"></section>')
    content = f"""<!doctype html><html lang="zh"><meta charset="utf-8">
<title>PPTNative {version_id}</title><style>
body{{font-family:"Microsoft YaHei",sans-serif;max-width:1100px;margin:28px auto;padding:0 20px;background:#f4f6f8;color:#17212b}}
section{{background:white;padding:16px;margin:20px 0}}img{{width:100%;height:auto}}textarea{{width:100%;min-height:90px}}
button,a{{margin-right:16px}}h2{{font-size:20px}}#status{{white-space:pre-wrap}}
</style><h1>PPTNative V1.0 · {version_id}</h1>
<p>综合质检 · {html.escape(manifest["status"])}。本页对应固定版本。</p>
<a href="deck.pptx">下载此版PPTX</a><a href="qa/summary.json">查看质检报告</a>
<p>通过本地审阅服务提交意见时，填写要修改的页码和具体要求。</p>
<textarea id="feedback" aria-label="修改意见"></textarea><p><button onclick="submitFeedback()">提交修改</button>
<button onclick="confirmVersion()">确认此版</button></p><p id="status"></p>{''.join(pages)}
<script>
async function send(endpoint,data){{
 if(location.protocol==='file:'){{document.querySelector('#status').textContent='请通过 python -m pptnative serve 启动的审阅地址提交修改。';return;}}
 const r=await fetch(endpoint,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(data)}});
 const d=await r.json();document.querySelector('#status').textContent=JSON.stringify(d,null,2);
 if(d.request_id){{const timer=setInterval(async()=>{{const q=await fetch('/requests/'+d.request_id);const s=await q.json();const area=document.querySelector('#status');area.textContent=JSON.stringify(s,null,2);if(s.review_url){{const link=document.createElement('a');link.href=s.review_url;link.textContent='打开修改后的版本';area.appendChild(document.createElement('br'));area.appendChild(link);}}if(['complete','failed'].includes(s.status))clearInterval(timer);}},3000);}}
}}
function submitFeedback(){{send('/feedback',{{base_version:'{version_id}',text:document.querySelector('#feedback').value}});}}
function confirmVersion(){{send('/confirm',{{base_version:'{version_id}'}});}}
</script></html>"""
    (version / "review.html").write_text(content, encoding="utf-8")


def create(job, template, outline):
    from .native import create as native_create
    return native_create(job, template, outline)


def revise(job, text, base_version, simulation=False, allowed_page_ids=None):
    from .native import revise as operation
    return operation(job, text, base_version, simulation, allowed_page_ids=allowed_page_ids)


def simulate(job, kind, base_version=None):
    state = read(job / "job.json")
    selected_version = base_version or state["current"]
    plan = read(job / "versions" / selected_version / "plan.json")
    result = ask(job / "simulation", kind, (
        "你模拟教师对当前成品提出一轮明确修改意见，用于测试PPTNative反馈回路。"
        "按指定kind生成自然中文请求，顶层必须且仅返回feedback、reason两个字符串字段。"
        "不是返回操作JSON。页码只使用输入number，不猜测其他页码。"
        "merge_pages请选择内容可统合的相邻两页（避开封面和结束页）要求合并，明确当前页码。"
        "set_title请选择一张内容页（优先之前合并页），明确当前页码和用引号括起的新标题，"
        "新标题应简明，长度不超过原题，不改任何正文或布局。仅提出所指定的一种操作。"
    ), {"kind": kind, "pages": [{"number": i, "id": p["id"], "title": p["title"],
                                  "origins": p["origins"], "point_ids": p["point_ids"]}
                                 for i, p in enumerate(plan["pages"], 1)]}, required=("feedback", "reason"))
    return result["feedback"], selected_version


def confirm(job, version):
    with locked(job):
        state = read(job / "job.json")
        if state["current"] != version:
            raise ValueError("Only the current reviewed version can be confirmed")
        manifest_path = job / "versions" / version / "version.json"
        manifest = read(manifest_path)
        if not manifest.get("qa_passed"):
            raise ValueError("Composite QA must pass before delivery")
        manifest["status"] = "confirmed"
        save(manifest_path, manifest)
        event(job, "delivery", version=version)


def recheck(job, version_id):
    manifest = read(job / 'versions' / version_id / 'version.json')
    if manifest['status'] == 'confirmed':
        raise ValueError('Confirmed versions cannot be rechecked in place')
    from .native import recheck as operation
    return operation(job, version_id)


def resume(job):
    from .native import resume as operation
    return operation(job)
