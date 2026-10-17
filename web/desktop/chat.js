'use strict';

/* Main conversation using the existing Gradio callbacks and local APIs. */
import {
  $, h, icon, api, gradio, emit, on, registerView, setViewTitle, setSessions, setCurrentSession,
  toast, reportError, errorMessage, openDialog, confirmDialog, promptDialog, openPopover, closePopover,
  fieldLabel, switchControl, copyText, navigate, registerAction, app,
} from './app.js';
import { createGroupAdmin } from './group-admin.js';

const defaults = () => ({ platforms: ['qq', 'wechat'], conversations: [], start: '', end: '', keywords: '' });

const state = {
  ready: false,
  initializing: false,
  gradioError: '',
  busy: false,
  loading: false,
  history: [],
  evidence: '',
  progress: '',
  sid: null,
  choices: [],
  conversations: [],
  filters: defaults(),
  profiles: [],
  profile: 'deep',
  visionMode: 'ocr',
  nativeVisionAvailable: false,
  visionNote: '',
  showReasoning: true,
  stopRequested: false,
  stopSent: false,
  accepted: false,
  model: '',
  groupAdmin: null,
};

let ui = null;
let mounted = false;
let reasoningVersion = 0;
let reasoningSave = Promise.resolve();
let chatLoadToken = 0;
let pagehideInstalled = false;

/* ---------- small helpers ---------- */
function messageText(message) {
  if (!message) return '';
  if (typeof message.content === 'string') return message.content;
  return (message.content || []).map(part => part.text || '').join('');
}

function htmlToText(value) {
  const template = document.createElement('template');
  template.innerHTML = value || '';
  return template.content.textContent || '';
}

function currentLabel() {
  return state.choices.find(item => item[1] === state.sid)?.[0] || '';
}

function currentTitle() {
  if (!state.sid) return '新对话';
  return currentLabel().replace(/^\d{2}-\d{2}T\d{2}:\d{2}\s*·\s*/, '') || '对话';
}

function profileOf(id) {
  return state.profiles.find(profile => profile.id === id) || state.profiles.find(profile => profile.id === 'deep') || null;
}

function visionAvailable() {
  return Boolean(state.nativeVisionAvailable);
}

function nearBottom() {
  const pane = ui?.scroll;
  if (!pane) return true;
  return pane.scrollHeight - pane.scrollTop - pane.clientHeight < 140;
}

function scrollBottom(behavior = 'auto') {
  if (!ui?.scroll) return;
  ui.scroll.scrollTo({ top: ui.scroll.scrollHeight, behavior });
}

function updateTopbar() {
  if (!ui || !mounted) return;
  setViewTitle('对话', currentTitle() === '新对话' ? '从 QQ / 微信原话里查找答案' : currentTitle());
  updateRangeChip();
  const model = ui.modelLabel;
  if (model) model.textContent = state.model || (state.gradioError ? '内置对话不可用' : '内置模型');
  syncControls();
}

function syncControls() {
  if (!ui) return;
  const locked = !state.ready || state.loading || state.initializing;
  const busy = state.busy;
  ui.input.disabled = !state.ready || state.loading;
  ui.input.placeholder = busy ? '正在回答，可以先整理下一句…' : '随心输入，问问聊天里的事…';
  ui.send.hidden = busy || !ui.input.value.trim();
  ui.send.disabled = locked || busy || !ui.input.value.trim();
  ui.stop.hidden = !busy;
  ui.stop.disabled = state.stopRequested;
  ui.rangeChip.disabled = locked || busy;
  ui.moreButton.disabled = locked || busy;
  ui.intensityChip.disabled = locked || !state.profiles.length;
  ui.visionToggle.disabled = locked || !visionAvailable();
  ui.watchButton.disabled = locked || busy;
  if (ui.reasoning) ui.reasoning.setChecked(state.showReasoning);
  const live = ui.live;
  if (live) {
    const text = busy ? (state.stopRequested ? '正在停止…' : (state.progress.split('\n').filter(Boolean).at(-1) || '正在查找聊天里的线索…')) : '';
    live.textContent = text;
    live.hidden = !text;
  }
  if (ui.form) ui.form.classList.toggle('is-busy', busy);
}

function updateRangeChip() {
  if (!ui) return;
  const filters = state.filters;
  const filtered = JSON.stringify(filters) !== JSON.stringify(defaults());
  const names = filters.platforms.map(platform => platform === 'qq' ? 'QQ' : '微信').join(' + ');
  const scope = [names || '未选择平台', filters.conversations.length ? `${filters.conversations.length} 个会话` : '全部会话',
    filters.start || filters.end ? `${filters.start || '不限'} 至 ${filters.end || '现在'}` : '时间随问题'];
  if (filters.keywords) scope.push(`关键词：${filters.keywords}`);
  ui.rangeChip.textContent = filtered ? '已设范围' : '全部聊天';
  ui.rangeChip.title = scope.join(' · ');
  ui.rangeChip.classList.toggle('accent', filtered);
}

