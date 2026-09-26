'use strict';

const $ = (id) => document.getElementById(id);
const API = '/legacy';
const READ_FIELDS={qq_per_chat:'read-qq-per-chat',wechat_per_chat:'read-wechat-per-chat'};
const defaults = () => ({platforms: ['qq', 'wechat'], conversations: [], start: '', end: '', keywords: ''});
const state = {ready: false, busy: false, loading: false, hash: crypto.randomUUID(), sid: null,
  functions: {}, choices: [], conversations: [], history: [], filters: defaults(), evidence: '',
  watchBusy:false, watchId:null, progress: '', accepted: false, stopRequested: false, stopSent: false, draftConversations: new Set(),
  profiles: [], profile: 'deep', visionMode:'ocr', nativeVisionAvailable:false, showReasoning:true};
let watchCards=[],watchSeeds=[],watchSelection=new Set(),watchJob=null,activeWatchJob=null;
let watchEditId=null,watchThread=[],watchOlder=null,watchRequest=0,watchStopRequested=false;
const watchJobs=new Map(),watchPollers=new Set();
let jobStarting=null;
function chatBlockedByJob() {
  return [...watchJobs.values(),...(jobStarting?[jobStarting]:[])].some(j=>
    ['delete','catalog','unknown'].includes(j.kind) || (state.watchId && ['question','check'].includes(j.kind)));
}
const mobile = matchMedia('(max-width: 760px)');
let toastTimer;

function toast(text) {
  $('toast').textContent = text; $('toast').hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 5000);
}
function remember() { try { localStorage.setItem('chatlocal.lastConversation', state.sid || ''); } catch {} }
function sidebar(open) {
  document.body.classList.toggle('sidebar-closed', !open);
  $('open-sidebar').hidden = open;
  $('sidebar-scrim').hidden = !(open && mobile.matches);
}
function focusComposer() { if (mobile.matches) sidebar(false); $('question').focus(); }
function syncControls() {
  state.watchBusy=Boolean(jobStarting||watchJobs.size);
  activeWatchJob=[...watchJobs.values()].find(j=>j.card_id===state.watchId && j.status==='running')||null;
  watchJob=activeWatchJob?.id||null;
  const workspaceRunning=Boolean(state.workspaceId && state.workspaceTaskRunning);
  const locked = !state.ready || state.busy || state.loading || chatBlockedByJob() || workspaceRunning;
  const dataLocked = !state.ready || state.busy || state.loading || state.watchBusy;
  $('reasoning-toggle').hidden=Boolean(state.workspaceId||state.watchId);
  $('question').disabled = !state.ready || state.loading;
  $('send-button').disabled = locked || !$('question').value.trim();
  $('send-button').hidden = state.busy || workspaceRunning;
  const watchRunning=state.watchId && activeWatchJob?.status==='running' && activeWatchJob.card_id===state.watchId && activeWatchJob.kind!=='sync';
  $('stop-button').hidden = !state.busy && !watchRunning && !workspaceRunning;
  $('stop-button').disabled = watchRunning?watchStopRequested:state.stopRequested;
  if (watchRunning) $('send-button').hidden=true;
  for (const id of ['new-chat','header-range','range-chip','more-options','open-data','intensity-slider','toggle-intensity']) $(id).disabled = locked;
  for (const id of ['read-latest','load-stickers','sync-messages','watch-current','watch-new','watch-submit']) $(id).disabled = dataLocked;
  $('vision-toggle').disabled=locked||!state.nativeVisionAvailable;
  Object.values(READ_FIELDS).forEach(id=>$(id).disabled=dataLocked);
  if(typeof syncDataControls==='function')syncDataControls(dataLocked);
  $('intensity-stops').querySelectorAll('button').forEach(b => b.disabled=locked);
  $('messages').setAttribute('aria-busy', String(state.busy));
  document.querySelectorAll('.history-item').forEach(b => b.disabled = !state.ready || state.busy || state.loading);
  document.querySelectorAll('.history-delete, .watch-delete').forEach(b => b.disabled = dataLocked);
  for(const id of ['watch-edit','watch-pause','watch-delete-current']) $(id).disabled=dataLocked;
  $('watch-check-now').disabled=dataLocked || watchCards.find(c=>c.id===state.watchId)?.status==='paused';
}
function fitInput() {
  const input=$('question'); input.style.height='auto'; input.style.height=Math.min(180, Math.max(52,input.scrollHeight))+'px';
  document.querySelector('.workspace').style.setProperty('--composer-height',document.querySelector('.composer-dock').offsetHeight+'px');
  syncControls();
}
function closeMenu() { $('composer-menu').hidden=true; $('more-options').setAttribute('aria-expanded','false'); }
function titleOf(label) { return label.replace(/^\d{2}-\d{2}[T ]\d{2}:\d{2} · /, ''); }
function savePicker(update) {
  if (update?.choices) state.choices = update.choices;
  if (update && Object.hasOwn(update, 'value')) { state.sid=update.value; remember(); }
  renderHistory();
}
function renderHistory() {
  const query=$('history-search').value.toLocaleLowerCase();
  const fragment=document.createDocumentFragment();
  for (const [label,id] of state.choices) {
    const title=titleOf(label);
    if (query && !title.toLocaleLowerCase().includes(query)) continue;
    const button=document.createElement('button');
    button.className='history-item'+(!state.watchId && state.sid===id?' active':'');
    button.textContent=title; button.title=label.replace('T',' ');
    button.disabled=!state.ready || state.busy || state.loading;
    button.setAttribute('aria-current',!state.watchId && state.sid===id?'true':'false');
    button.addEventListener('click',() => loadSession(id));
    const row=document.createElement('div');row.className='history-row';
    const remove=document.createElement('button');remove.type='button';remove.className='icon-button history-delete';
    remove.title='删除对话';remove.setAttribute('aria-label','删除对话：'+title);remove.disabled=button.disabled;
    remove.innerHTML='<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/></svg>';
    remove.onclick=()=>deleteSession(id,title);row.append(button,remove);fragment.append(row);
  }
  if (!fragment.childNodes.length) {
    const text=document.createElement('p'); text.className='empty-note';
    text.textContent=query?'没有找到相关对话':'还没有对话，直接输入问题开始。'; fragment.append(text);
  }
  $('history-list').replaceChildren(fragment);
  const current=state.choices.find(c => c[1]===state.sid);
  if(!state.watchId && !state.workspaceId) $('thread-title').textContent=current?titleOf(current[0]):'新对话';
}

