(() => {
  const $ = id => document.getElementById(id);
  const dialog = document.createElement('dialog');
  dialog.id = 'mcp-settings'; dialog.className = 'mcp-dialog';
  dialog.setAttribute('aria-labelledby', 'mcp-title');
  dialog.innerHTML = `<div class="dialog-head"><h2 id="mcp-title">连接外部 Agent</h2><button id="mcp-close" type="button" class="icon-button" aria-label="关闭 MCP 设置">×</button></div>
    <div class="mcp-body">
      <p>让 Codex 等支持 MCP 的 Agent 查询你允许的 QQ / 微信资料。无需配置 Tulpa 模型，不开放发送或群管理。</p>
      <div class="mcp-service"><label><input id="mcp-enabled" type="checkbox">启用本机 MCP 服务</label><label>端口 <input id="mcp-port" type="number" min="1024" max="65535" value="18777"></label><button id="mcp-save-service" type="button">保存服务设置</button></div>
      <p id="mcp-service-status" role="status"></p>
      <p class="mcp-muted">仅连接当前 Tulpa 数据目录。应用运行时可用；已返回的内容会进入外部 Agent 的上下文，并可能发给它的模型服务。</p>
      <details id="mcp-create-section" open><summary>新建授权连接</summary>
        <form id="mcp-create-form" autocomplete="off">
          <label for="mcp-name">连接名称</label><input id="mcp-name" required maxlength="60" placeholder="例如：Codex 课程资料调查">
          <fieldset><legend>允许访问</legend><label><input id="mcp-qq" type="checkbox" checked> QQ</label><label><input id="mcp-wechat" type="checkbox" checked> 微信</label>
            <label class="mcp-all"><input id="mcp-all" type="checkbox">允许所选平台全部会话（包括以后入库的会话）</label>
            <div id="mcp-picker"><label for="mcp-search">选择会话</label><input id="mcp-search" type="search" placeholder="搜索群名或联系人"><div id="mcp-chats" class="mcp-chats"></div><button id="mcp-more" type="button" hidden>更多会话</button><p id="mcp-selection" class="mcp-muted">已选择 0 个会话</p></div>
          </fieldset>
          <div class="mcp-dates"><label>开始日期<input id="mcp-start" type="date"></label><label>截止日期<input id="mcp-end" type="date"></label></div><small class="mcp-muted">留空表示不限制；截止日期包含当天。身份目录也遵守这个范围。</small>
          <fieldset><legend>按需处理权限</legend>
            <label><input id="mcp-media" type="checkbox">向外部 Agent 提供图片（最多 60 次/小时）</label>
            <label><input id="mcp-prepare" type="checkbox">下载并解析文件（最多 12 次/小时，每个 32 MiB）</label>
            <label><input id="mcp-voice" type="checkbox">本地转写语音（最多 12 次/小时）</label>
            <label><input id="mcp-onebot" type="checkbox">读取 OneBot 群公告、精华、当前成员和文件目录</label>
          </fieldset><p class="mcp-muted">普通消息查询可连续分页，没有任务消息总量上限。文件和语音处理仅生成本地缓存。OneBot 须已有有效配置；群信息和成员是查询当时的状态，不能还原历史。权限修改请撤销旧连接后新建。</p>
          <button id="mcp-create" type="submit">创建连接凭据</button>
        </form>
      </details>
      <section id="mcp-secret" hidden><h3>保存连接配置</h3><p>Token 只显示这一次。关闭后无法找回；丢失时撤销并重新创建。</p><label>本机地址<input id="mcp-url" readonly></label><label>访问 Token<input id="mcp-token" type="password" readonly autocomplete="off"></label><button id="mcp-reveal" type="button">显示 / 隐藏 Token</button><p>Codex：将下面内容加入本机 config.toml，然后重新连接 MCP。其他客户端使用上面的地址与 Bearer Token。</p><textarea id="mcp-config" readonly rows="5" aria-label="Codex MCP 连接配置"></textarea><button id="mcp-copy" type="button">复制 Codex 配置</button></section>
      <h3>已授权连接</h3><div id="mcp-connections"></div>
      <details><summary>最近访问记录</summary><p class="mcp-muted">只记录工具、耗时和读取数量，不记录查询词、正文或 Token。</p><div id="mcp-audit"></div></details>
      <p id="mcp-error" role="alert"></p>
    </div>`;
  document.body.append(dialog);
  let current, selected = new Map(), page = 0, searchVersion = 0, searchTimer;
  async function api(path, method='GET', body) {
    const response = await fetch('/api/mcp'+path, {method, cache:'no-store', headers: {'Content-Type':'application/json','X-ChatWeave-UI':'1'}, body: body===undefined ? undefined : JSON.stringify(body)});
    const data = await response.json(); if (!response.ok) throw Error(data.detail || '连接设置操作失败。'); return data;
  }
  function node(tag, value, cls) {const n=document.createElement(tag);n.textContent=value;if(cls)n.className=cls;return n;}
  async function refresh() {
    current=await api(''); $('mcp-enabled').checked=current.enabled;$('mcp-port').value=current.port;
    $('mcp-service-status').textContent=current.error || (current.running ? '运行中 · '+current.url : current.enabled ? '服务尚未启动' : '服务已关闭');
    $('mcp-connections').replaceChildren();
    for (const row of current.connections) {
      const card=node('div','', 'mcp-connection'); card.append(node('strong',row.name+(row.revoked?' · 已撤销':'')));
      const scope=row.scope;
      card.append(node('p',scope.platforms.join(' / ')+' · '+(scope.conversations.length?scope.conversations.length+' 个指定会话':'全部会话')+' · '+(scope.start||'不限起点')+' 至 '+(scope.end||'包括未来消息'), 'mcp-muted'));
      card.append(node('p','来源账号：'+Object.entries(row.accounts).map(([p,id])=>p+' '+(id||'当前导入资料')).join(' / ')+' · '+row.calls+' 次调用', 'mcp-muted'));
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
  $('open-mcp').onclick=()=>{dialog.showModal();action(async()=>{await refresh();await chats();});};
  $('mcp-close').onclick=()=>dialog.close();
  dialog.addEventListener('close',()=>{for(const id of ['mcp-token','mcp-config'])$(id).value='';$('mcp-token').type='password';$('mcp-secret').hidden=true;});
  $('mcp-save-service').onclick=()=>action(async()=>{await api('','PUT',{enabled:$('mcp-enabled').checked,port:Number($('mcp-port').value)});await refresh();});
  $('mcp-more').onclick=()=>action(()=>chats(false));
  $('mcp-search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>action(()=>chats()),200);};
  for(const p of ['qq','wechat'])$('mcp-'+p).onchange=()=>{for(const [key,pair] of selected)if(pair[0]===p&&!$('mcp-'+p).checked)selected.delete(key);action(()=>chats());};
  $('mcp-all').onchange=()=>{$('mcp-picker').hidden=$('mcp-all').checked;};
  $('mcp-create-form').onsubmit=e=>{e.preventDefault();action(async()=>{
    $('mcp-create').disabled=true;
    try{const body={name:$('mcp-name').value,platforms:['qq','wechat'].filter(p=>$('mcp-'+p).checked),conversations:[...selected.values()],all_conversations:$('mcp-all').checked,start:$('mcp-start').value,end:$('mcp-end').value};
      for(const flag of ['media','prepare','voice','onebot'])body[flag]=$('mcp-'+flag).checked;
      const result=await api('/connections','POST',body);await refresh();
      $('mcp-secret').hidden=false;$('mcp-url').value=current.url;$('mcp-token').value=result.token;
      $('mcp-config').value='[mcp_servers.tulpa]\nurl = '+JSON.stringify(current.url)+'\nhttp_headers = { Authorization = '+JSON.stringify('Bearer '+result.token)+' }\ntool_timeout_sec = 240\n';
      $('mcp-secret').scrollIntoView({block:'nearest'});
    }finally{$('mcp-create').disabled=false;}
  });};
  $('mcp-reveal').onclick=()=>{$('mcp-token').type=$('mcp-token').type==='password'?'text':'password';};
  $('mcp-copy').onclick=()=>action(async()=>{await navigator.clipboard.writeText($('mcp-config').value);$('mcp-copy').textContent='已复制';});
})();
