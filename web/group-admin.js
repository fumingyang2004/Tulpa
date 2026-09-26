'use strict';
// Approval lives beside the composer, never inside model-produced HTML.
const groupAdminState={sid:null,loading:false,signature:'',operations:[],available:false,changing:false,busy:new Set()};
const groupAdminLabels={PENDING:'等待审批',EXECUTING:'正在执行',SUCCEEDED:'已执行',REJECTED:'已拒绝',FAILED:'未执行成功',UNKNOWN:'结果未知，请在 QQ 核对',EXPIRED:'审批已过期',CANCELLED:'已取消'};
async function groupAdminApi(path='',body,method='POST') {
  const response=await fetch('/api/group-admin'+path,body===undefined?{cache:'no-store'}:{method,
    headers:{'Content-Type':'application/json','X-Group-Admin-UI':'1'},body:JSON.stringify(body)});
  const value=await response.json();
  if(!response.ok)throw Error(typeof value.detail==='string'?value.detail:'群管理请求失败，请重试。');
  return value;
}
function groupAdminOrdinaryChat(){return !state.workspaceId&&!state.watchId;}
function groupAdminPolicyNote(mode){
  $('group-admin-policy-note').textContent=mode==='allow'?'本对话中，之后明确要求的群管理操作将自动允许并保留记录。已有待审批操作仍需逐项选择。':mode==='deny'?'本对话中，之后提出的群管理操作将默认拒绝，不执行。':'群管理操作执行前会在这里显示具体内容，等你允许或拒绝。';
  $('group-admin-policy-note').hidden=!groupAdminState.available||!groupAdminOrdinaryChat();
}
function renderGroupAdmin(){
  const panel=$('group-admin-panel'),normal=groupAdminOrdinaryChat();
  $('group-admin-policy-control').hidden=!normal||!groupAdminState.available;
  $('group-admin-policy').disabled=groupAdminState.changing||state.loading;
  if(!normal){panel.hidden=true;$('group-admin-policy-note').hidden=true;return;}
  const pending=groupAdminState.operations.filter(o=>['PENDING','EXECUTING'].includes(o.status));
  const recent=groupAdminState.operations.filter(o=>!['PENDING','EXECUTING'].includes(o.status)).slice(0,8);
  const signature=JSON.stringify([state.sid,groupAdminState.operations,[...groupAdminState.busy]]);
  if(signature===groupAdminState.signature){panel.hidden=!pending.length&&!recent.length;return;}
  groupAdminState.signature=signature;
  const fragment=document.createDocumentFragment();
  for(const item of pending){
    const card=document.createElement('article');card.className='group-admin-card';
    const header=document.createElement('div');header.className='group-admin-head';
    const title=document.createElement('strong');title.textContent='当前操作';
    const status=document.createElement('span');status.textContent=groupAdminLabels[item.status];header.append(title,status);
    const summary=document.createElement('p');summary.className='group-admin-summary';summary.textContent=item.summary;
    const account=document.createElement('small');account.textContent='使用 QQ '+item.target.account+' · '+(item.target.actor_role==='owner'?'群主':'管理员');
    card.append(header,summary,account);
    if(item.status==='PENDING'){
      const actions=document.createElement('div');actions.className='group-admin-actions';
      for(const [allow,text] of [[true,'允许'],[false,'拒绝']]){
        const button=document.createElement('button');button.type='button';button.textContent=text;
        button.className=allow?'primary-button':'quiet-button';button.disabled=groupAdminState.busy.has(item.id);
        button.onclick=()=>void decideGroupAdmin(item,allow);actions.append(button);
      }
      card.append(actions);
    }
    fragment.append(card);
  }
  if(recent.length){
    const history=document.createElement('details');history.className='group-admin-history';
    const title=document.createElement('summary');title.textContent='最近操作 · '+groupAdminLabels[recent[0].status];history.append(title);
    for(const item of recent){
      const row=document.createElement('div');row.className='group-admin-history-row';
      const summary=document.createElement('p');summary.textContent=item.summary;
      const status=document.createElement('small');status.textContent=groupAdminLabels[item.status]+(item.decision==='default_allow'?' · 默认允许':item.decision==='default_deny'?' · 默认拒绝':'')+(item.detail?' · '+item.detail:'');
      row.append(summary,status);history.append(row);
    }
    fragment.append(history);
  }
  panel.replaceChildren(fragment);panel.hidden=!pending.length&&!recent.length;
  if(typeof fitInput==='function')fitInput();
}
async function decideGroupAdmin(item,allow){
  if(groupAdminState.busy.has(item.id)||state.sid!==item.session_id)return;
  const sid=item.session_id;groupAdminState.busy.add(item.id);renderGroupAdmin();
  try{
    const result=await groupAdminApi('/'+encodeURIComponent(item.id)+'/decision',{session_id:sid,allow,token:item.approval_token});
    if(state.sid===sid){
      groupAdminState.operations=groupAdminState.operations.map(o=>o.id===item.id?result:o);
      toast(groupAdminLabels[result.status]||'操作已更新');
    }
  }catch(error){toast(error.message);}
  finally{groupAdminState.busy.delete(item.id);renderGroupAdmin();void refreshGroupAdmin();}
}
async function refreshGroupAdmin(){
  if(!state.ready||groupAdminState.loading)return;
  if(!groupAdminOrdinaryChat()){renderGroupAdmin();return;}
  const sid=state.sid||'';
  if(groupAdminState.sid!==sid){groupAdminState.sid=sid;groupAdminState.operations=[];groupAdminState.signature='';renderGroupAdmin();}
  groupAdminState.loading=true;
  try{
    const value=await groupAdminApi('?session_id='+encodeURIComponent(sid));
    if((state.sid||'')!==sid||!groupAdminOrdinaryChat())return;
    groupAdminState.available=value.available;groupAdminState.operations=value.operations;
    if(!groupAdminState.changing)$('group-admin-policy').value=value.policy.mode;
    groupAdminPolicyNote($('group-admin-policy').value);renderGroupAdmin();
  }catch{}finally{groupAdminState.loading=false;}
}
$('group-admin-policy').addEventListener('change',async()=>{
  const mode=$('group-admin-policy').value;
  groupAdminState.changing=true;renderGroupAdmin();
  try{
    if(!state.sid){
      const draft=$('question').value;
      await newSession();
      if(!$('question').value)$('question').value=draft;
      fitInput();
    }
    const sid=state.sid;if(!sid)throw Error('请先新建对话。');
    await groupAdminApi('/policy',{session_id:sid,mode},'PUT');
    if(state.sid===sid){$('group-admin-policy').value=mode;groupAdminPolicyNote(mode);}
  }catch(error){toast(error.message);}
  finally{groupAdminState.changing=false;void refreshGroupAdmin();}
});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)void refreshGroupAdmin();});
setInterval(()=>{if(!document.hidden)void refreshGroupAdmin();},1500);
void refreshGroupAdmin();