function updateIntensityChip() {
  if (!ui) return;
  const profile = profileOf(state.profile);
  ui.intensityLabel.textContent = profile?.label || '分析强度';
  ui.intensityChip.title = profile ? profile.description : '调整本轮分析强度';
}

/* ---------- transcripts / rendering ---------- */
function parseAssistantNode(html) {
  const template = document.createElement('template');
  template.innerHTML = html || '';
  return template.content;
}

function retainReasoning(target, incoming) {
  const old = target.querySelector('.model-reasoning');
  const oldState = old ? {
    open: old.open,
    top: old.querySelector('.reasoning-body')?.scrollTop || 0,
    follow: (() => {
      const body = old.querySelector('.reasoning-body');
      return body ? body.scrollHeight - body.scrollTop - body.clientHeight < 50 : true;
    })(),
  } : null;
  target.replaceChildren(...incoming);
  const next = target.querySelector('.model-reasoning');
  if (next && oldState) {
    next.open = oldState.open;
    const body = next.querySelector('.reasoning-body');
    if (body) body.scrollTop = oldState.follow ? body.scrollHeight : oldState.top;
  }
}

function pendingLabel() {
  const lines = state.progress.split('\n').map(line => line.trim()).filter(Boolean);
  let text = lines.at(-1) || '正在查找聊天里的线索…';
  if (text.startsWith('请求模型')) text = '正在整理线索…';
  if (text.includes('：{')) text = text.split('：{')[0] + '…';
  if (state.stopRequested) text = '正在停止…';
  return text.slice(0, 120);
}

function paintPending(node, html) {
  if (!node.querySelector('.pending-line')) {
    node.innerHTML = '<div class="pending-reasoning"></div><div class="pending-line" role="status"><span class="pulse" aria-hidden="true"></span><span class="pending-label"></span></div><details class="turn-activity"><summary>查看查询过程</summary><pre></pre></details>';
  }
  const reasoning = node.querySelector('.pending-reasoning');
  if (reasoning.dataset.html !== html) {
    const fragment = parseAssistantNode(html);
    const incoming = fragment.querySelector('.model-reasoning');
    retainReasoning(reasoning, incoming ? [incoming] : []);
    reasoning.dataset.html = html;
  }
  node.querySelector('.pending-label').textContent = pendingLabel();
  node.querySelector('.turn-activity pre').textContent = state.progress || '准备查询…';
}

function assistantActions(node, index) {
  const content = node.querySelector('.assistant-body');
  const actions = node.querySelector('.assistant-actions');
  if (!content || !actions) return;
  actions.replaceChildren();
  const copy = h('button', { class: 'text-button', type: 'button', title: '复制回答' },
    icon('copy'), '复制');
  copy.addEventListener('click', async () => {
    const claims = [...content.querySelectorAll('.claim-text')].map(item => item.textContent);
    const clone = content.cloneNode(true);
    clone.querySelectorAll('.model-reasoning,.turn-activity,.assistant-actions').forEach(item => item.remove());
    const text = claims.length ? claims.join('\n\n') : (content.querySelector('.answer-note')?.textContent || clone.textContent || '').trim();
    try { await copyText(text); toast('已复制回答。', 'success'); }
    catch { toast('复制失败，可以选中文字复制。', 'warn'); }
  });
  actions.append(copy);
  if (index === state.history.length - 1 && state.evidence.includes('<details') && !state.busy) {
    const source = h('button', { class: 'text-button', type: 'button', title: '查看本轮原文与来源' }, icon('link'), '本轮来源');
    source.addEventListener('click', showEvidence);
    actions.append(source);
  }
  const question = [...state.history.slice(0, index)].reverse().find(item => item.role === 'user');
  if (question) {
    const watch = h('button', { class: 'text-button', type: 'button', title: '从这个问题创建关注卡' }, icon('watch'), '关注此事');
    watch.addEventListener('click', () => {
      const ids = [...content.querySelectorAll('a.open-chat')].map(link => Number(link.dataset.messageId)).filter(Boolean);
      emit('watch-create', { query: htmlToText(messageText(question)).slice(0, 1600), seedIds: ids.slice(0, 20) });
    });
    actions.append(watch);
  }
}

