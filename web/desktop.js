"use strict";
(function(){
  if(document.getElementById('desktop-settings'))return;
  const isDesktop=document.documentElement.dataset.desktop==='true';
  const icon=name=>`<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const el=id=>document.getElementById(id);
  if(isDesktop){
    document.title='Tulpa';
    document.querySelector('#welcome h2').textContent='从真实交流中，慢慢懂你。';
    document.querySelector('#welcome > p').textContent='留住重要的消息，理清值得继续的事。';
    const eyebrow=document.createElement('div');eyebrow.className='desktop-eyebrow';eyebrow.textContent='TULPA / 通信工作区';
    document.querySelector('#welcome h2').before(eyebrow);
    document.querySelectorAll('.suggestions button').forEach((button,i)=>button.insertAdjacentHTML('afterbegin',icon(['chat','watch','workspace'][i])));
    const badge=document.createElement('span');badge.className='desktop-local-badge';badge.textContent='本机工作空间';
    el('header-range').before(badge);
  }
  const settings=document.createElement('button');settings.id='desktop-settings';settings.className='desktop-settings-button';
  settings.innerHTML=icon('sliders')+'<span>模型与连接</span><span id="desktop-model-status">设置</span>';
  document.querySelector('.sidebar-bottom').prepend(settings);
  const dialog=document.createElement('dialog');dialog.id='desktop-config';dialog.className='desktop-config';
  dialog.setAttribute('aria-labelledby','desktop-config-title');
  dialog.innerHTML=`<div class="dialog-head"><h2 id="desktop-config-title">模型与连接</h2><button type="button" class="icon-button" id="desktop-config-close" aria-label="关闭设置">${icon('close')}</button></div>
  <div class="desktop-config-body">
    <p class="config-intro">内置聊天和外部 Agent 共用本机资料。只连接 Codex 等外部 Agent 时，无需填写模型 API Key。</p>
    <div class="desktop-settings-nav"><button type="button" id="desktop-open-mcp">连接外部 Agent / MCP</button><button type="button" id="desktop-open-data">导入聊天资料</button></div>
    <details id="desktop-model-section"><summary>内置聊天模型</summary>
      <form id="desktop-config-form" autocomplete="off">
        <label for="desktop-api-base">API 地址</label><input id="desktop-api-base" type="url" required placeholder="例如 https://api.deepseek.com" spellcheck="false" maxlength="2048"><small>填写基础地址，无需添加 /chat/completions。</small>
        <label for="desktop-api-key">API Key</label><input id="desktop-api-key" type="password" autocomplete="new-password" maxlength="4096"><small id="desktop-key-hint"></small>
        <label for="desktop-model">模型名称</label><input id="desktop-model" required placeholder="例如 deepseek-flash" spellcheck="false" maxlength="200"><small>须与服务商提供的模型 ID 一致，接口需支持流式输出与工具调用。</small>
        <div class="config-foot"><button type="submit" class="primary-button" id="desktop-config-save">保存模型设置</button></div>
      </form>
    </details>
    <details id="desktop-onebot-section"><summary>QQ 扩展 / OneBot（可选）</summary>
      <form id="desktop-onebot-form" autocomplete="off">
        <p class="config-intro">这份配置同时用于 QQ 回复、群管理和已授权的 MCP 群资料读取。OneBot 服务须独立运行；本地聊天检索不依赖它。</p>
        <label for="desktop-sender-url">本机 OneBot 地址</label><input id="desktop-sender-url" type="url" placeholder="http://127.0.0.1:3000" maxlength="2048">
        <label for="desktop-sender-token">访问 Token</label><input id="desktop-sender-token" type="password" autocomplete="new-password" maxlength="4096"><small>只保存在本机，不提供给外部 Agent。留空保留已存 Token。</small>
        <label for="desktop-events-url">实时事件地址 · 持续群聊</label><input id="desktop-events-url" type="url" placeholder="ws://127.0.0.1:3001" maxlength="2048"><small>在 SnowLuma 开启正向 WebSocket 服务。持续群聊从这里接收新消息，无需数据库实时读取。</small>
        <label for="desktop-events-token">事件 Token</label><input id="desktop-events-token" type="password" autocomplete="new-password" maxlength="4096"><small>填写 WebSocket 节点的 Token，可能与 HTTP 不同。留空保留；尚未设置时使用 HTTP Token。</small>
        <div class="config-foot"><button type="button" id="desktop-onebot-test">检测已保存的连接</button><button type="submit" class="primary-button" id="desktop-onebot-save">保存并检测</button></div>
        <p id="desktop-onebot-status" role="status"></p>
      </form>
    </details>
    <label class="desktop-check" id="desktop-background-label"><input id="desktop-background" type="checkbox">关闭窗口后留在托盘，继续提供 MCP 和实时读取</label>
    <small id="desktop-tray-hint">托盘菜单可以打开窗口、设置开机启动或彻底退出。退出会停止本机服务。</small>
    <p class="desktop-storage" id="desktop-storage"></p><p><a href="/api/desktop/diagnostics" download="Tulpa-diagnostics.json">导出运行诊断</a><small>包含系统与客户端版本、权限和错误类别，不包含聊天或密钥。</small></p>
    <p id="desktop-config-error" class="config-error" role="status"></p>
  </div>`;
  document.body.append(dialog);
  async function api(path,method='GET',body){
    const r=await fetch('/api/desktop/'+path,{method,cache:'no-store',headers:{'Content-Type':'application/json','X-ChatWeave-UI':'1'},body:body===undefined?undefined:JSON.stringify(body)});
    const data=await r.json();if(!r.ok)throw Error(data.detail||'设置操作失败。');return data;
  }
  async function load(){
    const [loaded,prefs]=await Promise.all([api('status'),api('preferences')]);const s=loaded.settings;
    el('desktop-api-base').value=s.api_base;el('desktop-model').value=s.model;el('desktop-api-key').value='';el('desktop-api-key').required=!s.has_api_key;
    el('desktop-api-key').placeholder=s.has_api_key?'已保存 · 留空保留现有密钥':'输入你的 API Key';
    el('desktop-key-hint').textContent=s.has_api_key?'密钥已保存在本机，不会回显。':'密钥不会随发布包分发。';
    el('desktop-sender-url').value=s.sender_url;el('desktop-sender-token').value='';el('desktop-sender-token').placeholder=s.has_sender_token?'已保存 · 留空保留':'访问 Token（如接口需要）';
    el('desktop-events-url').value=s.events_url||'';el('desktop-events-token').value='';el('desktop-events-token').placeholder=s.has_events_token?'已保存 · 留空保留':'WebSocket 节点 Token';
    el('desktop-model-status').textContent=s.configured?'已配置':prefs.mode==='mcp'?'外部 Agent':'设置';
    el('model-label').textContent=s.configured?s.model:'未配置模型';
    el('desktop-background').checked=prefs.background;
    el('desktop-background-label').hidden=el('desktop-tray-hint').hidden=!loaded.owned;
    el('desktop-storage').textContent=`Tulpa ${loaded.version} · 数据保存在 ${loaded.storage}`;
    return {s,prefs};
  }
  async function open(section){
    el('desktop-config-error').textContent='';
    try{await load();}catch(e){el('desktop-config-error').textContent=e.message;}
    if(section)el('desktop-'+section+'-section').open=true;
    if(!dialog.open)dialog.showModal();
    if(section)el('desktop-'+section+'-section').scrollIntoView({block:'nearest'});
  }
  settings.onclick=()=>open();
  window.addEventListener('tulpa-open-settings',e=>open(e.detail));
  el('desktop-config-close').onclick=()=>dialog.close();
  el('desktop-open-mcp').onclick=()=>{dialog.close();el('open-mcp').click();};
  el('desktop-open-data').onclick=()=>{dialog.close();el('open-data').click();};
  async function testOneBot(){const result=await api('onebot/test','POST',{});el('desktop-onebot-status').textContent=result.message;}
  async function action(button,fn){button.disabled=true;el('desktop-config-error').textContent='';try{await fn();}catch(e){el('desktop-config-error').textContent=e.message;}finally{button.disabled=false;}}
  el('desktop-config-form').onsubmit=event=>{
    event.preventDefault();action(el('desktop-config-save'),async()=>{
      await api('settings','PUT',{api_base:el('desktop-api-base').value,api_key:el('desktop-api-key').value,model:el('desktop-model').value});
      await load();el('desktop-config-error').textContent='模型设置已保存，对下一次提问生效。';
    });
  };
  el('desktop-onebot-form').onsubmit=event=>{
    event.preventDefault();action(el('desktop-onebot-save'),async()=>{
      await api('settings','PUT',{sender_url:el('desktop-sender-url').value,sender_token:el('desktop-sender-token').value,events_url:el('desktop-events-url').value,events_token:el('desktop-events-token').value});
      await load();await testOneBot();
    });
  };
  el('desktop-onebot-test').onclick=()=>action(el('desktop-onebot-test'),testOneBot);
  el('desktop-background').onchange=()=>action(el('desktop-background'),async()=>{await api('preferences','PUT',{background:el('desktop-background').checked});});
  dialog.addEventListener('close',()=>{el('desktop-api-key').value='';el('desktop-sender-token').value='';el('desktop-events-token').value='';});
  const welcome=document.createElement('dialog');welcome.id='desktop-onboarding';welcome.className='desktop-config';
  welcome.innerHTML=`<div class="dialog-head"><h2>从哪里开始？</h2><button type="button" class="icon-button" id="desktop-onboarding-close" aria-label="关闭使用向导">${icon('close')}</button></div>
    <div class="desktop-config-body"><p class="config-intro">资料留在本机。选择一种方式开始，稍后也可以同时使用。</p>
      <button class="desktop-mode" id="desktop-mode-mcp"><strong>连接外部 Agent</strong><span>在 Codex 等客户端使用 QQ / 微信资料，无需给 Tulpa 配置模型。</span></button>
      <button class="desktop-mode" id="desktop-mode-chat"><strong>使用内置聊天</strong><span>配置自己的模型，在 Tulpa 中调查、整理和起草回复。</span></button>
      <p id="desktop-onboarding-error" role="alert"></p>
    </div>`;
  document.body.append(welcome);
  el('desktop-onboarding-close').onclick=()=>welcome.close();
  for(const mode of ['mcp','chat'])el('desktop-mode-'+mode).onclick=async()=>{
    try{await api('preferences','PUT',{mode,background:mode==='mcp'});welcome.close();
      if(mode==='mcp')el('open-mcp').click();else await open('model');
    }catch(e){el('desktop-onboarding-error').textContent=e.message;}
  };
  load().then(async({s,prefs})=>{
    if(isDesktop&&!s.configured&&!prefs.mode&&!sessionStorage.getItem('tulpa.setup-seen')){
      const r=await fetch('/api/mcp',{cache:'no-store'});if(!r.ok)return;const mcp=await r.json();
      if(!mcp.enabled){sessionStorage.setItem('tulpa.setup-seen','1');welcome.showModal();}
    }
  }).catch(()=>{el('desktop-model-status').textContent='设置不可用';});
})();
