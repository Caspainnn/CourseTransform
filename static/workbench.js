
(function(){
  "use strict";

  /* ================= 数据层 ================= */
  const STYLE_MAP = {academic:"简约学术", warm:"温暖人文", biz:"严谨清晰", mono:"极简黑白"};
  let KB_DATA = [];
  const INITIAL_PARAMS = new URLSearchParams(location.search);

  const state = {
    step:1,
    feature:"ppt",
    count:12,
    style:"academic",
    template:null,
    refs:[],
    localRefs:[],
    outline:[],
    slides:[],
    current:0,
    generated:false,
    kbTab:"all",
    kbSelected:[]
  };

  const $ = (sel, root) => (root||document).querySelector(sel);
  const $$ = (sel, root) => Array.prototype.slice.call((root||document).querySelectorAll(sel));
  const esc = (str) => String(str==null?"":str).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  function originLabel(record){
    const origin = record && record.origin;
    if(!origin) return "";
    if(origin.stage === "pdf2md") return "PDF→MD 产物";
    if(origin.stage === "lesson" || origin.jobId) return "教案任务产物";
    return "";
  }

  /* ================= Toast ================= */
  const toastWrap = $("#toastWrap");
  function toast(msg){
    const el = document.createElement("div");
    el.className = "toast";
    el.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round"><path d="m5 13 4 4L19 7"/></svg><span>'+esc(msg)+'</span>';
    toastWrap.appendChild(el);
    setTimeout(()=>{ el.classList.add("out"); setTimeout(()=>el.remove(), 260); }, 2400);
  }

  /* ================= 大纲生成 ================= */

  /* ================= 步骤切换 ================= */
  function goStep(n){
    state.step = n;
    $$(".panel").forEach(p => { p.hidden = true; });
    const panel = $("#panel-"+n);
    if(panel) panel.hidden = false;
    $$(".step").forEach(b => {
      const v = Number(b.dataset.step);
      b.classList.toggle("active", v===n);
      b.classList.toggle("done", v<n);
      b.setAttribute("aria-selected", v===n ? "true" : "false");
    });
    $$(".step-line").forEach((line,i)=>line.classList.toggle("done", i < n-1));
    window.scrollTo({top:0, behavior:"smooth"});
  }

  /* ================= 参考资料 ================= */
  function getRef(id){ return KB_DATA.find(k => k.id===id); }
  function renderRefs(){
    const box = $("#refList");
    const items = state.refs.map(id=>getRef(id)).filter(Boolean)
      .map(r=>Object.assign({}, r, {local:false}))
      .concat(state.localRefs);
    if(!items.length){
      box.innerHTML = '<div class="empty-ref"><svg viewBox="0 0 24 24" fill="none" stroke-width="1.6" stroke-linecap="round"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H19v15H6.5A2.5 2.5 0 0 0 4 20.5V5.5Z"/><path d="M8 7h6"/></svg><div>还没有引用任何资料，从最近产物选择或上传本地文件</div></div>';
    } else {
      box.innerHTML = items.map(r=>{
        const tag = r.type==="jiaoan" ? '<span class="tag tag-jiaoan">教案</span>'
          : r.type==="jiaocai" ? '<span class="tag tag-jiaocai">教材</span>'
          : '<span class="tag tag-local">本地</span>';
        const attr = r.local ? 'data-remove-local="'+r.id+'"' : 'data-remove-ref="'+r.id+'"';
        return '<div class="ref-item'+(r.local?" is-local":"")+'">'+
          '<div class="ref-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3v5h5"/><path d="M19 8v11a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h7l5 5Z"/></svg></div>'+
          '<div class="ref-body"><div class="ref-title">'+esc(r.title)+'</div><div class="ref-meta">'+tag+'<span>'+esc(r.meta)+'</span>'+(originLabel(r)?'<span class="tag tag-local">'+originLabel(r)+'</span>':'')+'</div></div>'+
          '<button class="ref-x" '+attr+' aria-label="移除 '+esc(r.title)+'"><svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg></button>'+
        '</div>';
      }).join("");
    }
    const total = state.refs.length + state.localRefs.length;
    $("#sumRefs").textContent = total;
    $("#s2Refs").textContent = total + " 份";
  }

  $("#refList").addEventListener("click", e=>{
    const kbBtn = e.target.closest("[data-remove-ref]");
    if(kbBtn){
      state.refs = state.refs.filter(id => id !== kbBtn.dataset.removeRef);
      renderRefs();
      toast("已移除该参考资料");
      return;
    }
    const localBtn = e.target.closest("[data-remove-local]");
    if(localBtn){
      state.localRefs = state.localRefs.filter(f => f.id !== localBtn.dataset.removeLocal);
      renderRefs();
      toast("已移除本地文件");
    }
  });

  /* ================= 本地参考资料上传 ================= */
  const refFileInput = $("#refFileInput");
  $("#btnUploadRef").addEventListener("click", ()=>refFileInput.click());

  refFileInput.addEventListener("change", async()=>{
    const epoch=taskEpoch, files=Array.from(refFileInput.files||[]);
    refFileInput.value="";
    for(const file of files){
      if(epoch!==taskEpoch)return;
      try { const result=await uploadFile(file); if(epoch!==taskEpoch)return; state.localRefs.push({...result,local:true,type:"local"}); }
      catch(error){ report(error); }
    }
    if(epoch!==taskEpoch)return;
    renderRefs(); persistForm().catch(report);
  });

  /* ================= 知识库弹窗 ================= */
  const kbModal = $("#kbModal");
  function openKB(){
    state.kbSelected = state.refs.slice();
    state.kbTab = "all";
    $("#kbSearch").value = "";
    $$(".kb-tab").forEach(t => t.classList.toggle("on", t.dataset.kbtab==="all"));
    renderKB();
    kbModal.classList.add("open");
    document.body.style.overflow = "hidden";
    setTimeout(()=>$("#kbSearch").focus(), 80);
  }
  function closeKB(){
    kbModal.classList.remove("open");
    document.body.style.overflow = "";
  }
  function renderKB(){
    const q = $("#kbSearch").value.trim().toLowerCase();
    const list = KB_DATA.filter(r => (state.kbTab==="all" || r.type===state.kbTab) &&
      (!q || (r.title+r.desc).toLowerCase().indexOf(q) > -1));
    $("#kbList").innerHTML = list.length ? list.map(r=>{
      const on = state.kbSelected.indexOf(r.id) > -1;
      const tag = r.type==="jiaoan" ? '<span class="tag tag-jiaoan">教案</span>' : '<span class="tag tag-jiaocai">教材</span>';
      return '<div class="kb-row'+(on?" on":"")+'" data-kb-id="'+r.id+'" role="option" aria-selected="'+on+'" tabindex="0">'+
        '<span class="kb-check"><svg viewBox="0 0 24 24" fill="none" stroke-width="3" stroke-linecap="round"><path d="m5 13 4 4L19 7"/></svg></span>'+
        '<div class="ref-body"><b>'+esc(r.title)+'</b><p>'+esc(r.desc)+'</p><div class="ref-meta">'+tag+'<span>'+esc(r.meta)+'</span>'+(originLabel(r)?'<span class="tag tag-local">'+originLabel(r)+'</span>':'')+'</div></div>'+
      '</div>';
    }).join("") : '<div class="empty-ref" style="margin-top:6px;">没有匹配的资料，换个关键词试试</div>';
    $("#kbPicked").textContent = state.kbSelected.length;
    const countBy = t => KB_DATA.filter(r=>r.type===t).length;
    $("#kbCountJ").textContent = countBy("jiaoan");
    $("#kbCountC").textContent = countBy("jiaocai");
    $("#kbCountAll").textContent = KB_DATA.length;
  }
  function toggleKb(id){
    const i = state.kbSelected.indexOf(id);
    if(i > -1) state.kbSelected.splice(i,1);
    else {
      if(state.kbSelected.length >= 6){ toast("最多引用 6 份参考资料"); return; }
      state.kbSelected.push(id);
    }
    renderKB();
  }
  $$(".kb-tab").forEach(t=>t.addEventListener("click", ()=>{
    state.kbTab = t.dataset.kbtab;
    $$(".kb-tab").forEach(x=>x.classList.toggle("on", x===t));
    renderKB();
  }));
  $("#kbSearch").addEventListener("input", renderKB);
  $("#kbList").addEventListener("click", e=>{
    const row = e.target.closest("[data-kb-id]");
    if(row) toggleKb(row.dataset.kbId);
  });
  $("#kbList").addEventListener("keydown", e=>{
    const row = e.target.closest("[data-kb-id]");
    if(row && (e.key==="Enter" || e.key===" ")){ e.preventDefault(); toggleKb(row.dataset.kbId); }
  });

  $("#kbRefresh").addEventListener("click", ()=>refreshKnowledge().catch(report));

  $("#btnOpenKB").addEventListener("click", openKB);
  $("#kbClose").addEventListener("click", closeKB);
  $("#kbCancel").addEventListener("click", closeKB);
  $("#kbConfirm").addEventListener("click", ()=>{
    state.refs = state.kbSelected.slice();
    renderRefs(); closeKB();
    toast("已加入 " + state.refs.length + " 份参考资料");
  });
  kbModal.addEventListener("click", e=>{ if(e.target===kbModal) closeKB(); });
  document.addEventListener("keydown", e=>{ if(e.key==="Escape" && kbModal.classList.contains("open")) closeKB(); });

  /* ================= 表单 ================= */
  const countInput = $("#fCount"), countRange = $("#fCountRange");
  function setCount(v, from){
    v = Math.min(40, Math.max(6, Number(v)||12));
    state.count = v;
    countInput.value = v;
    countRange.value = v;
    $("#sumCount").textContent = v;
    const pct = (v-6)/(40-6)*100;
    countRange.style.background = "linear-gradient(90deg,var(--accent) 0%,var(--accent) "+pct+"%,#e7e2d9 "+pct+"%,#e7e2d9 100%)";
    $$(".chip-btn[data-count]").forEach(b=>b.classList.toggle("on", Number(b.dataset.count)===v));
    if(!from) { /* 保持静默 */ }
  }
  countInput.addEventListener("input", ()=>setCount(countInput.value));
  countInput.addEventListener("blur", ()=>setCount(countInput.value));
  countRange.addEventListener("input", ()=>setCount(countRange.value));
  $$(".chip-btn[data-count]").forEach(b=>b.addEventListener("click", ()=>setCount(b.dataset.count)));

  $$(".style-opt").forEach(opt=>opt.addEventListener("click", ()=>{
    state.style = opt.dataset.style;
    $$(".style-opt").forEach(o=>{
      const on = o===opt;
      o.classList.toggle("on", on);
      o.setAttribute("aria-checked", on?"true":"false");
    });
    $("#sumStyle").textContent = STYLE_MAP[state.style];
    $("#s2Style").textContent = STYLE_MAP[state.style];
  }));

  /* ================= 模板上传 ================= */
  const tplSlot = $("#tplSlot");
  function setStyleLock(locked){
    const picker = $(".style-picker");
    if(picker) picker.classList.toggle("locked", locked);
    const hint = $("#styleLockHint");
    if(hint) hint.hidden = !locked;
    $$(".style-opt").forEach(o=>{
      o.disabled = locked;
      o.setAttribute("aria-disabled", locked ? "true" : "false");
    });
    const label = locked ? "跟随模板" : STYLE_MAP[state.style];
    $("#sumStyle").textContent = label;
    $("#s2Style").textContent = label;
  }
  function showTemplate(file){
    const size = file.size ? (file.size/1024/1024).toFixed(1)+" MB" : "本地文件";
    tplSlot.innerHTML = '<div class="tpl-file">'+
      '<div class="tpl-thumb" aria-hidden="true"></div>'+
      '<div class="tpl-meta"><b>'+esc(file.name)+'</b><span>'+esc(size)+' · 已解析 '+esc(file.layoutCount)+' 个版式</span></div>'+
      '<button class="btn btn-sm" id="tplReplace">替换</button>'+
      '<button class="ref-x" id="tplRemove" aria-label="移除模板"><svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg></button>'+
    '</div>'+
    '<div class="notice"><svg viewBox="0 0 24 24" fill="none" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 8h.01M11 12h1v4h1"/></svg><span>模板已锁定，本次任务将直接套用。暂不支持模板库管理。</span></div>';
    state.template = file;
    $("#s2Tpl").textContent = "已上传";
    setStyleLock(true);
    $("#tplReplace").addEventListener("click", ()=>{
      const input = document.createElement("input");
      input.type = "file"; input.accept = ".pptx,.potx";
      input.addEventListener("change", ()=>{ if(input.files[0]) handleTpl(input.files[0]); });
      input.click();
    });
    $("#tplRemove").addEventListener("click", ()=>{
      state.template = null;
      $("#s2Tpl").textContent = "未上传";
      setStyleLock(false);
      renderDrop();
      toast("已移除模板");
    });

  }

  async function handleTpl(file){
    const epoch=taskEpoch;
    try { const result=await uploadFile(file,"template"); if(epoch!==taskEpoch)return; showTemplate(result); await persistForm(); }
    catch(error){ report(error); }
  }

  function renderDrop(){
    tplSlot.innerHTML = '<label class="drop" id="dropZone" for="tplInput">'+
      '<svg viewBox="0 0 24 24" fill="none" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 16V4"/><path d="m7.5 8.5 4.5-4.5 4.5 4.5"/><path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/></svg>'+
      '<div class="drop-label">拖入模板，或<b>点击上传</b></div>'+
      '<div class="drop-hint">支持 .pptx / .potx · 建议 ≤ 30MB</div>'+
      '<input type="file" id="tplInput" accept=".pptx,.potx" aria-label="上传 PPT 模板"></label>';
    bindDrop();
  }
  function bindDrop(){
    const input = $("#tplInput"), zone = $("#dropZone");
    if(!input || !zone) return;
    input.addEventListener("change", ()=>{ if(input.files[0]) handleTpl(input.files[0]); });
    ["dragenter","dragover"].forEach(ev=>zone.addEventListener(ev, e=>{ e.preventDefault(); zone.classList.add("over"); }));
    ["dragleave","drop"].forEach(ev=>zone.addEventListener(ev, e=>{ e.preventDefault(); zone.classList.remove("over"); }));
    zone.addEventListener("drop", e=>{
      const f = e.dataTransfer && e.dataTransfer.files[0];
      if(f) handleTpl(f);
    });
  }
  bindDrop();

  /* ================= 大纲渲染 ================= */
  function renderOutline(){
    const list = $("#outlineList");
    list.innerHTML = state.outline.map((s,i)=>
      '<div class="slide-card" data-idx="'+i+'">'+
        '<span class="idx">'+String(i+1).padStart(2,"0")+'</span>'+
        '<div class="slide-main">'+
          '<input class="slide-title-input" data-title="'+i+'" value="'+esc(s.title)+'" aria-label="第 '+(i+1)+' 页标题">'+
          (s.lead ? '<div class="hint" style="font-size:11px;color:var(--ink-3);margin-top:3px;">'+esc(s.lead)+'</div>' : '')+
          '<div class="points">'+ (s.points||[]).map(p=>'<div class="point"><span>'+esc(p)+'</span></div>').join("") +'</div>'+
        '</div>'+
        '<div class="slide-tools">'+
          '<button class="mini-btn" data-move="up" data-i="'+i+'" aria-label="上移"><svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round"><path d="M6 15l6-6 6 6"/></svg></button>'+
          '<button class="mini-btn" data-move="down" data-i="'+i+'" aria-label="下移"><svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round"><path d="M6 9l6 6 6-6"/></svg></button>'+
          '<button class="mini-btn" data-del="'+i+'" aria-label="删除此页"><svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg></button>'+
        '</div>'+
      '</div>'
    ).join("");
    $("#outlineCount").textContent = "共 " + state.outline.length + " 页";
    $("#s2Count").textContent = state.outline.length + " 页";
  }
  $("#outlineList").addEventListener("input", e=>{
    const input = e.target.closest("[data-title]");
    if(input){
      state.outline[Number(input.dataset.title)].title = input.value;
      $("#deckTitle").textContent = state.outline[0] ? state.outline[0].title : ""; scheduleOutlineSave();
    }
  });
  $("#outlineList").addEventListener("click", e=>{
    const mv = e.target.closest("[data-move]");
    const del = e.target.closest("[data-del]");
    if(mv){
      const i = Number(mv.dataset.i), j = mv.dataset.move==="up" ? i-1 : i+1;
      if(j<0 || j>=state.outline.length) return;
      const t = state.outline[i]; state.outline[i]=state.outline[j]; state.outline[j]=t;
      renderOutline(); scheduleOutlineSave();
    }
    if(del){
      if(state.outline.length <= 3){ toast("至少保留 3 页，才能生成完整课堂 PPT"); return; }
      state.outline.splice(Number(del.dataset.del),1);
      renderOutline(); scheduleOutlineSave();
      toast("已删除该页");
    }
  });
  $("#btnAddSlide").addEventListener("click", ()=>{
    state.outline.push({id:"p"+crypto.randomUUID().replaceAll("-","").slice(0,12),type:"content", kicker:"新增页", title:"新的一页 · 待补充标题", lead:"点击标题直接编辑", points:["补充要点 1","补充要点 2"]});
    renderOutline(); scheduleOutlineSave();
    const inputs = $$(".slide-title-input");
    const last = inputs[inputs.length-1];
    if(last){ last.focus(); last.select(); last.scrollIntoView({block:"center", behavior:"smooth"}); }
    toast("已添加一页，直接改标题即可");
  });

  function addChat(box, text, who){
    const el = document.createElement("div");
    el.className = "chat-msg" + (who==="me" ? " me" : "");
    el.textContent = text;
    box.appendChild(el);
    box.scrollTop = box.scrollHeight;
  }

  /* ================= 基础信息 → 大纲 ================= */
  function readForm(){
    return {
      topic: $("#fTopic").value.trim() || "未命名课题",
      teacher: $("#fTeacher").value.trim() || "教师",
      audience: $("#fAudience").value.trim() || "课堂",
      duration: $("#fDuration").value,
      extra: $("#fExtra").value.trim()
    };
  }
  function updateStep2Summary(){
    const f = readForm();
    $("#s2Topic").textContent = f.topic.length > 16 ? f.topic.slice(0,16)+"…" : f.topic;
    $("#s2Style").textContent = state.template ? "跟随模板" : STYLE_MAP[state.style];
    $("#s2Refs").textContent = (state.refs.length + state.localRefs.length) + " 份";
    $("#s2Tpl").textContent = state.template ? "已上传" : "未上传";
    $("#s2Count").textContent = state.outline.length + " 页";
  }

  function buildFromForm(){ renderOutline(); updateStep2Summary(); }

  /* ---- 大纲制作过程进度 ---- */
  let outlineTimer = null;
  function showOutlineReady(){
    const gen = $("#outlineGen"), ready = $("#outlineReady");
    if(gen) gen.hidden = true;
    if(ready) ready.hidden = false;
  }

  async function runOutlineGeneration(instruction=""){
    try {
      await persistForm();
      if(!state.taskId) throw Error("请填写课题名称");
      await flushOutline();
      goStep(2); $("#outlineReady").hidden=true; $("#outlineGen").hidden=false;
      await api(`/api/tasks/${state.taskId}/outline`,"POST",{instruction});
      await followTask();
    } catch(error){ showOutlineReady(); report(error); }
  }

  $("#btnToOutline").addEventListener("click", ()=>{
    if(!$("#fTopic").value.trim()){ toast("先填写课题名称"); $("#fTopic").focus(); return; }
    runOutlineGeneration();
  });
  $("#btnBackTo1").addEventListener("click", ()=>goStep(1));
  $("#btnBackTo2").addEventListener("click", ()=>{ showOutlineReady(); goStep(2); });


  $("#btnRegen").addEventListener("click", async()=>{
    const instruction=$("#regenPrompt").value.trim();
    if(!instruction){toast("请填写修改要求");return;}
    addChat($("#regenLog"),instruction,"me");
    await runOutlineGeneration(instruction);
    $("#regenPrompt").value="";
  });

  /* ================= PPT 渲染 ================= */

  function actualImage(slide){
    return slide.previewUrl ? '<img src="'+esc(slide.previewUrl)+'" alt="'+esc(slide.title)+'" style="width:100%;height:100%;object-fit:contain">' : '<div class="empty-ref">此版本尚无真实预览</div>';
  }

  function renderDeck(){

    const list = $("#deckList");
    list.innerHTML = state.slides.map((s,i)=>
      '<button class="thumb'+(i===0?" on":"")+'" data-slide="'+i+'" role="option" aria-selected="'+(i===0)+'">'+
        '<div class="thumb-canvas" aria-hidden="true">'+actualImage(s)+'</div>'+
        '<div class="thumb-meta"><span class="thumb-no">'+String(i+1).padStart(2,"0")+'</span><span class="thumb-t">'+esc(s.title)+'</span></div>'+
      '</button>'
    ).join("");
    $("#sumSlides").textContent = state.slides.length;
    $("#s2Count").textContent = state.slides.length + " 页";
    selectSlide(0);
  }
  function selectSlide(i){
    if(!state.slides.length) return;
    state.current = Math.max(0, Math.min(state.slides.length-1, i));
    const s = state.slides[state.current];
    $("#previewFrame").innerHTML = actualImage(s);
    $("#previewFrame").style.background="#fff";
    $("#previewMeta").textContent = "第 " + (state.current+1) + " 页 / 共 " + state.slides.length + " 页";
    $$(".thumb").forEach((t,idx)=>{
      const on = idx===state.current;
      t.classList.toggle("on", on);
      t.setAttribute("aria-selected", on ? "true" : "false");
      if(on && t.scrollIntoView) t.scrollIntoView({block:"nearest", behavior:"smooth"});
    });
  }
  $("#deckList").addEventListener("click", e=>{
    const t = e.target.closest("[data-slide]");
    if(t) selectSlide(Number(t.dataset.slide));
  });
  $("#deckList").addEventListener("keydown", e=>{
    if(e.key==="ArrowDown" || e.key==="ArrowUp"){
      e.preventDefault();
      selectSlide(state.current + (e.key==="ArrowDown"?1:-1));
    }
  });
  $("#btnPrev").addEventListener("click", ()=>selectSlide(state.current-1));
  $("#btnNext").addEventListener("click", ()=>selectSlide(state.current+1));

  /* ================= 生成流程 ================= */

  async function runGeneration(){
    try {
      await persistForm(); await flushOutline();
      if(state.task && state.task.currentVersion){
        const created=await api("/api/tasks","POST",{...taskForm(),outline:state.outline});
        state.taskId=created.id; state.task=created; state.outlineRevision=created.outlineRevision;
        localStorage.setItem("pptnative-task",created.id);
      }
      goStep(3); $("#genStage").hidden=false; $("#deckStage").hidden=true;
      await api(`/api/tasks/${state.taskId}/generate`,"POST",{});
      await followTask();
    } catch(error){ report(error); }
  }

  $("#btnGenerate").addEventListener("click", ()=>{
    $$(".step")[2].classList.add("done");
    runGeneration();
  });
  $("#btnBackOutline").addEventListener("click", ()=>{
    showOutlineReady();
    goStep(2);
  });

  /* ================= 单页修改 ================= */

  function setPreviewBusy(on,label){
    const overlay=$("#previewBusy"); if(!overlay) return;
    state.reviseBusy=!!on;
    overlay.hidden=!on;
    overlay.closest(".preview-frame").classList.toggle("is-busy",!!on);
    overlay.closest(".preview-frame").setAttribute("aria-busy", on?"true":"false");
    if(label) $("#previewBusyText").textContent=label;
    $("#reviseInput").disabled=!!on;
    $("#btnRevise").disabled=!!on;
  }
  $("#btnRevise").addEventListener("click", async()=>{
    const text=$("#reviseInput").value.trim(), slide=state.slides[state.current];
    if(!text||!slide){toast("请填写修改要求");return;}
    if(state.reviseBusy) return;
    setPreviewBusy(true,`正在修改第 ${state.current+1} 页…`);
    const guard=setTimeout(()=>{setPreviewBusy(false);toast("修改仍在后台进行，稍后刷新即可查看结果");},10*60*1000);
    try {
      await api(`/api/tasks/${state.taskId}/slides/${state.current}/revise`,"POST",{
        text,pageId:slide.id,base_version:state.version});
      addChat($("#reviseLog"),text,"me"); $("#reviseInput").value="";
      await followTask();
      const pending=state.task?.result;
      if(pending?.status==="needs_input"&&pending.question){
        addChat($("#reviseLog"),pending.question,"ai"); toast(pending.question);
      }
    } catch(error){ report(error); }
    finally { clearTimeout(guard); setPreviewBusy(false); $("#reviseInput").focus(); }
  });
  async function downloadPPT(confirmVersion=false){
    try {
      if(!state.version) throw Error("尚未生成可交付版本");
      if(confirmVersion){ state.task=await api(`/api/tasks/${state.taskId}/confirm`,"POST",{base_version:state.version}); }
      const a=document.createElement("a");a.href=`/api/tasks/${state.taskId}/versions/${state.version}/download`;
      a.download="";a.click();
    } catch(error){ report(error); }
  }
  $("#btnDownloadTop").addEventListener("click", ()=>downloadPPT(false));
  $("#btnConfirmDownload").addEventListener("click", ()=>downloadPPT(true));

  function resetTaskState(){
    setBusyControls(false); ["#btnToOutline","#btnGenerate","#btnRegen"].forEach(s=>$(s).disabled=false);
    $("#taskStatus").hidden=true;$("#taskActions").hidden=true;
    state.taskId=null; state.task=null; state.version=null; state.outlineRevision=0; state.template=null; state.refs=[]; localStorage.removeItem("pptnative-task"); renderDrop(); setStyleLock(false);
    state.outline = []; state.slides = []; state.current = 0; state.generated = false;
    state.localRefs = []; $("#taskError").hidden=true;
    $("#fTopic").value = ""; $("#fExtra").value = "";
    $("#reviseInput").value = ""; $("#regenPrompt").value = "";
    $("#regenLog").innerHTML = ""; $("#reviseLog").innerHTML = "";
    $("#deckStage").hidden = true; $("#genStage").hidden = false;
    renderOutline();
    renderRefs();
    showOutlineReady();
  }
  async function newTaskFlow(){
    try{await settleCurrent();}catch(error){report(error);return;}
    taskEpoch++;formDirty=false;outlineDirty=false;history.replaceState(null,"",location.pathname);
    pollEpoch++;
    enterFlow();
    resetTaskState();
    goStep(1);
    toast("已新建任务，请填写基础信息");
    setTimeout(()=>$("#fTopic").focus(), 300);
  }
  $("#btnNewTask").addEventListener("click", newTaskFlow);
  $("#btnNewTaskDraft").addEventListener("click", newTaskFlow);
  $("#btnNewTaskDraft2").addEventListener("click", newTaskFlow);
  $("#btnDraftHistory").addEventListener("click", ()=>showDrafts());

  /* ---- 草稿卡片 ---- */

  async function openDraft(id){
    try {
      await settleCurrent();
      const epoch=++taskEpoch;
      pollEpoch++;
      const task=await api("/api/tasks/"+id);if(epoch!==taskEpoch)return;
      applyTask(task); enterFlow();
      if(["queued","running"].includes(state.task.status)){
        showPendingOperation(state.task);
        await followTask();
      }
      else if(state.generated){goStep(3);}
      else if(state.outline.length){showOutlineReady();goStep(2);}
      else goStep(1);
    } catch(error){report(error);}
  }
  document.querySelector(".draft-grid").addEventListener("click", e=>{
    const open=e.target.closest("[data-open-task]");
    if(open) openDraft(open.dataset.openTask);
  });

  $("#btnResetStep1").addEventListener("click", ()=>{
    $("#fTopic").value = "";
    $("#fTeacher").value = ""; $("#fDuration").value = "45 分钟";
    $("#fAudience").value = "";
    $("#fExtra").value = "";
    setCount(12);
    state.style = "academic";
    $$(".style-opt").forEach(o=>{ const on=o.dataset.style==="academic"; o.classList.toggle("on",on); o.setAttribute("aria-checked", on?"true":"false"); });
    state.refs = [];
    state.localRefs = []; $("#taskError").hidden=true;
    renderRefs();
    setStyleLock(!!state.template);
    toast("已恢复默认设置");
  });

  /* ================= 视图切换：草稿箱 / 创建流程 ================= */
  function highlightNav(){
    $$(".nav-item").forEach(b=>{
      const on = b.dataset.feature === "ppt";
      b.classList.toggle("active", on);
      if(on) b.setAttribute("aria-current","page"); else b.removeAttribute("aria-current");
    });
  }
  function hidePipelineViews(){
    const pdf=$("#view-pdf"), word=$("#view-word");
    if(pdf) pdf.hidden = true;
    if(word) word.hidden = true;
  }
  async function showDrafts(){
    try{await settleCurrent();}catch(error){report(error);return;}
    pollEpoch++;history.replaceState(null,"",location.pathname);
    refreshDrafts().catch(report);
    $("#view-drafts").hidden = false;
    $("#view-ppt").hidden = true;
    $("#view-wip").hidden = true;
    hidePipelineViews();
    $("#crumbCurrent").innerHTML = "<b>历史草稿</b>";
    highlightNav();
    window.scrollTo({top:0, behavior:"smooth"});
  }
  function enterFlow(){
    $("#view-drafts").hidden = true;
    $("#view-wip").hidden = true;
    $("#view-ppt").hidden = false;
    hidePipelineViews();
    $("#crumbCurrent").innerHTML = "<b>教案 + 教材转 PPT</b>";
    highlightNav();
    window.scrollTo({top:0, behavior:"smooth"});
  }

  const accountPopover = $("#accountPopover"), accountModal = $("#accountModal");
  function renderAccountSummary(){
    $("#apKbCount").textContent = KB_DATA.length + " 份";
    $("#apKbBadge").textContent = String(KB_DATA.length);
    if(!KB_DATA.length) refreshKnowledge().then(renderAccountSummary).catch(()=>{});
    fetch("/api/health").then(response=>response.json()).then(health=>{
      const model = $("#apModelState");
      if(model) model.textContent = health.modelConfigured ? "已配置" : "未配置";
    }).catch(()=>{ const model = $("#apModelState"); if(model) model.textContent = "未知"; });
  }
  function openAccountPopover(){
    $("#btnLocalUser").setAttribute("aria-expanded", "true");
    accountPopover.hidden = false;
    renderAccountSummary();
    positionAccountPopover();
    setTimeout(()=>{ const first = accountPopover.querySelector("[data-account-panel]"); if(first) first.focus(); }, 50);
  }
  function positionAccountPopover(){
    const rect = $("#btnLocalUser").getBoundingClientRect();
    const width = Math.min(320, window.innerWidth - 16);
    accountPopover.style.width = width + "px";
    const height = accountPopover.offsetHeight || 320;
    const left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8));
    let top = rect.top - height - 10;
    if(top < 8) top = rect.bottom + 10;
    top = Math.max(8, Math.min(top, window.innerHeight - height - 8));
    accountPopover.style.left = left + "px";
    accountPopover.style.top = top + "px";
  }
  function closeAccountPopover(){
    accountPopover.hidden = true;
    $("#btnLocalUser").setAttribute("aria-expanded", "false");
  }
  function closeAccountModal(){
    accountModal.classList.remove("open");
    document.body.style.overflow = "";
  }
  function renderAccountModal(kind){
    const body = $("#accountModalBody");
    if(kind === "user"){
      body.innerHTML = `
        <div class="acct-user"><div class="avatar" aria-hidden="true">本</div><div><b>当前用户</b><span>教学工作台</span></div></div>
        <div class="acct-row"><span>账户体系</span><b>未启用（单用户）</b></div>
        <div class="acct-row"><span>数据位置</span><b>项目 data/ 目录</b></div>
        <div class="acct-row"><span>资料范围</span><b>本地存储 · 无云同步</b></div>
        <p class="acct-note">不提供账号、多租户或云同步；资料与产物按保留期清理，请及时下载。</p>`;
      return;
    }
    if(kind === "knowledge"){
      const rows = KB_DATA.length ? KB_DATA.map(item =>
        `<div class="acct-list-row"><span>${esc(item.title)}</span><em>${item.type === "jiaoan" ? "教案" : "教材"}</em></div>`
      ).join("") : '<div class="empty-ref">暂无资料</div>';
      body.innerHTML = `
        <div class="acct-toolbar"><span>共 <b>${KB_DATA.length}</b> 份资料</span><button class="btn btn-sm" id="acctKbRefresh" type="button">刷新</button></div>
        <div class="acct-list">${rows}</div>
        <p class="acct-note">最近产物仅用于本次/近期交接；请及时下载 ZIP 自行保存。教案与 PPT 阶段可选择近期产物。</p>`;
      $("#acctKbRefresh").addEventListener("click", async ()=>{
        try{ await refreshKnowledge(); renderAccountModal("knowledge"); renderAccountSummary(); }
        catch(error){ report(error); }
      });
      if(!KB_DATA.length) refreshKnowledge().then(()=>renderAccountModal("knowledge")).catch(report);
      return;
    }
    body.innerHTML = `
      <div class="acct-row"><span>模型服务</span><b id="acctModelState">检查中…</b></div>
      <div class="acct-row"><span>原生 PPT 运行时</span><b id="acctNativeState">检查中…</b></div>
      <p class="acct-note">模型名、接口地址与密钥只在服务端 .env 读取；页面编辑功能暂未开放（占位）。</p>`;
    fetch("/api/health").then(response=>response.json()).then(health=>{
      const model = $("#acctModelState"), native = $("#acctNativeState");
      if(model) model.textContent = health.modelConfigured ? "已配置（服务端 .env）" : "未配置";
      if(native) native.textContent = health.nativeRuntime ? "已就绪" : "不可用";
    }).catch(()=>{
      const model = $("#acctModelState"), native = $("#acctNativeState");
      if(model) model.textContent = "状态未知";
      if(native) native.textContent = "状态未知";
    });
  }
  function openAccountModal(kind){
    const meta = {
      user:{title:"用户信息", sub:"账户与数据位置"},
      knowledge:{title:"最近产物", sub:"本次与近期生成的文件"},
      model:{title:"模型管理", sub:"服务端模型与运行时状态"}
    }[kind];
    closeAccountPopover();
    $("#accountModalTitle").textContent = meta.title;
    $("#accountModalSub").textContent = meta.sub;
    $("#accountModalHint").textContent = kind === "knowledge" ? "资料库" : "设置";
    accountModal.classList.add("open");
    document.body.style.overflow = "hidden";
    renderAccountModal(kind);
    setTimeout(()=>$("#accountModalClose").focus(), 60);
  }
  $("#btnLocalUser").addEventListener("click", (event)=>{
    event.stopPropagation();
    if(accountPopover.hidden) openAccountPopover(); else closeAccountPopover();
  });
  accountPopover.addEventListener("click", (event)=>{
    const entry = event.target.closest("[data-account-panel]");
    if(entry) openAccountModal(entry.dataset.accountPanel);
  });
  $("#accountModalClose").addEventListener("click", closeAccountModal);
  $("#accountModalDone").addEventListener("click", closeAccountModal);
  accountModal.addEventListener("click", (event)=>{ if(event.target === accountModal) closeAccountModal(); });
  document.addEventListener("click", (event)=>{
    if(!accountPopover.hidden && !event.target.closest("#accountPopover") && !event.target.closest("#btnLocalUser")) closeAccountPopover();
  });
  document.addEventListener("keydown", (event)=>{
    if(event.key === "Escape"){ closeAccountPopover(); closeAccountModal(); }
  });
  window.addEventListener("resize", closeAccountPopover);
  window.addEventListener("scroll", closeAccountPopover, true);

  const WIP_META = {
    pdf:{eyebrow:"Workflow 01", title:"教材 PDF 转 MD", desc:"把扫描版或文字版教材转成结构化的 Markdown，保留章节、注释与图表位置。"},
    word:{eyebrow:"Workflow 02", title:"教材 MD 转教案 Word", desc:"按课标与课时模板，把教材 Markdown 转成可直接使用的教案 Word 文档。"}
  };
  function switchFeature(name){
    if(name === "ppt"){ showDrafts(); return; }
    if(name === "pdf" || name === "word"){
      const key = name === "pdf" ? "pdf" : "lesson";
      const current = new URLSearchParams(location.search);
      const jobId = INITIAL_PARAMS.get(key) || current.get(key);
      INITIAL_PARAMS.delete(key);
      history.replaceState(null, "", "?feature=" + name + (jobId ? "&" + key + "=" + encodeURIComponent(jobId) : ""));
      $("#view-drafts").hidden = true;
      $("#view-ppt").hidden = true;
      $("#view-wip").hidden = true;
      hidePipelineViews();
      $("#view-"+name).hidden = false;
      $("#crumbCurrent").innerHTML = "<b>" + (name === "pdf" ? "教材 PDF 转 MD" : "教材 MD 转教案 Word") + "</b>";
      $$(".nav-item").forEach(b=>{
        const on = b.dataset.feature === name;
        b.classList.toggle("active", on);
        if(on) b.setAttribute("aria-current","page"); else b.removeAttribute("aria-current");
      });
      if(window.PipelineUI) window.PipelineUI.enter(name);
      window.scrollTo({top:0, behavior:"smooth"});
      return;
    }
    $("#view-drafts").hidden = true;
    $("#view-ppt").hidden = true;
    $("#view-wip").hidden = false;
    hidePipelineViews();
    $("#crumbCurrent").innerHTML = "<b>" + WIP_META[name].title + "</b>";
    $("#wipEyebrow").textContent = WIP_META[name].eyebrow;
    $("#wipTitle").textContent = WIP_META[name].title;
    $("#wipDesc").textContent = WIP_META[name].desc;
    $$(".nav-item").forEach(b=>{
      const on = b.dataset.feature === name;
      b.classList.toggle("active", on);
      if(on) b.setAttribute("aria-current","page"); else b.removeAttribute("aria-current");
    });
    window.scrollTo({top:0, behavior:"smooth"});
  }
  $$("[data-feature]").forEach(b=>b.addEventListener("click", ()=>switchFeature(b.dataset.feature)));

  async function showPptWithRefs(ids, topic){
    try{ await settleCurrent(); }catch(error){ /* keep going with saved state */ }
    pollEpoch++;
    taskEpoch++;
    formDirty=false; outlineDirty=false;
    resetTaskState();
    enterFlow();
    goStep(1);
    try{ await refreshKnowledge(); }catch(error){ report(error); }
    state.refs = (ids||[]).filter(id=>getRef(id));
    if(topic) $("#fTopic").value = topic;
    renderRefs();
    updateStep2Summary();
    toast("已带入 " + state.refs.length + " 份阶段产物，请确认课题与页数");
    setTimeout(()=>$("#fTopic").focus(), 300);
  }
  window.Workbench = {showFeature: switchFeature, showPptWithRefs, toast: toast};

  /* ================= 步骤条点击 ================= */
  $$(".step").forEach(b=>b.addEventListener("click", ()=>{
    const n = Number(b.dataset.step);
    if(n === state.step) return;
    if(n > 1 && !state.outline.length){
      toast("先填写基础信息并生成大纲");
      goStep(1);
      return;
    }
    if(n === 3 && !state.generated){
      runGeneration();
      return;
    }
    if(n === 2 && state.generated){
      // 回到大纲前保留已生成结果
      showOutlineReady();
      goStep(2);
      return;
    }
    if(n === 2){
      if($("#outlineGen").hidden === false) return;
      showOutlineReady();
      goStep(2);
      return;
    }
    goStep(n);
  }));

  let pollEpoch=0, saveTimer=null, formTimer=null, saveChain=Promise.resolve();
  const STATUS_LABELS={draft:"草稿",outline_ready:"大纲就绪",queued:"排队中",running:"制作中",ready:"待审阅",ready_for_review:"待审阅",building:"制作中",confirmed:"已确认",failed:"失败",interrupted:"已中断",cancelled:"已取消",needs_input:"需要补充意见"};
  let outlineDirty=false;
  let taskEpoch=0, formDirty=false, formEditSequence=0;
  function markFormDirty(){formDirty=true;formEditSequence++;}
  function setBusyControls(busy){
    $$("#panel-1 input,#panel-1 textarea,#panel-1 select,#outlineList input,#outlineList button,#btnAddSlide,.style-opt,.chip-btn,[data-remove-ref],[data-remove-local],#tplReplace,#tplRemove,#btnUploadRef,#btnOpenKB,#btnResetStep1").forEach(x=>x.disabled=busy);
    if(state.template)$$(".style-opt").forEach(x=>x.disabled=true);
  }
  const outlineRevisions=new Map();
  async function api(url, method="GET", body){
    const options={method,headers:{}};
    if(body!==undefined){options.headers["Content-Type"]="application/json";options.body=JSON.stringify(body);}
    const r=await fetch(url,options); const d=await r.json();
    if(r.status===401){ showAuthGate(); throw Error(d.error||"请先登录"); }
    if(!r.ok) throw Error(d.error||`请求失败 ${r.status}`);
    return d;
  }
  function report(error){
    const status=$("#taskStatus"); status.hidden=false; status.textContent=error.message||String(error);
    toast(status.textContent);
  }
  async function uploadFile(file,kind="reference"){
    const form=new FormData();form.append("file",file);form.append("kind",kind);
    const r=await fetch("/api/files",{method:"POST",body:form});const d=await r.json();
    if(!r.ok) throw Error(d.error);return d;
  }
  async function refreshKnowledge(){
    try{
      KB_DATA=await api("/api/knowledge");renderKB();renderRefs();
      const conn=$("#knowledgeConnection"); if(conn) conn.textContent="可用";
    }catch(error){const conn=$("#knowledgeConnection"); if(conn) conn.textContent="不可用";throw error;}
  }
  function taskForm(){
    return {...readForm(),count:state.count,style:state.style,template:state.template?.fileId||null,
      refs:state.refs,localRefs:state.localRefs.map(x=>x.fileId)};
  }
  async function persistForm(){
    clearTimeout(formTimer);
    if(!$("#fTopic").value.trim()) return;
    const payload=taskForm(), initialId=state.taskId, epoch=taskEpoch, editSequence=formEditSequence;
    saveChain=saveChain.catch(()=>{}).then(async()=>{
      if(epoch!==taskEpoch)return;
      const id=initialId||state.taskId;
      let result;
      if(id){
        if(["queued","running"].includes(state.task?.status)) return;
        result=await api(`/api/tasks/${id}`,"PUT",payload);
      } else {
        result=await api("/api/tasks","POST",payload);
      }
      if(epoch!==taskEpoch)return;
      state.task=result;state.taskId=result.id;state.outlineRevision=result.outlineRevision;
      if(editSequence===formEditSequence)formDirty=false;
      localStorage.setItem("pptnative-task",state.taskId);
    });
    await saveChain;
  }
  function scheduleOutlineSave(){
    outlineDirty=true;clearTimeout(saveTimer);saveTimer=setTimeout(()=>flushOutline().catch(report),450);
  }
  async function flushOutline(){
    clearTimeout(saveTimer);
    if(!outlineDirty||!state.taskId||!state.outline.length) return saveChain;
    const pages=JSON.parse(JSON.stringify(state.outline)), id=state.taskId, revision=state.outlineRevision, epoch=taskEpoch;outlineDirty=false;
    saveChain=saveChain.catch(()=>{}).then(async()=>{
      const result=await api(`/api/tasks/${id}/outline`,"PUT",{pages,base_revision:outlineRevisions.get(id)??revision});
      outlineRevisions.set(id,result.outlineRevision);
      if(epoch===taskEpoch&&state.taskId===id)state.outlineRevision=result.outlineRevision;
    });
    try{await saveChain;}catch(error){if(epoch===taskEpoch&&state.taskId===id)outlineDirty=true;throw error;}
  }
  async function settleCurrent(){
    clearTimeout(formTimer);clearTimeout(saveTimer);
    if(formDirty)await persistForm();
    await flushOutline();await saveChain;
  }
  function applyTask(task){
    const selectedPageId=state.taskId===task.id?state.slides[state.current]?.id:null;
    state.taskId=task.id;state.task=task;state.outlineRevision=task.outlineRevision;
    outlineRevisions.set(task.id,task.outlineRevision);
    state.outline=task.outline;state.slides=task.slides;state.version=task.selectedVersion;
    state.generated=!!task.currentVersion;state.refs=task.refs;state.style=task.style;
    state.localRefs=task.referenceFiles.map(f=>({...f,local:true,type:"local"}));
    setCount(task.count);state.template=task.templateFile;
    if(state.template) showTemplate(state.template);else{renderDrop();setStyleLock(false);}
    const fields={topic:"fTopic",teacher:"fTeacher",audience:"fAudience",duration:"fDuration",extra:"fExtra"};
    Object.entries(fields).forEach(([key,id])=>{$("#"+id).value=task[key]||"";});
    $$(".style-opt").forEach(o=>{const on=o.dataset.style===state.style;o.classList.toggle("on",on);o.setAttribute("aria-checked",String(on));});
    renderRefs();renderOutline();renderDeck();updateStep2Summary();
    const selectedIndex=state.slides.findIndex(p=>p.id===selectedPageId);if(selectedIndex>=0)selectSlide(selectedIndex);
    $("#deckTitle").textContent=task.topic;
    const busy=["queued","running"].includes(task.status);
    setBusyControls(busy);
    const failed=["failed","interrupted","cancelled","needs_input"].includes(task.status);
    $("#genStage h2").textContent=failed?"制作尚未完成":"正在生成草稿 PPT";
    $("#outlineGen h2").textContent=failed?"大纲处理尚未完成":"正在制作大纲";
    $$(".gen-ring").forEach(x=>x.style.animationPlayState=busy?"running":"paused");
    $("#taskActions").hidden=!(busy||failed);
    $("#retryTask").hidden=!failed;$("#cancelTask").hidden=task.status!=="queued";
    const event=task.events?.at(-1);
    const labels=STATUS_LABELS;
    $("#taskStatus").hidden=false;
    $("#taskStatus").textContent=`${labels[task.status]||task.status} · ${task.progress?.label||"等待处理"}${task.progress?.totalPages?' · 已制作 '+task.progress.completedPages+'/'+task.progress.totalPages+' 页':''}${task.result?.question?'\n'+task.result.question:''}`;
    $("#taskError").hidden=!task.error;$("#taskErrorText").textContent=task.error||"";
    const progress=task.progress?.percent||0;
    $("#genBar").style.width=progress+"%";$("#outlineGenBar").style.width=(task.status==="outline_ready"?100:progress)+"%";
    $("#genText").textContent=$("#taskStatus").textContent;$("#outlineGenText").textContent=$("#taskStatus").textContent;
    $("#btnGenerate").disabled=busy;$("#btnToOutline").disabled=busy;$("#btnRegen").disabled=busy;
    const selected=task.versions.find(v=>v.id===state.version);
    $("#deckVersion").textContent=selected?selected.id+" · "+(STATUS_LABELS[selected.status]||selected.status):"尚未生成";
    $("#saveTime").textContent=new Date(task.updated).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",hour12:false});
    const deliverable=selected?.qa_passed&&["ready_for_review","confirmed"].includes(selected.status);
    $("#btnRevise").disabled=busy||state.reviseBusy||state.version!==task.currentVersion||!deliverable;
    $("#btnDownloadTop").disabled=!deliverable;
    $("#btnConfirmDownload").disabled=busy||!deliverable||state.version!==task.currentVersion;
    if(!$("#versionSelect")){
      const select=document.createElement("select");select.id="versionSelect";select.className="control";select.setAttribute("aria-label","历史版本");
      $("#previewMeta").parentElement.appendChild(select);
      select.addEventListener("change",async()=>{try{applyTask(await api(`/api/tasks/${state.taskId}?version=${select.value}`));}catch(e){report(e);}});
    }
    $("#versionSelect").innerHTML=task.versions.map(v=>`<option value="${esc(v.id)}" ${v.id===state.version?'selected':''}>${esc(v.id)} · ${esc(STATUS_LABELS[v.status]||v.status)}</option>`).join("");
    if(state.generated){$("#genStage").hidden=true;$("#deckStage").hidden=false;}
    else if(failed){$("#genStage").hidden=false;$("#deckStage").hidden=true;}
    localStorage.setItem("pptnative-task",task.id);
    history.replaceState(null,"","?task="+encodeURIComponent(task.id));
  }
  async function followTask(){
    const epoch=++pollEpoch,id=state.taskId;
    let disconnected=false;
    while(epoch===pollEpoch){
      let task;
      try{task=await api("/api/tasks/"+id);disconnected=false;}
      catch(error){
        if(!disconnected){report(Error("连接中断，正在重连；任务已保存"));disconnected=true;}
        await new Promise(r=>setTimeout(r,3000));continue;
      }
      if(epoch!==pollEpoch)return;
      applyTask(task);
      if(!["queued","running"].includes(task.status)){
        if(task.outline.length&&task.status==="outline_ready"){showOutlineReady();goStep(2);}
        else if(task.currentVersion)goStep(3);
        if(task.error)toast("处理未完成，候选已保存，可查看技术详情或继续制作");return;
      }
      await new Promise(r=>setTimeout(r,1500));
    }
  }
  function showPendingOperation(task){
    if(task.operation==="outline"){goStep(2);$("#outlineReady").hidden=true;$("#outlineGen").hidden=false;}
    else {goStep(3);$("#genStage").hidden=state.generated;$("#deckStage").hidden=!state.generated;}
  }
  async function refreshDrafts(){
    const tasks=await api("/api/tasks");
    $("#draftItems").innerHTML=tasks.length?tasks.map(t=>`<article class="draft-card">
      <div class="draft-thumb">${t.slides[0]?.previewUrl?`<img src="${esc(t.slides[0].previewUrl)}" alt="${esc(t.topic)}" style="width:100%;height:100%;object-fit:contain">`:'<div class="empty-ref">尚未生成成品</div>'}</div>
      <div class="draft-body"><div class="draft-tags"><span class="pill">${esc(STATUS_LABELS[t.status]||t.status)} · ${esc(t.currentVersion||'大纲草稿')}</span></div>
      <h3>${esc(t.topic)}</h3><div class="draft-desc">${t.outline.length} 页 · ${esc(STYLE_MAP[t.style])}</div>
      <div class="draft-meta">${esc(new Date(t.updated).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",hour12:false}))}</div><div class="draft-actions"><button class="btn btn-sm btn-primary" data-open-task="${t.id}">继续编辑</button></div></div></article>`).join(""):'<div class="empty-ref">还没有草稿，从新建任务开始</div>';
  }
  $("#retryTask").addEventListener("click",async()=>{try{const task=await api(`/api/tasks/${state.taskId}/resume`,"POST",{});applyTask(task);showPendingOperation(task);await followTask();}catch(e){report(e);}});
  $("#cancelTask").addEventListener("click",async()=>{try{applyTask(await api(`/api/tasks/${state.taskId}/cancel`,"POST",{}));}catch(e){report(e);}});
  $("#knowledgeUpload").addEventListener("click",()=>$("#knowledgeFile").click());
  $("#knowledgeFile").addEventListener("change",async e=>{
    const file=e.target.files[0];if(!file)return;
    try{const f=await uploadFile(file);await api("/api/knowledge","POST",{fileId:f.fileId,type:$("#knowledgeType").value});await refreshKnowledge();toast("资料已入库");}catch(error){report(error);}
    e.target.value="";
  });
  document.addEventListener("change",e=>{if(e.target.closest("#panel-1")){markFormDirty();clearTimeout(formTimer);formTimer=setTimeout(()=>persistForm().catch(report),400);}});
  document.addEventListener("input",e=>{if(e.target.closest("#panel-1")){markFormDirty();clearTimeout(formTimer);formTimer=setTimeout(()=>persistForm().catch(report),650);}});
  document.addEventListener("click",e=>{if(e.target.closest(".style-opt,.chip-btn,#tplRemove,#kbConfirm,[data-remove-local],[data-remove-ref]")){markFormDirty();clearTimeout(formTimer);formTimer=setTimeout(()=>persistForm().catch(report),150);}});
  $(".icon-btn").addEventListener("click",()=>toast("方向键切换预览；先确认大纲，再生成；历史版本可随时查看。"));

  /* ================= 初始化 ================= */
  /* ================= 登录 ================= */
  let authUser=null, cooldownTimer=null;
  function showAuthGate(show=true){
    const gate=$("#authGate"); if(!gate) return;
    gate.hidden=!show;
    document.body.style.overflow=show?"hidden":"";
    if(show) setTimeout(()=>$("#authEmail").focus(),50);
  }
  function applyUser(user){
    authUser=user;
    $$(".user-chip b").forEach(x=>x.textContent=user.email);
    $$(".user-chip span").forEach(x=>x.textContent="数据仅本人可见");
    const head=$("#accountPopover .ap-head");
    if(head){ const b=head.querySelector("b"), sp=head.querySelector("span");
      if(b) b.textContent=user.email; if(sp) sp.textContent="已登录 · 数据隔离"; }
    const avatar=$("#accountPopover .avatar"); if(avatar) avatar.textContent=user.email.slice(0,1).toUpperCase();
  }
  async function authBoot(){
    try{ const r=await fetch("/api/auth/me"); if(r.ok){ applyUser(await r.json()); return true; } }catch(error){}
    showAuthGate(true); return false;
  }
  function authMsg(text){ const el=$("#authMsg"); if(el) el.textContent=text||""; }
  async function sendAuthCode(){
    const email=$("#authEmail").value.trim();
    if(!email){ authMsg("请填写邮箱"); return; }
    const button=$("#btnSendCode"); button.disabled=true; authMsg("正在发送…");
    try{
      const r=await fetch("/api/auth/code",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({email})});
      const d=await r.json();
      if(!r.ok){ authMsg(d.error||"发送失败"); return; }
      $("#authCodeRow").hidden=false; $("#authCode").focus();
      if(d.devCode){ $("#authCode").value=d.devCode; authMsg("本机调试验证码已填入：" + d.devCode); }
      else authMsg("验证码已发送，请查收邮箱（10 分钟内有效）");
      let left=45; const tick=()=>{ $("#authCooldown").textContent=left>0?`${left}s 后可重发`:""; if(left--<=0){clearInterval(cooldownTimer);button.disabled=false;} };
      clearInterval(cooldownTimer); button.disabled=true; tick(); cooldownTimer=setInterval(tick,1000);
    }catch(error){ authMsg("网络异常，请重试"); }
    finally{ button.disabled = button.disabled || false; }
  }
  async function doLogin(){
    const email=$("#authEmail").value.trim(), code=$("#authCode").value.trim();
    if(!email||!/^\d{6}$/.test(code)){ authMsg("请输入邮箱和 6 位验证码"); return; }
    const button=$("#btnDoLogin"); button.disabled=true; authMsg("正在登录…");
    try{
      const r=await fetch("/api/auth/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({email,code})});
      const d=await r.json();
      if(!r.ok){ authMsg(d.error||"登录失败"); button.disabled=false; return; }
      location.reload();
    }catch(error){ authMsg("网络异常，请重试"); button.disabled=false; }
  }
  async function doLogout(){
    try{ await fetch("/api/auth/logout",{method:"POST"}); }catch(error){}
    location.reload();
  }
  $("#btnSendCode").addEventListener("click",sendAuthCode);
  $("#btnDoLogin").addEventListener("click",doLogin);
  $("#btnLogout").addEventListener("click",doLogout);
  $("#authEmail").addEventListener("keydown",e=>{ if(e.key==="Enter") sendAuthCode(); });
  $("#authCode").addEventListener("keydown",e=>{ if(e.key==="Enter") doLogin(); });

  setCount(12);
  renderRefs();
  $("#sumStyle").textContent = STYLE_MAP[state.style];
  setStyleLock(false);
  renderOutline();
  renderDeck();
  goStep(1);
  const saved=new URLSearchParams(location.search).get("task")||localStorage.getItem("pptnative-task");
  const initialFeature = INITIAL_PARAMS.get("feature");
  authBoot().then(authed=>{
    if(!authed) return;
    showDrafts().then(()=>{ if(saved && !initialFeature) openDraft(saved); }).finally(()=>{
      if(initialFeature === "pdf" || initialFeature === "word") setTimeout(()=>switchFeature(initialFeature), 0);
    });
    refreshKnowledge().catch(report);
  });
  if(window.innerWidth <= 860){
    const activeNav = $(".nav-item.active");
    if(activeNav) activeNav.scrollIntoView({inline:"center", block:"nearest"});
  }
})();
