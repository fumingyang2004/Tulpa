'use strict';
let dataSettings=null,deleteToken=null;
const readSelection={qq:new Set(),wechat:new Set()},deleteSelection=new Set(),mediaRefreshSelection=new Set();
function syncDataControls(locked) {
  document.querySelectorAll('#client-scope-controls input,#client-scope-controls select,#client-scope-controls button,#delete-section input,#delete-section select,#delete-section button,#media-refresh-section input,#media-refresh-section select,#media-refresh-section button').forEach(n=>n.disabled=locked);
  for(const p of ['qq','wechat']) {
    const enabled=$('read-enable-'+p).checked,section=$('read-enable-'+p).closest('.scope-platform');
    section.classList.toggle('read-disabled',!enabled);
    section.querySelectorAll('input:not([type=checkbox]),select,.conversation-options input').forEach(n=>n.disabled=locked||!enabled);
    if($('read-all-'+p))$('read-'+p+'-per-chat').disabled=locked||!enabled||$('read-all-'+p).checked;
  }
  $('delete-confirm').disabled=locked||!deleteToken;
}
function invalidateDelete(){deleteToken=null;$('delete-summary').textContent='';$('delete-confirm').disabled=true;}
function scopeChoices(host,rows,selected,query,onchange) {
  host.replaceChildren();
  const visible=rows.filter(r=>r.conversation.toLowerCase().includes(query.toLowerCase())||r.conversation_id.includes(query));
  for(const row of visible) {
    const label=document.createElement('label'),input=document.createElement('input'),text=document.createElement('span');
    input.type='checkbox';input.value=row.conversation_id;input.checked=selected.has(row.conversation_id);
    input.onchange=()=>{input.checked?selected.add(input.value):selected.delete(input.value);onchange?.();};
    text.textContent=`${row.conversation} (${row.count})`;label.title=row.conversation_id;
    label.append(input,text);host.append(label);
  }
  if(!visible.length){const p=document.createElement('p');p.className='field-hint';p.textContent='暂无匹配会话';host.append(p);}
}
function renderReadScope(p) {
  $('read-pick-'+p).hidden=$('read-mode-'+p).value!=='selected';
  scopeChoices($('read-options-'+p),(dataSettings?.conversations||[]).filter(r=>r.platform===p),readSelection[p],$('read-search-'+p).value);
}
function renderDeleteScope() {
  scopeChoices($('delete-options'),(dataSettings?.imported||[]).filter(r=>r.platform===$('delete-platform').value),deleteSelection,$('delete-search').value,invalidateDelete);
}
async function loadDataSettings({keepDraft=false}={}) {
  dataSettings=await watchApi('/api/data/settings');
  for(const p of ['qq','wechat']) {
    if(!keepDraft) {
      const scope=dataSettings.scope[p];readSelection[p]=new Set(scope.conversations||[]);
      $('read-enable-'+p).checked=scope.enabled;$('read-mode-'+p).value=scope.conversations===null?'all':'selected';
    }
    for(const id of readSelection[p])if(!dataSettings.conversations.some(r=>r.platform===p&&r.conversation_id===id))dataSettings.conversations.push({platform:p,conversation_id:id,conversation:id+'（当前目录缺失）',count:0});
    renderReadScope(p);
  }
  renderDeleteScope();renderMediaRefreshScope();syncControls();
  const pending=dataSettings.pending_imports||[];
  if($('import-resume-note')){
    $('import-resume-note').hidden=!pending.length;
    $('import-resume-note').textContent=pending.length?`有 ${pending.length} 次未完成读取。已入库记录保留，使用相同设置再次读取会继续。`:'';
  }
}
function clientReadScope() {
  const scope={};
  for(const p of ['qq','wechat']) {
    const selected=$('read-mode-'+p).value==='selected',enabled=$('read-enable-'+p).checked;
    if(enabled&&selected&&!readSelection[p].size)throw new Error(`请勾选 ${p==='qq'?'QQ':'微信'} 会话，或选择全部会话`);
    scope[p]={enabled,conversations:selected&&readSelection[p].size?[...readSelection[p]]:null};
  }
  if(!Object.values(scope).some(p=>p.enabled))throw new Error('请至少选择一个平台');
  return scope;
}
async function runDataJob(path,body,{keepDraft=false}={}) {
  if(state.watchBusy||state.busy||state.loading)return;
  const starting={kind:path.split('/').at(-1)};jobStarting=starting;syncControls();$('read-progress').hidden=false;$('read-progress').textContent='正在准备…';
  try {
    const result=await watchApi(path,body);
    if(jobStarting===starting)jobStarting=null;
    await monitorWatchJob(result.job_id,{kind:result.kind});await loadDataSettings({keepDraft});
  }catch(error){$('read-progress').textContent=error.message;toast(error.message);}
  finally {if(jobStarting===starting)jobStarting=null;syncControls();}
}
function clientReadRequest() {
  const scope=clientReadScope(),ranges={},limits=readLimits(),all_messages={};
  for(const p of ['qq','wechat']) {
    const start=$('read-'+p+'-start').value,end=$('read-'+p+'-end').value;
    ranges[p]={start,end};
    all_messages[p]=Boolean($('read-all-'+p)?.checked);
    if(scope[p].enabled&&start&&end&&start>end)throw new Error(`${p==='qq'?'QQ':'微信'} 开始日期不能晚于截止日期`);
    if(!scope[p].enabled||all_messages[p])limits[p+'_per_chat']=500;
  }
  return {scope,limits,ranges,all_messages,load_stickers:$('load-stickers').checked};
}
async function readScoped() {
  for(const [p,id] of Object.entries(READ_FIELDS))if($('read-enable-'+p.split('_')[0]).checked&&!$(id).reportValidity())return;
  try {
    await runDataJob('/api/data/read',clientReadRequest());
    if($('chat-dialog').open&&chatPage)await pollChatWindow(true);
  }catch(error){toast(error.message);}
}
$('read-latest').onclick=readScoped;
$('read-catalog').onclick=()=>runDataJob('/api/data/catalog',{platforms:['qq','wechat'].filter(p=>$('read-enable-'+p).checked)},{keepDraft:true});
for(const p of ['qq','wechat']) {
  const all=$('read-all-'+p);
  if(all){
    try{all.checked=localStorage.getItem('chatweave.read-all-'+p)==='true';}catch{}
    all.onchange=()=>{try{localStorage.setItem('chatweave.read-all-'+p,String(all.checked));}catch{}syncControls();};
  }
  $('read-enable-'+p).onchange=()=>{syncControls();showReadLimits();};
  $('read-mode-'+p).onchange=()=>renderReadScope(p);
  $('read-search-'+p).oninput=()=>renderReadScope(p);
}
let activeImportJob=null;
function renderImportProgress(job){
  const host=$('import-meter'),p=job.progress;
  if(!host||!p)return;
  host.hidden=false;activeImportJob=job.status==='running'?job.id:null;
  const running=job.status==='running',bar=$('import-progress-bar');
  bar.hidden=!running&&job.status!=='completed';
  $('import-stop').hidden=!running;$('import-stop').disabled=false;
  const stages={preparing:'准备读取器',snapshot:'制作本次快照',decrypt:'验证并解密数据',strip_header:'准备数据库',inventory:'比较消息标识',reading:'读取消息',waiting_for_commit:'等待当前批次入库',exported:'核对入库结果',committing:'写入消息'};
  $('import-meter-title').textContent=(p.platform==='qq'?'QQ':'微信')+' · '+(running?(stages[p.stage]||'正在处理'):job.status==='completed'?'本轮处理结束':'已停止或未完成');
  // Unknown totals remain indeterminate; never invent a percent during snapshot
  // or declare completion because a single platform/batch finished.
  if(running&&p.expected>0){bar.max=p.expected;bar.value=Math.min(p.scanned||0,p.expected);}
  else if(!running&&job.status==='completed'){bar.max=1;bar.value=1;}
  else bar.removeAttribute('value');
  const n=v=>Number(v||0).toLocaleString();
  $('import-meter-detail').textContent=`已新增 ${n(p.imported)} 条 · 已有 ${n(p.duplicate)} 条 · 跳过 ${n(p.skipped)} 条 · 已提交 ${n(p.batches)} 批 · 用时 ${Math.round(p.elapsed_seconds||0)} 秒`+
    (p.scanned!=null?` · 已扫描 ${n(p.scanned)} 条`:'')+` · 诊断编号 ${(p.job_id||'').slice(0,8)}`+(job.error?'\n'+job.error:'');
}
if($('import-stop'))$('import-stop').onclick=async()=>{
  if(!activeImportJob)return;
  $('import-stop').disabled=true;
  try{await watchApi('/api/watch-jobs/'+activeImportJob+'/stop',{});$('import-meter-title').textContent='正在安全停止，保留已完成批次…';}
  catch(error){toast(error.message);$('import-stop').disabled=false;}
};
for(const p of ['qq','wechat'])for(const edge of ['start','end']) {
  const id='read-'+p+'-'+edge,key='chatlocal.'+id;
  try {
    const saved=localStorage.getItem(key);
    $(id).value=saved===null?(localStorage.getItem('chatlocal.read-'+edge)||''):saved;
    localStorage.setItem(key,$(id).value);
  }catch{}
  $(id).onchange=()=>{try{localStorage.setItem(key,$(id).value);}catch{}};
}
$('delete-platform').onchange=()=>{deleteSelection.clear();invalidateDelete();renderDeleteScope();};
$('delete-search').oninput=renderDeleteScope;
for(const id of ['delete-start','delete-end'])$(id).onchange=invalidateDelete;
$('delete-preview').onclick=async()=>{
  invalidateDelete();
  if(!$('delete-start').reportValidity()||!$('delete-end').reportValidity())return;
  const body={platform:$('delete-platform').value,conversations:[...deleteSelection],start:$('delete-start').value,end:$('delete-end').value};
  state.loading=true;syncControls();
  try {
    const result=await watchApi('/api/data/delete-preview',body);
    const names=result.all_conversations?`全部会话（${result.conversations} 个会话有匹配记录）`:dataSettings.imported.filter(r=>r.platform===body.platform&&deleteSelection.has(r.conversation_id)).map(r=>r.conversation).join('、');
    $('delete-summary').textContent=`${body.platform==='qq'?'QQ':'微信'} · ${names} · ${body.start} 至 ${body.end}（含当天）：${result.messages} 条消息，${result.media} 个媒体引用。共享媒体保留。预览 10 分钟内有效。`;
    deleteToken=result.messages?result.token:null;
    $('delete-confirm').textContent=result.all_conversations?`确认删除 ${body.platform==='qq'?'QQ':'微信'} 全部会话在此范围的 ${result.messages} 条消息`:`确认删除这 ${result.messages} 条消息`;
  }catch(error){$('delete-summary').textContent=error.message;}
  finally{state.loading=false;syncControls();}
};
$('delete-confirm').onclick=async()=>{
  if(!deleteToken)return;
  const token=deleteToken;deleteToken=null;
  await runDataJob('/api/data/delete',{token});
  $('delete-summary').textContent=$('read-progress').textContent;
  $('chat-timeline').replaceChildren();$('chat-location').textContent='记录已更新，请重新选择会话或打开证据。';
  $('chat-dialog').close();deleteSelection.clear();renderDeleteScope();syncControls();
};