// Consume full Gradio queue events (simple_format), sharing its existing State,
// cancellation, data limits and persistence. No model endpoint/key in the browser.
async function call(name, data, update) {
  const fn=state.functions[name];
  if (fn===undefined) throw new Error('服务尚未准备好，请刷新页面。');
  // Background status reads must not open a second consumer of the chat SSE
  // queue: two readers sharing session_hash can steal each other's events.
  if(name==='refresh') {
    const r=await fetch(`${API}/gradio_api/run/refresh`,{method:'POST',headers:{'Content-Type':'application/json'},
      signal:AbortSignal.timeout(20000),body:JSON.stringify({fn_index:fn,data,session_hash:state.hash})});
    if(!r.ok)throw Error('聊天数据状态读取失败，请稍后重试。');
    const result=(await r.json()).data;update?.(result,true);return result;
  }
  const joined=await fetch(`${API}/gradio_api/queue/join`, {method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({fn_index:fn,data,session_hash:state.hash,simple_format:true,event_data:null,trigger_id:null})});
  if (!joined.ok) throw new Error('连接服务失败，请确认本机服务正在运行。');
  const {event_id}=await joined.json();
  const stream=await fetch(`${API}/gradio_api/queue/data?session_hash=${encodeURIComponent(state.hash)}`);
  if (!stream.ok || !stream.body) throw new Error('无法接收结果，请刷新后重试。');
  const reader=stream.body.getReader(), decoder=new TextDecoder(); let buffer='';
  try {
    while (true) {
      const chunk=await reader.read();
      if (chunk.done) throw new Error('连接中断，已保存的对话可以从左侧恢复。');
      buffer+=decoder.decode(chunk.value,{stream:true});
      let end;
      while ((end=buffer.indexOf('\n'))>=0) {
        const line=buffer.slice(0,end).trimEnd(); buffer=buffer.slice(end+1);
        if (!line.startsWith('data: ')) continue;
        const event=JSON.parse(line.slice(6));
        if (event.event_id && event.event_id!==event_id) continue;
        if (event.msg==='process_generating' || event.msg==='process_completed') {
          if (event.success===false) throw new Error('本次操作未完成，请稍后重试。');
          const result=event.output?.data;
          if (result) update?.(result,event.msg==='process_completed');
          if (event.msg==='process_completed') return result;
        }
        if (event.msg==='unexpected_error') throw new Error('本机服务发生错误，请稍后重试。');
      }
    }
  } finally { await reader.cancel().catch(() => {}); }
}

