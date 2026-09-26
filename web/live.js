'use strict';
let liveLastId=0,liveBusy=false,liveEditing=false,liveRefreshAt=0;
const liveLabels={starting:'正在启动',listening:'监听中',receiving:'读取新增',retrying:'正在重试',degraded:'实时读取受限',client_stopped:'客户端未运行',needs_baseline:'需先导入'};
const liveShortLabels={starting:'启动中',listening:'监听中',receiving:'更新中',retrying:'重试中',degraded:'读取受限',client_stopped:'未运行',needs_baseline:'待导入'};
function liveLabel(p,short=false) {
  if(p.status==='off')return '监听已关闭';
  return (short?liveShortLabels:liveLabels)[p.status]||'状态未知';
}
function liveDetail(p) {
  if(p.status!=='off')return p.detail;
  return '本工具的实时监听已关闭；可在下方选择“数据库监听”并保存。与历史导入的平台勾选无关，也不代表客户端已退出。';
}
function liveClock(value) {return value?new Date(value*1000).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'尚无';}
async function pollLive() {
  if(liveBusy||document.hidden)return;
  liveBusy=true;
  try {
    const data=await watchApi('/api/live-status');
    $('live-summary').classList.remove('disconnected');
    $('live-summary').replaceChildren(...data.platforms.map(p=>{
      const line=document.createElement('span');line.className='live-line '+p.status;
      const dot=document.createElement('i');dot.setAttribute('aria-hidden','true');
      line.append(dot,document.createTextNode(`${p.platform==='qq'?'QQ':'微信'} ${liveLabel(p,true)}`));
      line.title=liveDetail(p)+'；最后收到 '+liveClock(p.last_received);return line;
    }));
    $('live-summary').title=data.platforms.map(p=>`${p.platform==='qq'?'QQ':'微信'}：${liveLabel(p)}；${liveDetail(p)}；最后收到 ${liveClock(p.last_received)}`).join('\n')+'\n点击查看实时摄取详情';
    $('live-detail').replaceChildren(...data.platforms.map(p=>{
      const line=document.createElement('p');line.textContent=`${p.platform==='qq'?'QQ':'微信'}：${liveDetail(p)} 最后收到 ${liveClock(p.last_received)}；${p.metrics.seconds==null?'尚未成功完成一轮实时读取':'最近成功读取耗时 '+p.metrics.seconds+' 秒'}。`;return line;
    }));
    if(!liveEditing) {
      $('live-qq').value=data.settings.qq;$('live-wechat').value=data.settings.wechat;
      $('live-stickers').checked=data.settings.load_stickers;
    }
    const upper=Math.max(...data.platforms.map(p=>p.last_id||0));
    if(upper>liveLastId && state.ready && !state.busy && !state.loading && Date.now()-liveRefreshAt>5000) {
      liveRefreshAt=Date.now();
      const refreshed=await call('refresh',[]);setData(refreshed[0],refreshed[1]);
      await loadWatches();
      liveLastId=upper;
      // Timestamp means the visible UI has refreshed its local data, not that
      // the user opened/read every newly arrived message bubble.
      await watchApi('/api/live-visible',{through_id:upper});
    }
  } catch(error) {
    $('live-summary').textContent='实时状态连接中断';
    $('live-summary').classList.add('disconnected');
    $('live-summary').title=error.message;
  } finally {liveBusy=false;}
}
$('live-summary').onclick=()=>{
  $('open-data').click();$('live-settings-panel').open=true;
  setTimeout(()=>$('live-settings-panel').scrollIntoView({block:'center',behavior:'smooth'}),100);
};
for(const id of ['live-qq','live-wechat','live-stickers']) $(id).onchange=()=>{liveEditing=true;};
$('live-save').onclick=async()=>{
  try {
    await watchApi('/api/live-settings',{qq:$('live-qq').value,wechat:$('live-wechat').value,
      load_stickers:$('live-stickers').checked},'PUT');
    liveEditing=false;toast('实时设置已保存');await pollLive();
  } catch(error) {toast(error.message);}
};
setInterval(pollLive,2000);void pollLive();
document.addEventListener('visibilitychange',()=>{if(!document.hidden)void pollLive();});
