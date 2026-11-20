(function(){
  "use strict";

  /* ================= helpers ================= */
  const $ = (sel, root) => (root || document).querySelector(sel);
  const esc = (value) => String(value == null ? "" : value).replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
  const toast = (message) => { if (window.Workbench) window.Workbench.toast(message); };

  async function api(url, method = "GET", body){
    const options = {method, headers:{}};
    if (body !== undefined){ options.headers["Content-Type"] = "application/json"; options.body = JSON.stringify(body); }
    const response = await fetch(url, options);
    let data = {};
    try { data = await response.json(); } catch (error) { /* non JSON */ }
    if (!response.ok) throw Error(data.error || `请求失败 ${response.status}`);
    return data;
  }
  const statusLabel = {
    queued:"排队中", running:"生成中", succeeded:"完成", partial_failed:"部分失败",
    quality_failed:"质检未通过", failed:"失败", interrupted:"已中断", cancelled:"已取消",
    ok:"通过", ok_with_warnings:"通过（有提示）"
  };
  function badgeClass(status){
    if (status === "ok" || status === "ok_with_warnings" || status === "succeeded") return "ok";
    if (status === "quality_failed" || status === "partial_failed" || status === "interrupted") return "warn";
    if (status === "failed") return "err";
    return "";
  }
  function badge(status, text){
    return `<span class="badge ${badgeClass(status)}">${esc(text || statusLabel[status] || status)}</span>`;
  }
  function fmtTime(value){
    if (!value) return "";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? esc(value) : date.toLocaleString("zh-CN", {hour12:false});
  }
  function fileSize(bytes){
    if (!bytes) return "0 B";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / 1024 / 1024).toFixed(1) + " MB";
  }

  const state = {
    pdfFile:null, activePdf:null, pdfTimer:null,
    artifacts:[], selectedArtifacts:new Set(),
    textbookFile:null, templates:[], images:[], imageCaptions:{},
    activeLesson:null, lessonTimer:null,
    apiPreset:"server", initialized:{pdf:false, word:false}
  };

  /* ================= stage switching ================= */
  document.addEventListener("click", (event)=>{
    const go = event.target.closest("[data-goto]");
    if (go && window.Workbench) window.Workbench.showFeature(go.dataset.goto);
  });

  /* ================= PDF → MD ================= */
  const pdfFileInput = $("#pdfFile");
  pdfFileInput.addEventListener("change", ()=>{
    const file = pdfFileInput.files[0];
    if (!file) return;
    if (!/\.pdf$/i.test(file.name)){ toast("请选择 PDF 文件"); pdfFileInput.value=""; return; }
    state.pdfFile = file;
    const picked = $("#pdfPicked");
    picked.hidden = false;
    picked.innerHTML = `<span><b>${esc(file.name)}</b> · ${fileSize(file.size)}</span><button type="button" id="pdfClear" aria-label="移除">✕</button>`;
    $("#pdfClear").addEventListener("click", ()=>{ state.pdfFile=null; pdfFileInput.value=""; picked.hidden=true; picked.innerHTML=""; $("#pdfStart").disabled=true; });
    $("#pdfStart").disabled = false;
    $("#pdfState").textContent = "";
  });

  $("#pdfStart").addEventListener("click", async ()=>{
    if (!state.pdfFile){ toast("先选择教材 PDF"); return; }
    const button = $("#pdfStart");
    button.disabled = true;
    $("#pdfState").textContent = "上传并解析…";
    try {
      const form = new FormData();
      form.append("file", state.pdfFile);
      const response = await fetch("/api/pipeline/pdf/jobs", {method:"POST", body:form});
      const data = await response.json();
      if (!response.ok) throw Error(data.error || "创建任务失败");
      history.replaceState(null, "", `?feature=pdf&pdf=${encodeURIComponent(data.id)}`);
      await refreshPdfJobs();
      await pollPdf(data.id);
    } catch (error) {
      $("#pdfState").textContent = "";
      showPdfError(error.message);
    } finally { button.disabled = false; }
  });

  $("#pdfRefresh").addEventListener("click", ()=>refreshPdfJobs().catch(showPdfError));

  async function refreshPdfJobs(){
    const jobs = await api("/api/pipeline/pdf/jobs");
    const box = $("#pdfJobs");
    if (!jobs.length){
      box.innerHTML = '<div class="empty-ref">还没有解析任务</div>';
      return;
    }
    box.innerHTML = jobs.map(job => `
      <div class="job-card" data-pdf-job="${esc(job.id)}">
        <div class="job-head"><b>${esc(job.fileName || "教材 PDF")}</b>${badge(job.status)}</div>
        <div class="job-meta">${esc(fmtTime(job.created))} · ${fileSize(job.size)}${job.chapters?.length ? " · " + job.chapters.length + " 章" : ""}</div>
        ${job.error ? `<div class="job-meta" style="color:#a13c2a">${esc(job.error)}</div>` : ""}
        <div class="bar-actions">
          <button class="btn btn-sm" data-open-pdf="${esc(job.id)}">查看</button>
          ${["failed","interrupted","cancelled"].includes(job.status) ? `<button class="btn btn-sm" data-retry-pdf="${esc(job.id)}">重试</button>` : ""}
        </div>
      </div>`).join("");
  }
  $("#pdfJobs").addEventListener("click", async (event)=>{
    const open = event.target.closest("[data-open-pdf]");
    const retry = event.target.closest("[data-retry-pdf]");
    try {
      if (open){ history.replaceState(null, "", `?feature=pdf&pdf=${encodeURIComponent(open.dataset.openPdf)}`); await pollPdf(open.dataset.openPdf); }
      if (retry){ await api(`/api/pipeline/pdf/jobs/${retry.dataset.retryPdf}/retry`, "POST", {}); await pollPdf(retry.dataset.retryPdf); await refreshPdfJobs(); }
    } catch (error) { showPdfError(error.message); }
  });

  async function pollPdf(id){
    clearTimeout(state.pdfTimer);
    try {
      const job = await api(`/api/pipeline/pdf/jobs/${id}`);
      state.activePdf = job;
      renderPdfJob(job);
      if (["queued","running"].includes(job.status)){
        $("#pdfState").textContent = job.status === "queued" ? "排队中…" : "解析中…";
        state.pdfTimer = setTimeout(()=>pollPdf(id), 900);
      } else {
        $("#pdfState").textContent = "";
        await refreshPdfJobs();
        if (job.status === "succeeded") toast(`解析完成：${job.chapters.length} 个章节`);
      }
    } catch (error) { showPdfError(error.message); }
  }

  function showPdfError(message){
    const box = $("#pdfResult");
    box.hidden = false;
    $("#pdfResultTitle").textContent = "解析未完成";
    $("#pdfResultMeta").textContent = "";
    $("#pdfChapters").innerHTML = `<div class="notice" style="grid-column:1/-1">${esc(message)}</div>`;
  }

  function renderPdfJob(job){
    const box = $("#pdfResult");
    box.hidden = false;
    const sendAll = $("#pdfSendAll");
    if (sendAll) sendAll.disabled = !(job.chapters && job.chapters.length);
    $("#pdfResultTitle").textContent = job.fileName || "解析结果";
    if (job.status === "failed"){
      showPdfError(job.error || "解析失败");
      return;
    }
    if (["queued","running"].includes(job.status)){
      $("#pdfResultMeta").textContent = "解析中，完成后逐章显示 Markdown 与原图。";
      $("#pdfChapters").innerHTML = '<div class="empty-ref" style="grid-column:1/-1">正在解析…</div>';
      return;
    }
    $("#pdfResultMeta").textContent = `${job.result?.pages ?? ""} 页 · ${job.result?.images ?? 0} 张图片 · 共 ${job.chapters.length} 章 · ${job.sha256 ? "SHA " + job.sha256.slice(0,12) : ""}`;
    $("#pdfChapters").innerHTML = job.chapters.map(chapter => `
      <article class="result-card">
        <h3>${esc(chapter.title)}</h3>
        <div class="rc-meta">第 ${chapter.pages.join(", ")} 页 · ${chapter.chars} 字符 · ${chapter.imageCount} 张图</div>
        <pre data-preview="${chapter.index}" hidden></pre>
        <div class="bar-actions">
          <button class="btn btn-sm" data-pdf-preview="${chapter.index}">预览 MD</button>
          <button class="btn btn-sm" data-pdf-download="${chapter.index}">下载 MD</button>
          ${chapter.imageCount ? `<button class="btn btn-sm" data-pdf-images="${chapter.index}">下载原图</button>` : ""}
          <button class="btn btn-sm btn-accent" data-pdf-handoff="${esc(chapter.artifactId)}">送入教案</button>
        </div>
      </article>`).join("");
  }

  $("#pdfChapters").addEventListener("click", async (event)=>{
    const preview = event.target.closest("[data-pdf-preview]");
    const handoff = event.target.closest("[data-pdf-handoff]");
    const download = event.target.closest("[data-pdf-download]");
    const images = event.target.closest("[data-pdf-images]");
    try {
      if (download && state.activePdf){
        window.location.href = `/api/pipeline/pdf/jobs/${state.activePdf.id}/download/${download.dataset.pdfDownload}`;
      }
      if (images && state.activePdf){
        window.location.href = `/api/pipeline/pdf/jobs/${state.activePdf.id}/images/${images.dataset.pdfImages}.zip`;
      }
      if (preview && state.activePdf){
        const index = preview.dataset.pdfPreview;
        const pre = $(`[data-preview="${index}"]`, $("#pdfChapters"));
        const response = await fetch(`/api/pipeline/pdf/jobs/${state.activePdf.id}/preview/${index}`);
        const text = await response.text();
        pre.hidden = !pre.hidden;
        pre.textContent = text.slice(0, 6000);
      }
      if (handoff){
        const artifactId = handoff.dataset.pdfHandoff;
        state.selectedArtifacts = new Set([artifactId]);
        if (window.Workbench) window.Workbench.showFeature("word");
        toast("已带入 1 章教材，继续选模板与简报");
      }
    } catch (error) { toast(error.message); }
  });

  $("#pdfSendAll").addEventListener("click", ()=>{
    if (!state.activePdf || !state.activePdf.chapters?.length){ toast("还没有可用的章节产物"); return; }
    state.selectedArtifacts = new Set(state.activePdf.chapters.map(item=>item.artifactId));
    if (window.Workbench) window.Workbench.showFeature("word");
    toast(`已带入 ${state.selectedArtifacts.size} 章教材`);
  });

  /* ================= MD → Lesson ================= */
  const presetValues = {
    server:{base:"", model:""},
    deepseek:{base:"https://api.deepseek.com/v1", model:"deepseek-chat"},
    local:{base:"http://127.0.0.1:11435/v1", model:"qwen2.5:7b"},
    custom:{base:"", model:""}
  };
  $("#wordApiPreset").addEventListener("change", ()=>{
    state.apiPreset = $("#wordApiPreset").value;
    const preset = presetValues[state.apiPreset] || presetValues.custom;
    $("#wordBase").value = preset.base;
    $("#wordModel").value = preset.model;
    $("#wordApiStatus").textContent = state.apiPreset === "server" ? "使用服务端 .env 中的 JIAOAN_* 配置（不经过浏览器传 Key）" : "";
  });
  $("#wordKeyToggle").addEventListener("click", ()=>{
    const input = $("#wordKey");
    const visible = input.type === "text";
    input.type = visible ? "password" : "text";
    $("#wordKeyToggle").textContent = visible ? "显示" : "隐藏";
  });

  function apiPayload(){
    if ($("#wordOffline").checked || state.apiPreset === "server") return null;
    const payload = {
      base_url: $("#wordBase").value.trim(),
      api_key: $("#wordKey").value.trim(),
      model: $("#wordModel").value.trim(),
      label: $("#wordApiPreset").selectedOptions[0]?.textContent || "自定义 API"
    };
    if (!payload.base_url || !payload.model) throw Error("请填写 Base URL 和模型名");
    return payload;
  }

  $("#wordModelsBtn").addEventListener("click", async ()=>{
    try {
      const body = {base_url: $("#wordBase").value.trim(), api_key: $("#wordKey").value.trim()};
      if (!body.base_url) throw Error("先填写 Base URL");
      $("#wordApiStatus").textContent = "获取模型中…";
      const data = await api("/api/pipeline/lesson/models", "POST", body);
      const list = $("#wordModels");
      list.innerHTML = (data.models || []).map(name=>`<option value="${esc(name)}"></option>`).join("");
      $("#wordApiStatus").textContent = data.models?.length ? `获取到 ${data.models.length} 个模型` : "接口未返回模型列表，请直接手填模型名";
    } catch (error) { $("#wordApiStatus").textContent = "获取模型失败：" + error.message; }
  });
  $("#wordTestBtn").addEventListener("click", async ()=>{
    try {
      const body = apiPayload();
      if (!body) throw Error("服务器 .env 模式无法在浏览器测试；直接生成即可，日志会显示调用错误");
      $("#wordApiStatus").textContent = "测试连接中…";
      const data = await api("/api/pipeline/lesson/test", "POST", body);
      $("#wordApiStatus").textContent = `连接正常 · ${data.model} · ${data.latency_ms}ms · 回复：${data.reply || "-"}`;
    } catch (error) { $("#wordApiStatus").textContent = "连接失败：" + error.message; }
  });

  $("#wordTextbookFile").addEventListener("change", ()=>{
    const file = $("#wordTextbookFile").files[0];
    if (!file) return;
    state.textbookFile = file;
    const picked = $("#wordTextbookPicked");
    picked.hidden = false;
    picked.innerHTML = `<span><b>${esc(file.name)}</b> · ${fileSize(file.size)}</span><button type="button" id="wordTextbookClear" aria-label="移除">✕</button>`;
    $("#wordTextbookClear").addEventListener("click", ()=>{ state.textbookFile=null; $("#wordTextbookFile").value=""; picked.hidden=true; picked.innerHTML=""; });
  });

  function renderFileList(target, items, removeAttr){
    const box = $(target);
    box.innerHTML = items.map((file, index)=>`<div class="file-row"><span>${esc(file.name)} · ${fileSize(file.size)}</span><button type="button" ${removeAttr}="${index}" aria-label="移除">✕</button></div>`).join("");
  }
  function renderImageList(){
    $("#wordImageList").innerHTML = state.images.map((file, index)=>`<div class="file-row"><span>${esc(file.name)} · ${fileSize(file.size)}</span><input class="control caption-input" data-caption-index="${index}" placeholder="图注（可选）" value="${esc(state.imageCaptions[file.name] || "")}" aria-label="图注"><button type="button" data-rm-image="${index}" aria-label="移除">✕</button></div>`).join("");
  }
  $("#wordTemplates").addEventListener("change", (event)=>{
    state.templates = state.templates.concat(Array.from(event.target.files || []));
    event.target.value = "";
    renderFileList("#wordTemplateList", state.templates, "data-rm-template");
  });
  $("#wordImages").addEventListener("change", (event)=>{
    state.images = state.images.concat(Array.from(event.target.files || []));
    event.target.value = "";
    renderImageList();
  });
  $("#wordTemplateList").addEventListener("click", (event)=>{
    const button = event.target.closest("[data-rm-template]");
    if (button){ state.templates.splice(Number(button.dataset.rmTemplate), 1); renderFileList("#wordTemplateList", state.templates, "data-rm-template"); }
  });
  $("#wordImageList").addEventListener("click", (event)=>{
    const button = event.target.closest("[data-rm-image]");
    if (button){ const removed=state.images.splice(Number(button.dataset.rmImage), 1)[0]; if(removed) delete state.imageCaptions[removed.name]; renderImageList(); }
  });

  async function refreshWordArtifacts(){
    const records = await api("/api/pipeline/artifacts?stage=pdf2md");
    state.artifacts = records;
    const box = $("#wordArtifacts");
    if (!records.length){
      box.innerHTML = '<div class="empty-ref">还没有 PDF→MD 产物，先到第 01 步解析教材</div>';
      return;
    }
    box.innerHTML = records.map(record => {
      const checked = state.selectedArtifacts.has(record.id);
      return `<label class="artifact-row ${checked ? "on" : ""}">
        <input type="checkbox" data-artifact="${esc(record.id)}" ${checked ? "checked" : ""}>
        <span class="ar-body"><b>${esc(record.title)}</b>
        <span>${esc(record.source?.pdfName || "")} · ${record.meta?.chars ?? 0} 字 · ${record.meta?.imageCount ?? 0} 图 · ${esc(fmtTime(record.created))}</span></span>
      </label>`;
    }).join("");
  }
  $("#wordArtifacts").addEventListener("change", (event)=>{
    const input = event.target.closest("[data-artifact]");
    if (!input) return;
    if (input.checked) state.selectedArtifacts.add(input.dataset.artifact);
    else state.selectedArtifacts.delete(input.dataset.artifact);
    input.closest(".artifact-row").classList.toggle("on", input.checked);
  });

  $("#wordStart").addEventListener("click", async ()=>{
    const button = $("#wordStart");
    if (!state.selectedArtifacts.size && !state.textbookFile){
      toast("请选择 PDF→MD 阶段产物，或上传教材 Markdown");
      return;
    }
    let apiConfig = null;
    try { apiConfig = apiPayload(); } catch (error) { toast(error.message); return; }
    button.disabled = true;
    $("#wordState").textContent = "创建任务…";
    try {
      const payload = {
        subject: $("#wordSubject").value.trim(),
        grade: $("#wordGrade").value.trim(),
        duration: Number($("#wordDuration").value),
        lesson_type: $("#wordType").value,
        student_profile: $("#wordProfile").value.trim(),
        offline: $("#wordOffline").checked,
        review: $("#wordReview").checked,
        filter_images: $("#wordFilter").checked,
        keep_all_images: $("#wordKeepAll").checked,
        chapters: $("#wordChapterFilter").value.trim() || "all",
        textbookArtifacts: Array.from(state.selectedArtifacts),
        captions: state.imageCaptions
      };
      const periods = Number($("#wordPeriods").value);
      if (periods) payload.periods = periods;
      if (apiConfig) payload.api = apiConfig;
      const form = new FormData();
      form.append("payload", JSON.stringify(payload));
      if (state.textbookFile) form.append("textbook", state.textbookFile);
      state.templates.forEach(file=>form.append("templates", file));
      state.images.forEach(file=>form.append("images", file));
      const response = await fetch("/api/pipeline/lesson/jobs", {method:"POST", body:form});
      const data = await response.json();
      if (!response.ok) throw Error(data.error || "创建任务失败");
      history.replaceState(null, "", `?feature=word&lesson=${encodeURIComponent(data.id)}`);
      await pollLesson(data.id);
    } catch (error) {
      $("#wordState").textContent = "";
      showWordError(error.message);
    } finally { button.disabled = false; }
  });

  $("#wordJobsRefresh").addEventListener("click", ()=>refreshWordJobs().catch(showWordError));

  async function refreshWordJobs(){
    const jobs = await api("/api/pipeline/lesson/jobs");
    const box = $("#wordJobs");
    if (!jobs.length){ box.innerHTML = '<div class="empty-ref">暂无任务</div>'; return; }
    box.innerHTML = jobs.slice(0, 8).map(job => `
      <div class="job-card">
        <div class="job-head"><b>${esc(job.source?.textbookName || "教案任务")}</b>${badge(job.status)}</div>
        <div class="job-meta">${esc(fmtTime(job.created_at))} · ${esc(job.progressText || "")}</div>
        <div class="bar-actions">
          <button class="btn btn-sm" data-open-lesson="${esc(job.id)}">查看</button>
          ${job.recoverable ? `<button class="btn btn-sm" data-retry-lesson="${esc(job.id)}">重试</button>` : ""}
        </div>
      </div>`).join("");
  }
  $("#wordJobs").addEventListener("click", async (event)=>{
    const open = event.target.closest("[data-open-lesson]");
    const retry = event.target.closest("[data-retry-lesson]");
    try {
      if (open){ history.replaceState(null, "", `?feature=word&lesson=${encodeURIComponent(open.dataset.openLesson)}`); await pollLesson(open.dataset.openLesson); }
      if (retry){
        let apiConfig = null;
        try { apiConfig = apiPayload(); } catch (error) { apiConfig = null; }
        const result = await api(`/api/pipeline/lesson/jobs/${retry.dataset.retryLesson}/retry`, "POST", apiConfig ? {api:apiConfig} : {});
        history.replaceState(null, "", `?feature=word&lesson=${encodeURIComponent(result.id)}`);
        await pollLesson(result.id);
      }
    } catch (error) { showWordError(error.message); }
  });

  async function pollLesson(id){
    clearTimeout(state.lessonTimer);
    try {
      const job = await api(`/api/pipeline/lesson/jobs/${id}`);
      state.activeLesson = job;
      renderLessonJob(job);
      if (["queued","running"].includes(job.status)){
        const total = job.progress?.total || 0;
        const current = job.progress?.current || 0;
        $("#wordState").textContent = job.status === "queued" ? "排队中…" : "生成中…";
        state.lessonTimer = setTimeout(()=>pollLesson(id), 1800);
        $("#wordProgressText").textContent = job.progressText + (total ? ` · ${current}/${total}` : "");
        return;
      }
      $("#wordState").textContent = "";
      await refreshWordJobs();
      await refreshWordArtifacts().catch(()=>{});
      if (job.status === "succeeded") toast("全部学时生成完成");
      else if (job.status === "partial_failed") toast("部分章节失败；成功学时仍可下载与送入 PPT");
      else if (job.status === "failed") toast("生成失败：" + (job.error || "查看任务日志"));
    } catch (error) { showWordError(error.message); }
  }

  function showWordError(message){
    $("#wordError").hidden = false;
    $("#wordError").textContent = message;
    $("#wordState").textContent = "";
  }

  function renderLessonJob(job){
    $("#wordJobMeta").textContent = `${job.id} · ${fmtTime(job.created_at)}`;
    $("#wordError").hidden = !job.error;
    $("#wordError").textContent = job.error || "";
    const logBox = $("#wordLog");
    if (job.has_log){
      logBox.hidden = false;
      logBox.innerHTML = `<a href="/api/pipeline/lesson/jobs/${esc(job.id)}/log?format=text" target="_blank" rel="noopener">查看本次运行日志</a>`;
    } else { logBox.hidden = true; logBox.innerHTML = ""; }
    const total = job.progress?.total || 0;
    const current = job.progress?.current || 0;
    $("#wordProgress").hidden = !total && !["queued","running"].includes(job.status);
    $("#wordProgressBar").style.width = total ? Math.round(current / total * 100) + "%" : (job.status === "succeeded" ? "100%" : "0%");
    $("#wordProgressText").textContent = job.progressText || statusLabel[job.status] || job.status;

    const chapters = job.chapters || [];
    $("#wordChapters").innerHTML = chapters.length ? chapters.map(chapter => `
      <div class="job-card">
        <div class="job-head"><b>${esc(chapter.title || chapter.id)}</b>${badge(chapter.status)}</div>
        <div class="job-meta">${chapter.validation?.metrics ? `${chapter.validation.metrics.periods || 0} 学时 · 引用 ${chapter.validation.metrics.citations ?? 0} 条 · 图 ${chapter.validation.metrics.figures ?? 0} 张` : ""}</div>
        ${chapter.error ? `<div class="job-meta" style="color:#a13c2a">${esc(chapter.error)}</div>` : ""}
        ${(chapter.validation?.issues || []).length ? `<div class="job-meta" style="color:#96602e">${esc(chapter.validation.issues.map(x=>x.message).join("；").slice(0,220))}</div>` : ""}
        ${chapter.downloads ? `<div class="bar-actions">${Object.entries(chapter.downloads).map(([key,url])=>`<a class="btn btn-sm" href="${esc(url)}" download>${key==="plan"?"课时 JSON":key==="review"?"质检报告":key==="acceptance"?"验收报告":"配图说明"}</a>`).join("")}</div>` : ""}
      </div>`).join("") : '<div class="empty-ref">生成后在此查看章节与学时状态</div>';

    const files = job.docx_files || [];
    $("#wordResultMeta").textContent = `${files.length} 份 Word · 模型：${job.api?.label || "-"}${job.source?.textbookName ? " · 教材：" + job.source.textbookName : ""}`;
    $("#wordResults").hidden = !files.length;
    $("#wordDocx").innerHTML = files.map(item => `
      <article class="result-card">
        <h3>${esc(item.title)}</h3>
        <div class="rc-meta">${esc(item.chapter_id || "")} · ${badge(item.status)}</div>
        <div class="bar-actions">
          <a class="btn btn-sm" href="${esc(item.url)}" download>下载 DOCX</a>
          ${item.previewUrl ? `<a class="btn btn-sm" href="${esc(item.previewUrl)}" target="_blank" rel="noopener">查看中间稿</a>` : ""}
          <button class="btn btn-sm btn-accent" data-docx-handoff="${item.index}" ${["ok","ok_with_warnings"].includes(item.status) ? "" : "disabled title='质检未通过，不能送入 PPT'"}>送入 PPT</button>
        </div>
      </article>`).join("");
  }

  $("#wordDocx").addEventListener("click", async (event)=>{
    const handoff = event.target.closest("[data-docx-handoff]");
    if (!handoff || !state.activeLesson) return;
    handoff.disabled = true;
    try {
      const artifact = await api(`/api/pipeline/lesson/jobs/${state.activeLesson.id}/artifact/${handoff.dataset.docxHandoff}`, "POST", {});
      const result = await api("/api/pipeline/handoff/ppt", "POST", {artifactId:artifact.id});
      if (window.Workbench) await window.Workbench.showPptWithRefs(result.refs.map(item=>item.id), result.topic);
      toast(`已送入 PPT：${result.refs.length} 份资料，确认大纲后再制作`);
    } catch (error) {
      showWordError(error.message);
    } finally { handoff.disabled = false; }
  });

  /* ================= init ================= */
  async function enter(name){
    try {
      if (name === "pdf"){
        if (!state.initialized.pdf){ state.initialized.pdf = true; }
        await refreshPdfJobs();
        const id = new URLSearchParams(location.search).get("pdf");
        if (id) await pollPdf(id);
      }
      if (name === "word"){
        if (!state.initialized.word){
          state.initialized.word = true;
          $("#wordApiPreset").dispatchEvent(new Event("change"));
        }
        await refreshWordArtifacts();
        await refreshWordJobs();
        const params = new URLSearchParams(location.search);
        if (params.get("lesson")) await pollLesson(params.get("lesson"));
      }
    } catch (error) {
      if (name === "pdf") showPdfError(error.message);
      else showWordError(error.message);
    }
  }

  window.PipelineUI = {enter};
  const bootFeature = new URLSearchParams(location.search).get("feature");
  if (bootFeature === "pdf" || bootFeature === "word"){
    let tries = 0;
    const timer = setInterval(()=>{
      const view = document.getElementById("view-" + bootFeature);
      if (view && !view.hidden){ clearInterval(timer); enter(bootFeature); }
      else if (++tries > 40) clearInterval(timer);
    }, 50);
  }
})();