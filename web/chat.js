'use strict';
// Independent of Agent jobs and client ingestion: poll committed local changes.
let chatPage=null,evidenceAnchor=null,chatRequest=0,chatLoading=false,chatFollowing=false;
let chatBounds=null,chatTimer=null,chatPollBusy=false,chatNewMessages=false,chatWindowRequest=0;
let chatConversations=[],chatListVersion=null,chatListRequest=0,chatListBusy=false,chatListMore=false;
const chatKey=c=>JSON.stringify([c.platform,c.conversation_id]);
const chatAtBottom=()=>{const t=$('chat-timeline');return t.scrollHeight-t.scrollTop-t.clientHeight<65;};
async function chatFetch(path,params={}) {
  const response=await fetch(path+'?'+new URLSearchParams(params),{signal:AbortSignal.timeout(12000),cache:'no-store'});
  if(!response.ok)throw new Error(response.status===404?'这条消息已删除或会话暂无本地记录。':'读取失败，正在等待重试。');
  return response.json();
}
function scrollMark(host) {
  const top=host.getBoundingClientRect().top;
  return [...host.children].filter(n=>n.getBoundingClientRect().bottom>top).slice(0,8)
    .map(n=>({key:n.dataset.messageId||n.dataset.key,offset:n.getBoundingClientRect().top-top}));
}
function restoreScroll(host,marks,oldTop) {
  const node=[...host.children].find(n=>marks.some(m=>m.key===(n.dataset.messageId||n.dataset.key)));
  const mark=node&&marks.find(m=>m.key===(node.dataset.messageId||node.dataset.key));
  if(mark)host.scrollTop+=node.getBoundingClientRect().top-host.getBoundingClientRect().top-mark.offset;
  else host.scrollTop=oldTop;
}
function reconcileChatNodes(host,rows,key,build) {
  const old=new Map([...host.children].map(n=>[n.dataset.key||n.dataset.messageId,n]));
  rows.forEach((row,index)=>{
    const id=String(key(row)),signature=JSON.stringify(row);let node=old.get(id);
    if(!node||node.dataset.signature!==signature) {
      const fresh=build(row);fresh.dataset.signature=signature;
      if(node)node.replaceWith(fresh);node=fresh;
    }
    if(host.children[index]!==node)host.insertBefore(node,host.children[index]||null);
    old.delete(id);
  });
  for(const node of old.values())node.remove();
}
function renderChatChoices() {
  const host=$('chat-conversations'),marks=scrollMark(host),top=host.scrollTop;
  reconcileChatNodes(host,chatConversations,chatKey,c=>{
    const node=document.createElement('button');node.className='chat-conversation-card';node.dataset.key=chatKey(c);
    const heading=document.createElement('span');heading.className='chat-card-heading';
    const name=document.createElement('strong');name.textContent=c.conversation;
    const time=document.createElement('time');time.dateTime=new Date(c.timestamp).toISOString();time.textContent=c.time.slice(5,16);heading.append(name,time);
    const platform=document.createElement('span');platform.className='chat-card-platform';platform.textContent=c.platform==='qq'?'QQ':'微信';
    const preview=document.createElement('span');preview.className='chat-card-preview';preview.textContent=(c.sender?c.sender+'：':'')+c.preview;
    node.title=c.conversation+'\n'+preview.textContent+'\n'+c.time;node.append(heading,platform,preview);
    node.onclick=()=>openChat(null,[c.platform,c.conversation_id],50,0);return node;
  });
  for(const node of host.children)node.setAttribute('aria-current',String(Boolean(chatPage&&node.dataset.key===chatKey(chatPage))));
  if(top>20)restoreScroll(host,marks,top);
  $('chat-more-conversations').hidden=!chatListMore;
  $('chat-list-note').textContent=chatConversations.length?'按最新消息排列 · 仅本地记录':'没有找到已导入的会话';
}
async function loadChatConversations(force=false,more=false) {
  if(chatListBusy||!$('chat-dialog').open)return;
  chatListBusy=true;
  const token=chatListRequest,query=$('chat-search').value.trim(),platform=$('chat-platform-filter').value;
  try {
    const params={query,limit:100};if(platform)params.platform=platform;
    if(!force&&!more&&chatListVersion)Object.assign(params,{since_seq:chatListVersion.high_water,epoch:chatListVersion.epoch});
    const first=await chatFetch('/api/chat-conversations',params);
    if(token!==chatListRequest||!$('chat-dialog').open)return;
    if(first.unchanged)return;
    const wanted=Math.max(100,chatConversations.length)+(more?100:0);
    let rows=first.conversations,page=first;
    while(page.has_more&&rows.length<wanted) {
      page=await chatFetch('/api/chat-conversations',{...params,since_seq:-1,offset:page.next_offset});rows=rows.concat(page.conversations);
      if(token!==chatListRequest||!$('chat-dialog').open)return;
    }
    chatConversations=[...new Map(rows.map(c=>[chatKey(c),c])).values()];chatListVersion=first;chatListMore=page.has_more;
    renderChatChoices();
  } catch(error) {if(token===chatListRequest)$('chat-list-note').textContent=error.message;}
  finally {chatListBusy=false;}
}
function createChatMessage(message) {
  const article=document.createElement('article');article.className='original-message'+(message.is_self===1?' self':'');article.dataset.messageId=String(message.id);
  const sender=document.createElement('div');sender.className='sender-line';sender.textContent=`${message.sender}${message.is_self===1?' · 本人':message.is_self===null?' · 身份未标定':''} · ${message.time}`;
  const bubble=document.createElement('div');bubble.className='original-bubble';
  if(message.reply_to) {
    const quote=document.createElement('blockquote');quote.className='reply-quote';quote.textContent=(message.reply_to.sender?message.reply_to.sender+'：':'')+(message.reply_to.content||'引用消息');
    if(message.reply_to.message_id) {quote.tabIndex=0;quote.title='打开被引用消息';quote.onclick=()=>openChat(message.reply_to.message_id);quote.onkeydown=e=>{if(e.key==='Enter')quote.click();};}
    bubble.append(quote);
  }
  if(message.content){const text=document.createElement('p');text.textContent=message.content;bubble.append(text);}
  if(message.voice)bubble.append(renderVoice(message.id,message.voice));
  const saveSource=(type,id,label)=>{const b=document.createElement('button');b.className='collect-evidence source-button';b.dataset.sourceType=type;b.dataset.sourceId=id;b.textContent=label;return b;};
  if(!window.TULPA_MCP_ONLY)bubble.append(saveSource(message.voice?'voice':'message',String(message.id),'保存证据'));
  for(const item of message.media) {
    if(!item.available){const missing=document.createElement('p');missing.className='media-unavailable';missing.textContent=item.reason;bubble.append(missing);continue;}
    const figure=document.createElement('figure'),img=document.createElement('img'),caption=document.createElement('figcaption');
    img.src=item.url;img.alt=item.kind==='sticker'?'表情包':item.animated?'动态图片':'聊天图片';img.loading='lazy';
    if(item.width>0&&item.height>0){const scale=Math.min(1,300/item.width,360/item.height);img.width=Math.round(item.width*scale);img.height=Math.round(item.height*scale);img.style.width=`${img.width}px`;img.style.aspectRatio=`${item.width} / ${item.height}`;}
    img.onclick=()=>showImage(item.url);img.onerror=()=>{img.hidden=true;caption.textContent='媒体不可用 · 缓存文件可能已缺失';};
    img.onload=()=>{if(article.isConnected&&chatFollowing&&!chatPage?.has_after)$('chat-timeline').scrollTop=$('chat-timeline').scrollHeight;};
    caption.textContent=(item.thumbnail?'缩略图 · ':'')+(item.animated?'GIF / 动态图片 · 点击放大':'点击查看原图');figure.append(img,caption);if(!window.TULPA_MCP_ONLY)figure.append(saveSource('media',`${message.id}:${item.index}`,'保存这张图'));bubble.append(figure);
  }
  for(const file of message.files||[]){const button=document.createElement('button');button.className='source-button open-artifact';button.dataset.sourceId=file.id;button.textContent='文件 · '+file.filename;bubble.append(button);}
  if(typeof openReply==='function'){
    const reply=document.createElement('button');reply.className='source-button reply-trigger';reply.textContent='帮我回复';reply.onclick=()=>openReply(message.id);bubble.append(reply);
    article.tabIndex=0;
    article.addEventListener('contextmenu',event=>{event.preventDefault();showReplyMenu(event,message.id);});
  }
  article.append(sender,bubble);return article;
}
function updateChatControls() {
  if(!chatPage)return;
  $('chat-platform').textContent=(chatPage.platform==='qq'?'QQ':'微信')+' · 原始记录';
  $('chat-view-title').textContent=chatPage.conversation||'会话暂无本地消息';
  $('chat-location').textContent=(evidenceAnchor?'证据 M'+evidenceAnchor+' · ':'')+`显示 ${chatPage.messages.length} 条消息 · 自动更新已入库内容`;
  $('chat-earlier').disabled=chatLoading||!chatPage.has_before||!chatPage.messages.length;
  $('chat-later').disabled=chatLoading||!chatPage.has_after||!chatPage.messages.length;
  $('chat-anchor').disabled=chatLoading||!evidenceAnchor;
  $('chat-latest').hidden=!chatPage.has_after&&chatAtBottom();
  $('chat-latest').textContent=chatNewMessages?'有新消息 · 查看最新':'回到最新';
}
function renderChatPage(page,position='preserve',anchor=null) {
  if(typeof syncReplyConversation==='function')syncReplyConversation(page);
  const host=$('chat-timeline'),marks=scrollMark(host),top=host.scrollTop;
  chatPage=page;
  reconcileChatNodes(host,page.messages,m=>m.id,createChatMessage);
  for(const node of host.children)node.classList.toggle('anchored',Number(node.dataset.messageId)===evidenceAnchor);
  if(position==='bottom')host.scrollTop=host.scrollHeight;
  else if(position==='anchor') {
    const node=[...host.children].find(n=>Number(n.dataset.messageId)===anchor);
    if(node)host.scrollTop+=node.getBoundingClientRect().top-host.getBoundingClientRect().top-(host.clientHeight-node.offsetHeight)/2;
  } else restoreScroll(host,marks,top);
  if(!page.messages.length){const empty=document.createElement('p');empty.className='empty-note';empty.textContent=page.latest_message_id?'当前范围的消息已删除或不可用。可点击“回到最新”查看其余记录。':'此会话暂无已导入消息。';host.replaceChildren(empty);}
  updateChatControls();renderChatChoices();
  if(typeof loadReplyReceipts==='function')void loadReplyReceipts();
}
function setChatBounds(page) {
  if(!page.messages.length)return;
  const first=page.messages[0],last=page.messages.at(-1);
  chatBounds={first_timestamp:first.timestamp,first_id:first.id,last_timestamp:last.timestamp,last_id:last.id};
}
async function openChat(anchor=null,conversation=null,before=20,after=20,keepAnchor=false) {
  if(!$('chat-dialog').open){$('chat-dialog').showModal();void loadChatConversations(true);scheduleChatPoll();}
  const token=++chatRequest;chatLoading=true;chatFollowing=false;chatNewMessages=false;
  if(!keepAnchor)evidenceAnchor=anchor;
  try {
    if(anchor===null&&!conversation) {
      if(chatPage)conversation=[chatPage.platform,chatPage.conversation_id];
      else {
        // The initial directory request may still be in flight; fetch a single
        // newest conversation without tying selection to the Agent's filters.
        const list=await chatFetch('/api/chat-conversations',{limit:1});
        if(token!==chatRequest||!$('chat-dialog').open)return;
        if(!list.conversations.length){$('chat-location').textContent='暂无已导入聊天，请先读取本机消息。';return;}
        conversation=[list.conversations[0].platform,list.conversations[0].conversation_id];
      }
      before=50;after=0;
    }
    $('chat-location').textContent='正在读取本地聊天…';updateChatControls();
    const params={before,after};
    if(anchor!==null)params.anchor_message_id=anchor;
    if(conversation){params.platform=conversation[0];params.conversation_id=conversation[1];}
    const page=await chatFetch('/api/chat-context',params);
    if(token!==chatRequest||!$('chat-dialog').open)return;
    setChatBounds(page);chatFollowing=anchor===null&&!page.has_after;
    renderChatPage(page,anchor===null?'bottom':'anchor',anchor);
    $('chat-live-status').textContent='每 2 秒更新 · 仅本地已入库';
  } catch(error){if(token===chatRequest)$('chat-live-status').textContent=error.message;}
  finally {if(token===chatRequest){chatLoading=false;updateChatControls();}}
}
async function pollChatWindow(force=false) {
  if(!$('chat-dialog').open||chatLoading||!chatPage||!chatBounds)return;
  const token=chatRequest,request=++chatWindowRequest,follow=chatFollowing&&!chatPage.has_after;
  const data=await chatFetch('/api/chat-updates',{platform:chatPage.platform,conversation_id:chatPage.conversation_id,...chatBounds,
    follow,since_seq:force?-1:chatPage.high_water,epoch:chatPage.epoch});
  if(token!==chatRequest||request!==chatWindowRequest||chatLoading||!$('chat-dialog').open)return;
  // The user may scroll up while this request is in flight. Do not replace
  // their old window with the newest 200 rows; retry with their current bounds.
  if(follow&&!chatFollowing)return;
  $('chat-live-status').textContent='每 2 秒更新 · 仅本地已入库';
  if(data.unchanged){chatPage.high_water=data.high_water;chatPage.epoch=data.epoch;return;}
  const oldLast=[chatPage.latest_timestamp,chatPage.latest_message_id],newLast=[data.latest_timestamp,data.latest_message_id];
  if(newLast[0]>oldLast[0]||(newLast[0]===oldLast[0]&&newLast[1]>oldLast[1]))chatNewMessages=!follow;
  const page={...chatPage,...data};
  if(follow){setChatBounds(page);chatNewMessages=false;}
  renderChatPage(page,follow?'bottom':'preserve');
}
function scheduleChatPoll() {
  clearTimeout(chatTimer);
  if($('chat-dialog').open&&!document.hidden)chatTimer=setTimeout(tickChatBrowser,2000);
}
async function tickChatBrowser() {
  if(chatPollBusy||!$('chat-dialog').open||document.hidden)return;
  chatPollBusy=true;
  try {await Promise.all([loadChatConversations(),pollChatWindow()]);}
  catch(error){$('chat-live-status').textContent='连接中断，自动重试中';}
  finally {chatPollBusy=false;scheduleChatPoll();}
}
function showImage(url){$('image-preview').src=url;if(!$('image-dialog').open)$('image-dialog').showModal();}
$('browse-chats').onclick=()=>openChat();
let chatSearchTimer;
function filterChatChoices(){++chatListRequest;chatListVersion=null;chatConversations=[];clearTimeout(chatSearchTimer);chatSearchTimer=setTimeout(()=>loadChatConversations(true),180);}
$('chat-search').addEventListener('input',filterChatChoices);
$('chat-platform-filter').onchange=filterChatChoices;
$('chat-more-conversations').onclick=()=>loadChatConversations(true,true);
$('chat-earlier').onclick=()=>{if(chatPage?.messages.length)void openChat(chatPage.messages[0].id,null,40,0,true);};
$('chat-later').onclick=()=>{if(chatPage?.messages.length)void openChat(chatPage.messages.at(-1).id,null,0,40,true);};
$('chat-anchor').onclick=()=>{if(evidenceAnchor)void openChat(evidenceAnchor,null,20,20,true);};
$('chat-latest').onclick=()=>{if(chatPage)void openChat(null,[chatPage.platform,chatPage.conversation_id],50,0,true);};
$('chat-timeline').addEventListener('scroll',()=>{chatFollowing=Boolean(chatPage&&!chatPage.has_after&&chatAtBottom());updateChatControls();},{passive:true});
$('chat-dialog').addEventListener('close',()=>{++chatRequest;++chatListRequest;chatLoading=false;clearTimeout(chatTimer);clearTimeout(chatSearchTimer);});
document.addEventListener('visibilitychange',()=>{if(document.hidden)clearTimeout(chatTimer);else if($('chat-dialog').open)void tickChatBrowser();});
