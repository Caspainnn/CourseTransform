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
      $(".db-status").innerHTML='<span class="dot" aria-hidden="true"></span>知识库已连接 · '+KB_DATA.length+' 份';
      $("#knowledgeConnection").textContent="已连接";
    }catch(error){$(".db-status").textContent="知识库未连接";$("#knowledgeConnection").textContent="连接失败";throw error;}
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
    $("#btnRevise").disabled=busy||state.version!==task.currentVersion||!deliverable;
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
