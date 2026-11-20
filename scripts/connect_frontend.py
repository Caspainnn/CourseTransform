"""One-time mechanical conversion of the frozen prototype; CSS stays byte-identical."""
from pathlib import Path
import re

root = Path(__file__).resolve().parents[1]
html = (root / 'reference/workbench.html').read_text(encoding='utf-8')
script_start = html.index('<script>')
shell, js = html[:script_start], html[script_start + len('<script>'):html.index('</script>', script_start)]
def between(start, end, replacement):
    global js
    a, b = js.index(start), js.index(end, js.index(start))
    js = js[:a] + replacement + '\n' + js[b:]

between('  const KB_DATA = [', '\n  const state =', '  let KB_DATA = [];')
js = js.replace('refs:["j1","c1"]', 'refs:[]')
between('  const EXTRA_POOL = [', '  /* ================= 步骤切换', '')
between('  refFileInput.addEventListener("change"', '  /* ================= 知识库弹窗', '''
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
''')
between('  $("#kbRefresh").addEventListener', '  $("#btnOpenKB").addEventListener', '''
  $("#kbRefresh").addEventListener("click", ()=>refreshKnowledge().catch(report));
''')
js = js.replace('已解析 18 个版式', "已解析 '+esc(file.layoutCount)+' 个版式")
js = js.replace('state.template = file.name;', 'state.template = file;')
js = js.replace('toast("模板上传成功，共识别 18 个版式");', '')
between('  function handleTpl(file){', '  function renderDrop(){', '''
  async function handleTpl(file){
    const epoch=taskEpoch;
    try { const result=await uploadFile(file,"template"); if(epoch!==taskEpoch)return; showTemplate(result); await persistForm(); }
    catch(error){ report(error); }
  }
''')
js = js.replace('state.outline.push({type:', 'state.outline.push({id:"p"+crypto.randomUUID().replaceAll("-","").slice(0,12),type:')
js = js.replace('renderOutline();\n    }', 'renderOutline(); scheduleOutlineSave();\n    }')
js = js.replace('renderOutline();\n      toast("已删除', 'renderOutline(); scheduleOutlineSave();\n      toast("已删除')
js = js.replace('renderOutline();\n    const inputs', 'renderOutline(); scheduleOutlineSave();\n    const inputs')
js = js.replace('$("#deckTitle").textContent = state.outline[0] ? state.outline[0].title : "";', '$("#deckTitle").textContent = state.outline[0] ? state.outline[0].title : ""; scheduleOutlineSave();')
js = js.replace('|| "《荷塘月色》——通感与情感线索"', '|| "未命名课题"').replace('|| "陈老师"', '|| "教师"').replace('|| "高一班级"', '|| "课堂"')
between('  function buildFromForm(){', '  /* ---- 大纲制作过程进度', '''
  function buildFromForm(){ renderOutline(); updateStep2Summary(); }
''')
between('  function runOutlineGeneration(){', '  $("#btnToOutline").addEventListener', '''
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
''')
between('  $("#btnRegen").addEventListener', '  /* ================= PPT 渲染', '''
  $("#btnRegen").addEventListener("click", async()=>{
    const instruction=$("#regenPrompt").value.trim();
    if(!instruction){toast("请填写修改要求");return;}
    addChat($("#regenLog"),instruction,"me");
    await runOutlineGeneration(instruction);
    $("#regenPrompt").value="";
  });
''')
between('  const STYLE_THEME =', '  function renderDeck(){', '''
  function actualImage(slide){
    return slide.previewUrl ? '<img src="'+esc(slide.previewUrl)+'" alt="'+esc(slide.title)+'" style="width:100%;height:100%;object-fit:contain">' : '<div class="empty-ref">此版本尚无真实预览</div>';
  }
''')
js = js.replace('    state.slides = state.outline.slice();', '')
js = js.replace('thumbArt(s)', 'actualImage(s)')
js = js.replace('slideHTML(s, state.slides.length, state.current)', 'actualImage(s)')
js = js.replace('$("#previewFrame").setAttribute("style", themeVars());', '$("#previewFrame").style.background="#fff";')
between('  function runGeneration(){', '  $("#btnGenerate").addEventListener', '''
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
''')
js=js.replace('$("#btnBackOutline").addEventListener("click", ()=>{\n    state.generated = false;', '$("#btnBackOutline").addEventListener("click", ()=>{')
between('  $("#btnRevise").addEventListener', '  function resetTaskState(){', '''
  $("#btnRevise").addEventListener("click", async()=>{
    const text=$("#reviseInput").value.trim(), slide=state.slides[state.current];
    if(!text||!slide){toast("请填写修改要求");return;}
    try {
      await api(`/api/tasks/${state.taskId}/slides/${state.current}/revise`,"POST",{
        text,pageId:slide.id,base_version:state.version});
      addChat($("#reviseLog"),text,"me"); $("#reviseInput").value="";
      await followTask();
    } catch(error){ report(error); }
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
''')
js = js.replace('    state.outline = []; state.slides = [];', '    state.taskId=null; state.task=null; state.version=null; state.outlineRevision=0; state.template=null; state.refs=[]; localStorage.removeItem("pptnative-task"); renderDrop(); setStyleLock(false);\n    state.outline = []; state.slides = [];')
js = js.replace('  function resetTaskState(){', '  function resetTaskState(){\n    setBusyControls(false); ["#btnToOutline","#btnGenerate","#btnRegen"].forEach(s=>$(s).disabled=false);\n    $("#taskStatus").hidden=true;$("#taskActions").hidden=true;')
js = js.replace('    state.localRefs = [];', '    state.localRefs = []; $("#taskError").hidden=true;')
js = js.replace('  function newTaskFlow(){', '  async function newTaskFlow(){\n    try{await settleCurrent();}catch(error){report(error);return;}\n    taskEpoch++;formDirty=false;outlineDirty=false;history.replaceState(null,"",location.pathname);')
js = js.replace('    if(state.generated && !confirm("新建任务会清空当前草稿，继续吗？")) return;', '    pollEpoch++;')
between('  const DRAFTS = {', '  $("#btnResetStep1").addEventListener', '''
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
''')
js = js.replace('state.refs = ["j1","c1"];', 'state.refs = [];')
js = js.replace('$("#fTopic").value = "《荷塘月色》——通感与情感线索";', '$("#fTopic").value = "";')
js = js.replace('$("#fTeacher").value = "陈老师";', '$("#fTeacher").value = "";')
js = js.replace('$("#fAudience").value = "高一（3）班 · 语文基础中等偏上";', '$("#fAudience").value = "";')
js = js.replace('  function showDrafts(){', '  async function showDrafts(){\n    try{await settleCurrent();}catch(error){report(error);return;}\n    pollEpoch++;history.replaceState(null,"",location.pathname);\n    refreshDrafts().catch(report);')
js = js.replace('  setCount(12);', '  setCount(12);', 1)
js = js.replace('  buildFromForm();\n  renderOutline();\n  renderDeck();', '  renderOutline();\n  renderDeck();')
# Keep all original shell styles; replace static examples with real empty state.
a=shell.index('          <article class="draft-card">')
b=shell.index('          <article class="draft-card draft-new">',a)
shell=shell[:a]+'          <div id="draftItems" style="display:contents"></div>\n'+shell[b:]
shell=shell.replace('仅显示最近 30 天的草稿，制作过程会自动保存','任务与历史版本保存在本机，制作过程会自动保存')
shell=shell.replace('知识库已连接 · v2.4','知识库连接中…')
shell=shell.replace('<span class="pill">草稿 v1</span>', '<span class="pill" id="deckVersion">尚未生成</span>')
shell=shell.replace('最近修改 刚刚', '最近修改 <span id="saveTime">—</span>')
shell=shell.replace('<h2 id="deckTitle">《荷塘月色》——通感与情感线索</h2>', '<h2 id="deckTitle">课件预览</h2>')
shell=shell.replace('<div class="avatar" aria-hidden="true">陈</div>', '<div class="avatar" aria-hidden="true">本</div>')
shell=shell.replace('<b>陈老师</b><span>语文教研组 · 高中</span>', '<b>本机用户</b><span>本地教学工作台</span>')
shell=shell.replace('value="《荷塘月色》——通感与情感线索"', 'value=""').replace('value="陈老师"', 'value=""').replace('value="高一（3）班 · 语文基础中等偏上"', 'value=""')
shell=shell.replace('大纲已就绪，确认后约需 40 秒生成整套草稿','大纲已就绪；制作耗时随页数、模型与质检情况变化')
shell=shell.replace('已连接「教学资源库 v2.4」','本机教学资源库')
shell=shell.replace('<span class="pill sage">已连接</span>', '<span class="pill sage" id="knowledgeConnection">连接中</span>')
shell=shell.replace('本月生成额度','本机任务存储').replace('已生成 17 / 50 份 PPT<br>剩余额度可覆盖约 6 个单元','任务与资料仅保存在本机')
shell=shell.replace('width:34%', 'width:0%')
shell=shell.replace('<main class="content" id="content">','''<main class="content" id="content">
      <div id="taskStatus" class="notice" role="status" hidden></div>
      <details id="taskError" class="side-note" hidden style="margin-bottom:12px"><summary>技术详情</summary><pre id="taskErrorText" style="white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px"></pre></details>
      <div id="taskActions" class="head-actions" hidden>
        <button class="btn btn-sm" id="retryTask">继续制作</button>
        <button class="btn btn-sm" id="cancelTask">取消排队</button>
      </div>''')
