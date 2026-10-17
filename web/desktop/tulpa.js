'use strict';

/* Tulpa social memory: UI-only controls, manual corrections and evidence. */
import {
  h, icon, api, registerView, setViewTitle, toast, errorMessage, debounce,
  switchControl, fieldLabel, emptyState, loadingState, fmtFullTime, relativeTime,
} from './app.js';

const STATUS_LABELS = {
  collecting: '正在积累',
  updating: '正在整理这一阶段',
  update_failed: '整理失败，15 分钟后重试',
  no_self_participation: '上一阶段无本人参与，未生成画像',
  await_model: '等待配置模型',
};

const state = {
  listing: null,
  selectedId: null,
  detail: null,
  search: '',
  busy: false,
  mounted: false,
  pollTimer: null,
};

let ui = null;
let loadToken = 0;
let detailToken = 0;

function controlText(control) {
  return control?.enabled
    ? '已对所有可识别会话开启，包括新增会话。只从后续实时行为成长；历史导入不会生成画像。每 100 条有效文字，才将有限上下文交给你配置的模型整理。'
    : 'Tulpa 已暂停。已有记忆冻结，回复不使用成长记忆和成长案例；仍可从已导入聊天检索真实互动。聊天同步照常。再次开启仅从开启时刻继续。';
}

function displayControl(control) {
  if (ui?.toggle) ui.toggle.setChecked(Boolean(control?.enabled));
  if (ui?.intro) ui.intro.textContent = controlText(control);
}

function progressCaption(scope) {
  const remaining = Math.max(0, (scope.total || 0) - (scope.processed || 0));
  return `下一次整理 ${remaining}/100` + (scope.updated_at ? ' · 最近更新 ' + fmtFullTime(scope.updated_at) : '');
}

async function loadListing() {
  if (!ui) return;
  const token = ++loadToken;
  try {
    const data = await api('/api/tulpa?q=' + encodeURIComponent(ui.search.value.trim()), { timeout: 16000 });
    if (token !== loadToken || !ui) return;
    state.listing = data;
    displayControl(data.control);
    renderList();
    if (state.selectedId) {
      const exists = (data.scopes || []).some(scope => scope.id === state.selectedId) ||
        (data.directory || []).some(scope => scope.id === state.selectedId);
      if (exists && !state.detail) await showScope(state.selectedId);
    }
  } catch (error) {
    if (token === loadToken && ui) {
      ui.list.replaceChildren(emptyState('alert', '记忆暂时无法读取', errorMessage(error), null, true));
    }
  }
}

function renderList() {
  if (!ui || !state.listing) return;
  const data = state.listing;
  const host = ui.list;
  host.replaceChildren();
  if (!(data.scopes || []).length && !(data.directory || []).length) {
    host.append(h('p', { class: 'memory-empty', text: ui.search.value ? '没有匹配的记忆会话。' : '还没有匹配的行为记录。实时收到文字后会出现在这里。' }));
    return;
  }
  for (const scope of data.scopes || []) {
    host.append(h('button', {
      class: 'memory-card' + (scope.id === state.selectedId ? ' selected' : ''), type: 'button',
      onclick: () => void showScope(scope.id),
    }, h('strong', { text: scope.name }), h('small', { text: `${scope.platform === 'qq' ? 'QQ' : '微信'} · ${scope.kind === 'group' ? '群聊' : '私聊'} · ${scope.progress}/100 · ${scope.episodes} 个案例` })));
  }
  if ((data.directory || []).length) {
    host.append(h('div', { class: 'memory-list-label', text: '其他已导入会话 · 尚未成长' }));
    for (const item of data.directory) {
      host.append(h('button', {
        class: 'memory-card', type: 'button',
        onclick: async () => {
          try {
            const created = await api('/api/tulpa/conversation', {
              method: 'POST', body: { platform: item.platform, conversation_id: item.conversation_id },
            });
            await loadListing();
            await showScope(created.id);
          } catch (error) { toast(errorMessage(error, '无法打开这个会话的记忆。'), 'bad'); }
        },
      }, h('strong', { text: item.name }), h('small', { text: (item.platform === 'qq' ? 'QQ' : '微信') + ' · 尚未成长' })));
    }
  }
}