function renderMessages(forceBottom = false) {
  if (!ui || !mounted) return;
  const follow = forceBottom || nearBottom();
  const host = ui.messages;
  ui.welcome.hidden = state.history.length > 0;
  ui.thread.classList.toggle('has-messages', state.history.length > 0);
  while (host.children.length > state.history.length) host.lastElementChild.remove();
  state.history.forEach((message, index) => {
    let article = host.children[index];
    if (!article) { article = h('article'); host.append(article); }
    const role = message.role === 'user' ? 'user' : 'assistant';
    const html = messageText(message);
    const last = index === state.history.length - 1;
    const waiting = state.busy && last && role === 'assistant' && html.includes('pending-answer');
    if (article.dataset.role !== role) {
      article.className = 'message ' + role;
      article.dataset.role = role;
      article.dataset.html = '';
      if (role === 'user') {
        article.replaceChildren(h('div', { class: 'user-bubble' }));
      } else {
        article.replaceChildren(
          h('div', { class: 'assistant-head' }, h('span', { class: 'assistant-avatar', 'aria-hidden': 'true', text: 'T' }), h('span', { class: 'assistant-name', text: 'Tulpa' })),
          h('div', { class: 'assistant-body' }),
          h('div', { class: 'assistant-actions' }));
      }
    }
    if (role === 'user') {
      const bubble = article.firstElementChild;
      const text = htmlToText(html);
      if (bubble.textContent !== text) bubble.textContent = text;
      return;
    }
    const content = article.querySelector('.assistant-body');
    const actions = article.querySelector('.assistant-actions');
    if (waiting) {
      paintPending(content, html);
      article.dataset.html = '';
      actions.replaceChildren();
      actions.hidden = true;
      return;
    }
    actions.hidden = false;
    if (article.dataset.html !== html) {
      const fragment = parseAssistantNode(html);
      retainReasoning(content, [...fragment.childNodes]);
      article.dataset.html = html;
    }
    assistantActions(article, index);
    actions.hidden = false;
  });
  if (follow) requestAnimationFrame(() => scrollBottom('auto'));
}

function showEvidence() {
  if (!state.evidence) {
    toast('这一轮没有可展开的原文来源。', 'warn');
    return;
  }
  const body = h('div', { class: 'stack' });
  const intro = h('p', { class: 'status-line', text: '以下原文由本机程序生成；结论与原文的语义一致性仍需核对。' });
  const source = h('div', { html: state.evidence });
  body.append(intro, source);
  openDialog({ title: '本轮原文与来源', subtitle: '只展示本轮实际读取的本地证据', body, wide: true });
}

/* ---------- sessions ---------- */
function applyPicker(update) {
  if (!update) return;
  if (Array.isArray(update.choices)) {
    state.choices = update.choices;
    app.sessions = update.choices.map(item => ({ label: item[0], id: item[1] }));
    setSessions(update.choices, state.sid);
  }
  if (typeof update.value === 'string' && update.value) {
    state.sid = update.value;
  }
  setCurrentSession(state.sid);
  updateTopbar();
}

function resetConversation() {
  state.history = [];
  state.evidence = '';
  state.progress = '';
  state.filters = defaults();
  state.stopRequested = false;
  state.stopSent = false;
  state.accepted = false;
  renderMessages(true);
  updateRangeChip();
  if (ui) {
    ui.input.value = '';
    fitInput();
  }
}

async function refreshSessions() {
  try {
    const result = await gradio.call('refresh_sessions', []);
    applyPicker(result?.[0]);
    return state.choices;
  } catch (error) {
    state.gradioError = error.message;
    return [];
  }
}

async function loadSession(id, { quiet = false } = {}) {
  if (!id || state.loading || state.busy) return;
  const token = ++chatLoadToken;
  state.loading = true;
  syncControls();
  try {
    const result = await gradio.call('load_session', [id]);
    if (token !== chatLoadToken) return;
    state.sid = result?.[0] || id;
    state.history = Array.isArray(result?.[1]) ? result[1] : [];
    state.evidence = typeof result?.[4] === 'string' ? result[4] : '';
    state.progress = typeof result?.[5] === 'string' ? result[5] : '';
    state.filters = {
      platforms: Array.isArray(result?.[7]) && result[7].length ? result[7] : ['qq', 'wechat'],
      conversations: Array.isArray(result?.[8]) ? result[8] : [],
      start: result?.[9] || '',
      end: result?.[10] || '',
      keywords: result?.[11] || '',
    };
    state.stopRequested = false;
    state.stopSent = false;
    state.accepted = false;
    try { localStorage.setItem('tulpa.lastConversation', state.sid); } catch { /* local choice */ }
    setCurrentSession(state.sid);
    renderMessages(true);
    updateRangeChip();
    updateTopbar();
    if (ui) {
      ui.input.value = '';
      fitInput();
    }
    state.groupAdmin?.setSession(state.sid);
  } catch (error) {
    if (!quiet) reportError(error, '对话读取失败，请稍后重试。');
  } finally {
    if (token === chatLoadToken) {
      state.loading = false;
      syncControls();
      if (!quiet) setTimeout(() => ui?.input?.focus(), 30);
    }
  }
}

async function newSession({ quiet = false } = {}) {
  if (state.loading || state.busy) {
    if (!quiet) toast('请等待当前回答结束或先停止回答。', 'warn');
    return null;
  }
  const token = ++chatLoadToken;
  state.loading = true;
  syncControls();
  try {
    const result = await gradio.call('new_session', []);
    if (token !== chatLoadToken) return null;
    state.sid = result?.[0] || null;
    applyPicker(result?.[1]);
    resetConversation();
    try { localStorage.setItem('tulpa.lastConversation', state.sid || ''); } catch { /* local choice */ }
    setCurrentSession(state.sid);
    state.groupAdmin?.setSession(state.sid);
    setTimeout(() => ui?.input?.focus(), 30);
    return state.sid;
  } catch (error) {
    if (!quiet) reportError(error, '新建对话失败，请稍后重试。');
    return null;
  } finally {
    if (token === chatLoadToken) {
      state.loading = false;
      syncControls();
    }
  }
}

