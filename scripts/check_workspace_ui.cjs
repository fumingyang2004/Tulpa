// Executes the shipped workspace JS against a small DOM/HTTP fixture.
// This verifies wiring/state only, NOT browser rendering or browser acceptance.
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
class Element{
  constructor(tag='div'){this.tagName=tag;this.children=[];this.dataset={};this.value='';this.hidden=false;this.classList={add(){},remove(){}};this.events={};}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;}
  addEventListener(name,fn){this.events[name]=fn;}
  showModal(){this.open=true;}
  close(){this.open=false;this.events.close?.();}
  remove(){}
  setAttribute(name,value){this[name]=value;}
  focus(){doc.activeElement=this;}
  contains(el){return this===el||this.children.some(c=>c.contains?.(el));}
  get options(){return this.children;}
  get childNodes(){return this.children;}
  get selectedOptions(){return this.children.filter(c=>c.selected);}
  querySelector(tag){return this.children.find(c=>c.tagName===tag);}
}
const nodes=new Map(),el=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
const doc={body:new Element('body'),hidden:false,createElement:t=>new Element(t),createTextNode:text=>({textContent:text}),querySelector(){return null;}};
const w={id:'a',title:'Fixture',goal:'范围内调查',scope:{platforms:['qq'],conversations:[],start:null,end:null},scope_epoch:1,revision:1,updated_at:1,watch_ids:[],discovery_cursor:2,processed_seq:1,applied_seq:0};
const calls=[],errors=[];
const ref={source_type:'message',source_id:'1',available:true,label:'原始通知',url:'/?anchor=1'};
const home={scope_epoch:1,outputs:[{id:'o',name:'报告',type:'调查报告',summary:'整理连接条件与注意事项',format:'md',version:1,updated_at:1,refs:[ref],sources:{messages:1,files:0}}],
  understanding:[{text:'已有依据的正式结论',refs:[ref],output_id:'o',output_name:'报告',version:1}],understanding_total:1,
  questions:[{text:'截止日期是否改变？',refs:[ref],output_id:'o',output_name:'报告',version:1}],question_total:1,
  materials:[{...ref,state:'accepted',title:'原始通知',reason:'提供连接条件',platform:'qq',conversation:'测试群'}],confirmed_materials:1,
  recent:{items:[],gap:false},tasks:[],proposals:1};
const ctx=vm.createContext({console,document:doc,$:el,window:{},state:{ready:true,initialized:true,workspaceId:null,profile:'quick',visionMode:'ocr',conversations:[]},watchCards:[],
  Option:function(text,value){const n=new Element('option');n.textContent=text;n.value=value;return n;},
  localStorage:{setItem(){},removeItem(){},getItem(){return null;}},URLSearchParams,location:{search:''},mobile:{matches:false},sidebar(){},leaveWatch(){},syncControls(){},fitInput(){},newSession(){},openWatch(){},toast:t=>errors.push(t),setInterval(){return 1;},clearInterval(){},confirm(){return true;},
  watchApi:async(path,body,method)=>{calls.push([path,body,method]);if(path==='/api/workspaces')return {workspaces:[w]};
    if(path==='/api/workspaces/a')return {workspace:w,outputs:[{id:'o',name:'报告',format:'md',head:1,scope_epoch:1}],tasks:[],pending:1,filtered:2,local_seq:2};
    if(path.endsWith('/home'))return home;
    if(path.endsWith('/thread'))return {turns:[{id:'old-task',question:'上次的问题',state:'completed',mode:'ask',created_at:1,events:[],record:{result:{claims:[{text:'持续对话中的回答',artifact_evidence_ids:['F12:C34']}]},requests:1}}]};
    if(path.endsWith('/schedule'))return {minutes:0,profile:'standard',vision:'ocr'};
    if(path.endsWith('/outputs/o'))return {name:'报告',body:'# 正文\n',format:'md',version:1,refs:[],origin:'agent'};
    if(path.endsWith('/proposals'))return {proposals:[]};
    if(path.endsWith('/materials'))return {materials:home.materials,candidates:[]};
    if(path.endsWith('/history'))return {versions:[],audit:[]};
    if(path.endsWith('/tasks'))return {task_id:'t',state:'running'};
    if(path==='/api/agent-presets')return {profiles:[{id:'standard',label:'标准',budget:{max_requests:6,max_tool_calls:20,max_seconds:120}}]};
    throw Error('Unexpected '+path);}});
