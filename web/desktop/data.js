'use strict';

/* Data & sync: reads, live ingestion, refresh, import, media, voice and deletion. */
import {
  h, icon, api, gradio, app, on, emit, registerView, startJob, monitorJob, openJobDialog,
  toast, errorMessage, fieldLabel, checkbox, selectControl, option, renderRoute,
  emptyState, loadingState, fmtNumber, fmtTime,
} from './app.js';
import { createVoiceSettings } from './voice.js';

const PLATFORMS = ['qq', 'wechat'];
const stageNames = {
  preparing: '准备读取器', snapshot: '制作本次快照', decrypt: '验证并解密数据', strip_header: '准备数据库',
  inventory: '比较消息标识', reading: '读取消息', waiting_for_commit: '等待当前批次入库',
  exported: '核对入库结果', committing: '写入消息',
};
const liveStatusLabels = {
  unsupported: '当前平台不支持实时监听', starting: '正在启动', listening: '监听中', receiving: '读取新增',
  retrying: '正在重试', degraded: '实时读取受限', client_stopped: '客户端未运行',
  needs_baseline: '需先导入', off: '监听已关闭',
};
const autoChoices = [[0, '关闭'], [5, '每 5 分钟'], [15, '每 15 分钟'], [30, '每 30 分钟'], [60, '每小时'],
  [360, '每 6 小时'], [720, '每 12 小时'], [1440, '每天'], [4320, '每 3 天'], [10080, '每周']];

const state = {
  settings: null,
  accounts: null,
  readRules: null,
  refresh: null,
  live: null,
  mcp: null,
  native: null,
  nativeSaving: false,
  nativeDirty: false,
  readPanels: {},
  mediaSelection: new Set(),
  deleteSelection: new Set(),
  deleteToken: null,
  voice: null,
  section: 'overview',
  mounted: false,
  listeners: [],
  bannerJob: null,
};

let ui = null;
let refreshing = null;

function platformLabel(platform) { return platform === 'qq' ? 'QQ' : '微信'; }

function uniqueConversations(rows) {
  return [...new Map((rows || []).map(row => [JSON.stringify([row.platform, row.conversation_id]), row])).values()];
}

function readStored(key, fallback = '') {
  try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; }
}
function store(key, value) {
  try { localStorage.setItem(key, value); } catch { /* local preference */ }
}

function readLimitValue(platform) {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem('tulpa.readLimits') || '{}') || {}; } catch { saved = {}; }
  const rule = state.readRules?.[platform + '_per_chat'] || { default: 500, min: 1, max: 10000 };
  const value = Number(saved[platform + '_per_chat']);
  return Number.isInteger(value) && value >= rule.min && value <= rule.max ? value : rule.default;
}

async function loadBase() {
  const [settings, accounts, readRules, refresh, live, mcp, overview, native] = await Promise.allSettled([
    api('/api/data/settings', { timeout: 20000 }),
    api('/api/data/accounts', { timeout: 25000 }),
    api('/api/read-options', { timeout: 8000 }),
    api('/api/refresh-status', { timeout: 12000 }),
    api('/api/live-status', { timeout: 12000 }),
    api('/api/mcp', { timeout: 12000 }),
    api('/api/terminal/overview', { timeout: 15000 }),
    api('/api/terminal/native', { timeout: 12000 }),
  ]);
  if (settings.status === 'rejected') throw settings.reason;
  state.settings = settings.value;
  if (overview.status === 'fulfilled') app.global.overview = overview.value;
  state.accounts = accounts.status === 'fulfilled' ? accounts.value : null;
  state.readRules = readRules.status === 'fulfilled' ? readRules.value : null;
  state.refresh = refresh.status === 'fulfilled' ? refresh.value : null;
  state.live = live.status === 'fulfilled' ? live.value : null;
  state.mcp = mcp.status === 'fulfilled' ? mcp.value : null;
  state.native = native.status === 'fulfilled' ? native.value : null;
  state.settings.conversations = uniqueConversations(state.settings.conversations || []);
}

async function refreshSettings() {
  if (refreshing) return refreshing;
  refreshing = (async () => {
    try {
      const [settings, refresh, live, mcp, native] = await Promise.all([
        api('/api/data/settings', { timeout: 20000 }),
        api('/api/refresh-status', { timeout: 12000 }).catch(() => null),
        api('/api/live-status', { timeout: 12000 }).catch(() => null),
        api('/api/mcp', { timeout: 12000 }).catch(() => null),
        api('/api/terminal/native', { timeout: 12000 }).catch(() => null),
      ]);
      state.settings = settings;
      state.settings.conversations = uniqueConversations(settings.conversations || []);
      state.refresh = refresh;
      state.live = live;
      state.mcp = mcp;
      if (native) state.native = native;
      for (const panel of Object.values(state.readPanels)) panel.refreshOptions?.();
      renderOverview();
      renderNativeSource();
      renderLive();
      renderMediaPicker();
      renderDeletePicker();
      renderJobs();
      syncAutoControls();
      renderReadStatus();
    } catch { /* existing values stay visible */ }
    finally { refreshing = null; }
  })();
  return refreshing;
}

function makeConversationList(rows, selected, searchValue, allowedPlatforms, onChange) {
  const host = h('div', { class: 'conversation-options' });
  const query = (searchValue || '').trim().toLocaleLowerCase();
  let shown = 0;
  const fragment = document.createDocumentFragment();
  for (const row of rows) {
    if (!allowedPlatforms.includes(row.platform) || (query && !row.conversation.toLocaleLowerCase().includes(query))) continue;
    shown += 1;
    const key = row.conversation_id;
    const input = h('input', { type: 'checkbox', checked: selected.has(key) });
    input.addEventListener('change', () => {
      if (input.checked) selected.add(key); else selected.delete(key);
      onChange?.();
    });
    fragment.append(h('label', { class: 'conversation-option', title: row.conversation_id },
      input, h('span', { text: row.conversation + (row.count !== undefined ? ' (' + row.count + ')' : '') })));
  }
  if (!shown) fragment.append(h('p', { class: 'field-hint', text: '暂无匹配会话。可先更新会话目录。' }));
  host.replaceChildren(fragment);
  return host;
}

