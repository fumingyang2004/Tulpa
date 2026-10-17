// Real browser layout with synthetic APIs; no QQ connection or model calls.
const {chromium}=require('playwright-core');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../..'),web=path.join(root,'web/desktop');
const origin='http://127.0.0.1:39880';
(async()=>{
 const {responses}=await import('./fixtures.mjs');
 const launch={headless:true};
 if(process.env.BROWSER_EXECUTABLE)launch.executablePath=process.env.BROWSER_EXECUTABLE;
 else if(process.platform==='win32')launch.channel='msedge';
 if(process.platform==='linux')launch.args=['--no-sandbox'];
 const browser=await chromium.launch(launch);
 try{
  const page=await browser.newPage({viewport:{width:1280,height:850},colorScheme:'dark'}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route(origin+'/**',async route=>{
   const url=new URL(route.request().url());
   if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(web,'index.html'),'utf8'),headers:{'Content-Security-Policy':"default-src 'self';script-src 'self';style-src 'self' 'unsafe-inline';img-src 'self' data:;connect-src 'self';frame-ancestors 'none';base-uri 'none'"}});
   if(url.pathname==='/ui/csp-probe.js')return route.fulfill({contentType:'text/javascript',body:"try{eval('1+1');window.__cspBlocked=false}catch(e){window.__cspBlocked=e instanceof EvalError}"});
   if(url.pathname.startsWith('/ui/')){
    const name=path.basename(url.pathname),file=path.join(web,name);
    if(!fs.existsSync(file))return route.fulfill({status:404,body:'missing'});
    return route.fulfill({contentType:name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':'image/png',body:fs.readFileSync(file)});
   }
   if(url.pathname==='/legacy/gradio_api/queue/join')return route.fulfill({json:{event_id:'fixture'}});
   if(url.pathname==='/legacy/gradio_api/queue/data')return route.fulfill({contentType:'text/event-stream',body:'data: '+JSON.stringify({msg:'process_completed',event_id:'fixture',success:true,output:{data:[{choices:[],value:null}]}})+'\n\n'});
   let result=responses(url.pathname);
   if(url.pathname==='/api/desktop/snowluma/managed')result={data:{ok:true,enabled:true,backend:'docker',supported_backends:['docker'],phase:'waiting_login',login_available:true,has_consent:true,consent_current:true}};
   return route.fulfill({status:result.status||200,json:result.data});
  });
  await page.goto(origin);await page.waitForSelector('.home-page');
  const go=route=>page.evaluate(async route=>{const m=await import('/ui/app.js');history.replaceState(null,'','#/'+route);await m.renderRoute({force:true})},route);
  for(const route of ['home','chat','chats','watches','files','collections','memory','workspaces','data','agents','settings']){
   await go(route);const text=await page.locator('#view-root').innerText();assert(!/这个页面暂时无法打开|前端未能启动/.test(text),route+': '+text);
  }
  await go('home');
  const width=async min=>assert((await page.locator('#main').boundingBox()).width>=min);
  for(let n=0;n<3;n++){await page.click('#sidebar-collapse');await width(1279);await page.click('#menu-button');await width(1000)}
  await page.click('#sidebar-collapse');await page.reload();await page.waitForSelector('.home-page');await width(1279);
  await page.setViewportSize({width:390,height:820});await width(389);await page.click('#menu-button');await page.waitForTimeout(250);
  assert(await page.locator('#app-sidebar').isVisible());assert((await page.locator('#app-sidebar').boundingBox()).x>=-1);
  await page.locator('#sidebar-nav [data-route="home"]').click();await page.setViewportSize({width:1280,height:850});
  if(await page.locator('body').evaluate(n=>n.classList.contains('sidebar-collapsed')))await page.click('#menu-button');
  for(const theme of ['light','dark']){
   await page.evaluate(async t=>(await import('/ui/app.js')).setAppearance(t),theme);
   const logo=await page.locator('.brand-mark img').evaluate(async n=>{await n.decode();const b=n.getBoundingClientRect();return {ratio:b.width/b.height,natural:n.naturalWidth/n.naturalHeight,filter:getComputedStyle(n).filter}});
   assert(Math.abs(logo.ratio-logo.natural)<.02);assert.equal(logo.filter,'none');
  }
  await page.evaluate(()=>import('/ui/csp-probe.js'));
  assert.equal(await page.evaluate(()=>window.__cspBlocked),true,'page scripts must obey CSP');
  await page.evaluate(()=>{window.__mode='error';window.__calls=0;window.TulpaDesktopUI={appearance:'dark',call:async action=>{
   if(action!=='open_qq_login')return 'dark';window.__calls++;
   if(window.__mode==='error')throw Error('fixture browser failure');
   return {ok:true,novnc_url:'http://127.0.0.1:6080/?resize=scale',password:'fixture-password',browser_opened:window.__mode==='success',browser_error:'fixture open unavailable'};
  }};window.dispatchEvent(new Event('tulpa-native-ready'))});
  await go('settings?section=snowluma');await page.getByRole('button',{name:'打开扫码登录',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('.snow-feedback')?.textContent.includes('fixture browser failure'));
  await page.waitForTimeout(2200);assert.match(await page.locator('.snow-feedback').innerText(),/fixture browser failure/);
  await page.evaluate(()=>window.__mode='failed-browser');await page.getByRole('button',{name:'打开扫码登录',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('[aria-label="QQ 桌面登录地址"]')?.value.includes('6080'));
  assert.equal(await page.locator('[aria-label="VNC 密码"]').getAttribute('type'),'password');
  await page.evaluate(()=>window.__mode='success');const calls=await page.evaluate(()=>window.__calls);
  await page.getByRole('button',{name:'在浏览器中打开 QQ 桌面',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('.snow-feedback')?.textContent.includes('已通过系统浏览器'));
  assert.equal(await page.evaluate(()=>window.__calls),calls+1);
  await page.getByRole('button',{name:'隐藏登录信息',exact:true}).click();assert.equal(await page.locator('[aria-label="VNC 密码"]').inputValue(),'');
  assert.deepEqual(errors,[]);console.log('PASS real browser: 11 routes, sidebar collapse/reload/mobile, logo themes, CSP, native VNC errors/retry and credential clearing');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
