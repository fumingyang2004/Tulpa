// Shipped JS with deterministic DOM geometry/network. Not a browser visual test.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element {
  constructor(tag='div'){this.tagName=tag;this.children=[];this.dataset={};this.style={};this.events={};this.value='';this.hidden=false;this._scroll=0;this.clientHeight=400;this.classList={toggle(){}};}
  set textContent(v){this.text=String(v);this.children=[];} get textContent(){return this.text||this.children.map(c=>c.textContent).join('');}
  append(...xs){for(const x of xs){x.remove?.();this.children.push(x);x.parent=this;}}
  replaceChildren(...xs){for(const c of this.children)c.parent=null;this.children=[];this.append(...xs);}
  insertBefore(n,b){n.remove();let i=this.children.indexOf(b);if(i<0)i=this.children.length;this.children.splice(i,0,n);n.parent=this;}
  replaceWith(n){const p=this.parent,i=p.children.indexOf(this);p.children[i]=n;n.parent=p;this.parent=null;}
  remove(){if(this.parent){const p=this.parent;p.children.splice(p.children.indexOf(this),1);this.parent=null;}}
  setAttribute(k,v){this[k]=v;} addEventListener(k,f){this.events[k]=f;}
  showModal(){this.open=true;} close(){this.open=false;this.events.close?.();}
  get scrollHeight(){return this.children.length*100;} get scrollTop(){return this._scroll;}
  set scrollTop(v){this._scroll=Math.max(0,Math.min(v,this.scrollHeight-this.clientHeight));}
  get offsetHeight(){return 100;}
  getBoundingClientRect(){const top=this.parent?this.parent.children.indexOf(this)*100-this.parent.scrollTop:0;return {top,bottom:top+100};}
}
const nodes=new Map(),el=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
const doc={hidden:false,createElement:t=>new Element(t),events:{},addEventListener(k,f){this.events[k]=f;}};
let rows=Array.from({length:20},(_,i)=>({id:i+1,platform:'qq',conversation_id:'course',conversation:'课程群',sender:'老师',is_self:0,
  timestamp:1000+i,content:i===5?'<script>not executable</script>':'消息 '+i,time:'2026-09-24 10:00',media:[],files:[]}));
let sequence=1,requests=[],hold=null;
const makePage=(messages=rows)=>({platform:'qq',conversation_id:'course',conversation:'课程群',anchor_message_id:messages.at(-1)?.id,messages,
  epoch:'fixture',high_water:sequence,has_before:messages[0]?.id>1,has_after:messages.at(-1)?.id!==rows.at(-1)?.id,
  latest_message_id:rows.at(-1)?.id,latest_timestamp:rows.at(-1)?.timestamp});
async function fetch(path){
  const url=new URL(path,'http://localhost'),q=url.searchParams;requests.push(path);
  let data;
  if(url.pathname==='/api/chat-conversations')data={conversations:[{platform:'qq',conversation_id:'course',conversation:'课程群',sender:'老师',preview:rows.at(-1).content,timestamp:2000,time:'2026-09-24 10:00'}],epoch:'fixture',high_water:sequence,has_more:false};
  else if(url.pathname==='/api/chat-context'){
    if(q.get('conversation_id')==='other')data={...makePage([]),platform:'wechat',conversation_id:'other',conversation:'微信好友',has_after:false};
    else if(q.has('anchor_message_id'))data=makePage(rows.slice(0,10));
    else data=makePage();
  }else if(url.pathname==='/api/chat-updates'){
    data=q.get('follow')==='true'?makePage():makePage(rows.filter(m=>m.timestamp>=Number(q.get('first_timestamp'))&&m.timestamp<=Number(q.get('last_timestamp'))));
    if(hold){const cb=hold;hold=null;await cb();}
  }else throw Error(path);
  return {ok:true,json:async()=>JSON.parse(JSON.stringify(data))};
}
let timer=0;const timers=new Set();
const ctx=vm.createContext({console,assert,$:el,document:doc,fetch,AbortSignal,URLSearchParams,
  renderVoice:()=>new Element('audio'),setTimeout:()=>{timers.add(++timer);return timer;},clearTimeout:id=>timers.delete(id)});
vm.runInContext(fs.readFileSync('web/chat.js','utf8'),ctx);
const run=s=>vm.runInContext(s,ctx),add=()=>{rows=[...rows,{...rows.at(-1),id:rows.length+1,timestamp:1000+rows.length,content:'新消息 '+rows.length}];sequence++;};
(async()=>{
  await run("openChat(null,['qq','course'])");
  assert.equal(el('chat-timeline').children.length,20);assert.equal(el('chat-timeline').scrollTop,1600);
  assert(el('chat-conversations').children[0].textContent.includes('消息 19'));
  assert.equal(el('chat-conversations').children[0]['aria-current'],'true');
  const old=el('chat-timeline').children[5];assert(old.textContent.includes('<script>not executable</script>'));
  add();await run('tickChatBrowser()');
  assert.equal(el('chat-timeline').children.length,21);assert.equal(el('chat-timeline').scrollTop,1700);
  assert.equal(el('chat-timeline').children[5],old,'Unchanged message/media node replaced');
  // Read older messages: arrival must neither replace the window nor pull scroll.
  el('chat-timeline').scrollTop=400;el('chat-timeline').events.scroll();add();await run('tickChatBrowser()');
  assert.equal(el('chat-timeline').scrollTop,400);assert.equal(el('chat-timeline').children.length,21);
  assert(!el('chat-latest').hidden);assert.equal(el('chat-latest').textContent,'有新消息 · 查看最新');
  assert(el('chat-conversations').children[0].textContent.includes('新消息 21'));
  await run("openChat(null,['qq','course'],50,0,true)");
  assert.equal(el('chat-timeline').children.length,22);assert.equal(el('chat-timeline').scrollTop,1800);
  // A user scroll while a tail request is in flight wins over auto-follow.
  let release;hold=()=>new Promise(r=>release=r);add();const moving=run('pollChatWindow()');
  await Promise.resolve();el('chat-timeline').scrollTop=500;el('chat-timeline').events.scroll();release();await moving;
  assert.equal(el('chat-timeline').children.length,22);assert.equal(el('chat-timeline').scrollTop,500);
  await run('pollChatWindow()');assert(!el('chat-latest').hidden);
  // Evidence anchor stays available while browsing / returning to live tail.
  await run('openChat(5)');assert.equal(run('evidenceAnchor'),5);assert(!el('chat-anchor').disabled);
  rows=rows.filter(m=>m.id!==5);sequence++;await run('pollChatWindow()');
  assert(!el('chat-timeline').children.some(n=>n.dataset.messageId==='5'));
  // Slow response from previous conversation cannot overwrite the next choice.
  hold=()=>new Promise(r=>release=r);const stale=run('pollChatWindow(true)');await Promise.resolve();
  await run("openChat(null,['wechat','other'])");release();await stale;
  assert.equal(run('chatPage.platform'),'wechat');assert.equal(el('chat-view-title').textContent,'微信好友');
  el('chat-dialog').close();const count=requests.length;await run('tickChatBrowser()');assert.equal(requests.length,count);
  assert.equal(timers.size,0);doc.hidden=true;await run('tickChatBrowser()');assert.equal(requests.length,count);
  console.log('PASS: two-pane selection/last-message preview, live follow, history scroll/new-message cue, keyed DOM reuse, literal text, deleted anchor, in-flight scroll/switch safety and polling stop. DOM fixture only.');
})().catch(e=>{console.error(e);process.exitCode=1;});
