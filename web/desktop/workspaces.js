'use strict';

/* Persistent workspaces: scoped investigation, confirmed outputs and versions. */
import {
  h, icon, api, app, registerView, registerAction, navigate, setViewTitle, toast, errorMessage,
  openDialog, confirmDialog, promptDialog, fieldLabel, checkbox, selectControl, option,
  emptyState, loadingState, fmtNumber, fmtFullTime, fmtTime, debounce,
} from './app.js';

const TASK_STATES = { completed: '已完成', running: '调查中', cancelled: '已停止', interrupted: '已中断', error: '未完成', failed: '未完成' };
const TASK_MODES = { ask: '提问', task: '整理 / 修改', fact: '补充事实', changes: '维护建议', auto: '调查', manual: '手动编辑' };
const MATERIAL_STATES = { accepted: '已确认', pending: '待确认', excluded: '已排除' };
const SOURCE_KINDS = { message: '聊天原文', media: '聊天图片', voice: '语音', artifact_source: '文件', artifact_chunk: '文件片段', qq_notice: 'QQ 公告', qq_essence: 'QQ 精华' };

const ws = {
  list: [],
  current: null,
  home: null,
  turns: [],
  materials: [],
  candidates: [],
  schedule: null,
  tab: 'home',
  activeTask: null,
  expanded: new Map(),
  mounted: false,
  conversations: null,
  watchCards: null,
  collections: null,
};

let ui = null;
let detailToken = 0;
let pollTimer = null;
let refreshTimer = null;
let formRef = null;

function workspaceApi(path, body, method) {
  return api('/api/workspaces' + path, { method: method || (body === undefined ? 'GET' : 'POST'), body, timeout: 30000 });
}

function taskStateLabel(value) {
  return TASK_STATES[value] || value || '未知';
}

function when(value) {
  return value ? fmtFullTime(value) : '尚未更新';
}

function scopeText(workspace) {
  const scope = workspace?.scope;
  if (!scope) return '';
  const names = (scope.platforms || []).map(p => p === 'qq' ? 'QQ' : '微信').join(' + ');
  const conversations = (scope.conversations || []).map(raw => {
    try { return JSON.parse(raw)[1]; } catch { return raw; }
  });
  const start = scope.start ? new Date(scope.start + 8 * 3600000).toISOString().slice(0, 10) : '不限开始';
  const end = scope.end ? new Date(scope.end - 1 + 8 * 3600000).toISOString().slice(0, 10) : '今后新增也在范围内';
  return `${names} · ${conversations.length ? conversations.join('、') : '全部已导入会话'} · ${start} 至 ${end}`;
}

function sourceRef(ref) {
  if (!ref) return h('span');
  if (ref.notice && !ref.available) return h('span', { class: 'field-hint', text: ref.notice });
  if (!ref.available) return h('span', { class: 'field-hint', text: '来源不可用／超出范围' });
  const wrap = h('span', { class: 'row wrap', style: { marginRight: '6px', verticalAlign: 'middle' } });
  if (ref.changed) wrap.append(h('span', { class: 'badge warn', text: '来源已变更' }));
  const label = ref.label || ref.title || `${ref.source_type} ${ref.source_id}`;
  let link;
  if (String(ref.source_type).startsWith('artifact')) {
    const params = new URLSearchParams(String(ref.url || '').split('?')[1] || '');
    const id = Number(params.get('file')) || Number(String(ref.source_id).split(':')[0]);
    link = h('button', { class: 'source-button open-artifact', type: 'button', text: label, dataset: { sourceId: String(id) } });
  } else if (['qq_notice', 'qq_essence'].includes(ref.source_type)) {
    link = h('button', { class: 'source-button open-knowledge', type: 'button', text: label, dataset: { knowledgeId: String(ref.id || ref.source_id) } });
  } else if (ref.message_id || String(ref.url || '').includes('anchor=')) {
    const mid = ref.message_id || Number(new URLSearchParams(String(ref.url).split('?')[1] || '').get('anchor'));
    link = h('button', { class: 'source-button open-chat', type: 'button', text: label, dataset: { messageId: String(mid) } });
  } else if (ref.url) {
    link = h('a', { class: 'source-button', href: ref.url, target: '_blank', rel: 'noopener', text: label });
  } else {
    link = h('span', { class: 'badge', text: label });
  }
  wrap.append(link);
  if (ref.notice) wrap.append(h('span', { class: 'field-hint', text: ref.notice }));
  return wrap;
}

function sourceCounts(counts) {
  const parts = [];
  if (counts?.messages) parts.push(`${counts.messages} 条消息`);
  if (counts?.files) parts.push(`${counts.files} 个文件`);
  if (counts?.other) parts.push(`${counts.other} 项其他资料`);
  if (counts?.unavailable) parts.push(`${counts.unavailable} 项不可用`);
  return parts.join(' · ') || '未关联原始证据';
}

function disclosure(key, label, initial = false) {
  const details = h('details', { class: 'disclosure' }, h('summary', { text: label }));
  const storageKey = (ws.current?.workspace?.id || '') + ':' + key;
  details.open = ws.expanded.get(storageKey) ?? initial;
  details.addEventListener('toggle', () => ws.expanded.set(storageKey, details.open));
  return details;
}

function section(host, title, aside = null) {
  const node = h('section', { class: 'ws-section' },
    h('div', { class: 'ws-section-heading' }, h('h3', { text: title }), aside ? h('span', { class: 'spacer', style: { flex: '1' } }) : null, aside));
  host.append(node);
  return node;
}

function compose(text) {
  if (!ui) return;
  ui.input.value = text;
  ui.input.focus();
  ui.input.dispatchEvent(new Event('input'));
}

/* ---------- list view ---------- */
function renderList(root) {
  const host = h('div', { class: 'view-scroll' });
  const inner = h('div', { class: 'page ws-list-page' });
  host.append(inner);
  root.append(host);
  let disposed = false;
  const newButton = h('button', { class: 'primary-button', type: 'button', onclick: () => void openCreateForm() }, icon('plus'), '新建工作区');
  inner.append(h('header', { class: 'home-head' },
    h('div', { class: 'home-head-copy' },
      h('div', { class: 'home-eyebrow', text: 'WORKSPACE' }),
      h('h2', { text: '把一件事持续做下去' }),
      h('p', { text: '工作区保存目标、范围、资料关联、成果版本和每次调查过程。正式改动都需要你确认。' })),
    h('div', { class: 'home-head-actions' }, newButton)));
  const grid = h('div', { class: 'ws-card-grid' });
  const empty = emptyState('workspace', '还没有工作区', '围绕一个目标建立工作区，让 Agent 在明确范围内持续调查、整理和修订成果。',
    h('button', { class: 'primary-button', type: 'button', onclick: () => void openCreateForm() }, icon('plus'), '新建工作区'));
  inner.append(grid, empty);
  empty.hidden = true;

  function paint() {
    if (disposed) return;
    grid.replaceChildren(...ws.list.map(item => h('button', {
      class: 'ws-card', type: 'button', onclick: () => navigate('workspaces/' + item.id),
    },
      h('header', {}, h('strong', { text: item.title }), item.gap ? h('span', { class: 'badge warn', text: '变化缺口' }) : null),
      h('p', { class: 'ws-card-goal', text: item.goal || '尚未填写目标' }),
      h('footer', {}, icon('clock'), h('span', { text: '更新 ' + relativeTime(item.updated_at) }), item.pending ? h('span', { class: 'badge accent', text: `${item.pending} 项待整理` }) : null))));
    empty.hidden = ws.list.length > 0;
  }

  async function load() {
    try {
      const data = await workspaceApi('');
      if (disposed) return;
      ws.list = data.workspaces || [];
      paint();
    } catch (error) {
      if (!disposed) inner.append(errorState(error));
    }
  }
  paint();
  void load();
  refreshTimer = setInterval(() => { if (!document.hidden) void load(); }, 30000);
  return () => { disposed = true; clearInterval(refreshTimer); };
}

function relativeTime(value) {
  if (!value) return '尚未更新';
  const delta = (Date.now() - value * 1000) / 1000;
  if (delta < 60) return '刚刚';
  if (delta < 3600) return Math.floor(delta / 60) + ' 分钟前';
  if (delta < 86400) return Math.floor(delta / 3600) + ' 小时前';
  return Math.floor(delta / 86400) + ' 天前';
}

function errorState(error) {
  return h('div', { class: 'card pad', style: { marginTop: '12px' } },
    emptyState('alert', '工作区暂时无法读取', errorMessage(error, '请稍后重试。'), null, true));
}

