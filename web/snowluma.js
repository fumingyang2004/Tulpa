'use strict';
(function(){
  // Both settings entrances share one in-page operation. Credentials and durable
  // authorization stay on the server; browser drafts do not grant MCP access.
  const views=new Set(),draft={folder:'',accepted:false,busy:false,notice:'',refreshError:'',current:null,
    selection:null,account:'',http:'',ws:'__choose__',qq:'',imported:false};
  let timer=null,refreshing=null,revision=0,lastReady=false;
  const emit=()=>{for(const view of views)view.render();};
  async function request(action='',body){
    const response=await fetch('/api/desktop/snowluma/'+action,{method:body===undefined?'GET':'POST',cache:'no-store',
      headers:{'Content-Type':'application/json','X-ChatWeave-UI':'1'},body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(30000)});
    const value=await response.json();
    if(!response.ok||value.ok===false){const error=Error(value.message||value.detail||'连接设置未完成。');error.code=value.code;error.safe=true;throw error;}
    return value;
  }
  function clearSelection(){draft.selection=null;draft.account='';draft.http='';draft.ws='__choose__';}
  function setFolder(folder){
    if(draft.busy||draft.current?.enabled)return;
    revision++;draft.folder=folder;draft.accepted=false;draft.imported=false;draft.notice='';clearSelection();emit();
  }
  function receive(s){
    draft.refreshError='';
    if(draft.current&&draft.current.consent_fingerprint!==s.consent_fingerprint){
      revision++;draft.accepted=false;clearSelection();draft.notice='协议或配置范围已变化，请阅读后重新勾选授权。';
    }
    draft.current=s;
    if(draft.qq&&!(s.choices||[]).some(row=>row.id===draft.qq))draft.qq='';
    if(s.folder&&!draft.folder)draft.folder=s.folder;
    emit();
  }
  async function saved(settings){
    // A view refresh failure must not turn a confirmed save into a failed save.
    await Promise.allSettled([...views].map(view=>Promise.resolve().then(()=>view.onSaved(settings))));
  }
  async function refresh(){
    if(draft.busy)return;
    if(refreshing)return refreshing;
    const before=revision;
    refreshing=(async()=>{
      try{
        const s=await request('managed');
        if(draft.busy||before!==revision)return;
        receive(s);
        if(s.qq_ready&&!lastReady){
          const r=await fetch('/api/desktop/status',{cache:'no-store',signal:AbortSignal.timeout(15000)});
          if(r.ok)await saved((await r.json()).settings);
        }
        lastReady=Boolean(s.qq_ready);
      }catch{if(!draft.busy&&before===revision){draft.refreshError='无法连接本机服务，请确认 Tulpa 仍在运行；恢复后会自动刷新。';emit();}}
      finally{refreshing=null;}
    })();
    return refreshing;
  }
  async function run(task){
    if(draft.busy)return;
    draft.busy=true;revision++;emit();
    try{await task();}
    catch(error){
      draft.notice=error.safe||error.code?error.message:'未取得操作结果，请检测当前连接后再决定是否重试。';
      if(['terms_changed','consent_required','installation_changed'].includes(error.code)){draft.accepted=false;clearSelection();}
      if(['selection_expired','selection_required','config_changed'].includes(error.code)){
        clearSelection();draft.notice+=' 点击“载入并配置”重新检查。';
      }
    }finally{draft.busy=false;emit();}
  }
  async function checkConsent(){
    // Recheck terms before a mutation; a stale rendered checkbox grants nothing.
    receive(await request('managed'));
    if(!draft.accepted||!draft.current.available){draft.notice='请先阅读并勾选自动配置授权。';return false;}
    return true;
  }
  function selectedAccount(){return draft.selection?.accounts.find(a=>a.account===draft.account);}
  function chooseAccount(account){
    draft.account=account;
    const nodes=selectedAccount()?.nodes;
    for(const kind of ['http','ws']){
      const usable=(nodes?.[kind]||[]).filter(n=>n.usable);
      draft[kind]=usable.length===1?usable[0].id:kind==='http'?'':'__choose__';
    }
  }
  function canImport(){return draft.accepted&&draft.selection&&draft.account&&draft.http&&draft.ws!=='__choose__';}
  async function importSelected(){
    if(!canImport())return;
    const body={selection_id:draft.selection.selection_id,account:draft.account,http_id:draft.http,ws_id:draft.ws};
    draft.notice='正在检测 HTTP 与实时事件连接，通过后保存…';emit();
    const data=await request('import',body);
    draft.notice=data.message;
    if(data.saved){draft.imported=true;clearSelection();await saved(data.settings);}
  }
  async function connect(){
    if(draft.busy||!draft.folder.trim()||!draft.accepted||draft.current?.enabled||draft.imported)return;
    await run(async()=>{
      if(!await checkConsent())return;
      clearSelection();draft.notice='正在检查 SnowLuma 文件夹…';emit();
      const preview=await request('managed/inspect',{folder:draft.folder.trim()});
      draft.folder=preview.folder;
      if(preview.mode==='existing'){
        draft.notice='正在读取已有账号和连接，随后自动检测并保存…';emit();
        const data=await request('inspect',{folder:preview.folder});
        draft.selection=data;chooseAccount(data.accounts.length===1?data.accounts[0].account:'');
        const nodes=selectedAccount()?.nodes;
        if(nodes?.http.filter(n=>n.usable).length===1&&nodes?.ws.filter(n=>n.usable).length===1)await importSelected();
        else draft.notice=data.accounts.length>1?'检测到多个 QQ 账号，请选择这次要连接的账号。':'连接节点不唯一或缺少实时事件节点，请确认下方选项后连接。';
      }else if(preview.mode==='local'){
        draft.notice='正在自动配置并连接 QQ…';emit();
        const s=await request('managed/start',{accepted:true,fingerprint:preview.consent_fingerprint,selection_id:preview.selection_id});
        draft.notice='';receive(s);
      }else throw Object.assign(Error('无法识别这份 SnowLuma 的配置状态，尚未启动或保存。'),{code:'unexpected_mode'});
    });
  }
  async function action(name,body={}){
    await run(async()=>{
      const s=await request('managed/'+name,body);
      draft.notice='';
      if(name==='revoke'){draft.accepted=false;draft.imported=false;clearSelection();}
      receive(s);
    });
  }
  function mount(host,{prefix,onSaved=async()=>{}}={}){
    const id=name=>'snow-managed-'+prefix+'-'+name,el=name=>document.getElementById(id(name));
    host.classList.add('snowluma-setup','snow-managed');
    host.innerHTML=`<h3>连接 QQ</h3><p>自行下载并解压 <a href="https://github.com/SnowLuma/SnowLuma/releases/tag/v1.14.22" target="_blank" rel="noopener noreferrer">SnowLuma Windows x64 完整版</a>。输入路径、勾选授权，再点击“载入并配置”即可。</p>
      <label for="${id('folder')}">SnowLuma 文件夹</label><div class="snow-folder"><input id="${id('folder')}" type="text" maxlength="2048" placeholder="粘贴已解压的 SnowLuma 文件夹路径" spellcheck="false"><button id="${id('browse')}" type="button">选择文件夹</button></div>
      <label class="desktop-check"><input id="${id('consent')}" type="checkbox">同意 SnowLuma 使用细则，并自动配置和连接 QQ</label>
      <div class="snow-managed-actions"><button id="${id('start')}" type="button" class="primary-button" disabled>载入并配置</button><button id="${id('retry')}" type="button" hidden>重试</button><button id="${id('cancel')}" type="button" hidden>取消本次接入</button><button id="${id('revoke')}" type="button" hidden>撤销自动配置授权</button></div>
      <p id="${id('status')}" role="status" aria-live="polite"></p>
      <div id="${id('existing')}" hidden><label for="${id('account')}">QQ 账号</label><select id="${id('account')}"></select>
        <label for="${id('http')}">HTTP 服务节点</label><select id="${id('http')}"></select>
        <label for="${id('ws')}">实时事件节点</label><select id="${id('ws')}"></select>
        <p id="${id('preview')}" class="snow-preview"></p><button id="${id('apply')}" type="button">确认并连接</button></div>
      <div id="${id('selection')}" hidden><label for="${id('qq')}">选择要连接的 QQ 进程</label><select id="${id('qq')}"></select><button id="${id('select')}" type="button">连接所选 QQ</button><small>账号待验证表示尚不能确认登录 QQ 号；连接后仍会核对。</small></div>
      <details><summary>协议与自动配置说明</summary><div id="${id('agreements')}"></div>
        <p>无需填写端口、Token 或管理密码。仅使用你选择的本地文件夹，不下载或复制 SnowLuma。新目录先备份，再初始化随机密码及独立 HTTP / WS Token，仅监听本机。只有一个 QQ 进程时自动连接，多个时由你选择。</p>
        <p>已有连接自动检测并导入，保留原密码和节点，不接管或关闭外部服务。不会增加 Agent 权限，也不影响聊天、人格及学习记录。</p>
        <p id="${id('security')}"></p><p id="${id('consent-at')}"></p></details>
      <details><summary>高级诊断与恢复</summary><p id="${id('diagnostics')}"></p><p>失败不会覆盖有效连接。恢复整个 Tulpa 备份前请先退出程序。撤销自动配置授权只停止 Tulpa 自己启动的服务，保留 QQ 和已有外部服务。</p></details>`;
    const nativePicker=Boolean(window.TULPA_FOLDER_PICKER&&window.chrome?.webview);
    let pendingPicker=null,dead=false;
    el('browse').hidden=!nativePicker;
    function options(element,items,value,placeholder){
      // Avoid replacing a focused native select on every status poll.
      const key=JSON.stringify([items,placeholder]);
      if(element.dataset.options!==key){
        element.replaceChildren(new Option(placeholder.text,placeholder.value));
        for(const row of items){const option=new Option(row.text,row.value);option.disabled=Boolean(row.disabled);element.add(option);}
        element.dataset.options=key;
      }
      element.value=value;
    }
    function render(){
      if(dead)return;
      const s=draft.current||{},locked=draft.busy||Boolean(s.enabled);
      if(el('folder').value!==draft.folder)el('folder').value=draft.folder;
      el('folder').disabled=el('browse').disabled=locked;
      el('consent').checked=draft.accepted;
      el('consent').disabled=locked||!s.available||!draft.folder.trim();
      el('start').disabled=locked||!s.available||!draft.accepted||!draft.folder.trim()||draft.imported||Boolean(draft.selection);
      el('start').textContent=draft.busy?'正在载入并配置…':draft.imported?'已载入配置':s.enabled?(s.qq_ready?'QQ 已连接':'自动配置进行中'):'载入并配置';
      el('status').textContent=draft.refreshError||draft.notice||((s.enabled||!['idle',undefined].includes(s.phase))?
        (s.phase_label||'')+(s.account?' · QQ '+s.account:'')+(s.message?'。'+s.message:''):'');
      el('retry').hidden=!s.available||!['error','cancelled','stopped'].includes(s.phase)||!s.consent_current;
      el('cancel').hidden=!s.enabled||['ready','error','revoked','stopped'].includes(s.phase);
      el('revoke').hidden=!(s.has_consent??s.consent_current);
      for(const name of ['retry','cancel','revoke','select','account','http','ws','qq'])el(name).disabled=draft.busy;
      el('selection').hidden=s.phase!=='choosing'||!s.selection_required;
      options(el('qq'),(s.choices||[]).map(row=>({value:row.id,text:(row.account_label||'账号待验证')+' · PID '+row.pid+' · '+row.path})),draft.qq,{text:'请选择 QQ 进程',value:''});
      el('select').disabled=draft.busy||!draft.qq;
      el('existing').hidden=!draft.selection;
      if(draft.selection){
        options(el('account'),draft.selection.accounts.map(a=>({value:a.account,text:a.account+(a.code==='ok'?'':' · '+a.message)})),draft.account,{text:'请选择 QQ 账号',value:''});
        const account=selectedAccount();
        for(const kind of ['http','ws']){
          const items=(account?.nodes[kind]||[]).map(n=>({value:n.id,text:n.name+' · '+(n.url||n.reason)+(n.usable?'':' · 不可用'),disabled:!n.usable}));
          if(kind==='ws')items.push({value:'',text:'仅 HTTP（不支持持续群聊）'});
          options(el(kind),items,draft[kind],{text:kind==='http'?'请选择 HTTP 节点':'请选择实时事件节点',value:kind==='http'?'':'__choose__'});
        }
        el('preview').textContent=!account?'请选择实际要使用的 QQ 账号。':draft.ws===''?'仅 HTTP：保存将关闭原有事件连接，持续群聊暂不可用。':'将核对两个节点是否属于同一 QQ 账号，通过后保存。';
      }
      el('apply').disabled=draft.busy||!canImport();
      el('security').textContent=s.security_note||'';
      el('consent-at').textContent=s.consent_at?'授权时间：'+new Date(s.consent_at*1000).toLocaleString()+'；'+(s.consent_current?'适用于当前范围。':'内容或范围已变化，需重新同意。'):'';
      el('agreements').replaceChildren();
      for(const d of s.agreements||[]){
        let url;try{url=new URL(d.url);}catch{continue;}
        if(url.origin!=='https://github.com'||!url.pathname.startsWith('/SnowLuma/SnowLuma/'))continue;
        const p=document.createElement('p'),a=document.createElement('a');a.href=url.href;a.target='_blank';a.rel='noopener noreferrer';a.textContent=d.title+' · '+(d.version||s.version);
        p.append(a,document.createTextNode(' · SHA256 '+d.sha256));el('agreements').append(p);
      }
      el('diagnostics').textContent=['自动初始化支持 SnowLuma '+(s.version||''),s.code?'状态码：'+s.code:'',s.folder?'运行目录：'+s.folder:'',s.storage?'本机凭据与进度：'+s.storage:'',s.service_pid?'服务 PID：'+s.service_pid:'',s.qq_pid?'QQ PID：'+s.qq_pid:''].filter(Boolean).join('\n');
    }
    el('folder').oninput=()=>setFolder(el('folder').value);
    el('consent').onchange=()=>{if(!draft.busy){draft.accepted=el('consent').checked;if(!draft.accepted)clearSelection();emit();}};
    el('browse').onclick=()=>{if(draft.busy)return;pendingPicker='snow-local-'+Date.now()+'-'+Math.random().toString(16).slice(2);window.chrome.webview.postMessage('tulpa-pick-snowluma-folder:'+pendingPicker);};
    function picked(event){
      const result=event.data;if(dead||result?.kind!=='snowluma-folder'||result.requestId!==pendingPicker)return;
      pendingPicker=null;if(result.path)setFolder(result.path);
    }
    if(nativePicker)window.chrome.webview.addEventListener('message',picked);
    el('start').onclick=()=>void connect();
    el('account').onchange=()=>{chooseAccount(el('account').value);emit();};
    for(const kind of ['http','ws'])el(kind).onchange=()=>{draft[kind]=el(kind).value;emit();};
    el('apply').onclick=()=>void run(async()=>{if(await checkConsent())await importSelected();});
    for(const name of ['retry','cancel','revoke'])el(name).onclick=()=>void action(name);
    el('qq').onchange=()=>{draft.qq=el('qq').value;emit();};
    el('select').onclick=()=>{if(draft.qq)void action('select',{choice_id:draft.qq});};
    const view={render,onSaved,host};views.add(view);render();void refresh();
    if(!timer)timer=setInterval(()=>{if(!document.hidden&&[...views].some(v=>v.host.isConnected&&v.host.getClientRects().length))void refresh();},2000);
    return {refresh,reset(){render();void refresh();},destroy(){dead=true;views.delete(view);if(nativePicker)window.chrome.webview.removeEventListener?.('message',picked);if(!views.size){clearInterval(timer);timer=null;}}};
  }
  window.TulpaSnowLuma={mount,mountManaged:mount};
  // The desktop shell disables browser accelerators. Handle only the explicit
  // refresh key in the page; reloading the UI does not restart MCP or OneBot.
  document.addEventListener('keydown',event=>{
    if(!window.TULPA_FOLDER_PICKER||event.key!=='F5'||event.ctrlKey||event.altKey||event.metaKey)return;
    event.preventDefault();
    if(event.repeat)return;
    if(draft.busy){draft.notice='连接配置正在进行，请等待操作结束后再刷新。';emit();return;}
    window.location.reload();
  });
})();
