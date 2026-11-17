"""CourseTransform stage2 网页演示服务。

用法：
    python webapp.py            # 默认 http://localhost:8080
    python webapp.py --port 9000

页面功能：
- 选择教材 md 与模板 docx（默认为仓库自带仿真材料）
- 选择模型：离线演示（MockLLMClient，无需网络）或真实模型（OpenAI 兼容接口，需 .env 中的 Key）
- 点击"开始生成"后实时滚动显示生成日志
- 完成后在线预览教案中间稿与验证报告，并提供教案.docx 等下载链接
"""
import argparse
import json
import logging
import threading
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request, send_file

from src.config import load_config
from src.llm_client import LLMClient, MockLLMClient, load_dotenv
from src.utils import ensure_dir, read_text

ROOT = Path(__file__).resolve().parent
DEMO_MD = ROOT / "demo_materials" / "教材md版" / "md" / "第2章.md"
DEMO_TPL = ROOT / "demo_materials" / "第03学时_GAN生成sin曲线实践_教学设计_模板版.docx"

app = Flask(__name__)

JOBS: dict[str, dict] = {}
JOB_LOCK = threading.Lock()
_active_job_id: str | None = None


class JobLogHandler(logging.Handler):
    """把 course_transform 的日志转发进当前任务缓冲，供页面实时轮询。"""

    def emit(self, record: logging.LogRecord) -> None:
        job_id = _active_job_id
        if job_id and job_id in JOBS:
            try:
                msg = f"[{record.asctime}] [{record.levelname}] {record.getMessage()}"
            except Exception:
                msg = record.getMessage()
            JOBS[job_id]["log"].append(msg)


logger = logging.getLogger("course_transform")
logger.addHandler(JobLogHandler())


def run_job(job_id: str, mode: str, knowledge: str, template: str) -> None:
    global _active_job_id
    job = JOBS[job_id]
    try:
        from src.export_docx import export
        from src.validator import generate_with_validation

        _active_job_id = job_id
        config = load_config(ROOT / "config.yaml")
        if mode == "mock":
            job["log"].append("[页面] 已选择离线演示模式（不访问网络，内容为确定性填充）")
            llm = MockLLMClient(config.get("llm", {}))
        else:
            load_dotenv()
            llm_cfg = config["llm"]
            import os

            if not os.environ.get(llm_cfg.get("api_key_env", "LLM_API_KEY")):
                raise RuntimeError(
                    f"环境变量 {llm_cfg.get('api_key_env')} 未设置："
                    "请在 .env 中配置 API Key，或改用离线演示模式"
                )
            job["log"].append(f"[页面] 真实模型模式：{llm_cfg.get('base_url')} / {llm_cfg.get('model')}")
            llm = LLMClient(llm_cfg)

        out_dir = ensure_dir(ROOT / "output" / f"web_{job_id}")
        knowledge_md = read_text(knowledge)
        job["log"].append(f"[页面] 教材 md：{Path(knowledge).name}（{len(knowledge_md)} 字符）")
        md_path, reports = generate_with_validation(knowledge_md, template, out_dir, llm, config)
        docx_path = export(md_path, template, out_dir / "教案.docx", config)
        job["result"] = {
            "out_dir": str(out_dir),
            "docx": str(docx_path),
            "md": str(md_path),
            "report": str(out_dir / f"validation_report_round{len(reports)-1}.json"),
            "rounds": len(reports),
            "passed": reports[-1]["passed"],
        }
        job["status"] = "done"
    except Exception as exc:  # noqa: BLE001
        job["status"] = "error"
        job["error"] = str(exc)
    finally:
        _active_job_id = None


