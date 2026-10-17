'use strict';

/* Local file library: search metadata first, fetch or parse only on explicit action. */
import {
  h, icon, api, app, registerView, registerAction, navigate, toast, errorMessage,
  openDialog, confirmDialog, emptyState, fmtBytes, fmtNumber, sleep, copyText,
} from './app.js';

const state = {
  status: null,
  filters: { query: '', platform: '', conversation: '', extension: '', sender: '', start: '', end: '', body: false },
  offset: 0,
  files: [],
  hasMore: false,
  matchCount: 0,
  note: '',
  currentId: null,
  current: null,
  pendingChunk: '',
  detailStatusNode: null,
  mounted: false,
};

let ui = null;
let dialogRef = null;
let searchToken = 0;

const availabilityLabels = { METADATA_ONLY: '仅有文件记录', AVAILABLE_LOCAL: '本体在本机', CACHED: '已缓存', MISSING: '本机未找到文件' };
const parseLabels = { PARSED: '已解析', PARTIAL: '部分解析', NO_TEXT: '无可提取文字' };

function fileState(file) {
  return [availabilityLabels[file.availability], parseLabels[file.parse_status],
    file.cache_status === 'ARCHIVED' ? '长期保留' : '', file.remote_status === 'absent' ? '最近目录未发现' : '']
    .filter(Boolean).join(' · ');
}

function parsePair(value) {
  try { return JSON.parse(value); } catch { return null; }
}

function conversationOptions() {
  const rows = state.status?.conversations || [];
  return rows.map(row => ({ value: JSON.stringify([row.platform, row.conversation_id]), label: `${row.platform === 'qq' ? 'QQ' : '微信'} · ${row.conversation}` }));
}

function refreshConversationSelect() {
  if (!ui) return;
  const platform = ui.platform.value;
  const current = ui.conversation.value;
  ui.conversation.replaceChildren(h('option', { value: '', text: '全部会话' }));
  for (const item of conversationOptions()) {
    const pair = parsePair(item.value);
    if (platform && pair?.[0] !== platform) continue;
    ui.conversation.append(h('option', { value: item.value, text: item.label }));
  }
  ui.conversation.value = current;
}

async function loadStatus() {
  state.status = await api('/api/artifacts/status', { timeout: 16000 });
  if (ui) {
    ui.status.textContent = `QQ ${fmtNumber(state.status.counts?.qq || 0)} 个来源 · 微信 ${fmtNumber(state.status.counts?.wechat || 0)} 个来源 · 原文件缓存 ${fmtBytes(state.status.cache_bytes)}。${state.status.note || ''}`;
    ui.inventory.disabled = !state.status.onebot_configured;
    ui.inventory.title = state.status.onebot_configured ? '只同步元数据，不下载正文' : '需要配置本机 OneBot 接口';
    refreshConversationSelect();
  }
  return state.status;
}

function fileRow(file) {
  const extension = (file.extension || '').replace(/^\./, '');
  return h('article', { class: 'file-row' },
    h('span', { class: 'file-icon' }, icon('file'), extension ? h('span', { text: extension.slice(0, 4) }) : null),
    h('div', { class: 'file-main' },
      h('strong', { text: file.filename || '未命名文件' }),
      h('p', { text: `${file.platform === 'qq' ? 'QQ' : '微信'} · ${file.conversation} · ${file.sender} · ${file.time}` }),
      h('p', { text: `${fmtBytes(file.size)} · ${fileState(file)} · ${file.folder || '聊天附件'}` }),
      file.text ? h('p', { class: 'file-snippet', text: file.text.slice(0, 260) }) : null),
    h('div', { class: 'file-side' },
      h('button', { class: 'quiet-button compact', type: 'button', text: '详情', onclick: () => void showFile(file.id) }),
      file.availability === 'METADATA_ONLY' ? h('span', { class: 'badge warn', text: '需取得' }) : null));
}

function renderResults() {
  if (!ui) return;
  ui.results.replaceChildren();
  if (!state.files.length) {
    ui.results.append(emptyState('files', '没有找到文件', state.note || '调整筛选条件，或先发现本机历史文件消息。',
      h('button', { class: 'quiet-button', type: 'button', text: '发现本地历史文件', onclick: () => void discover() })));
  } else {
    for (const file of state.files) ui.results.append(fileRow(file));
  }
  ui.resultNote.textContent = `找到 ${fmtNumber(state.matchCount)} 项。${state.note || ''}`;
  ui.more.hidden = !state.hasMore;
}