/* ---------- read panel ---------- */
function buildReadPanel(platform) {
  const scope = state.settings.scope?.[platform] || {};
  const accountInfo = state.accounts?.[platform] || {};
  const selected = new Set(Array.isArray(scope.conversations) ? scope.conversations : []);
  state.readPanels[platform] = {};
  const enabled = h('input', { type: 'checkbox', checked: scope.enabled !== false });
  const mode = selectControl([['all', '全部会话'], ['selected', '指定会话']], Array.isArray(scope.conversations) ? 'selected' : 'all');
  const account = h('select', { 'aria-label': '本机 ' + platformLabel(platform) + ' 账号' });
  const accountNote = h('p', { class: 'field-hint' });
  function refreshAccounts() {
    const info = state.accounts?.[platform] || {};
    const accounts = Array.isArray(info.accounts) ? info.accounts : [];
    const current = account.value || scope.account || state.settings.catalog_accounts?.[platform] || info.selected || '';
    account.replaceChildren(option('', accounts.length ? '请选择账号' : '未检测到本机账号', { disabled: accounts.length > 0 }));
    for (const id of accounts) account.append(option(id, id));
    if (current && !accounts.includes(current)) account.append(option(current, current + '（当前未找到）'));
    account.value = current;
    accountNote.textContent = info.detail || (current ? '读取后会记住该账号，后续刷新继续使用。' : '检测到多个账号时请选择本次读取的账号。');
  }
  refreshAccounts();
  const search = h('input', { type: 'search', placeholder: '搜索好友或群', 'aria-label': '搜索会话' });
  const optionsHost = h('div');
  const pick = h('div', { class: 'read-pick' }, search, optionsHost);
  const start = h('input', { type: 'date', value: readStored('tulpa.read.' + platform + '.start', '') });
  const end = h('input', { type: 'date', value: readStored('tulpa.read.' + platform + '.end', '') });
  start.addEventListener('change', () => store('tulpa.read.' + platform + '.start', start.value));
  end.addEventListener('change', () => store('tulpa.read.' + platform + '.end', end.value));
  const allMessages = checkbox('读取所选日期范围内全部消息', readStored('tulpa.read-all-' + platform, 'false') === 'true');
  allMessages.input.addEventListener('change', () => {
    store('tulpa.read-all-' + platform, String(allMessages.input.checked));
    sync();
  });
  const limit = h('input', { type: 'number', min: 1, max: 10000, step: 1, value: readLimitValue(platform), 'aria-label': platformLabel(platform) + ' 每会话条数上限' });
  limit.addEventListener('input', () => {
    if (limit.value.trim() && limit.validity.valid) {
      let saved = {};
      try { saved = JSON.parse(localStorage.getItem('tulpa.readLimits') || '{}') || {}; } catch { saved = {}; }
      saved[platform + '_per_chat'] = Number(limit.value);
      store('tulpa.readLimits', JSON.stringify(saved));
    }
  });
  const limitRow = h('div', { class: 'read-count-field' }, h('label', { text: '每会话条数上限' }), limit, h('span', { text: platform === 'qq' ? '文本、非文本各自的上限' : '含不支持类型的原始消息' }));
  function refreshOptions() {
    optionsHost.replaceChildren(makeConversationList(state.settings.conversations, selected, search.value, [platform], sync));
    sync();
  }
  function sync() {
    const on = enabled.checked;
    mode.disabled = !on;
    account.disabled = !on;
    search.disabled = !on || mode.value !== 'selected';
    start.disabled = !on;
    end.disabled = !on;
    allMessages.input.disabled = !on;
    pick.hidden = mode.value !== 'selected';
    limit.disabled = !on || allMessages.input.checked;
    optionsHost.querySelectorAll('input').forEach(input => { input.disabled = !on || mode.value !== 'selected'; });
    panel.classList.toggle('disabled', !on);
  }
  enabled.addEventListener('change', sync);
  mode.addEventListener('change', sync);
  search.addEventListener('input', refreshOptions);
  account.addEventListener('change', () => { selected.clear(); mode.value = 'all'; accountNote.textContent = account.value ? '读取后会记住该账号。' : '请选择账号后读取。'; refreshOptions(); });
  const panel = h('section', { class: 'read-platform', 'aria-label': platformLabel(platform) + ' 读取设置' },
    h('div', { class: 'read-platform-head' },
      h('label', {}, enabled, h('span', { text: platformLabel(platform) })),
      mode),
    h('label', { class: 'read-account' }, h('span', { text: '本机账号' }), account),
    accountNote,
    pick,
    h('div', { class: 'date-fields' },
      h('label', {}, '开始日期', start), h('label', {}, '截止日期（含当天）', end)),
    allMessages,
    limitRow);
  const info = {
    platform, panel, enabled, mode, account, selected, start, end, allMessages, limit,
    refreshOptions, refreshAccounts, sync,
    scope() {
      if (!enabled.checked) return { enabled: false, conversations: null };
      if (!account.value) throw new Error('请选择本机 ' + platformLabel(platform) + ' 账号；没有选项时请重新检测账号。');
      const selectedValues = mode.value === 'selected' ? [...selected] : null;
      if (mode.value === 'selected' && !selected.size) throw new Error('请勾选 ' + platformLabel(platform) + ' 会话，或选择全部会话。');
      return { enabled: true, conversations: selectedValues, account: account.value };
    },
    range() {
      if (start.value && end.value && start.value > end.value) throw new Error(platformLabel(platform) + ' 开始日期不能晚于截止日期。');
      return { start: start.value, end: end.value };
    },
    conversations() {
      return mode.value === 'selected' ? [...selected] : null;
    },
  };
  state.readPanels[platform] = info;
  refreshOptions();
  return panel;
}

function readFormBody() {
  const scope = {};
  const ranges = {};
  const allMessages = {};
  const limits = {};
  for (const platform of PLATFORMS) {
    const panel = state.readPanels[platform];
    scope[platform] = panel.scope();
    ranges[platform] = panel.range();
    allMessages[platform] = panel.allMessages.input.checked;
    let value = panel.limit.valueAsNumber;
    if (allMessages[platform]) value = 500;
    if (!Number.isInteger(value) || value < 1 || value > 10000) throw new Error('每会话条数上限须为 1–10000 之间的整数。');
    limits[platform + '_per_chat'] = value;
  }
  if (!PLATFORMS.some(platform => scope[platform].enabled)) throw new Error('请至少选择一个平台。');
  return { scope, limits, ranges, all_messages: allMessages, load_stickers: ui.readStickers.input.checked };
}

