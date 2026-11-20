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
    textbookFile:null, templates:[], images:[], imageCaptions:{}, knowledgeRefs:[], refRows:[], knowledgeCache:[], refChoice:{artifacts:new Set(), knowledge:new Set()},
    activeLesson:null, lessonTimer:null, wordStageHidden:false, wordStartedAt:0, wordClock:null, docxBlocks:null, docxIndex:0, docxDirty:false,
    initialized:{pdf:false, word:false}
  };

  /* ================= PDF → MD ================= */
  const PDF_STEPS = ["检测文字层", "OCR 识别", "切分章节", "导出原图", "生成 Markdown"];
  const pdfFileInput = $("#pdfFile");

  state.pdfDocs = {};
  state.pdfJob = null;
  state.pdfChapter = 1;
  state.pdfMode = "render";
  state.pdfText = "";
  state.pdfEditing = false;
  state.pdfDirty = false;
  state.pdfClock = null;
  state.pdfStartedAt = 0;
  state.pdfHidden = false;

  function selectPdf(file){
    if (!file) return;
    if (!/\.pdf$/i.test(file.name)){
      toast("请选择 PDF 文件");
      pdfFileInput.value = "";
      return;
    }
    state.pdfFile = file;
    const picked = $("#pdfPicked");
    picked.hidden = false;
    picked.innerHTML = `<span><b>${esc(file.name)}</b> · ${fileSize(file.size)}</span><button type="button" id="pdfClear" aria-label="移除">✕</button>`;
    $("#pdfClear").addEventListener("click", ()=>{ state.pdfFile=null; pdfFileInput.value=""; picked.hidden=true; picked.innerHTML=""; $("#pdfStart").disabled=true; });
    $("#pdfStart").disabled = false;
    $("#pdfState").textContent = "";
    hidePdfError();
  }
  pdfFileInput.addEventListener("change", ()=>{
    const file = pdfFileInput.files[0];
    if (file) selectPdf(file);
  });
  const pdfDrop = $("#pdfDrop");
  ["dragenter", "dragover"].forEach(type=>pdfDrop.addEventListener(type, (event)=>{
    event.preventDefault();
    event.stopPropagation();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
    pdfDrop.classList.add("over");
  }));
  ["dragleave", "drop"].forEach(type=>pdfDrop.addEventListener(type, (event)=>{
    event.preventDefault();
    event.stopPropagation();
    pdfDrop.classList.remove("over");
  }));
  pdfDrop.addEventListener("drop", (event)=>{
    const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
    if (file) selectPdf(file);
  });
  // Dropping a PDF anywhere in this page should upload it, never navigate to the file.
  const pdfView = $("#view-pdf");
  ["dragenter", "dragover"].forEach(type=>pdfView.addEventListener(type, (event)=>{
    if (event.dataTransfer && Array.from(event.dataTransfer.types || []).includes("Files")){
      event.preventDefault();
      event.stopPropagation();
    }
  }));
  pdfView.addEventListener("drop", (event)=>{
    if (event.target.closest("#pdfDrop")) return;
    event.preventDefault();
    event.stopPropagation();
    const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
    if (file) selectPdf(file);
  });

  /* ---------- parse progress ---------- */
  function fmtDuration(seconds){
    const total = Math.max(0, Math.floor(seconds || 0));
    return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
  }
  function stopPdfClock(){
    if (state.pdfClock){ clearInterval(state.pdfClock); state.pdfClock = null; }
  }
  function startPdfClock(){
    stopPdfClock();
    if (!state.pdfStartedAt) state.pdfStartedAt = Date.now();
    state.pdfClock = setInterval(()=>{
      const timer = $("#pdfProgressTimer");
      if (timer) timer.textContent = `总进度 ${state.pdfPct || 0}% · 已用 ${fmtDuration((Date.now() - state.pdfStartedAt) / 1000)}`;
    }, 1000);
  }
  function pdfProgressState(job){
    const log = (job && job.log) || [];
    const all = log.join("\n");
    const elapsed = (Date.now() - (state.pdfStartedAt || Date.now())) / 1000;
    let step = 0, pct = 6, label = "读取 PDF 页结构…";
    if (job && job.status === "queued"){ pct = 5; label = "任务排队中…"; }
    if (/启动解析服务/.test(all)){ step = 1; pct = Math.max(pct, 26); label = "解析服务正在还原版面、图片与公式…"; }
    if (/解析服务完成/.test(all)){ step = 3; pct = Math.max(pct, 90); label = "解析完成，正在整理章节…"; }
    if (/检测到 \d+\/\d+ 页无可用文字/.test(all)){ step = 1; pct = Math.max(pct, 18); label = "检测到扫描页，启动本机 OCR…"; }
    const ocr = [...all.matchAll(/本机 OCR 第 (\d+)\/(\d+) 页/g)].pop();
    if (ocr){
      const done = Number(ocr[1]), total = Math.max(1, Number(ocr[2]));
      step = 1;
      pct = 18 + Math.round(62 * done / total);
      label = `OCR 识别第 ${done}/${total} 页…`;
    } else if (job && job.status === "running"){
      pct = Math.max(pct, Math.min(72, 10 + Math.floor(elapsed * 1.2)));
      if (!/检测到|切分|已生成|OCR|解析服务/.test(all)) label = "提取文字层、编号标题与页结构…";
    }
    const cut = all.match(/切分出 (\d+) 个章节，导出 (\d+) 张图片/);
    if (cut){
      step = 3; pct = Math.max(pct, 86);
      label = `已切分 ${cut[1]} 章、导出 ${cut[2]} 张原图…`;
    } else if (/切分出 \d+ 个章节/.test(all)){
      step = 2; pct = Math.max(pct, 84); label = "正在切分章节…";
    }
    const gens = [...all.matchAll(/已生成 [^\n]+\.md/g)];
    if (gens.length){
      step = 4; pct = Math.max(pct, 90 + Math.min(8, gens.length * 2));
      label = `已生成 ${gens.length} 章 Markdown…`;
    }
    if (job && job.status === "succeeded"){ step = 5; pct = 100; label = "解析完成，正在打开结果…"; }
    return { step, pct: Math.min(100, pct), label };
  }
  function updatePdfProgress(job){
    const info = pdfProgressState(job || {});
    state.pdfPct = info.pct;
    $("#pdfProgressBar").style.width = info.pct + "%";
    const track = $("#pdfProgressTrack");
    if (track) track.setAttribute("aria-valuenow", String(info.pct));
    $("#pdfProgressText").textContent = info.label;
    const timer = $("#pdfProgressTimer");
    if (timer && state.pdfStartedAt) timer.textContent = `总进度 ${info.pct}% · 已用 ${fmtDuration((Date.now() - state.pdfStartedAt) / 1000)}`;
    document.querySelectorAll("#pdfProgressSteps [data-pdf-step]").forEach(el=>{
      const index = Number(el.dataset.pdfStep);
      el.classList.toggle("done", index < info.step);
      el.classList.toggle("on", index === info.step);
    });
    const tail = ((job && job.log) || []).slice(-3);
    const box = $("#pdfProgressLog");
    if (box){ box.hidden = !tail.length; box.textContent = tail.join("\n"); }
    $("#pdfProgressTitle").textContent = job && job.status === "succeeded" ? "解析完成" : "正在解析教材";
    if (job && job.fileName) $("#pdfProgressFile").textContent = job.fileName;
  }
  function showPdfProgress(job){
    $("#pdfPageHead").hidden = true;
    $("#pdfWorkspace").hidden = true;
    $("#pdfDocStage").hidden = true;
    hidePdfError();
    $("#pdfProgress").hidden = false;
    updatePdfProgress(job || {fileName: state.pdfFile && state.pdfFile.name, status: "queued", log: []});
  }
  function showPdfWorkspace(){
    stopPdfClock();
    $("#pdfProgress").hidden = true;
    $("#pdfDocStage").hidden = true;
    $("#pdfPageHead").hidden = false;
    $("#pdfWorkspace").hidden = false;
    $("#pdfState").textContent = "";
    state.pdfHidden = false;
  }
  function hidePdfError(){
    const box = $("#pdfError");
    if (box) box.hidden = true;
  }
  function showPdfError(message){
    stopPdfClock();
    $("#pdfProgress").hidden = true;
    $("#pdfDocStage").hidden = true;
    $("#pdfPageHead").hidden = false;
    $("#pdfWorkspace").hidden = false;
    const box = $("#pdfError");
    if (box){ box.hidden = false; $("#pdfErrorText").textContent = message || "解析失败"; }
  }

  $("#pdfStart").addEventListener("click", async ()=>{
    if (!state.pdfFile){ toast("先选择教材 PDF"); return; }
    const button = $("#pdfStart");
    button.disabled = true;
    hidePdfError();
    state.pdfStartedAt = Date.now();
    state.pdfHidden = false;
    showPdfProgress({fileName: state.pdfFile.name, status: "queued", log: ["上传 PDF…"]});
    startPdfClock();
    try {
      const form = new FormData();
      form.append("file", state.pdfFile);
      const response = await fetch("/api/pipeline/pdf/jobs", {method:"POST", body:form});
      const data = await response.json();
      if (!response.ok) throw Error(data.error || "创建任务失败");
      history.replaceState(null, "", `?feature=pdf&pdf=${encodeURIComponent(data.id)}`);
      $("#pdfProgressFile").textContent = data.fileName || state.pdfFile.name;
      await refreshPdfJobs();
      await pollPdf(data.id);
    } catch (error) {
      showPdfError(error.message);
    } finally { button.disabled = false; }
  });

  $("#pdfProgressBack").addEventListener("click", ()=>{
    if (!state.activePdf) return;
    state.pdfHidden = true;
    showPdfWorkspace();
    toast("解析在后台继续，完成后会自动打开结果");
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
      if (open){
        state.pdfHidden = false;
        history.replaceState(null, "", `?feature=pdf&pdf=${encodeURIComponent(open.dataset.openPdf)}`);
        await pollPdf(open.dataset.openPdf);
      }
      if (retry){
        state.pdfStartedAt = Date.now();
        state.pdfHidden = false;
        await api(`/api/pipeline/pdf/jobs/${retry.dataset.retryPdf}/retry`, "POST", {});
        await pollPdf(retry.dataset.retryPdf);
        await refreshPdfJobs();
      }
    } catch (error) { showPdfError(error.message); }
  });

  async function pollPdf(id){
    clearTimeout(state.pdfTimer);
    try {
      const job = await api(`/api/pipeline/pdf/jobs/${id}`);
      state.activePdf = job;
      if (["queued","running"].includes(job.status)){
        if (!state.pdfStartedAt) state.pdfStartedAt = Date.parse(job.created) || Date.now();
        startPdfClock();
        if (!state.pdfHidden) showPdfProgress(job);
        state.pdfTimer = setTimeout(()=>pollPdf(id), 1000);
      } else {
        stopPdfClock();
        await refreshPdfJobs();
        if (job.status === "succeeded"){
          if (!$("#view-pdf").hidden) await showPdfDoc(job);
          toast(`解析完成：${job.chapters.length} 个章节`);
        } else {
          showPdfError(job.error || "解析失败");
        }
      }
    } catch (error) { showPdfError(error.message); }
  }

  /* ---------- result document ---------- */
  function pageRange(pages){
    const nums = [...(pages || [])].sort((a, b) => a - b);
    if(!nums.length) return "";
    const parts = [];
    let start = nums[0], prev = nums[0];
    for(const n of nums.slice(1)){
      if(n === prev + 1){ prev = n; continue; }
      parts.push(start === prev ? String(start) : `${start}–${prev}`);
      start = prev = n;
    }
    parts.push(start === prev ? String(start) : `${start}–${prev}`);
    return parts.join("、");
  }
  function pdfChapter(){
    const job = state.pdfJob;
    if (!job || !job.chapters) return null;
    return job.chapters.find(item => item.index === state.pdfChapter) || job.chapters[0] || null;
  }
  function pdfMetaText(job){
    const result = job.result || {};
    let ocr = "";
    if (result.engine === "mineru") ocr = ` · 解析服务 ${result.mineruTier || "standard"}${result.mineruSeconds ? "（" + Math.round(result.mineruSeconds) + "s）" : ""}`;
    else if (result.ocr) ocr = ` · 本机 OCR ${result.ocrPages ?? ""} 页${result.ocrConfidence ? "（均值 " + result.ocrConfidence + "）" : ""}`;
    const edited = (job.chapters || []).some(item => item.edited) ? " · 含本机修订" : "";
    return `${result.pages ?? ""} 页 · ${result.images ?? 0} 张原图 · 共 ${(job.chapters || []).length} 章${ocr}${edited} · ${job.sha256 ? "SHA " + job.sha256.slice(0,12) : ""}`;
  }
  function renderPdfNav(job){
    const select = $("#pdfDocSelect");
    if (!select) return;
    const chapters = job.chapters || [];
    select.innerHTML = chapters.map(chapter => `<option value="${chapter.index}">${esc(chapter.title)}${Number(chapter.revision || 1) > 1 ? " · r" + chapter.revision : ""}</option>`).join("");
    select.hidden = chapters.length < 2;
    select.value = String(state.pdfChapter);
  }
  function imageUrl(jobId, index, src){
    if (!/^images\//.test(src)) return "";
    const name = src.slice(7).split("/").pop();
    if (!name) return "";
    return `/api/pipeline/pdf/jobs/${encodeURIComponent(jobId)}/images/${index}/${encodeURIComponent(name)}`;
  }
  function mdInline(text, ctx){
    let out = esc(text);
    out = out.replace(/`([^`]+)`/g, "<code>$1</code>");
    out = out.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (match, alt, src)=>{
      const url = imageUrl(ctx.jobId, ctx.index, src);
      if (!url) return "";
      return `<img src="${url}" alt="${alt}" loading="lazy">${alt ? `<span class="md-img-cap">${alt}</span>` : ""}`;
    });
    out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    out = out.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
    return out;
  }
  function joinSoftLines(lines){
    return lines.reduce((out, line)=>{
      if (!out) return line;
      const needSpace = /[A-Za-z0-9]$/.test(out) && /^[A-Za-z0-9]/.test(line);
      return out + (needSpace ? " " : "") + line;
    }, "");
  }
  function listMarker(line){
    const bullet = line.match(/^\s*([-*+])\s+(.*)$/);
    if (bullet) return {type:"ul", text:bullet[2]};
    const paren = line.match(/^\s*\d{1,2}[)）、]\s*(.*)$/);
    if (paren) return {type:"ol", text:paren[1]};
    const dot = line.match(/^\s*\d{1,2}\.\s+(.*)$/);
    if (dot) return {type:"ol", text:dot[1]};
    return null;
  }
  function sectionHeading(line){
    const match = line.match(/^(\d{1,2}(?:\.\d{1,2})+)\s*(\S.*)$/);
    if (!match) return null;
    const title = match[2].trim();
    if (!title || title.length > 42 || /[。；！？，,：:、%]/.test(title)) return null;
    if (!/^[A-Za-z\u4e00-\u9fa5]/.test(title)) return null;
    if (/\.{3,}|…/.test(title)) return null;
    const depth = match[1].split(".").length;
    return {level:Math.min(4, depth + 1), text:`${match[1]} ${title}`};
  }
  function renderMarkdown(text, ctx){
    const lines = String(text || "").replace(/\r\n/g, "\n").split("\n");
    const html = [];
    let para = [], list = null, listType = "";
    const flushPara = ()=>{
      if (!para.length) return;
      html.push(`<p>${mdInline(joinSoftLines(para), ctx)}</p>`);
      para = [];
    };
    const flushList = ()=>{
      if (!list){ return; }
      html.push(`<${listType}>${list.map(item => `<li>${mdInline(item, ctx)}</li>`).join("")}</${listType}>`);
      list = null; listType = "";
    };
    for (let i = 0; i < lines.length; i++){
      const line = lines[i];
      const fence = line.match(/^```(.*)$/);
      if (fence){
        flushPara(); flushList();
        const code = [];
        i++;
        while (i < lines.length && !/^```/.test(lines[i])) code.push(lines[i++]);
        html.push(`<pre><code>${esc(code.join("\n"))}</code></pre>`);
        continue;
      }
      if (!line.trim()){
        if (list){
          let next = i + 1;
          while (next < lines.length && (!lines[next].trim() || /^\s*\d{1,3}\s*$/.test(lines[next]))) next++;
          const marker = next < lines.length ? listMarker(lines[next]) : null;
          if (marker && marker.type === listType){ i = next - 1; continue; }
          flushList();
        }
        flushPara();
        continue;
      }
      if (list && /^\s*\d{1,3}\s*$/.test(line)) continue;
      const heading = line.match(/^(#{1,6})\s+(.*)$/);
      if (heading){
        flushPara(); flushList();
        const level = Math.min(4, heading[1].length);
        html.push(`<h${level}>${mdInline(heading[2], ctx)}</h${level}>`);
        continue;
      }
      const section = sectionHeading(line);
      if (section){
        flushPara(); flushList();
        html.push(`<h${section.level}>${mdInline(section.text, ctx)}</h${section.level}>`);
        continue;
      }
      if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)){
        flushPara(); flushList(); html.push("<hr>"); continue;
      }
      if (/^>\s?/.test(line)){
        flushPara(); flushList();
        const quote = [];
        while (i < lines.length && /^>\s?/.test(lines[i])) quote.push(lines[i++].replace(/^>\s?/, ""));
        i--;
        html.push(`<blockquote>${mdInline(joinSoftLines(quote), ctx)}</blockquote>`);
        continue;
      }
      const marker = listMarker(line);
      if (marker){
        flushPara();
        if (!list || listType !== marker.type){ flushList(); list = []; listType = marker.type; }
        list.push(marker.text);
        continue;
      }
      if (list) flushList();
      para.push(line);
    }
    flushPara(); flushList();
    return html.join("") || '<p class="hint">本章暂无内容</p>';
  }
  window.PipelineMD = {render: renderMarkdown};

  function setPdfMode(mode, silent){
    state.pdfMode = mode;
    document.querySelectorAll("[data-pdf-view]").forEach(btn=>{
      const active = btn.dataset.pdfView === mode;
      btn.classList.toggle("on", active);
      btn.setAttribute("aria-selected", active ? "true" : "false");
    });
    if (state.pdfEditing) return;
    const render = $("#pdfDocRender"), raw = $("#pdfDocText");
    render.hidden = mode !== "render";
    raw.hidden = mode !== "text";
    if (mode === "render"){
      const chapter = pdfChapter();
      render.innerHTML = renderMarkdown(state.pdfText, {jobId: state.pdfJob.id, index: chapter ? chapter.index : state.pdfChapter});
    } else {
      raw.textContent = state.pdfText;
    }
    if (!silent && mode === "text") raw.scrollTop = 0;
  }
  async function selectPdfChapter(index, force){
    if (state.pdfDirty && !force && !confirm("当前章节有未保存的修改，确定放弃？")) return;
    const chapter = (state.pdfJob.chapters || []).find(item => item.index === index);
    if (!chapter) return;
    state.pdfChapter = index;
    state.pdfEditing = false;
    state.pdfDirty = false;
    $("#pdfDocEditor").hidden = true;
    $("#pdfDocEditActions").hidden = true;
    $("#pdfDocEdit").disabled = false;
    $("#pdfDocEdit").textContent = "编辑";
    $("#pdfDocDownload").disabled = false;
    const picker = $("#pdfDocSelect");
    if (picker) picker.value = String(index);
    const key = `${state.pdfJob.id}:${index}:${chapter.revision || 1}`;
    if (state.pdfDocs[key] == null){
      const response = await fetch(`/api/pipeline/pdf/jobs/${state.pdfJob.id}/preview/${index}`, {cache:"no-store"});
      if (!response.ok) throw Error("读取章节内容失败");
      state.pdfDocs[key] = await response.text();
    }
    state.pdfText = state.pdfDocs[key];
    $("#pdfDocEditor").value = state.pdfText;
    $("#pdfDocStatus").textContent = `${chapter.revision > 1 ? "修订 r" + chapter.revision + " · " : ""}${chapter.chars} 字符`;
    setPdfMode(state.pdfMode, true);
  }
  async function showPdfDoc(job){
    state.pdfHidden = false;
    state.pdfStartedAt = 0;
    stopPdfClock();
    const same = state.pdfJob && state.pdfJob.id === job.id;
    state.pdfJob = job;
    if (!same){
      state.pdfDocs = {};
      state.pdfChapter = 1;
      state.pdfMode = "render";
      state.pdfEditing = false;
      state.pdfDirty = false;
    }
    $("#pdfPageHead").hidden = true;
    $("#pdfWorkspace").hidden = true;
    $("#pdfProgress").hidden = true;
    $("#pdfDocStage").hidden = false;
    hidePdfError();
    $("#pdfDocTitle").textContent = job.fileName || "解析结果";
    $("#pdfDocMeta").textContent = pdfMetaText(job);
    if (!job.chapters || !job.chapters.length){
      $("#pdfDocSelect").hidden = true;
      $("#pdfDocRender").innerHTML = '<div class="notice">没有可显示的章节内容。</div>';
      $("#pdfDocText").textContent = "";
      return;
    }
    if (!job.chapters.some(item => item.index === state.pdfChapter)) state.pdfChapter = job.chapters[0].index;
    renderPdfNav(job);
    await selectPdfChapter(state.pdfChapter, true);
  }

  $("#pdfDocSelect").addEventListener("change", async (event)=>{
    const select = event.currentTarget;
    const index = Number(select.value);
    try {
      await selectPdfChapter(index);
      if (state.pdfChapter !== index) select.value = String(state.pdfChapter);
    } catch (error) {
      select.value = String(state.pdfChapter);
      toast(error.message);
    }
  });
  $("#pdfDocBack").addEventListener("click", ()=>{
    if (state.pdfDirty && !confirm("当前章节有未保存的修改，确定返回？")) return;
    state.pdfDirty = false;
    showPdfWorkspace();
    refreshPdfJobs().catch(()=>{});
    history.replaceState(null, "", "?feature=pdf");
  });
  document.querySelectorAll("[data-pdf-view]").forEach(btn=>{
    btn.addEventListener("click", ()=>setPdfMode(btn.dataset.pdfView));
  });
  $("#pdfDocEdit").addEventListener("click", ()=>{
    if (!state.pdfText) return;
    state.pdfEditing = true;
    state.pdfDirty = false;
    const editor = $("#pdfDocEditor");
    editor.value = state.pdfText;
    editor.hidden = false;
    $("#pdfDocRender").hidden = true;
    $("#pdfDocText").hidden = true;
    $("#pdfDocEditActions").hidden = false;
    $("#pdfDocEdit").disabled = true;
    $("#pdfDocStatus").textContent = "编辑中…";
    $("#pdfDocDirty").textContent = "修改保存在本页；点击“保存修订”生成新版本";
    document.querySelectorAll("[data-pdf-view]").forEach(btn=>btn.disabled = true);
    editor.focus();
  });
  $("#pdfDocEditor").addEventListener("input", ()=>{
    state.pdfDirty = true;
    const size = $("#pdfDocEditor").value.length;
    $("#pdfDocDirty").textContent = `有未保存的修改 · ${size} 字符 · 保存后生成新修订`;
  });
  $("#pdfDocCancel").addEventListener("click", ()=>{
    if (state.pdfDirty && !confirm("放弃本次修改？")) return;
    state.pdfEditing = false;
    state.pdfDirty = false;
    $("#pdfDocEditor").hidden = true;
    $("#pdfDocEditActions").hidden = true;
    $("#pdfDocEdit").disabled = false;
    document.querySelectorAll("[data-pdf-view]").forEach(btn=>btn.disabled = false);
    const chapter = pdfChapter();
    $("#pdfDocStatus").textContent = `${chapter && chapter.revision > 1 ? "修订 r" + chapter.revision + " · " : ""}${chapter ? chapter.chars : state.pdfText.length} 字符`;
    setPdfMode(state.pdfMode, true);
  });
  $("#pdfDocSave").addEventListener("click", async ()=>{
    const text = $("#pdfDocEditor").value;
    if (!text.trim()){ toast("章节内容不能为空"); return; }
    const button = $("#pdfDocSave");
    button.disabled = true;
    button.textContent = "保存中…";
    try {
      const job = await api(`/api/pipeline/pdf/jobs/${state.pdfJob.id}/chapters/${state.pdfChapter}`, "POST", {markdown: text});
      const key = `${job.id}:${state.pdfChapter}:${(job.chapters.find(c => c.index === state.pdfChapter) || {}).revision || 1}`;
      state.pdfJob = job;
      state.activePdf = job;
      state.pdfDocs[key] = text;
      state.pdfText = text;
      state.pdfDirty = false;
      state.pdfEditing = false;
      $("#pdfDocEditor").hidden = true;
      $("#pdfDocEditActions").hidden = true;
      $("#pdfDocEdit").disabled = false;
      document.querySelectorAll("[data-pdf-view]").forEach(btn=>btn.disabled = false);
      $("#pdfDocMeta").textContent = pdfMetaText(job);
      renderPdfNav(job);
      const chapter = pdfChapter();
      $("#pdfDocStatus").textContent = `修订 r${chapter.revision} · ${chapter.chars} 字符`;
      setPdfMode(state.pdfMode, true);
      toast(`已保存修订 r${chapter.revision}`);
      refreshPdfJobs().catch(()=>{});
    } catch (error) {
      toast(error.message);
    } finally {
      button.disabled = false;
      button.textContent = "保存修订";
    }
  });
  $("#pdfDocDownload").addEventListener("click", ()=>{
    if (state.pdfJob) window.location.href = `/api/pipeline/pdf/jobs/${state.pdfJob.id}/bundle`;
  });
  $("#pdfDocHandoff").addEventListener("click", ()=>{
    const chapter = pdfChapter();
    if (!chapter){ toast("请选择章节"); return; }
    state.selectedArtifacts = new Set([chapter.artifactId]);
    if (window.Workbench) window.Workbench.showFeature("word");
    toast("已带入 1 章教材，继续选模板与简报");
  });


  /* ================= MD → Lesson ================= */
  function isMarkdownTitle(title){ return /\.(md|markdown)$/i.test(String(title || "")); }
  function isDocxTitle(title){ return /\.docx$/i.test(String(title || "")); }
  function refTypeLabel(item){
    if (item.kind === "artifact") return "教材";
    if (item.kind === "textbook") return "教材";
    if (item.kind === "template") return "模板";
    if (item.kind === "image") return "配图";
    if (item.kind === "knowledge") return isDocxTitle(item.title) ? "模板" : "教材";
    return "资料";
  }
  function refBadgeClass(item){
    const type = refTypeLabel(item);
    if (type === "模板") return "template";
    if (type === "教材") return "jiaocai";
    return "";
  }

  function renderWordRefs(){
    const box = $("#wordRefList");
    const items = [];
    state.selectedArtifacts.forEach(id=>{
      const record = state.artifacts.find(item=>item.id === id);
      items.push({kind:"artifact", id, title:record?.title || "教材章节",
        meta:record ? `PDF→MD · ${record.meta?.chars ?? 0} 字 · ${record.meta?.imageCount ?? 0} 图` : "PDF→MD 教材"});
    });
    state.knowledgeRefs.forEach(ref=>items.push({kind:"knowledge", id:ref.id, title:ref.title,
      meta:`最近产物 · ${isDocxTitle(ref.title) ? "DOCX 模板" : "Markdown 教材"}`}));
    if (state.textbookFile) items.push({kind:"textbook", title:state.textbookFile.name,
      meta:`本地上传 · ${fileSize(state.textbookFile.size)}`});
    state.templates.forEach((file,index)=>items.push({kind:"template", index, title:file.name,
      meta:`本地上传模板 · ${fileSize(file.size)}`}));
    state.images.forEach((file,index)=>items.push({kind:"image", index, title:file.name,
      meta:`本地上传配图 · ${fileSize(file.size)}`, caption:state.imageCaptions[file.name] || ""}));

    box.classList.toggle("has-items", items.length > 0);
    if (!items.length){
      box.innerHTML = '<div class="empty-ref">还没有引用任何资料，从最近产物选择或上传本地文件</div>';
      return;
    }
    box.innerHTML = items.map(item=>{
      const type = refTypeLabel(item);
      const key = item.kind === "artifact" || item.kind === "knowledge" ? `${item.kind}:${item.id}` : `${item.kind}:${item.index}`;
      return `<div class="word-ref-item">
        <span class="wr-badge ${refBadgeClass(item)}">${type}</span>
        <span class="wr-body"><b>${esc(item.title)}</b><span>${esc(item.meta)}</span>
        ${item.kind === "image" ? `<input class="control caption-input" data-caption-ref="${item.index}" placeholder="图注（可选）" value="${esc(item.caption)}" aria-label="图注">` : ""}
        </span>
        <button type="button" class="ref-x" data-rm-ref="${esc(key)}" aria-label="移除 ${esc(item.title)}">✕</button>
      </div>`;
    }).join("");
  }

  $("#wordRefList").addEventListener("click", (event)=>{
    const remove = event.target.closest("[data-rm-ref]");
    if (!remove) return;
    const [kind, value] = remove.dataset.rmRef.split(":");
    if (kind === "artifact") state.selectedArtifacts.delete(value);
    else if (kind === "knowledge") state.knowledgeRefs = state.knowledgeRefs.filter(item=>item.id !== value);
    else if (kind === "textbook") state.textbookFile = null;
    else if (kind === "template") state.templates.splice(Number(value), 1);
    else if (kind === "image"){
      const removed = state.images.splice(Number(value), 1)[0];
      if (removed) delete state.imageCaptions[removed.name];
    }
    renderWordRefs();
  });
  $("#wordRefList").addEventListener("input", (event)=>{
    const input = event.target.closest("[data-caption-ref]");
    if (!input) return;
    const file = state.images[Number(input.dataset.captionRef)];
    if (file) state.imageCaptions[file.name] = input.value;
  });

  /* ---- reference picker modal ---- */
  const wordRefModal = $("#wordRefModal");
  function openWordRefModal(){
    state.refChoice = {artifacts:new Set(state.selectedArtifacts), knowledge:new Set(state.knowledgeRefs.map(x=>x.id))};
    $("#wordRefSearch").value = "";
    wordRefModal.classList.add("open");
    document.body.style.overflow = "hidden";
    loadWordRefOptions().catch(showWordError);
    setTimeout(()=>$("#wordRefSearch").focus(), 80);
  }
  function closeWordRefModal(){
    wordRefModal.classList.remove("open");
    document.body.style.overflow = "";
  }
  async function loadWordRefOptions(){
    $("#wordRefOptions").innerHTML = '<div class="empty-ref">加载中…</div>';
    const [records, knowledge] = await Promise.all([
      api("/api/pipeline/artifacts?stage=pdf2md"),
      api("/api/knowledge")
    ]);
    state.artifacts = records;
    state.knowledgeCache = knowledge;
    state.refRows = [
      ...records.map(record=>({kind:"artifact", id:record.id, title:record.title,
        meta:`${record.source?.pdfName || ""} · ${record.meta?.chars ?? 0} 字 · ${record.meta?.imageCount ?? 0} 图`,
        label:"教材"})),
      ...knowledge.filter(item=>isMarkdownTitle(item.title)).map(item=>({
        kind:"knowledge", id:item.id, title:item.title,
        meta:`最近产物 · ${item.meta || ""}`, label:"教材"}))
    ];
    const seen = new Set();
    state.refRows = state.refRows.filter(row=>{
      const key = row.title + "|" + row.label;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    renderWordRefOptions();
  }
  function renderWordRefOptions(){
    const q = $("#wordRefSearch").value.trim().toLowerCase();
    const rows = (state.refRows || []).filter(row=>!q || (row.title + row.meta).toLowerCase().includes(q));
    $("#wordRefOptions").innerHTML = rows.length ? rows.map(row=>{
      const on = row.kind === "artifact"
        ? state.refChoice.artifacts.has(row.id)
        : state.refChoice.knowledge.has(row.id);
      return `<div class="kb-row ${on ? "on" : ""}" data-ref-kind="${row.kind}" data-ref-id="${esc(row.id)}" role="option" aria-selected="${on}" tabindex="0">
        <span class="kb-check"><svg viewBox="0 0 24 24" fill="none" stroke-width="3" stroke-linecap="round"><path d="m5 13 4 4L19 7"/></svg></span>
        <div class="ref-body"><b>${esc(row.title)}</b><p>${esc(row.meta)}</p>
        <div class="ref-meta"><span class="wr-badge ${row.label === "模板" ? "template" : "jiaocai"}">${row.label}</span></div></div>
      </div>`;
    }).join("") : '<div class="empty-ref">没有匹配的教材或模板</div>';
    const count = state.refChoice.artifacts.size + state.refChoice.knowledge.size;
    $("#wordRefPicked").textContent = count;
  }
  function toggleRefChoice(row){
    const set = row.kind === "artifact" ? state.refChoice.artifacts : state.refChoice.knowledge;
    if (set.has(row.id)) set.delete(row.id);
    else {
      if (state.refChoice.artifacts.size + state.refChoice.knowledge.size >= 6){ toast("最多选择 6 份资料"); return; }
      set.add(row.id);
    }
    renderWordRefOptions();
  }
  $("#wordRefOptions").addEventListener("click", (event)=>{
    const row = event.target.closest("[data-ref-id]");
    if (row) toggleRefChoice({kind:row.dataset.refKind, id:row.dataset.refId});
  });
  $("#wordRefOptions").addEventListener("keydown", (event)=>{
    const row = event.target.closest("[data-ref-id]");
    if (row && (event.key === "Enter" || event.key === " ")){ event.preventDefault(); toggleRefChoice({kind:row.dataset.refKind, id:row.dataset.refId}); }
  });
  $("#wordRefSearch").addEventListener("input", renderWordRefOptions);
  $("#wordRefRefresh").addEventListener("click", ()=>loadWordRefOptions().catch(showWordError));
  $("#wordRefClose").addEventListener("click", closeWordRefModal);
  $("#wordRefCancel").addEventListener("click", closeWordRefModal);
  wordRefModal.addEventListener("click", (event)=>{ if (event.target === wordRefModal) closeWordRefModal(); });
  $("#wordRefConfirm").addEventListener("click", ()=>{
    const rows = state.refRows || [];
    state.selectedArtifacts = new Set(state.refChoice.artifacts);
    state.knowledgeRefs = rows.filter(row=>row.kind === "knowledge" && state.refChoice.knowledge.has(row.id))
      .map(row=>({id:row.id, title:row.title}));
    renderWordRefs();
    closeWordRefModal();
    toast("已加入 " + (state.selectedArtifacts.size + state.knowledgeRefs.length) + " 份参考资料");
  });
  document.addEventListener("keydown", (event)=>{ if (event.key === "Escape" && wordRefModal.classList.contains("open")) closeWordRefModal(); });

  /* ---- local upload ---- */
  $("#wordKbPick").addEventListener("click", openWordRefModal);
  $("#wordLocalUpload").addEventListener("click", ()=>$("#wordLocalFiles").click());
  $("#wordLocalFiles").addEventListener("change", async (event)=>{
    const files = Array.from(event.target.files || []);
    event.target.value = "";
    const markdown = [];
    for (const file of files){
      const name = file.name.toLowerCase();
      if (file.type.startsWith("image/")) state.images.push(file);
      else if (name.endsWith(".docx")) state.templates.push(file);
      else if (name.endsWith(".md") || name.endsWith(".markdown")) markdown.push(file);
      else toast(`已跳过不支持的文件：${file.name}`);
    }
    if (markdown.length){
      const chunks = await Promise.all(markdown.map(file=>file.text()));
      const name = markdown.length > 1 ? "教材合并.md" : markdown[0].name;
      state.textbookFile = new File([chunks.join("\n\n")], name, {type:"text/markdown"});
    }
    renderWordRefs();
    if (files.length) toast("已加入本地资料");
  });

  $("#wordStart").addEventListener("click", async ()=>{
    const button = $("#wordStart");
    const hasTextbook = state.selectedArtifacts.size > 0 || Boolean(state.textbookFile)
      || state.knowledgeRefs.some(ref=>isMarkdownTitle(ref.title));
    if (!hasTextbook){
      toast("请先选择教材来源（PDF→MD 产物、最近产物教材或本地 Markdown）");
      return;
    }
    button.disabled = true;
    state.wordStageHidden = false;
    state.wordStartedAt = 0;
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
        knowledgeRefs: state.knowledgeRefs.map(item=>item.id),
        captions: state.imageCaptions
      };
      const periods = Number($("#wordPeriods").value);
      if (periods) payload.periods = periods;
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
      if (open){
        state.wordStageHidden = false; state.wordStartedAt = 0;
        history.replaceState(null, "", `?feature=word&lesson=${encodeURIComponent(open.dataset.openLesson)}`);
        await pollLesson(open.dataset.openLesson);
      }
      if (retry){
        state.wordStageHidden = false; state.wordStartedAt = 0;
        const result = await api(`/api/pipeline/lesson/jobs/${retry.dataset.retryLesson}/retry`, "POST", {});
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
      if (!state.wordStartedAt && job.created_at){
        state.wordStartedAt = Date.parse(job.created_at) || Date.now();
      }
      if (["queued","running"].includes(job.status)){
        if (!state.wordStageHidden) showWordProgress(job);
        else updateWordProgress(job);
        state.lessonTimer = setTimeout(()=>pollLesson(id), 1400);
        return;
      }
      stopWordClock();
      hideWordProgress();
      renderLessonJob(job);
      $("#wordState").textContent = "";
      await refreshWordJobs();
      state.artifacts = await api("/api/pipeline/artifacts?stage=pdf2md"); renderWordRefs();
      if (job.status === "succeeded"){
        toast("全部学时生成完成");
        setTimeout(()=>{ const box = $("#wordResults"); if (box && !box.hidden) box.scrollIntoView({behavior:"smooth", block:"start"}); }, 120);
      }
      else if (job.status === "partial_failed") toast("部分章节失败；成功学时仍可下载与送入 PPT");
      else if (job.status === "failed") toast("生成失败：" + (job.error || "查看任务日志"));
    } catch (error) { showWordError(error.message); }
  }

  function fmtElapsed(start){
    const sec = Math.max(0, Math.floor((Date.now() - start) / 1000));
    return `${String(Math.floor(sec / 60)).padStart(2, "0")}:${String(sec % 60).padStart(2, "0")}`;
  }
  function stopWordClock(){ if (state.wordClock){ clearInterval(state.wordClock); state.wordClock = null; } }
  function startWordClock(){
    stopWordClock();
    state.wordClock = setInterval(()=>{
      const timer = $("#wordProgressTimer");
      if (timer) timer.textContent = `总进度 ${state.wordPct || 0}% · 已用 ${fmtElapsed(state.wordStartedAt || Date.now())}`;
    }, 1000);
  }
  function showWordProgress(job){
    $("#wordPageHead").hidden = true;
    $("#wordStageGrid").hidden = true;
    $("#wordResults").hidden = true;
    $("#wordProgressStage").hidden = false;
    $("#wordProgressTitle").textContent = "正在生成教案";
    $("#wordProgressFile").textContent = job.source?.textbookName || job.id || "";
    if (!state.wordStartedAt) state.wordStartedAt = Date.now();
    startWordClock();
    updateWordProgress(job);
  }
  function updateWordProgress(job){
    const total = job.progress?.total || 0;
    const current = job.progress?.current || 0;
    let step = 0, pct = 6, label = job.progressText || "准备任务…";
    if (job.status === "queued"){ pct = 5; label = "排队中…"; }
    if (total){ step = 1; pct = 15; label = job.progressText || "拆分学时…"; }
    if (total && current){ step = 2; pct = 15 + Math.round(70 * current / total); label = `生成第 ${current}/${total} 章…`; }
    if (total && current >= total){ step = 3; pct = Math.max(pct, 90); label = "质检验收与打包 Word…"; }
    if (job.status === "succeeded"){ step = 5; pct = 100; label = "完成，正在打开结果…"; }
    else if (job.status === "running" && !total){ pct = Math.max(pct, Math.min(40, 8 + Math.floor((Date.now() - state.wordStartedAt) / 1000 * 0.6))); }
    state.wordPct = pct;
    $("#wordStageBar").style.width = pct + "%";
    $("#wordStageTrack").setAttribute("aria-valuenow", String(pct));
    $("#wordStageText").textContent = label;
    document.querySelectorAll("#wordProgressSteps [data-word-step]").forEach(el=>{
      const index = Number(el.dataset.wordStep);
      el.classList.toggle("done", index < step);
      el.classList.toggle("on", index === step);
    });
    const timer = $("#wordProgressTimer");
    if (timer) timer.textContent = `总进度 ${pct}% · 已用 ${fmtElapsed(state.wordStartedAt || Date.now())}`;
    $("#wordProgress").hidden = false;
    $("#wordProgressBar").style.width = pct + "%";
    $("#wordProgressText").textContent = job.progressText || label;
  }
  function hideWordProgress(){
    $("#wordProgressStage").hidden = true;
    $("#wordPageHead").hidden = false;
    $("#wordStageGrid").hidden = false;
  }

  $("#wordProgressBack").addEventListener("click", ()=>{
    state.wordStageHidden = true;
    hideWordProgress();
    toast("生成在后台继续，完成后会自动显示结果");
  });

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
        <div class="rc-meta">${esc(item.chapter_id || "")} · ${badge(item.status)}${item.revision ? ` · 修订 r${item.revision}` : ""}</div>
        <div class="bar-actions">
          <button class="btn btn-sm" data-docx-edit="${item.index}">编辑</button>
          <a class="btn btn-sm" href="${esc(item.url)}" download>下载 DOCX</a>
          ${item.previewUrl ? `<a class="btn btn-sm" href="${esc(item.previewUrl)}" target="_blank" rel="noopener">查看中间稿</a>` : ""}
          <button class="btn btn-sm btn-accent" data-docx-handoff="${item.index}" ${["ok","ok_with_warnings"].includes(item.status) ? "" : "disabled title='质检未通过，不能送入 PPT'"}>送入 PPT</button>
        </div>
      </article>`).join("");
  }

  $("#wordDocx").addEventListener("click", async (event)=>{
    const edit = event.target.closest("[data-docx-edit]");
    if (edit){
      try { await openDocxEditor(Number(edit.dataset.docxEdit)); }
      catch (error) { toast(error.message); }
      return;
    }
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

  /* ---------- lesson DOCX editor ---------- */
  const docxModal = $("#docxEditModal");
  function docxChanges(){
    const changed = [];
    document.querySelectorAll("#docxEditBlocks textarea").forEach(area=>{
      if (area.value !== area.dataset.original){
        changed.push({id: area.dataset.blockId, text: area.value});
      }
    });
    return changed;
  }
  function refreshDocxCount(){
    const count = docxChanges().length;
    state.docxDirty = count > 0;
    $("#docxEditCount").textContent = `${count} 处修改`;
    document.querySelectorAll("#docxEditBlocks .docx-block").forEach(block=>{
      const area = block.querySelector("textarea");
      block.classList.toggle("changed", area.value !== area.dataset.original);
    });
    $("#docxEditSave").disabled = !count;
  }
  async function openDocxEditor(index){
    if (!state.activeLesson) return;
    const data = await api(`/api/pipeline/lesson/jobs/${state.activeLesson.id}/docx/${index}/blocks`);
    state.docxIndex = index;
    state.docxDirty = false;
    const box = $("#docxEditBlocks");
    box.innerHTML = "";
    const blocks = (data.blocks || []).filter(block => String(block.text || "").trim());
    blocks.forEach((block, position)=>{
      const wrap = document.createElement("div");
      wrap.className = "docx-block";
      const label = document.createElement("label");
      label.textContent = block.kind === "cell" ? `表格文字 ${position + 1}` : `段落 ${position + 1}`;
      const area = document.createElement("textarea");
      area.value = block.text;
      area.dataset.blockId = block.id;
      area.dataset.original = block.text;
      area.rows = Math.min(6, Math.max(1, Math.ceil(block.text.length / 52)));
      area.addEventListener("input", refreshDocxCount);
      wrap.append(label, area);
      box.append(wrap);
    });
    $("#docxEditTitle").textContent = `编辑教案 Word · ${data.title}`;
    $("#docxEditHint").textContent = `共 ${blocks.length} 个可编辑段落；保存后生成新修订，原文件保留`;
    refreshDocxCount();
    docxModal.classList.add("open");
    setTimeout(()=>{ const first = box.querySelector("textarea"); if (first) first.focus(); }, 80);
  }
  function closeDocxEditor(force){
    if (!force && state.docxDirty && !confirm("有未保存的修改，确定关闭？")) return;
    docxModal.classList.remove("open");
    state.docxDirty = false;
  }
  $("#docxEditClose").addEventListener("click", ()=>closeDocxEditor(false));
  $("#docxEditCancel").addEventListener("click", ()=>closeDocxEditor(false));
  docxModal.addEventListener("click", (event)=>{ if (event.target === docxModal) closeDocxEditor(false); });
  $("#docxEditSave").addEventListener("click", async ()=>{
    const changes = docxChanges();
    if (!changes.length){ toast("没有修改"); return; }
    const button = $("#docxEditSave");
    button.disabled = true;
    button.textContent = "保存中…";
    try {
      const result = await api(`/api/pipeline/lesson/jobs/${state.activeLesson.id}/docx/${state.docxIndex}/edit`, "POST", {blocks: changes});
      state.activeLesson = result.job;
      state.docxDirty = false;
      closeDocxEditor(true);
      renderLessonJob(result.job);
      toast(`已保存修订 r${result.edit.revision}（${result.edit.changed} 处）`);
    } catch (error) { toast(error.message); }
    finally { button.disabled = false; button.textContent = "保存修订"; }
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
        }
        state.artifacts = await api("/api/pipeline/artifacts?stage=pdf2md");
        renderWordRefs();
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