PAGE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CourseTransform · stage2 教案生成演示</title>
<style>
  :root { --ink:#1f2937; --accent:#2563eb; --bg:#f3f4f6; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:"PingFang SC","Microsoft YaHei",sans-serif; background:var(--bg); color:var(--ink); }
  .wrap { max-width:960px; margin:0 auto; padding:24px 16px 60px; }
  h1 { font-size:22px; margin:0 0 4px; }
  .sub { color:#6b7280; font-size:13px; margin-bottom:20px; }
  .card { background:#fff; border-radius:12px; padding:20px; margin-bottom:16px; box-shadow:0 1px 3px rgba(0,0,0,.08); }
  .card h2 { font-size:15px; margin:0 0 12px; color:#374151; }
  label { display:block; font-size:13px; color:#4b5563; margin:10px 0 4px; }
  input[type=text] { width:100%; padding:9px 10px; border:1px solid #d1d5db; border-radius:8px; font-size:13px; }
  .radios { display:flex; gap:16px; margin-top:6px; }
  .radios label { display:flex; align-items:center; gap:6px; margin:0; font-size:13px; }
  button { margin-top:16px; background:var(--accent); color:#fff; border:none; border-radius:8px; padding:10px 26px; font-size:15px; cursor:pointer; }
  button:disabled { background:#9ca3af; cursor:not-allowed; }
  #log { background:#0f172a; color:#e2e8f0; font-family:Menlo,Consolas,monospace; font-size:12px; line-height:1.6; border-radius:8px; padding:12px; height:220px; overflow-y:auto; white-space:pre-wrap; margin:0; }
  .spinner { display:inline-block; width:14px; height:14px; border:2px solid #cbd5e1; border-top-color:var(--accent); border-radius:50%; animation:spin .8s linear infinite; vertical-align:-2px; }
  @keyframes spin { to { transform:rotate(360deg); } }
  .badge { padding:2px 10px; border-radius:99px; font-size:12px; }
  .pass { background:#dcfce7; color:#166534; } .fail { background:#fee2e2; color:#991b1b; }
  pre.preview { background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px; padding:12px; font-size:12px; max-height:340px; overflow:auto; white-space:pre-wrap; }
  .dl { display:inline-block; margin:4px 8px 0 0; padding:6px 14px; background:#eff6ff; color:var(--accent); border-radius:8px; text-decoration:none; font-size:13px; }
  .err { background:#fef2f2; color:#991b1b; padding:10px 12px; border-radius:8px; font-size:13px; }
  .hidden { display:none; }
</style>
</head>
<body>
<div class="wrap">
  <h1>CourseTransform · stage2 教案生成演示</h1>
  <div class="sub">教材 Markdown + 教学设计模板 docx →（大模型逐块填充 + AI 验证回环）→ 教案 docx</div>

  <div class="card">
    <h2>① 输入材料</h2>
    <label>教材章节 Markdown 路径</label>
    <input type="text" id="knowledge" value="{{ demo_md }}">
    <label>教学设计模板 docx 路径</label>
    <input type="text" id="template" value="{{ demo_tpl }}">
    <label>模型</label>
    <div class="radios">
      <label><input type="radio" name="mode" value="mock" checked> 离线演示（无需网络/Key，内容为确定性填充）</label>
      <label><input type="radio" name="mode" value="real"> 真实模型（DeepSeek，读取 .env 中的 Key）</label>
    </div>
    <button id="btn" onclick="start()">开始生成</button>
    <span id="state" class="hidden"><span class="spinner"></span> <span id="stateText">生成中…</span></span>
  </div>

  <div class="card hidden" id="logCard">
    <h2>② 生成日志（实时）</h2>
    <pre id="log"></pre>
  </div>

  <div class="card hidden" id="resultCard">
    <h2>③ 结果 <span id="verdict"></span></h2>
    <div id="err" class="err hidden"></div>
    <div id="resultBody" class="hidden">
      <p style="font-size:13px;margin:4px 0 10px">验证回环共 <b id="rounds"></b> 轮，最终结果 <span id="badge" class="badge"></span></p>
      <a class="dl" id="dlDocx" href="#">⬇ 下载 教案.docx</a>
      <a class="dl" id="dlMd" href="#">⬇ 教案中间稿.md</a>
      <a class="dl" id="dlReport" href="#">⬇ 验证报告.json</a>
      <h2 style="margin-top:18px">教案中间稿预览</h2>
      <pre class="preview" id="preview"></pre>
    </div>
  </div>
</div>
<script>
let jobId = null, timer = null;
async function start() {
  const btn = document.getElementById('btn');
  btn.disabled = true;
  document.getElementById('state').classList.remove('hidden');
  const body = {
    mode: document.querySelector('input[name=mode]:checked').value,
    knowledge: document.getElementById('knowledge').value.trim(),
    template: document.getElementById('template').value.trim(),
  };
  const resp = await fetch('/api/run', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  const data = await resp.json();
  if (!data.ok) { alert('启动失败：' + data.error); btn.disabled = false; document.getElementById('state').classList.add('hidden'); return; }
  jobId = data.job_id;
  document.getElementById('logCard').classList.remove('hidden');
  document.getElementById('resultCard').classList.add('hidden');
  document.getElementById('log').textContent = '';
  timer = setInterval(poll, 1000);
}
async function poll() {
  const resp = await fetch('/api/status/' + jobId);
  const job = await resp.json();
  const logEl = document.getElementById('log');
  logEl.textContent = job.log.join('\\n');
  logEl.scrollTop = logEl.scrollHeight;
  document.getElementById('stateText').textContent = job.status === 'running' ? '生成中…' : (job.status === 'done' ? '完成' : '出错');
  if (job.status === 'done' || job.status === 'error') {
    clearInterval(timer);
    document.getElementById('btn').disabled = false;
    document.getElementById('state').classList.add('hidden');
    const rc = document.getElementById('resultCard');
    rc.classList.remove('hidden');
    const err = document.getElementById('err');
    if (job.status === 'error') { err.textContent = '生成失败：' + job.error; err.classList.remove('hidden'); return; }
    err.classList.add('hidden');
    document.getElementById('resultBody').classList.remove('hidden');
    document.getElementById('rounds').textContent = job.result.rounds;
    const badge = document.getElementById('badge');
    badge.textContent = job.result.passed ? '验证 PASS' : '验证 FAIL';
    badge.className = 'badge ' + (job.result.passed ? 'pass' : 'fail');
    document.getElementById('dlDocx').href = '/download/' + jobId + '/教案.docx';
    document.getElementById('dlMd').href = '/download/' + jobId + '/教案中间稿.md';
    document.getElementById('dlReport').href = '/download/' + jobId + '/report';
    const preview = document.getElementById('preview');
    const raw = await (await fetch('/download/' + jobId + '/md')).text();
    preview.textContent = raw.split('\\n').filter(l => !l.startsWith('<!-- CTBLOCK')).join('\\n');
    preview.scrollTop = 0;
  }
}
</script>
</body>
</html>"""


@app.get("/")
def index():
    return render_template_string(PAGE, demo_md=str(DEMO_MD), demo_tpl=str(DEMO_TPL))


@app.post("/api/run")
def api_run():
    global _active_job_id
    with JOB_LOCK:
        if _active_job_id is not None:
            return jsonify({"ok": False, "error": "已有任务在运行，请稍候"}), 409
        data = request.get_json(force=True, silent=True) or {}
        mode = data.get("mode", "mock")
        knowledge = data.get("knowledge") or str(DEMO_MD)
        template = data.get("template") or str(DEMO_TPL)
        for label, path in (("教材 md", knowledge), ("模板 docx", template)):
            if not Path(path).exists():
                return jsonify({"ok": False, "error": f"{label}不存在：{path}"}), 400
        import uuid

        job_id = uuid.uuid4().hex[:8]
        JOBS[job_id] = {"status": "running", "log": [], "result": None, "error": None}
        _active_job_id = job_id
        threading.Thread(target=run_job, args=(job_id, mode, knowledge, template), daemon=True).start()
        return jsonify({"ok": True, "job_id": job_id})


@app.get("/api/status/<job_id>")
def api_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "任务不存在"}), 404
    return jsonify({"status": job["status"], "log": job["log"][-200:], "result": job["result"], "error": job["error"]})


@app.get("/download/<job_id>/<kind>")
def download(job_id: str, kind: str):
    job = JOBS.get(job_id)
    if not job or not job.get("result"):
        return "任务不存在或未完成", 404
    r = job["result"]
    mapping = {"docx": r["docx"], "md": r["md"], "report": r["report"]}
    path = mapping.get(kind)
    if not path or not Path(path).exists():
        return "文件不存在", 404
    return send_file(path, as_attachment=(kind == "docx"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CourseTransform stage2 网页演示")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    print(f"演示页面：http://localhost:{args.port}")
    app.run(host="127.0.0.1", port=args.port, threaded=True)
