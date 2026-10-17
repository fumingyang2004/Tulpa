'use strict';

/* Watch cards: persistent questions, scheduled checks and their own threads. */
import {
  h, icon, api, app, emit, on, registerView, registerAction, navigate, setViewTitle, startJob, monitorJob,
  toast, errorMessage, openDialog, confirmDialog, fieldLabel, checkbox,
  emptyState,
} from './app.js';

const state = {
  cards: [],
  conversations: [],
  settings: null,
  selected: null,
  thread: [],
  older: null,
  request: 0,
  activeJob: null,
  countdownTimer: null,
  refreshTimer: null,
  mounted: false,
};

let pendingCreate = null;
let formRef = null;
let editing = null;
let requestBusy = false;

function watchApi(path, body, method) {
  return api(path, { method: method || (body === undefined ? 'GET' : 'POST'), body, timeout: 22000 });
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function frequencyText(minutes) {
  return ({ 0: '仅手动', 60: '每小时', 360: '每 6 小时', 720: '每 12 小时', 1440: '每天', 4320: '每 3 天', 10080: '每周' })[minutes] || `每 ${minutes} 分钟`;
}

function watchFrequency(card) {
  return [card.schedule_minutes ? frequencyText(card.schedule_minutes) : '',
    card.message_threshold ? `每 ${card.message_threshold} 条新消息` : ''].filter(Boolean).join(' 或 ') || '仅手动';
}

function watchTime(value) {
  if (!value) return '尚未检查';
  const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(date.getTime())) return '尚未检查';
  return date.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });
}

function scopeName(card) {
  const labels = asArray(card.conversation_scope).map(raw => {
    try {
      const pair = JSON.parse(raw);
      const found = state.conversations.find(row => row.platform === pair[0] && row.conversation_id === pair[1]);
      return found?.conversation || pair[1];
    } catch { return raw; }
  });
  const platforms = asArray(card.platform_scope).map(p => p === 'qq' ? 'QQ' : '微信').join(' + ');
  return platforms + ' · ' + (labels.length ? labels.join('、') : '全部会话');
}

function caption(card) {
  if (card.status === 'paused') return '已暂停';
  if (card.error) return '回答未完成 · 可重试';
  if (card.pending_messages) return `${card.pending_messages} 条新消息 · 待下次更新`;
  if (!card.last_checked_at) return '待首次查询';
  const result = card.last_result || {};
  if (result.mode === 'scheduled_question') return result.answer?.insufficient ? '尚无足够证据' : '已更新回答';
  if (result.initial) return '已建立当前结论';
  if (asArray(result.changes).length) return `${result.changes.length} 项新进展`;
  if (asArray(result.new_uncertainties || result.uncertainties).length && !result.no_change) return '有待确认的新信息';
  if (result.refresh_notice) return '本地暂无新变化 · 刷新未完整';
  return '暂无新变化';
}

function countdown(card, now = Date.now()) {
  if (card.status === 'paused') return '已暂停';
  if (state.activeJob?.card_id === card.id && state.activeJob.status === 'running') return '正在检查';
  const count = card.message_threshold ? (card.last_checked_at ? `消息累计 ${card.trigger_messages || 0} / ${card.message_threshold} 条` : '待首次查询后开始计数') : '';
  if (!card.schedule_minutes || !card.next_run_at) return count || '仅手动检查';
  let seconds = Math.max(0, Math.ceil(card.next_run_at - now / 1000));
  if (!seconds) return '已到时间，等待检查' + (count ? ' · ' + count : '');
  const days = Math.floor(seconds / 86400); seconds %= 86400;
  const hours = Math.floor(seconds / 3600); seconds %= 3600;
  const minutes = Math.floor(seconds / 60); seconds %= 60;
  return '还有 ' + (days ? days + ' 天 ' : '') + (days || hours ? hours + ' 小时 ' : '') +
    (days || hours || minutes ? minutes + ' 分 ' : '') + seconds + ' 秒' + (count ? ' · ' + count : '');
}

function tickCountdowns() {
  document.querySelectorAll('[data-watch-countdown]').forEach(node => {
    const card = state.cards.find(item => item.id === node.dataset.watchCountdown);
    if (card) node.textContent = countdown(card);
  });
}