async function search(append = false) {
  if (!ui) return;
  const token = ++searchToken;
  const params = new URLSearchParams({
    query: ui.query.value,
    platform: ui.platform.value,
    extension: ui.extension.value.trim().replace(/^\./, ''),
    sender: ui.sender.value,
    start: ui.start.value,
    end: ui.end.value,
    body: String(ui.body.input.checked),
    offset: String(append ? state.offset : 0),
  });
  if (ui.conversation.value) {
    const pair = parsePair(ui.conversation.value);
    if (pair) { params.set('platform', pair[0]); params.set('conversation_id', pair[1]); }
  }
  ui.results.setAttribute('aria-busy', 'true');
  try {
    const data = await api('/api/artifacts?' + params.toString(), { timeout: 20000 });
    if (token !== searchToken || !ui) return;
    if (!append) state.files = [];
    state.files = state.files.concat(data.files || []);
    state.offset = data.next_offset || 0;
    state.hasMore = Boolean(data.has_more);
    state.matchCount = data.match_count || 0;
    state.note = data.note || '';
    renderResults();
  } catch (error) {
    if (token === searchToken && ui) {
      ui.results.replaceChildren(emptyState('alert', '文件检索未完成', errorMessage(error), null, true));
      ui.resultNote.textContent = '检索失败。';
    }
  } finally {
    if (token === searchToken && ui) ui.results.removeAttribute('aria-busy');
  }
}

async function runArtifactJob(path, body, label) {
  if (!ui) return;
  const statusNode = state.detailStatusNode;
  if (statusNode) {
    statusNode.textContent = (label || '正在处理') + '，原文件和正文均保持本地…';
    statusNode.className = 'status-line';
  }
  await api(path, { method: 'POST', body: body || {}, timeout: 30000 });
  const started = Date.now();
  while (Date.now() - started < 10 * 60 * 1000) {
    await sleep(900);
    const status = await api('/api/artifacts/status', { timeout: 12000 });
    state.status = status;
    if (status.running) continue;
    if (status.error) throw new Error(status.error);
    const result = status.result;
    return typeof result === 'string' ? result : JSON.stringify(result, null, 2);
  }
  throw new Error('文件任务耗时较长，可在稍后重新打开详情查看结果。');
}

async function discover() {
  if (!ui) return;
  try {
    ui.resultNote.textContent = '正在发现本地历史文件消息…';
    const result = await runArtifactJob('/api/artifacts-discover', {}, '发现文件消息');
    ui.resultNote.textContent = '发现完成：' + result;
    await loadStatus();
    await search();
  } catch (error) {
    ui.resultNote.textContent = errorMessage(error, '发现未完成。');
  }
}

async function inventory() {
  if (!ui) return;
  const pair = parsePair(ui.conversation.value);
  if (!pair) { toast('请先选择一个 QQ 群。', 'warn'); return; }
  if (pair[0] !== 'qq') { toast('微信文件按聊天附件发现，无群文件目录。', 'warn'); return; }
  try {
    ui.resultNote.textContent = '正在同步所选 QQ 群目录（只取元数据）…';
    const result = await runArtifactJob('/api/artifacts-inventory', { conversation_id: pair[1] }, '同步群文件目录');
    ui.resultNote.textContent = '同步完成：' + result;
    await loadStatus();
    await search();
  } catch (error) {
    ui.resultNote.textContent = errorMessage(error, '目录同步未完成。');
  }
}

