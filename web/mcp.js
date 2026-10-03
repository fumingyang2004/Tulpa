(() => {
  const $ = id => document.getElementById(id);
  const dialog = document.createElement('dialog');
  dialog.id = 'mcp-settings'; dialog.className = 'mcp-dialog';
  dialog.setAttribute('aria-labelledby', 'mcp-title');
  dialog.innerHTML = `<div class="dialog-head"><h2 id="mcp-title">连接外部 Agent</h2><button id="mcp-close" type="button" class="icon-button" aria-label="关闭 MCP 设置">×</button></div>
    <div class="mcp-body">
      <p>让 Codex、DeepSeek Harness 等 MCP 客户端使用你授权的资料与 QQ 操作。无需配置 Tulpa 模型，发送和管理默认关闭；勾选授权后可直接执行，无需逐次审批。</p>
      <section class="mcp-step"><h3>1. 准备资料</h3><p id="mcp-data-status" role="status"></p><button id="mcp-open-data" type="button">导入 / 管理聊天</button>
      <p id="mcp-onebot-status" class="mcp-muted"></p><button id="mcp-open-onebot" type="button">配置 OneBot</button> <button id="mcp-test-onebot" type="button">检测 OneBot</button></section>
      <p id="mcp-service-status" role="status"></p>
      <label class="mcp-background" id="mcp-background-label" hidden><input id="mcp-background" type="checkbox">关闭窗口后留在托盘，继续提供服务</label>
      <details id="mcp-create-section" open><summary>2. 选择范围并创建连接</summary>
        <p class="mcp-muted">仅授权访问当前 Tulpa 的本地资料。创建连接时可一并开启服务；外部 Agent 读到的内容可能发给它的模型服务。</p>
        <form id="mcp-create-form" autocomplete="off">
          <label for="mcp-name">连接名称</label><input id="mcp-name" required maxlength="60" placeholder="例如：Codex 课程资料调查">
          <fieldset><legend>允许访问</legend><label><input id="mcp-qq" type="checkbox" checked> QQ</label><label><input id="mcp-wechat" type="checkbox" checked> 微信</label>
            <label class="mcp-all"><input id="mcp-all" type="checkbox">允许所选平台全部会话（包括以后入库的会话）</label>
            <div id="mcp-picker"><label for="mcp-search">选择会话</label><input id="mcp-search" type="search" placeholder="搜索群名或联系人"><div id="mcp-chats" class="mcp-chats"></div><button id="mcp-more" type="button" hidden>更多会话</button><p id="mcp-selection" class="mcp-muted">已选择 0 个会话</p></div>
          </fieldset>
          <div class="mcp-dates"><label>开始日期<input id="mcp-start" type="date"></label><label>截止日期<input id="mcp-end" type="date"></label></div><small class="mcp-muted">留空表示不限制；截止日期包含当天。身份目录也遵守这个范围。</small>
          <fieldset><legend>按需处理权限</legend>
            <label><input id="mcp-media" type="checkbox">向外部 Agent 提供图片（最多 60 次/小时）</label>
            <label><input id="mcp-prepare" type="checkbox">下载原文件 / 按需提取文字（合计最多 12 次/小时，每个 32 MiB）</label>
            <label><input id="mcp-voice" type="checkbox">本地转写语音（最多 12 次/小时）</label>
            <label><input id="mcp-onebot" type="checkbox">读取 OneBot 群公告、精华、当前成员和文件目录</label>
            <label><input id="mcp-send" type="checkbox">允许直接发送 QQ 消息（无需逐次审批）</label>
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
  document.body.append(dialog);
  let current, credential=null, busy=false, createError='', platformsInitialized=false, selected = new Map(), page = 0, searchVersion = 0, searchTimer;
  async function api(path, method='GET', body) {
    const response = await fetch('/api/mcp'+path, {method, cache:'no-store', headers: {'Content-Type':'application/json','X-ChatWeave-UI':'1'}, body: body===undefined ? undefined : JSON.stringify(body)});
    const data = await response.json(); if (!response.ok) throw Error(data.detail || '连接设置操作失败。'); return data;
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
    $('mcp-data-status').textContent=Object.keys(counts).length?Object.entries(counts).map(([p,n])=>(p==='qq'?'QQ':'微信')+' '+n.toLocaleString()+' 条本地消息').join(' · '):'先导入聊天资料，再创建授权连接。无需等待全部历史导入完成。';
    $('mcp-onebot-status').textContent=current.onebot_configured?'已保存 OneBot 配置；点击检测确认服务在线。':'OneBot 未配置，本地聊天查询仍可使用。公告、精华及群文件可稍后接入。';
    $('mcp-connections').replaceChildren();
    for (const row of current.connections) {
      const card=node('div','', 'mcp-connection'); card.append(node('strong',row.name+(row.revoked?' · 已撤销':'')));
      const scope=row.scope;
      card.append(node('p',scope.platforms.join(' / ')+' · '+(scope.conversations.length?scope.conversations.length+' 个指定会话':'全部会话')+' · '+(scope.start||'不限起点')+' 至 '+(scope.end||'包括未来消息'), 'mcp-muted'));
      card.append(node('p','操作权限：'+[scope.send?'QQ 直接发送':'',scope.manage?'群管理直接操作':''].filter(Boolean).join('、')+(scope.send||scope.manage?' · 持续授权，可撤销':'仅资料读取'),'mcp-muted'));
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
    const data=await api('/conversations?'+new URLSearchParams({query:$('mcp-search').value,offset:page}));if(version!==searchVersion)return;
    for(const item of data.items){const pair=[item.platform,item.conversation_id],key=JSON.stringify(pair);const label=node('label','');
      const input=document.createElement('input');input.type='checkbox';input.checked=selected.has(key);
      input.disabled=!$(item.platform==='qq'?'mcp-qq':'mcp-wechat').checked;
      input.onchange=()=>{if(input.checked)selected.set(key,pair);else selected.delete(key);selection();};
      label.append(input,node('span',item.platform+' · '+item.name+' ('+item.count+')'));$('mcp-chats').append(label);}
    if(reset&&!data.items.length)$('mcp-chats').append(node('p','没有匹配的已导入会话。','mcp-muted'));
    page=data.next_offset;$('mcp-more').hidden=!data.has_more;selection();
  }
  async function action(fn){$('mcp-error').textContent='';try{await fn();}catch(e){$('mcp-error').textContent=e.message;}}
  async function desktop(path,method='GET',body){const r=await fetch('/api/desktop/'+path,{method,cache:'no-store',headers:{'Content-Type':'application/json','X-ChatWeave-UI':'1'},body:body===undefined?undefined:JSON.stringify(body)});const data=await r.json();if(!r.ok)throw Error(data.detail||'设置操作失败。');return data;}
  $('open-mcp').onclick=()=>{if(!dialog.open)dialog.showModal();action(async()=>{await refresh();await chats();const [prefs,status]=await Promise.all([desktop('preferences'),desktop('status')]);$('mcp-background-label').hidden=!status.owned;$('mcp-background').checked=prefs.background;});};
  $('mcp-close').onclick=()=>dialog.close();
  $('mcp-operation-history').addEventListener('toggle',()=>{if($('mcp-operation-history').open)action(async()=>{const data=await api('/operations');const host=$('mcp-operations');host.replaceChildren();for(const row of data.operations){const card=node('div','', 'mcp-connection');card.append(node('strong',row.connection_name+' · '+row.state),node('pre',row.summary),node('p',row.result.note||''));host.append(card);}if(!data.operations.length)host.append(node('p','暂无操作记录。'));});});
  function clearSecret(){credential=null;for(const id of ['mcp-token','mcp-config'])$(id).value='';$('mcp-token').type='password';$('mcp-secret').hidden=true;}
  dialog.addEventListener('close',clearSecret);
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
      for(const flag of ['media','prepare','voice','onebot','send','manage'])body[flag]=$('mcp-'+flag).checked;
      if(!body.platforms.length)throw Error('请选择允许访问的平台。');
      if(!body.all_conversations&&!body.conversations.length)throw Error('请选择会话，或明确勾选允许所选平台全部会话。');
      if(body.start&&body.end&&body.start>body.end)throw Error('截止日期不能早于开始日期。');
      const latest=await api('');serviceState(latest);
      if(body.platforms.some(p=>!latest.data_platforms?.[p]))throw Error('请先导入所选平台的聊天，再创建连接。');
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