/* ---------- job banner / jobs ---------- */
function jobTitle(job) {
  return job.meta?.label || ({ read: '读取聊天记录', catalog: '更新会话目录', delete: '删除聊天记录', 'media-refresh': '刷新媒体', sync: '刷新消息', check: '更新关注', question: '回答问题' })[job.kind] || '任务';
}
function jobPercent(job) {
  const progress = job.progress;
  if (!progress?.expected) return null;
  return Math.max(0, Math.min(100, Math.round((Number(progress.scanned || 0) / Number(progress.expected)) * 100)));
}
function renderBanner(job) {
  if (!ui) return;
  const host = ui.banner;
  if (!job) { host.hidden = true; return; }
  host.hidden = false;
  const progress = job.progress;
  const running = job.status === 'running';
  const title = jobTitle(job);
  const detail = job.error || (progress
    ? `${progress.platform ? platformLabel(progress.platform) + ' · ' : ''}${stageNames[progress.stage] || '正在处理'}${progress.imported ? ' · 新增 ' + fmtNumber(progress.imported) + ' 条' : ''}`
    : (job.events?.at(-1) || (running ? '正在准备…' : '本轮处理结束')));
  const percent = jobPercent(job);
  const bar = h('div', { class: 'progress' + (percent === null && running ? ' indeterminate' : '') },
    h('span', { style: percent === null ? {} : { width: percent + '%' } }));
  const events = h('pre', { class: 'job-events', text: (job.events || []).join('\n') || '尚无过程记录。' });
  const metrics = h('div', { class: 'job-metrics' });
  if (progress) {
    for (const [label, value] of [['新增', fmtNumber(progress.imported || 0)], ['已有', fmtNumber(progress.duplicate || 0)],
      ['跳过', fmtNumber(progress.skipped || 0)], ['用时', Math.round(progress.elapsed_seconds || 0) + ' 秒']]) {
      metrics.append(h('div', {}, h('strong', { text: value }), h('span', { text: label })));
    }
  }
  const stop = h('button', {
    class: 'quiet-button compact danger-text', type: 'button', text: '停止', hidden: !running,
    onclick: async () => {
      try { await api('/api/watch-jobs/' + encodeURIComponent(job.id) + '/stop', { method: 'POST', body: {} }); toast('已请求停止，已完成的批次会保留。', 'warn'); }
      catch (error) { toast(errorMessage(error), 'bad'); }
    },
  });
  host.replaceChildren(
    h('div', { class: 'job-card-head' },
      h('strong', { text: title }),
      h('span', { class: 'badge ' + (running ? 'info' : job.status === 'error' ? 'bad' : 'ok'), text: running ? '运行中' : job.status === 'error' ? '未完成' : '已完成' }),
      h('span', { class: 'spacer', style: { flex: '1' } }), stop),
    h('p', { class: 'status-line ' + (job.status === 'error' ? 'error' : ''), text: detail }),
    bar,
    metrics,
    h('details', { class: 'disclosure', style: { marginTop: '8px' } }, h('summary', { text: '查看处理过程' }), events));
  state.bannerJob = job;
}

function renderJobs() {
  if (!ui) return;
  const jobs = [...app.jobs.values()].sort((a, b) => (b.finishedAt || 0) - (a.finishedAt || 0) || b.id.localeCompare(a.id)).slice(0, 12);
  ui.jobsHost.replaceChildren();
  if (!jobs.length) {
    ui.jobsHost.append(emptyState('clock', '还没有任务记录', '读取、刷新、导入和删除的过程会显示在这里。', null, true));
    return;
  }
  for (const job of jobs) {
    const progress = job.progress;
    ui.jobsHost.append(h('article', { class: 'job-card' },
      h('div', { class: 'job-card-head' },
        h('strong', { text: jobTitle(job) }),
        h('span', { class: 'badge ' + (job.status === 'running' ? 'info' : job.status === 'error' ? 'bad' : 'ok'), text: job.status === 'running' ? '运行中' : job.status === 'error' ? '未完成' : '已完成' })),
      h('p', { class: 'status-line ' + (job.status === 'error' ? 'error' : ''), text: job.error || job.events?.at(-1) || '尚无过程记录。' }),
      progress ? h('p', { class: 'field-hint', text: `新增 ${fmtNumber(progress.imported || 0)} · 已有 ${fmtNumber(progress.duplicate || 0)} · 跳过 ${fmtNumber(progress.skipped || 0)} · ${Math.round(progress.elapsed_seconds || 0)} 秒` }) : null,
      h('div', { class: 'form-actions' }, h('button', {
        class: 'quiet-button compact', type: 'button', text: '查看详情', onclick: () => openJobDialog(job),
      }))));
  }
}

/* ---------- overview ---------- */
function renderOverview() {
  if (!ui) return;
  const overview = app.global.overview;
  const counts = overview?.counts || {};
  const platforms = state.mcp?.data_platforms || {};
  const refresh = state.refresh;
  const syncRows = (refresh?.platforms || []).map(item => {
    const label = item.status === 'error' ? '刷新失败' : item.status === 'running' ? '刷新中' : item.last_sync_at ? '最近成功 ' + fmtTime(item.last_sync_at) : '尚未刷新';
    return h('div', { class: 'home-status-row' },
      h('span', { class: 'status-dot ' + (item.status === 'error' ? 'bad' : item.status === 'running' ? 'warn' : item.last_sync_at ? 'ok' : '') }),
      h('div', {}, h('strong', { text: platformLabel(item.platform) + ' · ' + label }),
        h('small', { text: item.detail || (item.added ? '上次新增 ' + fmtNumber(item.added) + ' 条' : '尚无成功记录') })));
  });
  const accounts = state.accounts || {};
  ui.overviewBody.replaceChildren(
    h('div', { class: 'data-status-grid' },
      h('div', { class: 'data-status-card' },
        h('header', {}, h('h3', { text: 'QQ' }), h('span', { class: 'badge ' + (platforms.qq ? 'ok' : ''), text: platforms.qq ? '已导入' : '未导入' })),
        h('p', { text: (platforms.qq ? fmtNumber(platforms.qq) + ' 条已导入消息' : '尚未导入 QQ 消息') }),
        h('p', { class: 'data-time', text: accounts.qq?.detail || '账号目录尚未检测' })),
      h('div', { class: 'data-status-card' },
        h('header', {}, h('h3', { text: '微信' }), h('span', { class: 'badge ' + (platforms.wechat ? 'ok' : ''), text: platforms.wechat ? '已导入' : '未导入' })),
        h('p', { text: (platforms.wechat ? fmtNumber(platforms.wechat) + ' 条已导入消息' : '尚未导入微信消息') }),
        h('p', { class: 'data-time', text: accounts.wechat?.detail || '账号目录尚未检测' })),
      h('div', { class: 'data-status-card' },
        h('header', {}, h('h3', { text: '本机会话' }), h('span', { class: 'badge', text: fmtNumber(state.settings?.conversations?.length || 0) })),
        h('p', { text: '已识别会话目录，包含已导入记录与最近更新。' }),
        h('p', { class: 'data-time', text: '总消息 ' + fmtNumber(counts.messages || 0) }))),
    h('div', { class: 'section', style: { marginTop: '14px' } },
      h('div', { class: 'section-head' }, h('h2', { text: '最近刷新' })),
      h('div', { class: 'card' }, h('div', { class: 'home-status-list' }, syncRows.length ? syncRows : h('p', { class: 'home-empty-inline', text: '尚无刷新记录。' })))) ,
    h('div', { class: 'form-actions', style: { marginTop: '12px' } },
      h('button', { class: 'primary-button', type: 'button', onclick: () => void doSync() }, icon('refresh'), '刷新消息'),
      h('button', { class: 'quiet-button', type: 'button', onclick: () => activate('read') }, '读取历史'),
      h('button', { class: 'quiet-button', type: 'button', onclick: () => void updateCatalog() }, '更新会话目录'),
      h('a', { class: 'quiet-button', href: '/api/desktop/diagnostics', download: 'Tulpa-diagnostics.json' }, icon('download'), '导出诊断')));
}

