'use strict';

/* Original chat browser: local conversations, paging, live polling and replies. */
import {
  $, h, icon, api, registerView, setViewTitle, toast, errorMessage, openImage, openPopover, closePopover,
  emptyState, loadingState, debounce,
} from './app.js';
import { createReplyController, loadReplyReceipts } from './reply.js';
import { renderVoiceMessage } from './voice.js';

const browse = {
  page: null,
  anchor: null,
  conversations: [],
  version: null,
  listMore: false,
};

let ui = null;
let mounted = false;
let listRequest = 0;
let listBusy = false;
let pageRequest = 0;
let pageLoading = false;
let windowRequest = 0;
let pollTimer = null;
let searchTimer = null;
let newMessages = false;
let receiptsKey = '';
let receiptsAt = 0;
let reply = null;
let nativeVision = false;

const keyOf = item => JSON.stringify([item.platform, item.conversation_id]);

function platformName(platform) {
  return platform === 'qq' ? 'QQ' : '微信';
}

async function chatFetch(path, params = {}) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') query.set(key, String(value));
  }
  return api(path + (query.toString() ? '?' + query.toString() : ''), { timeout: 14000 });
}

function scrollMark(host) {
  const top = host.getBoundingClientRect().top;
  return [...host.children].filter(node => node.getBoundingClientRect().bottom > top).slice(0, 10)
    .map(node => ({ key: node.dataset.key, offset: node.getBoundingClientRect().top - top }));
}

function restoreScroll(host, marks, oldTop) {
  const node = [...host.children].find(item => marks.some(mark => mark.key === item.dataset.key));
  const mark = node && marks.find(item => item.key === node.dataset.key);
  if (mark) host.scrollTop += node.getBoundingClientRect().top - host.getBoundingClientRect().top - mark.offset;
  else host.scrollTop = oldTop;
}

function reconcile(host, rows, build) {
  const old = new Map([...host.children].map(node => [node.dataset.key, node]));
  rows.forEach((row, index) => {
    const signature = JSON.stringify(row.value);
    let node = old.get(row.key);
    if (!node || node.dataset.signature !== signature) {
      const fresh = build(row);
      fresh.dataset.key = row.key;
      fresh.dataset.signature = signature;
      if (node) node.replaceWith(fresh);
      node = fresh;
    }
    if (host.children[index] !== node) host.insertBefore(node, host.children[index] || null);
    old.delete(row.key);
  });
  for (const node of old.values()) node.remove();
}

function dayLabel(day) {
  const date = new Date(day + 'T00:00:00');
  if (Number.isNaN(date.getTime())) return day;
  const today = new Date();
  const yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);
  const iso = value => value.toISOString().slice(0, 10);
  if (day === iso(today)) return '今天';
  if (day === iso(yesterday)) return '昨天';
  return date.toLocaleDateString('zh-CN', { month: 'long', day: 'numeric', weekday: 'short' });
}

