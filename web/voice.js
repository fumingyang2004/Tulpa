'use strict';
const voiceLabels={unprepared:'尚未转写',prepared:'音频可播放 · 尚未转写',queued:'已排队',running:'本地处理中',ready:'转写完成',failed:'转写失败 · 可重试',missing:'媒体不可用 · 本机未找到原音频'};
function renderVoice(mid,value) {
  const box=document.createElement('div');box.className='voice-message';
  function render(v) {
    box.replaceChildren();const label=document.createElement('p');label.className='field-hint';
    label.textContent='语音 · '+(v.duration_ms?`${(v.duration_ms/1000).toFixed(1)} 秒 · `:'')+(voiceLabels[v.status]||v.status);box.append(label);
    if(v.playable) {const audio=document.createElement('audio');audio.controls=true;audio.preload='none';audio.src=v.audio_url;audio.style.maxWidth='100%';box.append(audio);}
    if(v.transcript) {const text=document.createElement('p');text.textContent='[语音转写] '+v.transcript;box.append(text);const note=document.createElement('small');note.textContent='本地机器识别，可能有误听；重要信息请试听核对。';box.append(note);}
    if(v.error) {const error=document.createElement('p');error.className='field-hint';error.textContent=v.error;box.append(error);}
    const busy=['queued','running'].includes(v.status),actions=document.createElement('div');
    if(!v.playable) button('准备播放（不转写）','prepare',actions,busy);
    button(v.status==='ready'?'重新检查转写（优先复用缓存）':['failed','missing'].includes(v.status)?'重试本地转写':'转写语音','transcribe',actions,busy);
    box.append(actions);
    if(busy) setTimeout(async()=>{if(!box.isConnected||!$('chat-dialog').open)return;try{render(await watchApi('/api/voice/'+mid));}catch(e){label.textContent=e.message;}},1500);
  }
  function button(text,action,parent,busy) {
    const b=document.createElement('button');b.className='source-button';b.textContent=text;b.disabled=busy;
    b.onclick=async()=>{b.disabled=true;try{render(await watchApi('/api/voice/'+mid,{action,retry:true}));}catch(e){toast(e.message);b.disabled=false;}};parent.append(b);
  }
  render(value);return box;
}
async function loadVoiceSettings(keepDraft=false) {
  try {
    const v=await watchApi('/api/voice/settings');
    if(!keepDraft)$('voice-strategy').value=v.strategy;
    $('voice-status').textContent=(v.local_asr_ready?`本地模型 ${v.model}`:'本地 ASR 尚未准备')+` · 队列 ${v.queued} 条 · 已转写 ${v.counts.ready||0} 条`+
      (v.discovery.running?' · 正在发现语音…':v.discovery.error?' · '+v.discovery.error:v.discovery.result?` · 最近发现 ${v.discovery.result.found} 条，新增 ${v.discovery.result.imported} 条`+(v.discovery.result.enqueued!==undefined?`，排队 ${v.discovery.result.enqueued} 条`:''):'');
    if($('voice-settings').open&&$('data-dialog').open&&(v.queued||v.discovery.running))setTimeout(()=>loadVoiceSettings(true),2000);
  }catch(e){$('voice-status').textContent=e.message;}
}
$('voice-settings').addEventListener('toggle',()=>{if($('voice-settings').open)void loadVoiceSettings();});
$('voice-save').onclick=async()=>{try{await watchApi('/api/voice/settings',{strategy:$('voice-strategy').value},'PUT');await loadVoiceSettings();toast('语音设置已保存');}catch(e){toast(e.message);}};
async function backfillVoice(transcribe) {
  const p=$('voice-platform').value,start=$('read-'+p+'-start').value,end=$('read-'+p+'-end').value;
  try {
    if(!start||!end)throw new Error('请先填写上方对应平台的起止日期');
    const scope=clientReadScope()[p];if(!scope.enabled)throw new Error('请先启用该平台');
    await watchApi('/api/voice/backfill',{platform:p,start,end,conversations:scope.conversations,transcribe});
    await loadVoiceSettings(true);
  }catch(e){toast(e.message);}
}
$('voice-discover').onclick=()=>backfillVoice(false);
$('voice-backfill').onclick=()=>backfillVoice(true);