async function showFile(id) {
  if (!id) return;
  if (ui) ui.detailStatus.textContent = '正在读取文件详情…';
  let file;
  try {
    file = await api('/api/artifacts/' + id, { timeout: 16000 });
  } catch (error) {
    toast(errorMessage(error, '文件详情读取失败。'), 'bad');
    return;
  }
  state.currentId = id;
  state.current = file;
  const detail = h('div', { class: 'file-detail' });
  const status = h('p', { class: 'status-line', text: '' });
  state.detailStatusNode = status;
  const actions = h('div', { class: 'file-actions' });
  const chunks = h('div', { class: 'file-chunks' });
  detail.append(
    h('h3', { text: file.filename || '未命名文件' }),
    h('p', { class: 'file-meta', text: `${file.platform === 'qq' ? 'QQ' : '微信'} · ${file.conversation} · ${file.sender} · ${file.time}` }),
    h('p', { class: 'file-meta', text: `${fmtBytes(file.size)} · ${fileState(file)} · ${file.folder || '聊天附件'}` }),
    h('div', { class: 'file-actions' },
      h('button', {
        class: 'source-button collect-evidence', type: 'button', text: '保存文件来源',
        dataset: { sourceType: 'artifact_source', sourceId: String(id) },
      })),
    actions, status);

  const definitions = [
    ['locate', '检查本机本体'],
    ['materialize', '取得原文件'],
    ['prepare', '解析 / 建立索引'],
    ['pin', '永久保留'],
    ['evict', '清理原文件缓存'],
  ];
  for (const [action, label] of definitions) {
    const button = h('button', { class: 'quiet-button compact', type: 'button', text: label });
    button.addEventListener('click', async () => {
      if (action === 'evict') {
        const ok = await confirmDialog({ title: '清理原文件缓存', message: '仅删除本项目的这份原文件缓存，保留解析文本与索引。继续？', confirmText: '清理缓存', danger: true });
        if (!ok) return;
      }
      let confirmed = false;
      if (['prepare', 'materialize', 'pin'].includes(action) && (file.size || 0) > 32 * 1024 * 1024) {
        const ok = await confirmDialog({
          title: '大文件处理', message: '此文件超过默认 32 MiB。允许本次扩大至 100 MiB？',
          details: file.filename, confirmText: '继续', danger: false,
        });
        if (!ok) return;
        confirmed = true;
      }
      button.disabled = true;
      try {
        const result = await runArtifactJob('/api/artifacts/' + id + '/action', { action, confirmed }, label);
        status.textContent = '完成：' + result;
        status.className = 'status-line success';
        await loadStatus();
        await showFile(id);
        await search();
      } catch (error) {
        status.textContent = errorMessage(error, '文件操作未完成。');
        status.className = 'status-line error';
        button.disabled = false;
      }
    });
    actions.append(button);
  }
  if (file.sha256 && file.cache_status !== 'EVICTED') {
    actions.append(h('a', { class: 'quiet-button', href: '/api/artifacts/' + id + '/content', download: file.filename || '', target: '_blank', rel: 'noopener' }, icon('download'), '打开 / 保存原文件'));
    actions.append(h('button', {
      class: 'quiet-button compact', type: 'button', text: '复制 SHA256',
      onclick: async () => { try { await copyText(file.sha256); toast('已复制 SHA256。', 'success'); } catch { toast('复制失败。', 'warn'); } },
    }));
  }
  if (file.message_id) {
    actions.append(h('button', {
      class: 'quiet-button compact', type: 'button', text: '查看聊天来源',
      onclick: () => { dialogRef?.close(); navigate('chats', { anchor: file.message_id }); },
    }));
  }
  if (file.parse_note) detail.append(h('p', { class: 'answer-note', text: file.parse_note }));
  if (file.sha256) detail.append(h('p', { class: 'file-hash', text: 'SHA256：' + file.sha256 }));

  let chunkOffset = 0;
  const nextChunks = h('button', { class: 'quiet-button compact', type: 'button', text: '阅读原文片段' });
  nextChunks.addEventListener('click', async () => {
    nextChunks.disabled = true;
    try {
      const data = await api(`/api/artifacts/${id}/chunks?offset=${chunkOffset}`, { timeout: 16000 });
      for (const chunk of data.files || []) chunks.append(chunkBlock(chunk));
      chunkOffset = data.next_offset || 0;
      nextChunks.hidden = !data.has_more;
      if (!(data.files || []).length) chunks.append(h('p', { class: 'field-hint', text: data.note || '没有可读取的片段。' }));
    } catch (error) { toast(errorMessage(error), 'bad'); }
    finally { nextChunks.disabled = false; }
  });
  detail.append(chunks, h('div', { class: 'form-actions' }, nextChunks));

  if (dialogRef?.dialog.open) {
    dialogRef.body.replaceChildren(detail);
  } else {
    dialogRef = openDialog({
      title: '文件详情', subtitle: '原始文件与解析文本均保存在本机',
      body: detail, side: true, className: 'artifact-detail-dialog',
      onClose: () => { dialogRef = null; },
    });
  }
  if (state.pendingChunk && /^[1-9]\d*$/.test(state.pendingChunk)) {
    const chunkId = state.pendingChunk;
    state.pendingChunk = '';
    try {
      const data = await api(`/api/artifacts/${id}/chunk/${chunkId}`, { timeout: 16000 });
      for (const chunk of data.files || []) {
        const block = chunkBlock(chunk);
        block.open = true;
        chunks.prepend(block);
      }
    } catch (error) { toast(errorMessage(error, '原文片段读取失败。'), 'bad'); }
  }
}