async function loadCards() {
  const data = await watchApi('/api/watch-cards');
  state.cards = Array.isArray(data.cards) ? data.cards : [];
  const active = state.cards.filter(card => card.status !== 'paused').length;
  const counter = document.querySelector('#watch-nav-count');
  if (counter) { counter.hidden = !active; counter.textContent = active > 99 ? '99+' : String(active); }
  return state.cards;
}

async function loadSettings() {
  if (state.settings && state.conversations.length) return state.settings;
  state.settings = await watchApi('/api/data/settings');
  state.conversations = Array.isArray(state.settings.conversations) ? state.settings.conversations : [];
  return state.settings;
}

function selectedCard() {
  return state.cards.find(card => card.id === state.selected) || null;
}

/* ---------- list view ---------- */
function renderList(root) {
  const page = h('div', { class: 'view-scroll' });
  const inner = h('div', { class: 'page watch-list-page' });
  page.append(inner);
  root.append(page);
  let disposed = false;

  const newButton = h('button', { class: 'primary-button', type: 'button', onclick: () => openForm({}) }, icon('plus'), '新建关注');
  const head = h('header', { class: 'home-head' },
    h('div', { class: 'home-head-copy' },
      h('div', { class: 'home-eyebrow', text: 'WATCH' }),
      h('h2', { text: '关注值得继续的事' }),
      h('p', { text: '每张关注卡有自己的对话、检查频率与当前结论。所有回答仍来自本机聊天记录。' })),
    h('div', { class: 'home-head-actions' }, newButton));
  const grid = h('div', { class: 'watch-grid' });
  const empty = emptyState('watch', '还没有关注卡', '可以从这里新建，也可以在任意回答或聊天原文中选择“关注此事”。',
    h('button', { class: 'primary-button', type: 'button', onclick: () => openForm({}) }, icon('plus'), '新建关注'), false);
  const body = h('div', {}, grid, empty);
  inner.append(head, body);
  empty.hidden = true;

  function paint() {
    if (disposed) return;
    grid.replaceChildren(...state.cards.map(card => h('button', {
      class: 'watch-card', type: 'button', onclick: () => navigate('watches/' + card.id),
    },
      h('div', { class: 'watch-card-head' },
        h('strong', { text: card.title || '未命名关注' }),
        h('span', { class: 'badge ' + (card.status === 'paused' ? '' : card.error ? 'warn' : 'ok'), text: card.status === 'paused' ? '暂停' : card.error ? '待重试' : '关注' })),
      h('p', { text: card.watch_query || '尚未填写关注问题' }),
      h('div', { class: 'watch-meta' }, icon('clock'), h('span', { text: watchFrequency(card) }), h('span', { text: '·' }), h('span', { class: 'watch-countdown', dataset: { watchCountdown: card.id }, text: countdown(card) })),
      h('div', { class: 'watch-meta' }, h('span', { text: caption(card) })))));
    empty.hidden = state.cards.length > 0;
    tickCountdowns();
  }

  void (async () => {
    try {
      await loadCards();
      if (!disposed) paint();
    } catch (error) {
      if (!disposed) inner.append(h('div', { style: { marginTop: '12px' } }, errorState(error)));
    }
  })();
  state.refreshTimer = setInterval(() => {
    if (document.hidden) return;
    void loadCards().then(paint).catch(() => {});
  }, 15000);
  state.countdownTimer = setInterval(() => { if (!document.hidden) tickCountdowns(); }, 1000);
  const off = on('job-done', () => { if (!disposed) void loadCards().then(paint).catch(() => {}); });
  paint();
  return () => { disposed = true; clearInterval(state.refreshTimer); clearInterval(state.countdownTimer); off(); };
}

function errorState(error) {
  return h('div', { class: 'card pad' },
    emptyState('alert', '关注卡暂时无法读取', errorMessage(error, '请稍后重试。'),
      h('button', { class: 'quiet-button', type: 'button', text: '重试', onclick: () => location.reload() }), true));
}