function nearBottom() { const pane=$('messages-scroll'); return pane.scrollHeight-pane.scrollTop-pane.clientHeight<140; }
function bottom() { const pane=$('messages-scroll'); pane.scrollTop=pane.scrollHeight; $('jump-bottom').hidden=true; }
function messageText(message) {
  return typeof message.content==='string'?message.content:(message.content||[]).map(part => part.text||'').join('');
}
let reasoningPreferenceVersion=0,reasoningSave=Promise.resolve();
function setShowReasoning(show,rememberChoice=true) {
  state.showReasoning=Boolean(show);
  document.body.classList.toggle('hide-model-reasoning',!state.showReasoning);
  $('reasoning-toggle').setAttribute('aria-checked',String(state.showReasoning));
  if(rememberChoice){
    reasoningPreferenceVersion++;
    try{localStorage.setItem('tulpa.showReasoning',String(state.showReasoning));}catch{}
    const value=state.showReasoning;
    reasoningSave=reasoningSave.then(async()=>{
      const r=await fetch('/api/ui-preferences',{method:'PUT',headers:{'Content-Type':'application/json','X-ChatWeave-UI':'1'},body:JSON.stringify({show_reasoning:value})});
      if(!r.ok)throw Error('显示设置未能保存，当前页面的选择仍然生效。');
    }).catch(()=>toast('显示设置未能保存，当前页面的选择仍然生效。'));
  }
}
async function loadReasoningPreference() {
  const version=reasoningPreferenceVersion;
  try{
    const r=await fetch('/api/ui-preferences',{cache:'no-store'});
    if(!r.ok)return;
    const data=await r.json();
    if(version===reasoningPreferenceVersion && typeof data.show_reasoning==='boolean')setShowReasoning(data.show_reasoning,false);
  }catch{} // Offline startup keeps the cached display preference.
}
function reasoningPosition(node) {
  const details=node.querySelector('.model-reasoning'),body=details?.querySelector('.reasoning-body');
  return details?{open:details.open,top:body.scrollTop,follow:body.scrollHeight-body.scrollTop-body.clientHeight<50}:null;
}
function restoreReasoningPosition(node,saved) {
  const details=node.querySelector('.model-reasoning');
  if(!details)return;
  if(saved)details.open=saved.open;
  const body=details.querySelector('.reasoning-body');
  body.scrollTop=!saved||saved.follow?body.scrollHeight:saved.top;
}
function pending(node,html) {
  if (!node.querySelector('.pending-line')) node.innerHTML='<div class="pending-reasoning"></div><div class="pending-line" role="status"><span class="pulse" aria-hidden="true"></span><span class="pending-label"></span></div><details class="turn-activity"><summary>查看查询过程</summary><pre></pre></details>';
  const reasoning=node.querySelector('.pending-reasoning');
  if(reasoning.dataset.html!==html){
    const saved=reasoningPosition(reasoning),template=document.createElement('template');
    template.innerHTML=html;
    const incoming=template.content.querySelector('.model-reasoning');
    reasoning.replaceChildren(...(incoming?[incoming]:[]));
    reasoning.dataset.html=html;
    restoreReasoningPosition(reasoning,saved);
  }
  const lines=state.progress.split('\n').filter(Boolean);
  let text=lines.at(-1)||'正在查找聊天里的线索…';
  if (text.startsWith('请求模型')) text='正在整理线索…';
  if (text.includes('：{')) text=text.split('：{')[0]+'…';
  if (state.stopRequested) text='正在停止…';
  node.querySelector('.pending-label').textContent=text.slice(0,110);
  node.querySelector('.turn-activity pre').textContent=state.progress || '准备查询…';
}
function renderMessages(forceBottom=false) {
  if(state.workspaceId)return;
  if(state.watchId) {renderWatchThread(forceBottom);return;}
  const follow=forceBottom || nearBottom(), host=$('messages');
  $('welcome').hidden=state.history.length>0;
  while (host.children.length>state.history.length) host.lastElementChild.remove();
  state.history.forEach((message,index) => {
    let article=host.children[index];
    if (!article) { article=document.createElement('article'); host.append(article); }
    const role=message.role==='user'?'user':'assistant', html=messageText(message);
    const waiting=state.busy && index===state.history.length-1 && role==='assistant' && html.includes('pending-answer');
    if (article.dataset.role!==role) {
      article.className='message '+role; article.dataset.role=role; article.dataset.html='';
      article.innerHTML=role==='user'?'<div class="user-bubble"></div>':'<div class="assistant-content"></div><div class="assistant-actions"></div>';
    }
    const content=article.firstElementChild;
    if (waiting) { pending(content,html); article.dataset.html=''; article.lastElementChild.hidden=true; }
    else if (article.dataset.html!==html) {
      // Only backend-produced HTML is inserted. User/model/source text is escaped
      // by chatlocal.render; never interpret provider text as HTML on this client.
      const saved=reasoningPosition(content);
      content.innerHTML=html; article.dataset.html=html;
      restoreReasoningPosition(content,saved);
    }
    if (role==='assistant' && !waiting) {
      const actions=article.lastElementChild; actions.hidden=false;
      if (!actions.childNodes.length) {
        const copy=document.createElement('button'); copy.className='icon-button'; copy.title='复制回答'; copy.setAttribute('aria-label','复制回答');
        copy.innerHTML='<svg><use href="#i-copy"/></svg>';
        copy.onclick=async () => {
          const claims=[...content.querySelectorAll('.claim-text')].map(p => p.textContent);
          const answerOnly=content.cloneNode(true);
          answerOnly.querySelectorAll('.model-reasoning,.turn-activity').forEach(n=>n.remove());
          const text=claims.length?claims.join('\n\n'):(content.querySelector('.answer-note')?.textContent || answerOnly.textContent);
          try { await navigator.clipboard.writeText(text); toast('已复制回答'); } catch { toast('复制失败，可以选中文字复制。'); }
        };
        const source=document.createElement('button'); source.className='source-button'; source.textContent='本轮来源';
        source.onclick=() => { $('all-evidence').innerHTML=state.evidence; $('evidence-dialog').showModal(); };
        const watch=document.createElement('button');watch.className='source-button';watch.textContent='关注此事';
        watch.onclick=()=>{
          const previous=state.history[index-1];
          const prompt=previous?new DOMParser().parseFromString(messageText(previous),'text/html').body.textContent:'';
          const ids=[...content.querySelectorAll('a.open-chat')].map(a=>Number(a.dataset.messageId));
          openWatchForm(prompt,ids);
        };
        actions.append(copy,source,watch);
      }
      actions.querySelector('.source-button').hidden=index!==state.history.length-1 || !state.evidence.includes('<details') || state.busy;
    }
  });
  if (follow) requestAnimationFrame(bottom);
}
function updateRange() {
  if(state.workspaceId)return;
  if(state.watchId) {updateWatchHeader();return;}
  const f=state.filters, filtered=JSON.stringify(f)!==JSON.stringify(defaults());
  const names=f.platforms.map(p => p==='qq'?'QQ':'微信').join(' + ');
  const range=[names,f.conversations.length?`${f.conversations.length} 个会话`:'全部会话',f.start||f.end?`${f.start||'不限'} 至 ${f.end||'现在'}`:'时间随问题'];
  if (f.keywords) range.push(`关键词：${f.keywords}`);
  $('range-chip').textContent=filtered?'已设置范围':'全部聊天';
  $('range-chip').title=range.join(' · '); $('range-chip').classList.toggle('filtered',filtered);
}
function selectProfile(id,rememberChoice=true) {
  const index=state.profiles.findIndex(p=>p.id===id);
  if (index<0) return;
  const p=state.profiles[index], b=p.budget;
  state.profile=id; $('intensity-slider').value=String(index);
  $('intensity-slider').setAttribute('aria-valuetext',p.label);
  $('intensity-slider').style.setProperty('--intensity-fill',`${index/(state.profiles.length-1)*100}%`);
  $('intensity-label').textContent=p.label;
  $('intensity-chip-label').textContent=p.label;
  $('intensity-summary').textContent=`最多 ${b.max_tool_calls} 次查询 · ${b.max_seconds/60} 分钟`;
  $('intensity-description').textContent=p.description;
  $('intensity-budget').textContent=`思考：${b.reasoning_effort==='none'?'关闭':b.reasoning_effort} · ${b.max_requests} 次模型请求 · ${b.max_messages} 条相关消息 · ${b.max_evidence_chars.toLocaleString()} 字符证据 · 单次生成 ${b.max_output_tokens.toLocaleString()} tokens。`;
  $('intensity-stops').querySelectorAll('button').forEach((button,i)=>button.setAttribute('aria-pressed',String(i===index)));
  if (rememberChoice) { try { localStorage.setItem('chatlocal.agentProfile',id); } catch {} }
  fitInput();
}
async function loadProfiles() {
  const response=await fetch('/api/agent-presets');
  if (!response.ok) throw new Error('无法加载分析强度，请刷新页面。');
  const catalog=await response.json(); state.profiles=catalog.profiles;
  $('intensity-slider').max=String(state.profiles.length-1);
  const fragment=document.createDocumentFragment();
  state.profiles.forEach((p,index)=>{
    const button=document.createElement('button'); button.type='button'; button.textContent=p.label;
    button.onclick=()=>selectProfile(p.id); fragment.append(button);
  });
  $('intensity-stops').replaceChildren(fragment);
  let saved; try { saved=localStorage.getItem('chatlocal.agentProfile'); } catch {}
  selectProfile(state.profiles.some(p=>p.id===saved)?saved:catalog.default,false);
}
function setIntensityOpen(open,restoreFocus=false) {
  $('intensity-popover').hidden=!open;
  $('toggle-intensity').setAttribute('aria-expanded',String(open));
  if(open){closeMenu();$('intensity-slider').focus();}
  else if(restoreFocus)$('toggle-intensity').focus();
}
function setVisionMode(mode,rememberChoice=true) {
  state.visionMode=mode==='native'&&state.nativeVisionAvailable?'native':'ocr';
  const on=state.visionMode==='native';
  $('vision-toggle').setAttribute('aria-checked',String(on));
  $('vision-toggle').setAttribute('aria-label',`DeepSeek 原生识图：${on?'开启':'关闭，使用本地 OCR'}`);
  $('vision-label').textContent=on?'识图开':'识图关';
  $('composer-privacy').textContent=on?'识图开启：按需将指定图片和少量上下文发送给 DeepSeek，回答可展开原文核对。':'识图关闭：本地 OCR，不上传图片；相关文字片段会发送给 API。';
  if(rememberChoice){try{localStorage.setItem('chatlocal.visionMode',state.visionMode);}catch{}}
}
async function loadVisionOptions() {
  const response=await fetch('/api/vision-options');
  if(!response.ok)throw Error('无法加载识图设置，请刷新页面。');
  const options=await response.json();state.nativeVisionAvailable=options.native_available;
  $('vision-toggle').title=options.note;
  let saved;try{saved=localStorage.getItem('chatlocal.visionMode');}catch{}
  setVisionMode(saved||'ocr',false);
}
function setData(html, choices) {
  if (typeof html==='string') {
    $('data-status').innerHTML=html;
    const cards=[...$('data-status').querySelectorAll('.data-card')];
    const summaries=cards.map(c => { const text=c.textContent; const match=text.match(/([\d,]+) 条(?:文本|消息)/); return c.querySelector('strong')?.textContent+(match?` ${match[1]} 条`:' 未导入'); });
    $('data-summary').textContent=summaries.join(' · ');
    $('open-data').title=summaries.join(' · ')+'；点击查看数据与同步设置';
    const model=$('data-status').textContent.match(/模型：([^·]+)/);
    $('model-label').textContent=model?model[1].trim():'DeepSeek';
  }
  if (choices?.choices) state.conversations=choices.choices;
}