function messageNode(message) {
  const article = h('article', {
    class: 'original-message' + (message.is_self === 1 ? ' self' : ''),
    'data-message-id': String(message.id),
    tabIndex: 0,
  });
  const identity = message.is_self === 1 ? '本人' : message.is_self === null ? '身份未标定' : '';
  article.append(h('div', { class: 'sender-line', text: `${message.sender}${identity ? ' · ' + identity : ''} · ${message.time}` }));
  const bubble = h('div', { class: 'original-bubble' });
  if (message.reply_to) {
    const quote = h('blockquote', {
      class: 'reply-quote', text: (message.reply_to.sender ? message.reply_to.sender + '：' : '') + (message.reply_to.content || '引用消息'),
      tabIndex: message.reply_to.message_id ? 0 : -1,
    });
    if (message.reply_to.message_id) {
      quote.title = '打开被引用消息';
      quote.addEventListener('click', () => void openChat(message.reply_to.message_id, null, 20, 20));
      quote.addEventListener('keydown', event => { if (event.key === 'Enter') quote.click(); });
    }
    bubble.append(quote);
  }
  if (message.content) bubble.append(h('p', { text: message.content }));
  if (message.voice) bubble.append(renderVoiceMessage(message.id, message.voice));
  if (!window.TULPA_MCP_ONLY) {
    const save = h('button', {
      class: 'source-button collect-evidence', type: 'button', text: '保存证据',
      dataset: { sourceType: message.voice ? 'voice' : 'message', sourceId: String(message.id) },
    });
    bubble.append(save);
  }
  for (const item of message.media || []) {
    if (!item.available) {
      bubble.append(h('p', { class: 'media-unavailable', text: item.reason || '媒体不可用' }));
      continue;
    }
    const image = h('img', {
      src: item.url, alt: item.kind === 'sticker' ? '表情包' : item.animated ? '动态图片' : '聊天图片',
      loading: 'lazy',
    });
    if (item.width > 0 && item.height > 0) {
      const scale = Math.min(1, 300 / item.width, 360 / item.height);
      image.width = Math.round(item.width * scale);
      image.height = Math.round(item.height * scale);
      image.style.width = image.width + 'px';
      image.style.aspectRatio = item.width + ' / ' + item.height;
    }
    image.addEventListener('error', () => {
      image.hidden = true;
      caption.textContent = '媒体不可用 · 缓存文件可能已缺失';
    });
    const link = h('a', { class: 'open-image', href: item.url, target: '_blank', rel: 'noopener' }, image);
    const caption = h('figcaption', { text: (item.thumbnail ? '缩略图 · ' : '') + (item.animated ? 'GIF / 动态图片 · 点击放大' : '点击查看原图') });
    const figure = h('figure', {}, link, caption);
    if (!window.TULPA_MCP_ONLY) {
      figure.append(h('button', {
        class: 'source-button collect-evidence', type: 'button', text: '保存这张图',
        dataset: { sourceType: 'media', sourceId: `${message.id}:${item.index}` },
      }));
    }
    bubble.append(figure);
  }
  for (const file of message.files || []) {
    bubble.append(h('button', {
      class: 'source-button open-artifact', type: 'button', text: '文件 · ' + file.filename,
      dataset: { sourceId: String(file.id) },
    }));
  }
  const replyButton = h('button', { class: 'source-button reply-trigger', type: 'button', text: '帮我回复' });
  replyButton.addEventListener('click', event => {
    event.stopPropagation();
    void reply?.open(message.id);
  });
  bubble.append(replyButton);
  article.addEventListener('contextmenu', event => {
    event.preventDefault();
    showMessageMenu(event, message.id);
  });
  article.append(bubble);
  return article;
}

function showMessageMenu(event, messageId) {
  closePopover();
  const button = h('button', {
    type: 'button', onclick: () => { closePopover(); void reply?.open(messageId); },
  }, icon('chat'), '帮我回复');
  openPopover(event.currentTarget, button, { label: '消息操作' });
}

function conversationNode(conversation) {
  const node = h('button', {
    class: 'chat-conversation-card', type: 'button', title: conversation.conversation + '\n' + conversation.preview,
    'aria-current': String(browse.page && keyOf(conversation) === keyOf(browse.page)),
    onclick: () => {
      void openChat(null, [conversation.platform, conversation.conversation_id], 50, 0);
      document.body.classList.remove('browse-directory-open');
    },
  },
    h('span', { class: 'chat-card-heading' },
      h('strong', { text: conversation.conversation }),
      h('time', { text: (conversation.time || '').slice(5, 16) })),
    h('span', { class: 'chat-card-platform', text: platformName(conversation.platform) + ' · ' + conversation.count + ' 条' }),
    h('span', { class: 'chat-card-preview', text: (conversation.sender ? conversation.sender + '：' : '') + conversation.preview }));
  return node;
}

function renderConversations() {
  if (!ui) return;
  const focused = document.activeElement?.closest?.('.chat-conversation-card');
  const focusedKey = focused?.dataset.key;
  const host = ui.list;
  const rows = browse.conversations.map(item => ({ key: keyOf(item), value: item }));
  reconcile(host, rows, row => {
    const node = conversationNode(row.value);
    node.dataset.key = row.key;
    return node;
  });
  for (const node of host.children) {
    const data = browse.conversations.find(item => keyOf(item) === node.dataset.key);
    if (data) node.setAttribute('aria-current', String(Boolean(browse.page && keyOf(data) === keyOf(browse.page))));
  }
  if (focusedKey) host.querySelector('[data-key="' + CSS.escape(focusedKey) + '"]')?.focus({ preventScroll: true });
  ui.more.hidden = !browse.listMore;
  ui.listNote.textContent = browse.conversations.length ? '按最新消息排列 · 仅本地记录' : '没有找到已导入的会话';
}