/* ---------- detail view ---------- */
function renderDetail(root, id) {
  state.selected = id;
  state.thread = [];
  state.older = null;
  state.activeJob = null;
  let disposed = false;
  let loadToken = 0;

  const page = h('div', { class: 'chat-page watch-detail' });
  const threadScroll = h('div', { class: 'chat-thread-scroll' });
  const threadHost = h('div', { class: 'chat-thread watch-thread' });
  const olderButton = h('button', { class: 'quiet-button compact', type: 'button', text: '查看更早记录', hidden: true });
  threadHost.append(olderButton);
  const entriesHost = h('div');
  threadHost.append(entriesHost);
  threadScroll.append(threadHost);

  const scopeText = h('div');
  const scheduleText = h('div');
  const checkNow = h('button', { class: 'quiet-button compact', type: 'button', text: '立即更新' });
  const editButton = h('button', { class: 'quiet-button compact', type: 'button', text: '设置' });
  const pauseButton = h('button', { class: 'quiet-button compact', type: 'button', text: '暂停' });
  const deleteButton = h('button', { class: 'quiet-button compact danger-text', type: 'button', text: '删除' });
  const back = h('button', { class: 'icon-button', type: 'button', 'aria-label': '返回关注列表', title: '返回列表', onclick: () => navigate('watches') }, icon('arrow-left'));
  const bar = h('div', { class: 'watch-threadbar' },
    back,
    h('div', { class: 'watch-threadbar-copy' }, scopeText, scheduleText),
    h('div', { class: 'watch-threadbar-actions' }, checkNow, editButton, pauseButton, deleteButton));

  const input = h('textarea', { rows: 1, maxLength: 1600, placeholder: '继续追问这件事…', 'aria-label': '关注追问' });
  const profile = h('select', { 'aria-label': '回答时的分析强度' },
    h('option', { value: 'quick', text: '快速' }), h('option', { value: 'standard', text: '标准' }),
    h('option', { value: 'deep', text: '深入' }), h('option', { value: 'thorough', text: '充分' }));
  const send = h('button', { class: 'send-button', type: 'submit', 'aria-label': '发送追问', title: '发送追问' }, icon('send'));
  const stop = h('button', { class: 'send-button stop-button', type: 'button', 'aria-label': '停止', title: '停止', hidden: true }, icon('stop'));
  const live = h('span', { class: 'status-line', hidden: true, role: 'status' });
  const composer = h('form', {
    class: 'composer',
    onsubmit: event => { event.preventDefault(); void ask(); },
  },
    h('div', { class: 'composer-text' }, input),
    h('div', { class: 'composer-toolbar' },
      h('label', { class: 'field-hint', style: { display: 'flex', alignItems: 'center', gap: '5px' } }, '强度', profile),
      h('span', { class: 'spacer' }), send, stop),
    h('div', { class: 'composer-foot' }, live));
  page.append(bar, threadScroll, h('div', { class: 'watch-composer' }, composer));
  root.append(page);

  function card() { return selectedCard(); }

  function updateHeader() {
    const item = card();
    if (!item) return;
    setViewTitle(item.title || '关注', '关注卡 · ' + watchFrequency(item));
    scopeText.textContent = scopeName(item);
    const next = item.status === 'paused' ? '已暂停' : item.next_run_at ? '下次 ' + watchTime(item.next_run_at) : '随时手动检查';
    scheduleText.replaceChildren(
      h('strong', { dataset: { watchCountdown: item.id }, text: countdown(item) }),
      document.createTextNode(' · ' + watchFrequency(item) + ' · ' + next + ' · 上次成功 ' + watchTime(item.last_checked_at)));
    pauseButton.textContent = item.status === 'paused' ? '恢复' : '暂停';
    checkNow.textContent = item.last_checked_at ? '立即更新' : '开始查询';
    checkNow.disabled = item.status === 'paused';
  }

  function updateControls() {
    const running = state.activeJob?.card_id === state.selected && state.activeJob.status === 'running';
    send.hidden = running;
    send.disabled = running || !input.value.trim();
    stop.hidden = !running;
    input.disabled = false;
    live.hidden = !running;
    live.textContent = running ? (state.activeJob.events?.at(-1) || '正在检查…') : '';
    checkNow.disabled = running || card()?.status === 'paused';
    pauseButton.disabled = running;
    deleteButton.disabled = running;
  }

  function runStamp(entry) {
    const label = entry.kind === 'question' ? '追问'
      : entry.kind === 'check' ? (entry.trigger === 'scheduled' ? '定时更新' : entry.trigger === 'message_count' ? '消息数触发更新' : '手动更新')
      : '关注设置';
    return h('div', { class: 'watch-run-stamp', text: label + (entry.created_at ? ' · ' + watchTime(entry.created_at) : '') });
  }

  function renderThread(forceBottom = false) {
    const follow = forceBottom || threadScroll.scrollHeight - threadScroll.scrollTop - threadScroll.clientHeight < 140;
    olderButton.hidden = !state.older;
    entriesHost.replaceChildren();
    const item = card();
    const hasSetup = state.thread.some(entry => entry.kind === 'setup');
    if (item && !state.older && !hasSetup) {
      entriesHost.append(h('article', { class: 'message user' }, h('div', { class: 'user-bubble', text: item.watch_query || '关注这件事的后续进展' })));
    }
    for (const entry of state.thread) {
      if (['setup', 'settings', 'question'].includes(entry.kind) && entry.question) {
        entriesHost.append(h('article', { class: 'message user' }, h('div', { class: 'user-bubble', text: entry.question })));
      }
      const article = h('article', { class: 'message assistant' },
        h('div', { class: 'assistant-head' }, h('span', { class: 'assistant-avatar', 'aria-hidden': 'true', text: 'T' }), h('span', { class: 'assistant-name', text: 'Tulpa' })),
        runStamp(entry),
        h('div', { class: 'assistant-body' }));
      const body = article.querySelector('.assistant-body');
      if (entry.status === 'running') {
        body.innerHTML = '<div class="pending-line" role="status"><span class="pulse" aria-hidden="true"></span><span class="pending-label">正在查找和核对消息…</span></div>';
      } else if (entry.html) {
        body.innerHTML = entry.html;
      } else {
        body.append(h('p', { class: 'answer-note', text: '这一轮未完成，原有结论与检查进度保留。' }));
      }
      entriesHost.append(article);
    }
    const running = state.activeJob?.card_id === state.selected && state.activeJob.status === 'running';
    if (running && !state.thread.some(entry => entry.status === 'running')) {
      entriesHost.append(h('article', { class: 'message assistant' },
        h('div', { class: 'assistant-head' }, h('span', { class: 'assistant-avatar', text: 'T' }), h('span', { class: 'assistant-name', text: 'Tulpa' })),
        h('div', { class: 'watch-run-stamp', text: state.activeJob.kind === 'question' ? '追问' : '更新关注' }),
        h('div', { class: 'assistant-body' },
          h('div', { class: 'pending-line', role: 'status' }, h('span', { class: 'pulse' }), h('span', { class: 'pending-label', text: state.activeJob.events?.at(-1) || '正在准备…' })))));
    }
    if (!entriesHost.children.length) entriesHost.append(emptyState('watch', '还没有回答', '点击“开始查询”，或在下方继续追问。', null, true));
    if (follow) requestAnimationFrame(() => { threadScroll.scrollTop = threadScroll.scrollHeight; });
  }

  async function loadThread(older = false) {
    const token = ++loadToken;
    const item = card();
    if (!item) return;
    try {
      const suffix = older && state.older ? '?before=' + state.older : '';
      const pageData = await watchApi('/api/watch-cards/' + encodeURIComponent(item.id) + '/thread' + suffix);
      if (token !== loadToken || disposed) return;
      const oldHeight = threadScroll.scrollHeight;
      if (older) state.thread = [...pageData.entries, ...state.thread];
      else {
        const first = pageData.entries[0]?.id || 0;
        state.thread = [...state.thread.filter(entry => entry.id < first), ...pageData.entries];
      }
      state.thread = [...new Map(state.thread.map(entry => [entry.id, entry])).values()];
      if (older || state.thread.length <= pageData.entries.length) state.older = pageData.older;
      state.cards = state.cards.some(card => card.id === item.id)
        ? state.cards.map(card => card.id === item.id ? pageData.card : card)
        : [pageData.card, ...state.cards];
      updateHeader();
      updateControls();
      renderThread(older);
      if (older) threadScroll.scrollTop += threadScroll.scrollHeight - oldHeight;
    } catch (error) {
      if (!disposed && token === loadToken) toast(errorMessage(error, '关注对话读取失败。'), 'bad');
    }
  }

  async function runJob(path, body, meta = {}) {
    if (requestBusy) return;
    requestBusy = true;
    try {
      const result = await startJob(path, body, { label: meta.label || '更新关注', ...meta });
      requestBusy = false;
      if (result?.job_id) {
        state.activeJob = app.jobs.get(result.job_id) || { id: result.job_id, card_id: state.selected, status: 'running', events: [] };
        updateControls();
        renderThread();
      }
    } catch (error) {
      requestBusy = false;
      toast(errorMessage(error), 'bad');
    }
  }

  async function ask() {
    const question = input.value.trim();
    const item = card();
    if (!question || !item || requestBusy) return;
    requestBusy = true;
    try {
      const result = await watchApi('/api/watch-cards/' + encodeURIComponent(item.id) + '/ask', {
        question, profile: profile.value, vision_mode: (() => { try { return localStorage.getItem('tulpa.visionMode') || 'ocr'; } catch { return 'ocr'; } })(),
      });
      input.value = '';
      input.style.height = 'auto';
      state.activeJob = { id: result.job_id, card_id: item.id, status: 'running', events: [] };
      monitorJob(result.job_id, { kind: 'question', cardId: item.id, label: '回答关注问题' });
      updateControls();
      renderThread();
    } catch (error) {
      toast(errorMessage(error), 'bad');
    } finally {
      requestBusy = false;
    }
  }

  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = Math.min(130, Math.max(38, input.scrollHeight)) + 'px';
    updateControls();
  });
  input.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
      event.preventDefault();
      composer.requestSubmit();
    }
  });
  send.addEventListener('click', () => composer.requestSubmit());
  olderButton.addEventListener('click', () => void loadThread(true));
  checkNow.addEventListener('click', () => {
    if (!card()) return;
    void runJob('/api/watch-cards/' + encodeURIComponent(card().id) + '/check', {}, { kind: 'check', label: '更新关注' });
  });
  editButton.addEventListener('click', () => { if (card()) openForm({ card: card() }); });
  deleteButton.addEventListener('click', () => void deleteCard(card()));
  pauseButton.addEventListener('click', async () => {
    const item = card();
    if (!item || requestBusy) return;
    requestBusy = true;
    try {
      await watchApi('/api/watch-cards/' + encodeURIComponent(item.id) + '/pause', { paused: item.status !== 'paused' });
      await loadCards();
      await loadThread();
    } catch (error) { toast(errorMessage(error), 'bad'); }
    finally { requestBusy = false; }
  });
  stop.addEventListener('click', async () => {
    const job = state.activeJob;
    if (!job) return;
    try {
      await watchApi('/api/watch-jobs/' + encodeURIComponent(job.id) + '/stop', {});
      toast('正在停止；若正在刷新数据，将在该步骤结束后停止。', 'warn');
    } catch (error) { toast(errorMessage(error), 'bad'); }
  });

  const offs = [
    on('job', job => {
      if (!disposed && job?.card_id === state.selected) {
        state.activeJob = job;
        updateControls();
        renderThread();
        if (job.events?.length) live.textContent = job.events.at(-1);
      }
    }),
    on('job-done', payload => {
      if (!disposed && payload?.job?.card_id === state.selected) {
        state.activeJob = null;
        void loadCards().then(() => loadThread()).catch(() => {});
      }
    }),
    on('watch-updated', updatedId => {
      if (!disposed && updatedId === state.selected) void loadCards().then(() => loadThread()).catch(() => {});
    }),
  ];

  async function boot() {
    try {
      await loadCards();
      await loadSettings();
      if (!selectedCard()) {
        navigate('watches');
        return;
      }
      profile.value = selectedCard()?.profile || 'deep';
      updateHeader();
      updateControls();
      await loadThread();
      input.focus();
    } catch (error) {
      toast(errorMessage(error, '关注卡暂时无法读取。'), 'bad');
    }
  }
  void boot();
  state.countdownTimer = setInterval(() => { if (!document.hidden) tickCountdowns(); }, 1000);
  return () => {
    disposed = true;
    loadToken += 1;
    clearInterval(state.countdownTimer);
    offs.forEach(off => off());
    state.activeJob = null;
  };
}