/* ---------- live ---------- */
function renderLive() {
  if (!ui) return;
  const snapshot = state.live || app.global.live;
  if (!snapshot) {
    ui.liveList.replaceChildren(h('p', { class: 'field-hint', text: '正在读取实时状态…' }));
    return;
  }
  ui.liveList.replaceChildren(...snapshot.platforms.map(item => h('div', { class: 'live-row' },
    h('span', { class: 'status-dot ' + (['listening', 'receiving'].includes(item.status) ? 'live' : ['degraded', 'retrying', 'client_stopped'].includes(item.status) ? 'warn' : '') }),
    h('div', {},
      h('strong', { text: platformLabel(item.platform) + ' · ' + (liveStatusLabels[item.status] || item.status) }),
      h('small', { text: (item.detail || '') + ' · 最后收到 ' + (item.last_received ? new Date(item.last_received * 1000).toLocaleTimeString('zh-CN', { hour12: false }) : '尚无') +
        (item.metrics?.seconds != null ? ' · 最近读取 ' + item.metrics.seconds + ' 秒' : '') })))));
  if (ui.liveQQ && document.activeElement !== ui.liveQQ) ui.liveQQ.value = snapshot.settings?.qq || 'database';
  if (ui.liveWechat && document.activeElement !== ui.liveWechat) ui.liveWechat.value = snapshot.settings?.wechat || 'database';
  if (ui.liveStickers && document.activeElement !== ui.liveStickers.input) ui.liveStickers.input.checked = Boolean(snapshot.settings?.load_stickers);
  if (ui.liveImages && document.activeElement !== ui.liveImages.input) ui.liveImages.input.checked = snapshot.settings?.wechat_read_images !== false;
}

async function saveLive() {
  if (!ui) return;
  ui.liveSave.disabled = true;
  try {
    const data = await api('/api/live-settings', {
      method: 'PUT',
      body: {
        qq: ui.liveQQ.value, wechat: ui.liveWechat.value,
        load_stickers: ui.liveStickers.input.checked,
        wechat_read_images: ui.liveImages.input.checked,
      },
    });
    state.live = { ...(state.live || {}), settings: data };
    toast('实时设置已保存。', 'success');
    renderLive();
  } catch (error) { toast(errorMessage(error, '实时设置未能保存。'), 'bad'); }
  finally { ui.liveSave.disabled = false; }
}

/* ---------- auto refresh ---------- */
function syncAutoControls() {
  if (!ui) return;
  const auto = state.refresh?.auto_refresh;
  if (!auto) return;
  if (document.activeElement !== ui.autoSelect) ui.autoSelect.value = String(auto.minutes ?? 0);
  if (document.activeElement !== ui.autoStickers.input) ui.autoStickers.input.checked = Boolean(auto.load_stickers);
}

async function saveAuto() {
  if (!ui) return;
  try {
    await api('/api/auto-refresh', {
      method: 'POST',
      body: { minutes: Number(ui.autoSelect.value), load_stickers: ui.autoStickers.input.checked },
    });
    toast('自动刷新设置已保存。', 'success');
    await refreshSettings();
  } catch (error) { toast(errorMessage(error, '自动刷新设置未能保存。'), 'bad'); }
}

/* ---------- sync / catalog / accounts ---------- */
async function doSync() {
  try {
    const stickers = ui?.readStickers?.input.checked ?? readStored('tulpa.loadStickers', 'false') === 'true';
    await startJob('/api/sync', { load_stickers: Boolean(stickers) }, { label: '刷新消息' });
  } catch (error) { toast(errorMessage(error), 'bad'); }
}

async function updateCatalog() {
  try {
    const platforms = PLATFORMS.filter(platform => state.readPanels[platform]?.enabled.checked);
    if (!platforms.length) throw new Error('请先启用至少一个平台。');
    const accounts = {};
    for (const platform of platforms) {
      const panel = state.readPanels[platform];
      if (!panel.account.value) throw new Error('请选择本机 ' + platformLabel(platform) + ' 账号。');
      accounts[platform] = panel.account.value;
    }
    await startJob('/api/data/catalog', { platforms, accounts }, { label: '更新会话目录' });
  } catch (error) { toast(errorMessage(error), 'bad'); }
}

async function redetectAccounts() {
  try {
    state.accounts = await api('/api/data/accounts', { timeout: 25000 });
    for (const panel of Object.values(state.readPanels)) panel.refreshAccounts?.();
    renderOverview();
    toast('账号目录已重新检测。', 'success');
    await refreshSettings();
  } catch (error) { toast(errorMessage(error, '账号检测失败。'), 'bad'); }
}

/* ---------- import ---------- */
async function runImport() {
  if (!ui) return;
  const path = ui.importPath.value.trim();
  if (!path) { toast('请填写项目内的文件或目录。', 'warn'); return; }
  ui.importResult.hidden = false;
  ui.importResult.textContent = '正在导入…';
  ui.importButton.disabled = true;
  try {
    const result = await gradio.call('import_local_options', [path, '自动识别', null, null, ui.importStickers.input.checked]);
    ui.importResult.textContent = result?.[0] || '导入请求已提交。';
    emit('data-changed');
    await refreshSettings();
  } catch (error) {
    ui.importResult.textContent = errorMessage(error, '导入未完成。');
  } finally {
    ui.importButton.disabled = false;
  }
}

/* ---------- media ---------- */
function renderMediaPicker() {
  if (!ui) return;
  const platform = ui.mediaPlatform.value;
  const mode = ui.mediaMode.value;
  ui.mediaPicker.hidden = mode === 'all';
  if (mode === 'all') return;
  ui.mediaOptions.replaceChildren(makeConversationList(
    state.settings?.conversations || [], state.mediaSelection, ui.mediaSearch.value, [platform], () => {},
  ));
}

async function runMediaRefresh() {
  if (!ui) return;
  try {
    if (!ui.mediaStart.value || !ui.mediaEnd.value) throw new Error('请填写开始和截止日期。');
    if (ui.mediaStart.value > ui.mediaEnd.value) throw new Error('开始日期不能晚于截止日期。');
    if (ui.mediaMode.value === 'selected' && !state.mediaSelection.size) throw new Error('请勾选会话，或明确选择全部已导入会话。');
    ui.mediaResult.textContent = '正在准备媒体刷新…';
    await startJob('/api/data/media-refresh', {
      platform: ui.mediaPlatform.value,
      conversations: ui.mediaMode.value === 'all' ? [] : [...state.mediaSelection],
      start: ui.mediaStart.value, end: ui.mediaEnd.value,
      load_stickers: ui.mediaStickers.input.checked,
    }, { label: '刷新媒体' });
  } catch (error) {
    ui.mediaResult.textContent = errorMessage(error, '媒体刷新未完成。');
  }
}