async function loadConversations(force = false, more = false) {
  if (!ui || listBusy) return;
  listBusy = true;
  const token = ++listRequest;
  const query = ui.search.value.trim();
  const platform = ui.platform.value;
  try {
    const params = { query, limit: 100 };
    if (platform) params.platform = platform;
    if (!force && !more && browse.version) {
      params.since_seq = browse.version.high_water;
      params.epoch = browse.version.epoch;
    }
    const first = await chatFetch('/api/chat-conversations', params);
    if (token !== listRequest || !mounted || !ui) return;
    if (first.unchanged) return;
    const wanted = Math.max(100, browse.conversations.length) + (more ? 100 : 0);
    let rows = first.conversations;
    let page = first;
    while (page.has_more && rows.length < wanted) {
      page = await chatFetch('/api/chat-conversations', { ...params, since_seq: -1, offset: page.next_offset });
      if (token !== listRequest || !mounted || !ui) return;
      rows = rows.concat(page.conversations);
    }
    browse.conversations = [...new Map(rows.map(item => [keyOf(item), item])).values()];
    browse.version = first;
    browse.listMore = page.has_more;
    renderConversations();
  } catch (error) {
    if (token === listRequest && ui) ui.listNote.textContent = errorMessage(error, '会话列表读取失败。');
  } finally {
    listBusy = false;
  }
}

function updateControls() {
  if (!ui) return;
  const page = browse.page;
  if (!page) {
    ui.earlier.disabled = ui.anchorButton.disabled = ui.later.disabled = true;
    ui.latest.hidden = true;
    return;
  }
  ui.earlier.disabled = pageLoading || !page.has_before || !page.messages?.length;
  ui.later.disabled = pageLoading || !page.has_after || !page.messages?.length;
  ui.anchorButton.disabled = pageLoading || !browse.anchor;
  ui.latest.hidden = !page.has_after && !newMessages;
  ui.latest.textContent = newMessages && !browse.following ? '有新消息 · 查看最新' : '回到最新';
  ui.paneTitle.textContent = page.conversation || '会话暂无本地消息';
  ui.platform.textContent = platformName(page.platform) + ' · 原始记录';
  ui.location.textContent = (browse.anchor ? '证据 M' + browse.anchor + ' · ' : '') +
    `显示 ${page.messages?.length || 0} 条消息 · 自动更新已入库内容`;
  setViewTitle('聊天记录', page.conversation || platformName(page.platform) + ' 会话');
}

function renderPage(page, position = 'preserve', anchor = null) {
  if (!ui) return;
  browse.page = page;
  if (reply) reply.syncConversation(page);
  const host = ui.timeline;
  const marks = scrollMark(host);
  const oldTop = host.scrollTop;
  const rows = [];
  let day = '';
  for (const message of page.messages || []) {
    const current = (message.time || '').slice(0, 10);
    if (current && current !== day) {
      day = current;
      rows.push({ key: 'day:' + current, value: { day: current }, kind: 'day' });
    }
    rows.push({ key: 'm:' + message.id, value: message, kind: 'message' });
  }
  reconcile(host, rows, row => row.kind === 'day'
    ? h('div', { class: 'date-separator', text: dayLabel(row.value.day) })
    : messageNode(row.value));
  for (const node of host.children) {
    node.classList.toggle('anchored', Number(node.dataset.messageId) === browse.anchor);
  }
  if (!page.messages?.length) {
    host.replaceChildren(emptyState('chat', page.latest_message_id ? '当前范围的消息已删除或不可用' : '此会话暂无已导入消息',
      page.latest_message_id ? '可以回到最新查看其余记录，或到数据页读取新的本机消息。' : '保持客户端登录后，在数据页读取或刷新本机记录。', null, true));
  }
  if (position === 'bottom') {
    host.scrollTop = host.scrollHeight;
    browse.following = true;
    newMessages = false;
  } else if (position === 'anchor') {
    const node = [...host.children].find(item => Number(item.dataset.messageId) === anchor);
    if (node) host.scrollTop += node.getBoundingClientRect().top - host.getBoundingClientRect().top - (host.clientHeight - node.offsetHeight) / 2;
  } else {
    restoreScroll(host, marks, oldTop);
  }
  if (page.messages?.length) {
    const first = page.messages[0];
    const last = page.messages.at(-1);
    page._bounds = { first_timestamp: first.timestamp, first_id: first.id, last_timestamp: last.timestamp, last_id: last.id };
  }
  renderConversations();
  updateControls();
  if (page.messages?.length) void loadReceipts(page);
}

