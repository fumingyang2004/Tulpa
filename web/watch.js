'use strict';

async function watchApi(path,body,method) {
  const response=await fetch(path,{method:method||(body===undefined?'GET':'POST'),
    signal:AbortSignal.timeout(20000),
    ...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
  const data=await response.json();
  if(!response.ok) throw new Error(typeof data.detail==='string'?data.detail:'操作未完成，请重试');
  return data;
}
function watchCaption(card) {
  if(card.status==='paused') return '已暂停';
  if(card.error) return '回答未完成 · 可重试';
  if(card.pending_messages) return `${card.pending_messages} 条新消息 · 待下次更新`;
  if(!card.last_checked_at) return '待首次查询';
  const r=card.last_result;
  if(r.mode==='scheduled_question') return r.answer?.insufficient?'尚无足够证据':'已更新回答';
  if(r.initial) return '已建立当前结论';
  if(r.changes?.length) return `${r.changes.length} 项新进展`;
  if((r.new_uncertainties||r.uncertainties)?.length && !r.no_change) return '有待确认的新信息';
  return r.refresh_notice?'本地暂无新变化 · 刷新未完整':'暂无新变化';
}
function frequencyText(minutes) {
  return ({0:'仅手动',60:'每小时',360:'每 6 小时',720:'每 12 小时',1440:'每天',4320:'每 3 天',10080:'每周（每 7 天）'})[minutes]||`每 ${minutes} 分钟`;
}
function watchFrequency(card) {
  return [card.schedule_minutes?frequencyText(card.schedule_minutes):'',card.message_threshold?`每 ${card.message_threshold} 条新消息`:''].filter(Boolean).join(' 或 ')||'仅手动';
}
function watchTime(value) {
  if(!value) return '尚未检查';
  const date=typeof value==='number'?new Date(value*1000):new Date(value);
  return date.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
}
function scopeName(card) {
  const labels=card.conversation_scope.map(value=>state.conversations.find(c=>JSON.stringify(JSON.parse(c[1]))===JSON.stringify(JSON.parse(value)))?.[0]||JSON.parse(value)[1]);
  return card.platform_scope.map(p=>p==='qq'?'QQ':'微信').join(' + ')+' · '+(labels.length?labels.join('、'):'全部会话');
}
function selectedWatch() {return watchCards.find(c=>c.id===state.watchId);}
function setWatchSidebarExpanded(expanded,remember=true) {
  $('watch-sidebar').hidden=!expanded;
  $('toggle-watches').setAttribute('aria-expanded',String(expanded));
  $('toggle-watches').title=expanded?'收起关注卡':'展开关注卡';
  if(remember)try{localStorage.setItem('chatlocal.watchSidebarExpanded',String(expanded));}catch{}
}
function watchCountdown(card,now=Date.now()) {
  if(card.status==='paused')return '已暂停';
  if(activeWatchJob&&activeWatchJob.card_id===card.id&&activeWatchJob.status==='running')return '正在检查';
  const count=card.message_threshold?(card.last_checked_at?`消息累计 ${card.trigger_messages||0} / ${card.message_threshold} 条`:'待首次查询后开始计数'):'';
  if(!card.schedule_minutes||!card.next_run_at)return count||'仅手动检查';
  let seconds=Math.max(0,Math.ceil(card.next_run_at-now/1000));
  if(!seconds)return '已到时间，等待检查'+(count?' · '+count:'');
  const days=Math.floor(seconds/86400);seconds%=86400;
  const hours=Math.floor(seconds/3600);seconds%=3600;
  const minutes=Math.floor(seconds/60);seconds%=60;
  return '还有 '+(days?days+' 天 ':'')+(days||hours?hours+' 小时 ':'')+(days||hours||minutes?minutes+' 分 ':'')+seconds+' 秒'+(count?' · '+count:'');
}
function tickWatchCountdowns() {
  for(const node of document.querySelectorAll('[data-watch-countdown]')) {
    const card=watchCards.find(c=>c.id===node.dataset.watchCountdown);
    if(card)node.textContent=watchCountdown(card);
  }
}
function rememberWatch() {try{localStorage.setItem('chatlocal.lastWatch',state.watchId||'');}catch{}}
function leaveWatch() {
  state.watchId=null;watchThread=[];watchOlder=null;watchRequest++;rememberWatch();
  $('watch-threadbar').hidden=true;$('watch-older').hidden=true;$('watch-current').hidden=false;
  $('question').placeholder='随心输入，问问聊天里的事…';
  $('question').value='';
}
function updateWatchHeader() {
  const card=selectedWatch();if(!card) return;
  $('thread-title').textContent=card.title;$('watch-threadbar').hidden=false;$('watch-current').hidden=true;
  $('watch-thread-scope').textContent=scopeName(card);
  const next=card.status==='paused'?'已暂停':card.next_run_at?'下次 '+watchTime(card.next_run_at):'随时手动检查';
  $('watch-thread-schedule').textContent=`${watchFrequency(card)} · ${next} · 上次成功 ${watchTime(card.last_checked_at)}`;
  const countdown=document.createElement('strong');countdown.dataset.watchCountdown=card.id;countdown.textContent=watchCountdown(card);
  $('watch-thread-schedule').prepend(countdown,document.createTextNode(' · '));
  $('watch-pause').textContent=card.status==='paused'?'恢复':'暂停';
  $('watch-check-now').textContent=card.last_checked_at?'立即更新':'开始查询';
  $('range-chip').textContent='关注范围'+(card.conversation_scope.length?' · '+card.conversation_scope.length+' 个会话':'');
  $('range-chip').title=scopeName(card);$('range-chip').classList.add('filtered');
  $('question').placeholder='继续追问这件事…';
}
function renderWatches() {
  $('watch-count').textContent=watchCards.filter(c=>c.status==='active').length||'';
  const sidebar=document.createDocumentFragment(),list=document.createDocumentFragment();
  for(const card of watchCards) {
    const shortcut=document.createElement('button');shortcut.className='history-item watch-shortcut'+(state.watchId===card.id?' active':'');
    shortcut.setAttribute('aria-current',String(state.watchId===card.id));shortcut.title=card.title;
    const title=document.createElement('span');title.textContent=card.title;
    const brief=document.createElement('small');brief.textContent=watchCaption(card)+' · '+watchFrequency(card);
    shortcut.append(title,brief);shortcut.onclick=()=>openWatch(card.id);sidebar.append(shortcut);
    const entry=document.createElement('button');entry.className='watch-list-entry';
    const heading=document.createElement('strong');heading.textContent=card.title;
    const note=document.createElement('span');note.textContent=watchCaption(card)+' · '+watchFrequency(card);
    const countdown=document.createElement('small');countdown.dataset.watchCountdown=card.id;countdown.textContent=watchCountdown(card);
    entry.append(heading,note,countdown);entry.onclick=()=>openWatch(card.id);list.append(entry);
  }
  if(!watchCards.length) {
    const p=document.createElement('p');p.className='empty-note';p.textContent='创建关注后，进展会回到同一段对话。';list.append(p);
    const hint=document.createElement('p');hint.className='empty-note';hint.textContent='暂无关注，点击右侧菜单创建。';sidebar.append(hint);
  }
  $('watch-sidebar').replaceChildren(sidebar);$('watch-list').replaceChildren(list);
  if(state.watchId) updateWatchHeader();
}
async function fetchWatchThread(older=false) {
  const id=state.watchId;if(!id) return;
  const token=++watchRequest;
  const page=await watchApi('/api/watch-cards/'+id+'/thread'+(older&&watchOlder?'?before='+watchOlder:''));
  if(token!==watchRequest || id!==state.watchId) return;
  const oldHeight=$('messages-scroll').scrollHeight;
  // Refresh only the newest page, preserving already-loaded older history.
  if(older) watchThread=[...page.entries,...watchThread];
  else {const first=page.entries[0]?.id||0;watchThread=[...watchThread.filter(e=>e.id<first),...page.entries];}
  watchThread=[...new Map(watchThread.map(e=>[e.id,e])).values()];
  if(older || watchThread.length<=page.entries.length) watchOlder=page.older;
  watchCards=watchCards.some(c=>c.id===id)?watchCards.map(c=>c.id===id?page.card:c):[page.card,...watchCards];
  renderWatches();renderWatchThread();syncControls();
  if(older) $('messages-scroll').scrollTop+=$('messages-scroll').scrollHeight-oldHeight;
}
async function openWatch(id,fromJob=false) {
  if(!fromJob && (state.busy||state.loading)) return;
  if(typeof leaveWorkspace==='function')leaveWorkspace();
  state.loading=true;syncControls();
  try {
    if(state.watchId!==id) {state.watchId=id;watchThread=[];watchOlder=null;$('messages').replaceChildren();$('question').value='';}
    rememberWatch();$('watches-dialog').close();
    await fetchWatchThread();renderHistory();renderWatchThread(true);updateWatchHeader();
  } catch(error) {toast(error.message);}
  finally {state.loading=false;syncControls();fitInput();if(mobile.matches)sidebar(false);}
}
function renderWatchThread(forceBottom=false) {
  const follow=forceBottom||nearBottom(),host=$('messages');$('welcome').hidden=true;
  $('watch-older').hidden=!watchOlder;
  const rows=[];
  const card=selectedWatch();
  if(card && !watchOlder && !watchThread.some(e=>e.kind==='setup')) rows.push({key:'original-topic',role:'user',text:card.watch_query});
  for(const entry of watchThread) {
    if(['setup','settings','question'].includes(entry.kind)) rows.push({key:entry.id+'u',role:'user',text:entry.question});
    rows.push({key:entry.id+'a',role:'assistant',entry});
  }
  const waiting=activeWatchJob?.card_id===state.watchId && activeWatchJob.status==='running';
  if(waiting && !watchThread.some(e=>e.status==='running')) rows.push({key:'pending',role:'assistant',entry:{html:'',status:'running',created_at:null,kind:activeWatchJob.kind,trigger:activeWatchJob.trigger}});
  const existing=new Map([...host.children].map(n=>[n.dataset.key,n]));
  for(const row of rows) {
    let article=existing.get(row.key);
    if(!article) {article=document.createElement('article');article.className='message '+row.role;article.dataset.key=row.key;}
    if(row.role==='user') {
      if(article.dataset.text!==row.text) {const bubble=document.createElement('div');bubble.className='user-bubble';bubble.textContent=row.text;article.replaceChildren(bubble);article.dataset.text=row.text;}
    } else {
      const entry=row.entry;
      if(!article.childNodes.length) {
        const stamp=document.createElement('div');stamp.className='watch-run-stamp';
        const content=document.createElement('div');content.className='assistant-content';
        article.append(stamp,content);
      }
      const stamp=article.firstElementChild,content=article.lastElementChild;
      const label=entry.kind==='question'?'追问':entry.kind==='check'?(entry.trigger==='scheduled'?'定时更新':entry.trigger==='message_count'?'消息数触发更新':'手动更新'):'关注设置';
      stamp.textContent=label+(entry.created_at?' · '+watchTime(entry.created_at):'');
      if(entry.status==='running') {
        if(!content.querySelector('.pending-line')) content.innerHTML='<div class="pending-line" role="status"><span class="pulse"></span><span class="pending-label"></span></div><details class="turn-activity"><summary>查看查询过程</summary><pre></pre></details>';
        content.querySelector('.pending-label').textContent=activeWatchJob?.events.at(-1)||'正在准备…';
        content.querySelector('pre').textContent=(activeWatchJob?.events||[]).join('\n');article.dataset.html='';
      } else if(article.dataset.html!==entry.html) {content.innerHTML=entry.html;article.dataset.html=entry.html;}
    }
    host.append(article);existing.delete(row.key);
  }
  for(const node of existing.values()) node.remove();
  if(!rows.length) {const note=document.createElement('p');note.className='empty-note';note.textContent='还没有回答。点击“开始查询”，或在下方提问。';host.append(note);}
  if(follow) requestAnimationFrame(bottom);
}
async function loadWatches() {
  const [cards,sync]=await Promise.all([watchApi('/api/watch-cards'),watchApi('/api/refresh-status')]);watchCards=cards.cards;
  $('sync-summary').textContent='最近一次刷新：'+sync.platforms.map(p=>`${p.platform==='qq'?'QQ':'微信'} ${p.status==='error'?'刷新失败':p.status==='running'?'刷新中':p.last_sync_at?('+'+p.added+' · '+p.last_sync_at.slice(11,16)):'尚未刷新'}`).join(' / ');
  $('sync-summary').title=sync.platforms.map(p=>`${p.platform==='qq'?'QQ':'微信'}：${p.detail}；最后成功同步 ${p.last_sync_at||'无'}`).join('\n');
  if(!autoRefreshSaving){$('auto-refresh').value=sync.auto_refresh.minutes;$('auto-refresh-stickers').checked=sync.auto_refresh.load_stickers;}
  renderWatches();
  if(state.watchId && !selectedWatch()) {leaveWatch();$('messages').replaceChildren();renderMessages();renderHistory();updateRange();}
  for(const job of sync.running_jobs||[])if(!watchPollers.has(job.id))void monitorWatchJob(job.id,job);
  syncControls();
}
async function monitorWatchJob(id,seed={}) {
  if(watchPollers.has(id)) return;
  watchPollers.add(id);watchJobs.set(id,{id,kind:'unknown',status:'running',...seed});syncControls();
  try {
    while(true) {
      const job=await watchApi('/api/watch-jobs/'+id);watchJobs.set(id,job);syncControls();
      if(typeof renderImportProgress==='function')renderImportProgress(job);
      $('watch-job-events').textContent=job.events.join('\n');$('watch-job-status').textContent=job.events.at(-1)||'正在准备…';
      if(['read','catalog','delete','media-refresh'].includes(job.kind)){$('read-progress').hidden=false;$('read-progress').textContent=job.events.join('\n')||'正在准备…';}
      if(job.kind==='media-refresh')$('media-refresh-result').textContent=$('read-progress').textContent;
      if(state.watchId===job.card_id) await fetchWatchThread();
      syncControls();
      if(job.status!=='running') {
        if(job.status==='error') throw new Error(job.error);
        const label=job.result?.summary||(Array.isArray(job.result)?job.result.map(r=>`${r.platform==='qq'?'QQ':'微信'} ${r.status==='ok'?'+'+r.added:r.detail}${r.timing?.total_seconds!=null?' · '+Number(r.timing.total_seconds).toFixed(1)+' 秒':''}`).join(' / '):job.kind==='question'?'已回答':watchCaption(job.result));
        $('watch-job-status').textContent=label;
        if(['read','catalog','delete','media-refresh'].includes(job.kind))$('read-progress').textContent=job.events.join('\n')+'\n'+label;
        if(job.kind==='media-refresh')$('media-refresh-result').textContent=label;
        if(!['scheduled','message_count'].includes(job.trigger) || job.result?.last_result?.changes?.length || job.result?.last_result?.new_uncertainties?.length || job.result?.last_result?.stop_reason)toast(label);
        break;
      }
      await new Promise(resolve=>setTimeout(resolve,1000));
    }
  } catch(error) {toast(error.message);$('watch-job-status').textContent=error.message;if($('data-dialog').open)$('read-progress').textContent=error.message;}
  finally {
    if(watchJob===id)watchStopRequested=false;
    watchJobs.delete(id);watchPollers.delete(id);syncControls();
    await loadWatches().catch(()=>{});
    if(state.watchId) await fetchWatchThread().catch(()=>{});
    if(!state.busy&&!state.loading) {try{const r=await call('refresh',[]);setData(r[0],r[1]);}catch{}}
  }
}
async function runWatchJob(path,body,{open=false,clearQuestion=false}={}) {
  const kind=path==='/api/sync'?'sync':path.endsWith('/ask')?'question':'check';
  const conflict=kind==='question'?[...watchJobs.values()].some(j=>j.kind!=='sync'):state.watchBusy;
  if(jobStarting||conflict||state.busy||state.loading) {toast('请等待当前操作完成；可以先编辑问题。');return;}
  const starting={kind};jobStarting=starting;syncControls();
  try {
    const result=await watchApi(path,body);
    if(clearQuestion && $('question').value.trim()===body.question)$('question').value='';
    if(open&&result.card_id) await openWatch(result.card_id,true);
    if(jobStarting===starting)jobStarting=null;
    await monitorWatchJob(result.job_id,{kind,card_id:result.card_id});
  } catch(error) {toast(error.message);}
  finally {if(jobStarting===starting)jobStarting=null;syncControls();}
}
async function sendWatchQuestion() {
  const question=$('question').value.trim();if(!question||!state.ready||chatBlockedByJob())return;
  await runWatchJob('/api/watch-cards/'+state.watchId+'/ask',{question,profile:state.profile,vision_mode:state.visionMode},{clearQuestion:true});
}
async function stopWatch() {
  if(!watchJob)return;watchStopRequested=true;syncControls();
  try {await watchApi('/api/watch-jobs/'+watchJob+'/stop',{});toast('正在停止；若正在刷新数据，将在该步骤结束后停止。');}
  catch(error) {watchStopRequested=false;syncControls();toast(error.message);}
}
function renderWatchChoices() {
  const q=$('watch-conversation-search').value.toLowerCase(),platforms=[$('watch-qq').checked?'qq':null,$('watch-wechat').checked?'wechat':null];
  const fragment=document.createDocumentFragment();
  for(const [label,value] of state.conversations) {
    if(!platforms.includes(JSON.parse(value)[0])||!label.toLowerCase().includes(q))continue;
    const row=document.createElement('label');row.className='conversation-option';
    const key=JSON.stringify(JSON.parse(value)),input=document.createElement('input');input.type='checkbox';input.checked=watchSelection.has(key);
    input.onchange=()=>{input.checked?watchSelection.add(key):watchSelection.delete(key);selectionSummary();};
    const text=document.createElement('span');text.textContent=label.replace(/^wechat/,'微信').replace(/^qq/,'QQ');row.append(input,text);fragment.append(row);
  }
  $('watch-conversations').replaceChildren(fragment);selectionSummary();
}
function selectionSummary() {
  const platforms=[$('watch-qq').checked?'qq':null,$('watch-wechat').checked?'wechat':null];
  const selected=[...watchSelection].filter(v=>platforms.includes(JSON.parse(v)[0]));
  $('watch-selection-summary').textContent=selected.length?`已选择 ${selected.length} 个会话`:'未指定会话：将关注以上平台的全部已导入会话';
}
function setWatchFrequency(value) {
  const found=[...$('watch-frequency').options].some(o=>o.value===String(value));
  $('watch-frequency').value=found?String(value):'custom';$('watch-custom-minutes').value=value||1440;
  $('watch-custom-frequency').hidden=found;$('watch-custom-minutes').required=!found;
}
function openWatchForm(query='',ids=[],scope=null,card=null) {
  if(state.watchBusy||state.busy||state.loading){toast('请等待当前操作完成。');return;}
  watchEditId=card?.id||null;watchSeeds=[...new Set(ids)].slice(0,20);
  watchSelection=new Set((card?.conversation_scope||(scope?[JSON.stringify(scope)]:state.filters.conversations)).map(v=>JSON.stringify(JSON.parse(v))));
  $('watch-title').value=card?.title||query.slice(0,60);$('watch-query').value=card?.watch_query||query.slice(0,1600);
  const platforms=card?.platform_scope||(scope?[scope[0]]:state.filters.platforms);
  $('watch-qq').checked=platforms.includes('qq');$('watch-wechat').checked=platforms.includes('wechat');
  setWatchFrequency(card?.schedule_minutes||0);$('watch-profile').value=card?.profile||state.profile;
  $('watch-count-enabled').checked=Boolean(card?.message_threshold);$('watch-message-threshold').value=card?.message_threshold||100;setWatchCountEnabled();
  $('watch-stickers').checked=card?Boolean(card.load_stickers):false;
  $('watch-create-title').textContent=card?'关注设置':'新建关注';$('watch-submit').textContent=card?'保存设置':'创建并查询';
  $('watch-conversation-search').value='';renderWatchChoices();$('watch-create-dialog').showModal();$('watch-title').focus();
}
function editWatch() {const card=selectedWatch();if(card)openWatchForm('',[],null,card);}
async function watchEvidence(mid) {
  try {const page=await watchApi('/api/chat-context?anchor_message_id='+mid+'&before=0&after=0');openWatchForm('关注这件事的后续进展',[mid],[page.platform,page.conversation_id]);}
  catch(error){toast(error.message);}
}
async function deleteWatch(card) {
  if(!card||state.watchBusy||state.busy||state.loading)return;
  if(!confirm(`删除关注“${card.title}”？\n\n会删除这张卡、全部检查和追问记录，无法撤销。QQ／微信原始记录保留。`))return;
  state.loading=true;syncControls();
  try {
    await watchApi('/api/watch-cards/'+card.id,undefined,'DELETE');
    watchCards=watchCards.filter(c=>c.id!==card.id);
    if(state.watchId===card.id){leaveWatch();$('messages').replaceChildren();renderMessages(true);renderHistory();updateRange();}
    renderWatches();toast('关注已删除');
  }catch(error){toast(error.message);}finally{state.loading=false;syncControls();}
}
$('toggle-watches').onclick=()=>setWatchSidebarExpanded($('watch-sidebar').hidden);
try{setWatchSidebarExpanded(localStorage.getItem('chatlocal.watchSidebarExpanded')==='true',false);}catch{setWatchSidebarExpanded(false,false);}
$('open-watches').onclick=async()=>{$('watches-dialog').showModal();try{await loadWatches();}catch(error){toast(error.message);}};
$('watch-new').onclick=()=>openWatchForm();
$('watch-current').onclick=()=>{
  const question=[...state.history].reverse().find(m=>m.role==='user');
  openWatchForm(question?new DOMParser().parseFromString(messageText(question),'text/html').body.textContent:$('question').value);
};
$('watch-edit').onclick=editWatch;
$('watch-delete-current').onclick=()=>deleteWatch(selectedWatch());
$('watch-check-now').onclick=()=>runWatchJob('/api/watch-cards/'+state.watchId+'/check',{});
$('watch-pause').onclick=async()=>{
  const card=selectedWatch();if(!card||state.watchBusy||state.loading)return;
  state.loading=true;syncControls();
  try{await watchApi('/api/watch-cards/'+card.id+'/pause',{paused:card.status!=='paused'});await loadWatches();await fetchWatchThread();}
  catch(error){toast(error.message);}finally{state.loading=false;syncControls();}
};
$('watch-older').onclick=()=>fetchWatchThread(true).catch(error=>toast(error.message));
$('watch-conversation-search').oninput=renderWatchChoices;$('watch-qq').onchange=renderWatchChoices;$('watch-wechat').onchange=renderWatchChoices;
$('watch-clear-selection').onclick=()=>{watchSelection.clear();renderWatchChoices();};
$('watch-frequency').onchange=()=>{const custom=$('watch-frequency').value==='custom';$('watch-custom-frequency').hidden=!custom;$('watch-custom-minutes').required=custom;};
function setWatchCountEnabled() {
  const enabled=$('watch-count-enabled').checked;
  $('watch-count-field').hidden=!enabled;$('watch-message-threshold').disabled=!enabled;$('watch-message-threshold').required=enabled;
}
$('watch-count-enabled').onchange=setWatchCountEnabled;
setWatchCountEnabled();
$('watch-form').onsubmit=async event=>{
  event.preventDefault();if(state.watchBusy||state.busy||state.loading)return;
  const platforms=[$('watch-qq').checked?'qq':null,$('watch-wechat').checked?'wechat':null].filter(Boolean);
  if(!platforms.length){toast('请至少选择一个平台');return;}
  const body={title:$('watch-title').value,watch_query:$('watch-query').value,platforms,
    conversations:[...watchSelection].filter(v=>platforms.includes(JSON.parse(v)[0])),seed_ids:watchSeeds,
    profile:$('watch-profile').value,load_stickers:$('watch-stickers').checked,
    message_threshold:$('watch-count-enabled').checked?Number($('watch-message-threshold').value):0,
    schedule_minutes:Number($('watch-frequency').value==='custom'?$('watch-custom-minutes').value:$('watch-frequency').value)};
  if(watchEditId) {
    state.loading=true;syncControls();
    try{await watchApi('/api/watch-cards/'+watchEditId,body,'PUT');$('watch-create-dialog').close();await loadWatches();await fetchWatchThread();toast('关注设置已保存');}
    catch(error){toast(error.message);}finally{state.loading=false;syncControls();}
  }else{$('watch-create-dialog').close();void runWatchJob('/api/watch-cards',body,{open:true});}
};
$('sync-messages').onclick=()=>runWatchJob('/api/sync',{load_stickers:$('load-stickers').checked});
let autoRefreshSaving=false;
async function saveAutoRefresh() {
  autoRefreshSaving=true;$('auto-refresh').disabled=true;$('auto-refresh-stickers').disabled=true;
  try{await watchApi('/api/auto-refresh',{minutes:Number($('auto-refresh').value),load_stickers:$('auto-refresh-stickers').checked});toast('自动刷新设置已保存');}
  catch(error){toast(error.message);}
  finally{autoRefreshSaving=false;$('auto-refresh').disabled=false;$('auto-refresh-stickers').disabled=false;await loadWatches().catch(()=>{});}
}
$('auto-refresh').onchange=saveAutoRefresh;$('auto-refresh-stickers').onchange=saveAutoRefresh;
setInterval(async()=>{if(state.ready&&!state.loading){try{await loadWatches();if(state.watchId)await fetchWatchThread();}catch{}}},15000);
setInterval(()=>{if(!document.hidden)tickWatchCountdowns();},1000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)tickWatchCountdowns();});
for(const platform of ['qq','wechat'])$('reconnect-'+platform).onclick=async()=>{
  if(state.watchBusy||state.loading||state.busy){toast('请等待当前操作完成。');return;}
  try{await watchApi('/api/sync/reconnect',{platform});await loadWatches();toast('进度已准备重新接续；请点击刷新消息。');}catch(error){toast(error.message);}
};
void init();