async function loadSession(id) {
  if (state.busy || state.loading) return;
  state.loading=true; syncControls();
  try {
    const r=await call('load_session',[id]);
    leaveWatch();if(typeof leaveWorkspace==='function')leaveWorkspace();
    state.sid=id; remember(); state.history=r[1]||[]; state.evidence=r[4]||''; state.progress=r[5]||'';
    state.filters={platforms:r[7]||['qq','wechat'],conversations:r[8]||[],start:r[9]||'',end:r[10]||'',keywords:r[11]||''};
    $('question').value=''; $('messages').replaceChildren(); updateRange(); renderMessages(true); renderHistory();
  } catch (error) { toast(error.message); }
  finally { state.loading=false; syncControls(); fitInput(); focusComposer(); }
}
async function newSession() {
  if (state.busy || state.loading) return;
  state.loading=true; syncControls();
  try {
    const r=await call('new_session',[]);leaveWatch();if(typeof leaveWorkspace==='function')leaveWorkspace();savePicker(r[1]);
    state.history=[]; state.evidence=''; state.progress=''; state.filters=defaults();
    $('question').value=''; $('messages').replaceChildren(); updateRange(); renderMessages(true);
  } catch (error) { toast(error.message); }
  finally { state.loading=false; syncControls(); fitInput(); focusComposer(); }
}
async function deleteSession(id,title) {
  if (!state.ready || state.busy || state.loading || state.watchBusy) return;
  if (!confirm(`删除对话“${title}”？\n\n会删除这段对话的全部问答和查询过程，无法撤销。已导入的 QQ／微信原始记录与关注卡保留。`)) return;
  state.loading=true;syncControls();
  try {
    const r=await call('delete_session',[id,null]);
    if (r[0]?.cleared_current && !state.watchId) {
      state.history=[];state.evidence='';state.progress='';state.filters=defaults();
      $('question').value='';$('messages').replaceChildren();
      $('evidence-dialog').close();$('all-evidence').replaceChildren();
      updateRange();renderMessages(true);
    }
    savePicker(r[2]);toast('对话已删除');
  } catch(error) {toast(error.message);}
  finally {state.loading=false;syncControls();fitInput();}
}
async function sendStop() {
  if (!state.busy || !state.accepted || state.stopSent) return;
  state.stopSent=true;
  try {
    const result=await fetch(`${API}/gradio_api/run/stop_agent`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({data:[null],fn_index:state.functions.stop_agent,session_hash:state.hash})});
    if (!result.ok) throw new Error();
  } catch { state.stopSent=false; state.stopRequested=false; syncControls(); toast('停止请求未送达，请再试一次。'); }
}
async function sendQuestion(event) {
    event.preventDefault();
  setIntensityOpen(false);
  if(state.workspaceId) {await sendWorkspaceTask();return;}
  if(state.watchId) {await sendWatchQuestion();return;}
  const question=$('question').value.trim();
  if (!question || !state.ready || state.busy || state.loading || chatBlockedByJob()) return;
  state.busy=true; state.accepted=false; state.stopRequested=false; state.stopSent=false; state.progress=''; closeMenu(); syncControls();
  const f=state.filters;
  try {
    await call('agent_action_controls',[question,f.platforms,f.conversations,f.start,f.end,f.keywords,null,state.profile,state.visionMode],(r,done) => {
      const firstUpdate=!state.accepted;
      if (r[6]?.choices) savePicker(r[6]);
      if (typeof r[1]==='string') state.progress=r[1];
      if (typeof r[4]==='string') state.evidence=r[4];
      if (Array.isArray(r[0])) state.history=r[0];
      if (r[7]?.value==='' && !state.accepted) { state.accepted=true; if($('question').value.trim()===question)$('question').value=''; }
      if (done) state.busy=false;
      if (state.stopRequested && !state.stopSent) void sendStop();
      renderMessages(firstUpdate && state.accepted); fitInput();
      if (done && !state.accepted && typeof r[1]==='string') toast(r[1]);
    });
  } catch (error) {
    state.stopRequested=true; await sendStop(); toast(error.message);
    if (!state.accepted && !$('question').value) $('question').value=question;
  } finally { state.busy=false; syncControls(); renderMessages(); focusComposer(); }
}