/* ---------- delete ---------- */
function renderDeletePicker() {
  if (!ui) return;
  const platform = ui.deletePlatform.value;
  ui.deleteOptions.replaceChildren(makeConversationList(
    (state.settings?.imported || []).filter(row => row.platform === platform),
    state.deleteSelection, ui.deleteSearch.value, [platform], invalidateDelete,
  ));
}

function invalidateDelete() {
  state.deleteToken = null;
  if (!ui) return;
  ui.deleteSummary.textContent = '';
  ui.deleteConfirm.disabled = true;
}

async function previewDelete() {
  if (!ui) return;
  invalidateDelete();
  if (!ui.deleteStart.reportValidity() || !ui.deleteEnd.reportValidity()) return;
  ui.deletePreview.disabled = true;
  try {
    const result = await api('/api/data/delete-preview', {
      method: 'POST',
      body: { platform: ui.deletePlatform.value, conversations: [...state.deleteSelection], start: ui.deleteStart.value, end: ui.deleteEnd.value },
    });
    const chosen = ui.deletePlatform.value === 'qq' ? 'QQ' : '微信';
    const names = result.all_conversations
      ? `全部会话（${result.conversations} 个会话有匹配记录）`
      : [...state.deleteSelection].length ? `${state.deleteSelection.length} 个指定会话` : '全部会话';
    ui.deleteSummary.textContent = `${chosen} · ${names} · ${ui.deleteStart.value} 至 ${ui.deleteEnd.value}（含当天）：${fmtNumber(result.messages)} 条消息，${fmtNumber(result.media)} 个媒体引用。共享媒体保留。预览 10 分钟内有效。`;
    state.deleteToken = result.messages ? result.token : null;
    ui.deleteConfirm.disabled = !state.deleteToken;
    ui.deleteConfirm.textContent = result.all_conversations
      ? `确认删除 ${chosen} 全部会话在此范围的 ${fmtNumber(result.messages)} 条消息`
      : `确认删除这 ${fmtNumber(result.messages)} 条消息`;
  } catch (error) {
    ui.deleteSummary.textContent = errorMessage(error, '删除预览失败。');
  } finally {
    ui.deletePreview.disabled = false;
  }
}

async function runDelete() {
  if (!ui || !state.deleteToken) return;
  const token = state.deleteToken;
  state.deleteToken = null;
  ui.deleteConfirm.disabled = true;
  try {
    await startJob('/api/data/delete', { token }, { label: '删除聊天记录' });
    ui.deleteSummary.textContent = '删除任务已开始；详情见上方任务进度。';
    state.deleteSelection.clear();
    renderDeletePicker();
  } catch (error) { toast(errorMessage(error), 'bad'); }
}

/* ---------- QQ history source ---------- */
function nativeQQInfo() {
  return state.native?.platforms?.qq || {};
}

function nativeSourceLabel(value) {
  if (value === 'database') return '本机 QQ 数据库';
  if (value === 'snowluma') return 'SL 托管历史消息';
  return '自动选择';
}

function renderNativeSource() {
  if (!ui?.nativeSourceInfo) return;
  if (!state.native?.platforms?.qq) {
    ui.nativeSourceInfo.replaceChildren(h('p', { class: 'field-hint', text: 'QQ 来源状态暂时无法读取；可稍后刷新，或按已保存的配置直接选择并保存。' }));
    return;
  }
  const info = nativeQQInfo();
  const saved = info.source || 'auto';
  if (!state.nativeDirty && document.activeElement !== ui.nativeSourceSelect) ui.nativeSourceSelect.value = saved;
  const actual = info.source_type === 'snowluma'
    ? (info.source_label || 'SL 托管历史消息')
    : (info.source_label || '本机数据库');
  const ready = info.source_type === 'snowluma'
    ? (info.source_ready ? 'SL 已就绪，可读取历史' : 'SL 尚未就绪，请先在 SL 托管中完成登录')
    : '按本机数据库读取';
  const key = info.requires_key
    ? '需要本机数据库密钥（读取器会按提示处理）'
    : '无需本机数据库密钥';
  const rows = [
    ['已保存选择', nativeSourceLabel(saved)],
    ['当前实际来源', actual],
    ['就绪状态', ready],
    ['密钥要求', key],
  ];
  if (info.source_account) rows.push(['SL 账号', info.source_account]);
  if (info.data_root) rows.push(['本机数据目录', info.data_root, true]);
  if (info.keys_file) rows.push(['密钥文件', info.keys_file, true]);
  ui.nativeSourceInfo.replaceChildren(
    h('div', { class: 'native-source-rows' }, rows.map(([label, value, mono]) => h('div', { class: 'native-source-row' },
      h('span', { text: label }), h('strong', { class: mono ? 'mono' : '', text: value })))),
    info.detail ? h('p', { class: 'field-hint', text: info.detail }) : null);
}

async function saveNativeSource() {
  if (!ui || state.nativeSaving) return;
  const info = nativeQQInfo();
  state.nativeSaving = true;
  ui.nativeSourceSave.disabled = true;
  ui.nativeSourceStatus.textContent = '正在保存 QQ 来源…';
  ui.nativeSourceStatus.className = 'status-line';
  try {
    const body = { source: ui.nativeSourceSelect.value };
    if (info.data_root) body.data_root = info.data_root;
    if (info.keys_file) body.keys_file = info.keys_file;
    await api('/api/terminal/native/qq', { method: 'PUT', body, timeout: 20000 });
    const [native, live] = await Promise.all([
      api('/api/terminal/native', { timeout: 12000 }),
      api('/api/live-status', { timeout: 12000 }).catch(() => null),
    ]);
    state.native = native;
    state.nativeDirty = false;
    if (live) state.live = live;
    renderNativeSource();
    renderLive();
    ui.nativeSourceStatus.hidden = false;
    ui.nativeSourceStatus.textContent = '来源已保存。旧的实时读取器已重置：请先按上方读取设置补读有界历史，再重新开启实时摄取。';
    ui.nativeSourceStatus.className = 'status-line success';
    toast('QQ 历史来源已保存。', 'success');
  } catch (error) {
    ui.nativeSourceStatus.hidden = false;
    ui.nativeSourceStatus.textContent = errorMessage(error, 'QQ 历史来源未能保存。');
    ui.nativeSourceStatus.className = 'status-line error';
  } finally {
    state.nativeSaving = false;
    ui.nativeSourceSave.disabled = false;
  }
}

/* ---------- section nav ---------- */
function activate(section) {
  if (!ui) return;
  if (!['overview', 'read', 'live', 'auto', 'import', 'media', 'voice', 'delete', 'jobs'].includes(section)) section = 'overview';
  state.section = section;
  ui.nav.querySelectorAll('button').forEach(button => {
    if (button.dataset.section === section) button.setAttribute('aria-current', 'true');
    else button.removeAttribute('aria-current');
  });
  ui.sections.querySelectorAll('.data-section').forEach(node => {
    node.classList.toggle('active', node.dataset.section === section);
  });
}

