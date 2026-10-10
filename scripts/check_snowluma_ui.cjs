// Actual full/lite + MCP entrances in Edge. All APIs are synthetic, no QQ writes.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright-core');
const root=path.resolve(process.env.TULPA_TEST_PACKAGE||path.join(__dirname,'..')),origin='http://127.0.0.1:39878';
const source=fs.readFileSync(path.join(root,'web/index.html'),'utf8');
function html(edition){
  let body=source;
  if(edition==='lite'){
    const parts=[source.match(/<svg class="icon-defs"[\s\S]*?<\/svg>/)[0]];
    for(const id of ['data-dialog','chat-dialog','image-dialog'])parts.push(source.match(new RegExp('<dialog id="'+id+'"[\\s\\S]*?<\\/dialog>'))[0]);
    body=fs.readFileSync(path.join(root,'web/mcp-home.html'),'utf8').replace('<!-- shared-forms -->',parts.join('\n'));
  }
  const keep=['snowluma.js','mcp.js',edition==='lite'?'mcp-home.js':'desktop.js'];
  return body.replace(/<script\b[^>]*>[\s\S]*?<\/script>/g,tag=>keep.some(name=>tag.includes('/ui/'+name))?tag:'');
}
function preview(multiple,httpOnly=false){
  const node=(kind,n)=>({id:kind+n,name:kind+' 节点 '+n,url:(kind==='http'?'http':'ws')+'://127.0.0.1:'+(4200+n),usable:true,token_present:true});
  return {ok:true,selection_id:'fixture-preview',folder:'C:\\SnowLuma-fixture',version:'1.14.20',message:'识别完成',accounts:[1,...(multiple?[2]:[])].map(n=>({account:String(12344+n),code:'ok',message:'可选择节点',nodes:{http:[node('http',1),...(multiple?[node('http',2)]:[])],ws:httpOnly?[]:[node('ws',1),...(multiple?[node('ws',2)]:[])]}}))};
}
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try{
    for(const edition of ['full','lite']){
      const page=await browser.newPage({viewport:{width:1100,height:960}}),errors=[],requests=[];
      page.on('pageerror',e=>errors.push(e.message));
      let settings={api_base:'',model:'',has_api_key:false,configured:false,sender_url:'http://127.0.0.1:19000/old',events_url:'ws://127.0.0.1:19001/old',has_sender_token:true,has_events_token:true};
      let multiple=false,httpOnly=false,failInspect=false,failImport=false,slow=false,dropImport=false,expired=false;
      let managedState={available:true,phase:'idle',phase_label:'请选择 SnowLuma 文件夹',version:'1.14.22',agreements:[],consent_fingerprint:'fixture'};
      await page.addInitScript(()=>{window.TULPA_FOLDER_PICKER=true;window.__pickerListeners=[];window.chrome={webview:{postMessage:m=>{window.__pickerRequest=m;},addEventListener:(_,fn)=>window.__pickerListeners.push(fn)}};});
      await page.route(origin+'/**',async route=>{
        const req=route.request(),url=new URL(req.url());
        const reply=data=>route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
        if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:html(edition)});
        if(url.pathname.startsWith('/ui/'))return route.fulfill({contentType:url.pathname.endsWith('.js')?'text/javascript':url.pathname.endsWith('.css')?'text/css':'image/png',body:fs.readFileSync(path.join(root,'web',path.basename(url.pathname)))});
        if(url.pathname==='/api/desktop/status')return reply({settings,owned:true,storage:'fixture',version:'test'});
        if(url.pathname==='/api/desktop/preferences')return reply({mode:'mcp',background:true});
        if(url.pathname==='/api/desktop/snowluma/managed')return reply(managedState);
        if(url.pathname==='/api/read-options')return reply({qq_per_chat:{min:1,max:10000,default:100},wechat_per_chat:{min:1,max:10000,default:100}});
        if(url.pathname==='/api/mcp')return reply({running:false,enabled:false,port:4200,data_platforms:{},connections:[],recent:[],onebot_configured:true});
        if(url.pathname==='/api/mcp/operations')return reply({operations:[]});
        if(url.pathname==='/api/mcp/chat-overview')return reply({groups:[]});
        if(url.pathname==='/api/mcp/conversations')return reply({items:[],has_more:false,next_offset:0});
        if(url.pathname==='/api/components/voice')return reply({status:'available',message:'fixture',total:1,downloaded:0});
        const body=req.postDataJSON();requests.push({path:url.pathname,body});
        if(url.pathname==='/api/desktop/snowluma/managed/inspect')return reply(failInspect?{ok:false,code:'folder_missing',message:'文件夹不存在，请重新填写。'}:{ok:true,mode:'existing',folder:body.folder,version:'1.14.20',selection_id:'existing-fixture',consent_fingerprint:'existing-consent',message:'检测到已有配置'});
        if(url.pathname==='/api/desktop/snowluma/inspect')return reply(preview(multiple,httpOnly));
        if(url.pathname==='/api/desktop/snowluma/import'){
          if(slow)await new Promise(resolve=>setTimeout(resolve,250));
          if(dropImport)return route.abort('failed');
          if(expired)return reply({ok:false,saved:false,code:'selection_expired',message:'识别结果已过期，请重新检测文件夹。'});
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
      const prefix=edition==='full'?'desktop':'lite',q=(name,p='mcp')=>page.locator('#snow-managed-'+p+'-'+name);
      const manual=page.locator('#'+prefix+'-onebot-manual'),settingsDialog=page.locator(edition==='full'?'#desktop-config':'#lite-onebot');
      const mcpDialog=page.locator('#mcp-settings');
      const httpField=page.locator(edition==='full'?'#desktop-sender-url':'#lite-onebot-url');
      const imports=()=>requests.filter(r=>r.path.endsWith('/import'));
      const has=async(text,p='mcp')=>page.waitForFunction(([id,text])=>document.getElementById(id).textContent.includes(text),['snow-managed-'+p+'-status',text]);
      async function openMcp(){await page.locator('#open-mcp').click();await mcpDialog.waitFor({state:'visible'});await q('folder').evaluate(e=>{for(let p=e.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true;});}
      async function screenshot(name){if(process.env.SNOWLUMA_UI_SCREENSHOTS){fs.mkdirSync(process.env.SNOWLUMA_UI_SCREENSHOTS,{recursive:true});await q('folder',await settingsDialog.isVisible()?prefix:'mcp').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(process.env.SNOWLUMA_UI_SCREENSHOTS,edition+'-'+name+'.png')});}}
      await page.goto(origin);await openMcp();
      assert.equal(await page.locator('[id^=snow-managed-][id$=-inspect]').count(),0);
      assert.equal(await q('consent').isChecked(),false);
      // Picking/pasting never loads, saves or starts anything before the one click.
      await q('browse').click();await page.evaluate(()=>{const requestId=window.__pickerRequest.split(':')[1];for(const fn of window.__pickerListeners)fn({data:{kind:'snowluma-folder',requestId,path:''}});});
      assert.equal(requests.length,0);
      await q('browse').click();await page.evaluate(()=>{const requestId=window.__pickerRequest.split(':')[1];for(const fn of window.__pickerListeners)fn({data:{kind:'snowluma-folder',requestId,path:'C:\\SnowLuma-fixture'}});});
      assert.equal(requests.length,0);assert.equal(await q('start').isEnabled(),false);assert.equal(await q('folder',prefix).inputValue(),'C:\\SnowLuma-fixture');
      await q('consent').check();assert.equal(requests.length,0);await screenshot('one-click');
      slow=true;
      await q('start').evaluate((b,p)=>{b.click();b.click();document.getElementById('snow-managed-'+p+'-start').click();},prefix);
      await has('已核对');assert.equal(imports().length,1);assert.equal(await q('start').isEnabled(),false);
      assert.deepEqual(imports()[0].body,{selection_id:'fixture-preview',account:'12345',http_id:'http1',ws_id:'ws1'});
      assert.equal(requests.filter(r=>r.path.endsWith('/managed/start')).length,0);
      // The actual MCP shortcut switches to the other actual modal, without a rescan.
      const count=requests.length;await page.locator('#mcp-open-onebot').click();await settingsDialog.waitFor({state:'visible'});
      assert.equal(await q('folder',prefix).inputValue(),'C:\\SnowLuma-fixture');assert.equal(await q('consent',prefix).isChecked(),true);
      await has('已核对',prefix);assert.equal(await q('start',prefix).textContent(),'已载入配置');assert.equal(await q('start',prefix).isEnabled(),false);
      assert.equal(requests.length,count);assert.equal(await manual.getAttribute('open'),null);assert.equal(await httpField.inputValue(),'http://127.0.0.1:4202/api');await screenshot('shared-saved');
      // Path changes invalidate consent in BOTH views; a bad path preserves settings.
      await q('folder',prefix).fill('C:\\Missing');assert.equal(await q('consent').isChecked(),false);
      failInspect=true;await q('consent',prefix).check();await q('start',prefix).click();await has('文件夹不存在',prefix);
      assert.equal(imports().length,1);assert.equal(await httpField.inputValue(),'http://127.0.0.1:4202/api');
      failInspect=false;await q('folder',prefix).fill('C:\\SnowLuma-fixture');await q('consent',prefix).check();
      // Changed server-side terms stop the pending click before any inspection/write.
      managedState={...managedState,consent_fingerprint:'changed'};const beforeTerms=requests.length;
      await q('start',prefix).click();await has('勾选',prefix);assert.equal(await q('consent',prefix).isChecked(),false);assert.equal(requests.length,beforeTerms);
      // Multiple accounts require a choice in the same flow, with shared selectors.
      multiple=true;await q('consent',prefix).check();await q('start',prefix).click();await has('多个 QQ',prefix);
      assert.equal(await q('apply',prefix).isEnabled(),false);assert.equal(imports().length,1);
      await q('account',prefix).selectOption('12345');await q('http',prefix).selectOption('http2');await q('ws',prefix).selectOption('ws2');
      await settingsDialog.evaluate(d=>d.close());await openMcp();assert.equal(await q('account').inputValue(),'12345');assert.equal(await q('http').inputValue(),'http2');
      failImport=true;await q('apply').click();await has('认证被拒绝');assert.equal(await httpField.inputValue(),'http://127.0.0.1:4202/api');
      failImport=false;await q('apply').evaluate(b=>{b.click();b.click();});await has('已核对');assert.equal(imports().length,3);
      // HTTP-only is never silently selected or saved.
      multiple=false;httpOnly=true;await q('folder').fill('C:\\HTTP-only');await q('consent').check();await q('start').click();await has('缺少实时事件');
      assert.equal(imports().length,3);assert.equal(await q('apply').isEnabled(),false);await q('ws').selectOption('');assert.ok((await q('preview').textContent()).includes('关闭原有事件连接'));
      await q('apply').click();await has('已核对');assert.equal(settings.events_url,'');
      // An unknown import outcome is not automatically retried.
      httpOnly=false;dropImport=true;await q('folder').fill('C:\\Disconnected');await q('consent').check();await q('start').click();await has('未取得操作结果');
      const dropped=imports().length;await page.waitForTimeout(2200);assert.equal(imports().length,dropped);dropImport=false;
      // A preview lost on restart can be rescanned without retyping the path.
      expired=true;await q('apply').click();await has('已过期');assert.equal(await q('existing').isVisible(),false);
      assert.equal(await q('start').isEnabled(),true);expired=false;await q('start').click();await has('已核对');
      // Manual advanced connection and model-independent save remain usable.
      await page.locator('#mcp-open-onebot').click();await settingsDialog.waitFor({state:'visible'});await manual.locator('summary').click();
      await httpField.fill('http://127.0.0.1:19002/manual');await manual.locator('button[type=submit]').click();
      await page.waitForFunction(id=>document.getElementById(id).textContent.includes('已连接'),edition==='full'?'desktop-onebot-status':'lite-onebot-result');assert.equal(settings.sender_url,'http://127.0.0.1:19002/manual');
      await manual.locator('summary').click();await q('folder',prefix).fill('C:\\Fresh-draft');
      await page.setViewportSize({width:390,height:844});await screenshot('narrow');assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
      assert.deepEqual(errors,[]);assert.ok(!requests.some(r=>Object.keys(r.body||{}).some(k=>k.endsWith('token'))&&r.path.includes('snowluma')));
      // Synthetic F5 has no browser default: navigation proves our page handler
      // works even when the desktop disables its built-in browser accelerator.
      await Promise.all([page.waitForEvent('load'),page.evaluate(()=>document.dispatchEvent(new KeyboardEvent('keydown',{key:'F5',cancelable:true})))]);
      await openMcp();assert.equal(await q('consent').isChecked(),false,'reload never carries a draft consent into a new context');
      await page.close();
    }
    console.log('PASS full/lite actual MCP + settings entrances: path -> consent -> one click; no separate load; native picker no side effect; shared path/consent/result/selections; concurrent dedup; terms/path invalidation; existing import, multi-account and HTTP-only choices, failures and UNKNOWN retention; advanced manual save; 390px. All APIs mocked; real QQ writes=0.');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