async function deleteSession(id, title) {
  if (!state.ready || state.loading || state.busy) return;
  const confirmed = await confirmDialog({
    title: '删除对话',
    message: `删除对话“${title || '未命名'}”？`,
    details: '会删除这段对话的全部问答和查询过程，无法撤销。已导入的 QQ / 微信原始记录、关注卡和工作区保留。',
    confirmText: '删除对话',
    danger: true,
  });
  if (!confirmed) return;
  state.loading = true;
  syncControls();
  try {
    const result = await gradio.call('delete_session', [id, null]);
    const info = result?.[0];
    applyPicker(result?.[2]);
    if (info?.cleared_current) {
      state.sid = null;
      resetConversation();
      setCurrentSession(null);
      state.groupAdmin?.setSession(null);
    } else {
      updateTopbar();
    }
    toast('对话已删除。', 'success');
  } catch (error) {
    reportError(error, '对话删除失败，请稍后重试。');
  } finally {
    state.loading = false;
    syncControls();
  }
}

/* ---------- sending / stopping ---------- */
async function sendStop() {
  if (!state.busy || !state.accepted || state.stopSent) return;
  state.stopSent = true;
  syncControls();
  try {
    await gradio.stop();
  } catch {
    state.stopSent = false;
    state.stopRequested = false;
    syncControls();
    toast('停止请求未送达，请再试一次。', 'bad');
  }
}

function applyStreamResult(result) {
  if (!Array.isArray(result)) return;
  if (Array.isArray(result[0])) state.history = result[0];
  if (typeof result[1] === 'string') state.progress = result[1];
  if (typeof result[4] === 'string') state.evidence = result[4];
  if (result[5]) {
    if (state.sid !== result[5]) state.groupAdmin?.setSession(result[5]);
    state.sid = result[5];
  }
  if (result[6]) applyPicker(result[6]);
  if (result[7] && Object.prototype.hasOwnProperty.call(result[7], 'value') && result[7].value === '' && !state.accepted) {
    state.accepted = true;
    if (ui && ui.input.value.trim() === questionSubmitted) ui.input.value = '';
  }
}

let questionSubmitted = '';

async function sendQuestion() {
  if (!ui) return;
  closePopover();
  const question = ui.input.value.trim();
  if (!question || !state.ready || state.busy || state.loading) return;
  if (state.gradioError) { toast(state.gradioError, 'bad'); return; }
  state.busy = true;
  state.accepted = false;
  state.stopRequested = false;
  state.stopSent = false;
  state.progress = '';
  questionSubmitted = question;
  syncControls();
  fitInput();
  let firstUpdate = true;
  try {
    const filters = state.filters;
    await gradio.call('agent_action_controls', [
      question, filters.platforms, filters.conversations, filters.start, filters.end, filters.keywords,
      state.sid, state.profile, state.visionMode,
    ], (result, done) => {
      applyStreamResult(result);
      progressOnResult(result);
      if (done) state.busy = false;
      if (state.stopRequested && !state.stopSent) void sendStop();
      renderMessages(firstUpdate && state.accepted);
      firstUpdate = false;
      fitInput();
      if (done) {
        state.busy = false;
        syncControls();
        state.groupAdmin?.refresh();
        updateTopbar();
      }
    });
  } catch (error) {
    state.stopRequested = true;
    await sendStop();
    toast(errorMessage(error, '本轮回答未完成，已保存的内容可以继续查看。'), 'bad');
    if (!state.accepted && ui.input.value === '') ui.input.value = question;
  } finally {
    state.busy = false;
    syncControls();
    renderMessages();
    updateTopbar();
    fitInput();
    ui.input.focus();
  }
}

function progressOnResult(result) {
  if (!Array.isArray(result)) return;
  if (typeof result[1] === 'string' && result[1]) {
    const line = result[1].split('\n').filter(Boolean).at(-1);
    if (line) state.progress = result[1];
  }
}

/* ---------- composer growth ---------- */
function fitInput() {
  if (!ui) return;
  const input = ui.input;
  input.style.height = 'auto';
  input.style.height = Math.min(170, Math.max(40, input.scrollHeight)) + 'px';
  syncControls();
}

/* ---------- intensity ---------- */
async function loadProfiles() {
  try {
    const catalog = await api('/api/agent-presets', { timeout: 12000 });
    state.profiles = Array.isArray(catalog.profiles) ? catalog.profiles : [];
    let saved = 'deep';
    try { saved = localStorage.getItem('tulpa.agentProfile') || catalog.default || 'deep'; } catch { /* local choice */ }
    state.profile = state.profiles.some(profile => profile.id === saved) ? saved : (catalog.default || state.profiles[0]?.id || 'deep');
    updateIntensityChip();
  } catch {
    state.profiles = [];
    updateIntensityChip();
  }
}

function selectProfile(id, remember = true) {
  if (!state.profiles.some(profile => profile.id === id)) return;
  state.profile = id;
  if (remember) {
    try { localStorage.setItem('tulpa.agentProfile', id); } catch { /* local choice */ }
  }
  updateIntensityChip();
}

