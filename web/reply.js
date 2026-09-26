'use strict';
let replySession=null,replyBusy=false,replyDirty=false,replyTimer=null,replyOpening=0;
let replyStage='instruction',replyInstruction='',replyError='',replySave=null;
async function replyAPI(path,body,method='POST'){
  const response=await fetch('/api/replies'+path,{method,cache:'no-store',headers:{'Content-Type':'application/json','X-Reply-UI':'1'},...(body===undefined?{}:{body:JSON.stringify(body)})});
  const data=await response.json();if(!response.ok)throw Error(data.detail||'回复操作失败。');return data;
}
function showReplyMenu(event,mid){
  document.querySelector('.reply-context-menu')?.remove();
  const menu=document.createElement('div');menu.className='reply-context-menu';menu.setAttribute('role','menu');
  const button=document.createElement('button');button.textContent='帮我回复';button.setAttribute('role','menuitem');button.onclick=()=>{menu.remove();void openReply(mid);};menu.append(button);
  const rect=event.currentTarget.getBoundingClientRect();
  menu.style.left=Math.min(event.clientX||rect.left+20,window.innerWidth-150)+'px';menu.style.top=Math.min(event.clientY||rect.top+20,window.innerHeight-60)+'px';
  $('chat-dialog').append(menu);button.focus();
  menu.addEventListener('keydown',e=>{if(e.key==='Escape')menu.remove();});
  menu.addEventListener('focusout',()=>setTimeout(()=>menu.remove(),150));
}
function renderReply(data){
  const old=replySession;replySession=data;
  const t=data.target,busy=!!data.active_run,locked=data.status!=='DRAFT',draft=replyStage==='draft';
  const generationFailed=data.status==='DRAFT'&&data.error&&!busy&&['error','cancelled','interrupted'].includes(data.generations[0]?.status);
  $('reply-target').textContent=`${t.platform==='qq'?'QQ':'微信'} · ${t.conversation} · 回复 ${t.sender}`;
  $('reply-source').textContent=t.content||'[媒体消息，请结合识图或转写]';
  if(!draft){if($('reply-text').value!==replyInstruction)$('reply-text').value=replyInstruction;}
  else if(!replyDirty||old?.id!==data.id){if($('reply-text').value!==data.final_text)$('reply-text').value=data.final_text;replyDirty=false;}
  $('reply-editor-label').textContent=draft?(generationFailed?'上一次回复草稿 · 本次生成未替换':'回复草稿 · 可直接编辑'):'回复要求（可留空）';
  $('reply-text').maxLength=draft?2000:1000;
  $('reply-text').placeholder=draft?'在这里编辑准备发送的回复':'例如：帮我婉拒、调侃一下、不要太正式';
  $('reply-text').disabled=busy||locked||replyBusy;
  $('reply-profile').disabled=busy||locked||replyBusy;$('reply-vision').disabled=busy||locked||replyBusy;
  $('reply-generate').disabled=busy||locked||replyBusy;
  $('reply-generate').textContent=draft?'重新生成':'生成回复';$('reply-generate').hidden=locked||busy;
  $('reply-stop').hidden=!busy;$('reply-stop').disabled=replyBusy;
  $('reply-adjust').hidden=!draft||locked||busy;$('reply-adjust').disabled=replyBusy;
  $('reply-back').hidden=draft||!data.ai_text||locked||busy;$('reply-back').disabled=replyBusy;
  $('reply-copy').hidden=!draft;
  $('reply-copy').disabled=!$('reply-text').value;
  $('reply-approve').disabled=!draft||busy||locked||replyBusy||!data.ai_text||!$('reply-text').value.trim()||!data.sender.available;
  $('reply-reject').disabled=locked||replyBusy;
  $('reply-quote').disabled=!draft||busy||locked||replyBusy||!data.sender.available;
  $('reply-new').hidden=!['SENT','REJECTED'].includes(data.status);$('reply-new').disabled=replyBusy;
  $('reply-new').textContent=data.status==='REJECTED'?'重新起草':'再写一条';
  const savedError=data.error+(generationFailed&&data.ai_text?' 当前显示的是上一次草稿，本次未替换。':'');
  $('reply-status').textContent=replyError||savedError||(busy?'正在读取上下文并生成回复…':({DRAFT:draft?(replyDirty?'已编辑，尚未发送。':'草稿已保存，尚未发送。'):'填写要求并选择强度，或直接生成。',USER_APPROVED:'已批准，准备发送…',SENDING:'正在发送，请勿重复操作。',SENT:'QQ 已返回成功回执；桌面 QQ 可能不显示，可在手机 QQ 核对。',REJECTED:'已拒绝，未发送。草稿和生成记录已保留。',UNKNOWN:'发送结果未知，请到 QQ 核对，本条不会重发。'}[data.status]||data.status));
  const address=replyAddress(t);
  $('reply-address').textContent=address?`QQ ${address.account} → ${address.kind==='group'?'群':'好友'}「${t.conversation}」（${address.peer}）`:'';
  $('reply-sender').textContent=locked?'':data.sender.available?'批准将发送上方编辑框中的文字；拒绝不会发送。':data.sender.note;
  $('reply-notes').textContent=(data.result.notes||[]).join('；');$('reply-basis').textContent=data.result.context_note||(data.ai_text?'旧版草稿保留的来源记录。':'生成后可查看本轮读取的上下文。');
  if(data.result.tulpa){const t=data.result.tulpa;if(t.history_examples!==undefined)$('reply-basis').textContent+=` 从已导入聊天读取 ${t.history_examples} 个真实互动案例。`;$('reply-basis').textContent+=' '+(t.enabled?`Tulpa：参考 ${t.episodes||0} 个成长案例${t.memory_used?'、自动记忆':''}${t.manual_used?'、人工修正':''}。`:'Tulpa 已关闭，本轮未使用成长记忆或成长案例；历史聊天检索仍可用。');}
  $('reply-references').hidden=!data.generations.length&&!data.ai_text;
  const evidence=$('reply-evidence');evidence.replaceChildren();
  const contextIds=data.result.context_ids||[...(data.result.evidence_ids||[]),...(data.result.style_ids||[])];
  const ownIds=data.result.self_message_ids||data.result.style_ids||[];
  [...new Set(contextIds)].forEach(mid=>{const b=document.createElement('button');b.className='source-button';b.textContent=ownIds.includes(mid)?'本人发言 · M'+mid:'上下文 · M'+mid;b.onclick=()=>replyAction(async()=>{await saveReplyEdit();await openChat(mid);});evidence.append(b);});
  const history=$('reply-history');history.replaceChildren();
  for(const g of data.generations){const d=document.createElement('details'),s=document.createElement('summary'),p=document.createElement('pre');
    s.textContent=`${g.status==='running'?'生成中':g.status==='completed'?'已生成':g.status} · ${g.seconds||0} 秒 · ${g.requests||0} 次请求 · ${g.usage.total_tokens||0} tokens`;
    p.textContent=[g.result.error||'',g.result.reply_text||'',...g.events.map(e=>e.text||e.name||e.type)].filter(Boolean).join('\n');d.append(s,p);history.append(d);}
}
function replyAddress(target){
  const parts=/^(\d+):(group|direct):(\d+)$/.exec(target.conversation_id||'');
  return target.platform==='qq'&&parts?{account:parts[1],kind:parts[2],peer:parts[3]}:null;
}
function replyInConversation(target,page){return !page||(target.platform===page.platform&&target.conversation_id===page.conversation_id);}
function syncReplyConversation(page){
  if(replySession&&!replyInConversation(replySession.target,page)&&!$('reply-panel').hidden)void closeReplyPanel();
}
async function closeReplyPanel(){
  ++replyOpening;$('reply-panel').hidden=true;clearTimeout(replyTimer);
  try{await saveReplyEdit();}catch(e){toast('编辑暂未保存：'+e.message);}
  if(typeof chatPage!=='undefined')void loadReplyReceipts();
}
function acceptReplyResult(data){
  // Only a successful generation replaces the instruction in the shared editor.
  if(!data.active_run&&!data.error&&data.ai_text){replyStage='draft';replyDirty=false;}
  renderReply(data);
}
async function openReply(mid){
  if(replyBusy){toast('请等待当前回复操作完成。');return;}
  const token=++replyOpening;clearTimeout(replyTimer);
  // Never silently discard an unsaved edit when switching selected messages.
  try{await saveReplyEdit();const data=await replyAPI('',{message_id:mid});if(token!==replyOpening)return;
    if(!$('chat-dialog').open||!replyInConversation(data.target,typeof chatPage==='undefined'?null:chatPage))return;
    const same=replySession?.id===data.id;
    replyDirty=false;replyError='';replyStage=data.ai_text?'draft':'instruction';
    if(!same)replyInstruction=data.instruction;
    $('reply-quote').checked=false;$('reply-native-option').disabled=!state.nativeVisionAvailable;
    if(!same){$('reply-vision').value=state.visionMode;$('reply-profile').value=state.profile||'standard';$('reply-references').open=false;}
    renderReply(data);$('reply-panel').hidden=false;$('reply-text').focus();pollReply();
  }catch(e){toast(e.message);}
}
async function pollReply(){
  clearTimeout(replyTimer);if($('reply-panel').hidden||!replySession)return;
  const id=replySession.id,token=replyOpening;
  if(replySession.active_run||['SENDING','USER_APPROVED'].includes(replySession.status)){
    replyTimer=setTimeout(async()=>{try{const data=await replyAPI('/'+id,undefined,'GET');if(replySession?.id===id&&token===replyOpening&&!replyBusy)acceptReplyResult(data);}catch(e){if(token===replyOpening)$('reply-status').textContent=e.message;}pollReply();},1200);
  }
}
async function saveReplyEdit(){
  if(replySave)await replySave;
  if(!replySession||!replyDirty)return;
  const id=replySession.id,text=$('reply-text').value;
  replySave=replyAPI('/'+id,{text,revision:replySession.revision},'PATCH');
  try{const data=await replySave;if(replySession?.id===id){replyDirty=$('reply-text').value!==text;renderReply(data);}}
  finally{replySave=null;}
}
async function replyAction(fn){
  if(replyBusy||!replySession)return;replyBusy=true;replyError='';renderReply(replySession);
  try{await fn();}catch(e){replyError=e.message;toast(e.message);}finally{replyBusy=false;renderReply(replySession);pollReply();}
}
$('reply-text').addEventListener('input',()=>{if(replyStage==='instruction')replyInstruction=$('reply-text').value;else replyDirty=true;replyError='';renderReply(replySession);});
$('reply-close').onclick=closeReplyPanel;
$('reply-new').onclick=()=>replyAction(async()=>{
  if(!['SENT','REJECTED'].includes(replySession.status))return;
  const data=await replyAPI('',{message_id:replySession.message_id,fresh:true});
  replyDirty=false;replyInstruction='';replyStage='instruction';renderReply(data);$('reply-text').focus();
});
$('reply-adjust').onclick=()=>replyAction(async()=>{await saveReplyEdit();replyStage='instruction';renderReply(replySession);$('reply-text').focus();});
$('reply-back').onclick=()=>{if(replyBusy)return;replyStage='draft';replyDirty=false;renderReply(replySession);};
$('reply-copy').onclick=async()=>{try{await navigator.clipboard.writeText($('reply-text').value);toast('已复制回复。');}catch{toast('复制失败，可选中文字手动复制。');}};
$('reply-generate').onclick=()=>replyAction(async()=>{
  if(replySession.status!=='DRAFT'||replySession.active_run)return;
  await saveReplyEdit();replyDirty=false;
  acceptReplyResult(await replyAPI('/'+replySession.id+'/generate',{instruction:replyInstruction,profile:$('reply-profile').value,vision:$('reply-vision').value}));
});
$('reply-stop').onclick=()=>replyAction(async()=>{await replyAPI('/'+replySession.id+'/stop',{});});
$('reply-reject').onclick=()=>replyAction(async()=>{
  if(replySession.status!=='DRAFT')return;
  await saveReplyEdit();renderReply(await replyAPI('/'+replySession.id+'/reject',{revision:replySession.revision}));
});
$('reply-approve').onclick=()=>replyAction(async()=>{
  if($('reply-panel').hidden||replyStage!=='draft'||replySession.status!=='DRAFT'||replySession.active_run||!replySession.ai_text||!replySession.sender.available||!$('reply-text').value.trim())return;
  // This click approves the visible text and destination. Preflight cannot
  // substitute a different recipient, text, revision or quote choice.
  const text=$('reply-text').value,address=replyAddress(replySession.target),quote=$('reply-quote').checked,token=replyOpening;
  if(!address)throw Error('所选记录没有可靠的 QQ 发送目标，未发送。');
  await saveReplyEdit();const sid=replySession.id,revision=replySession.revision;
  $('reply-status').textContent='正在核对账号和会话…';
  const p=await replyAPI('/'+sid+'/preview',{revision,quote});
  if(token!==replyOpening||$('reply-panel').hidden)return;
  if(p.text!==text||p.revision!==revision||p.quote!==quote||!p.destination||['account','kind','peer'].some(k=>String(p.destination[k])!==address[k]))throw Error('发送核对结果与当前审核内容不一致，未发送。请重新打开草稿。');
  $('reply-status').textContent='已批准，正在发送…';
  try{renderReply(await replyAPI('/'+sid+'/send',{token:p.token,confirm:true}));if(typeof chatPage!=='undefined'){receiptLoadedAt=0;void loadReplyReceipts();}}
  catch(e){renderReply(await replyAPI('/'+sid,undefined,'GET'));throw e;}
});
$('chat-dialog').addEventListener('close',()=>{document.querySelector('.reply-context-menu')?.remove();void closeReplyPanel();});
let receiptKey='',receiptLoadedAt=0;
async function loadReplyReceipts(){
  if(!chatPage||!$('chat-dialog').open)return;
  const key=JSON.stringify([chatPage.platform,chatPage.conversation_id]);
  if(receiptKey===key&&Date.now()-receiptLoadedAt<2000)return;
  receiptKey=key;receiptLoadedAt=Date.now();$('chat-reply-receipts').hidden=true;
  try{
    const r=await fetch('/api/reply-receipts?'+new URLSearchParams({platform:chatPage.platform,conversation_id:chatPage.conversation_id}),{cache:'no-store'});
    if(!r.ok)return;const d=await r.json();if(key!==receiptKey)return;
    const host=$('chat-reply-receipt-list');host.replaceChildren();
    for(const item of d.receipts){const p=document.createElement('p');p.textContent=new Date(item.timestamp*1000).toLocaleString()+' · 已发送\n'+item.content;host.append(p);}
    $('chat-reply-receipts').hidden=!d.receipts.length;
  }catch{receiptLoadedAt=0;}
}
