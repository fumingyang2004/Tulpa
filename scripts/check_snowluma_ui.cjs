// Actual full/lite HTML and settings scripts in Edge; synthetic local API only.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright-core');
const root=path.resolve(__dirname,'..'),origin='http://127.0.0.1:39878';
const source=fs.readFileSync(path.join(root,'web/index.html'),'utf8');
function html(edition){
  let body=source;
  if(edition==='lite'){
    const parts=[source.match(/<svg class="icon-defs"[\s\S]*?<\/svg>/)[0]];
    for(const id of ['data-dialog','chat-dialog','image-dialog'])parts.push(source.match(new RegExp('<dialog id="'+id+'"[\\s\\S]*?<\\/dialog>'))[0]);
    body=fs.readFileSync(path.join(root,'web/mcp-home.html'),'utf8').replace('<!-- shared-forms -->',parts.join('\n'));
  }
  const keep=edition==='lite'?['snowluma.js','mcp-home.js']:['snowluma.js','desktop.js'];
  return body.replace(/<script\b[^>]*>[\s\S]*?<\/script>/g,tag=>keep.some(name=>tag.includes('/ui/'+name))?tag:'');
}
function preview(multiple){
  const node=(kind,n)=>({id:kind+n,name:kind+' 节点 '+n,url:(kind==='http'?'http':'ws')+'://127.0.0.1:'+(4200+n)+(kind==='http'?'/api':'/events'),usable:true,token_present:true});
  return {ok:true,selection_id:'fixture-preview',folder:'C:\\SnowLuma-fixture',version:'1.14.20',message:'识别完成，请选择账号与节点。',accounts:[1,...(multiple?[2]:[])].map(n=>({account:String(12344+n),code:'ok',message:'可选择节点。',nodes:{http:[node('http',1),...(multiple?[node('http',2)]:[])],ws:[node('ws',1),...(multiple?[node('ws',2)]:[])]}}))};
}
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try{
    for(const edition of ['full','lite']){
      const page=await browser.newPage({viewport:{width:1100,height:960}}),errors=[],requests=[];
      page.on('pageerror',e=>errors.push(e.message));
      let settings={api_base:'',model:'',has_api_key:false,configured:false,sender_url:'http://127.0.0.1:19000/old',events_url:'ws://127.0.0.1:19001/old',has_sender_token:true,has_events_token:true};
      let multiple=true,failInspect=false,failImport=false,slow=false;
      await page.addInitScript(()=>{window.TULPA_FOLDER_PICKER=true;window.__pickerListeners=[];window.chrome={webview:{postMessage:m=>{window.__pickerRequest=m;},addEventListener:(_,fn)=>window.__pickerListeners.push(fn)}};});
      await page.route(origin+'/**',async route=>{
        const req=route.request(),url=new URL(req.url());
        const reply=data=>route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
        if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:html(edition)});
        if(url.pathname.startsWith('/ui/'))return route.fulfill({contentType:url.pathname.endsWith('.js')?'text/javascript':url.pathname.endsWith('.css')?'text/css':'image/png',body:fs.readFileSync(path.join(root,'web',path.basename(url.pathname)))});
        if(url.pathname==='/api/desktop/status')return reply({settings,owned:true,storage:'fixture',version:'test'});
        if(url.pathname==='/api/desktop/preferences')return reply({mode:'mcp',background:true});
        if(url.pathname==='/api/desktop/snowluma/managed')return reply({available:true,phase:'idle',phase_label:'请选择 SnowLuma 文件夹',version:'1.14.22',agreements:[],consent_fingerprint:'fixture'});
        if(url.pathname==='/api/read-options')return reply({qq_per_chat:{min:1,max:10000,default:100},wechat_per_chat:{min:1,max:10000,default:100}});
        if(url.pathname==='/api/mcp')return reply({running:false,enabled:false,data_platforms:{}});
        if(url.pathname==='/api/mcp/operations')return reply({operations:[]});
        if(url.pathname==='/api/components/voice')return reply({status:'available',message:'fixture',total:1,downloaded:0});
        const body=req.postDataJSON();requests.push({path:url.pathname,body});
        if(url.pathname==='/api/desktop/snowluma/managed/inspect')return reply({ok:true,mode:'existing',folder:body.folder,version:'1.14.20',selection_id:'existing-fixture',consent_fingerprint:'existing-consent',message:'检测到已有配置'});
        if(url.pathname==='/api/desktop/snowluma/inspect')return reply(failInspect?{ok:false,code:'account_config_missing',message:'账号配置尚未生成，请手动加载 QQ。'}:preview(multiple));
        if(url.pathname==='/api/desktop/snowluma/import'){
          if(slow)await new Promise(resolve=>setTimeout(resolve,150));
          if(failImport)return reply({ok:false,saved:false,code:'ws_auth_error',message:'WS 认证被拒绝；未保存，原连接保持不变。'});
          settings={...settings,sender_url:'http://127.0.0.1:4202/api',events_url:body.ws_id?'ws://127.0.0.1:4202/events':''};
          return reply({ok:true,saved:true,settings,message:'已核对同一 QQ 账号并保存到 Tulpa。'});
        }
        if(url.pathname==='/api/desktop/settings'){
          assert.ok(!('api_key' in body),'OneBot requires no model key');
          settings={...settings,sender_url:body.sender_url,events_url:body.events_url};return reply(settings);
        }
        if(url.pathname==='/api/desktop/onebot/test')return reply({ok:true,message:'fixture HTTP/WS 已连接'});
        throw Error('Unexpected fixture endpoint '+url.pathname);
      });
      const prefix=edition==='full'?'desktop':'lite',q=name=>page.locator('#snow-'+prefix+'-'+name);
      const manual=page.locator('#'+prefix+'-onebot-manual'),dialog=page.locator(edition==='full'?'#desktop-config':'#lite-onebot');
      async function open(){if(edition==='full')await page.evaluate(()=>window.dispatchEvent(new CustomEvent('tulpa-open-settings',{detail:'onebot'})));else await page.locator('#open-onebot').click();await dialog.waitFor({state:'visible'});await page.locator('#snow-existing-'+prefix).evaluate(d=>d.open=true);}
      async function scan(){await q('inspect').click();await q('selection').waitFor({state:'visible'});await page.waitForFunction(id=>!document.getElementById(id).disabled,'snow-'+prefix+'-inspect');}
      async function choose(){await q('account').selectOption('12345');await q('http').selectOption('http2');await q('ws').selectOption('ws2');}
      async function status(text){await page.waitForFunction(([id,text])=>document.getElementById(id).textContent.includes(text),['snow-'+prefix+'-status',text]);}
      await page.goto(origin);await open();assert.equal(await manual.getAttribute('open'),null);
      const httpField=page.locator(edition==='full'?'#desktop-sender-url':'#lite-onebot-url');
      assert.equal(await httpField.inputValue(),'http://127.0.0.1:19000/old');
      // Cancelling a native directory choice preserves both saved fields and selection.
      await q('browse').click();await page.evaluate(()=>{const requestId=window.__pickerRequest.split(':')[1];for(const fn of window.__pickerListeners)fn({data:{kind:'snowluma-folder',requestId,path:''}});});
      assert.equal(requests.length,0);
      // A successful native selection only previews; it never saves.
      await q('browse').click();await page.evaluate(()=>{const requestId=window.__pickerRequest.split(':')[1];for(const fn of window.__pickerListeners)fn({data:{kind:'snowluma-folder',requestId,path:'C:\\SnowLuma-fixture'}});});
      await q('selection').waitFor({state:'visible'});assert.equal(await q('account').inputValue(),'');assert.equal(await q('apply').isEnabled(),false);
      assert.equal(await httpField.inputValue(),'http://127.0.0.1:19000/old');assert.equal(requests.filter(r=>r.path.endsWith('/import')).length,0);
      await q('account').selectOption('12345');assert.equal(await q('http').inputValue(),'');assert.equal(await q('ws').inputValue(),'__choose__');
      await choose();
      if(process.env.SNOWLUMA_UI_SCREENSHOTS)await page.screenshot({path:path.join(process.env.SNOWLUMA_UI_SCREENSHOTS,edition+'-selection.png')});
      failImport=true;await q('apply').click();await status('认证被拒绝');assert.equal(await httpField.inputValue(),'http://127.0.0.1:19000/old');
      failImport=false;slow=true;await q('apply').evaluate(b=>{b.click();b.click();});await status('已核对');
      assert.equal(requests.filter(r=>r.path.endsWith('/import')).length,2);assert.equal(await httpField.inputValue(),'http://127.0.0.1:4202/api');
      for(const r of requests.filter(r=>r.path.endsWith('/import')))assert.deepEqual(r.body,{selection_id:'fixture-preview',account:'12345',http_id:'http2',ws_id:'ws2'});
      // Reopening folds manual settings and reloads the saved values without a scan/save.
      await dialog.evaluate(d=>d.close());const count=requests.length;await open();assert.equal(requests.length,count);assert.equal(await manual.getAttribute('open'),null);
      assert.equal(await httpField.inputValue(),'http://127.0.0.1:4202/api');assert.equal(await q('selection').isVisible(),false);
      multiple=false;await scan();assert.equal(await q('account').inputValue(),'12345');await q('ws').selectOption('');
      assert.ok((await q('preview').textContent()).includes('关闭 Tulpa 原有事件连接'));await q('apply').click();await status('已核对');assert.equal(settings.events_url,'');
      failInspect=true;await q('inspect').click();await status('手动加载 QQ');assert.equal(await q('selection').isVisible(),false);
      await manual.locator('summary').click();await httpField.fill('http://127.0.0.1:19002/manual');
      await manual.locator('button[type=submit]').click();await page.waitForFunction(id=>document.getElementById(id).textContent.includes('已连接'),edition==='full'?'desktop-onebot-status':'lite-onebot-result');
      assert.equal(settings.sender_url,'http://127.0.0.1:19002/manual');
      // The main entry imports an unambiguous existing configuration after the
      // one checkbox, without asking the user to fill tokens or choose nodes.
      failInspect=false;multiple=false;slow=false;
      const managed=name=>page.locator('#snow-managed-'+prefix+'-'+name);
      await managed('folder').fill('C:\\SnowLuma-fixture');await managed('inspect').click();
      await page.waitForFunction(id=>document.getElementById(id).textContent.includes('已有配置'),'snow-managed-'+prefix+'-status');
      const beforeImport=requests.filter(r=>r.path.endsWith('/import')).length;
      assert.equal(await managed('consent').isChecked(),false);await managed('consent').check();await managed('start').click();
      await page.waitForFunction(id=>document.getElementById(id).textContent.includes('已核对'),'snow-managed-'+prefix+'-status');
      assert.equal(requests.filter(r=>r.path.endsWith('/import')).length,beforeImport+1);
      assert.equal(requests.filter(r=>r.path.endsWith('/managed/start')).length,0);
      // Multiple accounts/nodes still require an explicit selection, no guessing.
      multiple=true;await managed('start').click();
      await page.waitForFunction(id=>document.getElementById(id).textContent.includes('多个选择'),'snow-managed-'+prefix+'-status');
      assert.equal(requests.filter(r=>r.path.endsWith('/import')).length,beforeImport+1);
      assert.deepEqual(errors,[]);
      if(process.env.SNOWLUMA_UI_SCREENSHOTS){await manual.locator('summary').click();await page.screenshot({path:path.join(process.env.SNOWLUMA_UI_SCREENSHOTS,edition+'.png')});}
      await page.close();
    }
    console.log('PASS real Edge + full/lite settings HTML/scripts: native picker cancellation, explicit account/node selection, no silent save, error retention, independent model settings, HTTP-only warning, repeated opens/saves, collapsed manual fallback. Native picker callback is simulated.');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