function openIntensity() {
  if (!ui || !state.profiles.length) return;
  const profile = profileOf(state.profile);
  const budget = profile?.budget || {};
  const content = h('div', {},
    h('div', { class: 'popover-label', text: '本次分析强度' }),
    h('div', { class: 'intensity-scale' }, state.profiles.map(item => h('button', {
      type: 'button', text: item.label, 'aria-pressed': String(item.id === state.profile),
      onclick: () => { selectProfile(item.id); closePopover(); openIntensity(); },
    }))),
    h('p', { class: 'intensity-budget' },
      h('strong', { text: profile?.label || '分析强度' }),
      ' · ' + (profile?.description || ''),
      budget.max_tool_calls ? h('span', { text: `\n最多 ${budget.max_tool_calls} 次工具调用 · ${Math.round((budget.max_seconds || 0) / 60)} 分钟 · ${budget.max_requests} 次模型请求 · 证据上限 ${Number(budget.max_evidence_chars || 0).toLocaleString('zh-CN')} 字符。` }) : null,
      h('span', { text: '\n对下一次提问生效；档位越高通常越慢、用量越大。' })));
  openPopover(ui.intensityChip, content, { align: 'end', label: '分析强度' });
}

/* ---------- vision / reasoning ---------- */
async function loadVisionOptions() {
  try {
    const options = await api('/api/vision-options', { timeout: 9000 });
    state.nativeVisionAvailable = Boolean(options.native_available);
    state.visionNote = options.note || '';
    let saved = 'ocr';
    try { saved = localStorage.getItem('tulpa.visionMode') || 'ocr'; } catch { /* local choice */ }
    state.visionMode = saved === 'native' && state.nativeVisionAvailable ? 'native' : 'ocr';
    updateVisionControl();
  } catch {
    state.nativeVisionAvailable = false;
    state.visionMode = 'ocr';
    updateVisionControl();
  }
}

function updateVisionControl() {
  if (!ui) return;
  const on = state.visionMode === 'native';
  ui.visionToggle.setAttribute('aria-checked', String(on));
  ui.visionToggle.classList.toggle('vision-on', on);
  ui.visionLabel.textContent = on ? '识图开' : '识图关';
  ui.visionToggle.title = state.nativeVisionAvailable
    ? (state.visionNote || '按需将选定图片发送给当前模型。')
    : '当前模型未提供原生识图，使用本地 OCR。';
  ui.privacy.textContent = on
    ? '识图开启：按需将指定图片和少量上下文发送给当前模型；回答可展开原文核对。'
    : '识图关闭：本地 OCR，不上传图片；相关文字片段会发送给 API。';
  syncControls();
}

function setVisionMode(mode, remember = true) {
  state.visionMode = mode === 'native' && state.nativeVisionAvailable ? 'native' : 'ocr';
  if (remember) {
    try { localStorage.setItem('tulpa.visionMode', state.visionMode); } catch { /* local choice */ }
  }
  updateVisionControl();
}

function applyReasoningPreference(show, body = document.body) {
  state.showReasoning = Boolean(show);
  body.classList.toggle('hide-model-reasoning', !state.showReasoning);
  ui?.reasoning?.setChecked(state.showReasoning);
}

async function saveReasoningPreference(show) {
  const version = ++reasoningVersion;
  try { localStorage.setItem('tulpa.showReasoning', String(show)); } catch { /* local choice */ }
  reasoningSave = reasoningSave.then(async () => {
    if (version !== reasoningVersion) return;
    await api('/api/ui-preferences', { method: 'PUT', body: { show_reasoning: Boolean(show) } });
  }).catch(() => toast('显示设置未能保存；当前页面的选择仍然生效。', 'warn'));
  await reasoningSave;
}

async function loadReasoningPreference() {
  let local = true;
  try { if (localStorage.getItem('tulpa.showReasoning') === 'false') local = false; } catch { /* local choice */ }
  applyReasoningPreference(local);
  try {
    const data = await api('/api/ui-preferences', { timeout: 8000 });
    if (typeof data.show_reasoning === 'boolean') applyReasoningPreference(data.show_reasoning);
  } catch { /* the cached choice remains usable offline */ }
}

/* ---------- range ---------- */
function renderRangeChoices(host, query, platforms, draft) {
  const fragment = document.createDocumentFragment();
  let shown = 0;
  for (const [label, value] of state.conversations) {
    let pair;
    try { pair = JSON.parse(value); } catch { continue; }
    if (!platforms.includes(pair[0])) continue;
    if (query && !label.toLocaleLowerCase().includes(query)) continue;
    shown += 1;
    const input = h('input', { type: 'checkbox', value, checked: draft.has(value) });
    input.addEventListener('change', () => { if (input.checked) draft.add(value); else draft.delete(value); });
    fragment.append(h('label', { class: 'conversation-option', title: value }, input,
      h('span', { text: label.replace(/^wechat · /, '微信 · ').replace(/^qq · /, 'QQ · ') })));
  }
  if (!shown) fragment.append(h('p', { class: 'field-hint', text: '没有匹配的会话。更新聊天数据后可能找到更多。' }));
  host.replaceChildren(fragment);
}