/* ---------- build ---------- */
function buildPage() {
  const banner = h('div', { class: 'job-card job-banner', hidden: true });
  const nav = h('nav', { class: 'data-nav', 'aria-label': '数据设置分类' },
    h('div', { class: 'subnav' },
      ...[['overview', '概览'], ['read', '读取聊天'], ['live', '实时摄取'], ['auto', '自动刷新'], ['import', '文件导入'], ['media', '媒体刷新'], ['voice', '语音'], ['delete', '删除记录'], ['jobs', '任务与进度']]
        .map(([id, label]) => h('button', { type: 'button', dataset: { section: id }, onclick: () => activate(id) }, label))));
  const sections = h('div', { class: 'data-content' });

  // Overview
  const overviewBody = h('div');
  const overview = h('section', { class: 'data-section', dataset: { section: 'overview' } },
    h('div', { class: 'section' },
      h('div', { class: 'section-head' }, h('h2', { text: '本机数据概览' }),
        h('div', { class: 'section-head-actions' },
          h('button', { class: 'quiet-button compact', type: 'button', text: '重新检测账号', onclick: () => void redetectAccounts() }),
          h('button', { class: 'quiet-button compact', type: 'button', text: '更新目录', onclick: () => void updateCatalog() }))),
      overviewBody));

  // Read
  const readStickers = checkbox('手动读取 / 刷新时加载表情包（含 GIF / 动态图片）', readStored('tulpa.loadStickers', 'false') === 'true');
  readStickers.input.addEventListener('change', () => store('tulpa.loadStickers', String(readStickers.input.checked)));
  const readStatus = h('p', { class: 'field-hint', role: 'status', 'data-read-status': '1' });
  const readButton = h('button', { class: 'primary-button', type: 'button', onclick: () => void runRead() }, icon('download'), '按以上设置读取');
  const readCatalog = h('button', { class: 'quiet-button compact', type: 'button', text: '从客户端更新会话列表', onclick: () => void updateCatalog() });
  const readAccounts = h('button', { class: 'quiet-button compact', type: 'button', text: '重新检测账号', onclick: () => void redetectAccounts() });
  const nativeSourceSelect = selectControl([
    ['auto', '自动选择'], ['database', '本机 QQ 数据库'], ['snowluma', 'SL 托管历史消息'],
  ], 'auto', { 'aria-label': 'QQ 历史数据来源', 'data-native-source': 'qq' });
  nativeSourceSelect.addEventListener('change', () => { state.nativeDirty = true; });
  const nativeSourceInfo = h('div', { class: 'native-source-info' });
  const nativeSourceStatus = h('p', { class: 'status-line', role: 'status', hidden: true });
  const nativeSourceSave = h('button', {
    class: 'quiet-button compact', type: 'button', text: '保存 QQ 来源',
    onclick: () => void saveNativeSource(),
  });
  const nativeSourceCard = h('div', { class: 'native-source-card' },
    h('div', { class: 'native-source-head' },
      h('strong', { text: 'QQ 历史来源' }), h('span', { class: 'badge', text: '仅 QQ' })),
    h('p', { class: 'section-note', text: '决定“读取聊天”和历史刷新使用本机 QQ 数据库，还是当前 SL 托管登录账号返回的历史。保存来源会重置旧的实时读取器；请先按读取设置补读有界历史，再重新开启实时摄取。' }),
    fieldLabel('数据来源', nativeSourceSelect),
    h('p', { class: 'field-hint', text: '自动选择会在检测到本机 QQ 数据库时优先使用它；即使 SL 托管已登录，需读取 SL 历史时也请明确选择“SL 托管历史消息”。' }),
    nativeSourceInfo,
    h('div', { class: 'form-actions' }, nativeSourceSave, nativeSourceStatus));
  const read = h('section', { class: 'data-section', dataset: { section: 'read' } },
    h('div', { class: 'section' },
      h('div', { class: 'section-head' }, h('h2', { text: '下一次从客户端读取' }),
        h('div', { class: 'section-head-actions' }, readCatalog, readAccounts)),
      h('p', { class: 'section-note', text: '平台和会话选择用于本次历史读取，以及后续手动与定时刷新；不会关闭实时监听。日期和条数仅用于本次补读。扩大范围时须在这里补读，之前跳过的历史不会自动补回。' }),
      nativeSourceCard,
      buildReadPanel('qq'),
      buildReadPanel('wechat'),
      h('p', { class: 'field-hint', text: '北京时间，截止日期包含当天。开始留空表示不限开始时间，截止留空表示至今。未勾选“全部消息”时，每会话读取范围内最新的 1–10000 条。大范围会自动分批完成。' }),
      readStickers,
      h('div', { class: 'form-actions' }, readButton, h('button', { class: 'quiet-button', type: 'button', onclick: () => void doSync() }, icon('refresh'), '仅刷新新增消息'), readStatus),
      h('p', { class: 'field-hint', text: '找不到好友或群时更新会话列表；此操作不导入正文。按本机数据目录列出账号，不代表都已登录。' })));

  // Live
  const liveQQ = selectControl([['database', '数据库监听'], ['off', '关闭']]);
  const liveWechat = selectControl([['database', '数据库监听'], ['off', '关闭']]);
  const liveStickers = checkbox('实时载入表情包', false);
  const liveImages = checkbox('读取微信图片（历史导入和新增消息）', true);
  const liveList = h('div', { class: 'live-status-list' });
  const liveSave = h('button', { class: 'quiet-button', type: 'button', onclick: () => void saveLive() }, '保存实时设置');
  const live = h('section', { class: 'data-section', dataset: { section: 'live' } },
    h('div', { class: 'section' },
      h('div', { class: 'section-head' }, h('h2', { text: '实时摄取' }), h('span', { class: 'badge', text: '独立于历史导入' })),
      h('p', { class: 'section-note', text: '实时监听独立于上方的历史导入选择。启用后读取该平台本机可识别会话的新增消息；临时只导入某个好友或只勾选 QQ，不会暂停其他会话或微信监听。' }),
      liveList,
      h('div', { class: 'live-controls' }, fieldLabel('QQ', liveQQ), fieldLabel('微信', liveWechat)),
      h('div', { class: 'check-stack', style: { marginTop: '9px' } }, liveStickers, liveImages),
      h('p', { class: 'field-hint', text: '表情包默认关闭，实时设置在重启后保留。新消息直接进入本机数据库，不逐条调用 AI。读取受限或需补漏时，请手动刷新或开启自动刷新。' }),
      h('div', { class: 'form-actions' }, liveSave)));

  // Auto refresh
  const autoSelect = selectControl(autoChoices);
  const autoStickers = checkbox('自动刷新时读取表情包', false);
  const auto = h('section', { class: 'data-section', dataset: { section: 'auto' } },
    h('div', { class: 'section' },
      h('div', { class: 'section-head' }, h('h2', { text: '自动刷新' })),
      h('p', { class: 'section-note', text: '只自动同步新增消息，不调用模型，也不检查关注卡。关注卡有各自独立、可保存的检查频率。重启后自动刷新关闭。' }),
      h('div', { class: 'field-grid' }, fieldLabel('全局数据刷新', autoSelect), h('div', { style: { display: 'flex', alignItems: 'flex-end', paddingBottom: '6px' } }, autoStickers)),
      h('p', { class: 'field-hint', text: '刷新期间可继续对话；本轮使用提问时已入库的消息。' }),
      h('div', { class: 'form-actions' },
        h('button', { class: 'quiet-button', type: 'button', onclick: () => void saveAuto() }, '保存自动刷新设置'),
        h('button', { class: 'quiet-button', type: 'button', onclick: () => void doSync() }, icon('refresh'), '立即刷新')),
      h('details', { class: 'data-details' },
        h('summary', { text: '同步进度修复' }),
        h('p', { class: 'field-hint', text: '仅在数据库被替换、清理导致游标失效时使用：先按读取页范围补读历史，再重新接续。不会删除已导入消息。' }),
        h('div', { class: 'form-actions' },
          h('button', { class: 'quiet-button compact', type: 'button', text: '重新接续 QQ', onclick: () => void reconnect('qq') }),
          h('button', { class: 'quiet-button compact', type: 'button', text: '重新接续微信', onclick: () => void reconnect('wechat') })))));

  // Import
  const importPath = h('input', { type: 'text', value: 'imports', maxLength: 400, spellcheck: 'false' });
  const importStickers = checkbox('导入时加载表情包', false);
  const importResult = h('pre', { class: 'read-progress', role: 'status', hidden: true });
  const importButton = h('button', { class: 'quiet-button', type: 'button', onclick: () => void runImport() }, '导入记录');
  const importSection = h('section', { class: 'data-section', dataset: { section: 'import' } },
    h('div', { class: 'section' },
      h('div', { class: 'section-head' }, h('h2', { text: '从导出文件导入' })),
      h('p', { class: 'section-note', text: '将 JSON / JSONL 文件放入程序目录下的 imports 文件夹。仅导入文件中已有的记录，不受读取页日期与条数设置影响。单文件上限 100 MB；支持 QCE、wx-cli、ChatLab、WeFlow 和统一格式。' }),
      h('div', { class: 'field-grid' },
        fieldLabel('项目内文件或目录', importPath, '相对路径按程序目录解析。'),
        h('div', { style: { display: 'flex', alignItems: 'flex-end', paddingBottom: '7px' } }, importStickers)),
      h('div', { class: 'form-actions' }, importButton),
      importResult));

  // Media
  const mediaPlatform = selectControl([['qq', 'QQ'], ['wechat', '微信']]);
  const mediaMode = selectControl([['selected', '指定会话'], ['all', '该平台全部已导入会话']]);
  const mediaSearch = h('input', { type: 'search', placeholder: '查找群聊或好友', 'aria-label': '查找媒体会话' });
  const mediaOptions = h('div');
  const mediaPicker = h('div', {}, mediaSearch, mediaOptions);
  const mediaStart = h('input', { type: 'date', required: true });
  const mediaEnd = h('input', { type: 'date', required: true });
  const mediaStickers = checkbox('本次也恢复表情包（默认关闭）', false);
  const mediaResult = h('p', { class: 'field-hint', role: 'status' });
  const media = h('section', { class: 'data-section', dataset: { section: 'media' } },
    h('div', { class: 'section' },
      h('div', { class: 'section-head' }, h('h2', { text: '刷新已备份聊天的图片 / 表情' })),
      h('p', { class: 'section-note', text: '按会话和日期重试缺失媒体，可用时将缩略图升级为原图。只检查本机缓存，不重新导入聊天、不移动同步进度、不调用 AI；客户端尚未缓存的图片仍会显示不可用。' }),
      h('div', { class: 'field-grid' }, fieldLabel('平台', mediaPlatform), fieldLabel('聊天范围', mediaMode)),
      mediaPicker,
      h('div', { class: 'date-fields' }, h('label', {}, '开始日期', mediaStart), h('label', {}, '结束日期（含当天）', mediaEnd)),
      mediaStickers,
      h('div', { class: 'form-actions' }, h('button', { class: 'quiet-button', type: 'button', onclick: () => void runMediaRefresh() }, '刷新所选范围的媒体'), mediaResult)));

  // Voice
  const voice = h('section', { class: 'data-section', dataset: { section: 'voice' } },
    h('div', { class: 'section' },
      h('div', { class: 'section-head' }, h('h2', { text: '原生语音 · 本地转写' })),
      h('div', { id: 'voice-settings-mount' })));

  // Delete
  const deletePlatform = selectControl([['qq', 'QQ'], ['wechat', '微信']]);
  const deleteSearch = h('input', { type: 'search', placeholder: '查找会话以缩小范围（可选）', 'aria-label': '查找要删除的会话' });
  const deleteOptions = h('div');
  const deleteStart = h('input', { type: 'date', required: true });
  const deleteEnd = h('input', { type: 'date', required: true });
  const deletePreview = h('button', { class: 'quiet-button', type: 'button', onclick: () => void previewDelete() }, '预览删除范围');
  const deleteSummary = h('p', { class: 'field-hint', role: 'status' });
  const deleteConfirm = h('button', { class: 'danger-button', type: 'button', disabled: true, onclick: () => void runDelete() }, '确认删除');
  const deleteSection = h('section', { class: 'data-section', dataset: { section: 'delete' } },
    h('div', { class: 'section' },
      h('div', { class: 'section-head' }, h('h2', { text: '删除已导入聊天记录 · 释放空间' })),
      h('p', { class: 'section-note', text: '仅删除本项目记录，不改 QQ / 微信客户端。会清理对应来源归档及不再被其他消息使用的媒体，并压缩数据库；删除不可撤销。客户端快照缓存、imports 原文件、备份和报告、历史 AI 答案仍会保留。' }),
      h('div', { class: 'field-grid' }, fieldLabel('平台', deletePlatform)),
      h('p', { class: 'field-hint', text: '会话可选：不勾选表示所选平台的全部会话，仍受下方日期范围限制。搜索框只过滤列表，不限制删除范围。' }),
      deleteSearch, deleteOptions,
      h('div', { class: 'date-fields' }, h('label', {}, '开始日期', deleteStart), h('label', {}, '结束日期（含当天）', deleteEnd)),
      h('div', { class: 'form-actions' }, deletePreview, deleteConfirm),
      deleteSummary));

  // Jobs
  const jobsHost = h('div');
  const jobs = h('section', { class: 'data-section', dataset: { section: 'jobs' } },
    h('div', { class: 'section' },
      h('div', { class: 'section-head' }, h('h2', { text: '任务与进度' }),
        h('div', { class: 'section-head-actions' }, h('button', { class: 'quiet-button compact', type: 'button', text: '刷新状态', onclick: () => void refreshSettings() }))),
      h('p', { class: 'section-note', text: '读取、刷新、目录更新、媒体恢复和删除都在后台批次执行。中断后已完成的批次会保留；读取可按相同设置继续。' }),
      jobsHost));

  sections.append(overview, read, live, auto, importSection, media, voice, deleteSection, jobs);

  const head = h('header', { class: 'page-head' },
    h('div', { class: 'page-head-copy' },
      h('h2', { text: '数据与同步' }),
      h('p', { text: '所有读取和导入都在本机完成。模型只接收每轮实际检索到的相关片段。' })),
    h('div', { class: 'page-head-actions' },
      h('button', { class: 'primary-button', type: 'button', onclick: () => void doSync() }, icon('refresh'), '刷新消息'),
      h('button', { class: 'quiet-button', type: 'button', onclick: () => activate('read') }, icon('download'), '读取历史')));
  const layout = h('div', { class: 'data-layout' }, nav, sections);
  const page = h('div', { class: 'view-scroll' }, h('div', { class: 'page' }, head, banner, layout));
  ui = { page, nav, sections, banner, overviewBody, readStatus, readStickers, nativeSourceSelect, nativeSourceInfo,
    nativeSourceSave, nativeSourceStatus, liveList, liveSave, liveQQ, liveWechat,
    liveStickers, liveImages, autoSelect, autoStickers, importPath, importStickers, importResult, importButton,
    mediaPlatform, mediaMode, mediaPicker, mediaSearch, mediaOptions, mediaStart, mediaEnd, mediaStickers, mediaResult,
    deletePlatform, deleteSearch, deleteOptions, deleteStart, deleteEnd, deletePreview, deleteSummary, deleteConfirm,
    jobsHost };
  // Bind media/delete interactions now that ui exists.
  mediaPlatform.addEventListener('change', () => { state.mediaSelection.clear(); renderMediaPicker(); });
  mediaMode.addEventListener('change', renderMediaPicker);
  mediaSearch.addEventListener('input', renderMediaPicker);
  deletePlatform.addEventListener('change', () => { state.deleteSelection.clear(); invalidateDelete(); renderDeletePicker(); });
  deleteSearch.addEventListener('input', renderDeletePicker);
  for (const input of [deleteStart, deleteEnd]) input.addEventListener('change', invalidateDelete);
  const voiceController = createVoiceSettings({
    getReadScope: platform => ({
      enabled: state.readPanels[platform].enabled.checked,
      conversations: state.readPanels[platform].conversations(),
    }),
    getDateRange: platform => ({ start: state.readPanels[platform].start.value, end: state.readPanels[platform].end.value }),
  });
  voice.querySelector('#voice-settings-mount').append(voiceController.element);
  state.voice = voiceController;
  renderOverview();
  renderNativeSource();
  renderLive();
  renderDeletePicker();
  renderMediaPicker();
  syncAutoControls();
  renderJobs();
  activate(state.section);
  return page;
}

