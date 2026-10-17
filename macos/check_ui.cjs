'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('web/data.js','utf8');
const prefix=source.slice(0,source.indexOf('function invalidateDelete'));
const scope=source.slice(source.indexOf('function clientReadScope()'),source.indexOf('async function runDataJob'));
function check(platform, supported) {
  const elements=new Map();
  const section={classList:{add(){},toggle(){}},querySelectorAll(){return this.controls;},controls:[]};
  const $=id=>{
    if(!elements.has(id))elements.set(id,{checked:true,disabled:false,value:'all',closest:()=>section});
    return elements.get(id);
  };
  section.controls=[$('read-enable-wechat'),$('read-account-wechat')];
  const context=vm.createContext({assert,$,document:{documentElement:{dataset:{platform}},querySelectorAll:()=>[]}});
  vm.runInContext(prefix+scope+`
    let selections=[];
    function selectedReadAccount(p) { selections.push(p); return 'fixture'; }
    accountDiscovery={wechat:{supported:${supported}}};
    syncDataControls(false);
    const result=clientReadScope();
    assert.equal(result.qq.enabled,true);
    if(${supported}===false) {
      assert.equal($('read-enable-wechat').checked,false);
      assert.equal($('read-enable-wechat').disabled,true);
      assert.equal(result.wechat.enabled,false);
      assert.deepEqual(selections,['qq']);
    } else {
      assert.equal(result.wechat.enabled,true);
      assert.deepEqual(selections,['qq','wechat']);
    }
  `,context);
}
check('macos',true);
check('macos',false);
check('windows',true);
check('windows',false);
console.log('PASS Mac WeChat follows reader support; unsupported platforms stay excluded; QQ and Windows unchanged');
