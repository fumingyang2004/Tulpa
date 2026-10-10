// Real Edge, shipped settings component, synthetic same-origin responses only.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright-core');
const root=path.resolve(process.env.TULPA_TEST_PACKAGE||path.join(__dirname,'..'));
const origin='http://127.0.0.1:39879';
const manifest=JSON.parse(fs.readFileSync(path.join(root,'chatlocal/snowluma_manifest.json'),'utf8'));
for(const name of ['index.html','mcp-home.html']){
  const html=fs.readFileSync(path.join(root,'web',name),'utf8');
  assert(html.indexOf('/ui/snowluma.js')<html.indexOf('/ui/mcp.js'),'Managed component must load before MCP mount: '+name);
}
const shots=process.env.SNOWLUMA_UI_SCREENSHOTS;
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try{
    for(const edition of ['full','lite']){
      const page=await browser.newPage({viewport:{width:1024,height:1000}});let state={available:false,phase:'blocked',phase_label:'等待上游授权',version:manifest.version,agreements:manifest.agreements,
        consent_fingerprint:'fixture-v1',enabled:false,consent_current:false,real_pc_verified:false,authorization_note:'未取得上游自动化部署授权；此测试版不下载或启动 SnowLuma。',security_note:'DPAPI / 上游隔离目录',choices:[]};
      const calls=[],errors=[];page.on('pageerror',e=>errors.push(e.message));
      await page.route(origin+'/**',async route=>{
        const request=route.request(),url=new URL(request.url());
        const reply=value=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
        if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><link rel="stylesheet" href="/ui/desktop.css"><body><main style="max-width:780px;margin:24px auto;padding:20px"><h1>Tulpa · ${edition} 接入验收（模拟）</h1><div id="host"></div></main><script src="/ui/snowluma.js"></script><script>window.mounted=TulpaSnowLuma.mountManaged(document.getElementById('host'),{prefix:'fixture'});</script></body></html>`});
        if(url.pathname.startsWith('/ui/'))return route.fulfill({contentType:url.pathname.endsWith('.js')?'text/javascript':'text/css',body:fs.readFileSync(path.join(root,'web',path.basename(url.pathname)))});
        if(url.pathname==='/api/desktop/snowluma/managed')return reply(state);
        assert.equal(request.method(),'POST');assert.equal(request.headers()['x-chatweave-ui'],'1');
        const action=url.pathname.split('/').pop(),body=request.postDataJSON();calls.push({action,body});
        if(action==='start')state={...state,enabled:true,consent_current:true,consent_at:Date.now()/1000,phase:'downloading',phase_label:'下载并校验',progress:{downloaded:30,total:100}};
        if(action==='select'){assert.equal(body.choice_id,'choice-202');state={...state,phase:'waiting_login',phase_label:'等待 QQ 登录',message:'请在 QQ 完成必要扫码。',selection_required:false};}
        if(action==='retry')state={...state,phase:'verifying',phase_label:'验证连接',message:'正在核对同一账号…'};
        if(action==='cancel')state={...state,phase:'cancelled',phase_label:'已取消',enabled:false};
        if(action==='revoke')state={...state,phase:'revoked',phase_label:'已撤销授权',enabled:false,consent_current:false,has_consent:false};
        return reply({...state,ok:true});
      });
      const q=name=>page.locator('#snow-managed-fixture-'+name);
      async function refresh(){await page.evaluate(()=>window.mounted.refresh());}
      async function has(text){await page.waitForFunction(t=>document.getElementById('snow-managed-fixture-status').textContent.includes(t),text);}
      async function shot(name){if(shots){fs.mkdirSync(shots,{recursive:true});await page.screenshot({path:path.join(shots,edition+'-'+name+'.png'),fullPage:true});}}
      await page.goto(origin);await has('等待上游授权');assert.equal(await q('consent').isChecked(),false);assert.equal(await q('start').isEnabled(),false);await shot('authorization-blocked');
      state={...state,available:true,phase:'idle',phase_label:'等待授权',authorization_note:'模拟安装；不会接触 QQ。'};await refresh();
      assert.equal(await q('start').isEnabled(),false);await q('consent').check();await q('start').evaluate(e=>{e.click();e.click();});await has('下载并校验');
      assert.equal(calls.filter(c=>c.action==='start').length,1);assert.deepEqual(calls[0].body,{accepted:true,fingerprint:'fixture-v1'});assert.equal(await q('progress').getAttribute('value'),'30');await shot('downloading');
      state={...state,phase:'choosing',phase_label:'选择 QQ',selection_required:true,choices:[{id:'choice-101',pid:101,path:'C:/QQ/QQ.exe',account_label:'12345'},{id:'choice-202',pid:202,path:'C:/QQ/QQ.exe',account_label:'账号待验证'}]};await refresh();await q('qq').selectOption('choice-202');await refresh();assert.equal(await q('qq').inputValue(),'choice-202');await shot('choose-qq');
      await q('select').click();await has('等待 QQ 登录');
      state={...state,phase:'error',phase_label:'接入未完成',code:'event_unverified',message:'尚未收到同账号的生命周期或心跳事件。',retryable:true};await refresh();await shot('failure');await q('retry').click();await has('验证连接');
      state={...state,phase:'ready',phase_label:'QQ 已就绪',account:'12345',qq_ready:true,message:'管理认证与事件身份一致。'};await refresh();await shot('ready-mock');
      assert.equal(await q('consent').isChecked(),true);await q('revoke').click();await has('已撤销授权');assert.equal(calls.filter(c=>c.action==='revoke').length,1);
      state={...state,phase:'idle',phase_label:'等待重新同意',consent_fingerprint:'fixture-v2'};await refresh();assert.equal(await q('consent').isChecked(),false);assert.equal(await q('start').isEnabled(),false);
      state={...state,phase:'error',enabled:true,has_consent:true,consent_current:false,code:'terms_changed'};await refresh();assert.equal(await q('revoke').isVisible(),true);await q('revoke').click();await has('已撤销授权');assert.equal(await q('consent').isEnabled(),true);
      await page.setViewportSize({width:390,height:844});await shot('narrow');assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
      assert.deepEqual(errors,[]);assert.ok(!(await page.locator('body').innerText()).includes('Bearer '));
      await page.close();
    }
    console.log('PASS real Edge + shipped managed component: unchecked consent, explicit upstream gate, one start, download progress, process/unknown UIN, retry, ready(mock), revoke, changed terms, narrow screen; real SnowLuma/QQ calls=0');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