shell=shell.replace('<div class="kb-list" id="kbList">','''<div class="bar-actions" style="padding:8px 0">
        <select id="knowledgeType" class="control" aria-label="入库分类"><option value="jiaocai">教材</option><option value="jiaoan">教案</option></select>
        <button class="btn btn-sm" id="knowledgeUpload">上传到知识库</button><input id="knowledgeFile" type="file" accept=".doc,.docx,.pdf,.md,.txt" hidden>
        </div><div class="kb-list" id="kbList">''')
shell=shell.replace('<div id="deckStage"', '<div id="deckStage"')
# Small control addition inside existing preview toolbar, no page restructuring.
shell=shell.replace('id="previewMeta"', 'id="previewMeta"')
pos=shell.index('<div class="toast-wrap"')
extra='''<style>[hidden]{display:none!important}.thumb-canvas img{display:block}.slide-render img{display:block}#taskStatus{white-space:pre-wrap}#taskActions{margin-bottom:16px}.preview-toolbar{flex-wrap:wrap}#versionSelect{width:auto;flex:0 1 220px;min-width:120px;max-width:100%}</style>'''
shell=shell[:pos]+extra+shell[pos:]
api_code=(root/'scripts/frontend_api.js').read_text(encoding='utf-8')
js=js.replace('  /* ================= 初始化 ================= */', api_code+'\n  /* ================= 初始化 ================= */')
js=js.replace('  showDrafts();\n  if(window.innerWidth', '  const saved=new URLSearchParams(location.search).get("task")||localStorage.getItem("pptnative-task");\n  showDrafts(); refreshKnowledge().catch(report); if(saved) openDraft(saved);\n  if(window.innerWidth')
(root/'static/index.html').write_text(shell+'<script src="/static/workbench.js"></script>\n</body>\n</html>',encoding='utf-8')
(root/'static/workbench.js').write_text('\n'.join(line.rstrip() for line in js.splitlines())+'\n',encoding='utf-8')
