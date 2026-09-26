/* Reference sets only. Existing source viewers retain ownership of the content. */
(() => {
  const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  const dialog=el('dialog',undefined,'artifact-dialog'),head=el('div',undefined,'artifact-heading');
  const title=el('h2','证据集合'),close=el('button','关闭','quiet-button');close.onclick=()=>dialog.close();head.append(title,close);
  const status=el('p','','answer-note');status.setAttribute('role','status');
  const actions=el('div',undefined,'artifact-actions'),sets=el('button','我的集合','quiet-button'),create=el('button','新建集合','quiet-button'),qq=el('button','QQ 公告 / 精华','quiet-button');actions.append(sets,create,qq);
  const list=el('div',undefined,'artifact-list'),detail=el('section',undefined,'artifact-detail');
  dialog.append(head,el('p','只保存原始资料的引用。删除集合或移除项目不会删除聊天和文件。','answer-note'),actions,status,list,detail);document.body.append(dialog);
  let current=null,mode='sets',generation=0;
  const names={message:'聊天消息',media:'图片 / 表情',voice:'原生语音',artifact_source:'文件来源',artifact_chunk:'文件片段',qq_notice:'QQ 公告',qq_essence:'QQ 精华'};
  async function api(path,method='GET',data){const r=await fetch(path,{method,headers:data?{'Content-Type':'application/json'}:{},body:data?JSON.stringify(data):undefined});const d=await r.json();if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:'操作失败');return d;}
  async function guard(fn){try{await fn();}catch(e){status.textContent=e.message;}}
  function open(){if(!dialog.open)dialog.showModal();}
  const button=(text,fn)=>{const b=el('button',text,'source-button');b.onclick=()=>guard(fn);return b;};
  async function listing(append=false,offset=0){
    mode='sets';const token=++generation;const d=await api('/api/collections?offset='+offset);if(token!==generation)return;
    if(!append){list.replaceChildren();detail.replaceChildren();current=null;}status.textContent=`${d.total} 个集合`;
    for(const c of d.collections){const row=el('article',undefined,'artifact-row');row.append(button(c.title,()=>show(c.id)),el('p',`${c.item_count} 项 · ${new Date(c.updated_at*1000).toLocaleString()}`,'answer-note'));list.append(row);}
    if(d.next_offset!==null){const b=button('更多集合',async()=>{b.remove();await listing(true,d.next_offset);});list.append(b);}
  }
  async function show(cid,append=false,offset=0){
    mode='sets';current=cid;const token=++generation;const c=await api(`/api/collections/${encodeURIComponent(cid)}?offset=${offset}`);if(token!==generation)return;
    if(!append){detail.replaceChildren(el('h3',c.title));const bar=el('div',undefined,'artifact-actions');bar.append(
      button('重命名',async()=>{const name=prompt('集合名称',c.title);if(name!==null){await api('/api/collections/'+cid,'PATCH',{title:name});await listing();await show(cid);}}),
      button('删除集合',async()=>{if(confirm('删除这个集合及其引用？原始资料会保留。')){await api('/api/collections/'+cid,'DELETE');await listing();}}));detail.append(bar);}
    status.textContent=`${c.title} · ${c.item_count} 项`;
    for(const item of c.items){
      const row=el('article',undefined,'artifact-row');row.append(el('strong',names[item.source_type]||item.source_type));
      if(item.status==='available'){
        const s=item.source;row.append(el('p',s.title||s.filename||'原始来源'),el('p',[s.platform==='qq'?'QQ':'微信',s.conversation,s.sender||s.publisher].filter(Boolean).join(' · '),'answer-note'));
        if(s.excerpt)row.append(el('pre',s.excerpt));
        if(s.locator)row.append(el('p',JSON.stringify(s.locator),'answer-note'));
        if(s.media?.available){const img=el('img');img.src=s.media.url;img.alt='证据图片';img.loading='lazy';img.style.maxWidth='220px';img.style.maxHeight='180px';row.append(img);}
        const link=el('a','打开原始来源','source-button');link.href=s.url;link.target='_blank';link.rel='noopener';row.append(link);
      }else row.append(el('p',item.reason||'原始来源不可用','answer-note'));
      row.append(button('移除引用',async()=>{await api(`/api/collections/${cid}/items/${item.id}`,'DELETE');await show(cid);}));detail.append(row);
    }
    if(c.next_offset!==null){const b=button('更多证据',async()=>{b.remove();await show(cid,true,c.next_offset);});detail.append(b);}
  }
  async function collect(type,id){
    open();mode='sets';const token=++generation;const d=await api('/api/collections');if(token!==generation)return;
    detail.replaceChildren(el('h3','保存到证据集合'));
    const select=el('select');select.setAttribute('aria-label','选择证据集合');
    for(const c of d.collections)select.append(new Option(c.title,c.id));
    if(current&&d.collections.some(c=>c.id===current))select.value=current;
    let next=d.next_offset;
    const more=button('加载更多集合',async()=>{if(next===null)return;const page=await api('/api/collections?offset='+next);for(const c of page.collections)select.append(new Option(c.title,c.id));next=page.next_offset;more.hidden=next===null;});more.hidden=next===null;
    const name=el('input');name.placeholder='或输入新集合名称';name.maxLength=100;name.setAttribute('aria-label','新集合名称');
    detail.append(select,more,name,button('保存',async()=>{
      let cid=select.value;if(name.value.trim())cid=(await api('/api/collections','POST',{title:name.value.trim()})).id;
      if(!cid)throw Error('请选择集合或填写名称。');
      await api(`/api/collections/${cid}/items`,'POST',{items:[{source_type:type,source_id:String(id)}]});
      await listing();await show(cid);status.textContent='已保存引用，原资料保留在原处。';
    }));
  }
  async function knowledgeItem(id){
    open();mode='qq';const token=++generation;const s=await api('/api/knowledge/'+id);if(token!==generation)return;
    detail.replaceChildren(el('h3',s.kind==='notice'?'QQ 群公告':'QQ 精华'),el('p',s.conversation+' · '+s.publisher+' · '+s.time),
      el('pre',s.content),el('p',`${s.provenance} · 原始编号 ${s.native_id} · 缓存于 ${new Date(s.fetched_at*1000).toLocaleString()}`,'answer-note'));
    if(s.image_count)detail.append(el('p',`${s.image_count} 张图片的元数据；未下载或理解。`,'answer-note'));
    if(s.canonical_message_id){const a=el('a','打开原聊天','source-button');a.href='/?anchor='+s.canonical_message_id;a.target='_blank';a.rel='noopener';detail.append(a);}
    else if(s.kind==='essence')detail.append(el('p','尚未核对到原始聊天，不会猜测消息位置。','answer-note'));
    detail.append(button('保存这条资料',()=>collect('qq_'+s.kind,String(s.id))));
  }
  async function knowledge(){
    open();mode='qq';const token=++generation;const s=await api('/api/knowledge/status');if(token!==generation)return;
    list.replaceChildren();detail.replaceChildren();status.textContent=s.configured?s.note:'未配置本机 OneBot / NapCat。可选资料接口尚不能获取；其他聊天能力正常。';
    const group=el('select');group.setAttribute('aria-label','QQ群');for(const g of s.groups)group.append(new Option(g.conversation,g.conversation_id));
    const periodic=el('input');periodic.type='checkbox';const label=el('label','此群在启动及每6小时同步');label.prepend(periodic);
    const note=el('p','','answer-note');function update(){const v=s.sync.find(x=>x.conversation_id===group.value);periodic.checked=!!v?.enabled;note.textContent=v?`${v.status} · 最近成功：${v.last_success?new Date(v.last_success*1000).toLocaleString():'尚无'}`:'此群尚未同步';}
    group.onchange=()=>guard(async()=>{update();await load();});periodic.onchange=()=>guard(async()=>{const cid=group.value,enabled=periodic.checked;await api('/api/knowledge/schedule','POST',{conversation_id:cid,enabled});let saved=s.sync.find(x=>x.conversation_id===cid);if(saved)saved.enabled=enabled;else s.sync.push({conversation_id:cid,enabled,status:'pending'});update();});update();
    const refresh=button('同步所选群公告 / 精华',async()=>{
      await api('/api/knowledge/refresh','POST',{conversation_id:group.value,periodic:periodic.checked});status.textContent='正在同步，可继续正常对话…';refresh.disabled=true;
      for(;;){await new Promise(r=>setTimeout(r,1000));const v=await api('/api/knowledge/status');if(!v.running){if(mode==='qq'){s.sync=v.sync;update();status.textContent=v.error||JSON.stringify(v.result);await load();refresh.disabled=!s.configured;}break;}}
    });refresh.disabled=!s.configured;periodic.disabled=!s.configured;list.append(group,label,refresh,note);
    const sources=el('div');list.append(sources);
    async function load(append=false,offset=0){const cid=group.value;const d=await api('/api/knowledge?'+new URLSearchParams({conversation_id:cid,offset}));if(mode!=='qq'||cid!==group.value)return;if(!append)sources.replaceChildren();
      for(const item of d.sources)sources.append(button(`${item.kind==='notice'?'公告':'精华'} · ${item.content.slice(0,80)||'非文本资料'}`,()=>knowledgeItem(item.id)));
      if(d.next_offset!==null){const b=button('更多资料',async()=>{b.remove();await load(true,d.next_offset);});sources.append(b);}}
    await load();
  }
  document.getElementById('open-collections').onclick=()=>guard(async()=>{open();await listing();});sets.onclick=()=>guard(()=>listing());qq.onclick=()=>guard(knowledge);
  create.onclick=()=>guard(async()=>{const name=prompt('新集合名称');if(name!==null){const c=await api('/api/collections','POST',{title:name});await listing();await show(c.id);}});
  document.addEventListener('click',e=>{const b=e.target.closest('.collect-evidence,.open-collection,.open-knowledge');if(!b)return;e.preventDefault();
    guard(async()=>{open();if(b.classList.contains('collect-evidence'))await collect(b.dataset.sourceType,b.dataset.sourceId);else if(b.classList.contains('open-knowledge'))await knowledgeItem(b.dataset.knowledgeId);else{await listing();await show(b.dataset.collectionId);}});});
  const params=new URLSearchParams(location.search);if(params.has('collection'))guard(async()=>{open();await listing();await show(params.get('collection'));});
  else if(params.has('knowledge'))guard(()=>knowledgeItem(params.get('knowledge')));
})();
