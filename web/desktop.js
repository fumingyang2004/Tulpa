'use strict';
// Presentation and local configuration only. All existing workflows are reused.
(function(){
  // Configuration is shared by the web and desktop entry points. Also guard
  // against duplicate loading while an older running server injects this file.
  if(document.getElementById('desktop-settings'))return;
  const isDesktop=document.documentElement.dataset.desktop==='true';
  const icon=name=>`<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
  if(isDesktop){
    document.title='Tulpa';
    document.querySelector('#welcome h2').textContent='从真实交流中，慢慢懂你。';
    document.querySelector('#welcome > p').textContent='留住重要的消息，理清值得继续的事。';
    const eyebrow=document.createElement('div');eyebrow.className='desktop-eyebrow';eyebrow.textContent='TULPA / 通信工作区';
    document.querySelector('#welcome h2').before(eyebrow);
    document.querySelectorAll('.suggestions button').forEach((button,i)=>button.insertAdjacentHTML('afterbegin',icon(['chat','watch','workspace'][i])));
    const badge=document.createElement('span');badge.className='desktop-local-badge';badge.textContent='本机工作空间';
    document.getElementById('header-range').before(badge);
  }
  const settings=document.createElement('button');settings.id='desktop-settings';settings.className='desktop-settings-button';
  settings.innerHTML=icon('sliders')+'<span>模型设置</span><span id="desktop-model-status">未配置</span>';
  document.querySelector('.sidebar-bottom').prepend(settings);
  const dialog=document.createElement('dialog');dialog.id='desktop-config';dialog.className='desktop-config';
  dialog.setAttribute('aria-labelledby','desktop-config-title');
  dialog.innerHTML=`<div class="dialog-head"><h2 id="desktop-config-title">连接你的模型</h2><button type="button" class="icon-button" id="desktop-config-close" aria-label="关闭模型设置">${icon('close')}</button></div>
  <form id="desktop-config-form" autocomplete="off"><p class="config-intro">选择你使用的模型服务。配置仅保存在本机，随时可以修改。</p>
  <label for="desktop-api-base">API 地址</label><input id="desktop-api-base" type="url" required placeholder="例如 https://api.deepseek.com" spellcheck="false" maxlength="2048"><small>填写 API 基础地址，无需添加 /chat/completions。</small>
  <label for="desktop-api-key">API Key</label><input id="desktop-api-key" type="password" placeholder="输入你的 API Key" autocomplete="new-password" maxlength="4096"><small id="desktop-key-hint">密钥不会显示在聊天中，也不会随发布包分发。</small>
  <label for="desktop-model">模型名称</label><input id="desktop-model" required placeholder="例如 deepseek-flash" spellcheck="false" maxlength="200"><small>须与服务商提供的模型 ID 一致，接口需支持流式输出与工具调用。</small>
  <details><summary>QQ 发送接口（可选）</summary><label for="desktop-sender-url">本机 OneBot 地址</label><input id="desktop-sender-url" type="url" placeholder="http://127.0.0.1:3000" maxlength="2048"><label for="desktop-sender-token">访问 Token</label><input id="desktop-sender-token" type="password" autocomplete="new-password" maxlength="4096"><small>独立的本机 QQ 发送服务；每条消息仍需你批准。</small></details>
  <p class="desktop-storage" id="desktop-storage"></p><p><a href="/api/desktop/diagnostics" download="Tulpa-diagnostics.json">导出运行诊断</a><small>用于排查导入失败，包含系统及客户端版本、权限和错误类别，不包含聊天或密钥。</small></p><p id="desktop-config-error" class="config-error" role="status"></p>
  <div class="config-foot"><button type="button" class="quiet-button" id="desktop-config-later">稍后配置</button><button type="submit" class="primary-button" id="desktop-config-save">保存设置</button></div></form>`;
  document.body.append(dialog);
  const el=id=>document.getElementById(id);
  let loaded=null;
  async function load(){
    const response=await fetch('/api/desktop/status',{cache:'no-store'});if(!response.ok)throw Error('暂时无法读取模型设置。');
    loaded=await response.json();const s=loaded.settings;
    el('desktop-api-base').value=s.api_base;el('desktop-model').value=s.model;el('desktop-api-key').value='';el('desktop-api-key').required=!s.has_api_key;
    el('desktop-api-key').placeholder=s.has_api_key?'已保存 · 留空保留现有密钥':'输入你的 API Key';
    el('desktop-key-hint').textContent=s.has_api_key?'密钥已保存在本机，此处不会回显；填写新值即可替换。':'密钥不会显示在聊天中，也不会随发布包分发。';
    el('desktop-sender-url').value=s.sender_url;el('desktop-sender-token').value='';el('desktop-sender-token').placeholder=s.has_sender_token?'已保存 · 留空保留':'访问 Token（如接口需要）';
    el('desktop-model-status').textContent=s.configured?'已配置':'未配置';
    el('model-label').textContent=s.configured?s.model:'未配置模型';
    el('desktop-storage').textContent=`Tulpa ${loaded.version} · 数据保存在 ${loaded.storage}`;
    return s;
  }
  async function open(){el('desktop-config-error').textContent='';try{await load();}catch(e){el('desktop-config-error').textContent=e.message;}if(!dialog.open)dialog.showModal();}
  settings.onclick=open;
  el('desktop-config-close').onclick=el('desktop-config-later').onclick=()=>dialog.close();
  el('desktop-config-form').onsubmit=async event=>{
    event.preventDefault();const save=el('desktop-config-save');save.disabled=true;el('desktop-config-error').textContent='';
    try {
      const body={api_base:el('desktop-api-base').value,api_key:el('desktop-api-key').value,model:el('desktop-model').value,
        sender_url:el('desktop-sender-url').value,sender_token:el('desktop-sender-token').value};
      const response=await fetch('/api/desktop/settings',{method:'PUT',headers:{'Content-Type':'application/json','X-ChatWeave-UI':'1'},body:JSON.stringify(body)});
      const result=await response.json();if(!response.ok)throw Error(result.detail||'配置保存失败。');
      el('desktop-api-key').value='';el('desktop-sender-token').value='';await load();dialog.close();
      if(typeof toast==='function')toast('模型设置已保存，对下一次提问生效。');
    }catch(e){el('desktop-config-error').textContent=e.message;}finally{save.disabled=false;}
  };
  dialog.addEventListener('close',()=>{el('desktop-api-key').value='';el('desktop-sender-token').value='';});
  load().then(s=>{if(isDesktop&&!s.configured&&!sessionStorage.getItem('chatweave.setup-seen')){sessionStorage.setItem('chatweave.setup-seen','1');dialog.showModal();}}).catch(()=>{el('desktop-model-status').textContent='设置不可用';});
})();