function sourceButtons(ids) {
  const wrap = h('div', { class: 'memory-sources' });
  (ids || []).forEach((id, index) => {
    wrap.append(h('a', {
      class: 'source-button open-chat', href: '/?anchor=' + id,
      dataset: { messageId: String(id) }, text: '原文 ' + (index + 1),
    }));
  });
  return wrap;
}

function renderDetail(detail) {
  if (!ui) return;
  state.detail = detail;
  const host = ui.detail;
  host.replaceChildren();
  const model = state.listing?.scopes?.find(scope => scope.id === state.selectedId);
  const title = model?.name || detail.name || '记忆会话';
  host.append(h('h3', { text: title }));
  const statusText = state.listing?.control?.enabled
    ? (STATUS_LABELS[detail.status] || detail.status || '已开启')
    : '已冻结';
  host.append(h('p', { class: 'memory-status-line', text: `${statusText} · 累积 ${detail.total || 0} 条有效文字 · 已整理 ${detail.processed || 0} 条` }));
  const progress = h('div', { class: 'progress' }, h('span', { style: { width: Math.min(100, Math.max(0, detail.total - detail.processed)) + '%' } }));
  host.append(progress, h('p', { class: 'memory-status-line', text: progressCaption(detail) }));
  if (!state.listing?.control?.enabled) host.append(h('p', { class: 'memory-freeze', text: 'Tulpa 已暂停：这份记忆保留但冻结，不会用于回复，也不会继续整理。' }));
  host.append(h('h4', { text: '自动形成的记忆' }), h('div', { class: 'memory-block', text: detail.automatic || '还在认识这个社交场景。达到门槛后才会整理，不会扫描全部旧历史。' }));
  if (!detail.valid) host.append(h('p', { class: 'memory-error', text: '支撑原文已变化或删除。这份旧推断已停用，重新整理后才会用于回复。' }));
  if (detail.status === 'update_failed' || detail.status === 'await_model') {
    host.append(h('button', {
      class: 'quiet-button compact', type: 'button', text: '重新尝试整理', style: { marginTop: '7px' },
      onclick: async () => {
        try { await api('/api/tulpa/scopes/' + detail.id + '/retry', { method: 'POST', body: {} }); await showScope(detail.id); }
        catch (error) { toast(errorMessage(error, '重新整理未能开始。'), 'bad'); }
      },
    }));
  }
  const sources = h('details', {}, h('summary', { text: '查看自动记忆的原文依据' }), sourceButtons(detail.sources || []));
  host.append(sources);
  host.append(h('h4', { text: '你的修正与补充' }),
    h('p', { text: '这里的内容优先于自动推断，后续自动整理不会覆盖。清空后恢复只参考自动记忆。' }));
  const manual = h('textarea', { maxLength: 6000, 'aria-label': '人工记忆', value: detail.manual || '' });
  const status = h('p', { class: 'status-line', role: 'status' });
  const save = h('button', {
    class: 'primary-button', type: 'button', text: '保存人工记忆',
    onclick: async () => {
      save.disabled = true;
      try {
        const value = await api('/api/tulpa/scopes/' + detail.id, {
          method: 'PATCH', body: { manual: manual.value, revision: detail.revision },
        });
        detail.revision = value.revision;
        status.textContent = '已保存。自动整理不会覆盖你的修正。';
        status.className = 'status-line success';
        await loadListing();
      } catch (error) {
        status.textContent = errorMessage(error, '人工记忆未能保存。');
        status.className = 'status-line error';
      } finally { save.disabled = false; }
    },
  });
  host.append(fieldLabel('人工记忆', manual), save, status);

  const episodes = h('details', {}, h('summary', { text: `真实互动案例 · 最近 ${Math.min(20, (detail.episodes || []).length)} 个` }));
  if (!(detail.episodes || []).length) episodes.append(h('p', { class: 'memory-empty', text: '还没有可确认的回复案例。群聊需要明确引用；私聊按双方实际轮次记录。' }));
  for (const episode of detail.episodes || []) {
    const item = h('div', { class: 'memory-episode' },
      h('div', { text: new Date(episode.timestamp).toLocaleString('zh-CN') + (episode.valid ? '' : ' · 原文已变化，停用') }),
      h('div', { text: `对方 ${JSON.parse(episode.incoming).length} 条 → 本人 ${JSON.parse(episode.outgoing).length} 条` }),
      sourceButtons([...JSON.parse(episode.incoming), ...JSON.parse(episode.outgoing)]));
    episodes.append(item);
  }
  host.append(episodes);

  const history = h('details', {}, h('summary', { text: '记忆更新记录' }));
  for (const entry of detail.history || []) {
    history.append(h('div', { class: 'memory-episode' },
      h('div', { text: (entry.kind === 'manual' ? '人工修正' : '自动整理') + ' · ' + new Date(entry.created_at * 1000).toLocaleString('zh-CN') }),
      h('pre', { text: entry.body })));
  }
  if (!(detail.history || []).length) history.append(h('p', { class: 'memory-empty', text: '还没有整理记录。' }));
  host.append(history);
  host.scrollTop = 0;
}