function renderConversationChoices() {
  const query=$('conversation-search').value.trim().toLocaleLowerCase();
  const platforms=[...document.querySelectorAll('input[name=platform]:checked')].map(c=>c.value);
  const fragment=document.createDocumentFragment();
  for (const [label,value] of state.conversations) {
    const platform=JSON.parse(value)[0];
    if (!platforms.includes(platform) || (query && !label.toLocaleLowerCase().includes(query))) continue;
    const row=document.createElement('label'); row.className='conversation-option';
    const check=document.createElement('input'); check.type='checkbox'; check.value=value; check.checked=state.draftConversations.has(value);
    check.onchange=() => { if (check.checked) state.draftConversations.add(value); else state.draftConversations.delete(value); };
    const text=document.createElement('span'); text.textContent=label.replace(/^wechat · /,'微信 · ').replace(/^qq · /,'QQ · ');
    row.append(check,text); fragment.append(row);
  }
  if (!fragment.childNodes.length) { const p=document.createElement('p'); p.className='empty-note'; p.textContent='没有找到会话，请检查平台或更新聊天数据。'; fragment.append(p); }
  $('conversation-options').replaceChildren(fragment);
}
function fillRange(filters) {
  document.querySelectorAll('input[name=platform]').forEach(c=>c.checked=filters.platforms.includes(c.value));
  state.draftConversations=new Set(filters.conversations);
  $('start-date').value=filters.start.slice(0,10); $('end-date').value=filters.end.slice(0,10); $('keywords').value=filters.keywords;
  $('conversation-search').value=''; renderConversationChoices();
}
function openRange() {
  if(state.workspaceId) {editWorkspace();return;}
  if(state.watchId) {editWatch();return;}
  if (state.busy || state.loading) return;
  closeMenu(); fillRange(state.filters); $('range-dialog').showModal();
}
async function openData() {
  if (state.busy || state.loading) return;
  closeMenu(); $('data-dialog').showModal();
  state.loading=true; syncControls();
  try { const r=await call('refresh',[]); setData(r[0],r[1]); await loadDataSettings(); }
  catch (error) { toast(error.message); }
  finally { state.loading=false; syncControls(); }
}
function readLimits() {
  return Object.fromEntries(Object.entries(READ_FIELDS).map(([key,id])=>[key,$(id).valueAsNumber]));
}
function showReadLimits() {
  const valid=Object.values(READ_FIELDS).every(id=>!$('read-enable-'+id.split('-')[1]).checked||$('read-all-'+id.split('-')[1])?.checked||$(id).value.trim()&&$(id).validity.valid);
  $('read-window-summary').hidden=valid;
  $('read-window-summary').textContent=valid?'':'每会话条数上限须为 1–10000 之间的整数。';
}
async function loadReadOptions() {
  const response=await fetch('/api/read-options');
  if (!response.ok) throw new Error('无法加载读取范围设置，请刷新页面。');
  const rules=await response.json();
  let saved={};try { saved=JSON.parse(localStorage.getItem('chatlocal.readLimits')||'{}')||{}; } catch {}
  for (const [key,id] of Object.entries(READ_FIELDS)) {
    const input=$(id),rule=rules[key],value=saved[key];
    input.min=rule.min;input.max=rule.max;
    input.value=Number.isInteger(value) && value>=rule.min && value<=rule.max ? value : rule.default;
  }
  showReadLimits();
}

