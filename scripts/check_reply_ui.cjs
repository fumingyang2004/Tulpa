// Actual reply.js with a DOM/network fixture. Never sends to QQ; not visual QA.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync('web/index.html','utf8');
class Element{
  constructor(){this.children=[];this.events={};this.style={};this.value='';this.checked=false;this.hidden=false;this.disabled=false;this.open=false;}
  append(...xs){this.children.push(...xs);}replaceChildren(...xs){this.children=xs;}setAttribute(){}focus(){}remove(){}
  addEventListener(k,f){this.events[k]=f;}showModal(){throw Error('Reply must not open a modal');}close(){this.open=false;this.events.close?.();}
}
const nodes=new Map([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new Element()]));
const $=id=>{assert(nodes.has(id),'Missing HTML node: '+id);return nodes.get(id);};
$('chat-dialog').open=true;$('reply-panel').hidden=true;
assert(!html.includes('大肥鱼'));assert(!html.includes('id="reply-dialog"'));assert(!html.includes('id="reply-confirm-dialog"'));
assert(html.indexOf('id="reply-panel"')>html.indexOf('class="chat-conversation-pane"'));
assert.match(html,/<footer class="reply-footer"><button id="reply-approve"[^>]*>批准<\/button><button id="reply-reject"[^>]*>拒绝<\/button><\/footer>/);
const target={platform:'qq',conversation_id:'1:direct:2',conversation:'测试好友',sender:'朋友',content:'晚上打游戏吗？'};
const blank=id=>({id,message_id:1,target,instruction:'',status:'DRAFT',active_run:null,revision:0,ai_text:'',final_text:'',error:'',
  result:{},sender:{available:true,note:'本机发送接口'},generations:[]});
let stored=blank('s0'),sends=[],previews=[],copies=[],generations=[],anchors=[],serial=0,previewMismatch=false,holdPreview=null;
const clone=x=>JSON.parse(JSON.stringify(x));
async function fetch(url,options={}){
  const b=options.body?JSON.parse(options.body):{},method=options.method;let value;
  if(url.startsWith('/api/reply-receipts'))value={receipts:[]};
  else if(url==='/api/replies'){
    if(b.fresh&&['SENT','REJECTED'].includes(stored.status))stored=blank('s'+(++serial));value=stored;
  }else if(method==='GET')value=stored;
  else if(method==='PATCH'){assert.equal(b.revision,stored.revision);stored={...stored,final_text:b.text,revision:stored.revision+1};value=stored;}
  else if(url.endsWith('/preview')){
    previews.push(b);value={token:'approval',text:previewMismatch?'wrong text':stored.final_text,revision:stored.revision,quote:b.quote,
      destination:{account:'1',kind:'direct',peer:'2',name:'测试好友'}};
    if(holdPreview)await holdPreview();
  }else if(url.endsWith('/send')){
    assert.equal(b.token,'approval');assert.equal(b.confirm,true);sends.push(stored.final_text);stored={...stored,status:'SENT'};value=stored;
  }else if(url.endsWith('/generate')){
    generations.push(b);stored={...stored,instruction:b.instruction,active_run:'g',generations:[{status:'running',usage:{},result:{},events:[]}]};value=stored;
  }else if(url.endsWith('/reject')){
    assert.equal(b.revision,stored.revision);stored={...stored,status:'REJECTED',active_run:null};value=stored;
  }else throw Error(url);
  return {ok:true,json:async()=>clone(value)};
}
const timers=new Map();let timer=0;
const ctx=vm.createContext({console,$,document:{createElement:()=>new Element(),querySelector:()=>null},window:{innerWidth:1200,innerHeight:900},
 state:{visionMode:'ocr',profile:'standard',nativeVisionAvailable:true},chatPage:{platform:'qq',conversation_id:'1:direct:2'},fetch,URLSearchParams,
 navigator:{clipboard:{writeText:async t=>copies.push(t)}},setTimeout:f=>{timers.set(++timer,f);return timer;},clearTimeout:id=>timers.delete(id),toast(){},
 openChat:async mid=>{anchors.push(mid);vm.runInContext('syncReplyConversation(chatPage)',ctx);}});
