// Exercise actual persistence functions with a delayed transport, without browser-private hooks.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const nodes=new Map();
const node=key=>{if(!nodes.has(key))nodes.set(key,{value:'Course',addEventListener(){}});return nodes.get(key);};
const calls=[];
const context={state:{taskId:'A',task:{status:'draft'},outlineRevision:1,outline:[{id:'p01',title:'A',points:[]}],refs:[],localRefs:[],count:7,style:'academic'},
  $:node,$$:()=>[],document:{addEventListener(){}},localStorage:{setItem(){}},
  readForm:()=>({topic:'Course'}),setTimeout,clearTimeout,console,
  fetch:async(url,options)=>{calls.push(url);return{ok:true,json:async()=>({id:url.includes('/B')?'B':'A',outlineRevision:2})};}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'frontend_api.js'),'utf8'),context);
(async()=>{
  vm.runInContext('outlineDirty=true',context);
  const saving=vm.runInContext('flushOutline()',context);
  context.state.taskId='B';context.state.outlineRevision=1;
  vm.runInContext('taskEpoch++',context);
  await saving;
  assert.deepEqual(calls,['/api/tasks/A/outline']);
  assert.equal(context.state.taskId,'B');
  assert.equal(context.state.outlineRevision,1);
  context.state.taskId='A';
  let release;
  context.fetch=()=>new Promise(resolve=>{release=resolve;});
  const formSaving=vm.runInContext('markFormDirty();persistForm()',context);
  await Promise.resolve();await Promise.resolve();
  vm.runInContext('markFormDirty()',context);
  release({ok:true,json:async()=>({id:'A',outlineRevision:2})});
  await formSaving;
  assert.equal(vm.runInContext('formDirty',context),true,'older response must not clear newer edits');
  // Exercise generated upload handlers with transport completing after task switch.
  const generated=fs.readFileSync(require('node:path').join(__dirname,'../static/workbench.js'),'utf8');
  let referenceHandler;
  context.refFileInput={files:[{name:'A.pdf'}],value:'A.pdf',addEventListener:(_,fn)=>{referenceHandler=fn;}};
  let finishUpload;
  context.uploadFile=()=>new Promise(resolve=>{finishUpload=resolve;});
  context.renderRefs=()=>{};context.showTemplate=()=>{throw Error('stale template applied');};
  vm.runInContext(generated.slice(generated.indexOf('  refFileInput.addEventListener("change"'),generated.indexOf('  /* ================= 知识库弹窗')),context);
  const uploading=referenceHandler();
  context.state.taskId='B';vm.runInContext('taskEpoch++',context);
  finishUpload({fileId:'A-file'});await uploading;
  assert.equal(context.state.localRefs.length,0,'stale upload must not enter another task');
  vm.runInContext(generated.slice(generated.indexOf('  async function handleTpl(file){'),generated.indexOf('  function renderDrop(){')),context);
  const templateUpload=vm.runInContext('handleTpl({name:"A.pptx"})',context);
  vm.runInContext('taskEpoch++',context);
  finishUpload({fileId:'A-template'});await templateUpload;
  console.log('frontend persistence isolation passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