async function deleteCard(card) {
  if (!card) return;
  const ok = await confirmDialog({
    title: '删除关注',
    message: `删除关注“${card.title}”？`,
    details: '会删除这张卡、全部检查和追问记录，无法撤销。QQ / 微信原始记录会保留。',
    confirmText: '删除关注',
    danger: true,
  });
  if (!ok) return;
  try {
    await api('/api/watch-cards/' + encodeURIComponent(card.id), { method: 'DELETE', timeout: 20000 });
    await loadCards();
    toast('关注已删除。', 'success');
    navigate('watches');
  } catch (error) { toast(errorMessage(error), 'bad'); }
}

/* ---------- create / edit ---------- */
export async function openForm({ query = '', seedIds = [], scope = null, card = null } = {}) {
  if (formRef) return;
  try { await loadSettings(); } catch (error) { toast(errorMessage(error, '会话目录读取失败。'), 'bad'); return; }
  editing = card;
  const selected = new Set((card?.conversation_scope || (scope ? [JSON.stringify(scope)] : [])).map(raw => {
    try { return JSON.stringify(JSON.parse(raw)); } catch { return raw; }
  }));
  const platforms = card?.platform_scope || (scope ? [scope[0]] : ['qq', 'wechat']);
  const title = h('input', { type: 'text', maxLength: 100, value: card?.title || query.slice(0, 60), required: true, placeholder: '例如：智能物联网课程报告' });
  const watchQuery = h('textarea', { rows: 4, maxLength: 1600, required: true, placeholder: '例如：计算机应用数学有没有新的小测安排？', value: card?.watch_query || query.slice(0, 1600) });
  const platformChecks = ['qq', 'wechat'].map(item => {
    const input = h('input', { type: 'checkbox', checked: platforms.includes(item) });
    return { value: item, input, label: item === 'qq' ? 'QQ' : '微信' };
  });
  const search = h('input', { type: 'search', placeholder: '搜索群或联系人…' });
  const conversationList = h('div', { class: 'conversation-options' });
  const selectionSummary = h('p', { class: 'field-hint watch-selection-summary' });
  const frequency = h('select', { 'aria-label': '按时间更新' },
    [['0', '关闭定时'], ['60', '每小时'], ['360', '每 6 小时'], ['720', '每 12 小时'], ['1440', '每天'], ['4320', '每 3 天'], ['10080', '每周（每 7 天）'], ['custom', '自定义间隔']]
      .map(([value, label]) => h('option', { value, text: label })));
  const customMinutes = h('input', { type: 'number', min: 30, max: 10080, step: 1, value: 1440 });
  const customWrap = h('label', { class: 'field', hidden: true }, h('span', { text: '间隔（分钟）' }), customMinutes);
  const profileSelect = h('select', { 'aria-label': '回答时的分析强度' },
    [['quick', '快速'], ['standard', '标准'], ['deep', '深入'], ['thorough', '充分']]
      .map(([value, label]) => h('option', { value, text: label })));
  const countEnabled = checkbox('按新增消息数量自动更新', Boolean(card?.message_threshold));
  const threshold = h('input', { type: 'number', min: 1, max: 100000, step: 1, value: card?.message_threshold || 100, disabled: !card?.message_threshold });
  const countWrap = h('label', { class: 'field', hidden: !card?.message_threshold }, h('span', { text: '累计新消息（条）' }), threshold);
  const stickers = checkbox('本卡检查前刷新时读取表情包（含定时和消息数触发）', Boolean(card?.load_stickers));
  countEnabled.addEventListener('change', () => {
    countWrap.hidden = !countEnabled.input.checked;
    threshold.disabled = !countEnabled.input.checked;
  });

  function currentPlatforms() { return platformChecks.filter(item => item.input.checked).map(item => item.value); }
  function renderConversations() {
    const queryText = search.value.trim().toLocaleLowerCase();
    const allowed = currentPlatforms();
    let shown = 0;
    const fragment = document.createDocumentFragment();
    for (const row of state.conversations) {
      if (!allowed.includes(row.platform) || (queryText && !row.conversation.toLocaleLowerCase().includes(queryText))) continue;
      shown += 1;
      const key = JSON.stringify([row.platform, row.conversation_id]);
      const input = h('input', { type: 'checkbox', checked: selected.has(key) });
      input.addEventListener('change', () => { if (input.checked) selected.add(key); else selected.delete(key); updateSummary(); });
      fragment.append(h('label', { class: 'conversation-option', title: row.conversation_id }, input, h('span', { text: row.conversation + ' (' + row.count + ')' })));
    }
    if (!shown) fragment.append(h('p', { class: 'field-hint', text: '暂无匹配会话；不选会话将关注所选平台的全部已导入会话。' }));
    conversationList.replaceChildren(fragment);
    updateSummary();
  }
  function updateSummary() {
    const chosen = [...selected].filter(raw => {
      try { return currentPlatforms().includes(JSON.parse(raw)[0]); } catch { return false; }
    });
    selectionSummary.textContent = chosen.length ? `已选择 ${chosen.length} 个会话` : '未指定会话：将关注以上平台的全部已导入会话';
  }
  platformChecks.forEach(item => item.input.addEventListener('change', renderConversations));
  search.addEventListener('input', renderConversations);
  frequency.addEventListener('change', () => {
    customWrap.hidden = frequency.value !== 'custom';
    customMinutes.required = frequency.value === 'custom';
  });
  const initialFrequency = card?.schedule_minutes || 0;
  frequency.value = ['0', '60', '360', '720', '1440', '4320', '10080'].includes(String(initialFrequency)) ? String(initialFrequency) : 'custom';
  customMinutes.value = initialFrequency || 1440;
  customWrap.hidden = frequency.value !== 'custom';
  profileSelect.value = card?.profile || 'deep';
  renderConversations();

  const body = h('div', {},
    fieldLabel('标题', title),
    fieldLabel('定期询问的问题', watchQuery),
    h('div', { class: 'segmented', style: { marginTop: '9px' } },
      platformChecks.map(item => h('label', { class: 'check-row' }, item.input, h('span', { text: item.label })))),
    fieldLabel('会话范围', search, '不选会话则关注以上平台的全部已导入会话。'),
    conversationList,
    selectionSummary,
    h('div', { class: 'watch-form-grid' },
      h('div', {}, fieldLabel('按时间更新', frequency), customWrap),
      h('div', {}, fieldLabel('回答时的分析强度', profileSelect))),
    h('div', { class: 'watch-form-section' }, countEnabled, countWrap),
    h('div', { class: 'watch-form-section' }, stickers),
    h('p', { class: 'field-hint', style: { marginTop: '9px' }, text: '到时间或累计指定数量的新消息时，自动就关注的问题问一次 Agent，回答回到这段对话。定时更新即使没有新消息也会重新查询；电脑和本机服务须保持运行。创建后会先查询一次。' }));
  const footer = h('footer', { class: 'dialog-foot' },
    h('span', { class: 'field-hint', text: editing ? '保存后旧对话保留；问题或范围改变后，下一次会建立新的当前结论。' : '创建后先查询一次。' }),
    h('span', { class: 'spacer' }),
    h('button', { class: 'quiet-button', type: 'button', text: '取消', onclick: () => formRef?.close() }),
    h('button', { class: 'primary-button', type: 'button', text: editing ? '保存设置' : '创建并查询', onclick: () => void submit() }));
  formRef = openDialog({ title: editing ? '关注设置' : '新建关注', subtitle: scopeName({ platform_scope: currentPlatforms(), conversation_scope: [...selected] }), body, footer, wide: true, onClose: () => { formRef = null; } });

  async function submit() {
    const chosenPlatforms = currentPlatforms();
    if (!title.value.trim()) { toast('请填写标题。', 'warn'); title.focus(); return; }
    if (!watchQuery.value.trim()) { toast('请填写要定期询问的问题。', 'warn'); watchQuery.focus(); return; }
    if (!chosenPlatforms.length) { toast('请至少选择一个平台。', 'warn'); return; }
    const minutes = frequency.value === 'custom' ? Number(customMinutes.value) : Number(frequency.value);
    if (!Number.isFinite(minutes) || minutes < 0 || (minutes > 0 && minutes < 30)) { toast('自定义间隔至少 30 分钟。', 'warn'); return; }
    if (countEnabled.input.checked) {
      const value = Number(threshold.value);
      if (!Number.isInteger(value) || value < 1 || value > 100000) { toast('消息阈值须为 1–100000 的整数。', 'warn'); return; }
    }
    const conversations = [...selected].filter(raw => {
      try { return chosenPlatforms.includes(JSON.parse(raw)[0]); } catch { return false; }
    });
    const payload = {
      title: title.value.trim(),
      watch_query: watchQuery.value.trim(),
      platforms: chosenPlatforms,
      conversations,
      schedule_minutes: minutes,
      profile: profileSelect.value,
      load_stickers: Boolean(stickers.input.checked),
      message_threshold: countEnabled.input.checked ? Number(threshold.value) : 0,
    };
    const buttons = footer.querySelectorAll('button');
    buttons.forEach(button => { button.disabled = true; });
    try {
      if (editing) {
        await watchApi('/api/watch-cards/' + encodeURIComponent(editing.id), payload, 'PUT');
        formRef?.close();
        await loadCards();
        emit('watch-updated', editing.id);
        if (!(state.mounted && state.selected === editing.id)) navigate('watches/' + editing.id);
        toast('关注设置已保存。', 'success');
      } else {
        const result = await watchApi('/api/watch-cards', { ...payload, seed_ids: [...new Set(seedIds)].slice(0, 20) });
        formRef?.close();
        await loadCards();
        navigate('watches/' + result.card_id);
        monitorJob(result.job_id, { kind: 'check', cardId: result.card_id, label: '建立关注结论' });
      }
    } catch (error) {
      toast(errorMessage(error, '关注设置未能保存。'), 'bad');
      buttons.forEach(button => { button.disabled = false; });
    }
  }
}