vm.runInContext(fs.readFileSync('web/reply.js','utf8'),ctx);const run=s=>vm.runInContext(s,ctx);
function input(text){$('reply-text').value=text;$('reply-text').events.input();}
async function finish(){
  stored={...stored,active_run:null,revision:stored.revision+1,ai_text:'不了不了今天躺平',final_text:'不了不了今天躺平',
    result:{context_note:'程序记录本轮读取的上下文。',context_ids:[1,2],self_message_ids:[2]},generations:[{status:'completed',usage:{},result:{},events:[]}]};
  const id=run('replyTimer'),f=timers.get(id);assert(f);timers.delete(id);await f();
}
(async()=>{
  const editor=$('reply-text');await run('openReply(1)');assert(!$('reply-panel').hidden);assert.equal(sends.length,0);assert($('reply-approve').disabled);
  input('帮我婉拒');$('reply-profile').value='deep';$('reply-vision').value='native';
  await $('reply-generate').onclick();assert.deepEqual(generations[0],{instruction:'帮我婉拒',profile:'deep',vision:'native'});
  assert.equal(editor.value,'帮我婉拒');assert(editor.disabled);await finish();
  assert.equal($('reply-text'),editor);assert.equal(editor.value,'不了不了今天躺平');assert(!$('reply-approve').disabled);assert.equal(sends.length,0);
  input('不了哈哈，下次');run('renderReply(replySession)');assert.equal(editor.value,'不了哈哈，下次');
  await $('reply-evidence').children[0].onclick();assert.deepEqual(anchors,[1]);assert(!$('reply-panel').hidden);
  assert.equal(stored.ai_text,'不了不了今天躺平');assert.equal(stored.final_text,'不了哈哈，下次');
  await $('reply-adjust').onclick();assert.equal(editor.value,'帮我婉拒');assert($('reply-approve').disabled);
  input('口语一点');$('reply-back').onclick();assert.equal(editor.value,'不了哈哈，下次');
  await $('reply-copy').onclick();assert.deepEqual(copies,['不了哈哈，下次']);
  assert($('reply-address').textContent.includes('QQ 1 → 好友「测试好友」（2）'));
  // The explicit approval click sends exactly the edited text, once, without another modal.
  input('不了哈哈，明天再说');await Promise.all([$('reply-approve').onclick(),$('reply-approve').onclick()]);await $('reply-approve').onclick();
  assert.deepEqual(sends,['不了哈哈，明天再说']);assert.equal(previews.length,1);assert($('reply-approve').disabled);
  // Rejecting during generation is terminal; it must never send.
  await $('reply-new').onclick();assert.equal(editor.value,'');input('短一点');await $('reply-generate').onclick();
  await $('reply-reject').onclick();assert.equal(stored.status,'REJECTED');assert(!$('reply-new').hidden);assert.equal(sends.length,1);
  await $('reply-new').onclick();await $('reply-generate').onclick();await finish();
  // Backend preflight may not replace what the user approved.
  previewMismatch=true;await $('reply-approve').onclick();assert.equal(sends.length,1);assert($('reply-status').textContent.includes('不一致'));previewMismatch=false;
  // Closing while preflight is in flight cancels that approval before send.
  let release,entered;const waiting=new Promise(r=>entered=r);holdPreview=()=>new Promise(r=>{release=r;entered();});
  const approving=$('reply-approve').onclick();await waiting;await $('reply-close').onclick();release();await approving;holdPreview=null;
  assert.equal(sends.length,1);assert($('reply-panel').hidden);
  await run('openReply(1)');input('切换会话前的编辑');run("syncReplyConversation({platform:'qq',conversation_id:'1:direct:3'})");
  await run('saveReplyEdit()');assert($('reply-panel').hidden);assert.equal(stored.final_text,'切换会话前的编辑');
  await run('openReply(1)');await $('reply-reject').onclick();assert.equal(stored.final_text,'切换会话前的编辑');assert.equal(sends.length,1);
  // A late create response must not open a reply against the newly selected conversation.
  await run('closeReplyPanel()');const opening=run('openReply(1)');run("chatPage={platform:'wechat',conversation_id:'other'}");await opening;assert($('reply-panel').hidden);
  stored={...blank('wx'),target:{...target,platform:'wechat',conversation_id:'other'},sender:{available:false,note:'微信仅生成和复制'}};
  await run('openReply(1)');await $('reply-generate').onclick();await finish();assert($('reply-approve').disabled);assert(!$('reply-copy').disabled);
  console.log('PASS DOM fixture: inline single-editor stages/config, evidence, edit persistence, exact one-click approval/no modal, reject/cancel, duplicate clicks, preflight mismatch, close/switch safety and WeChat copy. No real sends or visual QA.');
})().catch(e=>{console.error(e);process.exitCode=1;});