function renderMediaRefreshScope() {
  $('media-refresh-picker').hidden=$('media-refresh-mode').value==='all';
  scopeChoices($('media-refresh-options'),(dataSettings?.imported||[]).filter(r=>r.platform===$('media-refresh-platform').value),mediaRefreshSelection,$('media-refresh-search').value);
}
$('media-refresh-platform').onchange=()=>{mediaRefreshSelection.clear();renderMediaRefreshScope();};
$('media-refresh-mode').onchange=renderMediaRefreshScope;
$('media-refresh-search').oninput=renderMediaRefreshScope;
$('media-refresh-run').onclick=async()=>{
  if(!$('media-refresh-start').reportValidity()||!$('media-refresh-end').reportValidity())return;
  if($('media-refresh-mode').value==='selected'&&!mediaRefreshSelection.size){toast('请勾选会话，或明确选择全部已导入会话');return;}
  const body={platform:$('media-refresh-platform').value,conversations:$('media-refresh-mode').value==='all'?[]:[...mediaRefreshSelection],
    start:$('media-refresh-start').value,end:$('media-refresh-end').value,load_stickers:$('media-refresh-stickers').checked};
  $('media-refresh-result').textContent='正在准备媒体刷新…';
  await runDataJob('/api/data/media-refresh',body,{keepDraft:true});
  $('media-refresh-result').textContent=$('read-progress').textContent;
  if($('chat-dialog').open&&chatPage)await pollChatWindow(true);
};
