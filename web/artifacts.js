/* File library: metadata first, binary acquisition only after an explicit action. */
(() => {
  const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  const dialog=el('dialog',undefined,'artifact-dialog');
  const head=el('div',undefined,'artifact-heading'),title=el('h2','文件 / 资料'),close=el('button','关闭','quiet-button');
  close.onclick=()=>dialog.close();head.append(title,close);
  const status=el('p','','answer-note'),result=el('p','','artifact-result');result.setAttribute('role','status');
  const form=el('form',undefined,'artifact-filters');
  const field=(label,input)=>{const wrap=el('label',label);wrap.append(input);form.append(wrap);return input;};
  const input=(placeholder,type='text')=>{const n=el('input');n.type=type;n.placeholder=placeholder;return n;};
  const query=field('文件名 / 正文',input('搜索文件…'));
  const platform=el('select');[['','QQ + 微信'],['qq','QQ'],['wechat','微信']].forEach(([v,t])=>{const o=el('option',t);o.value=v;platform.append(o);});field('平台',platform);
  const conversation=field('会话',el('select'));conversation.append(new Option('全部会话',''));
  const extension=field('类型',input('pdf / docx / pptx / xlsx'));
  const sender=field('上传者 / 发送者',input('姓名或账号'));
  const start=field('开始日期',input('','date')),end=field('截止日期（含当天）',input('','date'));
  const body=el('input');body.type='checkbox';field('搜索已解析正文',body);
  const submit=el('button','搜索','quiet-button');submit.type='submit';form.append(submit);
  const actions=el('div',undefined,'artifact-actions');
  const discover=el('button','发现本地历史文件消息','quiet-button'),inventory=el('button','同步所选QQ群目录','quiet-button');actions.append(discover,inventory);
  const list=el('div',undefined,'artifact-list'),more=el('button','更多','quiet-button');more.hidden=true;
  const detail=el('section',undefined,'artifact-detail');
  dialog.append(head,status,form,actions,result,list,more,detail);document.body.append(dialog);
  let offset=0,next=null,current=null,requestId=0;
  async function api(path,options){const r=await fetch(path,options);const d=await r.json();if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:'请求失败');return d;}
  const post=(path,data={})=>api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const size=n=>n===null||n===undefined?'大小未知':n<1048576?`${(n/1024).toFixed(1)} KiB`:`${(n/1048576).toFixed(1)} MiB`;
  function state(f){return [({METADATA_ONLY:'仅有文件记录',AVAILABLE_LOCAL:'本体在本机',CACHED:'已缓存',MISSING:'本机未找到文件'})[f.availability],
    ({PARSED:'已解析',PARTIAL:'部分解析',NO_TEXT:'无可提取文字'})[f.parse_status],f.cache_status==='ARCHIVED'?'长期保留':'',f.remote_status==='absent'?'最近目录未发现':''].filter(Boolean).join(' · ');}
  async function refreshStatus(){
    const s=await api('/api/artifacts/status');status.textContent=`QQ ${s.counts.qq||0} 个来源 · 微信 ${s.counts.wechat||0} 个来源 · 原文件缓存 ${size(s.cache_bytes)}。${s.note}`;
    const selected=conversation.value;conversation.replaceChildren(new Option('全部会话',''));
    for(const c of s.conversations){if(platform.value&&c.platform!==platform.value)continue;conversation.append(new Option(`${c.platform==='qq'?'QQ':'微信'} · ${c.conversation}`,JSON.stringify([c.platform,c.conversation_id])));}
    conversation.value=selected;
    inventory.disabled=!s.onebot_configured;
    inventory.title=s.onebot_configured?'只同步元数据，不下载正文':'需要配置本机OneBot接口';
    return s;
  }
  async function search(append=false){
    const token=++requestId;const p=new URLSearchParams({query:query.value,platform:platform.value,extension:extension.value.trim().replace(/^\./,''),sender:sender.value,start:start.value,end:end.value,body:body.checked,offset:append?next||0:0});
    if(conversation.value){const pair=JSON.parse(conversation.value);p.set('platform',pair[0]);p.set('conversation_id',pair[1]);}
    const d=await api('/api/artifacts?'+p);if(token!==requestId)return;
    if(!append)list.replaceChildren();next=d.next_offset;more.hidden=!d.has_more;
    result.textContent=`找到 ${d.match_count} 项。${d.note}`;
    for(const f of d.files){const row=el('article',undefined,'artifact-row');const button=el('button',f.filename,'source-button');button.onclick=()=>show(f.id);row.append(button,
      el('p',`${f.platform==='qq'?'QQ':'微信'} · ${f.conversation} · ${f.sender} · ${f.time}`,'answer-note'),
      el('p',`${size(f.size)} · ${state(f)} · ${f.folder||'聊天附件'}`,'answer-note'));
      if(f.text)row.append(el('p',f.text.slice(0,260)));list.append(row);}
  }
  async function job(path,data){
    result.textContent='正在处理，原文件和正文均保持本地…';
    await post(path,data);
    for(;;){await new Promise(r=>setTimeout(r,900));const s=await refreshStatus();if(s.running)continue;
      if(s.error)throw Error(s.error);result.textContent='完成：'+JSON.stringify(s.result);break;}
    const completion=result.textContent;
    if(current)await show(current);await search();result.textContent=completion;
  }
  async function show(id){
    current=id;const f=await api('/api/artifacts/'+id);detail.replaceChildren(el('h3',f.filename),
      el('p',`${f.platform==='qq'?'QQ':'微信'} · ${f.conversation} · ${f.sender} · ${f.time}`),
      el('p',`${size(f.size)} · ${state(f)}`));
    const buttons=el('div',undefined,'artifact-actions');
    const save=el('button','保存文件来源','collect-evidence source-button');save.dataset.sourceType='artifact_source';save.dataset.sourceId=id;buttons.append(save);
    for(const [action,label] of [['locate','检查本机本体'],['materialize','取得原文件'],['prepare','解析 / 建立索引'],['pin','永久保留'],['evict','清理原文件缓存']]){
      const b=el('button',label,'quiet-button');b.onclick=()=>guard(async()=>{
        let confirmed=false;
        if(action==='evict'&&!confirm('仅删除本项目的这份原文件缓存，保留解析文本与索引。继续？'))return;
        if(['prepare','materialize','pin'].includes(action)&&f.size>32*1048576){if(!confirm('此文件超过默认32 MiB。允许本次扩大至100 MiB？'))return;confirmed=true;}
        await job(`/api/artifacts/${id}/action`,{action,confirmed});
      });buttons.append(b);
    }
    if(f.sha256&&f.cache_status!=='EVICTED'){const a=el('a','打开 / 保存原文件','source-button');a.href=f.url;a.target='_blank';a.rel='noopener';buttons.append(a);}
    if(f.message_id){const a=el('a','查看聊天来源','open-chat source-button');a.href='/?anchor='+f.message_id;a.dataset.messageId=f.message_id;buttons.append(a);}
    detail.append(buttons);
    if(f.parse_note)detail.append(el('p',f.parse_note,'answer-note'));
    if(f.sha256)detail.append(el('p','SHA256：'+f.sha256,'artifact-hash'));
    let chunkOffset=0;
    const chunkList=el('div'),nextChunks=el('button','阅读原文片段','quiet-button');
    nextChunks.onclick=()=>guard(async()=>{const d=await api(`/api/artifacts/${id}/chunks?offset=${chunkOffset}`);
      for(const c of d.files){const block=el('details');const save=el('button','保存这个原文片段','collect-evidence source-button');save.dataset.sourceType='artifact_chunk';save.dataset.sourceId=`${id}:${c.chunk_id}`;block.append(el('summary',`${c.citation_id} · ${JSON.stringify(c.locator)}`),el('pre',c.text),save);chunkList.append(block);}
      chunkOffset=d.next_offset;nextChunks.hidden=!d.has_more;if(!d.files.length)chunkList.append(el('p',d.note));});
    detail.append(chunkList,nextChunks);
    const jump=new URLSearchParams(location.search);const chunkId=jump.get('chunk');
    if(jump.get('file')===String(id)&&chunkId&&/^[1-9]\d*$/.test(chunkId)){
      const d=await api(`/api/artifacts/${id}/chunk/${chunkId}`);
      for(const c of d.files){const block=el('details');block.open=true;block.append(el('summary',`已定位 ${c.citation_id} · ${JSON.stringify(c.locator)}`),el('pre',c.text));chunkList.prepend(block);}
    }
    detail.scrollIntoView({block:'nearest'});
  }
  async function guard(fn){try{await fn();}catch(e){result.textContent=e.message;}}
  async function open(id){if(!dialog.open)dialog.showModal();await refreshStatus();await search();if(id)await show(id);}
  document.getElementById('open-artifacts').onclick=()=>guard(()=>open());
  document.addEventListener('click',e=>{const b=e.target.closest('.open-artifact');if(b)guard(()=>open(Number(b.dataset.sourceId)));});
  form.onsubmit=e=>{e.preventDefault();guard(()=>search());};more.onclick=()=>guard(()=>search(true));platform.onchange=()=>guard(refreshStatus);
  discover.onclick=()=>guard(()=>job('/api/artifacts-discover'));
  inventory.onclick=()=>guard(async()=>{if(!conversation.value)throw Error('请先选择一个QQ群。');const [p,cid]=JSON.parse(conversation.value);if(p!=='qq')throw Error('微信文件按聊天附件发现，无群文件目录。');await job('/api/artifacts-inventory',{conversation_id:cid});});
  const requested=new URLSearchParams(location.search).get('file');
  if(requested&&/^[1-9]\d*$/.test(requested))guard(()=>open(Number(requested)));
})();
