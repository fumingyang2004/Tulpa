'use strict';
(()=>{
  const nav=document.getElementById('open-tulpa'),toggle=document.getElementById('tulpa-switch');
  const dialog=document.createElement('dialog');dialog.id='tulpa-dialog';dialog.setAttribute('aria-label','Tulpa 社交记忆');
  dialog.innerHTML=`<div class="tulpa-shell"><header class="tulpa-head"><h2>慢慢长出来的社交记忆</h2><button class="icon-button" aria-label="关闭 Tulpa 记忆">×</button></header><p class="tulpa-intro"></p><div class="tulpa-layout"><aside class="tulpa-directory"><input type="search" placeholder="搜索群聊或联系人" aria-label="搜索记忆会话"><div class="tulpa-list"></div></aside><section class="tulpa-detail"><p class="tulpa-empty">选择一个群或联系人，看看真实互动留下了什么。</p></section></div></div>`;
  document.body.append(dialog);
  const list=dialog.querySelector('.tulpa-list'),detail=dialog.querySelector('.tulpa-detail'),intro=dialog.querySelector('.tulpa-intro'),search=dialog.querySelector('input');
  let state=null,selected=null,busy=false,serial=0,timer,lastDirectory='';
  const labels={collecting:'正在积累',updating:'正在整理这一阶段',update_failed:'整理失败，15 分钟后重试',no_self_participation:'上一阶段无本人参与，未生成画像',await_model:'等待配置模型'};
  const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  const progressText=s=>`下一次整理 ${Math.min(100,s.total-s.processed)}/100${s.updated_at?' · 最近更新 '+new Date(s.updated_at*1000).toLocaleString():''}`;
  async function api(path,method='GET',body){const r=await fetch('/api/tulpa'+path,{method,cache:'no-store',headers:body?{'Content-Type':'application/json','X-Tulpa-UI':'1'}:{},body:body?JSON.stringify(body):undefined});const b=await r.json();if(!r.ok)throw Error(b.detail||'暂时无法读取记忆');return b;}
  function displayControl(c){toggle.disabled=false;toggle.textContent=c.enabled?'ON':'OFF';toggle.setAttribute('aria-checked',String(!!c.enabled));intro.textContent=c.enabled?'已对所有可识别会话开启，包括新增会话。只从后续实时行为成长；历史导入不会生成画像。每 100 条有效文字，才将有限上下文交给你配置的模型整理。':'Tulpa 已暂停。已有记忆冻结，回复不使用成长记忆和成长案例；仍可从已导入聊天检索真实互动。聊天同步照常。再次开启仅从开启时刻继续。';}
  async function refresh(){state=await api('?q='+encodeURIComponent(search.value));displayControl(state.control);
    const current=state.scopes.find(s=>s.id===selected),status=detail.querySelector('.tulpa-running-status');
    if(current&&status){status.textContent=`${state.control.enabled?(labels[current.status]||current.status):'已冻结'} · 累积 ${current.total} 条有效文字 · 已整理 ${current.processed} 条`;const progress=detail.querySelector('progress');if(progress)progress.value=current.progress;const caption=detail.querySelector('.tulpa-progress-caption');if(caption)caption.textContent=progressText(current);}
    const signature=JSON.stringify([state.scopes,state.directory,selected]);if(signature===lastDirectory)return;lastDirectory=signature;list.replaceChildren();if(!state.scopes.length&&!state.directory.length)list.append(node('p','还没有匹配的行为记录。实时收到文字后会出现在这里。','tulpa-empty'));state.scopes.forEach(s=>{const b=node('button',undefined,'tulpa-card'+(selected===s.id?' selected':''));b.append(node('strong',s.name),node('small',`${s.platform==='qq'?'QQ':'微信'} · ${s.kind==='group'?'群聊':'私聊'} · ${s.progress}/100 · ${s.episodes} 个案例`));b.dataset.scopeId=String(s.id);b.onclick=()=>show(s);list.append(b);});
    if(state.directory.length){list.append(node('p','其他已导入会话 · 尚未成长','tulpa-status'));state.directory.forEach(s=>{const b=node('button',s.name,'tulpa-card');b.onclick=async()=>{try{const r=await api('/conversation','POST',{platform:s.platform,conversation_id:s.conversation_id});await refresh();await show({...s,id:r.id});}catch(e){detail.replaceChildren(node('p',e.message,'tulpa-error'));}};list.append(b);});}
  }
  function sourceButtons(ids){const wrap=node('div',undefined,'tulpa-sources');ids.forEach((id,i)=>{const b=node('button','原文 '+(i+1),'source-button');b.onclick=()=>{dialog.close();void openChat(id);};wrap.append(b);});return wrap;}
  async function show(s){const request=++serial;selected=s.id;detail.replaceChildren(node('p','正在读取…'));try{const d=await api('/scopes/'+s.id);if(request!==serial)return;displayControl(d.control);detail.replaceChildren(node('h3',s.name));detail.append(node('p',`${d.control.enabled?(labels[d.status]||d.status):'已冻结'} · 累积 ${d.total} 条有效文字 · 已整理 ${d.processed} 条`,'tulpa-status tulpa-running-status'));const p=node('progress');p.max=100;p.value=Math.min(100,d.total-d.processed);detail.append(p,node('p',progressText(d),'tulpa-status tulpa-progress-caption'));
    detail.append(node('h4','自动形成的记忆'),node('pre',d.automatic||'还在认识这个社交场景。达到门槛后才会整理，不会扫描全部旧历史。'));
    if(!d.valid)detail.append(node('p','支撑原文已变化或删除。这份旧推断已停用，重新整理后才会用于回复。','tulpa-error'));
    if(d.status==='update_failed'||d.status==='await_model'){const retry=node('button','重新尝试整理','quiet-button');retry.onclick=async()=>{await api('/scopes/'+s.id+'/retry','POST',{});await show(s);};detail.append(retry);}
    const evidence=node('details');evidence.append(node('summary','查看自动记忆的原文依据'),sourceButtons(d.sources));detail.append(evidence);
    detail.append(node('h4','你的修正与补充'),node('p','这里的内容优先于自动推断，后续自动整理不会覆盖。清空后恢复只参考自动记忆。','tulpa-status'));
    const input=node('textarea');input.value=d.manual;input.maxLength=6000;input.setAttribute('aria-label','人工记忆');detail.append(input);const save=node('button','保存人工记忆','primary-button'),message=node('p','','tulpa-status');save.onclick=async()=>{save.disabled=true;try{const v=await api('/scopes/'+s.id,'PATCH',{manual:input.value,revision:d.revision});d.revision=v.revision;message.textContent='已保存。自动整理不会覆盖你的修正。';}catch(e){message.textContent=e.message;}finally{save.disabled=false;}};detail.append(save,message);
    const episodes=node('details');episodes.append(node('summary','真实互动案例 · 最近 20 个'));if(!d.episodes.length)episodes.append(node('p','还没有可确认的回复案例。群聊需要明确引用；私聊按双方实际轮次记录。','tulpa-empty'));
    d.episodes.forEach(e=>{const item=node('div',undefined,'tulpa-episode');item.append(node('div',new Date(e.timestamp).toLocaleString()+(e.valid?'':' · 原文已变化，停用')),node('div',`对方 ${JSON.parse(e.incoming).length} 条 → 本人 ${JSON.parse(e.outgoing).length} 条`),sourceButtons([...JSON.parse(e.incoming),...JSON.parse(e.outgoing)]));episodes.append(item);});detail.append(episodes);
    const history=node('details');history.append(node('summary','记忆更新记录'));d.history.forEach(h=>{const item=node('div',undefined,'tulpa-episode');item.append(node('div',(h.kind==='manual'?'人工修正':'自动整理')+' · '+new Date(h.created_at*1000).toLocaleString()),node('pre',h.body));history.append(item);});detail.append(history);
    list.querySelectorAll('.tulpa-card').forEach(b=>b.classList.toggle('selected',b.dataset.scopeId===String(s.id)));
  }catch(e){if(request===serial)detail.replaceChildren(node('p',e.message,'tulpa-error'));}}
  dialog.querySelector('.tulpa-head button').onclick=()=>dialog.close();
  nav.onclick=async()=>{if(!dialog.open)dialog.showModal();try{await refresh();}catch(e){detail.replaceChildren(node('p',e.message,'tulpa-error'));}};
  toggle.onclick=async()=>{if(busy)return;busy=true;toggle.disabled=true;try{const c=await api('/control','POST',{enabled:toggle.getAttribute('aria-checked')!=='true'});displayControl(c);if(dialog.open)await refresh();}catch(e){toggle.title=e.message;}finally{busy=false;toggle.disabled=false;}};
  search.oninput=()=>{clearTimeout(timer);timer=setTimeout(()=>refresh().catch(()=>{}),250);};
  refresh().catch(()=>{toggle.title='记忆状态暂时不可用';});
  setInterval(()=>{if(dialog.open&&!document.hidden)refresh().catch(()=>{});},15000);
})();
