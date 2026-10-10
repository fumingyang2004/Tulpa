(() => {
  const $ = id => document.getElementById(id);
  const dialog = document.createElement('dialog');
  dialog.id = 'mcp-settings'; dialog.className = 'mcp-dialog';
  dialog.setAttribute('aria-labelledby', 'mcp-title');
  dialog.innerHTML = `<div class="dialog-head"><h2 id="mcp-title">连接外部 Agent</h2><button id="mcp-close" type="button" class="icon-button" aria-label="关闭 MCP 设置">×</button></div>
    <div class="mcp-body">
      <p>让 Codex、DeepSeek Harness 等 MCP 客户端使用你授权的资料与 QQ 操作。无需配置 Tulpa 模型，发送和管理默认关闭；勾选授权后可直接执行，无需逐次审批。</p>
      <section class="mcp-chat-entry"><div><h3>群聊与学习</h3><p id="mcp-chat-summary" class="mcp-muted" role="status">查看会话状态与学习记录</p></div><button id="mcp-chat-open" type="button" aria-haspopup="dialog" aria-controls="mcp-chat-manager">管理</button></section>
      <section class="mcp-step"><h3>1. 准备资料</h3><p id="mcp-data-status" role="status"></p><button id="mcp-open-data" type="button">导入 / 管理聊天</button>
      <p id="mcp-onebot-status" class="mcp-muted"></p><div id="mcp-snowluma-managed"></div><button id="mcp-open-onebot" type="button">已有 OneBot / 高级连接</button> <button id="mcp-test-onebot" type="button">检测 OneBot</button></section>
      <p id="mcp-service-status" role="status"></p>
      <label class="mcp-background" id="mcp-background-label" hidden><input id="mcp-background" type="checkbox">关闭窗口后留在托盘，继续提供服务</label>
      <details id="mcp-create-section" open><summary>2. 选择范围并创建连接</summary>
        <p class="mcp-muted">仅授权访问当前 Tulpa 的本地资料。创建连接时可一并开启服务；外部 Agent 读到的内容可能发给它的模型服务。</p>
        <form id="mcp-create-form" autocomplete="off">
          <label for="mcp-name">连接名称</label><input id="mcp-name" required maxlength="60" placeholder="例如：Codex 课程资料调查">
          <fieldset><legend>允许访问</legend><label><input id="mcp-qq" type="checkbox" checked> QQ</label><label><input id="mcp-wechat" type="checkbox" checked> 微信</label>
            <label class="mcp-all"><input id="mcp-all" type="checkbox">允许所选平台全部会话（包括以后入库的会话）</label>
            <div id="mcp-picker"><label for="mcp-search">选择会话</label><input id="mcp-search" type="search" placeholder="搜索群名或联系人"><button id="mcp-live-groups" type="button">从 OneBot 选择群 · 无需导入</button><div id="mcp-chats" class="mcp-chats"></div><button id="mcp-more" type="button" hidden>更多会话</button><p id="mcp-selection" class="mcp-muted">已选择 0 个会话</p></div>
          </fieldset>
          <div class="mcp-dates"><label>开始日期<input id="mcp-start" type="date"></label><label>截止日期<input id="mcp-end" type="date"></label></div><small class="mcp-muted">留空表示不限制；截止日期包含当天。身份目录也遵守这个范围。</small>
          <fieldset><legend>按需处理权限</legend>
            <label><input id="mcp-media" type="checkbox">向外部 Agent 提供图片（最多 60 次/小时）</label>
            <label><input id="mcp-prepare" type="checkbox">下载原文件 / 按需提取文字（合计最多 12 次/小时，每个 32 MiB）</label>
            <label><input id="mcp-voice" type="checkbox">本地转写语音（最多 12 次/小时）</label>
            <label><input id="mcp-onebot" type="checkbox">读取 OneBot 群公告、精华、当前成员和文件目录</label>
            <label><input id="mcp-send" type="checkbox">允许直接发送 QQ 消息（无需逐次审批）</label>
            <label><input id="mcp-chat" type="checkbox">允许外部 Agent 持续群聊（需发送权限，可随时停止）</label>
            <label><input id="mcp-chat_reactions" type="checkbox" disabled>允许对已读群消息添加 / 撤销小表情回应（无需逐次审批）</label>
            <small class="mcp-muted">与发送表情包独立，默认每会话冷却 10 秒。旧连接不会自动获得此权限。</small>
            <fieldset class="mcp-chat-media"><legend>持续群聊 · 表情包（可选）</legend>
              <label><input id="mcp-chat_images" type="checkbox" disabled>看群内图片 / 动图，读取此 QQ 账号的收藏表情</label>
              <label><input id="mcp-chat_sticker_send" type="checkbox" disabled>允许在持续聊天的群里直接发送表情</label>
              <label><input id="mcp-chat_sticker_collect" type="checkbox" disabled>允许把看过的图片直接收藏到 QQ</label>
              <small class="mcp-muted">先开启持续群聊，再选择所需权限。看图由外部模型完成，不调用 Tulpa 模型；每小时最多看图 60 次、收藏 12 次，每张 6 MiB。发送每分钟最多 12 次；停止群聊也会停止表情操作。</small>
            </fieldset>
            <label><input id="mcp-manage" type="checkbox">允许直接执行 QQ 群管理（无需逐次审批）</label>
          </fieldset><p class="mcp-muted">普通消息查询可连续分页，没有任务消息总量上限。文件和语音处理仅生成本地缓存。OneBot 须已有有效配置；群信息和成员是查询当时的状态，不能还原历史。权限修改请撤销旧连接后新建。</p>
          <p id="mcp-create-status" class="mcp-muted" role="status" aria-live="polite"></p>
          <button id="mcp-create" type="submit" aria-describedby="mcp-create-status">创建连接凭据</button>
          <button id="mcp-edit-port" type="button" hidden>修改服务端口</button>
        </form>
      </details>
      <section id="mcp-secret" hidden><h3>3. 检测并连接 Agent</h3><p>Token 只显示这一次。关闭后无法找回；丢失时撤销并重新创建。</p><label>本机地址<input id="mcp-url" readonly></label><label>访问 Token<input id="mcp-token" type="password" readonly autocomplete="off"></label><button id="mcp-reveal" type="button">显示 / 隐藏 Token</button>
      <p><button id="mcp-test" type="button">检测连接与工具</button></p><p id="mcp-test-result" role="status"></p>
      <p>下方是将写入本机 Codex 的配置。点击写入会备份原文件、保留其他设置；已有同名连接时不会覆盖。写入后请在 Codex 的 MCP 设置中重新连接，必要时重启 Codex。</p><textarea id="mcp-config" readonly rows="5" aria-label="Codex MCP 连接配置"></textarea><button id="mcp-install-codex" type="button">写入本机 Codex 配置</button> <button id="mcp-copy" type="button">复制 Codex 配置</button><p id="mcp-install-result" role="status"></p><small class="mcp-muted">其他客户端：选择 Streamable HTTP，填写上方地址，添加 Authorization: Bearer Token。OneBot 的密钥不需要填入客户端。</small></section>
      <h3>已授权连接</h3><div id="mcp-connections"></div>
      <details id="mcp-advanced"><summary>高级服务设置</summary><p class="mcp-muted">通常无需修改。停用服务会断开外部 Agent；修改端口后，需要同步修改客户端的连接地址。</p><div class="mcp-service"><label><input id="mcp-enabled" type="checkbox">启用本机 MCP 服务</label><label>端口 <input id="mcp-port" type="number" min="1024" max="65535" value="18777"></label><button id="mcp-save-service" type="button">应用高级设置</button></div></details>
      <details><summary>最近访问记录</summary><p class="mcp-muted">只记录工具、耗时和读取数量，不记录查询词、正文或 Token。</p><div id="mcp-audit"></div></details>
      <details id="mcp-operation-history"><summary>QQ 操作记录</summary><p class="mcp-muted">按连接授权直接执行的发送和群管理回执。结果未知时先到 QQ 核对，不自动重试。</p><div id="mcp-operations"></div></details>
      <p id="mcp-error" role="alert"></p>
    </div>`;
  const chatDialog = document.createElement('dialog');
  chatDialog.id='mcp-chat-manager';chatDialog.className='mcp-dialog';
  chatDialog.setAttribute('aria-labelledby','mcp-chat-title');
  chatDialog.innerHTML=`<div class="dialog-head"><h2 id="mcp-chat-title">群聊与学习</h2><button id="mcp-chat-close" type="button" class="icon-button" aria-label="关闭群聊与学习">×</button></div>
    <div class="mcp-body" id="mcp-chat-panel">
      <p>请在已连接的 Agent 对话中发起持续聊天，例如“用小鲸鱼2号在某群持续聊天”。这里可以查看状态、停止会话和管理学习记录。</p>
      <details><summary>运行与人物说明</summary><p class="mcp-muted">在当前机器人 QQ 和群聊中边聊边学，潜水也会积累。停止后重新开始，原群的学习记录仍在。</p>
      <p class="mcp-muted">新人格可保存为 UTF-8 .md 文件，放入当前安装目录的 chatlocal/prompts/mcp_chat/。让 Agent 查看人格列表即可发现，无需重启；已经开启的群聊继续使用原人格。</p>
      <p class="mcp-muted">保持 QQ、OneBot WebSocket 事件服务和外部 Agent 运行。外部客户端结束任务后需在 Agent 对话中接续，Tulpa 不会自动启动模型。未接续时显示等待 Agent。</p></details>
      <p id="mcp-event-status" role="status" class="mcp-muted"></p><details id="mcp-chat-advanced"><summary>高级操作</summary><button id="mcp-chat-refresh" type="button">刷新聊天状态</button> <button id="mcp-chat-stop-all" type="button" disabled>停止全部群聊</button></details>
      <p id="mcp-chat-status" role="status" aria-live="polite">正在读取群聊状态…</p><div id="mcp-chat-sessions"></div><p id="mcp-chat-error" class="mcp-warning" role="alert"></p>
    </div>`;
  document.body.append(dialog,chatDialog);
  const managedQQ=window.TulpaSnowLuma?.mount($('mcp-snowluma-managed'),{prefix:'mcp',onSaved:async()=>refresh()});
  let current, credential=null, busy=false, createError='', platformsInitialized=false, selected = new Map(), page = 0, searchVersion = 0, searchTimer, chatTimer, chatRefreshing=false, liveGroups=null;
  async function api(path, method='GET', body) {
    let response,data;
    try{response=await fetch('/api/mcp'+path, {method, cache:'no-store', headers: {'Content-Type':'application/json','X-ChatWeave-UI':'1'}, body: body===undefined ? undefined : JSON.stringify(body)});}
    catch{throw Error('无法连接本机服务，请确认 Tulpa 仍在运行。');}
    try{data=await response.json();}catch{throw Error('本机服务暂时没有返回可读取的结果，请稍后重试。');}
    if (!response.ok) throw Error(data.detail || '连接设置操作失败。'); return data;
  }
  function node(tag, value, cls) {const n=document.createElement(tag);n.textContent=value;if(cls)n.className=cls;return n;}
  function renderService() {
    $('mcp-create').disabled=busy;
    $('mcp-save-service').disabled=$('mcp-enabled').disabled=$('mcp-port').disabled=busy;
    $('mcp-create').textContent=busy?'正在处理…':current?.running?'创建连接凭据':current?'开启服务并创建连接':'检查服务并创建连接';
    const error=createError||current?.error;
    $('mcp-create-status').textContent=error||(current?.running?'本机 MCP 服务已运行，可以创建连接。':current?'本机 MCP 服务尚未运行。点击下方按钮将开启服务，并按上面选择的范围创建连接。':'创建时会检查本机 MCP 服务状态。');
    $('mcp-create-status').classList.toggle('mcp-warning',!!error);
    $('mcp-edit-port').hidden=!current?.error;
    $('mcp-edit-port').disabled=busy;
    $('mcp-service-status').textContent=current?.error || (current?.running ? '本机服务运行中 · '+current.url : '本机服务尚未运行，可在下方创建连接时开启。');
  }
  function serviceState(data) {
    current={...current,...data};$('mcp-enabled').checked=current.enabled;$('mcp-port').value=current.port;renderService();
  }
  async function refresh() {
    serviceState(await api(''));
    const counts=current.data_platforms||{};
    if(!platformsInitialized&&Object.keys(counts).length){for(const p of ['qq','wechat'])$('mcp-'+p).checked=!!counts[p];platformsInitialized=true;}
    $('mcp-data-status').textContent=Object.keys(counts).length?Object.entries(counts).map(([p,n])=>(p==='qq'?'QQ':'微信')+' '+n.toLocaleString()+' 条本地消息').join(' · '):'历史查询需要导入资料；仅实时群聊可配置 OneBot 后直接选择群、授权，无需导入。';
    $('mcp-onebot-status').textContent=current.onebot_configured?'已保存 OneBot 配置；点击检测确认服务在线。':'OneBot 未配置，本地聊天查询仍可使用。公告、精华及群文件可稍后接入。';
    $('mcp-connections').replaceChildren();
    for (const row of current.connections) {
      const card=node('div','', 'mcp-connection'); card.append(node('strong',row.name+(row.revoked?' · 已撤销':'')));
      const scope=row.scope;
      card.append(node('p',scope.platforms.join(' / ')+' · '+(scope.conversations.length?scope.conversations.length+' 个指定会话':'全部会话')+' · '+(scope.start||'不限起点')+' 至 '+(scope.end||'包括未来消息'), 'mcp-muted'));
      card.append(node('p','操作权限：'+[scope.send?'QQ 直接发送':'',scope.chat?'持续群聊':'',scope.chat_reactions?'消息表情回应':'',scope.chat_images?'群聊看图与收藏目录':'',scope.chat_sticker_send?'发送表情':'',scope.chat_sticker_collect?'收藏到 QQ':'',scope.manage?'群管理直接操作':''].filter(Boolean).join('、')+(scope.send||scope.manage?' · 持续授权，可撤销':'仅资料读取'),'mcp-muted'));
      card.append(node('p','来源账号：'+Object.entries(row.accounts).map(([p,id])=>p+' '+(id||'当前导入资料')).join(' / ')+' · '+row.calls+' 次调用', 'mcp-muted'));
      if(row.valid===false&&!row.revoked)card.append(node('p','来源账号已改变，请撤销并重新创建连接。','mcp-warning'));
      if(!row.revoked){const button=node('button','撤销连接');button.type='button';button.onclick=()=>action(async()=>{await api('/connections/'+encodeURIComponent(row.id)+'/revoke','POST',{});await refresh();});card.append(button);}
      $('mcp-connections').append(card);
    }
    if(!current.connections.length)$('mcp-connections').append(node('p','还没有授权连接。','mcp-muted'));
    $('mcp-audit').replaceChildren(...current.recent.map(row=>node('p',new Date(row.at*1000).toLocaleString()+' · '+row.name+' · '+row.tool+' · '+row.status+' · '+row.seconds+' 秒 · '+row.messages+' 条消息','mcp-muted')));
  }
  function selection(){ $('mcp-selection').textContent='已选择 '+selected.size+' 个会话（搜索不会清除已选）'; }
  async function chats(reset=true) {
    const version=++searchVersion;if(reset){page=0;$('mcp-chats').replaceChildren();}
    const data=liveGroups?{items:liveGroups.filter(r=>r.name.toLowerCase().includes($('mcp-search').value.toLowerCase())),has_more:false,next_offset:0}:await api('/conversations?'+new URLSearchParams({query:$('mcp-search').value,offset:page}));if(version!==searchVersion)return;
    for(const item of data.items){const pair=[item.platform,item.conversation_id],key=JSON.stringify(pair);const label=node('label','');
      const input=document.createElement('input');input.type='checkbox';input.checked=selected.has(key);
      input.disabled=!$(item.platform==='qq'?'mcp-qq':'mcp-wechat').checked;
      input.onchange=()=>{if(input.checked)selected.set(key,pair);else selected.delete(key);selection();};
      label.append(input,node('span',item.platform+' · '+item.name+' ('+item.count+')'));$('mcp-chats').append(label);}
    if(reset&&!data.items.length)$('mcp-chats').append(node('p','没有匹配的已导入会话。','mcp-muted'));
    page=data.next_offset;$('mcp-more').hidden=!data.has_more;selection();
  }
  async function action(fn){$('mcp-error').textContent='';try{await fn();}catch(e){$('mcp-error').textContent=e.message;}}
  async function chatAction(fn){$('mcp-chat-error').textContent='';try{await fn();}catch(e){$('mcp-chat-error').textContent=e.message;}}
  const dateText=value=>value?new Date(value*1000).toLocaleString():'未记录';
  const thresholdText=lib=>lib.checked_expressions<10?`${lib.checked_expressions}/10，继续积累后才用于聊天`:`${lib.checked_expressions} 条已通过检查，可按语境使用`;
  function popup(title,cls=''){
    const panel=node('dialog','');panel.className='mcp-dialog mcp-body '+cls;
    const head=node('div','', 'mcp-panel-head'),close=node('button','关闭');close.type='button';close.onclick=()=>panel.close();
    head.append(node('h2',title),close);panel.append(head);panel.addEventListener('close',()=>panel.remove(),{once:true});
    document.body.append(panel);panel.showModal();return panel;
  }
  async function openLearning(group){
    const panel=popup(group.name+' · 学习记录','mcp-learning'),summary=node('p','正在加载学习记录…'),error=node('p','', 'mcp-warning'),records=node('div','');
    panel.append(node('p',`机器人 QQ ${group.account} · 群号 ${group.group}`,'mcp-muted'),summary,error,records);
    const advanced=node('details',''),advancedTitle=node('summary','高级设置与检查详情'),label=node('label',''),paused=node('input','');paused.type='checkbox';
    label.append(paused,document.createTextNode(' 暂停这个账号和群的学习（保留已有积累）'));
    const technical=node('pre','');advanced.append(advancedTitle,label,node('p','通过检查的表达默认可用。提取与自检分次调用，但模型可能记得前文，并非独立核验；不保证每条语义判断正确。','mcp-muted'),technical);panel.append(advanced);
    const budgetLabel=node('label','这个账号和群每小时最多学习阶段'),budget=node('input',''),budgetSave=node('button','保存预算');budget.type='number';budget.min='2';budget.max='100';budget.setAttribute('aria-label','每小时学习阶段预算');budgetSave.type='button';budgetLabel.append(budget);advanced.insertBefore(budgetLabel,technical);advanced.insertBefore(budgetSave,technical);
    let loading=false,changing=false;
    const query='/chat-library?'+new URLSearchParams({conversation_id:group.conversation_id});
    async function change(body){changing=true;error.textContent='';try{await api('/chat-library','POST',{conversation_id:group.conversation_id,change:body});await load(true);await refreshChats();}catch(e){error.textContent=e.message+' 可稍后重试。';}finally{changing=false;}}
    paused.onchange=()=>change({enabled:!paused.checked});
    budgetSave.onclick=()=>change({hourly_calls:Number(budget.value)});
    async function load(force=false){if(loading)return;loading=true;try{
      const data=await api(query),lib=data.library;
      summary.textContent=`表达：${thresholdText(lib)}。${lib.pending_expressions?`另有 ${lib.pending_expressions} 条旧记录保留待复核，暂不用于聊天。`:''}黑话：已掌握 ${lib.known_jargon}，待观察 ${lib.observing_jargon}。最近完成学习：${dateText(lib.last_completed_at)}。`;
      if(document.activeElement!==paused)paused.checked=!data.enabled;
      if(document.activeElement!==budget)budget.value=data.hourly_call_budget;
      technical.textContent=JSON.stringify({independence:data.independence,run:data.run,calls_last_hour:data.calls_last_hour,hourly_call_budget:data.hourly_call_budget,last_failure:data.last_failure},null,2);
      error.textContent='';
      if(!force&&records.contains(document.activeElement))return;
      records.replaceChildren();
      for(const [kind,title] of [['expressions','表达方式'],['jargon','群内用语']]){
        const section=node('section','');section.append(node('h3',title));
        if((kind==='expressions'?data.expression_count:data.jargon_count)>30)section.append(node('p','显示最近更新的 30 条记录。完整库仍保留，数量统计包含全部条目。','mcp-muted'));
        if(!(data[kind]||[]).length)section.append(node('p','还没有记录，聊天中会逐步积累。','mcp-muted'));
        for(const item of data[kind]||[]){
          const line=node('article','', 'mcp-learning-record');
          if(kind==='expressions'){
            line.append(node('h4',item.situation),node('p',item.style),node('small',!item.enabled?'已停用':item.evidence_version===2?'群友表达 · 已通过自检':'旧记录待复核 · 暂不用于聊天','mcp-muted'));
            if(item.evidence_version===2&&item.surface_form)line.append(node('p','可观察形式：'+item.surface_form,'mcp-muted'));
          }
          else line.append(node('h4',item.term),node('p',item.meaning||'还需观察用法，暂不作为确定释义'),node('small',!item.enabled?'已停用':item.manual?'人工释义':item.is_jargon?'已掌握':'待观察','mcp-muted'));
          const toggle=node('button',item.enabled?'停用此条':'启用此条');toggle.type='button';toggle.onclick=()=>change({kind,id:item.id,enabled:!item.enabled});line.append(toggle);
          const details=node('details','');details.append(node('summary','检查详情'),node('p',`有效批次命中 ${item.count} · 上下文独立性 ${item.independence}`));
          if(kind==='jargon'){
            const meaning=node('textarea',''),save=node('button','保存人工释义');meaning.value=item.meaning||'';meaning.maxLength=600;meaning.setAttribute('aria-label',item.term+' 的人工释义');save.type='button';save.onclick=()=>change({kind,id:item.id,meaning:meaning.value});details.append(meaning,save);
          }
          line.append(details);section.append(line);
        }records.append(section);
      }
    }catch(e){error.textContent=e.message+' 保留已显示记录，将自动重试。';}finally{loading=false;}}
    await load();const timer=setInterval(()=>{if(panel.open&&!document.hidden&&!changing)void load();},3000);
    panel.addEventListener('close',()=>clearInterval(timer),{once:true});
  }
  async function refreshChats(){
    if(chatRefreshing)return;chatRefreshing=true;
    try{
      const data=await api('/chat-overview'),host=$('mcp-chat-sessions');
      $('mcp-event-status').textContent=data.receiver?'实时连接：'+data.receiver.note:'';
      const opened=new Set([...host.querySelectorAll('details[open]')].map(el=>el.dataset.key)),focused=document.activeElement?.dataset.chatStop;
      const active=data.groups.reduce((n,g)=>n+g.active.length,0);$('mcp-chat-stop-all').disabled=!active;
      $('mcp-chat-summary').textContent=active?`${active} 个会话进行中 · 状态与学习记录`:data.groups.length?'暂无进行中的会话 · 学习记录已保留':'在 Agent 对话中开始持续聊天';
      if(!chatDialog.open)return;
      $('mcp-chat-status').textContent=active?`${active} 个持续聊天会话 · 潜水时也继续积累`:'暂无正在运行的群聊。开始后接续原账号、原群的积累。';host.replaceChildren();
      if(!data.groups.length)host.append(node('p','还没有持续水群记录。请在已连接的 Agent 对话中指定群聊和人物开始。','mcp-empty'));
      for(const group of data.groups){
        const card=node('article','', 'mcp-group-card');card.dataset.group=group.conversation_id;
        const head=node('div','', 'mcp-group-heading');head.append(node('h3',group.name),node('span',group.active.length?'进行中':'已停止','mcp-status-pill'));card.append(head,node('p',`机器人 QQ ${group.account} · 群号 ${group.group}`,'mcp-muted'));
        for(const row of group.active){
          const phase=row.turn_process?.phase,chatState=['REPLYING','SENDING'].includes(phase)?'准备回复':'潜水';
          const line=node('div','', 'mcp-active-run');line.append(node('p',`${chatState} · ${row.persona_name||'自定义人格'} · 会话 ${row.id.slice(0,8)}`));
          const stop=node('button','停止持续水群');stop.type='button';stop.dataset.chatStop=row.id;
          stop.onclick=()=>chatAction(async()=>{stop.disabled=true;try{await api('/chats/'+encodeURIComponent(row.id)+'/stop','POST',{});await refreshChats();}finally{stop.disabled=false;}});line.append(stop);card.append(line);
        }
        const learning=group.learning,lib=learning?.library;
        if(lib){
          const stats=node('div','', 'mcp-learning-stats');
          stats.append(node('p','学习 · '+group.learning_state),node('p','表达 · '+thresholdText(lib)),node('p',`黑话 · 已掌握 ${lib.known_jargon} · 待观察 ${lib.observing_jargon}`));card.append(stats);
          if(lib.pending_expressions)card.append(node('p',`${lib.pending_expressions} 条旧表达已保留，需重新核对群友原话，暂不参与回复。`,'mcp-muted'));
          card.append(node('p','本群最近完成学习：'+dateText(lib.last_completed_at),'mcp-muted'));
          if(group.active.length)card.append(node('p',`本次运行：已完成 ${learning.run.completed_batches} 批 · 正在积累 ${learning.run.buffered} 条新消息`,'mcp-muted'));
          else if(lib.checked_expressions||lib.known_jargon||lib.observing_jargon)card.append(node('p','重新开始会接续已有积累，不会清空记录。','mcp-muted'));
        }else card.append(node('p',group.error||'暂时无法读取学习状态，请检查连接。','mcp-warning'));
        const actions=node('div','', 'mcp-group-actions');
        const view=node('button','查看学习记录');view.type='button';view.disabled=!learning;view.onclick=()=>openLearning(group);actions.append(view);card.append(actions);
        const history=node('details','');history.dataset.key='history:'+group.conversation_id;history.open=opened.has(history.dataset.key);history.append(node('summary',`历史会话（${group.history.length}）`));
        for(const row of group.history){const entry=node('p',`${row.persona_name||'自定义人格'} · ${row.connection_name} · 已停止 · ${dateText(row.updated)}`);history.append(entry);}
        const details=node('details','');details.dataset.key='active:'+group.conversation_id;details.open=opened.has(details.dataset.key);details.append(node('summary','当前会话详情'));
        for(const row of group.active)details.append(node('p',row.connection_name+' · '+row.id),node('pre',row.persona),node('p',row.gap_note||''));
        if(group.active.length)card.append(details);if(group.history.length)card.append(history);host.append(card);
      }
      if(focused)host.querySelector('[data-chat-stop="'+CSS.escape(focused)+'"]')?.focus({preventScroll:true});
    }catch(e){$('mcp-chat-summary').textContent='群聊状态暂不可用 · 可打开管理查看';$('mcp-chat-status').textContent='暂时无法更新状态：'+e.message+'。已有记录保留，稍后自动重试。';}finally{chatRefreshing=false;}
  }

  function watchChats(){clearInterval(chatTimer);if(dialog.open||chatDialog.open)chatTimer=setInterval(()=>{if(!document.hidden)void refreshChats();},3000);}

  async function desktop(path,method='GET',body){const r=await fetch('/api/desktop/'+path,{method,cache:'no-store',headers:{'Content-Type':'application/json','X-ChatWeave-UI':'1'},body:body===undefined?undefined:JSON.stringify(body)});const data=await r.json();if(!r.ok)throw Error(data.detail||'设置操作失败。');return data;}
  $('open-mcp').onclick=()=>{if(!dialog.open)dialog.showModal();watchChats();void refreshChats();action(async()=>{await refresh();await chats();const [prefs,status]=await Promise.all([desktop('preferences'),desktop('status')]);$('mcp-background-label').hidden=!status.owned;$('mcp-background').checked=prefs.background;});};
  $('mcp-close').onclick=()=>dialog.close();
  $('mcp-operation-history').addEventListener('toggle',()=>{if($('mcp-operation-history').open)action(async()=>{const data=await api('/operations');const host=$('mcp-operations');host.replaceChildren();for(const row of data.operations){const card=node('div','', 'mcp-connection');card.append(node('strong',row.connection_name+' · '+row.state),node('pre',row.summary),node('p',row.result.note||''));host.append(card);}if(!data.operations.length)host.append(node('p','暂无操作记录。'));});});
  function clearSecret(){credential=null;for(const id of ['mcp-token','mcp-config'])$(id).value='';$('mcp-token').type='password';$('mcp-secret').hidden=true;}
  dialog.addEventListener('close',clearSecret);
  dialog.addEventListener('close',watchChats);
  chatDialog.addEventListener('close',watchChats);
  $('mcp-live-groups').onclick=()=>action(async()=>{liveGroups=(await api('/live-groups')).items;$('mcp-qq').checked=true;if(!current?.data_platforms?.wechat)$('mcp-wechat').checked=false;await chats();});
  $('mcp-chat-open').onclick=()=>{if(!chatDialog.open)chatDialog.showModal();watchChats();void refreshChats();};
  $('mcp-chat-close').onclick=()=>chatDialog.close();
  $('mcp-chat-refresh').onclick=()=>void refreshChats();
  $('mcp-chat-stop-all').onclick=()=>chatAction(async()=>{await api('/chats/all/stop','POST',{});await refreshChats();});
  function mediaPermissions(){
    const active=$('mcp-chat').checked&&$('mcp-send').checked;
    $('mcp-chat_reactions').disabled=!active;if(!active)$('mcp-chat_reactions').checked=false;
    $('mcp-chat_images').disabled=!active;if(!active)$('mcp-chat_images').checked=false;
    for(const flag of ['chat_sticker_send','chat_sticker_collect']){const input=$('mcp-'+flag);input.disabled=!active||!$('mcp-chat_images').checked;if(input.disabled)input.checked=false;}
  }
  $('mcp-chat').onchange=()=>{if($('mcp-chat').checked)$('mcp-send').checked=true;mediaPermissions();};
  $('mcp-send').onchange=()=>{if(!$('mcp-send').checked)$('mcp-chat').checked=false;mediaPermissions();};
  $('mcp-chat_images').onchange=mediaPermissions;
  $('mcp-save-service').onclick=()=>action(async()=>{
    if(busy)return;busy=true;createError='';renderService();
    try{serviceState(await api('','PUT',{enabled:$('mcp-enabled').checked,port:Number($('mcp-port').value)}));clearSecret();await refresh();}
    finally{busy=false;renderService();}
  });
  $('mcp-edit-port').onclick=()=>{$('mcp-advanced').open=true;$('mcp-port').scrollIntoView({block:'center'});$('mcp-port').focus();};
  $('mcp-open-data').onclick=()=>{dialog.close();$('open-data').click();};
  $('mcp-open-onebot').onclick=()=>{dialog.close();window.dispatchEvent(new CustomEvent('tulpa-open-settings',{detail:'onebot'}));};
  $('mcp-test-onebot').onclick=()=>action(async()=>{const b=$('mcp-test-onebot');b.disabled=true;try{const r=await desktop('onebot/test','POST',{});$('mcp-onebot-status').textContent=r.message;}finally{b.disabled=false;}});
  $('mcp-background').onchange=()=>action(()=>desktop('preferences','PUT',{background:$('mcp-background').checked}));
  $('mcp-more').onclick=()=>action(()=>chats(false));
  $('mcp-search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>action(()=>chats()),200);};
  for(const p of ['qq','wechat'])$('mcp-'+p).onchange=()=>{for(const [key,pair] of selected)if(pair[0]===p&&!$('mcp-'+p).checked)selected.delete(key);action(()=>chats());};
  $('mcp-all').onchange=()=>{$('mcp-picker').hidden=$('mcp-all').checked;};
  $('mcp-create-form').onsubmit=e=>{e.preventDefault();action(async()=>{
    if(busy)return;
    // Only the explicitly labelled start action may enable a stopped service.
    const allowStart=current&&!current.running,port=Number($('mcp-port').value);
    busy=true;createError='';renderService();
    try{const body={name:$('mcp-name').value,platforms:['qq','wechat'].filter(p=>$('mcp-'+p).checked),conversations:[...selected.values()],all_conversations:$('mcp-all').checked,start:$('mcp-start').value,end:$('mcp-end').value};
      for(const flag of ['media','prepare','voice','onebot','send','manage','chat','chat_reactions','chat_images','chat_sticker_send','chat_sticker_collect'])body[flag]=$('mcp-'+flag).checked;
      if(!body.platforms.length)throw Error('请选择允许访问的平台。');
      if(!body.all_conversations&&!body.conversations.length)throw Error('请选择会话，或明确勾选允许所选平台全部会话。');
      if(body.start&&body.end&&body.start>body.end)throw Error('截止日期不能早于开始日期。');
      const latest=await api('');serviceState(latest);
      if(body.platforms.some(p=>!latest.data_platforms?.[p]&&!(p==='qq'&&body.chat&&body.send)))throw Error('请先导入所选平台的聊天；仅 QQ 持续群聊可直接通过 OneBot 授权。');
      if(!current.running){
        if(!allowStart)throw Error('本机 MCP 服务尚未运行。请点击「开启服务并创建连接」继续。');
        if(!Number.isInteger(port)||port<1024||port>65535)throw Error('服务端口应为 1024 至 65535 的整数。');
        serviceState(await api('','PUT',{enabled:true,port}));
        if(!current.running)throw Error(current.error||'MCP 服务尚未启动，请稍后重试。');
      }
      const result=await api('/connections','POST',body);credential=result;
      $('mcp-test-result').textContent=$('mcp-install-result').textContent='';$('mcp-install-codex').disabled=false;$('mcp-copy').textContent='复制 Codex 配置';
      $('mcp-secret').hidden=false;$('mcp-url').value=current.url;$('mcp-token').value=result.token;
      $('mcp-config').value='[mcp_servers.tulpa]\nurl = '+JSON.stringify(current.url)+'\nhttp_headers = { Authorization = '+JSON.stringify('Bearer '+result.token)+' }\ntool_timeout_sec = 240\n';
      for(const [flag,tool] of [['send','send_qq_message'],['manage','manage_qq_group']])if(body[flag])$('mcp-config').value+='\n[mcp_servers.tulpa.tools.'+tool+']\napproval_mode = \"approve\"\n';
      if(body.chat)for(const tool of ['start_chat_session','get_chat_session','list_chat_sessions','wait_chat_messages','plan_chat_reply','send_chat_reply','send_chat_message','stop_chat_session','list_chat_groups','list_chat_personas','get_chat_learning','claim_chat_learning','submit_chat_learning','select_chat_expressions'])$('mcp-config').value+='\n[mcp_servers.tulpa.tools.'+tool+']\napproval_mode = "approve"\n';
      for(const [flag,names] of [['chat_reactions',['react_to_chat_message']],['chat_images',['read_chat_image','list_chat_stickers','read_chat_sticker','note_chat_sticker']],['chat_sticker_send',['send_chat_sticker']],['chat_sticker_collect',['collect_chat_sticker']]])if(body[flag])for(const tool of names)$('mcp-config').value+='\n[mcp_servers.tulpa.tools.'+tool+']\napproval_mode = "approve"\n';
      $('mcp-secret').scrollIntoView({block:'nearest'});
      // Show the one-time credential before refreshing the surrounding lists.
      try{await refresh();}catch{createError='连接已创建，下方凭据可用；连接列表暂时刷新失败。';}
    }catch(error){createError=error.message;}
    finally{busy=false;renderService();}
  });};
  $('mcp-reveal').onclick=()=>{$('mcp-token').type=$('mcp-token').type==='password'?'text':'password';};
  $('mcp-test').onclick=()=>action(async()=>{if(!credential)return;const b=$('mcp-test');b.disabled=true;$('mcp-test-result').textContent='正在验证服务、授权和工具…';try{const r=await api('/test','POST',{token:credential.token});$('mcp-test-result').textContent='检测通过 · '+r.tools+' 个可用工具 · '+(r.onebot?'OneBot 群资料可用':'当前未启用 OneBot 群资料')+'。本次未调用模型。';}catch(e){$('mcp-test-result').textContent='检测未通过。';throw e;}finally{b.disabled=false;}});
  $('mcp-install-codex').onclick=()=>action(async()=>{if(!credential)return;const b=$('mcp-install-codex');b.disabled=true;try{await api('/connections/'+encodeURIComponent(credential.id)+'/codex','POST',{token:credential.token});$('mcp-install-result').textContent='已写入本机 Codex。请在 Codex 的 MCP 设置中重新连接；若仍未出现，请重启 Codex。';}catch(e){b.disabled=false;throw e;}});
  $('mcp-copy').onclick=()=>action(async()=>{await navigator.clipboard.writeText($('mcp-config').value);$('mcp-copy').textContent='已复制';});
})();