async function openChat(anchor = null, conversation = null, before = 20, after = 20, keepAnchor = false) {
  if (!ui) return;
  const token = ++pageRequest;
  pageLoading = true;
  newMessages = false;
  if (!keepAnchor) browse.anchor = anchor;
  try {
    if (anchor === null && !conversation) {
      if (browse.page) conversation = [browse.page.platform, browse.page.conversation_id];
      else {
        const list = await chatFetch('/api/chat-conversations', { limit: 1 });
        if (token !== pageRequest || !mounted) return;
        if (!list.conversations.length) {
          ui.location.textContent = '暂无已导入聊天，请先在数据页读取本机消息。';
          ui.timeline.replaceChildren(emptyState('database', '还没有已导入的聊天', '打开“数据与同步”，选择本机账号并读取消息后，这里会出现会话。', null));
          return;
        }
        conversation = [list.conversations[0].platform, list.conversations[0].conversation_id];
        browse.conversations = browse.conversations.length ? browse.conversations : list.conversations;
      }
      before = 50;
      after = 0;
    }
    ui.status.textContent = '正在读取本地聊天…';
    updateControls();
    const params = { before, after };
    if (anchor !== null) params.anchor_message_id = anchor;
    if (conversation) { params.platform = conversation[0]; params.conversation_id = conversation[1]; }
    const page = await chatFetch('/api/chat-context', params);
    if (token !== pageRequest || !mounted || !ui) return;
    browse.following = anchor === null && !page.has_after;
    renderPage(page, anchor === null ? 'bottom' : 'anchor', anchor);
    ui.status.textContent = '每 2 秒更新 · 仅本地已入库';
  } catch (error) {
    if (token === pageRequest && ui) ui.status.textContent = errorMessage(error, '读取失败，正在等待重试。');
  } finally {
    if (token === pageRequest) {
      pageLoading = false;
      updateControls();
    }
  }
}

async function pollWindow(force = false) {
  if (!ui || pageLoading || !browse.page?._bounds || document.hidden) return;
  const page = browse.page;
  const token = pageRequest;
  const request = ++windowRequest;
  const follow = browse.following && !page.has_after;
  try {
    const data = await chatFetch('/api/chat-updates', {
      platform: page.platform, conversation_id: page.conversation_id,
      ...page._bounds, follow,
      since_seq: force ? -1 : page.high_water, epoch: page.epoch,
    });
    if (token !== pageRequest || request !== windowRequest || pageLoading || !mounted || !ui) return;
    if (follow && !browse.following) return;
    ui.status.textContent = '每 2 秒更新 · 仅本地已入库';
    if (data.unchanged) {
      page.high_water = data.high_water;
      page.epoch = data.epoch;
      return;
    }
    const oldLast = [page.latest_timestamp, page.latest_message_id];
    const newLast = [data.latest_timestamp, data.latest_message_id];
    const advanced = newLast[0] > oldLast[0] || (newLast[0] === oldLast[0] && newLast[1] > oldLast[1]);
    if (advanced && !follow) newMessages = true;
    const next = { ...page, ...data };
    if (follow) {
      browse.page = next;
      next._bounds = { first_timestamp: next.messages[0]?.timestamp, first_id: next.messages[0]?.id,
        last_timestamp: next.messages.at(-1)?.timestamp, last_id: next.messages.at(-1)?.id };
      newMessages = false;
    }
    renderPage(next, follow ? 'bottom' : 'preserve');
  } catch (error) {
    if (request === windowRequest && ui) ui.status.textContent = '连接中断，自动重试中';
  }
}