async function openRangeDialog() {
  if (!ui || state.busy || state.loading) return;
  if (!state.conversations.length) {
    try {
      const data = await api('/api/data/settings', { timeout: 14000 });
      state.conversations = (data.conversations || []).map(row => [
        `${row.platform === 'qq' ? 'qq' : 'wechat'} · ${row.conversation} (${row.count ?? 0})`,
        JSON.stringify([row.platform, row.conversation_id]),
      ]);
    } catch { /* the range dialog still allows dates, platforms and keywords */ }
  }
  const draft = new Set(state.filters.conversations);
  const platformBoxes = ['qq', 'wechat'].map(platform => {
    const label = platform === 'qq' ? 'QQ' : '微信';
    const input = h('input', { type: 'checkbox', value: platform, checked: state.filters.platforms.includes(platform) });
    input.addEventListener('change', () => refreshChoices());
    return { platform, input, label };
  });
  const list = h('div', { class: 'conversation-options' });
  const search = h('input', { type: 'search', placeholder: '搜索联系人或群名…', 'aria-label': '搜索会话' });
  const start = h('input', { type: 'date', value: (state.filters.start || '').slice(0, 10) });
  const end = h('input', { type: 'date', value: (state.filters.end || '').slice(0, 10) });
  const keywords = h('input', { type: 'text', placeholder: '例如：毕业设计 合同 修改', maxLength: 200, value: state.filters.keywords || '' });
  function selectedPlatforms() { return platformBoxes.filter(item => item.input.checked).map(item => item.platform); }
  function refreshChoices() {
    const platforms = selectedPlatforms();
    renderRangeChoices(list, search.value.trim().toLocaleLowerCase(), platforms, draft);
  }
  search.addEventListener('input', refreshChoices);
  refreshChoices();
  const body = h('div', {},
    h('p', { class: 'status-line', text: '通常直接在问题里说出联系人和时间即可。这里的范围对本次及后续提问生效。' }),
    h('div', { class: 'segmented', style: { marginTop: '9px' } }, platformBoxes.map(item => h('label', { class: 'check-row' }, item.input, h('span', { text: item.label })))),
    fieldLabel('联系人或群聊', search, '不勾选会话则搜索所选平台的全部已导入会话。'),
    list,
    h('div', { class: 'date-fields' }, h('label', {}, '开始日期', start), h('label', {}, '结束日期', end)),
    h('p', { class: 'field-hint', text: '北京时间；日期包含结束当天。留空时按问题判断。' }),
    fieldLabel('关键词提示（可选）', keywords));
  const footer = h('footer', { class: 'dialog-foot' },
    h('button', {
      class: 'quiet-button', type: 'button', text: '重置为全部',
      onclick: () => {
        platformBoxes.forEach(item => { item.input.checked = true; });
        draft.clear(); start.value = ''; end.value = ''; keywords.value = ''; search.value = '';
        refreshChoices();
      },
    }),
    h('span', { class: 'spacer' }),
    h('button', { class: 'quiet-button', type: 'button', text: '取消', onclick: () => ref.close() }),
    h('button', {
      class: 'primary-button', type: 'button', text: '应用范围',
      onclick: () => {
        const platforms = selectedPlatforms();
        if (!platforms.length) { toast('请至少选择 QQ 或微信。', 'warn'); return; }
        if (start.value && end.value && start.value > end.value) { toast('开始日期不能晚于结束日期。', 'warn'); return; }
        const conversations = [...draft].filter(value => {
          try { return platforms.includes(JSON.parse(value)[0]); } catch { return false; }
        });
        state.filters = {
          platforms,
          conversations,
          start: start.value,
          end: end.value,
          keywords: keywords.value.trim(),
        };
        ref.close();
        updateRangeChip();
        ui.input.focus();
      },
    }));
  const ref = openDialog({ title: '搜索范围', subtitle: '平台、会话、日期与关键词语义筛选', body, footer, wide: true });
}

/* ---------- composer menu ---------- */
function openMoreMenu() {
  if (!ui) return;
  const content = h('div', {},
    h('button', { type: 'button', onclick: () => { closePopover(); openRangeDialog(); } }, icon('filter'), '搜索范围'),
    h('button', { type: 'button', onclick: () => { closePopover(); navigateData('read'); } }, icon('data'), '聊天数据与导入'),
    h('button', { type: 'button', onclick: () => { closePopover(); emit('watch-create', { query: ui.input.value.trim() }); } }, icon('watch'), '创建关注卡'),
    h('hr'),
    h('a', { href: '/legacy', target: '_blank', rel: 'noopener', class: 'palette-item', style: { textDecoration: 'none' } }, icon('external'), h('span', { text: '高级工具 ↗' })));
  openPopover(ui.moreButton, content, { label: '更多操作' });
}
function navigateData(section) {
  navigate('data', { section });
}