async function runRead() {
  if (!ui) return;
  try {
    const body = readFormBody();
    ui.readStatus.textContent = '';
    await startJob('/api/data/read', body, { label: '读取聊天记录' });
  } catch (error) {
    ui.readStatus.textContent = errorMessage(error, '读取设置无效。');
    ui.readStatus.className = 'field-hint error-text';
  }
}

function renderReadStatus() {
  if (!ui) return;
  const pending = state.settings?.pending_imports || [];
  const parts = [];
  if (pending.length) parts.push(`有 ${pending.length} 次未完成读取。已入库记录保留，使用相同设置再次读取会继续。`);
  const latest = state.refresh?.platforms || [];
  if (latest.length) parts.push(latest.map(item => `${platformLabel(item.platform)}：${item.status === 'error' ? '刷新失败' : item.status === 'running' ? '刷新中' : item.last_sync_at ? '最近成功 ' + fmtTime(item.last_sync_at) : '尚未刷新'}`).join(' · '));
  if (!parts.length) parts.push('尚未增量刷新。保持客户端登录后，可手动读取新记录。');
  ui.readStatus.textContent = parts.join(' ');
  ui.readStatus.className = 'field-hint';
}

async function reconnect(platform) {
  try {
    await api('/api/sync/reconnect', { method: 'POST', body: { platform } });
    toast('进度已准备重新接续；请点击“刷新消息”。', 'success');
    await refreshSettings();
  } catch (error) { toast(errorMessage(error), 'bad'); }
}