async function showScope(id) {
  if (!ui) return;
  const token = ++detailToken;
  state.selectedId = id;
  renderList();
  ui.detail.replaceChildren(loadingState('正在读取记忆…'));
  try {
    const detail = await api('/api/tulpa/scopes/' + id, { timeout: 16000 });
    if (token !== detailToken || !ui) return;
    displayControl(detail.control);
    renderDetail(detail);
  } catch (error) {
    if (token === detailToken && ui) ui.detail.replaceChildren(emptyState('alert', '记忆详情无法读取', errorMessage(error), null, true));
  }
}

async function toggleMemory(next) {
  if (state.busy) return;
  state.busy = true;
  ui.toggle.disabled = true;
  try {
    const control = await api('/api/tulpa/control', { method: 'POST', body: { enabled: next } });
    displayControl(control);
    if (state.listing) state.listing.control = control;
    toast(next ? 'Tulpa 记忆已开启。' : 'Tulpa 记忆已暂停；已有记忆冻结。', 'success');
    if (state.selectedId) await showScope(state.selectedId);
    await loadListing();
  } catch (error) {
    ui.toggle.setChecked(!next);
    toast(errorMessage(error, '记忆开关未能保存。'), 'bad');
  } finally {
    state.busy = false;
    ui.toggle.disabled = false;
  }
}

function buildPage() {
  const toggle = switchControl('行为记忆', false, next => void toggleMemory(next));
  const intro = h('p', { class: 'memory-head-intro' });
  const search = h('input', { type: 'search', placeholder: '搜索群聊或联系人', 'aria-label': '搜索记忆会话' });
  const list = h('div', { class: 'memory-list' });
  const detail = h('section', { class: 'memory-detail' },
    emptyState('memory', '选择一个群或联系人', '看看真实互动慢慢留下了什么。实时文字达到门槛后才会开始整理；历史导入不会生成画像。'));
  const head = h('header', { class: 'memory-head' },
    h('div', { class: 'memory-head-copy' },
      h('h2', { text: '慢慢长出来的社交记忆' }),
      intro),
    h('div', { class: 'memory-head-actions' },
      toggle,
      h('button', { class: 'quiet-button compact', type: 'button', title: '刷新', onclick: () => void loadListing() }, icon('refresh'))));
  const page = h('div', { class: 'view-scroll' }, h('div', { class: 'memory-page' },
    head,
    h('div', { class: 'memory-layout' },
      h('aside', { class: 'memory-directory' },
        h('div', { class: 'memory-search' }, search),
        list),
      detail)));
  const debounced = debounce(() => void loadListing(), 250);
  search.addEventListener('input', debounced);
  ui = { page, toggle, intro, search, list, detail };
  return page;
}

registerView('memory', {
  title: '记忆',
  subtitle: 'Tulpa 社交记忆与真实互动案例',
  async mount(root) {
    state.mounted = true;
    state.selectedId = null;
    state.detail = null;
    state.listing = null;
    const page = buildPage();
    root.append(page);
    await loadListing();
    const first = state.listing?.scopes?.[0];
    if (first) await showScope(first.id);
    state.pollTimer = setInterval(() => { if (!document.hidden && state.mounted) void loadListing(); }, 15000);
    return () => {
      state.mounted = false;
      clearInterval(state.pollTimer);
      loadToken += 1;
      detailToken += 1;
      ui = null;
      state.listing = null;
      state.detail = null;
    };
  },
});