/* ---------- mount ---------- */
function buildUI(root) {
  const rangeChip = h('button', { class: 'chip', type: 'button', title: '搜索范围', onclick: openRangeDialog }, icon('filter'), h('span', { text: '全部聊天' }));
  const watchButton = h('button', { class: 'quiet-button compact', type: 'button' }, icon('watch'), '关注此事');
  const moreButton = h('button', { class: 'icon-button', type: 'button', 'aria-label': '更多选项', title: '更多选项' }, icon('more'));
  const topbar = h('div', { class: 'chat-topbar' },
    h('div', { class: 'chat-context' }, h('span', { class: 'chip' }, icon('lock'), '本机资料')),
    h('span', { class: 'spacer' }),
    watchButton,
    moreButton);

  const welcome = h('section', { class: 'chat-welcome' },
    h('div', { class: 'chat-welcome-mark' }, h('img', { src: '/ui/tulpa-logo.png', alt: '' })),
    h('h2', { text: '从聊天里，找回线索。' }),
    h('p', { text: '问问最近的安排，或接着上次的话题。QQ 与微信里的原话，都有迹可循。' }),
    h('div', { class: 'welcome-status' },
      h('span', { class: 'chip' }, icon('lock'), '本机资料'),
      h('span', { class: 'chip' }, icon('link'), '每条结论可核对原文'),
      h('span', { class: 'chip' }, icon('chat'), '多轮追问')),
    h('div', { class: 'quick-prompts' },
      quickPrompt('chat', '我错过了什么？', '快速回顾近期值得关注的事', 'What did I miss? / 我错过了什么？'),
      quickPrompt('watch', '我答应过什么？', '找出本人承诺与待办线索', '我最近答应别人做过什么？'),
      quickPrompt('workspace', '总结最近一周', '按范围梳理事情经过', '总结过去一周的聊天，列出值得关注的事情。')),
    h('p', { class: 'chat-welcome-note', text: '回答只引用本轮实际读取的本地原文。模型分析仍可能有误，请结合原文核对。' }));

  const messages = h('div', { id: 'chat-messages', 'aria-live': 'polite' });
  const thread = h('div', { class: 'chat-thread' }, welcome, messages);
  const scroll = h('div', { class: 'chat-thread-scroll' }, thread);

  const input = h('textarea', {
    rows: 1, maxLength: 6000, placeholder: '随心输入，问问聊天里的事…',
    'aria-label': '输入问题', spellcheck: 'false', disabled: true,
  });
  const intensityLabel = h('span', { class: 'chip-label', text: '深入' });
  const intensityChip = h('button', { class: 'composer-chip', type: 'button', 'aria-haspopup': 'menu', disabled: true },
    icon('sliders'), intensityLabel, icon('chevron-down'));
  const visionLabel = h('span', { text: '识图关' });
  const visionToggle = h('button', { class: 'composer-chip vision-toggle', type: 'button', role: 'switch', 'aria-checked': 'false', disabled: true, title: '图片识图设置' },
    h('span', { class: 'vision-dot', 'aria-hidden': 'true' }), visionLabel);
  const modelLabel = h('span', { class: 'model-label', text: '内置模型' });
  const send = h('button', { class: 'send-button', type: 'submit', 'aria-label': '发送', title: '发送（Enter）', disabled: true }, icon('send'));
  const stop = h('button', { class: 'send-button stop-button', type: 'button', 'aria-label': '停止回答', title: '停止回答', hidden: true }, icon('stop'));
  const live = h('span', { class: 'status-line', hidden: true, role: 'status' });
  const privacy = h('p', { class: 'composer-privacy', text: '识图关闭：本地 OCR，不上传图片；相关文字片段会发送给 API。' });
  const reasoning = switchControl('显示思考过程', true, next => {
    applyReasoningPreference(next);
    void saveReasoningPreference(next);
  });
  reasoning.title = '显示接口实际返回的思考内容；仅控制显示，不改变分析强度。';

  const form = h('form', { class: 'composer', onsubmit: event => { event.preventDefault(); void sendQuestion(); } },
    h('div', { class: 'composer-text' }, input),
    h('div', { class: 'composer-toolbar' },
      h('button', { class: 'composer-chip', type: 'button', 'aria-label': '更多选项', title: '更多选项', onclick: openMoreMenu }, icon('more')),
      rangeChip,
      intensityChip,
      visionToggle,
      h('span', { class: 'spacer' }),
      modelLabel,
      send,
      stop),
    h('div', { class: 'composer-foot' }, reasoning, live),
    privacy);
  const gaHost = h('div');
  const composerDock = h('div', { class: 'chat-composer-dock' }, h('div', { class: 'chat-composer-inner' }, gaHost, form));
  const page = h('div', { class: 'chat-page' }, topbar, scroll, composerDock);

  input.addEventListener('input', fitInput);
  input.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  rangeChip.addEventListener('click', openRangeDialog);
  intensityChip.addEventListener('click', openIntensity);
  visionToggle.addEventListener('click', () => setVisionMode(state.visionMode === 'native' ? 'ocr' : 'native'));
  moreButton.addEventListener('click', openMoreMenu);
  watchButton.addEventListener('click', () => {
    const lastQuestion = [...state.history].reverse().find(item => item.role === 'user');
    const lastAssistant = [...state.history].reverse().find(item => item.role === 'assistant');
    const ids = lastAssistant ? [...parseAssistantNode(messageText(lastAssistant)).querySelectorAll('a.open-chat')].map(link => Number(link.dataset.messageId)).filter(Boolean) : [];
    emit('watch-create', {
      query: lastQuestion ? htmlToText(messageText(lastQuestion)).slice(0, 1600) : input.value.trim().slice(0, 1600),
      seedIds: ids.slice(0, 20),
    });
  });
  stop.addEventListener('click', () => {
    state.stopRequested = true;
    syncControls();
    renderMessages();
    void sendStop();
  });

  return { page, topbar, scroll, thread, welcome, messages, input, form, rangeChip, watchButton, moreButton,
    intensityChip, intensityLabel, visionToggle, visionLabel, modelLabel, send, stop, live, privacy, reasoning, gaHost };
}