function chunkBlock(chunk) {
  const details = h('details', {},
    h('summary', { text: `${chunk.citation_id} · ${JSON.stringify(chunk.locator)}` }),
    h('pre', { text: chunk.text || '' }),
    h('button', {
      class: 'source-button collect-evidence', type: 'button', text: '保存这个原文片段',
      dataset: { sourceType: 'artifact_chunk', sourceId: `${chunk.id}:${chunk.chunk_id}` },
    }));
  return details;
}

function buildPage() {
  const query = h('input', { type: 'search', placeholder: '搜索文件名…', 'aria-label': '搜索文件名', value: state.filters.query });
  const platform = h('select', { 'aria-label': '文件平台' },
    h('option', { value: '', text: 'QQ + 微信' }), h('option', { value: 'qq', text: 'QQ' }), h('option', { value: 'wechat', text: '微信' }));
  const conversation = h('select', { 'aria-label': '文件会话' }, h('option', { value: '', text: '全部会话' }));
  const extension = h('input', { type: 'text', placeholder: 'pdf / docx / pptx / xlsx', 'aria-label': '文件类型' });
  const sender = h('input', { type: 'text', placeholder: '上传者 / 发送者', 'aria-label': '上传者或发送者' });
  const start = h('input', { type: 'date', 'aria-label': '开始日期' });
  const end = h('input', { type: 'date', 'aria-label': '截止日期' });
  const bodySearch = h('input', { type: 'checkbox' });
  const bodyLabel = h('label', { class: 'check-row', style: { flex: 'none' } }, bodySearch, h('span', { text: '搜索已解析正文' }));
  const searchButton = h('button', { class: 'primary-button compact', type: 'submit' }, icon('search'), '搜索');
  const discoverButton = h('button', { class: 'quiet-button compact', type: 'button', onclick: () => void discover() }, '发现本地历史文件消息');
  const inventoryButton = h('button', { class: 'quiet-button compact', type: 'button', disabled: true, onclick: () => void inventory() }, '同步所选 QQ 群目录');
  const status = h('p', { class: 'field-hint' });
  const resultNote = h('p', { class: 'field-hint', role: 'status' });
  const results = h('div', { class: 'files-results', 'aria-busy': 'false' });
  const more = h('button', { class: 'quiet-button compact', type: 'button', hidden: true, text: '更多文件', onclick: () => void search(true) });

  const form = h('form', { onsubmit: event => { event.preventDefault(); void search(); } },
    h('div', { class: 'files-search' }, query, searchButton),
    h('div', { class: 'files-filters' },
      platform, conversation, extension, sender,
      h('span', { class: 'field-hint', text: '日期' }), start, h('span', { class: 'field-hint', text: '至' }), end,
      bodyLabel));

  const head = h('header', { class: 'files-head' },
    h('div', { class: 'files-head-row' },
      h('div', {}, h('h2', { text: '资料 / 文件' }), status),
      h('span', { class: 'spacer' }), discoverButton, inventoryButton),
    form);
  const shell = h('div', { class: 'files-page' }, head, resultNote, results, h('div', { class: 'form-actions', style: { padding: '0 18px 12px' } }, more));
  ui = { query, platform, conversation, extension, sender, start, end, body: { input: bodySearch }, results, resultNote, more, status, inventory: inventoryButton, detailStatus: h('p') };
  platform.addEventListener('change', refreshConversationSelect);
  conversation.addEventListener('change', () => { /* search applies on submit */ });
  return shell;
}

registerView('files', {
  title: '资料 / 文件',
  subtitle: '聊天附件、原文件与解析正文',
  async mount(root, params, query) {
    state.mounted = true;
    state.filters = { query: '', platform: '', conversation: '', extension: '', sender: '', start: '', end: '', body: false };
    state.offset = 0;
    state.files = [];
    state.pendingChunk = query.chunk || '';
    const shell = buildPage();
    root.append(shell);
    try {
      await loadStatus();
      await search();
      if (query.file && /^\d+$/.test(query.file)) await showFile(Number(query.file));
    } catch (error) {
      ui.results.replaceChildren(h('div', { class: 'page' },
        emptyState('alert', '文件页暂时无法读取', errorMessage(error), null)));
    }
    return () => {
      state.mounted = false;
      searchToken += 1;
      dialogRef?.close();
      dialogRef = null;
      ui = null;
      state.files = [];
      state.current = null;
      state.detailStatusNode = null;
    };
  },
});

registerAction('open-file', id => {
  if (app.current?.name === 'files' && ui) void showFile(Number(id));
  else navigate('files', { file: id });
});