async function loadReceipts(page) {
  if (!page?.platform || !ui) return;
  const key = JSON.stringify([page.platform, page.conversation_id]);
  if (receiptsKey === key && Date.now() - receiptsAt < 2000) return;
  receiptsKey = key;
  receiptsAt = Date.now();
  try {
    const entries = await loadReplyReceipts(page.platform, page.conversation_id);
    if (!mounted || !ui || key !== receiptsKey) return;
    ui.receipts.hidden = !entries.length;
    ui.receiptList.replaceChildren(...entries.map(item => h('p', {
      text: new Date(item.timestamp * 1000).toLocaleString('zh-CN') + ' · 已发送\n' + item.content,
      style: { whiteSpace: 'pre-wrap' },
    })));
  } catch {
    receiptsAt = 0;
  }
}

function schedulePoll() {
  clearInterval(pollTimer);
  pollTimer = setInterval(() => {
    if (!mounted || document.hidden || !ui) return;
    void loadConversations();
    void pollWindow();
  }, 2000);
}

function closeDirectory() {
  document.body.classList.remove('browse-directory-open');
}

function buildUI() {
  const status = h('p', { text: '自动更新本地消息' });
  const directoryToggle = h('button', { class: 'quiet-button compact directory-toggle', type: 'button' }, icon('chats'), '会话');
  const head = h('div', { class: 'browse-head' },
    directoryToggle,
    h('div', {},
      h('h2', { text: '聊天记录' }),
      status),
    h('span', { class: 'spacer' }),
    h('span', { class: 'status-chip' }, h('span', { class: 'status-dot ok' }), h('span', { text: '仅本地记录' })));

  const search = h('input', { type: 'search', placeholder: '搜索联系人或群名…', 'aria-label': '搜索聊天会话' });
  const platform = h('select', { 'aria-label': '筛选聊天平台' },
    h('option', { value: '', text: '全部平台' }), h('option', { value: 'qq', text: 'QQ' }), h('option', { value: 'wechat', text: '微信' }));
  const list = h('div', { class: 'chat-conversations', 'aria-label': '会话列表' });
  const listNote = h('p', { class: 'chat-list-note' });
  const more = h('button', { class: 'quiet-button chat-list-more', type: 'button', text: '更多会话', hidden: true });
  const directory = h('aside', { class: 'chat-directory', 'aria-label': '聊天会话' },
    h('div', { class: 'chat-directory-head' },
      h('div', { class: 'chat-search-row' }, search),
      h('div', { class: 'chat-filter-row' }, platform)),
    list, listNote, more);

  const panePlatform = h('span', { class: 'chat-platform', text: '原始聊天记录' });
  const paneTitle = h('h3', { text: '选择一个会话' });
  const location = h('p', { class: 'field-hint', text: '显示本机已导入的消息和媒体' });
  const paneHead = h('header', { class: 'chat-pane-head' },
    h('div', { class: 'grow' }, panePlatform, paneTitle, location));
  const timeline = h('div', { class: 'chat-timeline', 'aria-label': '原始聊天消息' });

  const receiptsList = h('div', { class: 'reply-receipts' });
  const receipts = h('details', { class: 'chat-reply-receipts disclosure', hidden: true },
    h('summary', { text: '本工具发送回执' }),
    h('div', { class: 'disclosure-body' },
      h('p', { class: 'field-hint', text: '回执与客户端原始记录分开保存；协议发送可能不出现在桌面 QQ 的本地记录中。' }),
      receiptsList));

  const latest = h('button', { class: 'quiet-button compact', type: 'button', hidden: true, text: '回到最新' });
  const newBar = h('div', { class: 'chat-new-bar' }, latest);
  const earlier = h('button', { class: 'quiet-button compact', type: 'button', text: '更早的消息', disabled: true });
  const anchorButton = h('button', { class: 'quiet-button compact', type: 'button', text: '回到证据', disabled: true });
  const later = h('button', { class: 'quiet-button compact', type: 'button', text: '更新的消息', disabled: true });
  const paging = h('div', { class: 'chat-paging' }, earlier, anchorButton, h('span', { class: 'spacer' }), later);

  const pane = h('section', { class: 'chat-conversation-pane' },
    paneHead, timeline, receipts, newBar, paging);

  const layout = h('div', { class: 'browse-layout' }, directory, pane,
    h('button', { class: 'chat-directory-scrim', type: 'button', 'aria-label': '关闭会话列表', onclick: closeDirectory }));
  const page = h('div', { class: 'browse-page' }, head, layout);
  return { page, status, search, platform, list, listNote, more, directory, directoryToggle, timeline,
    platformLabel: panePlatform, paneTitle, location, receipts, receiptList: receiptsList,
    latest, earlier, anchorButton, later };
}