vm.runInContext(fs.readFileSync('web/workspaces.js','utf8'),ctx);
function find(root,text){if(root.textContent===text)return root;for(const c of root.children||[]){const x=find(c,text);if(x)return x;}}
function byClass(root,name){if(root.className===name)return root;for(const c of root.children||[]){const x=byClass(c,name);if(x)return x;}}
function all(root,predicate){return [...(predicate(root)?[root]:[]),...(root.children||[]).flatMap(c=>all(c,predicate))];}
(async()=>{
  await ctx.window.openWorkspace('a');assert.equal(ctx.state.workspaceId,'a');assert.equal(el('messages').hidden,true);
  assert.equal(el('question').placeholder,'在这个工作区继续工作…');
  for(const label of ['当前状态','已有依据的正式结论','最近没有发现重要变化','待确认事项 · 信息缺口，不是待办','资料','证据','工作成果','上次的问题','持续对话中的回答'])assert(find(el('workspace-page'),label),label);
  assert.equal(find(el('workspace-page'),'文件原文').href,'/?file=12&chunk=34');
  assert(!find(el('workspace-page'),'调查记录 · Agent 工作日志'),'logs not default main content');
  const requestsBefore=calls.filter(c=>c[0].endsWith('/tasks')).length;
  await find(el('workspace-page'),'继续调查这个问题').onclick();assert(el('question').value.includes('截止日期是否改变'));assert.equal(doc.activeElement,el('question'));
  assert.equal(calls.filter(c=>c[0].endsWith('/tasks')).length,requestsBefore,'click prepares investigation; no automatic model');
  const view=find(el('workspace-page'),'▤ 报告');assert(view);await view.onclick();
  const dialog=doc.body.children.at(-1);assert(find(dialog,'下载 MD'));await find(dialog,'编辑').onclick();
  assert.equal(dialog.children.find(c=>c.className==='ws-editor').hidden,false);
  await find(el('workspace-page'),'管理资料关联').onclick();assert(calls.some(c=>c[0].endsWith('/materials')));
  await find(el('workspace-page'),'加入成果').onclick();assert(el('question').value.includes('message 1'));
  await find(el('workspace-page'),'◷ 历史版本').onclick();assert(calls.some(c=>c[0].endsWith('/history')));
  await find(el('workspace-page'),'◉ Agent 对话').onclick();
  el('question').value='修改报告';await ctx.window.sendWorkspaceTask();assert(calls.some(c=>c[0].endsWith('/tasks')&&c[1].question==='修改报告'));
  // Scope is a permission boundary: filtering must not drop hidden selections.
  const qq1=JSON.stringify(['qq','one']),qq2=JSON.stringify(['qq','two']),wx=JSON.stringify(['wechat','three']);
  ctx.state.conversations=[['qq · 测试甲',qq1],['qq · 测试乙',qq2],['wechat · 测试丙',wx]];
  ctx.watchCards.push({id:'watch-a',title:'组会安排'},{id:'watch-b',title:'课堂小测'});
  w.scope.platforms=['qq','wechat'];w.scope.conversations=[qq1,wx];w.watch_ids=['watch-a'];
  await ctx.window.editWorkspace();const settings=doc.body.children.at(-1);
  assert(!all(settings,n=>n.tagName==='select'&&n.multiple).length,'use explicit checkboxes instead of native multiselect');
  const groups=all(settings,n=>n.className==='ws-checklist'),scopeGroup=groups[0],watchGroup=groups[1];
  const scopeChecks=all(scopeGroup,n=>n.type==='checkbox');assert.deepEqual(scopeChecks.filter(c=>c.checked).map(c=>c.value),[qq1,wx]);
  const search=all(scopeGroup,n=>n.type==='search')[0];search.value='测试乙';search.oninput();
  assert(find(scopeGroup,'已选择 2 个会话（包含筛选外的已选项）'));
  scopeChecks.find(c=>c.value===qq2).checked=true;scopeChecks.find(c=>c.value===qq2).onchange();
  const platformChecks=all(byClass(settings,'platforms'),n=>n.type==='checkbox');platformChecks[1].checked=false;platformChecks[1].onchange();
  assert(find(scopeGroup,'已选择 2 个会话（包含筛选外的已选项）'));
  platformChecks[1].checked=true;platformChecks[1].onchange();assert(find(scopeGroup,'已选择 3 个会话（包含筛选外的已选项）'));
  const watchChecks=all(watchGroup,n=>n.type==='checkbox');watchChecks[0].checked=false;watchChecks[0].onchange();watchChecks[1].checked=true;watchChecks[1].onchange();
  await find(settings,'保存设置').onclick();const saved=calls.filter(c=>c[0]==='/api/workspaces/a'&&c[2]==='PATCH').at(-1)[1];
  assert.deepEqual(Array.from(saved.scope.conversations).sort(),[qq1,qq2,wx].sort());assert.deepEqual(Array.from(saved.watch_ids),['watch-b']);
  await ctx.window.editWorkspace();const again=doc.body.children.at(-1);for(const c of all(again,n=>n.className==='ws-checklist').flatMap(g=>all(g,n=>n.type==='checkbox'))){c.checked=false;c.onchange();}
  await find(again,'保存设置').onclick();const cleared=calls.filter(c=>c[0]==='/api/workspaces/a'&&c[2]==='PATCH').at(-1)[1];assert.equal(cleared.scope.conversations.length,0);assert.equal(cleared.watch_ids.length,0);
  await ctx.window.editWorkspace();const scrollDialog=doc.body.children.at(-1);scrollDialog.scrollTop=800;await find(byClass(scrollDialog,'dialog-head'),'关闭').onclick();assert.equal(scrollDialog.open,false);
  ctx.window.leaveWorkspace();assert.equal(ctx.state.workspaceId,null);assert.equal(el('messages').hidden,false);
  home.understanding=[];home.outputs=[];home.questions=[];home.materials=[];home.proposals=0;home.confirmed_materials=0;
  await ctx.window.openWorkspace('a');assert(find(el('workspace-page'),'尚未确认状态文档'));assert(find(el('workspace-page'),'最近没有发现重要变化'));
  ctx.window.leaveWorkspace();
  assert.deepEqual(errors,[]);console.log('PASS: IDE chat/resources; checkbox preselection/filter retention/platforms/save/clear and dialog close wiring; DOM fixture, NOT browser rendering acceptance.');
})().catch(e=>{console.error(e);process.exitCode=1;});