registerView('data', {
  title: '数据与同步',
  subtitle: '本机读取、实时摄取、导入与任务进度',
  async mount(root, params, query) {
    state.mounted = true;
    state.nativeDirty = false;
    state.section = query.section || 'overview';
    const shell = h('div', { class: 'view-scroll' }, loadingState('正在读取数据设置…'));
    root.append(shell);
    try {
      await loadBase();
      shell.replaceChildren(buildPage());
      renderReadStatus();
      const offs = [
        on('job', job => {
          if (!state.mounted) return;
          renderBanner(job);
          renderJobs();
          if (job.progress) {
            const title = jobTitle(job);
            // Keep the compact read status useful while a job is running.
            if (['read', 'sync', 'catalog', 'media-refresh', 'delete'].includes(job.kind)) {
              ui.readStatus.textContent = title + ' · ' + (job.progress.stage ? (stageNames[job.progress.stage] || job.progress.stage) : '处理中') +
                (job.progress.imported ? ' · 新增 ' + fmtNumber(job.progress.imported) + ' 条' : '');
            }
          }
        }),
        on('job-done', payload => {
          if (!state.mounted) return;
          renderBanner(payload?.job || null);
          setTimeout(() => {
            if (state.mounted) void refreshSettings();
            renderJobs();
          }, 350);
        }),
        on('live', snapshot => { if (state.mounted) { state.live = snapshot; renderLive(); } }),
        on('data-changed', () => { if (state.mounted) void refreshSettings(); }),
      ];
      state.listeners = offs;
      renderBanner(app.activeJob);
      activate(state.section);
      // Surface any job that was already running when this page mounted.
      for (const job of app.jobs.values()) if (job.status === 'running') renderBanner(job);
      try {
        const status = await api('/api/refresh-status', { timeout: 10000 });
        for (const job of status.running_jobs || []) if (!app.jobPollers.has(job.id)) monitorJob(job.id, { kind: job.kind, cardId: job.card_id });
      } catch { /* optional */ }
    } catch (error) {
      shell.replaceChildren(h('div', { class: 'page' },
        h('div', { class: 'card', style: { maxWidth: '620px', margin: '8vh auto' } },
          emptyState('alert', '数据设置暂时无法读取', errorMessage(error, '请确认本机服务仍在运行。'),
            h('button', { class: 'quiet-button', type: 'button', text: '重新加载', onclick: () => renderRoute({ force: true }) })))));
    }
    return () => {
      state.mounted = false;
      state.nativeDirty = false;
      state.listeners.forEach(off => off());
      state.listeners = [];
      state.voice?.destroy();
      state.voice = null;
      state.readPanels = {};
      ui = null;
      state.bannerJob = null;
    };
  },
});
