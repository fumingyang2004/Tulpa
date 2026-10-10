'use strict';
(function(){
  function mountManaged(host,{prefix,onSaved=async()=>{},onExisting=async()=>({message:'请展开已有连接并导入。'})}={}){
    const id=name=>'snow-managed-'+prefix+'-'+name;
    const el=name=>document.getElementById(id(name));
    host.classList.add('snow-managed');
    host.innerHTML=`<h3>连接 QQ</h3><p>先自行下载并解压 <a href="https://github.com/SnowLuma/SnowLuma/releases/tag/v1.14.22" target="_blank" rel="noopener noreferrer">SnowLuma Windows x64 完整版</a>，再载入文件夹。同意后自动配置，无需填写端口、Token 或管理密码。</p>
      <label for="${id('folder')}">SnowLuma 文件夹</label><div class="snow-folder"><input id="${id('folder')}" type="text" maxlength="2048" placeholder="包含 index.mjs 和 node.exe 的文件夹" spellcheck="false"><button id="${id('browse')}" type="button">选择文件夹</button></div>
      <button id="${id('inspect')}" type="button">载入文件夹</button><small id="${id('picker-note')}"></small>
      <p id="${id('status')}" role="status" aria-live="polite">正在检查托管状态…</p>
      <p id="${id('notice')}" class="mcp-warning"></p>
      <label class="desktop-check"><input id="${id('consent')}" type="checkbox">同意 SnowLuma 使用细则，并自动配置和连接 QQ</label>
      <details><summary>协议、自动配置范围及凭据保护</summary><div id="${id('agreements')}"></div>
        <p>仅使用你选定的本地文件，不下载、安装或复制 SnowLuma。新目录先备份配置，再自动初始化随机密码及独立 HTTP / WS Token，仅使用本机端口。只有一个 QQ 进程时自动连接；有多个时由你选择。不会为 Agent 增加发送、管理或看图权限。</p>
        <p>已有独立配置将检测并导入，保留原密码和节点；不会接管或关闭它。新连接撤销后会停止 Tulpa 自己启动的服务，保留 QQ 客户端、聊天、人格和学习记录。SnowLuma 的使用仍须遵守官方协议。</p>
        <p id="${id('security')}"></p><p id="${id('consent-at')}"></p></details>
      <div class="snow-managed-actions"><button id="${id('start')}" type="button" disabled>同意并自动配置</button><button id="${id('retry')}" type="button" hidden>重试</button><button id="${id('cancel')}" type="button" hidden>取消本次接入</button><button id="${id('revoke')}" type="button" hidden>撤销自动配置授权</button></div>
      <div id="${id('selection')}" hidden><label for="${id('qq')}">选择要连接的 QQ 进程</label><select id="${id('qq')}"></select><button id="${id('select')}" type="button">连接所选 QQ</button><small>账号待验证表示尚不能确认登录 QQ 号；连接后仍会核对，绝不按昵称猜测。</small></div>
      <details><summary>高级诊断与恢复</summary><p id="${id('diagnostics')}"></p><p>出错后可以重试。旧服务与配置不会被清理；恢复整个 Tulpa 备份前请先退出程序。若要使用已有外部服务，先撤销托管，再展开下方“已有 SnowLuma”。</p></details>`;
    let current=null,busy=false,timer=null,lastReady=false,dead=false,preview=null,notice='',pendingPicker=null;
    const nativePicker=Boolean(window.TULPA_FOLDER_PICKER&&window.chrome?.webview);
    el('browse').hidden=!nativePicker;
    el('picker-note').textContent=nativePicker?'选择文件夹只检查文件；勾选并确认后才配置和启动。':'请粘贴已解压的文件夹路径，点击“载入文件夹”。';
    async function fetchState(action,body){
      const response=await fetch('/api/desktop/snowluma/managed'+(action?'/'+action:''),{method:action?'POST':'GET',cache:'no-store',
        headers:{'Content-Type':'application/json','X-ChatWeave-UI':'1'},body:action?JSON.stringify(body):undefined,signal:AbortSignal.timeout(15000)});
      const value=await response.json();if(!response.ok)throw Error(value.detail||'托管状态读取失败。');return value;
    }
    function controls(){
      el('start').disabled=busy||!current?.available||!preview||!el('consent').checked||Boolean(current?.enabled);
      el('start').textContent=current?.enabled?'自动配置已启动':!preview?'请先载入文件夹':!el('consent').checked?'勾选同意后自动配置':preview.mode==='existing'?'同意并连接已有服务':'同意并自动配置';
      el('consent').disabled=busy||!current?.available||!preview||Boolean(current?.enabled);
      el('folder').disabled=el('browse').disabled=el('inspect').disabled=busy||Boolean(current?.enabled);
      el('retry').disabled=el('cancel').disabled=el('revoke').disabled=el('select').disabled=busy;
    }
    function render(s){
      if(current&&current.consent_fingerprint!==s.consent_fingerprint){el('consent').checked=false;preview=null;notice='';}
      current=s;
      if(s.folder&&!el('folder').value)el('folder').value=s.folder;
      el('status').textContent=notice&&!s.enabled?notice:(s.phase_label||'状态未知')+(s.account?' · QQ '+s.account:'')+(s.message?'。'+s.message:'');
      el('notice').textContent=s.authorization_note||'';
      el('security').textContent=s.security_note||'';
      el('consent-at').textContent=s.consent_at?'本机记录的授权时间：'+new Date(s.consent_at*1000).toLocaleString()+'；'+(s.consent_current?'适用于当前范围。':'内容或范围已变化，需重新同意。'):'尚未授予自动配置权限。';
      el('agreements').replaceChildren();
      for(const d of s.agreements||[]){const p=document.createElement('p'),a=document.createElement('a');
        const url=new URL(d.url);if(url.origin!=='https://github.com'||!url.pathname.startsWith('/SnowLuma/SnowLuma/'))continue;
        a.href=d.url;a.target='_blank';a.rel='noopener noreferrer';a.textContent=d.title+' · '+(d.version||s.version);p.append(a,document.createTextNode(' · SHA256 '+d.sha256));el('agreements').append(p);}
      el('retry').hidden=!s.available||!['error','cancelled','stopped'].includes(s.phase)||!s.consent_current;
      el('cancel').hidden=!s.enabled||['ready','error','revoked','stopped'].includes(s.phase);
      el('revoke').hidden=!(s.has_consent??s.consent_current);
      el('selection').hidden=s.phase!=='choosing'||!s.selection_required;
      const selected=el('qq').value;el('qq').replaceChildren(new Option('请选择 QQ 进程',''));
      for(const row of s.choices||[])el('qq').add(new Option((row.account_label||'账号待验证')+' · PID '+row.pid+' · '+row.path,row.id));
      if([...el('qq').options].some(o=>o.value===selected))el('qq').value=selected;
      el('diagnostics').textContent=['自动初始化支持 SnowLuma '+s.version,s.code?'状态码：'+s.code:'',s.folder?'运行目录：'+s.folder:'',s.storage?'本机凭据与进度：'+s.storage:'',s.service_pid?'服务 PID：'+s.service_pid:'',s.qq_pid?'QQ PID：'+s.qq_pid:''].filter(Boolean).join('\n');
      controls();
    }
    async function refresh(){
      if(busy||dead)return;
      try{const s=await fetchState();if(dead)return;render(s);if(s.qq_ready&&!lastReady)await onSaved();lastReady=Boolean(s.qq_ready);}
      catch{if(!dead)el('status').textContent='无法读取本机托管状态，请确认 Tulpa 服务仍在运行；恢复连接后会自动刷新。';}
    }
    async function action(name,body={}){
      if(busy)return;busy=true;controls();
      try{const s=await fetchState(name,body);if(s.ok===false){notice=s.message;el('status').textContent=notice;return;}notice='';render(s);}
      catch{el('status').textContent='没有取得操作回执，请等待状态刷新后再重试。';}
      finally{busy=false;controls();}
    }
    el('consent').onchange=controls;
    function invalidate(){preview=null;notice='';el('consent').checked=false;controls();}
    el('folder').oninput=invalidate;
    async function inspect(){
      if(busy)return;invalidate();busy=true;controls();el('status').textContent='正在检查所选文件夹…';
      try{const s=await fetchState('inspect',{folder:el('folder').value});if(s.ok===false){notice=s.message;}
        else{preview=s;el('folder').value=s.folder;notice='SnowLuma '+s.version+' · '+s.message;}el('status').textContent=notice;}
      catch{notice='无法载入文件夹，请检查本机路径后重试。';el('status').textContent=notice;}
      finally{busy=false;controls();}
    }
    el('inspect').onclick=inspect;
    el('browse').onclick=()=>{if(busy)return;pendingPicker='snow-local-'+Date.now()+'-'+Math.random().toString(16).slice(2);window.chrome.webview.postMessage('tulpa-pick-snowluma-folder:'+pendingPicker);};
    if(nativePicker)window.chrome.webview.addEventListener('message',event=>{
      const result=event.data;if(result?.kind!=='snowluma-folder'||result.requestId!==pendingPicker)return;
      pendingPicker=null;if(result.path){el('folder').value=result.path;void inspect();}
    });
    el('start').onclick=async()=>{
      if(busy||!preview||!el('consent').checked)return;
      if(preview.mode==='existing'){
        busy=true;controls();notice='正在检测并导入已有连接…';el('status').textContent=notice;
        try{const result=await onExisting(preview.folder);notice=result?.message||'请选择下方账号与节点后导入。';}
        catch{notice='已有连接未能导入，请查看下方提示，原配置保持不变。';}
        finally{busy=false;el('status').textContent=notice;controls();}
      }else await action('start',{accepted:true,fingerprint:preview.consent_fingerprint,selection_id:preview.selection_id});
    };
    for(const name of ['retry','cancel','revoke'])el(name).onclick=()=>action(name);
    el('select').onclick=()=>{if(el('qq').value)void action('select',{choice_id:el('qq').value});};
    void refresh();timer=setInterval(()=>{if(host.isConnected&&!document.hidden&&host.getClientRects().length)void refresh();},2000);
    return {refresh,destroy(){dead=true;clearInterval(timer);}};
  }
  async function api(action,body){
    const response=await fetch('/api/desktop/snowluma/'+action,{method:'POST',cache:'no-store',
      headers:{'Content-Type':'application/json','X-ChatWeave-UI':'1'},body:JSON.stringify(body),signal:AbortSignal.timeout(30000)});
    const data=await response.json();if(!response.ok)throw Error(data.detail||'连接设置未完成。');return data;
  }
  function mount(host,{prefix,onSaved}){
    const id=name=>'snow-'+prefix+'-'+name;
    host.classList.add('snowluma-setup');
    host.innerHTML=`<div id="snow-managed-host-${prefix}"></div><details id="snow-existing-${prefix}"><summary>已有 SnowLuma · 导入外部连接</summary><h3>从 SnowLuma 导入连接</h3>
      <p class="config-intro">先手动下载、解压并启动 SnowLuma，完成它的首次设置、登录并加载 QQ。选择现有文件夹后，可直接导入该账号的连接。</p>
      <label for="${id('folder')}">SnowLuma 文件夹</label>
      <div class="snow-folder"><input id="${id('folder')}" type="text" maxlength="2048" placeholder="粘贴包含 launcher.bat 和 config 的文件夹路径" spellcheck="false"><button id="${id('browse')}" type="button">选择文件夹</button></div>
      <small id="${id('picker-note')}"></small>
      <button id="${id('inspect')}" type="button">识别账号与节点</button>
      <div id="${id('selection')}" hidden>
        <label for="${id('account')}">QQ 账号</label><select id="${id('account')}"></select>
        <label for="${id('http')}">HTTP 服务节点</label><select id="${id('http')}"></select>
        <label for="${id('ws')}">实时事件节点</label><select id="${id('ws')}"></select>
        <p id="${id('preview')}" class="snow-preview"></p>
        <small>只在点击下方按钮后更新 Tulpa 的连接。Token 直接从所选节点读取，不会显示；SnowLuma、其他客户端和现有授权不会被修改。</small>
        <button id="${id('apply')}" type="button" class="primary-button">导入、检测并保存到 Tulpa</button>
      </div>
      <p id="${id('status')}" role="status"></p></details>`;
    const managed=mountManaged(document.getElementById('snow-managed-host-'+prefix),{prefix,onSaved:async()=>{
      const response=await fetch('/api/desktop/status',{cache:'no-store'});const data=await response.json();await onSaved(data.settings);
    },onExisting:async folder=>{document.getElementById('snow-existing-'+prefix).open=true;el('folder').value=folder;return inspect({auto:true});}});
    const el=name=>document.getElementById(id(name));
    let preview=null,busy=false,generation=0,pendingPicker=null;
    const nativePicker=Boolean(window.TULPA_FOLDER_PICKER&&window.chrome?.webview);
    el('browse').hidden=!nativePicker;
    el('picker-note').textContent=nativePicker?'选择文件夹不会启动或修改 SnowLuma。':'网页中请复制并粘贴文件夹路径；桌面新版支持直接选择文件夹。';
    function validity(){el('apply').disabled=busy||!preview||!el('account').value||!el('http').value||el('ws').value==='__choose__';}
    function lock(value){busy=value;for(const control of document.getElementById('snow-existing-'+prefix).querySelectorAll('input,select,button'))control.disabled=value;validity();}
    function reset(){generation++;preview=null;pendingPicker=null;el('selection').hidden=true;el('status').textContent='';lock(false);}
    function nodeOptions(kind){
      const account=preview.accounts.find(a=>a.account===el('account').value);
      const select=el(kind),nodes=account?.nodes[kind]||[];
      select.replaceChildren(new Option(kind==='http'?'请选择 HTTP 节点':'请选择事件节点，或明确选择仅 HTTP',kind==='http'?'':'__choose__'));
      if(kind==='ws')select.add(new Option('仅 HTTP（关闭 Tulpa 中的事件连接）',''));
      for(const node of nodes){const option=new Option(node.name+' · '+(node.url||node.reason)+(node.usable?'':' · 不可用'),node.id);option.disabled=!node.usable;select.add(option);}
      const usable=nodes.filter(n=>n.usable);
      if(usable.length===1)select.value=usable[0].id;
      if(kind==='ws'&&!usable.length)select.value='';
    }
    function summary(){
      const account=preview?.accounts.find(a=>a.account===el('account').value);
      if(!account){el('preview').textContent='检测到多个账号，请明确选择实际要使用的 QQ 账号。';validity();return;}
      const http=account.nodes.http.find(n=>n.id===el('http').value),ws=account.nodes.ws.find(n=>n.id===el('ws').value);
      el('preview').textContent=[account.message,http?'HTTP：'+http.url:'请选择可用 HTTP 节点。',ws?'事件：'+ws.url:
        el('ws').value===''?'仅 HTTP：本次保存将关闭 Tulpa 原有事件连接，持续群聊暂不可用。':'请选择事件节点。'].join('\n');
      validity();
    }
    function changedAccount(){nodeOptions('http');nodeOptions('ws');summary();}
    el('account').onchange=changedAccount;el('http').onchange=el('ws').onchange=summary;
    el('folder').oninput=()=>{reset();};
    async function inspect({auto=false}={}){
      if(busy)return;const run=++generation;preview=null;el('selection').hidden=true;lock(true);el('status').textContent='正在读取所选文件夹中的账号配置…';
      try{
        const data=await api('inspect',{folder:el('folder').value});if(run!==generation)return;
        el('status').textContent=data.message+(data.ok?'':' 可以展开下方高级设置手动连接。');
        if(data.ok){preview=data;el('folder').value=data.folder;el('account').replaceChildren(new Option('请选择 QQ 账号',''));
          for(const account of data.accounts)el('account').add(new Option(account.account+(account.code==='ok'?'':' · '+account.message),account.account));
          if(data.accounts.length===1)el('account').value=data.accounts[0].account;
          el('selection').hidden=false;changedAccount();}
      }catch(e){if(run===generation)el('status').textContent='识别未完成，请检查路径后重试，也可使用下方手动配置。';}
      finally{if(run===generation)lock(false);}
      if(auto&&preview&&preview.accounts.length===1){
        const nodes=preview.accounts[0].nodes;
        if(nodes.http.filter(n=>n.usable).length===1&&nodes.ws.filter(n=>n.usable).length===1)return importSelected();
      }
      return {message:preview?'检测到多个选择或缺少事件节点，请在下方明确选择账号与节点。':el('status').textContent};
    }
    el('inspect').onclick=()=>inspect();
    async function importSelected(){
      if(busy||el('apply').disabled)return;const run=generation;
      const body={selection_id:preview.selection_id,account:el('account').value,http_id:el('http').value,ws_id:el('ws').value};
      lock(true);el('status').textContent='正在核对 HTTP 登录账号及 WS 事件连接，通过后保存…';
      try{const data=await api('import',body);if(run!==generation)return;el('status').textContent=data.message;
        if(data.saved)await onSaved(data.settings);return data;
      }catch(e){if(run===generation)el('status').textContent='未取得保存结果，请重新打开设置检测当前连接，再决定是否重试。';}
      finally{if(run===generation)lock(false);}
    }
    el('apply').onclick=importSelected;
    el('browse').onclick=()=>{if(busy)return;pendingPicker='snow-'+Date.now()+'-'+Math.random().toString(16).slice(2);window.chrome.webview.postMessage('tulpa-pick-snowluma-folder:'+pendingPicker);};
    if(nativePicker)window.chrome.webview.addEventListener('message',event=>{
      const result=event.data;if(result?.kind!=='snowluma-folder'||result.requestId!==pendingPicker)return;
      pendingPicker=null;if(result.path){reset();el('folder').value=result.path;void inspect();}
    });
    return {reset(){reset();void managed.refresh();}};
  }
  window.TulpaSnowLuma={mount,mountManaged};
})();
