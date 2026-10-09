/** Actual installed DSH SDK agent loop + local scripted SSE provider.
 * No real model, QQ, user home, MCP token, or user session is used.
 */
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import http from 'node:http';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import {RETRY_POLICY,configure} from './configure_dsh_retry.mjs';

const arg=name=>process.argv[process.argv.indexOf(name)+1];
assert(process.argv.includes('--dsh-root'),'Pass --dsh-root <existing DSH installation>');
const root=path.resolve(arg('--dsh-root'));
const require=createRequire(path.join(root,'package.json'));
const YAML=require('yaml');
const {JsonRpcLineTransport}=await import(pathToFileURL(require.resolve('@deepseek-ai/dsh-sdk-protocol')));
const temp=fs.mkdtempSync(path.join(os.tmpdir(),'tulpa-dsh-retry-'));
const home=path.join(temp,'home');fs.mkdirSync(home);
const dispatchFile=path.join(temp,'dispatch.jsonl');
const plugin=path.join(temp,'fixture.mjs');
fs.writeFileSync(plugin,`
import {defineTool} from ${JSON.stringify(pathToFileURL(require.resolve('@deepseek-ai/dsh-tools')).href)};
import fs from 'node:fs';
export const name='retry-fixture'; export const inject=['tools'];
export function apply(ctx){ctx.tools.register(defineTool({name:'fixture_send',description:'Mock write counter only',
 parameters:{session_id:{type:'string',required:true},state:{type:'string',required:true}},
 output:{schema:{type:'string'},render:(_args,value)=>[{type:'text',text:value}]},
 execute(args){fs.appendFileSync(${JSON.stringify(dispatchFile)},JSON.stringify(args)+'\\n');return args.state;}}));}
`);
const guard=path.join(temp,'network-guard.mjs');
fs.writeFileSync(guard,`const fetch0=globalThis.fetch;globalThis.fetch=(input,init)=>{const u=new URL(typeof input==='string'?input:input.url??input);if(u.hostname!=='127.0.0.1')throw new Error('External network forbidden in retry fixture');return fetch0(input,init);};`);
const sse=events=>events.map(e=>'event: '+e.type+'\ndata: '+JSON.stringify(e)+'\n\n').join('');
function response(kind){
  const events=[{type:'message_start',message:{id:'fixture',role:'assistant',usage:{input_tokens:10}}}];
  if(kind==='done')events.push({type:'content_block_start',index:0,content_block:{type:'text',text:'fixture complete'}},{type:'content_block_stop',index:0});
  else {
    const args=JSON.stringify({session_id:'a'.repeat(32),state:kind==='unknown'?'UNKNOWN':'SUCCEEDED'});
    const blocks=[args];
    // A valid completed tool block preceding a malformed block must NOT run.
    if(kind==='malformed')blocks.push('{"session_id":12345678'+'a'.repeat(24)+',"state":"SUCCEEDED"}');
    for(const [index,text] of blocks.entries())events.push(
      {type:'content_block_start',index,content_block:{type:'tool_use',id:'call-'+index,name:'fixture_send',input:{}}},
      {type:'content_block_delta',index,delta:{type:'input_json_delta',partial_json:text}},
      {type:'content_block_stop',index});
  }
  events.push({type:'message_delta',delta:{stop_reason:kind==='done'?'end_turn':'tool_use'},usage:{output_tokens:10}});
  if(kind!=='closed')events.push({type:'message_stop'});
  return sse(events);
}
const scenarios=new Map();let active;
const server=http.createServer(async(req,res)=>{
  let text='';for await(const chunk of req)text+=chunk;
  const data=JSON.parse(text);assert(data.tools.every(t=>t.name==='fixture_send'),'Only fixture tool may be exposed');
  const test=scenarios.get(active);assert(test,'No active scenario');
  const kind=test.script[Math.min(test.calls++,test.script.length-1)];
  if(kind==='auth'){res.writeHead(401,{'Content-Type':'application/json'});res.end(JSON.stringify({type:'error',error:{type:'authentication_error',message:'fixture auth failure'}}));}
  else{res.writeHead(200,{'Content-Type':'text/event-stream'});res.end(response(kind));}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const baseURL='http://127.0.0.1:'+server.address().port;
const rows=[
  {id:'llm-deepseek',config:{baseURL,apiKeyEnv:'DEEPSEEK_API_KEY',maxTokens:1024}},
  ...['session-log-deepseek','plugin-package-inventory-deepseek','persistent-pwsh','persistent-bash'].map(id=>({id,disabled:true})),
  {insert:[{id:'retry-fixture',name:plugin}]},
];
fs.writeFileSync(path.join(home,'cordis.patch.yml'),YAML.stringify(rows));
const configured=await configure(root,home,true);assert(configured.applied);
assert(!(await configure(root,home,true)).changed,'Configuration is idempotent');
const child=spawn(process.execPath,['--import',pathToFileURL(guard).href,require.resolve('@deepseek-ai/dsh/lib/bin.js'),'--profile','sdk-minimal'],{
  cwd:temp,windowsHide:true,stdio:['pipe','pipe','pipe'],
  env:{PATH:process.env.PATH,SystemRoot:process.env.SystemRoot,TEMP:temp,TMP:temp,USERPROFILE:temp,
    DSH_HOME:home,DSH_TELEMETRY_DISABLED:'1',DSH_TELEMETRY_MODE:'DISABLED',DEEPSEEK_API_KEY:'fixture-not-a-credential'},
});
let stderr='';child.stderr.on('data',x=>stderr+=x);
const rpc=new JsonRpcLineTransport(child.stdout,child.stdin);rpc.start();
child.stdin.on('error',()=>rpc.close());
const events=[];rpc.onNotification((method,params)=>events.push({method,...params}));
const deadline=()=>AbortSignal.timeout(40000);
const dispatches=()=>fs.existsSync(dispatchFile)?fs.readFileSync(dispatchFile,'utf8').trim().split('\n').filter(Boolean).length:0;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const report=[];
try {
  await rpc.request('initialize',{cwd:temp,provider:'deepseek-official',model:'deepseek-flash',reasoningEffort:'off',maxTokens:1024},deadline());
  for(const [name,script,requests,writes,retries] of [
    ['malformed_then_valid',['malformed','valid','done'],3,1,1],
    ['malformed_exhausted',['malformed'],3,0,2],
    ['stream_closed',['closed','valid','done'],3,1,1],
    ['auth_not_retried',['auth'],1,0,0],
    ['unknown_tool_not_retried',['unknown','done'],2,1,0],
    ['failure_after_write',['valid','malformed'],4,1,2],
  ]) {
    active=name;const test={script,calls:0};scenarios.set(name,test);const start=events.length,before=dispatches();
    await rpc.request('session/prompt',{sessionId:'fixture-'+name,contentBlocks:[{type:'text',text:'Run the local fixture.'}]},deadline());
    const until=Date.now()+25000;
    while(!events.slice(start).some(e=>e.event?.type==='turn/end')){
      if(Date.now()>until)throw new Error('Timed out: '+name+' '+stderr.slice(-2000));await sleep(20);
    }
    const observed=events.slice(start);const repeated=observed.filter(e=>e.event?.type==='llm/retry');
    assert.equal(test.calls,requests,name+' requests');assert.equal(dispatches()-before,writes,name+' writes');assert.equal(repeated.length,retries,name+' retries');
    report.push({name,requests:test.calls,mock_dispatches:writes,retries:repeated.length});
  }
  // Shutdown during backoff aborts the retry instead of making one last request.
  active='cancel_backoff';const test={script:['malformed'],calls:0};scenarios.set(active,test);const start=events.length,before=dispatches();
  await rpc.request('session/prompt',{sessionId:'fixture-cancel',contentBlocks:[{type:'text',text:'Cancel fixture'}]},deadline());
  const until=Date.now()+15000;
  while(!events.slice(start).some(e=>e.event?.type==='llm/retry')){assert(Date.now()<until);await sleep(5);}
  await rpc.request('shutdown',{},deadline());
  assert.equal(test.calls,1);assert.equal(dispatches(),before);
  report.push({name:active,requests:1,mock_dispatches:0});
  const result={status:'passed',harness:'installed DSH SDK agent loop',policy:RETRY_POLICY,checks:report,real_model_requests:0,real_qq_writes:0};
  if(process.argv.includes('--report'))fs.writeFileSync(arg('--report'),JSON.stringify(result,null,2));
  console.log(JSON.stringify(result));
} catch(error) {
  console.error(stderr.slice(-4000));throw error;
} finally {
  if(child.exitCode===null){try{await rpc.request('shutdown',{},AbortSignal.timeout(5000));}catch{child.stdin.end();}}
  rpc.close();server.closeAllConnections();await new Promise(r=>server.close(r));
}