function bind(view) {
  view.directoryToggle.addEventListener('click', () => {
    document.body.classList.toggle('browse-directory-open');
  });
  const doSearch = debounce(() => {
    browse.version = null;
    browse.conversations = [];
    void loadConversations(true);
  }, 180);
  view.search.addEventListener('input', doSearch);
  view.platform.addEventListener('change', () => {
    browse.version = null;
    browse.conversations = [];
    void loadConversations(true);
  });
  view.more.addEventListener('click', () => void loadConversations(true, true));
  view.earlier.addEventListener('click', () => {
    const first = browse.page?.messages?.[0];
    if (first) void openChat(first.id, null, 40, 0, true);
  });
  view.later.addEventListener('click', () => {
    const last = browse.page?.messages?.at(-1);
    if (last) void openChat(last.id, null, 0, 40, true);
  });
  view.anchorButton.addEventListener('click', () => {
    if (browse.anchor) void openChat(browse.anchor, null, 20, 20, true);
  });
  view.latest.addEventListener('click', () => {
    if (!browse.page) return;
    newMessages = false;
    void openChat(null, [browse.page.platform, browse.page.conversation_id], 50, 0, true);
  });
  view.timeline.addEventListener('scroll', () => {
    if (!browse.page) return;
    const pane = view.timeline;
    browse.following = Boolean(!browse.page.has_after && pane.scrollHeight - pane.scrollTop - pane.clientHeight < 65);
    updateControls();
  }, { passive: true });
  view.receipts.addEventListener('toggle', () => {
    if (view.receipts.open && browse.page) void loadReceipts(browse.page);
  });
}

registerView('chats', {
  title: '聊天记录',
  subtitle: '浏览本机已导入的 QQ / 微信原文',
  async mount(root, params, query) {
    mounted = true;
    const view = buildUI();
    ui = view;
    root.append(view.page);
    bind(view);
    try {
      const options = await api('/api/vision-options', { timeout: 8000 });
      nativeVision = Boolean(options.native_available);
    } catch { nativeVision = false; }
    const last = (() => { try { return localStorage.getItem('tulpa.agentProfile') || 'standard'; } catch { return 'standard'; } })();
    const vision = (() => { try { return localStorage.getItem('tulpa.visionMode') || 'ocr'; } catch { return 'ocr'; } })();
    reply = createReplyController({
      openChat: id => void openChat(id, null, 20, 20),
      getVisionMode: () => vision,
      getProfile: () => last,
      nativeVisionAvailable: () => nativeVision,
      onReceipts: () => { if (browse.page) { receiptsAt = 0; void loadReceipts(browse.page); } },
    });
    schedulePoll();
    const anchor = Number(query.anchor);
    if (Number.isSafeInteger(anchor) && anchor > 0) {
      await openChat(anchor);
    } else if (query.platform && query.conversation_id) {
      await openChat(null, [query.platform, query.conversation_id], 50, 0);
    } else if (browse.page) {
      await openChat(null, [browse.page.platform, browse.page.conversation_id], 50, 0);
    } else {
      await loadConversations(true);
      await openChat();
    }
    return () => {
      mounted = false;
      clearInterval(pollTimer);
      clearTimeout(searchTimer);
      pageRequest += 1;
      listRequest += 1;
      closeDirectory();
      reply?.destroy();
      reply = null;
      ui = null;
    };
  },
});

/* Open a conversation from other views without a full page reload. */
export function openEvidenceMessage(messageId) {
  if (mounted) void openChat(messageId, null, 20, 20);
}
