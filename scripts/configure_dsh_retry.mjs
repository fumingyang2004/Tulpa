/** Configure the official DSH provider retry hook, without patching node_modules.
 * Default is a preview. Only --apply writes the chosen home's existing patch.
 * No credentials or unrelated configuration are printed.
 */
import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';

export const RETRY_POLICY = Object.freeze({
  mode:'normal', maxRetries:2,
  retryableCodes:['EMPTY_RESPONSE','RATE_LIMIT','SERVER','TIMEOUT','TRANSPORT','STREAM_CLOSED','MALFORMED_RESPONSE'],
  backoff:{initialDelayMs:750,maxDelayMs:10000,jitterRatio:0.1},
});

export async function configure(root, home, apply=false) {
  const require=createRequire(path.join(path.resolve(root),'package.json'));
  const YAML=require('yaml');
  const {resolveRetryPolicy}=await import(pathToFileURL(require.resolve('@deepseek-ai/dsh-llm')));
  resolveRetryPolicy(RETRY_POLICY,'Tulpa DSH retry policy');
  const file=path.join(path.resolve(home),'cordis.patch.yml');
  const before=fs.readFileSync(file,'utf8');
  // Cordis !js expressions remain inert tagged scalars, never evaluated here.
  const yamlOptions={logLevel:'silent',customTags:[{tag:'tag:yaml.org,2002:js',resolve:value=>value}]};
  const doc=YAML.parseDocument(before,yamlOptions);
  if(doc.errors.length || !YAML.isSeq(doc.contents))throw new Error('Expected an existing valid DSH patch list');
  const providers=doc.contents.items.filter(row=>YAML.isMap(row) && row.get('id')==='llm-deepseek');
  if(providers.length!==1)throw new Error('Expected exactly one llm-deepseek row; no configuration was written');
  const row=providers[0];
  const old=row.getIn(['config','retryPolicy'])?.toJSON?.();
  const changed=JSON.stringify(old)!==JSON.stringify(RETRY_POLICY);
  let backup;
  if(changed && apply) {
    backup=path.join(path.resolve(home),'backups','retry-'+new Date().toISOString().replace(/[:.]/g,'-')+'.yml');
    fs.mkdirSync(path.dirname(backup),{recursive:true});
    fs.copyFileSync(file,backup,fs.constants.COPYFILE_EXCL);
    row.setIn(['config','retryPolicy'],doc.createNode(RETRY_POLICY));
    const output=doc.toString();
    const check=YAML.parse(output,yamlOptions);
    const original=YAML.parse(before,yamlOptions);
    const target=original.find(r=>r.id==='llm-deepseek');
    target.config={...target.config,retryPolicy:RETRY_POLICY};
    if(JSON.stringify(check)!==JSON.stringify(original))throw new Error('Unrelated configuration changed; update cancelled');
    if(fs.readFileSync(file,'utf8')!==before)throw new Error('Configuration changed concurrently; update cancelled');
    const temp=file+'.retry-tmp';
    fs.writeFileSync(temp,output,{flag:'wx'});
    fs.renameSync(temp,file);
  }
  return {applied:changed && apply,changed,policy:RETRY_POLICY,...backup?{backup}:{}};
}

if(process.argv[1] && import.meta.url===pathToFileURL(path.resolve(process.argv[1])).href) {
  const opt=name=>process.argv[process.argv.indexOf(name)+1];
  if(!process.argv.includes('--dsh-root') || !process.argv.includes('--home'))
    throw new Error('Usage: node scripts/configure_dsh_retry.mjs --dsh-root <installation> --home <DSH_HOME> [--apply]');
  console.log(JSON.stringify(await configure(opt('--dsh-root'),opt('--home'),process.argv.includes('--apply'))));
}
