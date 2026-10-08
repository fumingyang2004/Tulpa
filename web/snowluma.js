'use strict';
(function(){
  async function api(action,body){
    const response=await fetch('/api/desktop/snowluma/'+action,{method:'POST',cache:'no-store',
      headers:{'Content-Type':'application/json','X-ChatWeave-UI':'1'},body:JSON.stringify(body),signal:AbortSignal.timeout(30000)});
    const data=await response.json();if(!response.ok)throw Error(data.detail||'连接设置未完成。');return data;
  }
  function mount(host,{prefix,onSaved}){
    const id=name=>'snow-'+prefix+'-'+name;
    host.classList.add('snowluma-setup');
    host.innerHTML=`<h3>从 SnowLuma 导入连接</h3>
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
      <p id="${id('status')}" role="status"></p>`;
    const el=name=>document.getElementById(id(name));
    let preview=null,busy=false,generation=0,pendingPicker=null;
    const nativePicker=Boolean(window.TULPA_FOLDER_PICKER&&window.chrome?.webview);
    el('browse').hidden=!nativePicker;
    el('picker-note').textContent=nativePicker?'选择文件夹不会启动或修改 SnowLuma。':'网页中请复制并粘贴文件夹路径；桌面新版支持直接选择文件夹。';
    function validity(){el('apply').disabled=busy||!preview||!el('account').value||!el('http').value||el('ws').value==='__choose__';}
    function lock(value){busy=value;for(const control of host.querySelectorAll('input,select,button'))control.disabled=value;validity();}
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
    async function inspect(){
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
    }
    el('inspect').onclick=inspect;
    el('apply').onclick=async()=>{
      if(busy||el('apply').disabled)return;const run=generation;
      const body={selection_id:preview.selection_id,account:el('account').value,http_id:el('http').value,ws_id:el('ws').value};
      lock(true);el('status').textContent='正在核对 HTTP 登录账号及 WS 事件连接，通过后保存…';
      try{const data=await api('import',body);if(run!==generation)return;el('status').textContent=data.message;
        if(data.saved)await onSaved(data.settings);
      }catch(e){if(run===generation)el('status').textContent='未取得保存结果，请重新打开设置检测当前连接，再决定是否重试。';}
      finally{if(run===generation)lock(false);}
    };
    el('browse').onclick=()=>{if(busy)return;pendingPicker='snow-'+Date.now()+'-'+Math.random().toString(16).slice(2);window.chrome.webview.postMessage('tulpa-pick-snowluma-folder:'+pendingPicker);};
    if(nativePicker)window.chrome.webview.addEventListener('message',event=>{
      const result=event.data;if(result?.kind!=='snowluma-folder'||result.requestId!==pendingPicker)return;
      pendingPicker=null;if(result.path){reset();el('folder').value=result.path;void inspect();}
    });
    return {reset};
  }
  window.TulpaSnowLuma={mount};
})();
