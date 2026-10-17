// Windows native / Linux dual-mode / Mac Docker contracts, all synthetic.
import {JSDOM} from 'jsdom';
import assert from 'node:assert/strict';
const dom=new JSDOM('<div id="host"></div>',{url:'http://127.0.0.1:7861/',pretendToBeVisual:true});
const {window}=dom;window.matchMedia=()=>({matches:false,addEventListener(){}});
for(const key of ['window','document','Node','Element','HTMLElement','DocumentFragment','Event','NodeList','HTMLCollection','navigator','localStorage','Option']){
 const value=key==='window'?window:window[key];Object.defineProperty(globalThis,key,{value,configurable:true});
}
globalThis.matchMedia=window.matchMedia;
let state,releaseStart,holdStart=false,calls=[];
const reply=value=>({ok:true,status:200,json:async()=>structuredClone(value)});
globalThis.fetch=async(input,options={})=>{
 const action=String(input).split('/').pop();
 if(!options.method||options.method==='GET')return reply(state);
 const body=JSON.parse(options.body);calls.push({action,body});
 assert.equal(options.headers['Content-Type'],'application/json');assert.equal(options.headers['X-ChatWeave-UI'],'1');
 if(action==='inspect'||action==='docker-inspect')return reply({ok:true,mode:action==='inspect'?'local':'docker',folder:body.folder,selection_id:'selection-fixture',consent_fingerprint:'receipt-fixture',agreements:[]});
 if(action==='start'){
  if(holdStart)await new Promise(resolve=>{releaseStart=resolve});
  state={...state,enabled:true,has_consent:true,consent_current:true,folder:'C:\\FixtureSL',phase:'choosing',selection_required:true,choices:[{id:'qq-1',pid:101,account_label:'First'},{id:'qq-2',pid:202,account_label:'Second'}]};
 }else if(action==='select')state={...state,phase:'waiting_login',selection_required:false};
 else if(action==='retry')state={...state,phase:'waiting_login',code:''};
 else if(action==='revoke')state={...state,enabled:false,has_consent:false,consent_current:false,phase:'revoked'};
 else throw Error('Unexpected action '+action);
 return reply({ok:true,...state});
};
const {mountSnowLuma}=await import('../../web/desktop/snowluma.js');
const host=document.querySelector('#host'),wait=()=>new Promise(r=>setTimeout(r,30));
const button=text=>[...host.querySelectorAll('button')].find(n=>n.textContent.includes(text));
const visible=node=>!node.hidden&&!node.closest('[hidden]');
function fill(node,value){node.value=value;node.dispatchEvent(new window.Event('input',{bubbles:true}))}
state={backend:'local',supported_backends:['local'],enabled:false,phase:'idle',choices:[]};
let snow=mountSnowLuma(host);await wait();
const folder=host.querySelector('[aria-label="SnowLuma 文件夹"]');assert(visible(folder));assert(!visible(button('Docker（')));assert(!visible(button('打开扫码登录')));
fill(folder,'C:\\FirstSL');button('检测文件夹').click();await wait();
let consent=host.querySelector('input[type="checkbox"]');assert(!consent.checked);assert(button('同意并开始本机托管').disabled);
consent.checked=true;consent.dispatchEvent(new window.Event('change'));fill(folder,'C:\\ChangedSL');assert(!consent.checked);assert(button('同意并开始本机托管').disabled);
button('检测文件夹').click();await wait();assert.equal(calls.at(-1).body.folder,'C:\\ChangedSL');
consent.checked=true;consent.dispatchEvent(new window.Event('change'));holdStart=true;const start=button('同意并开始本机托管');start.click();start.click();await wait();
assert(folder.disabled);assert.equal(calls.filter(n=>n.action==='start').length,1);assert.deepEqual(calls.at(-1).body,{accepted:true,fingerprint:'receipt-fixture',selection_id:'selection-fixture'});
releaseStart();await wait();const choice=host.querySelector('select');choice.value='qq-2';choice.dispatchEvent(new window.Event('change'));button('连接所选 QQ').click();await wait();assert.deepEqual(calls.at(-1),{action:'select',body:{choice_id:'qq-2'}});
assert(!visible(button('打开扫码登录')));state={...state,phase:'error',code:'fixture',retryable:true};await snow.refresh();button('重试').click();await wait();assert.equal(calls.at(-1).action,'retry');
button('撤销授权').click();await wait();assert.equal(calls.at(-1).action,'revoke');assert(!calls.some(n=>n.action.startsWith('docker-')));assert(folder.disabled,'retained installation stays bound');snow.destroy();
state={supported_backends:['local','docker'],enabled:false,phase:'idle'};snow=mountSnowLuma(host);await wait();button('Docker（').click();button('检测 Docker').click();await wait();
consent=host.querySelector('input[type="checkbox"]');consent.checked=true;consent.dispatchEvent(new window.Event('change'));assert(!button('同意并开始 Docker 托管').disabled);
button('本机 SnowLuma').click();assert(!consent.checked);assert(button('同意并开始本机托管').disabled);snow.destroy();
state={backend:'docker',supported_backends:['docker'],enabled:false,phase:'idle'};snow=mountSnowLuma(host);await wait();assert(!visible(host.querySelector('[aria-label="SnowLuma 文件夹"]')));assert(visible(button('检测 Docker')));snow.destroy();
dom.window.close();console.log('PASS Windows local receipt/consent/start/QQ selection/retry/revoke, Linux mode invalidation and Mac Docker-only controls');