/* ---------- route ---------- */
registerView('watches', {
  title: '关注',
  subtitle: '定期检查并保留独立对话',
  async mount(root, params, query) {
    state.mounted = true;
    const id = params[0] || '';
    if (query.seed) {
      const mid = Number(query.seed);
      if (Number.isSafeInteger(mid) && mid > 0) {
        try {
          const page = await api('/api/chat-context?anchor_message_id=' + mid + '&before=0&after=0', { timeout: 12000 });
          pendingCreate = { query: '关注这件事的后续进展', seedIds: [mid], scope: [page.platform, page.conversation_id] };
        } catch { /* the user can still create a card manually */ }
      }
    }
    let cleanup;
    if (id) cleanup = renderDetail(root, id);
    else {
      const listCleanup = renderList(root);
      cleanup = () => { listCleanup(); state.mounted = false; };
    }
    if (pendingCreate) {
      const payload = pendingCreate;
      pendingCreate = null;
      setTimeout(() => void openForm(payload), 60);
    }
    return () => {
      state.mounted = false;
      cleanup?.();
    };
  },
});

/* ---------- global creation hooks ---------- */
on('watch-create', payload => {
  if (!payload) return;
  if (app.current?.name === 'watches') { void openForm(payload); return; }
  pendingCreate = payload;
  navigate('watches');
});

registerAction('watch-evidence', async messageId => {
  try {
    const page = await api('/api/chat-context?anchor_message_id=' + messageId + '&before=0&after=0', { timeout: 12000 });
    await openForm({ query: '关注这件事的后续进展', seedIds: [messageId], scope: [page.platform, page.conversation_id] });
  } catch (error) {
    toast(errorMessage(error, '无法读取这条线索的会话。'), 'bad');
  }
});
