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
    activeLesson:null, lessonTimer:null,
    initialized:{pdf:false, word:false}
  };

  /* ================= PDF → MD ================= */
  const pdfFileInput = $("#pdfFile");
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
    const ocr = job.result?.ocr
      ? ` · 本机 OCR ${job.result.ocrPages ?? ""} 页${job.result.ocrConfidence ? "（均值 " + job.result.ocrConfidence + "）" : ""}`
      : "";
    $("#pdfResultMeta").textContent = `${job.result?.pages ?? ""} 页 · ${job.result?.images ?? 0} 张图片 · 共 ${job.chapters.length} 章${ocr} · ${job.sha256 ? "SHA " + job.sha256.slice(0,12) : ""}`;
    $("#pdfChapters").innerHTML = job.chapters.map(chapter => `
      <article class="result-card">
        <h3>${esc(chapter.title)}</h3>
        <div class="rc-meta">第 ${pageRange(chapter.pages)} 页 · ${chapter.chars} 字符 · ${chapter.imageCount} 张图</div>
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
      meta:`知识库 · ${isDocxTitle(ref.title) ? "DOCX 模板" : "Markdown 教材"}`}));
    if (state.textbookFile) items.push({kind:"textbook", title:state.textbookFile.name,
      meta:`本地上传 · ${fileSize(state.textbookFile.size)}`});
    state.templates.forEach((file,index)=>items.push({kind:"template", index, title:file.name,
      meta:`本地上传模板 · ${fileSize(file.size)}`}));
    state.images.forEach((file,index)=>items.push({kind:"image", index, title:file.name,
      meta:`本地上传配图 · ${fileSize(file.size)}`, caption:state.imageCaptions[file.name] || ""}));

    box.classList.toggle("has-items", items.length > 0);
    if (!items.length){
      box.innerHTML = '<div class="empty-ref">还没有引用任何资料，从知识库选择或上传本地文件</div>';
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
        meta:`知识库 · ${item.meta || ""}`, label:"教材"}))
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
      toast("请先选择教材来源（PDF→MD 产物、知识库教材或本地 Markdown）");
      return;
    }
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
      if (open){ history.replaceState(null, "", `?feature=word&lesson=${encodeURIComponent(open.dataset.openLesson)}`); await pollLesson(open.dataset.openLesson); }
      if (retry){
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
      state.artifacts = await api("/api/pipeline/artifacts?stage=pdf2md"); renderWordRefs();
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