/* ---------- detail view ---------- */
function renderDetail(root, id) {
  const page = h('div', { class: 'ws-detail' });
  root.append(page);
  let disposed = false;
  let liveTask = null;

  const back = h('button', { class: 'icon-button', type: 'button', 'aria-label': '返回工作区列表', title: '返回列表', onclick: () => navigate('workspaces') }, icon('arrow-left'));
  const title = h('h2', { text: '正在读取工作区…' });
  const goal = h('p');
  const mobileNav = h('select', { class: 'ws-mobile-nav', 'aria-label': '切换工作区视图' },
    ...['home', 'state', 'outputs', 'materials', 'logs', 'history'].map(value => h('option', { value, text: { home: 'Agent 对话', state: '当前状态', outputs: '工作成果', materials: '资料', logs: '工作日志', history: '历史版本' }[value] })));
  const header = h('header', { class: 'ws-detail-head' }, back, h('div', { class: 'grow' }, title, goal), mobileNav,
    h('button', { class: 'quiet-button compact', type: 'button', text: '设置', onclick: () => void openForm(ws.current?.workspace) }));
  const tree = h('aside', { class: 'ws-tree', 'aria-label': '工作区资源树' });
  const main = h('div', { class: 'ws-main' });
  const layout = h('div', { class: 'ws-detail-layout' }, tree, main);
  const input = h('textarea', { rows: 1, maxLength: 2000, placeholder: '在这个工作区继续工作…', 'aria-label': '工作区任务输入' });
  const mode = selectControl([['auto', '自动识别意图'], ['ask', '只读提问'], ['task', '整理 / 修改'], ['fact', '补充事实'], ['changes', '维护建议']]);
  const send = h('button', { class: 'send-button', type: 'submit', 'aria-label': '开始任务', title: '开始任务' }, icon('send'));
  const stop = h('button', { class: 'send-button stop-button', type: 'button', 'aria-label': '停止任务', title: '停止', hidden: true }, icon('stop'));
  const live = h('span', { class: 'status-line', hidden: true, role: 'status' });
  const composer = h('form', {
    class: 'composer ws-composer-form',
    onsubmit: event => { event.preventDefault(); void startTask(); },
  },
    h('div', { class: 'composer-text' }, input),
    h('div', { class: 'composer-toolbar' }, mode, h('span', { class: 'spacer' }), send, stop),
    h('div', { class: 'composer-foot' }, h('span', { class: 'field-hint', text: '正式成果和状态只会通过待确认的修改建议更新。' }), live));
  const composerDock = h('div', { class: 'ws-composer' }, composer);
  page.append(header, layout, composerDock);

  function updateComposer() {
    const running = Boolean(ws.activeTask);
    send.hidden = running;
    send.disabled = running || !input.value.trim();
    stop.hidden = !running;
    input.disabled = false;
    live.hidden = !running;
    if (running) live.textContent = liveTask?.events?.at(-1)?.text || liveTask?.events?.at(-1)?.name || '正在调查…';
  }

  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = Math.min(140, Math.max(38, input.scrollHeight)) + 'px';
    updateComposer();
  });
  input.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
      event.preventDefault();
      composer.requestSubmit();
    }
  });
  stop.addEventListener('click', async () => {
    if (!ws.activeTask) return;
    try { await workspaceApi('/' + id + '/tasks/' + ws.activeTask + '/stop', {}); toast('已请求停止；草稿、过程与待处理候选会保留。', 'warn'); }
    catch (error) { toast(errorMessage(error), 'bad'); }
  });
  mobileNav.addEventListener('change', () => setTab(mobileNav.value));

  async function startTask(textOverride = null, taskMode = null) {
    const question = String(textOverride ?? input.value).trim();
    if (!question) return;
    if (ws.activeTask) { toast('该工作区已有任务执行中，请先等待或停止。', 'warn'); return; }
    try {
      const vision = (() => { try { return localStorage.getItem('tulpa.visionMode') || 'ocr'; } catch { return 'ocr'; } })();
      const profile = (() => { try { return localStorage.getItem('tulpa.agentProfile') || 'deep'; } catch { return 'deep'; } })();
      const result = await workspaceApi('/' + id + '/tasks', { question, mode: taskMode || mode.value, profile, vision });
      ws.activeTask = result.task_id;
      liveTask = null;
      ws.tab = 'home';
      if (input.value.trim() === question) input.value = '';
      input.style.height = 'auto';
      updateComposer();
      if (result.api_called === false) toast('没有待整理候选，未调用模型。', 'warn');
      await loadAll();
      setTab('home');
      pollTask();
    } catch (error) { toast(errorMessage(error, '任务未能开始。'), 'bad'); }
  }

  async function pollTask() {
    clearTimeout(pollTimer);
    if (!ws.activeTask || disposed) return;
    const tid = ws.activeTask;
    try {
      const task = await workspaceApi('/' + id + '/tasks/' + tid);
      if (disposed) return;
      liveTask = task;
      const progress = main.querySelector('#ws-live-progress');
      if (progress) progress.textContent = trace(task);
      if (live) live.textContent = task.events?.at(-1)?.text || task.events?.at(-1)?.name || '正在调查…';
      if (task.state !== 'running') {
        ws.activeTask = null;
        liveTask = null;
        updateComposer();
        await loadAll();
      } else {
        pollTimer = setTimeout(pollTask, 1500);
      }
    } catch (error) {
      if (!disposed) { toast(errorMessage(error, '任务状态读取失败。'), 'bad'); pollTimer = setTimeout(pollTask, 3000); }
    }
  }

  async function loadAll() {
    const token = ++detailToken;
    try {
      const [overview, home] = await Promise.all([
        workspaceApi('/' + id),
        workspaceApi('/' + id + '/home'),
      ]);
      if (token !== detailToken || disposed) return;
      if (overview.workspace.scope_epoch !== home.scope_epoch) return;
      const [thread, materials, schedule] = await Promise.all([
        workspaceApi('/' + id + '/thread'),
        workspaceApi('/' + id + '/materials'),
        workspaceApi('/' + id + '/schedule'),
      ]);
      if (token !== detailToken || disposed) return;
      ws.current = overview;
      ws.home = home;
      ws.turns = thread.turns || [];
      ws.materials = materials.materials || [];
      ws.candidates = materials.candidates || [];
      ws.schedule = schedule;
      const running = (overview.tasks || []).find(task => task.state === 'running');
      ws.activeTask = running?.id || null;
      if (ws.activeTask) pollTask();
      updateHeader();
      renderTree();
      renderMain();
      updateComposer();
    } catch (error) {
      if (token === detailToken && !disposed) {
        main.replaceChildren(errorState(error));
        toast(errorMessage(error, '工作区读取失败。'), 'bad');
      }
    }
  }

  function updateHeader() {
    const workspace = ws.current?.workspace;
    if (!workspace) return;
    title.textContent = workspace.title;
    goal.textContent = workspace.goal;
    setViewTitle(workspace.title || '工作区', '工作区 · ' + scopeText(workspace));
    mobileNav.value = ws.tab;
  }

  function renderTree() {
    const workspace = ws.current?.workspace;
    if (!workspace) return;
    tree.replaceChildren();
    tree.append(h('h3', { text: '工作区资源' }));
    const item = (label, tab, count = '') => h('button', {
      class: 'ws-tree-item' + (ws.tab === tab ? '' : ''), type: 'button',
      'aria-current': String(ws.tab === tab),
      onclick: () => setTab(tab),
    }, icon(tab === 'home' ? 'chat' : tab === 'state' ? 'file' : tab === 'outputs' ? 'files' : tab === 'materials' ? 'collection' : tab === 'history' ? 'clock' : 'data'),
    h('span', { class: 'tree-label', text: label }), count ? h('span', { class: 'badge', text: String(count) }) : null);
    tree.append(item('Agent 对话', 'home'), item('当前状态', 'state'));
    const outputs = disclosure('tree-outputs', '工作成果', true);
    outputs.className = 'ws-tree-group';
    for (const output of ws.home?.outputs || []) {
      if (output.resource_kind === 'state' || output.resource_kind === 'note') continue;
      outputs.append(h('button', { class: 'ws-tree-item', type: 'button', onclick: () => void preview(output.id) },
        icon('file'), h('span', { class: 'tree-label', text: output.name })));
    }
    tree.append(outputs);
    const materials = disclosure('tree-materials', '资料', true);
    materials.className = 'ws-tree-group';
    for (const material of ws.materials.filter(row => row.state !== 'excluded').slice(0, 100)) {
      materials.append(h('button', {
        class: 'ws-tree-item', type: 'button', onclick: () => showMaterial(material),
      }, icon(material.source_type?.startsWith('artifact') ? 'files' : 'collection'),
      h('span', { class: 'tree-label', text: material.title || material.label || '来源不可用' })));
    }
    tree.append(materials);
    const notes = disclosure('tree-notes', '笔记', false);
    notes.className = 'ws-tree-group';
    for (const output of ws.home?.outputs || []) {
      if (output.resource_kind !== 'note') continue;
      notes.append(h('button', { class: 'ws-tree-item', type: 'button', onclick: () => void preview(output.id) }, icon('file'), h('span', { class: 'tree-label', text: output.name })));
    }
    notes.append(h('button', { class: 'ws-tree-item subtle', type: 'button', text: '+ 整理一条笔记', onclick: () => compose('请将本次讨论整理成工作区笔记，保留出处，提出修改建议等待我确认。') }));
    tree.append(notes);
    tree.append(item('历史版本', 'history'), item('工作日志', 'logs'));
    tree.append(h('button', {
      class: 'ws-tree-item', type: 'button',
      onclick: () => void proposals(),
    }, icon('collection'), h('span', { class: 'tree-label', text: '修改建议' }), ws.home?.proposals ? h('span', { class: 'badge accent', text: String(ws.home.proposals) }) : null));
    const footer = h('div', { class: 'ws-tree-footer' },
      h('span', { class: 'field-hint', text: ws.schedule?.minutes ? `自动维护 · 每 ${ws.schedule.minutes >= 1440 ? ws.schedule.minutes / 1440 + ' 天' : ws.schedule.minutes + ' 分钟'}` : '自动维护关闭' }),
      h('button', { class: 'ws-tree-item', type: 'button', text: '工作区设置', onclick: () => void openForm(workspace) }));
    tree.append(footer);
  }

  function setTab(tab) {
    ws.tab = tab;
    mobileNav.value = tab;
    renderTree();
    renderMain();
  }

  function renderMain() {
    main.replaceChildren();
    const inner = h('div', { class: 'ws-main-inner' });
    main.append(inner);
    if (!ws.current || !ws.home) { inner.append(loadingState()); return; }
    if (ws.tab === 'home') renderHomeTab(inner);
    else if (ws.tab === 'state') renderStateTab(inner);
    else if (ws.tab === 'outputs') renderOutputsTab(inner);
    else if (ws.tab === 'materials') renderMaterialsTab(inner);
    else if (ws.tab === 'logs') renderLogsTab(inner);
    else if (ws.tab === 'history') void renderHistoryTab(inner);
  }

  function finding(point, index, question = false) {
    const article = h('article', { class: 'ws-finding' + (question ? ' ws-question' : '') });
    if ((point.text || '').length > 260) {
      article.append(h('p', { text: point.text.slice(0, 260) + '…' }));
      const details = disclosure((question ? 'question' : 'finding') + index, '展开完整内容');
      details.append(h('p', { text: point.text }));
      article.append(details);
    } else article.append(h('p', { text: point.text || '' }));
    const origin = h('div', { class: 'ws-origin' });
    if (point.output_id) origin.append(h('button', { class: 'text-button', type: 'button', text: `${point.output_name || '成果'} · v${point.version}`, onclick: () => void preview(point.output_id) }));
    if ((point.refs || []).length) {
      const refs = disclosure('refs-' + (question ? 'q' : 'f') + index, `${point.refs.length} 项原始证据`);
      for (const ref of point.refs) refs.append(sourceRef(ref), h('br'));
      origin.append(refs);
    } else if (question) origin.append(h('span', { class: 'field-hint', text: '报告中的覆盖限制；未关联单条原文' }));
    article.append(origin);
    if (point.stale) article.append(h('p', { class: 'ws-warning', text: '相关来源已变化或不可用，请重新核对。' }));
    if (question) article.append(h('button', {
      class: 'quiet-button compact', type: 'button', text: '继续调查这个问题',
      onclick: () => compose(`继续核实“${point.output_name}”v${point.version} 中的待确认问题：${point.text}\n请重新读取相关原始证据，核实后修改同一份成果；尚不能确认的内容继续保留。`),
    }));
    return article;
  }

  function renderCurrentState(host, compact = false) {
    const area = section(host, '当前状态', ws.home.current_state
      ? h('button', { class: 'text-button', type: 'button', text: '打开状态文档', onclick: () => void preview(ws.home.current_state.id) })
      : h('span', { class: 'ws-badge', text: '尚未确认状态文档' }));
    area.append(h('p', { class: 'answer-note', text: ws.home.current_state
      ? `已确认版本 v${ws.home.current_state.version} · ${when(ws.home.current_state.updated_at)}`
      : (ws.home.outputs.length ? '以下摘自已应用成果；整理时可提出专用状态文档。' : '围绕工作区目标展开调查。回答和未应用的建议不会自动写入这里。') }));
    for (const [index, point] of (ws.home.understanding || []).slice(0, compact ? 3 : 20).entries()) area.append(finding(point, index));
    for (const supplement of (ws.home.user_supplements || []).slice(0, compact ? 1 : 8)) {
      const row = h('div', { class: 'ws-finding', style: { borderLeft: '2px solid var(--warning)' } },
        h('span', { class: 'ws-badge warn', text: '用户补充 · 未独立核实' }), h('p', { text: supplement.text }));
      for (const ref of supplement.refs || []) row.append(sourceRef(ref));
      if (supplement.stale) row.append(h('p', { class: 'ws-warning', text: '此补充来源不可用或已变化，请重新确认。' }));
      area.append(row);
    }
    const recent = h('div', { class: 'ws-recent-line' },
      h('strong', { text: '最近变化 ' }),
      h('span', { text: (ws.home.recent?.items || []).length ? ws.home.recent.items.map(item => item.label + ' ' + item.count + ' 项').join(' · ') : '最近没有发现重要变化' }));
    area.append(recent);
    if (ws.home.recent?.gap) area.append(h('p', { class: 'ws-warning', text: '变化记录存在缺口，请在设置中执行日期补查。' }));
    if ((ws.home.questions || []).length) {
      const questions = disclosure('uncertainties', `待确认事项 · ${ws.home.question_total || ws.home.questions.length} 项信息缺口`);
      for (const [index, point] of (ws.home.questions || []).entries()) questions.append(finding(point, index, true));
      area.append(questions);
    }
    if (!compact && ws.home.current_state) area.append(h('button', { class: 'quiet-button compact', type: 'button', text: '预览 / 编辑当前状态', onclick: () => void preview(ws.home.current_state.id) }));
  }

  function renderHomeTab(host) {
    const intro = h('div', { class: 'ws-chat-state' });
    renderCurrentState(intro, true);
    host.append(intro);
    const feed = h('div', { class: 'ws-chat-feed' });
    host.append(feed);
    if (!ws.turns.length) {
      const empty = h('div', { class: 'ws-chat-empty' },
        h('h3', { text: '从这里继续推进这件事' }),
        h('p', { text: '可以提问、布置整理任务，或补充刚确认的信息。所有正式更新都先给你看差异。' }),
        h('div', { class: 'ws-inline-actions' },
          h('button', { class: 'quiet-button compact', type: 'button', text: '首次调查并整理', onclick: () => startTask('围绕工作区目标调查已有资料，提出当前状态、报告及资料索引的修改建议。', 'task') }),
          h('button', { class: 'quiet-button compact', type: 'button', text: '看看最近变化', onclick: () => startTask('看看最近变化，说明已知信息与待确认事项。', 'ask') })));
      feed.append(empty);
    }
    for (const turn of ws.turns) {
      const article = h('article', { class: 'ws-turn', dataset: { taskId: turn.id } });
      article.append(h('div', { class: 'ws-chat-user' }, h('p', { text: turn.question })));
      const answer = h('div', { class: 'ws-chat-answer' });
      answer.append(h('div', { class: 'answer-note', text: `${when(turn.created_at)} · ${TASK_MODES[turn.mode] || '调查'} · ${taskStateLabel(turn.state)}` }));
      if (turn.state === 'running') {
        const progress = disclosure('live-' + turn.id, '正在调查 · 查看工具过程', true);
        progress.append(h('pre', { class: 'ws-progress', id: 'ws-live-progress', text: trace(turn) }));
        answer.append(progress);
      }
      for (const claim of turn.record?.result?.claims || []) {
        const p = h('p', { text: claim.text });
        answer.append(p);
        const links = h('div', { class: 'ws-inline-actions' });
        for (const mid of claim.evidence_ids || []) links.append(h('button', { class: 'source-button open-chat', type: 'button', text: '聊天原文 M' + mid, dataset: { messageId: String(mid) } }));
        for (const fid of claim.artifact_evidence_ids || []) {
          const match = String(fid).match(/^F(\d+)(?::C(\d+))?$/);
          if (match) links.append(h('button', { class: 'source-button open-artifact', type: 'button', text: '文件 ' + fid, dataset: { sourceId: match[1] } }));
        }
        if (links.children.length) answer.append(links);
      }
      if (turn.mode === 'fact') answer.append(h('p', { class: 'answer-note', text: '这段补充按用户提供的信息保留，未经独立核实；确认建议后才进入正式状态。' }));
      if (turn.state !== 'running' && !(turn.record?.result?.claims || []).length) {
        answer.append(h('p', { class: 'answer-note', text: turn.record?.note || turn.record?.error || (turn.record?.drafts_saved?.length ? '已生成修改建议，尚未应用。' : '本轮未形成可确认的结论，可以查看过程继续调查。') }));
      }
      if (turn.record?.notice) answer.append(h('p', { class: 'ws-warning', text: turn.record.notice }));
      if (turn.record?.drafts_saved?.length) {
        answer.append(h('div', { class: 'ws-proposal-inline' },
          h('strong', { text: '建议修改' }),
          h('p', { text: [...new Set(turn.record.drafts_saved.map(item => item.name))].join('、') }),
          h('button', { class: 'quiet-button compact', type: 'button', text: '查看差异 / 应用或忽略', onclick: () => void proposals(turn.id) })));
      }
      const log = disclosure('turn-log-' + turn.id, `${Number(turn.record?.seconds || 0).toFixed(1)} 秒 · ${turn.record?.requests || 0} 次请求 · ${turn.record?.usage?.total_tokens || 0} tokens · 工作日志`);
      log.append(h('pre', { class: 'ws-progress', text: trace(turn) }), h('button', { class: 'quiet-button compact', type: 'button', text: '详细取证记录', onclick: () => void showTask(turn.id) }));
      answer.append(log);
      article.append(answer);
      feed.append(article);
    }
    host.append(h('div', { class: 'ws-chat-actions' },
      h('button', { class: 'quiet-button compact', type: 'button', text: '整理最近变化', onclick: () => startTask('整理最近变化，更新当前状态与相关成果；先提出建议供我确认。', 'changes') }),
      h('button', { class: 'quiet-button compact', type: 'button', text: `查看修改建议 (${ws.home.proposals || 0})`, onclick: () => void proposals() })));
  }

  function renderStateTab(host) {
    renderCurrentState(host, false);
    if (ws.home.current_state) {
      section(host, '状态文档').append(h('button', { class: 'primary-button compact', type: 'button', text: '打开状态文档', onclick: () => void preview(ws.home.current_state.id) }));
    }
  }

  function outputCard(output) {
    const card = h('article', { class: 'ws-output' },
      h('span', { class: 'ws-eyebrow', text: output.type }),
      h('h4', { text: output.name }),
      h('p', { class: 'ws-output-summary', text: output.summary || '暂未提取到摘要，可预览完整成果。' }),
      h('p', { class: 'answer-note', text: `更新 ${when(output.updated_at)} · v${output.version} · 来源：${sourceCounts(output.sources)}` }));
    if (output.origin === 'user') card.append(h('p', { class: 'answer-note', text: '用户编辑内容，尚未由 Agent 核对。' }));
    if (output.sources?.changed) card.append(h('p', { class: 'ws-warning', text: `${output.sources.changed} 项引用来源已有变化，需重新核对。` }));
    card.append(h('div', { class: 'ws-inline-actions' },
      h('button', { class: 'quiet-button compact', type: 'button', text: '预览', onclick: () => void preview(output.id) }),
      h('button', { class: 'quiet-button compact', type: 'button', text: '编辑', onclick: () => void preview(output.id, true) }),
      h('a', { class: 'quiet-button compact', href: `/api/workspaces/${id}/outputs/${output.id}/download`, download: '' }, '导出'),
      h('button', { class: 'quiet-button compact', type: 'button', text: '查看证据', onclick: () => void showOutputEvidence(output) })));
    return card;
  }

  function renderOutputsTab(host) {
    const area = section(host, '工作成果');
    for (const output of ws.home.outputs || []) area.append(outputCard(output));
    if (!(ws.home.outputs || []).length) area.append(emptyState('files', '还没有成果', '调查报告、状态文档和笔记会在这里保留版本。', null, true));
  }

  function showMaterial(material) {
    const dialog = openDialog({ title: material.title || material.label || '资料来源', subtitle: material.conversation || '', body: materialCard(material), side: true });
    return dialog;
  }

  function materialCard(material) {
    const card = h('article', { class: 'ws-material' },
      h('span', { class: 'ws-badge ' + (material.state === 'accepted' ? 'ok' : material.state === 'excluded' ? 'bad' : 'warn'), text: MATERIAL_STATES[material.state] || material.state }),
      h('h4', { text: material.available ? (material.title || material.label || '聊天资料') : '原始资料不可用或超出当前范围' }),
      h('p', { text: [material.platform === 'qq' ? 'QQ' : material.platform === 'wechat' ? '微信' : '', material.conversation, SOURCE_KINDS[material.source_type] || '资料'].filter(Boolean).join(' · ') }),
      h('p', { class: 'ws-material-reason', text: '关联原因：' + (material.reason || '当前无法查看关联说明。') }));
    if (material.changed) card.append(h('p', { class: 'ws-warning', text: '原文已有变化，需要重新核对。' }));
    const actions = h('div', { class: 'ws-inline-actions' });
    if (material.available) {
      if (String(material.source_type).startsWith('qq_')) {
        actions.append(h('button', {
          class: 'source-button open-knowledge', type: 'button', text: '查看来源',
          dataset: { knowledgeId: String(material.source_id) },
        }));
      } else {
        actions.append(sourceRef({
          source_type: material.source_type, source_id: material.source_id, available: true, label: '查看来源',
          url: String(material.source_type).startsWith('artifact') ? `/?file=${material.source_id}` : `/?anchor=${String(material.source_id).split(':')[0]}`,
          message_id: String(material.source_type).startsWith('artifact') ? null : Number(String(material.source_id).split(':')[0]),
        }));
      }
    }
    if (material.state === 'excluded') {
      actions.append(h('button', { class: 'quiet-button compact', type: 'button', text: '解除排除', onclick: async () => { await workspaceApi('/' + id + '/materials', { source_type: material.source_type, source_id: material.source_id, release: true }); await loadAll(); } }));
    } else {
      if (material.available && material.state === 'pending') {
        actions.append(h('button', { class: 'quiet-button compact', type: 'button', text: '确认关联', onclick: async () => { await workspaceApi('/' + id + '/materials', { source_type: material.source_type, source_id: material.source_id, state: 'accepted', reason: material.reason }); await loadAll(); } }));
      }
      if (material.available) actions.append(h('button', { class: 'quiet-button compact', type: 'button', text: '加入成果', onclick: () => compose(`请读取资料 ${material.source_type} ${material.source_id}（${material.title || material.label || '资料'}），核对它与工作区目标的关系，再补入已有成果；覆盖前展示拟议改动。`) }));
      actions.append(h('button', { class: 'quiet-button compact danger-text', type: 'button', text: '移除并排除', onclick: async () => { await workspaceApi('/' + id + '/materials', { source_type: material.source_type, source_id: material.source_id, state: 'excluded', reason: material.reason }); await loadAll(); } }));
    }
    card.append(actions);
    return card;
  }

  function renderMaterialsTab(host) {
    const area = section(host, '资料与证据');
    area.append(h('p', { class: 'answer-note', text: '这里是工作区已收资料，不会扩大允许访问的范围。Agent 收录建议需确认后生效。' }));
    for (const material of ws.materials) area.append(materialCard(material));
    if (!ws.materials.length) area.append(emptyState('collection', '还没有资料', 'Agent 发现候选后会在这里出现；你也可以从证据集合或文件页加入。', null, true));
    if (ws.candidates.length) {
      const pending = disclosure('candidates', `待确认候选 · ${ws.candidates.length} 项`);
      for (const item of ws.candidates) {
        const source = item.source || {};
        const card = h('article', { class: 'ws-material' },
          h('span', { class: 'ws-badge warn', text: '候选' }),
          h('h4', { text: source.title || source.label || item.source_id }),
          h('p', { text: [source.platform === 'qq' ? 'QQ' : source.platform === 'wechat' ? '微信' : '', source.conversation, SOURCE_KINDS[item.source_type] || item.source_type].filter(Boolean).join(' · ') }),
          h('p', { class: 'ws-material-reason', text: '关联原因：' + (item.reason || '新发现的相关资料') }),
          h('div', { class: 'ws-inline-actions' },
            h('button', { class: 'quiet-button compact', type: 'button', text: '确认收录', onclick: async () => { await workspaceApi('/' + id + '/materials', { source_type: item.source_type, source_id: item.source_id, state: 'accepted', reason: item.reason }); await loadAll(); } }),
            h('button', { class: 'quiet-button compact danger-text', type: 'button', text: '排除', onclick: async () => { await workspaceApi('/' + id + '/materials', { source_type: item.source_type, source_id: item.source_id, state: 'excluded', reason: item.reason }); await loadAll(); } })));
        pending.append(card);
      }
      area.append(pending);
    }
  }

  function renderLogsTab(host) {
    const area = section(host, 'Agent 工作日志');
    if (!ws.home.tasks?.length) area.append(h('p', { class: 'answer-note', text: '还没有调查记录。' }));
    for (const task of ws.home.tasks || []) {
      const row = h('article', { class: 'ws-log-row' },
        h('p', { text: task.question }),
        h('p', { class: 'answer-note', text: `${taskStateLabel(task.state)} · ${when(task.created_at)} · ${Number(task.seconds || 0).toFixed(1)} 秒 · ${task.requests || 0} 次请求 · ${task.tools || 0} 次工具 · ${fmtNumber(task.tokens || 0)} tokens` }));
      if (task.result) row.append(h('p', { class: 'answer-note', text: task.result }));
      if (task.drafts) row.append(h('p', { class: 'answer-note', text: `保存 ${task.drafts} 项草稿／修订；是否已应用请查看成果及拟议改动。` }));
      row.append(h('div', { class: 'ws-inline-actions' },
        h('button', { class: 'quiet-button compact', type: 'button', text: '详细过程与结果', onclick: () => void showTask(task.id) }),
        task.state !== 'running' ? h('button', { class: 'quiet-button compact', type: 'button', text: '继续这项任务', onclick: () => compose('继续完成：' + task.question + '。先读取已有成果与草稿，沿用原成果继续修改并重新核对证据。') }) : null));
      area.append(row);
    }
  }

  async function renderHistoryTab(host) {
    const area = section(host, '历史版本');
    try {
      const data = await workspaceApi('/' + id + '/history');
      if (disposed || ws.tab !== 'history') return;
      area.replaceChildren(h('div', { class: 'ws-section-heading' }, h('h3', { text: '历史版本' })));
      for (const version of data.versions || []) {
        const details = disclosure('version-' + version.id, `v${version.version} · ${version.summary || '版本'} · ${version.name}`);
        details.append(h('p', { class: 'answer-note', text: when(version.created_at) }), h('pre', { class: 'ws-diff', text: version.diff || '' }));
        if (version.scope_epoch === ws.current.workspace.scope_epoch) {
          details.append(h('button', {
            class: 'quiet-button compact', type: 'button', text: '恢复此版本',
            onclick: async () => {
              const ok = await confirmDialog({ title: '恢复版本', message: `恢复“${version.name}”至 v${version.version}？`, details: '会保存为新版本，原始资料不变。', confirmText: '恢复版本' });
              if (!ok) return;
              try {
                const current = await workspaceApi(`/${id}/outputs/${version.output_id}`);
                await workspaceApi(`/${id}/outputs/${version.output_id}/restore`, { version: version.version, base_version: current.version, confirm: true });
                await loadAll();
              } catch (error) { toast(errorMessage(error, '版本恢复失败。'), 'bad'); }
            },
          }));
        }
        area.append(details);
      }
      if (!(data.versions || []).length) area.append(h('p', { class: 'ws-empty', text: '确认第一批建议后，版本历史会保留在这里。' }));
      const audit = disclosure('audit', '资料、设置与维护记录');
      const labels = { created: '创建工作区', material_decision: '资料关联决定', exclusion_released: '解除资料排除', scope_changed: '修改访问范围', bounded_backfill: '日期补查', gap_reconnected: '重新接续变化', version_applied: '应用版本', task_completed: '调查完成', material_proposal_applied: '确认资料建议', proposals_rejected: '忽略修改建议', maintenance_settings: '更新自动维护设置' };
      for (const row of data.audit || []) audit.append(h('p', { class: 'answer-note', text: when(row.created_at) + ' · ' + (labels[row.kind] || '工作区操作') }));
      area.append(audit);
    } catch (error) {
      area.append(h('p', { class: 'ws-warning', text: errorMessage(error, '历史版本读取失败。') }));
    }
  }

  function trace(task) {
    return (task.events || []).map(event => event.type === 'model_start'
      ? `请求模型 ${event.request}/${event.request_limit || 6} · 本次上下文 ${event.characters || 0} 字符 · 思考 ${event.reasoning_effort || '关闭'}`
      : (event.text || [event.type, event.name, event.summary ? JSON.stringify(event.summary) : ''].filter(Boolean).join(' · '))).join('\n');
  }

  async function showTask(taskId) {
    try {
      const task = await workspaceApi('/' + id + '/tasks/' + taskId);
      const body = h('div', {},
        h('p', { text: task.question }),
        h('p', { class: 'answer-note', text: `${taskStateLabel(task.state)} · 请求 ${task.record?.requests || 0} 次 · ${task.record?.seconds || 0} 秒 · ${task.record?.usage?.total_tokens || 0} tokens` }));
      if (task.record?.budget) body.append(h('p', { class: 'answer-note', text: `本轮预算：${task.record.budget.max_requests} 次请求 / ${task.record.budget.max_tool_calls} 次工具 / ${task.record.budget.max_messages} 条消息 / ${task.record.budget.max_evidence_chars} 字符 / ${task.record.budget.max_seconds} 秒` }));
      const detail = h('details', { class: 'disclosure' }, h('summary', { text: '真实工具过程' }), h('pre', { class: 'ws-progress', text: trace(task) }));
      body.append(detail);
      for (const claim of task.record?.result?.claims || []) {
        const p = h('p', { text: claim.text });
        for (const mid of claim.evidence_ids || []) p.append(h('button', { class: 'source-button open-chat', type: 'button', text: ' M' + mid, dataset: { messageId: String(mid) } }));
        for (const fid of claim.artifact_evidence_ids || []) p.append(h('button', { class: 'source-button open-artifact', type: 'button', text: ' ' + fid, dataset: { sourceId: String(fid).replace(/^F/, '').split(':')[0] } }));
        body.append(p);
      }
      for (const key of ['note', 'notice', 'error', 'continuation']) if (task.record?.[key]) body.append(h('p', { class: 'answer-note', text: task.record[key] }));
      if (task.record?.result?.error) body.append(h('p', { class: 'ws-warning', text: task.record.result.error }));
      body.append(h('p', { class: 'answer-note', text: '覆盖：' + JSON.stringify(task.record?.coverage || {}) }));
      if (task.record?.drafts_saved) body.append(h('p', { class: 'answer-note', text: '程序确认保存的草稿：' + (task.record.drafts_saved.map(item => item.name + ' (' + item.format + ')').join('、') || '本轮没有写入成果') }));
      openDialog({ title: '任务记录', subtitle: when(task.created_at), body, wide: true });
    } catch (error) { toast(errorMessage(error), 'bad'); }
  }

  function evidenceDialog(output, refs) {
    const body = h('div', {}, h('p', { class: 'answer-note', text: `v${output.version} · ${sourceCounts(output.sources || {})}` }));
    for (const ref of refs || []) body.append(sourceRef(ref), h('br'));
    if (!(refs || []).length) body.append(h('p', { text: '这份成果没有关联原始证据。' }));
    openDialog({ title: output.name + ' · 来源证据', body, wide: true });
  }

  async function showOutputEvidence(output) {
    try {
      const value = await workspaceApi(`/${id}/outputs/${output.id}`);
      evidenceDialog(value, value.refs);
    } catch (error) { toast(errorMessage(error), 'bad'); }
  }

  async function preview(outputId, editing = false) {
    try {
      const value = await workspaceApi(`/${id}/outputs/${outputId}`);
      const view = h('pre', { class: 'ws-preview', text: value.body || '' });
      const editor = h('textarea', { class: 'ws-editor', rows: 18, 'aria-label': '成果正文', hidden: !editing });
      editor.value = value.body || '';
      const status = h('p', { class: 'status-line', role: 'status' });
      const save = h('button', {
        class: 'primary-button compact', type: 'button', text: '预览手动修改', hidden: !editing,
        onclick: async () => {
          save.disabled = true;
          try {
            await workspaceApi(`/${id}/outputs/${outputId}`, { body: editor.value, base_version: value.version }, 'PATCH');
            ref.close();
            await loadAll();
            await proposals();
            toast('已生成待确认的手动修改建议。', 'success');
          } catch (error) { status.textContent = errorMessage(error, '修改未能保存。'); save.disabled = false; }
        },
      });
      const body = h('div', {}, view, editor,
        h('p', { class: 'answer-note', text: value.origin === 'user' ? '这是用户编辑内容，未经 Agent 事实核对。' : '事实引用经过来源检查，不保证模型解释正确。' }),
        h('div', { class: 'form-actions' },
          h('a', { class: 'quiet-button compact', href: `/api/workspaces/${id}/outputs/${outputId}/download`, download: '' }, icon('download'), '下载 ' + String(value.format || '').toUpperCase()),
          h('button', { class: 'quiet-button compact', type: 'button', text: '编辑', onclick: () => { view.hidden = true; editor.hidden = false; save.hidden = false; editor.focus(); } }),
          save,
          h('button', { class: 'quiet-button compact', type: 'button', text: '查看版本 / 回退', onclick: () => { ref.close(); setTab('history'); } })),
        status);
      let ref = openDialog({ title: value.name, subtitle: `v${value.version} · ${value.resource_kind === 'state' ? '当前状态' : value.format.toUpperCase()}`, body, wide: true });
      for (const source of value.refs || []) body.append(sourceRef(source));
    } catch (error) { toast(errorMessage(error, '成果读取失败。'), 'bad'); }
  }

  async function proposals(taskId = null) {
    try {
      const result = await workspaceApi('/' + id + '/proposals');
      const selected = (result.proposals || []).filter(item => !taskId || item.task_id === taskId);
      if (!selected.length) { toast('当前没有待确认修改。', 'warn'); return; }
      const checks = [];
      const body = h('div', {}, h('p', { class: 'answer-note', text: '选择需要的修改，先核对差异和证据，再一次性确认。新成果也不会自动应用；忽略后正式状态不变。' }));
      for (const proposal of selected) {
        const details = h('details', { class: 'ws-version' });
        details.open = true;
        const input = h('input', { type: 'checkbox', checked: true });
        const label = h('label', { class: 'check-row' }, input, h('span', { text: `${proposal.name} · ${proposal.summary || ''}` }));
        checks.push([proposal.id, input]);
        details.append(h('summary', { text: proposal.action === 'material' ? '收录资料建议' : proposal.base_version ? '修改现有成果' : '创建新成果' }),
          label, h('pre', { class: 'ws-diff', text: proposal.action === 'material' ? proposal.summary : (proposal.diff || proposal.body || '') }));
        for (const ref of proposal.refs || []) details.append(sourceRef(ref));
        body.append(details);
      }
      const chosen = () => checks.filter(([, input]) => input.checked).map(([pid]) => pid);
      const ref = openDialog({
        title: '修改建议 · 确认后才应用', subtitle: taskId ? '仅显示本次任务的建议' : '所有待确认修改', body, wide: true,
        footer: h('footer', { class: 'dialog-foot' },
          h('span', { class: 'field-hint', text: '应用与忽略都需要明确选择。' }), h('span', { class: 'spacer' }),
          h('button', {
            class: 'quiet-button', type: 'button', text: '忽略选中建议',
            onclick: async () => {
              try { await workspaceApi('/' + id + '/reject', { ids: chosen() }); ref.close(); await loadAll(); }
              catch (error) { toast(errorMessage(error, '忽略建议失败。'), 'bad'); }
            },
          }),
          h('button', {
            class: 'primary-button', type: 'button', text: '确认并应用选中改动',
            onclick: async () => {
              try { await workspaceApi('/' + id + '/apply', { ids: chosen(), confirm: true }); ref.close(); await loadAll(); toast('选中的改动已应用。', 'success'); }
              catch (error) { toast(errorMessage(error, '应用改动失败。'), 'bad'); }
            },
          })),
      });
    } catch (error) { toast(errorMessage(error, '修改建议读取失败。'), 'bad'); }
  }

  async function backfillDialog() {
    const start = h('input', { type: 'date' }), end = h('input', { type: 'date' });
    const onlyNew = checkbox('仅补查最近一次范围设置新增的部分', false);
    let offset = 0;
    const note = h('p', { class: 'answer-note', text: '每页最多 200 项消息、文件和已启用 QQ 资料，只登记候选，不调用模型。仍受工作区范围限制。' });
    const next = h('button', {
      class: 'primary-button', type: 'button', text: '补查下一页',
      onclick: async () => {
        next.disabled = true;
        try {
          const result = await workspaceApi('/' + id + '/backfill', { start: start.value, end: end.value, offset, new_scope_only: onlyNew.input.checked });
          offset = result.next_offset;
          note.textContent = `本页 ${result.count} 项；${result.has_more ? '仍有更多，继续下一页' : '这个日期窗口已扫描完毕'}。点击整理最近变化后才分析。`;
          await loadAll();
        } catch (error) { note.textContent = errorMessage(error); }
        finally { next.disabled = false; }
      },
    });
    const body = h('div', {},
      h('p', { class: 'field-hint', text: '需要明确起止日期。旧缺口不能保证完整恢复；补查只登记候选，不调用模型。' }),
      h('div', { class: 'date-fields' }, h('label', {}, '开始日期', start), h('label', {}, '截止日期（含当天）', end)),
      onlyNew, note,
      ws.current.workspace.gap ? h('button', {
        class: 'quiet-button compact danger-text', type: 'button', text: '确认补查限制并重新接续',
        onclick: async () => {
          const ok = await confirmDialog({ title: '重新接续变化', message: '确认已完成需要的日期补查？', details: '旧缺口不能保证完整恢复，将从当前变化序列重新接续。', confirmText: '重新接续' });
          if (!ok) return;
          try { await workspaceApi('/' + id + '/reconnect', { confirm: true }); ref.close(); await loadAll(); }
          catch (error) { toast(errorMessage(error), 'bad'); }
        },
      }) : null);
    const ref = openDialog({ title: '指定日期补查', body, footer: h('footer', { class: 'dialog-foot' }, h('span', { class: 'spacer' }), next), wide: true });
  }

  async function openForm(workspace = null) {
    if (formRef) return;
    try {
      if (!ws.conversations) {
        const data = await api('/api/data/settings');
        ws.conversations = data.conversations || [];
      }
      if (!ws.watchCards) {
        const cards = await api('/api/watch-cards');
        ws.watchCards = cards.cards || [];
      }
      if (!ws.collections) {
        const collections = await api('/api/collections');
        ws.collections = collections.collections || [];
      }
    } catch (error) { toast(errorMessage(error, '表单资料读取失败。'), 'bad'); return; }
    const goal = h('textarea', { rows: 3, maxLength: 2000, value: workspace?.goal || '' });
    const titleInput = h('input', { type: 'text', maxLength: 100, value: workspace?.title || '' });
    goal.addEventListener('input', () => { if (!workspace && !titleInput.dataset.edited) titleInput.value = goal.value.slice(0, 32); });
    titleInput.addEventListener('input', () => { titleInput.dataset.edited = '1'; });
    const platformChecks = ['qq', 'wechat'].map(value => {
      const input = h('input', { type: 'checkbox', checked: (workspace?.scope?.platforms || ['qq', 'wechat']).includes(value) });
      return { value, input, label: value === 'qq' ? 'QQ' : '微信' };
    });
    const selectedConversations = new Set((workspace?.scope?.conversations || []).map(raw => {
      try { return JSON.stringify(JSON.parse(raw)); } catch { return raw; }
    }));
    const search = h('input', { type: 'search', placeholder: '搜索联系人或群名…' });
    const optionsHost = h('div', { class: 'conversation-options' });
    const summary = h('p', { class: 'field-hint' });
    const start = h('input', { type: 'date' }), end = h('input', { type: 'date' });
    const dateText = value => value ? new Date(value + 8 * 3600000).toISOString().slice(0, 10) : '';
    start.value = workspace?.scope?.start ? dateText(workspace.scope.start) : '';
    end.value = workspace?.scope?.end ? dateText(workspace.scope.end - 1) : '';
    const selectedWatches = new Set(workspace?.watch_ids || []);
    const collection = selectControl([['', '不选择']].concat((ws.collections || []).map(row => [row.id, row.title])));
    const messageSeed = h('input', { type: 'text', placeholder: '可选，例如 M39527' });
    const fileSeed = h('input', { type: 'text', placeholder: '可选，例如 F12' });
    const scopeEpoch = workspace?.scope_epoch;

    function currentPlatforms() { return platformChecks.filter(item => item.input.checked).map(item => item.value); }
    function renderConversations() {
      const allowed = currentPlatforms();
      const query = search.value.trim().toLocaleLowerCase();
      const fragment = document.createDocumentFragment();
      let shown = 0;
      for (const row of ws.conversations || []) {
        if (!allowed.includes(row.platform) || (query && !row.conversation.toLocaleLowerCase().includes(query))) continue;
        shown += 1;
        const key = JSON.stringify([row.platform, row.conversation_id]);
        const input = h('input', { type: 'checkbox', checked: selectedConversations.has(key) });
        input.addEventListener('change', () => { if (input.checked) selectedConversations.add(key); else selectedConversations.delete(key); updateSummary(); });
        fragment.append(h('label', { class: 'conversation-option' }, input, h('span', { text: row.conversation })));
      }
      if (!shown) fragment.append(h('p', { class: 'field-hint', text: '没有匹配的会话。' }));
      optionsHost.replaceChildren(fragment);
      updateSummary();
    }
    function updateSummary() {
      const chosen = [...selectedConversations].filter(raw => { try { return currentPlatforms().includes(JSON.parse(raw)[0]); } catch { return false; } });
      summary.textContent = chosen.length ? `已选择 ${chosen.length} 个会话` : '未指定会话：允许搜查所选平台的全部已导入会话';
    }
    platformChecks.forEach(item => item.input.addEventListener('change', renderConversations));
    search.addEventListener('input', renderConversations);
    renderConversations();

    const watchList = h('div', { class: 'conversation-options' });
    for (const card of ws.watchCards || []) {
      const input = h('input', { type: 'checkbox', checked: selectedWatches.has(card.id) });
      input.addEventListener('change', () => { if (input.checked) selectedWatches.add(card.id); else selectedWatches.delete(card.id); });
      watchList.append(h('label', { class: 'conversation-option' }, input, h('span', { text: card.title })));
    }
    if (!(ws.watchCards || []).length) watchList.append(h('p', { class: 'field-hint', text: '暂无可关联的关注卡。' }));

    const body = h('div', {},
      fieldLabel(workspace ? '工作区目标' : '你希望在这里完成什么？', goal, 'Agent 的所有调查都围绕这个目标，成果需你确认后才会更新。'),
      fieldLabel('名称（可修改）', titleInput),
      h('div', { class: 'segmented', style: { marginTop: '9px' } }, platformChecks.map(item => h('label', { class: 'check-row' }, item.input, h('span', { text: item.label })))),
      fieldLabel('允许搜查的会话', search, '不选＝所选平台全部会话。'),
      optionsHost, summary,
      h('div', { class: 'date-fields' }, h('label', {}, '开始日期（留空不限）', start), h('label', {}, '截止日期（留空包含未来新消息）', end)),
      h('div', { class: 'field-label', text: '关联已有关注卡（可选，不复制基线和调度）' }), watchList);
    if (!workspace) {
      body.append(fieldLabel('起始证据集合（复制引用，不改原集合；最多 100 项）', collection),
        h('div', { class: 'field-grid' }, fieldLabel('起始聊天证据编号（可选，例如 M39527）', messageSeed), fieldLabel('起始文件编号（可选，例如 F12）', fileSeed)));
    }
    body.append(h('p', { class: 'answer-note', text: '默认只发现新候选；明确整理或开启自动维护后才分析，所有建议待确认。范围/目标改变后，旧任务和成果正文隔离，需在新范围重新取证。' }));

    if (workspace) {
      const frequency = selectControl([[0, '关闭'], [1440, '每天'], [10080, '每周'], [-1, '自定义']]);
      frequency.value = [0, 1440, 10080].includes(ws.schedule?.minutes) ? String(ws.schedule.minutes) : '-1';
      const minutes = h('input', { type: 'number', min: 30, max: 525600, step: 1, value: ws.schedule?.minutes || 1440, hidden: frequency.value !== '-1' });
      frequency.addEventListener('change', () => { minutes.hidden = frequency.value !== '-1'; });
      const profile = selectControl([['quick', '快速'], ['standard', '标准'], ['deep', '深入'], ['thorough', '充分']], ws.schedule?.profile || 'standard');
      const vision = selectControl([['ocr', '本地 OCR'], ['native', '原生识图（按需发送指定图片）']], ws.schedule?.vision || 'ocr');
      const settings = h('fieldset', { class: 'ws-schedule' },
        h('legend', { text: '自动维护 · 只提建议' }),
        h('div', { class: 'field-grid' }, fieldLabel('频率', frequency), fieldLabel('自定义间隔（分钟，至少 30）', minutes)),
        h('div', { class: 'field-grid' }, fieldLabel('每次维护的预算档位', profile), fieldLabel('识图方式', vision)),
        h('p', { class: 'answer-note', text: ws.schedule?.next_at ? '下次维护 ' + when(ws.schedule.next_at) : '当前关闭。应用运行期间调度，重启后保留设置。' }),
        ws.schedule?.error ? h('p', { class: 'ws-warning', text: ws.schedule.error }) : null,
        h('button', {
          class: 'quiet-button compact', type: 'button', text: '保存维护设置',
          onclick: async () => {
            try {
              const value = frequency.value === '-1' ? Number(minutes.value) : Number(frequency.value);
              await workspaceApi('/' + id + '/schedule', { minutes: value, profile: profile.value, vision: vision.value }, 'PUT');
              toast('维护设置已保存；只生成待确认建议。', 'success');
              await loadAll();
            } catch (error) { toast(errorMessage(error, '维护设置未能保存。'), 'bad'); }
          },
        }),
        h('div', { class: 'form-actions' },
          h('button', { class: 'quiet-button compact', type: 'button', text: '指定日期补查', onclick: () => { ref.close(); void backfillDialog(); } }),
          h('button', { class: 'quiet-button compact', type: 'button', text: '删除工作区', onclick: () => void deleteWorkspace(workspace) })));
      body.append(settings);
    }

    async function submit() {
      const platforms = currentPlatforms();
      if (!goal.value.trim()) { toast('请填写工作区目标。', 'warn'); goal.focus(); return; }
      if (!titleInput.value.trim()) { toast('请填写名称。', 'warn'); titleInput.focus(); return; }
      if (!platforms.length) { toast('请至少选择一个平台。', 'warn'); return; }
      const chosenConversations = [...selectedConversations].filter(raw => { try { return platforms.includes(JSON.parse(raw)[0]); } catch { return false; } });
      const payload = {
        goal: goal.value.trim(), title: titleInput.value.trim(),
        scope: { platforms, conversations: chosenConversations, start: start.value, end: end.value },
        watch_ids: [...selectedWatches],
      };
      try {
        if (workspace) {
          await workspaceApi('/' + id, { ...payload, revision: workspace.revision }, 'PATCH');
          ref.close();
          await loadAll();
          toast('工作区设置已保存。', 'success');
        } else {
          const seeds = [];
          if (messageSeed.value.trim()) seeds.push({ source_type: 'message', source_id: messageSeed.value.trim().replace(/^M/i, '') });
          if (fileSeed.value.trim()) seeds.push({ source_type: 'artifact_source', source_id: fileSeed.value.trim().replace(/^F/i, '') });
          const created = await workspaceApi('', { ...payload, seeds, collection_id: collection.value || null });
          ref.close();
          await navigate('workspaces/' + created.id);
        }
      } catch (error) { toast(errorMessage(error, '工作区保存失败。'), 'bad'); }
    }
    const ref = openDialog({
      title: workspace ? '工作区设置' : '新建工作区', subtitle: workspace ? scopeText(workspace) : '目标和访问范围稍后可以修改',
      body, wide: true,
      footer: h('footer', { class: 'dialog-foot' }, h('span', { class: 'field-hint', text: '正式成果更新都需确认。' }), h('span', { class: 'spacer' }),
        h('button', { class: 'quiet-button', type: 'button', text: '取消', onclick: () => ref.close() }),
        h('button', { class: 'primary-button', type: 'button', text: workspace ? '保存设置' : '创建工作区', onclick: () => void submit() })),
      onClose: () => { formRef = null; },
    });
    formRef = ref;
  }

  async function deleteWorkspace(workspace) {
    const ok = await confirmDialog({
      title: '删除工作区', message: `删除“${workspace.title}”？`,
      details: '会删除成果、版本和专用资料集合。原始聊天、文件、其他集合和独立关注卡保留。',
      confirmText: '删除工作区', danger: true,
    });
    if (!ok) return;
    try {
      await workspaceApi('/' + id, { confirm: true }, 'DELETE');
      formRef?.close();
      toast('工作区已删除。', 'success');
      navigate('workspaces');
    } catch (error) { toast(errorMessage(error, '工作区删除失败。'), 'bad'); }
  }

  void loadAll();
  return () => {
    disposed = true;
    detailToken += 1;
    clearTimeout(pollTimer);
    clearTimeout(refreshTimer);
    formRef?.close();
    formRef = null;
    ui = null;
    ws.current = null;
    ws.home = null;
  };
}

