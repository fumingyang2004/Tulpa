// Actual frontend functions with a small DOM/network fixture, not a browser test.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map();
function element(id){if(!nodes.has(id))nodes.set(id,{value:'',disabled:false,hidden:false,textContent:'',style:{setProperty(){}},
  setAttribute(){},querySelectorAll(){return[];},querySelector(){return element('child');},focus(){},classList:{toggle(){}},replaceChildren(){}});return nodes.get(id);}
const ctx=vm.createContext({console,assert,crypto:require('node:crypto').webcrypto,AbortSignal,
  setTimeout,clearTimeout,matchMedia:()=>({matches:false}),
  document:{getElementById:element,querySelectorAll:()=>[],body:{classList:{toggle(){}}}},
  localStorage:{setItem(){},getItem(){return null;}}});
vm.runInContext(fs.readFileSync('web/app.js','utf8').split("$('composer').addEventListener")[0],ctx);
vm.runInContext(fs.readFileSync('web/watch.js','utf8').split('function renderWatchChoices()')[0]+'\nlet autoRefreshSaving=false;',ctx);
vm.runInContext(`
  renderMessages=renderHistory=fitInput=focusComposer=setData=()=>{};
  fetchWatchThread=async()=>{};loadWatches=async()=>{};
  state.ready=true;$('question').value='草稿';
  watchJobs.set('refresh',{id:'refresh',kind:'sync',status:'running'});syncControls();
  assert.equal($('question').disabled,false);assert.equal($('send-button').disabled,false);
  assert.equal($('read-latest').disabled,true);
  state.watchId='card';syncControls();assert.equal($('send-button').disabled,false);
  watchJobs.set('check',{id:'check',kind:'check',card_id:'card',status:'running'});syncControls();
  assert.equal($('question').disabled,false);assert.equal($('send-button').disabled,true);
  state.watchId=null;syncControls();assert.equal($('send-button').disabled,false);
  watchJobs.clear();watchJobs.set('delete',{kind:'delete',status:'running'});syncControls();
  assert.equal($('question').disabled,false);assert.equal($('send-button').disabled,true);
  watchJobs.clear();syncControls();
  const countCard={id:'count',status:'active',last_checked_at:'2026-09-24',message_threshold:100,trigger_messages:23,schedule_minutes:0};
  assert.equal(watchFrequency(countCard),'每 100 条新消息');
  assert.equal(watchCountdown(countCard),'消息累计 23 / 100 条');
  const combined={...countCard,schedule_minutes:1440,next_run_at:1000};
  assert.equal(watchFrequency(combined),'每天 或 每 100 条新消息');
  assert.equal(watchCountdown(combined,900000),'还有 1 分 40 秒 · 消息累计 23 / 100 条');
  assert.equal(watchCountdown({...countCard,status:'paused'}),'已暂停');
  assert.equal(watchFrequency({...countCard,message_threshold:0}),'仅手动');
  assert.equal(watchCaption({...countCard,pending_messages:283,last_result:{checked:40,pending:283}}),'283 条新消息 · 待下次更新');
  assert.equal(watchCaption({...countCard,pending_messages:0,last_result:{mode:'scheduled_question',answer:{claims:[]}}}),'已更新回答');
`,ctx);
(async()=>{
  await vm.runInContext(`(async()=>{
    const actualCall=call;let paths=[];
    fetch=async(path)=>{paths.push(path);return {ok:true,json:async()=>({data:['state',{}]})};};
    state.functions.refresh=1;await actualCall('refresh',[]);
    assert.equal(paths.length,1);assert.equal(paths[0],'/legacy/gradio_api/run/refresh');
    // Acknowledgement and final callbacks must not erase the next question.
    call=async(name,args,update)=>{
      assert.equal(name,'agent_action_controls');
      const r=[];r[7]={value:''};update(r,false);
      $('question').value='正在编辑下一问';update(r,true);return r;
    };
    await sendQuestion({preventDefault(){}});assert.equal($('question').value,'正在编辑下一问');
    // Completing a sync must not clear the independently running watch answer.
    call=async()=>['',{}];let refreshDone=false,answerDone=false;
    watchApi=async path=>({id:path.split('/').at(-1),kind:path.endsWith('/s')?'sync':'question',card_id:path.endsWith('/q')?'card':null,
      status:(path.endsWith('/s')?refreshDone:answerDone)?'completed':'running',events:[],result:{summary:'完成'}});
    state.watchId='card';const a=monitorWatchJob('s',{kind:'sync'}),b=monitorWatchJob('q',{kind:'question',card_id:'card'});
    await new Promise(r=>setTimeout(r,20));assert.equal(watchJobs.size,2);
    refreshDone=true;await a;assert.equal(watchJobs.size,1);assert.equal(watchJob,'q');
    assert.equal($('question').disabled,false);assert.equal($('send-button').disabled,true);
    answerDone=true;await b;assert.equal(watchJobs.size,0);assert.equal($('send-button').disabled,false);
  })()`,ctx);
  const html=fs.readFileSync('web/index.html','utf8');
  for(const id of ['load-stickers','live-stickers','watch-stickers','auto-refresh-stickers']){
    const tag=html.match(new RegExp('<input[^>]*id="'+id+'"[^>]*>'))[0];assert(!/\bchecked\b/.test(tag));
  }
  assert(!html.includes('id="live-reconcile"'));
  console.log('PASS: real JS controls allow sync + dialogue/drafts, scope destructive jobs, preserve next draft, independent job completion, status reads avoid SSE queue, all sticker switches default off. DOM fixture, not browser visual QA.');
})().catch(e=>{console.error(e);process.exitCode=1;});
