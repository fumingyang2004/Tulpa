/* Persistent workspaces, using the existing composer and source viewers. */
(() => {
  const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  const api=(path,data,method)=>watchApi('/api/workspaces'+path,data,method);
  let current=null,home=null,tab='home',activeTask=null,generation=0,turns=[],materials=[],schedule=null,intent='auto';
  const expanded=new Map();
  const button=(text,fn,cls='quiet-button')=>{const b=node('button',text,cls);b.type='button';b.onclick=()=>safe(fn);return b;};
  async function safe(fn){try{return await fn();}catch(e){toast(e.message);}}
  function modal(title){const d=node('dialog',undefined,'ws-dialog');const h=node('div',undefined,'dialog-head');h.append(node('h2',title),button('关闭',()=>d.close()));d.append(h);document.body.append(d);d.addEventListener('close',()=>d.remove());d.showModal();return d;}
  function field(parent,label,input){const l=node('label',undefined,'ws-field');l.append(node('span',label),input);parent.append(l);return input;}
  function checklist(parent,title,choices,selected,{search=false,eligible=()=>true,empty='尚未选择',unit='项'}={}){
    const section=node('div',undefined,'ws-checklist');section.setAttribute('role','group');section.setAttribute('aria-label',title);section.append(node('p',title,'ws-checklist-title'));parent.append(section);
    const selectedValues=new Set(selected),labels=new Map(choices);for(const value of selectedValues)if(!labels.has(value))labels.set(value,'原已选项（目录暂不可用）');
    const filter=search?field(section,'筛选会话名称',node('input')):null;if(filter){filter.type='search';filter.placeholder='搜索联系人或群名…';}
    const list=node('div',undefined,'conversation-options'),summary=node('p',undefined,'answer-note'),none=node('p','没有匹配的选项','answer-note'),rows=[];section.append(list,summary);
    const values=()=>[...selectedValues].filter(eligible);
    function update(){const q=(filter?.value||'').trim().toLowerCase();let visible=0;for(const r of rows){r.row.hidden=!eligible(r.value)||!r.label.toLowerCase().includes(q);if(!r.row.hidden)visible++;}none.hidden=visible>0;summary.textContent=values().length?`已选择 ${values().length} ${unit}${q?'（包含筛选外的已选项）':''}`:empty;}
    for(const [value,label] of labels){const row=node('label',undefined,'conversation-option'),input=node('input');input.type='checkbox';input.value=value;input.checked=selectedValues.has(value);input.onchange=()=>{input.checked?selectedValues.add(value):selectedValues.delete(value);update();};row.append(input,node('span',label));list.append(row);rows.push({row,value,label});}
    list.append(none);if(filter)filter.oninput=update;update();return {values,update};
  }
  function source(ref){const p=node('span');
    if(!ref.available){p.textContent='来源不可用／超出范围';return p;}
    if(ref.changed)p.append(node('strong','来源已变更 · '));
    const a=node('a',ref.label||`${ref.source_type} ${ref.source_id}`,'source-button');a.href=ref.url||'#';a.target='_blank';a.rel='noopener';p.append(a);return p;
  }
  async function listing(){const d=await api('');const host=$('workspace-list');host.replaceChildren();
    for(const w of d.workspaces){const b=button(w.title,()=>open(w.id),'history-item');b.title=w.pending?`${w.pending} 项新内容待整理`:'打开工作区';if(w.id===state.workspaceId)b.classList.add('active');host.append(b);}}
  window.leaveWorkspace=()=>{document.body.classList.remove('workspace-ide');state.workspaceId=null;state.workspaceTaskRunning=false;current=null;home=null;generation++;$('workspace-page').hidden=true;$('messages').hidden=false;$('watch-current').hidden=false;$('question').placeholder='随心输入，问问聊天里的事…';activeTask=null;try{localStorage.removeItem('chatlocal.workspace');}catch{}};
  async function open(id){if(state.busy||state.loading)return;leaveWatch();document.body.classList.add('workspace-ide');state.workspaceId=id;state.stopRequested=false;tab='home';intent='auto';$('question').value='';
    $('messages').hidden=true;$('welcome').hidden=true;$('watch-threadbar').hidden=true;$('watch-current').hidden=true;$('workspace-page').hidden=false;
    try{localStorage.setItem('chatlocal.workspace',id);}catch{}await refresh();$('messages-scroll').scrollTop=0;if(mobile.matches)sidebar(false);}
  window.openWorkspace=open;
  function scopeText(w){const s=w.scope;return s.platforms.map(p=>p==='qq'?'QQ':'微信').join(' + ')+' · '+(s.conversations.length?s.conversations.map(v=>state.conversations.find(c=>JSON.stringify(JSON.parse(c[1]))===JSON.stringify(JSON.parse(v)))?.[0]||JSON.parse(v)[1]).join('、'):'全部已导入会话')+' · '+(s.start?new Date(s.start).toLocaleDateString():'不限开始')+' 至 '+(s.end?new Date(s.end-1).toLocaleDateString():'今后新增也在范围内');}
  async function refresh(){const id=state.workspaceId;if(!id)return;const token=++generation;const d=await api('/'+id);const h=await api('/'+id+'/home');if(token!==generation||state.workspaceId!==id)return;
    if(d.workspace.scope_epoch!==h.scope_epoch)return;current=d;home=h;
    const extra=await Promise.all([api('/'+id+'/thread'),api('/'+id+'/materials'),api('/'+id+'/schedule')]);if(token!==generation||state.workspaceId!==id)return;turns=extra[0].turns;materials=extra[1].materials;schedule=extra[2];
    const running=d.tasks.find(t=>t.state==='running');state.workspaceTaskRunning=Boolean(running);activeTask=running?.id||null;
    $('thread-title').textContent=d.workspace.title;$('range-chip').textContent='工作区范围';$('range-chip').title=scopeText(d.workspace);$('question').placeholder='在这个工作区继续工作…';
    const scroll=$('messages-scroll'),top=scroll.scrollTop,follow=tab==='home'&&scroll.scrollHeight-scroll.clientHeight-top<100;await render();scroll.scrollTop=follow?(scroll.scrollHeight||top):top;syncControls();await listing();}
  const when=stamp=>stamp?new Date(stamp*1000).toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}):'尚未更新';
  const taskState=s=>({completed:'已完成',running:'调查中',cancelled:'已停止',interrupted:'已中断',error:'未完成',failed:'未完成'}[s]||s);
  function disclosure(key,label,initial=false){const d=node('details');const k=state.workspaceId+':'+key;d.open=expanded.get(k)??initial;d.append(node('summary',label));d.addEventListener('toggle',()=>expanded.set(k,d.open));return d;}
  function compose(text){$('question').value=text;fitInput();$('question').focus();}
  function section(host,title,aside){const s=node('section',undefined,'ws-section'),head=node('div',undefined,'ws-section-heading');head.append(node('h3',title));if(aside)head.append(aside);s.append(head);host.append(s);return s;}
  const goto=key=>{tab=key;return render();};
  const firstTask=()=>start('围绕工作区目标自主调查，优先已有资料，至少尝试两种真实来源。生成 Markdown 现状报告和 CSV 资料索引。没有依据的部分明确注明，不虚构经历。');
  const organize=()=>start('整理新增候选，核对旧成果；有依据时修改同一份报告和索引。保留冲突及不确定性。','changes');
  function counts(c){const a=[];if(c.messages)a.push(`${c.messages} 条消息`);if(c.files)a.push(`${c.files} 个文件`);if(c.other)a.push(`${c.other} 项其他证据`);if(c.unavailable)a.push(`${c.unavailable} 项不可用`);return a.join(' · ')||'未关联原始证据';}
  function outputCard(o){const a=node('article',undefined,'ws-output');a.append(node('span',o.type,'ws-eyebrow'),node('h4',o.name),node('p',o.summary||'暂未提取到摘要，可预览完整成果。','ws-output-summary'));
    a.append(node('p',`更新 ${when(o.updated_at)} · v${o.version} · 来源：${counts(o.sources)}`,'answer-note'));
    if(o.origin==='user')a.append(node('p','用户编辑内容，尚未由 Agent 核对。','answer-note'));
    if(o.sources.changed)a.append(node('p',`${o.sources.changed} 项引用来源已有变化，需重新核对。`,'ws-warning'));
    const actions=node('div',undefined,'ws-inline-actions'),download=node('a','导出','quiet-button');download.href=`/api/workspaces/${state.workspaceId}/outputs/${o.id}/download`;
    actions.append(button('预览',()=>preview(o.id)),button('编辑',()=>preview(o.id,true)),download,button('查看证据',()=>evidence(o)));a.append(actions);return a;}
  function evidence(o){const d=modal(o.name+' · 来源证据');d.append(node('p',`v${o.version} · ${counts(o.sources)}`,'answer-note'));for(const r of o.refs)d.append(source(r),node('br'));if(!o.refs.length)d.append(node('p','这份成果没有关联原始证据。'));}
  function materialCard(r,compact=false){const a=node('article',undefined,'ws-material'),id=state.workspaceId;
    a.append(node('span',({accepted:'已确认',pending:'待确认',excluded:'已排除'}[r.state]||r.state),'ws-badge'),node('h4',r.available?(r.title||r.label||'聊天资料'):'原始资料不可用或超出当前范围'));
    const kind={message:'聊天原文',media:'聊天图片',voice:'语音',artifact_source:'文件',artifact_chunk:'文件片段',qq_notice:'QQ 公告',qq_essence:'QQ 精华'}[r.source_type]||'资料';
    a.append(node('p',[r.platform==='qq'?'QQ':r.platform==='wechat'?'微信':'',r.conversation,kind].filter(Boolean).join(' · '),'answer-note'));
    a.append(node('p','关联原因：'+(r.reason||'当前无法查看关联说明。'),'ws-material-reason'));
    if(r.changed)a.append(node('p','原文已有变化，需要重新核对。','ws-warning'));
    const actions=node('div',undefined,'ws-inline-actions');if(r.available){const link=node('a','查看来源','quiet-button');link.href=r.url;link.target='_blank';link.rel='noopener';actions.append(link);}
    if(r.state==='excluded')actions.append(button('解除排除',async()=>{await api('/'+id+'/materials',{source_type:r.source_type,source_id:r.source_id,release:true});await refresh();}));
    else{if(r.available&&r.state==='pending')actions.append(button('确认关联',async()=>{await api('/'+id+'/materials',{source_type:r.source_type,source_id:r.source_id,state:'accepted',reason:r.reason});await refresh();}));
      if(r.available)actions.append(button('加入成果',()=>compose(`请读取资料 ${r.source_type} ${r.source_id}（${r.title||r.label}），核对它与工作区目标的关系，再补入已有成果；覆盖前展示拟议改动。`)));
      if(!compact)actions.append(button('移除并排除',async()=>{await api('/'+id+'/materials',{source_type:r.source_type,source_id:r.source_id,state:'excluded',reason:r.reason});await refresh();}));}
    a.append(actions);return a;}
  function finding(p,index,question=false){const a=node('article',undefined,question?'ws-question':'ws-finding');
    if(p.text.length>240){a.append(node('p',p.text.slice(0,240)+'…'));const full=disclosure((question?'question':'finding')+index,'展开完整内容');full.append(node('p',p.text));a.append(full);}else a.append(node('p',p.text));
    const origin=node('div',undefined,'ws-origin');origin.append(button(`${p.output_name} · v${p.version}`,()=>preview(p.output_id)));
    if(p.refs.length){const refs=disclosure('refs'+(question?'q':'f')+index,`${p.refs.length} 项原始证据`);for(const r of p.refs)refs.append(source(r),node('br'));origin.append(refs);}
    else if(question)origin.append(node('span','报告中的覆盖限制；未关联单条原文','answer-note'));
    a.append(origin);if(p.stale)a.append(node('p','相关来源已变化或不可用，请重新核对。','ws-warning'));
    if(question)a.append(button('继续调查这个问题',()=>compose(`继续核实“${p.output_name}”v${p.version} 中的待确认问题：${p.text}\n请重新读取相关原始证据，核实后修改同一份成果；尚不能确认的内容继续保留。`)));return a;}
  function logs(host){const log=disclosure('logs','调查记录 · Agent 工作日志');log.className='ws-log';host.append(log);
    if(!home.tasks.length)log.append(node('p','还没有调查记录。','answer-note'));
    const add=(t,target)=>{const a=node('article',undefined,'ws-log-row');a.append(node('p',t.question),node('p',`${taskState(t.state)} · ${when(t.created_at)} · ${Number(t.seconds).toFixed(1)} 秒 · ${t.requests} 次请求 · ${t.tools} 次工具 · ${Number(t.tokens).toLocaleString()} tokens`,'answer-note'));
      if(t.result)a.append(node('p',t.result,'answer-note'));if(t.drafts)a.append(node('p',`保存 ${t.drafts} 项草稿／修订；是否已应用请查看成果及拟议改动。`,'answer-note'));
      a.append(button('详细过程与结果',()=>showTask(t.id)));if(t.state!=='running')a.append(button('继续这项任务',()=>compose('继续完成：'+t.question+'。先读取已有成果与草稿，沿用原成果继续修改并重新核对证据。')));target.append(a);};
    for(const t of home.tasks.slice(0,3))add(t,log);if(home.tasks.length>3){const rest=disclosure('older-logs',`更早的 ${home.tasks.length-3} 次调查`);for(const t of home.tasks.slice(3))add(t,rest);log.append(rest);}}
  function evidenceLinks(target,claim){
    const refs=node('div',undefined,'ws-inline-actions');
    for(const mid of claim.evidence_ids||[])refs.append(button('聊天原文',()=>openChat(mid,null,20,20)));
    for(const fid of claim.artifact_evidence_ids||[]){const m=fid.match(/^F(\d+)(?::C(\d+))?$/);if(m){const a=node('a','文件原文','source-button');a.href='/?file='+m[1]+(m[2]?'&chunk='+m[2]:'');a.target='_blank';refs.append(a);}}
    if(refs.childNodes.length)target.append(refs);
  }
  function currentState(host,compact=false){
    const area=section(host,'当前状态',home.current_state?button('打开状态文档',()=>preview(home.current_state.id)):node('span','尚未确认状态文档','ws-badge'));
    area.append(node('p',home.current_state?`已确认版本 v${home.current_state.version} · ${when(home.current_state.updated_at)}`:home.outputs.length?'以下摘自已应用成果；整理时可提出专用状态文档。':'围绕工作区目标展开调查。回答和未应用的建议不会自动写入这里。','answer-note'));
    for(const [i,p] of home.understanding.slice(0,compact?2:8).entries())area.append(finding(p,i));
    for(const p of (home.user_supplements||[]).slice(0,compact?1:8)){const row=node('div',undefined,'ws-finding');row.append(node('span','用户补充 · 未独立核实','ws-badge'),node('p',p.text));for(const ref of p.refs)row.append(source(ref));if(p.stale)row.append(node('p','此补充来源不可用或已变化，请重新确认。','ws-warning'));area.append(row);}
    const recent=node('div',undefined,'ws-recent-line');recent.append(node('strong','最近变化 '),node('span',home.recent.items.length?home.recent.items.map(r=>r.label+' '+r.count+' 项').join(' · '):'最近没有发现重要变化'));area.append(recent);
    if(home.recent.gap)area.append(node('p','变化记录存在缺口，请在设置中执行日期补查。','ws-warning'));
    if(home.questions.length){const q=disclosure('uncertainties','待确认事项 · 信息缺口，不是待办');for(const [i,p] of home.questions.entries())q.append(finding(p,i,true));area.append(q);}
    if(!compact&&home.current_state)area.append(button('预览 / 编辑当前状态',()=>preview(home.current_state.id)));
  }
  function explorer(host){
    const tree=node('aside',undefined,'ws-explorer');tree.setAttribute('aria-label','工作区资源树');
    tree.append(node('h3','工作区资源'),button('◉ Agent 对话',()=>goto('home'),'ws-tree-item'),button('▤ 当前状态',()=>goto('state'),'ws-tree-item'));
    const group=(key,title)=>{const d=disclosure('tree-'+key,title,true);d.className='ws-tree-group';tree.append(d);return d;};
    const outputs=group('outputs','工作成果');for(const o of home.outputs.filter(o=>o.resource_kind!=='state'&&o.resource_kind!=='note'))outputs.append(button('▤ '+o.name,()=>preview(o.id),'ws-tree-item'));
    if(!outputs.querySelector('button'))outputs.append(node('p','整理后在这里确认成果','ws-tree-empty'));
    const files=group('files','资料'),evidence=group('evidence','证据');
    for(const r of materials.filter(r=>r.state!=='excluded').slice(0,100)){const parent=r.source_type.startsWith('artifact_')?files:evidence;const b=button(r.title||r.label||'来源不可用',()=>{const d=modal('资料来源');d.append(materialCard(r));},'ws-tree-item');b.title=[r.conversation,r.reason].filter(Boolean).join(' · ');parent.append(b);}
    files.append(button('管理资料关联',()=>goto('materials'),'ws-tree-item subtle'));
    const notes=group('notes','笔记');for(const o of home.outputs.filter(o=>o.resource_kind==='note'))notes.append(button('▤ '+o.name,()=>preview(o.id),'ws-tree-item'));
    notes.append(button('+ 整理一条笔记',()=>compose('请将本次讨论整理成工作区笔记，保留出处，提出修改建议等待我确认。'),'ws-tree-item subtle'));
    tree.append(button('◷ 历史版本',()=>goto('history'),'ws-tree-item'),button('≡ Agent 工作日志',()=>goto('logs'),'ws-tree-item'),button(`◇ 修改建议${home.proposals?' · '+home.proposals:''}`,proposals,'ws-tree-item'));
    const maintenance=node('div',undefined,'ws-tree-footer');maintenance.append(node('span',schedule?.minutes?`自动维护 · 每 ${schedule.minutes>=1440?schedule.minutes/1440+' 天':schedule.minutes+' 分钟'}`:'自动维护关闭','answer-note'),button('工作区设置',()=>form(current.workspace),'ws-tree-item'));tree.append(maintenance);host.append(tree);
  }
  function chat(host){
    const intro=node('div',undefined,'ws-chat-state');currentState(intro,true);host.append(intro);
    const feed=node('div',undefined,'ws-chat-feed');feed.setAttribute('aria-live','polite');host.append(feed);
    if(!turns.length){const empty=node('div',undefined,'ws-chat-empty');empty.append(node('h3','从这里继续推进这件事'),node('p','可以提问、布置整理任务，或补充刚确认的信息。所有正式更新都先给你看差异。'),button('首次调查并整理',()=>start('围绕工作区目标调查已有资料，提出当前状态、报告及资料索引的修改建议。','task')),button('看看最近变化',()=>start('看看最近变化，说明已知信息与待确认事项。','ask')));feed.append(empty);}
    for(const t of turns){
      const turn=node('article',undefined,'ws-turn');turn.dataset.taskId=t.id;
      const user=node('div',undefined,'ws-chat-user');user.append(node('p',t.question));turn.append(user);
      const reply=node('div',undefined,'ws-chat-answer');const meta=node('div',undefined,'answer-note');meta.textContent=`${when(t.created_at)} · ${{ask:'提问',task:'整理 / 修改',fact:'补充事实',changes:'维护建议',manual:'手动编辑'}[t.mode]||'调查'} · ${taskState(t.state)}`;reply.append(meta);
      if(t.state==='running'){const progress=disclosure('live-'+t.id,'正在调查 · 查看工具过程',true);progress.append(node('pre',trace(t),'ws-progress'));progress.querySelector('pre').id='ws-live-progress';reply.append(progress);}
      for(const c of t.record.result?.claims||[]){reply.append(node('p',c.text));evidenceLinks(reply,c);}
      if(t.mode==='fact')reply.append(node('p','这段补充按用户提供的信息保留，未经独立核实；确认建议后才进入正式状态。','answer-note'));
      if(t.state!=='running'&&!t.record.result?.claims?.length)reply.append(node('p',t.record.note||t.record.error||(t.record.drafts_saved?.length?'已生成修改建议，尚未应用。':'本轮未形成可确认的结论，可以查看过程继续调查。')));
      if(t.record.notice)reply.append(node('p',t.record.notice,'ws-warning'));
      if(t.record.drafts_saved?.length){const box=node('div',undefined,'ws-proposal-inline');box.append(node('strong','建议修改'),node('p',[...new Set(t.record.drafts_saved.map(p=>p.name))].join('、')),button('查看差异 / 应用或忽略',()=>proposals(t.id)));reply.append(box);}
      const log=disclosure('turn-log-'+t.id,`${Number(t.record.seconds||0).toFixed(1)} 秒 · ${t.record.requests||0} 次请求 · ${t.record.usage?.total_tokens||0} tokens · 工作日志`);log.append(node('pre',trace(t),'ws-progress'),button('详细取证记录',()=>showTask(t.id)));reply.append(log);turn.append(reply);feed.append(turn);
    }
    const controls=node('div',undefined,'ws-chat-actions');controls.append(button('整理最近变化',()=>start('整理最近变化，更新当前状态与相关成果；先提出建议供我确认。','changes')),button(`查看修改建议 (${home.proposals})`,proposals));
    const mode=node('select');mode.id='ws-intent';mode.setAttribute('aria-label','本轮交互类型');for(const [v,t] of [['auto','自动识别意图'],['ask','只读提问'],['task','整理 / 修改'],['fact','补充事实']]){const o=new Option(t,v);o.selected=intent===v;mode.append(o);}mode.onchange=()=>intent=mode.value;controls.append(mode);host.append(controls);
  }
  async function render(){if(!current||!home)return;const w=current.workspace,id=w.id,host=$('workspace-page');host.replaceChildren();
    const header=node('header',undefined,'ws-ide-header');const title=node('div');title.append(node('h2',w.title),node('p',w.goal,'ws-goal'));header.append(title,button('设置',()=>form(w)));host.append(header);
    const layout=node('div',undefined,'ws-ide-layout');host.append(layout);explorer(layout);const content=node('main',undefined,'ws-main');layout.append(content);
    if(tab==='home')chat(content);
    else if(tab==='state')currentState(content);
    else if(tab==='outputs'){const a=section(content,'工作成果');for(const o of home.outputs)a.append(outputCard(o));}
    else if(tab==='materials'){const a=section(content,'资料与证据');a.append(node('p','这里是工作区已收资料，不会扩大允许访问的范围。Agent 收录建议需确认后生效。','answer-note'));for(const r of materials)a.append(materialCard(r));}
    else if(tab==='logs')logs(content);
    else{
      const data=await api('/'+id+'/history');if(state.workspaceId!==id||tab!=='history')return;
      section(content,'历史版本');for(const v of data.versions){const item=node('details',undefined,'ws-version');item.append(node('summary',`v${v.version} · ${v.summary} · ${v.name}`),node('p',when(v.created_at),'answer-note'),node('pre',v.diff,'ws-diff'));if(v.scope_epoch===w.scope_epoch)item.append(button('恢复此版本',async()=>{const now=await api(`/${id}/outputs/${v.output_id}`);if(confirm(`恢复“${v.name}”至 v${v.version}？会保存为新版本，原始资料不变。`)){await api(`/${id}/outputs/${v.output_id}/restore`,{version:v.version,base_version:now.version,confirm:true});await refresh();}}));content.append(item);}if(!data.versions.length)content.append(node('p','确认第一批建议后，版本历史会保留在这里。','ws-empty'));
      const audit=disclosure('audit','资料、设置与维护记录');const labels={created:'创建工作区',material_decision:'资料关联决定',exclusion_released:'解除资料排除',scope_changed:'修改访问范围',bounded_backfill:'日期补查',gap_reconnected:'重新接续变化',version_applied:'应用版本',task_completed:'调查完成',material_proposal_applied:'确认资料建议',proposals_rejected:'忽略修改建议',maintenance_settings:'更新自动维护设置'};for(const r of data.audit||[])audit.append(node('p',when(r.created_at)+' · '+(labels[r.kind]||'工作区操作'),'answer-note'));content.append(audit);
    }
    if(activeTask)await pollTask(activeTask,false);
  }
  function trace(t){return t.events.map(e=>e.type==='model_start'?`请求模型 ${e.request}/${e.request_limit} · 本次上下文 ${e.characters} 字符 · 思考 ${e.reasoning_effort||'关闭'}`:e.text||[e.type,e.name,e.summary?JSON.stringify(e.summary):''].filter(Boolean).join(' · ')).join('\n');}
  async function pollTask(tid,refreshAfter=true){const wid=state.workspaceId;if(!wid)return;const t=await api(`/${wid}/tasks/${tid}`);if(state.workspaceId!==wid)return;
    const p=$('ws-live-progress');if(p)p.textContent=trace(t);
    if(t.state!=='running'){state.workspaceTaskRunning=false;activeTask=null;syncControls();if(refreshAfter)await refresh();}}
  async function showTask(tid){const wid=state.workspaceId,d=modal('任务记录'),t=await api(`/${wid}/tasks/${tid}`);d.append(node('p',t.question),node('p',`${t.state} · 请求 ${t.record.requests||0} 次 · ${t.record.seconds||0} 秒 · ${t.record.usage?.total_tokens||0} tokens`,'answer-note'));
    if(t.record.budget)d.append(node('p',`本轮预算：${t.record.budget.max_requests} 次请求 / ${t.record.budget.max_tool_calls} 次工具 / ${t.record.budget.max_messages} 条消息 / ${t.record.budget.max_evidence_chars} 字符 / ${t.record.budget.max_seconds} 秒`,'answer-note'));
    const detail=node('details');detail.append(node('summary','真实工具过程'),node('pre',trace(t),'ws-progress'));d.append(detail);
    for(const c of t.record.result?.claims||[]){const p=node('p',c.text);for(const mid of c.evidence_ids||[]){const a=node('a',` M${mid}`,'source-button');a.href='/?anchor='+mid;a.target='_blank';p.append(a);}for(const fid of c.artifact_evidence_ids||[]){const m=fid.match(/^F(\d+)(?::C(\d+))?$/);if(m){const a=node('a',' '+fid,'source-button');a.href='/?file='+m[1]+(m[2]?'&chunk='+m[2]:'');a.target='_blank';p.append(a);}}d.append(p);}
    for(const key of ['note','notice','error','continuation'])if(t.record[key])d.append(node('p',t.record[key],'answer-note'));
    if(t.record.result?.error)d.append(node('p',t.record.result.error));
    d.append(node('p','覆盖：'+JSON.stringify(t.record.coverage||{}),'answer-note'));
    if(t.record.drafts_saved)d.append(node('p','程序确认保存的草稿：'+(t.record.drafts_saved.map(p=>p.name+' ('+p.format+')').join('、')||'本轮没有写入成果'),'answer-note'));
  }
  async function start(question,mode='auto'){const wid=state.workspaceId;if(!wid||state.workspaceTaskRunning)return;const result=await api('/'+wid+'/tasks',{question,mode,profile:state.profile,vision:state.visionMode});activeTask=result.task_id;tab='home';await refresh();const scroll=$('messages-scroll');scroll.scrollTop=scroll.scrollHeight||0;if(result.api_called===false)toast('没有待整理候选，未调用模型。');}
  window.sendWorkspaceTask=async()=>safe(async()=>{const text=$('question').value.trim();if(!text||state.workspaceTaskRunning)return;await start(text,intent);if($('question').value.trim()===text)$('question').value='';fitInput();});
  window.stopWorkspaceTask=async()=>safe(async()=>{if(activeTask)await api(`/${state.workspaceId}/tasks/${activeTask}/stop`,{});toast('已请求停止；草稿、过程与待处理候选会保留。');});
  async function preview(oid,editing=false){const wid=state.workspaceId,v=await api(`/${wid}/outputs/${oid}`),d=modal(v.name+' · v'+v.version);
    const view=node('pre',v.body,'ws-preview'),editor=node('textarea');editor.value=v.body;editor.rows=18;editor.hidden=!editing;view.hidden=editing;editor.className='ws-editor';editor.setAttribute('aria-label','成果正文');d.append(view,editor);
    d.append(node('p',v.origin==='user'?'这是用户编辑内容，未经Agent事实核对。':'事实引用经过来源检查，不保证模型解释正确。','answer-note'));
    for(const r of v.refs)d.append(source(r),node('br'));
    const save=button('预览手动修改',async()=>{await api(`/${wid}/outputs/${oid}`,{body:editor.value,base_version:v.version},'PATCH');d.close();await refresh();await proposals();});save.hidden=!editing;
    const a=node('a','下载 '+v.format.toUpperCase(),'primary-button');a.href=`/api/workspaces/${wid}/outputs/${oid}/download`;d.append(a,button('编辑',()=>{editor.hidden=false;view.hidden=true;save.hidden=false;}),save,button('查看版本 / 回退',async()=>{d.close();tab='history';await render();}));
  }
  async function proposals(taskId=null){const wid=state.workspaceId,d=modal('修改建议 · 确认后才应用'),result=await api('/'+wid+'/proposals');const ids=[];
    d.append(node('p','选择需要的修改，先核对差异和证据，再一次确认。新成果也不会自动应用；忽略后正式状态不变。','answer-note'));
    const selected=result.proposals.filter(p=>!taskId||p.task_id===taskId);
    for(const p of selected){const item=node('details',undefined,'ws-card');item.open=true;const label=node('label'),check=node('input');check.type='checkbox';check.checked=true;ids.push([p.id,check]);label.append(check,document.createTextNode(p.name+' · '+p.summary));item.append(node('summary',p.action==='material'?'收录资料建议':p.base_version?'修改现有成果':'创建新成果'),label,node('pre',p.action==='material'?p.summary:(p.diff||p.body),'ws-diff'));for(const r of p.refs)item.append(source(r));d.append(item);}
    if(!selected.length){d.append(node('p','这些建议已处理，或当前没有待确认修改。'));return;}
    const chosen=()=>ids.filter(([,c])=>c.checked).map(([id])=>id);
    d.append(button('确认并应用选中改动',async()=>{await api('/'+wid+'/apply',{ids:chosen(),confirm:true});d.close();await refresh();},'primary-button'),button('忽略选中建议',async()=>{await api('/'+wid+'/reject',{ids:chosen()});d.close();await refresh();}));
  }
  async function backfill(){const wid=state.workspaceId,d=modal('指定日期补查'),a=field(d,'开始日期',node('input')),b=field(d,'截止日期（含当天）',node('input'));a.type=b.type='date';let offset=0;
    const onlyNew=field(d,'仅补查最近一次范围设置新增的部分',node('input'));onlyNew.type='checkbox';a.onchange=b.onchange=onlyNew.onchange=()=>{offset=0;};
    const note=node('p','每页最多200项消息、文件和已启用QQ资料，只登记候选，不调用模型。仍受工作区范围限制；分页直到完成才覆盖该窗口。','answer-note');d.append(note,button('补查下一页',async()=>{const r=await api('/'+wid+'/backfill',{start:a.value,end:b.value,offset,new_scope_only:onlyNew.checked});offset=r.next_offset;note.textContent=`本页 ${r.count} 项；${r.has_more?'仍有更多，继续下一页':'这个日期窗口已扫描完毕'}。点击整理最近变化后才分析。`;await refresh();}));
    if(current.workspace.gap)d.append(button('确认补查限制并重新接续',async()=>{if(confirm('确认已完成需要的日期补查？旧缺口不能保证完整恢复，将从当前变化序列重新接续。')){await api('/'+wid+'/reconnect',{confirm:true});d.close();await refresh();}}));
  }
  async function form(existing=null){const d=modal(existing?'工作区设置':'新建工作区'),goal=field(d,'你希望在这里完成什么？',node('textarea'));goal.rows=3;goal.maxLength=2000;goal.value=existing?.goal||'';
    const title=field(d,'名称（可修改）',node('input'));title.maxLength=100;title.value=existing?.title||'';goal.oninput=()=>{if(!existing&&!title.dataset.edited)title.value=goal.value.slice(0,32);};title.oninput=()=>title.dataset.edited='1';
    const platforms=node('div',undefined,'platforms');const checks={};for(const p of ['qq','wechat']){const check=node('input');check.type='checkbox';check.checked=(existing?.scope.platforms||['qq','wechat']).includes(p);const l=node('label');l.append(check,document.createTextNode(p==='qq'?'QQ':'微信'));platforms.append(l);checks[p]=check;}d.append(platforms);
    const scope=checklist(d,'允许搜查的会话（不选＝所选平台全部会话）',state.conversations.map(([label,value])=>[JSON.stringify(JSON.parse(value)),label.replace(/^wechat/,'微信').replace(/^qq/,'QQ')]),(existing?.scope.conversations||[]).map(value=>JSON.stringify(JSON.parse(value))),{search:true,eligible:value=>checks[JSON.parse(value)[0]]?.checked,empty:'未指定会话：允许搜查所选平台的全部已导入会话',unit:'个会话'});
    for(const check of Object.values(checks))check.onchange=scope.update;
    const a=field(d,'开始日期（留空不限）',node('input')),b=field(d,'截止日期（留空包含未来新消息）',node('input'));a.type=b.type='date';const date=ms=>new Date(ms+8*3600000).toISOString().slice(0,10);a.value=existing?.scope.start?date(existing.scope.start):'';b.value=existing?.scope.end?date(existing.scope.end-1):'';
    let collection=null,message=null,file=null;
    if(!existing){collection=field(d,'起始证据集合（复制引用，不改原集合；最多100项）',node('select'));collection.append(new Option('不选择',''));const sets=await watchApi('/api/collections');for(const c of sets.collections)collection.append(new Option(c.title,c.id));
      message=field(d,'起始聊天证据编号（可选，例如 M39527）',node('input'));file=field(d,'起始文件编号（可选，例如 F12）',node('input'));}
    const watches=checklist(d,'关联已有关注卡（可选，不复制基线和调度）',watchCards.map(w=>[w.id,w.title]),existing?.watch_ids||[],{empty:watchCards.length?'未关联关注卡':'暂无可关联的关注卡',unit:'张关注卡'});
    d.append(node('p','默认只发现新候选；明确整理或开启自动维护后才分析，所有建议待确认。范围/目标改变后，旧任务和成果正文隔离，需在新范围重新取证；使用日期补查新增范围。','answer-note'));
    d.append(button(existing?'保存设置':'创建工作区',async()=>{const s={platforms:Object.keys(checks).filter(p=>checks[p].checked),conversations:scope.values(),start:a.value,end:b.value};const data={goal:goal.value,title:title.value,scope:s,watch_ids:watches.values()};
      if(existing){await api('/'+existing.id,{...data,revision:existing.revision},'PATCH');d.close();await refresh();}
      else{const seeds=[];if(message.value.trim())seeds.push({source_type:'message',source_id:message.value.trim().replace(/^M/i,'')});if(file.value.trim())seeds.push({source_type:'artifact_source',source_id:file.value.trim().replace(/^F/i,'')});const w=await api('',{...data,seeds,collection_id:collection.value||null});if(data.watch_ids.length)await api('/'+w.id,{...data,revision:w.revision},'PATCH');d.close();await open(w.id);}},'primary-button'));
    if(existing){
      const saved=await api('/'+existing.id+'/schedule'),settings=node('fieldset',undefined,'ws-schedule');settings.append(node('legend','自动维护 · 只提建议'));
      const frequency=field(settings,'频率',node('select'));for(const [v,t] of [[0,'关闭'],[1440,'每天'],[10080,'每周'],[-1,'自定义']])frequency.append(new Option(t,String(v)));
      frequency.value=[0,1440,10080].includes(saved.minutes)?String(saved.minutes):'-1';
      const minutes=field(settings,'自定义间隔（分钟，至少30）',node('input'));minutes.type='number';minutes.min='30';minutes.max='525600';minutes.value=saved.minutes||1440;minutes.hidden=frequency.value!=='-1';frequency.onchange=()=>minutes.hidden=frequency.value!=='-1';
      const profiles=await watchApi('/api/agent-presets'),budget=field(settings,'每次维护的预算档位',node('select')),budgetNote=node('p',undefined,'answer-note');for(const p of profiles.profiles)budget.append(new Option(p.label,p.id));budget.value=saved.profile;
      const showBudget=()=>{const p=profiles.profiles.find(p=>p.id===budget.value);budgetNote.textContent=`每次最多 ${p.budget.max_requests} 次请求、${p.budget.max_tool_calls} 次工具、${p.budget.max_seconds} 秒；云端用量计入当前 API。没有待处理候选时不调用模型。`;};budget.onchange=showBudget;showBudget();settings.append(budgetNote);
      const vision=field(settings,'识图方式',node('select'));vision.append(new Option('本地 OCR','ocr'),new Option('原生识图（按需发送指定图片）','native'));vision.value=saved.vision;
      settings.append(node('p','范围：'+scopeText(existing),'answer-note'),node('p',saved.next_at?'下次维护 '+when(saved.next_at):'当前关闭。应用运行期间调度，重启后保留设置。','answer-note'));
      if(saved.error)settings.append(node('p',saved.error,'ws-warning'));
      settings.append(button('保存维护设置',async()=>{schedule=await api('/'+existing.id+'/schedule',{minutes:frequency.value==='-1'?Number(minutes.value):Number(frequency.value),profile:budget.value,vision:vision.value},'PUT');toast('维护设置已保存；只生成待确认建议。');d.close();await refresh();}));d.append(settings,button('指定日期补查',()=>{d.close();void backfill();}),button('查看关联关注卡',()=>{for(const wid of existing.watch_ids){d.append(button(watchCards.find(c=>c.id===wid)?.title||'关联关注卡',()=>{d.close();openWatch(wid);}));}}));
    }
    if(existing)d.append(button('删除工作区',async()=>{if(confirm('删除此工作区的成果、版本和专用资料集合？原始聊天、文件、其他集合和独立关注卡保留。')){await api('/'+existing.id,{confirm:true},'DELETE');d.close();leaveWorkspace();await listing();await newSession();}}));
  }
  window.editWorkspace=()=>safe(()=>form(current?.workspace));$('new-workspace').onclick=()=>safe(()=>form());
  setInterval(()=>{if(activeTask&&state.workspaceId)void safe(()=>pollTask(activeTask));},1500);
  setInterval(()=>{if(!document.hidden){void safe(listing);if(state.workspaceId&&!state.workspaceTaskRunning&&!document.querySelector('dialog[open]')&&!$('workspace-page').contains(document.activeElement))void safe(refresh);}},30000);
  let boot=setInterval(()=>{if(!state.ready||!state.initialized)return;clearInterval(boot);void safe(async()=>{await listing();const query=new URLSearchParams(location.search);let saved=query.get('workspace');try{saved=saved||localStorage.getItem('chatlocal.workspace');}catch{}if(saved){await open(saved);if(query.get('workspace')&&query.get('task'))await showTask(query.get('task'));}});},500);
})();