function quickPrompt(iconName, title, subtitle, prompt) {
  return h('button', { type: 'button', onclick: () => { if (!ui) return; ui.input.value = prompt; fitInput(); ui.input.focus(); } },
    icon(iconName), h('strong', { text: title }), h('small', { text: subtitle }));
}

function installPagehide() {
  if (pagehideInstalled) return;
  pagehideInstalled = true;
  window.addEventListener('pagehide', () => {
    if (state.busy && state.accepted && gradio.initialized) {
      fetch('/legacy/gradio_api/run/stop_agent', {
        method: 'POST', keepalive: true, credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ data: [null], fn_index: gradio.functions.stop_agent, session_hash: gradio.hash }),
      }).catch(() => {});
    }
  });
}

async function initializeChat() {
  if (state.initializing || state.ready) return;
  state.initializing = true;
  try {
    await gradio.init();
    state.gradioError = '';
    await Promise.all([loadProfiles(), loadVisionOptions(), loadReasoningPreference()]);
    try {
      const refreshed = await gradio.run('refresh', []);
      if (Array.isArray(refreshed?.[1]?.choices)) state.conversations = refreshed[1].choices;
    } catch { /* range choices can still load from the REST data page */ }
    const choices = await refreshSessions();
    api('/api/desktop/status', { timeout: 9000 }).then(status => {
      state.model = status?.settings?.configured ? (status.settings.model || '已配置模型') : '未配置模型';
      updateTopbar();
    }).catch(() => {});
    state.ready = true;
    state.initializing = false;
    syncControls();
    const params = new URLSearchParams(location.search);
    const requested = app.current?.query?.session || params.get('session');
    let last = '';
    try { last = localStorage.getItem('tulpa.lastConversation') || ''; } catch { /* local choice */ }
    if (app.pendingNewChat) {
      app.pendingNewChat = false;
      await newSession();
    } else if (requested && choices.some(item => item[1] === requested)) {
      await loadSession(requested);
    } else if (last && choices.some(item => item[1] === last)) {
      await loadSession(last, { quiet: true });
    } else {
      renderMessages(true);
      updateTopbar();
      setTimeout(() => ui?.input?.focus(), 40);
    }
  } catch (error) {
    state.initializing = false;
    state.ready = false;
    state.gradioError = errorMessage(error, '内置对话服务未启用。');
    syncControls();
    if (ui) {
      ui.welcome.querySelector('.chat-welcome-note').textContent = state.gradioError + ' 其他数据、聊天浏览与外部 Agent 页面仍可使用。';
      ui.welcome.querySelector('.chat-welcome-note').classList.add('warning-text');
    }
    toast(state.gradioError, 'bad');
  }
}

registerView('chat', {
  title: '对话',
  subtitle: '从 QQ / 微信原话里查找答案',
  async mount(root) {
    mounted = true;
    const view = buildUI(root);
    ui = view;
    root.append(view.page);
    applyReasoningPreference(state.showReasoning);
    renderMessages(true);
    updateTopbar();
    state.groupAdmin = createGroupAdmin(view.gaHost, {
      getSessionId: () => state.sid,
      ensureSession: async () => {
        if (state.sid) return state.sid;
        return await newSession({ quiet: true });
      },
    });
    const offs = [
      on('session-open', id => { if (mounted && id !== state.sid) void loadSession(id); }),
      on('session-delete', payload => { if (mounted) void deleteSession(payload.id, payload.title); }),
      on('session-new', () => { if (mounted) void newSession(); }),
      on('data-changed', () => { if (!mounted) return; updateTopbar(); }),
    ];
    installPagehide();
    if (!state.ready) await initializeChat();
    else {
      syncControls();
      if (app.pendingNewChat) { app.pendingNewChat = false; await newSession(); }
      else { setSessions(state.choices, state.sid); updateTopbar(); }
    }
    view.input.focus();
    return () => {
      mounted = false;
      offs.forEach(off => off());
      state.groupAdmin?.destroy();
      state.groupAdmin = null;
      ui = null;
    };
  },
});

/* Expose lightweight actions used by other views. */
registerAction('open-chat', messageId => {
  navigate('chats', { anchor: messageId });
});