$('composer').addEventListener('submit',sendQuestion);
$('reasoning-toggle').addEventListener('click',()=>setShowReasoning(!state.showReasoning));
$('toggle-intensity').onclick=()=>setIntensityOpen($('intensity-popover').hidden);
$('close-intensity').onclick=()=>setIntensityOpen(false,true);
$('vision-toggle').onclick=()=>{setIntensityOpen(false);setVisionMode(state.visionMode==='native'?'ocr':'native');};
document.addEventListener('click',event=>{if(!event.target.closest('#intensity-popover,#toggle-intensity'))setIntensityOpen(false);});
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!$('intensity-popover').hidden){event.preventDefault();setIntensityOpen(false,true);}});
$('intensity-slider').addEventListener('input',()=>selectProfile(state.profiles[Number($('intensity-slider').value)].id));
$('intensity-slider').addEventListener('keydown',event=>{ if (event.key==='Enter') event.preventDefault(); });
$('intensity-stops').addEventListener('keydown',event=>{ if (event.key==='Enter') event.stopPropagation(); });
document.querySelector('.intensity-info').addEventListener('toggle',fitInput);
$('question').addEventListener('input',fitInput);
$('question').addEventListener('keydown',event => {
  if (event.key==='Enter' && !event.shiftKey && !event.isComposing && event.keyCode!==229) { event.preventDefault(); $('composer').requestSubmit(); }
});
$('stop-button').onclick=() => { if(state.workspaceId){void stopWorkspaceTask();return;} if(state.watchId && activeWatchJob?.card_id===state.watchId) {void stopWatch();return;} state.stopRequested=true; syncControls(); renderMessages(); void sendStop(); };
$('new-chat').onclick=newSession;
$('open-sidebar').onclick=() => sidebar(true); $('close-sidebar').onclick=() => sidebar(false); $('sidebar-scrim').onclick=() => sidebar(false);
$('search-history').onclick=() => { $('history-search').hidden=!$('history-search').hidden; if (!$('history-search').hidden) $('history-search').focus(); else { $('history-search').value=''; renderHistory(); } };
$('history-search').addEventListener('input',renderHistory);
$('open-data').onclick=openData;
$('load-stickers').addEventListener('change',()=>{ try { localStorage.setItem('chatlocal.loadStickers.v2',String($('load-stickers').checked)); } catch {} });
Object.values(READ_FIELDS).forEach(id=>$(id).addEventListener('input',()=>{
  showReadLimits();
  if (Object.values(READ_FIELDS).every(field=>$(field).value.trim() && $(field).validity.valid)) {
    try { localStorage.setItem('chatlocal.readLimits',JSON.stringify(readLimits())); } catch {}
  }
}));
$('header-range').onclick=openRange; $('range-chip').onclick=openRange; $('menu-range').onclick=openRange; $('menu-data').onclick=openData;
$('more-options').onclick=() => { $('composer-menu').hidden=!$('composer-menu').hidden; $('more-options').setAttribute('aria-expanded',String(!$('composer-menu').hidden)); };
document.addEventListener('click',event => { if (!event.target.closest('.composer-options')) closeMenu(); });
document.addEventListener('keydown',event => { if (event.key==='Escape') closeMenu(); });
document.querySelectorAll('.close-dialog').forEach(b => b.onclick=() => b.closest('dialog').close());
document.querySelectorAll('dialog').forEach(dialog => dialog.addEventListener('click',event => {
  if (event.target===dialog) { const r=dialog.getBoundingClientRect(); if (event.clientX<r.left || event.clientX>r.right || event.clientY<r.top || event.clientY>r.bottom) dialog.close(); }
}));
document.querySelectorAll('[data-prompt]').forEach(b => b.onclick=() => { $('question').value=b.dataset.prompt; fitInput(); focusComposer(); });
$('conversation-search').addEventListener('input',renderConversationChoices);
document.querySelectorAll('input[name=platform]').forEach(c=>c.addEventListener('change',renderConversationChoices));
$('reset-range').onclick=() => fillRange(defaults());
$('range-form').addEventListener('submit',event => {
  event.preventDefault();
  const platforms=[...document.querySelectorAll('input[name=platform]:checked')].map(c=>c.value);
  if (!platforms.length) { toast('请至少选择 QQ 或微信。'); return; }
  const start=$('start-date').value, end=$('end-date').value;
  if (start && end && start>end) { toast('开始日期不能晚于结束日期。'); return; }
  // Preserve precise times from old sessions unless the date is changed.
  state.filters={platforms,conversations:[...state.draftConversations].filter(v=>platforms.includes(JSON.parse(v)[0])),
    start:start && start===state.filters.start.slice(0,10)?state.filters.start:start,
    end:end && end===state.filters.end.slice(0,10)?state.filters.end:end,keywords:$('keywords').value.trim()};
  updateRange(); $('range-dialog').close(); focusComposer();
});
$('import-form').addEventListener('submit',async event => {
  event.preventDefault(); if (state.busy || state.loading) return;
  state.loading=true; syncControls(); $('import-result').hidden=false; $('import-result').textContent='正在导入…';
  try { const r=await call('import_local_options',[$('import-path').value,'自动识别',null,null,$('load-stickers').checked]); $('import-result').textContent=r[0]; setData(r[1],r[2]); }
  catch (error) { $('import-result').textContent=error.message; }
  finally { state.loading=false; syncControls(); }
});
$('messages-scroll').addEventListener('scroll',() => $('jump-bottom').hidden=nearBottom());
$('jump-bottom').onclick=bottom;
mobile.addEventListener('change',() => sidebar(!mobile.matches));
window.addEventListener('resize',fitInput);
window.addEventListener('pagehide',() => {
  if (state.busy && state.accepted) void fetch(`${API}/gradio_api/run/stop_agent`, {method:'POST',keepalive:true,headers:{'Content-Type':'application/json'},
    body:JSON.stringify({data:[null],fn_index:state.functions.stop_agent,session_hash:state.hash})});
});