async function openCreateForm() {
  if (formRef) return;
  try {
    if (!ws.conversations) ws.conversations = (await api('/api/data/settings')).conversations || [];
    if (!ws.watchCards) ws.watchCards = (await api('/api/watch-cards')).cards || [];
    if (!ws.collections) ws.collections = (await api('/api/collections')).collections || [];
  } catch (error) { toast(errorMessage(error, '表单资料读取失败。'), 'bad'); return; }
  const goal = h('textarea', { rows: 3, maxLength: 2000, placeholder: '例如：整理本学期课程安排与所有作业截止时间' });
  const titleInput = h('input', { type: 'text', maxLength: 100, placeholder: '例如：本学期课程' });
  goal.addEventListener('input', () => { if (!titleInput.dataset.edited) titleInput.value = goal.value.slice(0, 32); });
  titleInput.addEventListener('input', () => { titleInput.dataset.edited = '1'; });
  const platformChecks = ['qq', 'wechat'].map(value => {
    const input = h('input', { type: 'checkbox', checked: true });
    return { value, input, label: value === 'qq' ? 'QQ' : '微信' };
  });
  const selected = new Set();
  const search = h('input', { type: 'search', placeholder: '搜索联系人或群名…' });
  const options = h('div', { class: 'conversation-options' });
  const summary = h('p', { class: 'field-hint' });
  const start = h('input', { type: 'date' }), end = h('input', { type: 'date' });
  const collection = selectControl([['', '不选择']].concat((ws.collections || []).map(row => [row.id, row.title])));
  const messageSeed = h('input', { type: 'text', placeholder: '可选，例如 M39527' });
  const fileSeed = h('input', { type: 'text', placeholder: '可选，例如 F12' });
  const watchChecks = new Map();
  const watchList = h('div', { class: 'conversation-options' });
  for (const card of ws.watchCards || []) {
    const input = h('input', { type: 'checkbox' });
    watchChecks.set(card.id, input);
    watchList.append(h('label', { class: 'conversation-option' }, input, h('span', { text: card.title })));
  }
  if (!(ws.watchCards || []).length) watchList.append(h('p', { class: 'field-hint', text: '暂无可关联的关注卡。' }));
  function platforms() { return platformChecks.filter(item => item.input.checked).map(item => item.value); }
  function renderConversations() {
    const allowed = platforms();
    const query = search.value.trim().toLocaleLowerCase();
    const fragment = document.createDocumentFragment();
    let count = 0;
    for (const row of ws.conversations || []) {
      if (!allowed.includes(row.platform) || (query && !row.conversation.toLocaleLowerCase().includes(query))) continue;
      count += 1;
      const key = JSON.stringify([row.platform, row.conversation_id]);
      const input = h('input', { type: 'checkbox', checked: selected.has(key) });
      input.addEventListener('change', () => { if (input.checked) selected.add(key); else selected.delete(key); updateSummary(); });
      fragment.append(h('label', { class: 'conversation-option' }, input, h('span', { text: row.conversation })));
    }
    if (!count) fragment.append(h('p', { class: 'field-hint', text: '没有匹配的会话。' }));
    options.replaceChildren(fragment);
    updateSummary();
  }
  function updateSummary() {
    const count = [...selected].filter(raw => { try { return platforms().includes(JSON.parse(raw)[0]); } catch { return false; } }).length;
    summary.textContent = count ? `已选择 ${count} 个会话` : '未指定会话：允许搜查所选平台的全部已导入会话';
  }
  platformChecks.forEach(item => item.input.addEventListener('change', renderConversations));
  search.addEventListener('input', renderConversations);
  renderConversations();
  const body = h('div', {},
    fieldLabel('你希望在这里完成什么？', goal),
    fieldLabel('名称（可修改）', titleInput),
    h('div', { class: 'segmented', style: { marginTop: '9px' } }, platformChecks.map(item => h('label', { class: 'check-row' }, item.input, h('span', { text: item.label })))),
    fieldLabel('允许搜查的会话', search, '不选＝所选平台全部会话。'), options, summary,
    h('div', { class: 'date-fields' }, h('label', {}, '开始日期（留空不限）', start), h('label', {}, '截止日期（留空包含未来新消息）', end)),
    h('div', { class: 'field-label', text: '关联已有关注卡（可选，不复制基线和调度）' }), watchList,
    fieldLabel('起始证据集合（复制引用，不改原集合；最多 100 项）', collection),
    h('div', { class: 'field-grid' }, fieldLabel('起始聊天证据编号（可选）', messageSeed), fieldLabel('起始文件编号（可选）', fileSeed)),
    h('p', { class: 'answer-note', text: '默认只发现新候选；明确整理后才分析，所有建议待确认。范围/目标改变后，旧任务和成果正文隔离。' }));
  const ref = openDialog({
    title: '新建工作区', subtitle: '目标和访问范围稍后可以修改', body, wide: true,
    footer: h('footer', { class: 'dialog-foot' }, h('span', { class: 'spacer' }),
      h('button', { class: 'quiet-button', type: 'button', text: '取消', onclick: () => ref.close() }),
      h('button', {
        class: 'primary-button', type: 'button', text: '创建工作区',
        onclick: async () => {
          const allowed = platforms();
          if (!goal.value.trim()) { toast('请填写工作区目标。', 'warn'); goal.focus(); return; }
          if (!titleInput.value.trim()) { toast('请填写名称。', 'warn'); titleInput.focus(); return; }
          if (!allowed.length) { toast('请至少选择一个平台。', 'warn'); return; }
          const conversations = [...selected].filter(raw => { try { return allowed.includes(JSON.parse(raw)[0]); } catch { return false; } });
          const seeds = [];
          if (messageSeed.value.trim()) seeds.push({ source_type: 'message', source_id: messageSeed.value.trim().replace(/^M/i, '') });
          if (fileSeed.value.trim()) seeds.push({ source_type: 'artifact_source', source_id: fileSeed.value.trim().replace(/^F/i, '') });
          try {
            const created = await workspaceApi('', {
              goal: goal.value.trim(), title: titleInput.value.trim(),
              scope: { platforms: allowed, conversations, start: start.value, end: end.value },
              seeds, collection_id: collection.value || null,
            });
            const watchIds = [...watchChecks].filter(([, input]) => input.checked).map(([wid]) => wid);
            if (watchIds.length) await workspaceApi('/' + created.id, { watch_ids: watchIds, revision: created.revision }, 'PATCH');
            ref.close();
            navigate('workspaces/' + created.id);
          } catch (error) { toast(errorMessage(error, '工作区创建失败。'), 'bad'); }
        },
      })),
    onClose: () => { formRef = null; },
  });
  formRef = ref;
}

registerView('workspaces', {
  title: '工作区',
  subtitle: '持续调查、成果版本与证据引用',
  async mount(root, params) {
    ws.mounted = true;
    ws.expanded = new Map();
    if (params[0]) {
      const cleanup = renderDetail(root, params[0]);
      return () => { ws.mounted = false; cleanup(); };
    }
    const cleanup = renderList(root);
    return () => { ws.mounted = false; cleanup(); };
  },
});

registerAction('open-workspace', id => navigate('workspaces/' + id));
