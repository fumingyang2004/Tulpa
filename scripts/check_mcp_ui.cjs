// Real browser + actual MCP frontend, isolated API fixtures; no personal grants or model calls.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright-core');
const packageArg=process.argv.indexOf('--package');
if(packageArg>=0&&!process.argv[packageArg+1])throw new Error('--package needs a directory');
const root=path.resolve(packageArg<0?path.join(__dirname,'..'):process.argv[packageArg+1]);
const html=`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>MCP UI fixture</title>
<link rel="stylesheet" href="/app.css"><link rel="stylesheet" href="/mcp.css"><link rel="stylesheet" href="/tulpa.css"><link rel="stylesheet" href="/desktop.css"><button id="open-mcp">外部 Agent / MCP</button><button id="open-data">导入</button><script src="/mcp.js"></script></html>`;
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1100,height:920}}),errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    let state,requests,mode,failList=false,delayCreate=false,sessions=[],overviewError=false,libraryError=false,libraryDelay=false,learningState='积累中',learning={enabled:true};
    let connections=[];
    const group=()=>({conversation_id:'111:group:444',account:'111',group:'444',name:'合成测试群',active:sessions.filter(s=>s.active),history:sessions.filter(s=>!s.active),learning,learning_state:learningState,can_start:!sessions.some(s=>s.active),connection_id:'fixture-connection'});
    const shot=async name=>{if(process.env.AUTO_LEARNING_SCREENSHOT_DIR){fs.mkdirSync(process.env.AUTO_LEARNING_SCREENSHOT_DIR,{recursive:true});await page.screenshot({path:path.join(process.env.AUTO_LEARNING_SCREENSHOT_DIR,name+'.png')});}};
    await page.route('http://127.0.0.1:39879/**',async route=>{
      const r=route.request(),url=new URL(r.url()),method=r.method();
      if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:html});
      if(['/mcp.js','/mcp.css','/app.css','/tulpa.css','/desktop.css'].includes(url.pathname))return route.fulfill({contentType:url.pathname.endsWith('.js')?'text/javascript':'text/css',body:fs.readFileSync(path.join(root,'web',url.pathname.slice(1)),'utf8')});
      const reply=(data,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
      if(url.pathname==='/api/mcp'&&method==='GET'){
        if(failList&&requests.some(x=>x.path.endsWith('/connections')))return reply({detail:'fixture list unavailable'},503);
        return reply({...state,connections,recent:[],data_platforms:{qq:3,wechat:2},onebot_configured:false});
      }
      if(url.pathname==='/api/mcp/conversations')return reply({items:[{platform:'qq',conversation_id:'111:group:222',name:'测试会话',count:3}],has_more:false,next_offset:80});
      if(url.pathname==='/api/mcp/live-groups')return reply({items:[{platform:'qq',conversation_id:'111:group:444',name:'未导入的实时群',count:'OneBot'}]});
      if(url.pathname==='/api/desktop/preferences')return reply({background:false});
      if(url.pathname==='/api/desktop/status')return reply({owned:true});
      if(url.pathname==='/api/mcp/chats'&&method==='GET')return reply({sessions,receiver:{state:'connected',note:'SnowLuma 实时事件已连接。'}});
      if(url.pathname==='/api/mcp/chat-overview')return overviewError?reply({detail:'合成连接暂不可用，请稍后重试'},503):reply({groups:sessions.length?[group()]:[],receiver:{state:'connected',note:'SnowLuma 实时事件已连接。'}});
      if(url.pathname==='/api/mcp/chat-library'&&method==='GET'){
        if(libraryDelay)await new Promise(r=>setTimeout(r,700));
        return libraryError?reply({detail:'合成记录读取失败'},503):reply(learning);
      }
      if(url.pathname==='/api/mcp/chat-personas')return reply({personas:[{id:'little_whale',name:'小鲸鱼'},{id:'little_whale_v2',name:'小鲸鱼2号'}]});
      if(url.pathname.endsWith('/learning')&&method==='GET')return reply(learning);
      const body=r.postDataJSON();requests.push({path:url.pathname,method,body});
      if(url.pathname==='/api/mcp/chat-library'&&method==='POST'){
        const change=body.change;
        if(change.kind)learning.expressions[0].enabled=change.enabled;else if(change.hourly_calls)learning.hourly_call_budget=change.hourly_calls;else learning.enabled=change.enabled;
        return reply(learning);
      }
      if(url.pathname==='/api/mcp/chats/start'){
        sessions.push({id:'fixture-new-chat',name:'测试群',connection_name:'qqmcp',active:true,persona_name:'小鲸鱼2号',persona:'合成人物提示',turn_process:{phase:'WAITING'}});
        learningState='等待 Agent';return reply({session_id:'fixture-new-chat',note:'已开启会话并接续原群积累。请在已连接的 Agent 中开始或继续该群聊天。'});
      }
      if(url.pathname.endsWith('/learning')&&method==='PUT'){
        learning={...learning,...body,expression_count:1,usable_expressions:body.allow_degraded?1:0,jargon_count:0,confirmed_jargon:0,pending:[],last_learned:0,calls_last_hour:2,hourly_call_budget:20,
          expressions:[{id:'synthetic-expression',situation:'<img src=x onerror=alert(1)>',style:'合成抽象表达',count:1,enabled:true,independence:'degraded'}]};return reply(learning);
      }
      if(url.pathname.endsWith('/learning/record')&&method==='POST'){
        learning.expressions[0].enabled=body.enabled;return reply(learning);
      }
      if(url.pathname.startsWith('/api/mcp/chats/')&&method==='POST'){
        const sid=url.pathname.split('/')[4];sessions=sessions.map(s=>sid==='all'||s.id===sid?{...s,active:false,state:'stopped'}:s);return reply({stopped:1});
      }
      if(url.pathname==='/api/mcp'&&method==='PUT'){
        state={...state,...body,running:body.enabled&&mode!=='conflict',error:mode==='conflict'?'MCP 端口无法使用，请更换端口。':'',url:`http://127.0.0.1:${body.port}/mcp`};return reply(state);
      }
      if(url.pathname==='/api/mcp/connections'&&method==='POST'){
        if(delayCreate)await new Promise(resolve=>setTimeout(resolve,150));
        if(mode==='reject')return reply({detail:'fixture invalid source'},400);
        return reply({id:'fixture-only',token:'fixture-only-not-a-real-credential'});
      }
      throw Error('Unexpected fixture request: '+method+' '+url.pathname);
    });
    const waitText=(id,text)=>page.waitForFunction(([id,text])=>document.getElementById(id).textContent.includes(text),[id,text]);
    async function setup(running=false){
      state={enabled:running,running,port:18777,url:'http://127.0.0.1:18777/mcp',error:''};requests=[];mode='ok';failList=false;delayCreate=false;sessions=[];connections=[];
      await page.goto('http://127.0.0.1:39879/');await page.locator('#open-mcp').click();await page.locator('#mcp-chats input').waitFor();
      await page.locator('#mcp-chat-advanced').evaluate(e=>e.open=true);
      await page.locator('#mcp-name').fill('qqmcp');await page.locator('#mcp-all').check();
      await page.locator('#mcp-start').fill('2026-09-01');await page.locator('#mcp-end').fill('2026-10-03');
    }
    async function create(){await page.locator('#mcp-create').click();await page.locator('#mcp-secret').waitFor({state:'visible'});await page.waitForFunction(()=>!document.getElementById('mcp-create').disabled);}
    // The reported grey-button case: stopped service has an explicit, usable action.
    await setup();assert.equal(await page.locator('#mcp-enabled').isVisible(),false);assert.equal(await page.locator('#mcp-create').isEnabled(),true);await waitText('mcp-create','开启服务并创建连接');
    if(process.env.MCP_UI_SCREENSHOT){await page.locator('#mcp-create').scrollIntoViewIfNeeded();await page.screenshot({path:process.env.MCP_UI_SCREENSHOT});}
    await create();
    assert.deepEqual(requests.map(x=>x.method+' '+x.path),['PUT /api/mcp','POST /api/mcp/connections']);
    assert.deepEqual(requests[1].body,{name:'qqmcp',platforms:['qq','wechat'],conversations:[],all_conversations:true,start:'2026-09-01',end:'2026-10-03',media:false,prepare:false,voice:false,onebot:false,send:false,manage:false,chat:false,chat_reactions:false,chat_images:false,chat_sticker_send:false,chat_sticker_collect:false});
    // Stale offline UI must recheck; it should not reconfigure an already running service.
    await setup();state.running=state.enabled=true;await create();assert.equal(requests.length,1);assert.equal(requests[0].method,'POST');
    // Stale online UI must ask explicitly before turning a now stopped service back on.
    await setup(true);state.running=state.enabled=false;await page.locator('#mcp-create').click();await waitText('mcp-create-status','尚未运行');
    assert.equal(requests.length,0);await waitText('mcp-create','开启服务并创建连接');await create();assert.equal(requests.length,2);
    // Conflict: show the failure next to the button, keep the form, and allow a port change.
    await setup();mode='conflict';await page.locator('#mcp-create').click();await waitText('mcp-create-status','端口无法使用');
    assert.equal(requests.length,1);assert.equal(await page.locator('#mcp-create').isEnabled(),true);assert.equal(await page.locator('#mcp-name').inputValue(),'qqmcp');
    assert.equal(await page.locator('#mcp-start').inputValue(),'2026-09-01');await page.locator('#mcp-edit-port').click();assert.equal(await page.locator('#mcp-port').evaluate(e=>e===document.activeElement),true);
    await page.locator('#mcp-port').fill('18778');mode='ok';await create();assert.equal(requests[1].body.port,18778);assert.equal(await page.locator('#mcp-url').inputValue(),'http://127.0.0.1:18778/mcp');
    // Scope mistakes must not enable a listener or silently grant all chats.
    await setup();await page.locator('#mcp-all').uncheck();await page.locator('#mcp-create').click();await waitText('mcp-create-status','请选择会话');assert.equal(requests.length,0);
    await page.locator('#mcp-all').check();await page.locator('#mcp-end').fill('2026-08-01');await page.locator('#mcp-create').click();await waitText('mcp-create-status','截止日期');assert.equal(requests.length,0);
    // Pending submissions are single-flight, including Enter/programmatic submit.
    await setup(true);delayCreate=true;await page.locator('#mcp-create-form').evaluate(form=>{form.requestSubmit();form.requestSubmit();});
    await page.locator('#mcp-secret').waitFor({state:'visible'});assert.equal(requests.length,1);
    // List refresh errors must not discard the one-time credential after successful creation.
    await setup(true);failList=true;await create();await waitText('mcp-create-status','连接已创建');assert.equal(await page.locator('#mcp-token').inputValue(),'fixture-only-not-a-real-credential');
    await setup(true);mode='reject';await page.locator('#mcp-create').click();await waitText('mcp-create-status','fixture invalid source');
    assert.equal(await page.locator('#mcp-secret').isVisible(),false);assert.equal(await page.locator('#mcp-create').isEnabled(),true);
    // Dedicated permission requires send, and clearing send clears chat.
    await setup(true);assert.equal(await page.locator('#mcp-chat').isChecked(),false);
    assert.equal(await page.locator('#mcp-chat_images').isEnabled(),false);
    assert.equal(await page.locator('#mcp-chat_reactions').isEnabled(),false);
    await page.locator('#mcp-chat').check();assert.equal(await page.locator('#mcp-send').isChecked(),true);
    assert.equal(await page.locator('#mcp-chat_images').isEnabled(),true);
    assert.equal(await page.locator('#mcp-chat_reactions').isEnabled(),true);
    assert.equal(await page.locator('#mcp-chat_reactions').isChecked(),false);
    await page.locator('#mcp-chat_reactions').check();
    assert.equal(await page.locator('#mcp-chat_sticker_send').isEnabled(),false);
    await page.locator('#mcp-chat_images').check();await page.locator('#mcp-chat_sticker_send').check();await page.locator('#mcp-chat_sticker_collect').check();
    await page.locator('#mcp-send').uncheck();assert.equal(await page.locator('#mcp-chat').isChecked(),false);
    assert.equal(await page.locator('#mcp-chat_sticker_send').isChecked(),false);
    assert.equal(await page.locator('#mcp-chat_reactions').isChecked(),false);
    assert.equal(await page.locator('#mcp-chat_sticker_collect').isEnabled(),false);
    await page.locator('#mcp-chat').check();await page.locator('#mcp-chat_images').check();await page.locator('#mcp-chat_sticker_send').check();await create();assert.equal(requests[0].body.chat,true);
    assert.equal(requests[0].body.chat_images,true);assert.equal(requests[0].body.chat_sticker_send,true);assert.equal(requests[0].body.chat_sticker_collect,false);
    assert.ok((await page.locator('#mcp-config').inputValue()).includes('tools.stop_chat_session'));
    assert.ok((await page.locator('#mcp-config').inputValue()).includes('tools.send_chat_sticker'));
    assert.ok(!(await page.locator('#mcp-config').inputValue()).includes('tools.collect_chat_sticker'));
    assert.equal(requests[0].body.chat_reactions,false);
    await setup(true);await page.locator('#mcp-chat').check();await page.locator('#mcp-chat_reactions').check();await create();
    assert.equal(requests[0].body.chat_reactions,true);assert.equal(requests[0].body.chat_images,false);
    assert.ok((await page.locator('#mcp-config').inputValue()).includes('tools.react_to_chat_message'));
    await setup(true);await page.locator('#mcp-all').uncheck();await page.locator('#mcp-live-groups').click();
    await waitText('mcp-chats','未导入的实时群');await page.locator('#mcp-chat-refresh').click();
    await waitText('mcp-event-status','SnowLuma 实时事件已连接');
    await page.locator('#mcp-search').fill('实时');await waitText('mcp-chats','未导入的实时群');
    await page.locator('#mcp-chats input').check();await page.locator('#mcp-chat').check();await create();
    assert.deepEqual(requests[0].body.conversations,[['qq','111:group:444']]);
    // Display literal persona, preserve expanded details across polling, stop one/all.
    learning={enabled:true,independence:'degraded',library:{checked_expressions:4,known_jargon:0,observing_jargon:17,last_completed_at:null},run:{completed_batches:0,buffered:8},calls_last_hour:2,hourly_call_budget:20,expression_count:4,jargon_count:17,
      expressions:[{id:'synthetic-expression',situation:'<img src=x onerror=alert(1)> 合成适用场景',style:'合成抽象表达方式；长文本自动换行。'.repeat(8),count:1,enabled:true,independence:'degraded'}],jargon:[{id:'synthetic-word',term:'云朵开机',meaning:'',enabled:true,is_jargon:false,count:1,independence:'degraded'}]};
    sessions=[{id:'fixture-chat',name:'测试群',connection_name:'qqmcp',persona_name:'小鲸鱼2号',active:true,state:'waiting_messages',persona:'<img src=x onerror=alert(1)> 自然聊天',participation:'natural',cursor:12}];
    await page.locator('#mcp-chat-refresh').click();await waitText('mcp-chat-status','1 个');
    await page.locator('#mcp-settings').evaluate(e=>e.scrollTop=0);await shot('01-lurking-threshold');
    await waitText('mcp-chat-sessions','4/10');await waitText('mcp-chat-sessions','未记录');
    await page.locator('#mcp-chat-sessions summary').click();assert.equal(await page.locator('#mcp-chat-sessions img').count(),0);
    await page.locator('#mcp-chat-refresh').click();await page.waitForTimeout(100);assert.equal(await page.locator('#mcp-chat-sessions details').getAttribute('open'),'');
    libraryDelay=true;await page.getByRole('button',{name:'查看学习记录',exact:true}).click();
    const learningDialog=page.locator('dialog.mcp-learning');await learningDialog.waitFor({state:'visible'});
    await learningDialog.getByText('正在加载学习记录…',{exact:true}).waitFor();await shot('09-loading');libraryDelay=false;
    await learningDialog.getByRole('button',{name:'停用此条'}).first().waitFor();assert.equal(await learningDialog.locator('img').count(),0);
    assert.equal(await learningDialog.getByRole('checkbox').count(),0); // Advanced controls are collapsed.
    await shot('02-records');
    await learningDialog.getByRole('button',{name:'停用此条'}).first().click();await learningDialog.getByRole('button',{name:'启用此条'}).waitFor();
    await learningDialog.getByText('高级设置与检查详情',{exact:true}).click();
    await learningDialog.getByRole('checkbox').check();await page.waitForFunction(()=>document.querySelector('dialog.mcp-learning pre').textContent.includes('degraded'));
    assert.equal(learning.enabled,false);
    await learningDialog.getByRole('checkbox').uncheck();await page.waitForTimeout(100);assert.equal(learning.enabled,true);
    await learningDialog.getByRole('spinbutton',{name:'每小时学习阶段预算'}).fill('8');await learningDialog.getByRole('button',{name:'保存预算'}).click();await page.waitForTimeout(100);assert.equal(learning.hourly_call_budget,8);
    libraryError=true;await learningDialog.getByText(/合成记录读取失败/).waitFor();assert.equal(await learningDialog.locator('.mcp-learning-record').count(),2);await shot('10-record-error');
    libraryError=false;await learningDialog.getByText(/合成记录读取失败/).waitFor({state:'hidden'});
    if(process.env.LEARNING_UI_SCREENSHOT)await learningDialog.screenshot({path:process.env.LEARNING_UI_SCREENSHOT});
    await learningDialog.getByRole('button',{name:'关闭',exact:true}).click();
    learningState='学习中';await page.locator('#mcp-chat-refresh').click();await waitText('mcp-chat-sessions','学习中');await page.locator('#mcp-settings').evaluate(e=>e.scrollTop=0);await shot('03-learning');
    await page.locator('[data-chat-stop]').click();await waitText('mcp-chat-sessions','已停止');
    assert.equal(await page.locator('#mcp-chat-stop-all').isEnabled(),false);
    await page.locator('#mcp-settings').evaluate(e=>e.scrollTop=0);await shot('04-stopped-retained');
    connections=[{id:'fixture-connection',name:'DSH 合成连接',revoked:false,accounts:{qq:'111'},scope:{platforms:['qq'],conversations:[['qq','111:group:444']],chat:true,send:true,chat_account:'111'}}];
    await page.locator('#mcp-chat-start').click();const startDialog=page.locator('dialog.mcp-chat-start-dialog');await startDialog.getByRole('combobox',{name:'聊天群'}).waitFor();
    assert.equal(await startDialog.getByRole('combobox',{name:'人物'}).inputValue(),'little_whale_v2');await shot('05-start');
    await startDialog.getByRole('button',{name:'开始持续水群'}).click();await startDialog.getByText('已开启会话并接续原群积累。请在已连接的 Agent 中开始或继续该群聊天。').waitFor();
    await startDialog.getByRole('button',{name:'关闭',exact:true}).click();await waitText('mcp-chat-sessions','等待 Agent');
    assert.equal(await page.locator('.mcp-group-card').count(),1);assert.equal(await page.locator('[data-chat-stop]').count(),1);
    await page.setViewportSize({width:390,height:844});await page.locator('#mcp-settings').evaluate(e=>e.scrollTop=0);await shot('06-narrow-resumed');
    assert.ok(await page.locator('#mcp-settings').evaluate(e=>e.scrollWidth<=e.clientWidth+1));
    overviewError=true;await page.locator('#mcp-chat-refresh').click();await waitText('mcp-chat-status','暂时无法更新');assert.equal(await page.locator('.mcp-group-card').count(),1);await shot('07-error-retained');
    overviewError=false;await waitText('mcp-chat-status','1 个'); // Normal automatic refresh, no save/click.
    await page.locator('#mcp-chat-stop-all').click();await waitText('mcp-chat-sessions','已停止');
    sessions=[];await page.locator('#mcp-chat-refresh').click();await waitText('mcp-chat-sessions','还没有持续水群记录');await page.locator('#mcp-settings').evaluate(e=>e.scrollTop=0);await shot('08-empty');
    assert.deepEqual(errors,[]);
    console.log('PASS: real Edge + actual MCP UI; existing connection/permission regressions, start/resume, grouped library, threshold/unknown time, records, pause, literal long content, learning/stop, auto refresh, error recovery, empty/narrow. API fixtures only.');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