async function init() {
  let showReasoning=true;try{showReasoning=localStorage.getItem('tulpa.showReasoning')!=='false';}catch{}
  setShowReasoning(showReasoning,false);
  void loadReasoningPreference();
  try { $('load-stickers').checked=localStorage.getItem('chatlocal.loadStickers.v2')==='true'; } catch {}
  sidebar(!mobile.matches); syncControls();
  try {
    const response=await fetch(`${API}/config`); if (!response.ok) throw new Error('无法连接本机服务，请刷新页面。');
    const config=await response.json(); state.functions=Object.fromEntries(config.dependencies.map(d=>[d.api_name,d.id]));
    await Promise.all([loadProfiles(),loadReadOptions(),loadVisionOptions()]);
    const sessions=await call('refresh_sessions',[]); savePicker(sessions[0]);
    const data=await call('refresh',[]); setData(data[0],data[1]);
    state.ready=true; syncControls(); renderHistory();
    await loadWatches();
    let last,lastWatch,lastWorkspace; try { last=localStorage.getItem('chatlocal.lastConversation');lastWatch=localStorage.getItem('chatlocal.lastWatch');lastWorkspace=localStorage.getItem('chatlocal.workspace'); } catch {}
    if(lastWorkspace || new URLSearchParams(location.search).get('workspace')) {renderMessages();}
    else if(lastWatch && watchCards.some(c=>c.id===lastWatch)) await openWatch(lastWatch);
    else if (last && state.choices.some(c=>c[1]===last)) await loadSession(last);
    else { renderMessages(); if (!mobile.matches) focusComposer(); }
    const anchor=Number(new URLSearchParams(location.search).get('anchor'));
    if (Number.isSafeInteger(anchor) && anchor>0) await openChat(anchor);
  } catch (error) { toast(error.message); $('history-list').textContent='连接失败，请刷新页面重试。'; }
  finally { state.initialized=true;syncControls(); }
}


document.addEventListener('click',event=>{
  const watch=event.target.closest('.watch-evidence');
  if(watch) { void watchEvidence(Number(watch.dataset.messageId));return; }
  const source=event.target.closest('a.open-chat');
  if(source) { event.preventDefault();void openChat(Number(source.dataset.messageId));return; }
  const image=event.target.closest('a.open-image');
  if(image) { event.preventDefault();showImage(image.getAttribute('href')); }
});
