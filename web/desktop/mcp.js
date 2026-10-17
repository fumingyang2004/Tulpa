'use strict';

/* External Agent / MCP: service, grants, dynamic permissions, chat learning and audit. */
import {
  $, h, icon, api, on, registerView, toast, errorMessage, fieldLabel,
  openDialog, emptyState, loadingState, fmtFullTime, fmtNumber, copyText,
} from './app.js';

const TABS = [
  ['service', '服务与连接'],
  ['create', '新建授权'],
  ['chats', '群聊与学习'],
  ['operations', '操作记录'],
];

const state = {
  status: null,
  permissions: null,
  connections: [],
  selected: new Map(),
  chats: null,
  chatsLoading: false,
  operations: [],
  recent: [],
  tab: 'service',
  credential: null,
  liveGroups: null,
  pickerOffset: 0,
  pickerMore: false,
  chatTimer: null,
  learningTimer: null,
  mounted: false,
};

let ui = null;
let pickerToken = 0;
let permissionInputs = null;

function permissionById(id) {
  return (state.permissions || []).find(item => item.id === id);
}

function permissionSummary(scope) {
  const labels = [];
  for (const item of state.permissions || []) {
    if (scope?.[item.id]) labels.push(item.label + (item.scope === 'account' ? '（整个账号）' : ''));
  }
  return labels.length ? labels.join('、') : '仅资料读取';
}

function allPermissionIds() {
  return (state.permissions || []).map(item => item.id);
}

function setPermissionState(map, { create = false, changed = null } = {}) {
  if (!map) return;
  // Normalise prerequisites first, then compute capability state from the
  // resulting values. An explicit send-off change clears chat (and therefore
  // all chat media); checking chat or send turns on direct send. Computing
  // `active` before this point left the media checkboxes disabled until an
  // unrelated second change event.
  if (changed === 'send' && !map.send?.checked) map.chat.checked = false;
  else if (map.chat?.checked) map.send.checked = true;
  const active = Boolean(map.chat?.checked && map.send?.checked);
  for (const id of ['chat_reactions', 'chat_images', 'chat_voice', 'chat_video', 'chat_files']) {
    const input = map[id];
    if (!input) continue;
    const allow = active && (id === 'chat_images' || id === 'chat_reactions' || id === 'chat_voice' || id === 'chat_video' || id === 'chat_files');
    input.disabled = !allow;
    if (!allow) input.checked = false;
  }
  for (const id of ['chat_sticker_send', 'chat_sticker_collect']) {
    const input = map[id];
    if (!input) continue;
    const allow = active && Boolean(map.chat_images?.checked);
    input.disabled = !allow;
    if (!allow) input.checked = false;
  }
  for (const item of state.permissions || []) {
    if (!item.extended) continue;
    if (['chat_voice', 'chat_video', 'chat_files'].includes(item.id)) continue;
    if (item.id === 'qq_friends' || item.id === 'qq_profile' || item.id === 'qq_qzone') {
      // Account-level permissions remain independently selectable.
      continue;
    }
  }
}

function buildPermissionFields(host, scope = {}, { create = false } = {}) {
  host.replaceChildren();
  const map = {};
  const base = (state.permissions || []).filter(item => !item.extended && !['chat_images', 'chat_sticker_send', 'chat_sticker_collect', 'chat_reactions'].includes(item.id));
  const chatMedia = (state.permissions || []).filter(item => ['chat_images', 'chat_sticker_send', 'chat_sticker_collect', 'chat_reactions'].includes(item.id));
  const extended = (state.permissions || []).filter(item => item.extended);
  const makeGroup = (title, items, description = '') => {
    const group = h('fieldset', { class: 'mcp-permission-group' }, h('legend', { text: title }));
    const grid = h('div', { class: 'mcp-permission-grid' });
    for (const item of items) {
      const input = h('input', { type: 'checkbox', checked: Boolean(scope[item.id]) });
      input.addEventListener('change', () => setPermissionState(map, { create, changed: item.id }));
      map[item.id] = input;
      grid.append(h('label', { class: 'mcp-permission' }, input, h('span', {}, item.label,
        h('small', { text: item.scope === 'account' ? '整个账号' : '按所选会话范围' }))));
    }
    group.append(grid);
    if (description) group.append(h('p', { class: 'field-hint', text: description }));
    host.append(group);
  };
  makeGroup('按需处理与发送', base, '发送与群管理默认关闭；勾选后外部 Agent 可直接执行，无需逐次审批。');
  makeGroup('持续群聊与表情', chatMedia, '持续群聊需要同时允许直接发送；表情发送/收藏还需要看图权限。');
  makeGroup('SL 扩展权限', extended, '默认关闭。标注“整个账号”的权限不受所选群或私聊范围限制。');
  setPermissionState(map, { create });
  return map;
}

function createConnectionCard(row) {
  const scope = row.scope || {};
  const selectedCount = (scope.conversations || []).length;
  const card = h('article', { class: 'mcp-connection' },
    h('header', {}, h('strong', { text: row.name + (row.revoked ? ' · 已撤销' : '') }),
      h('span', { class: 'badge ' + (row.revoked ? '' : row.valid === false ? 'bad' : 'ok'), text: row.revoked ? '已撤销' : row.valid === false ? '账号已变化' : '有效' })),
    h('p', { text: `${(scope.platforms || []).map(p => p === 'qq' ? 'QQ' : '微信').join(' / ')} · ${selectedCount ? selectedCount + ' 个指定会话' : '全部会话'} · ${scope.start || '不限起点'} 至 ${scope.end || '包括未来消息'}` }),
    h('p', { text: '操作权限：' + permissionSummary(scope) }),
    h('p', { text: '来源账号：' + Object.entries(row.accounts || {}).map(([p, id]) => (p === 'qq' ? 'QQ' : '微信') + ' ' + (id || '当前导入资料')).join(' / ') + ' · ' + fmtNumber(row.calls || 0) + ' 次调用' }),
    h('p', { class: 'mcp-muted', text: 'revision ' + (row.revision ?? 0) + ' · 创建于 ' + fmtFullTime(row.created_at) }));
  if (row.valid === false && !row.revoked) card.append(h('p', { class: 'mcp-warning', text: '来源账号已改变，请撤销并重新创建连接。' }));
  if (!row.revoked) {
    card.append(h('div', { class: 'mcp-connection-foot' },
      h('button', { class: 'quiet-button compact', type: 'button', text: '修改权限', onclick: () => void editPermissions(row) }),
      h('button', {
        class: 'quiet-button compact danger-text', type: 'button', text: '撤销连接',
        onclick: async () => {
          try {
            await api('/api/mcp/connections/' + encodeURIComponent(row.id) + '/revoke', { method: 'POST', body: {} });
            toast('连接已撤销。', 'success');
            await refreshService();
          } catch (error) { toast(errorMessage(error, '撤销连接失败。'), 'bad'); }
        },
      })));
  }
  return card;
}

async function refreshService() {
  const data = await api('/api/mcp', { timeout: 16000 });
  state.status = data;
  state.connections = data.connections || [];
  state.recent = data.recent || [];
  renderService();
  return data;
}

function renderService() {
  if (!ui) return;
  const data = state.status;
  if (!data) return;
  const running = Boolean(data.running);
  ui.serviceCard.className = 'mcp-service-card ' + (running ? 'running' : data.error ? 'error' : '');
  ui.serviceState.replaceChildren(icon(running ? 'agents' : 'alert'));
  ui.serviceTitle.textContent = running ? '本机 MCP 服务运行中' : data.error ? 'MCP 服务未启动' : 'MCP 服务未开启';
  ui.serviceDetail.textContent = data.error || (running ? data.url : '开启后，外部 Agent 才能通过本机 Streamlit HTTP 连接读取授权资料。');
  ui.serviceToggle.textContent = running ? '停用服务' : '开启服务';
  ui.serviceToggle.disabled = false;
  ui.servicePort.value = String(data.port ?? 18777);
  ui.serviceEnabled.checked = Boolean(data.enabled);
  ui.dataStatus.textContent = Object.keys(data.data_platforms || {}).length
    ? Object.entries(data.data_platforms).map(([p, n]) => (p === 'qq' ? 'QQ' : '微信') + ' ' + fmtNumber(n) + ' 条本地消息').join(' · ')
    : '暂无聊天资料。历史查询需要先导入；仅实时群聊可配置 OneBot 后直接选择群、授权，无需导入。';
  if (ui.createStatus) {
    ui.createStatus.textContent = running
      ? '本机 MCP 服务已运行，可以创建连接。'
      : '本机 MCP 服务尚未运行；创建连接时会按上方端口开启服务。';
  }
  ui.onebotStatus.textContent = data.onebot_configured ? '已保存 OneBot 配置；公告、精华及群文件可按权限读取。' : 'OneBot 未配置。本地聊天查询仍可使用。';
  ui.connections.replaceChildren();
  if (!state.connections.length) ui.connections.append(h('div', { class: 'mcp-empty', text: '还没有授权连接。创建一个连接，选择范围和权限。' }));
  else for (const row of state.connections) ui.connections.append(createConnectionCard(row));
  renderAudit();
}

function renderAudit() {
  if (!ui) return;
  ui.audit.replaceChildren();
  if (!(state.recent || []).length) ui.audit.append(h('p', { class: 'mcp-muted', text: '暂无访问记录。' }));
  for (const row of state.recent || []) {
    ui.audit.append(h('p', { class: 'mcp-audit-row', text: `${fmtFullTime(row.at)} · ${row.name} · ${row.tool} · ${row.status} · ${row.seconds || 0} 秒 · ${row.messages || 0} 条消息` }));
  }
}

async function saveService(enabled, port) {
  try {
    const status = await api('/api/mcp', { method: 'PUT', body: { enabled, port } });
    state.status = { ...state.status, ...status };
    await refreshService();
    toast(enabled ? 'MCP 服务已开启。' : 'MCP 服务已停用。', 'success');
  } catch (error) { toast(errorMessage(error, 'MCP 服务设置未能保存。'), 'bad'); }
}

/* ---------- permissions edit ---------- */
async function editPermissions(row) {
  let map = null;
  const form = h('div', {});
  map = buildPermissionFields(form, row.scope || {}, { create: false });
  const status = h('p', { class: 'status-line', role: 'status' });
  const body = h('div', {}, h('p', { class: 'mcp-muted', text: '原凭据继续使用，revision 会阻止旧页面覆盖新设置。已运行的操作会按新权限重新校验；新增在线能力需要当前 QQ 账号在线。' }), form, status);
  const save = h('button', {
    class: 'primary-button', type: 'button', text: '保存权限',
    onclick: async () => {
      save.disabled = true;
      status.textContent = '正在保存…';
      status.className = 'status-line';
      const payload = { expected_revision: row.revision ?? 0 };
      for (const id of allPermissionIds()) payload[id] = Boolean(map[id]?.checked);
      try {
        await api('/api/mcp/connections/' + encodeURIComponent(row.id), { method: 'PUT', body: payload });
        dialog.close();
        toast('权限已保存，原凭据继续有效。', 'success');
        await refreshService();
      } catch (error) {
        status.textContent = errorMessage(error, '权限未能保存；请重新打开连接后再试。');
        status.className = 'status-line error';
        save.disabled = false;
      }
    },
  });
  const dialog = openDialog({
    title: row.name + ' · 修改权限', subtitle: '凭据不变，权限按 revision 并发保护', body, wide: true,
    footer: h('footer', { class: 'dialog-foot' }, h('span', { class: 'spacer' }),
      h('button', { class: 'quiet-button', type: 'button', text: '取消', onclick: () => dialog.close() }), save),
  });
}

/* ---------- create connection ---------- */
function buildCreatePanel() {
  const name = h('input', { type: 'text', maxLength: 60, placeholder: '例如：Codex 课程资料调查', autocomplete: 'off' });
  const qq = h('input', { type: 'checkbox', checked: true });
  const wechat = h('input', { type: 'checkbox', checked: true });
  const allConversations = h('input', { type: 'checkbox' });
  const start = h('input', { type: 'date' });
  const end = h('input', { type: 'date' });
  const search = h('input', { type: 'search', placeholder: '搜索群名或联系人' });
  const picker = h('div', { class: 'mcp-chat-list' });
  const selectionNote = h('p', { class: 'mcp-muted', text: '已选择 0 个会话' });
  const more = h('button', { class: 'quiet-button compact', type: 'button', text: '更多会话', hidden: true });
  const liveGroups = h('button', { class: 'quiet-button compact', type: 'button', text: '从 OneBot 选择 QQ 群 · 无需导入' });
  const liveFriends = h('button', { class: 'quiet-button compact', type: 'button', text: '从 OneBot 选择 QQ 私聊 · 无需导入' });
  const pickerWrap = h('div', { class: 'mcp-picker-wrap' },
    h('label', { class: 'field-label', for: 'mcp-chat-search', text: '选择会话' }),
    search,
    h('div', { class: 'mcp-picker-actions' }, liveGroups, liveFriends),
    picker, more, selectionNote);
  const permissionsHost = h('div', {});
  const status = h('p', { class: 'mcp-create-status mcp-muted', role: 'status', 'aria-live': 'polite' });
  const submit = h('button', { class: 'primary-button', type: 'submit' }, '检查服务并创建连接');
  const createError = h('p', { class: 'mcp-warning', role: 'alert' });
  const qqBox = h('label', { class: 'check-row' }, qq, h('span', { text: 'QQ' }));
  const wechatBox = h('label', { class: 'check-row' }, wechat, h('span', { text: '微信' }));
  const allLabel = h('label', { class: 'check-row' }, allConversations, h('span', { text: '允许所选平台全部会话（包括以后入库的会话）' }));
  const form = h('form', { class: 'mcp-create-form', autocomplete: 'off' },
    fieldLabel('连接名称', name, '最多 60 字，仅用于识别这份授权。'),
    h('fieldset', {}, h('legend', { text: '允许访问' }),
      h('div', { class: 'row wrap' }, qqBox, wechatBox), allLabel, pickerWrap),
    h('div', { class: 'field-grid' }, fieldLabel('开始日期', start), fieldLabel('截止日期（含当天）', end)),
    h('p', { class: 'field-hint', text: '留空表示不限制；身份目录也遵守这个范围。' }),
    h('fieldset', {}, h('legend', { text: '按需处理与操作权限' }), permissionsHost),
    h('p', { class: 'field-hint', text: '普通消息查询可连续分页。文件和语音处理仅生成本地缓存。OneBot 群信息和成员是查询当时的状态，不能还原历史。可在已授权连接中修改权限，原凭据继续使用。' }),
    createError, status, submit);
  let map = null;
  let searchTimer = null;

  function selectedPayload() {
    return [...state.selected.entries()].map(([, value]) => value);
  }
  function selectionNoteUpdate() {
    selectionNote.textContent = `已选择 ${state.selected.size} 个会话（搜索不会清除已选）`;
  }
  function platformsChecked() {
    return ['qq', 'wechat'].filter(p => (p === 'qq' ? qq : wechat).checked);
  }
  async function loadPicker(reset = true) {
    const token = ++pickerToken;
    if (reset) { state.pickerOffset = 0; picker.replaceChildren(); }
    const platforms = platformsChecked();
    if (!platforms.length) {
      picker.append(h('p', { class: 'mcp-muted', text: '请先选择允许访问的平台。' }));
      more.hidden = true;
      return;
    }
    try {
      let data;
      if (state.liveGroups) {
        const query = search.value.trim().toLocaleLowerCase();
        data = { items: state.liveGroups.filter(row => row.name.toLocaleLowerCase().includes(query) && platforms.includes(row.platform)), has_more: false, next_offset: 0 };
      } else {
        data = await api('/api/mcp/conversations?' + new URLSearchParams({
          query: search.value.trim(), offset: String(state.pickerOffset), platform: platforms.length === 1 ? platforms[0] : '',
        }), { timeout: 16000 });
      }
      if (token !== pickerToken || !ui) return;
      for (const item of (data.items || []).filter(row => platforms.includes(row.platform))) {
        const pair = [item.platform, item.conversation_id];
        const key = JSON.stringify(pair);
        const input = h('input', { type: 'checkbox', checked: state.selected.has(key) });
        input.addEventListener('change', () => { if (input.checked) state.selected.set(key, pair); else state.selected.delete(key); selectionNoteUpdate(); });
        picker.append(h('label', {}, input, h('span', { text: (item.platform === 'qq' ? 'QQ' : '微信') + ' · ' + item.name + ' (' + item.count + ')' })));
      }
      if (reset && !(data.items || []).length) picker.append(h('p', { class: 'mcp-muted', text: state.liveGroups ? '没有匹配的实时群或好友。' : '没有匹配的已导入会话。' }));
      state.pickerOffset = data.next_offset || 0;
      state.pickerMore = Boolean(data.has_more);
      more.hidden = !state.pickerMore;
      selectionNoteUpdate();
    } catch (error) {
      if (token === pickerToken && ui) picker.append(h('p', { class: 'mcp-warning', text: errorMessage(error, '会话目录读取失败。') }));
    }
  }
  async function loadLive(kind) {
    try {
      const data = await api(kind === 'friends' ? '/api/mcp/live-friends' : '/api/mcp/live-groups', { timeout: 16000 });
      state.liveGroups = data.items || [];
      await loadPicker(true);
    } catch (error) {
      createError.textContent = errorMessage(error, '无法从 OneBot 读取目录；请确认连接在线。');
    }
  }
  liveGroups.addEventListener('click', () => void loadLive('groups'));
  liveFriends.addEventListener('click', () => void loadLive('friends'));
  search.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => void loadPicker(true), 200);
  });
  more.addEventListener('click', () => void loadPicker(false));
  allConversations.addEventListener('change', () => { pickerWrap.hidden = allConversations.checked; });
  for (const box of [qq, wechat]) box.addEventListener('change', () => {
    state.liveGroups = null;
    for (const [key, pair] of [...state.selected]) if (!platformsChecked().includes(pair[0])) state.selected.delete(key);
    void loadPicker(true);
  });
  map = buildPermissionFields(permissionsHost, {}, { create: true });

  function validate() {
    if (!name.value.trim()) throw new Error('请填写连接名称。');
    const platforms = platformsChecked();
    if (!platforms.length) throw new Error('请选择允许访问的平台。');
    if (!allConversations.checked && !state.selected.size) throw new Error('请选择会话，或明确勾选允许所选平台全部会话。');
    if (start.value && end.value && start.value > end.value) throw new Error('截止日期不能早于开始日期。');
    return platforms;
  }

  async function submitCreate(event) {
    event.preventDefault();
    createError.textContent = '';
    submit.disabled = true;
    submit.textContent = '正在检查并创建…';
    try {
      const platforms = validate();
      if (!state.permissions) throw new Error('权限目录尚未加载，请关闭后重新打开。');
      const body = {
        name: name.value.trim(),
        platforms,
        conversations: allConversations.checked ? [] : selectedPayload(),
        all_conversations: allConversations.checked,
        start: start.value,
        end: end.value,
      };
      for (const id of allPermissionIds()) body[id] = Boolean(map[id]?.checked);
      const latest = await api('/api/mcp', { timeout: 16000 });
      state.status = { ...state.status, ...latest };
      const hasExtended = (state.permissions || []).some(item => item.extended && body[item.id]);
      const empty = platforms.some(platform => !latest.data_platforms?.[platform] &&
        !(platform === 'qq' && ((body.chat && body.send) || hasExtended)));
      if (empty && allConversations.checked && !body.send && !body.manage && !body.chat && !body.onebot && !hasExtended) {
        body.allow_future_imports = true;
      } else if (empty) {
        throw new Error('请先导入所选平台的聊天；预先连接需要选择全部会话并保持只读。');
      }
      if (!latest.running) {
        const port = Number(ui.servicePort.value);
        if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('服务端口应为 1024 至 65535 的整数。');
        const started = await api('/api/mcp', { method: 'PUT', body: { enabled: true, port } });
        state.status = { ...state.status, ...started };
        if (!started.running) throw new Error(started.error || 'MCP 服务尚未启动，请稍后重试。');
      }
      const credential = await api('/api/mcp/connections', { method: 'POST', body, timeout: 30000 });
      state.credential = { ...credential, url: state.status.url || ('http://127.0.0.1:' + (state.status.port || 18777) + '/mcp'), scope: body };
      showSecret();
      await refreshService();
      toast('连接凭据已创建。Token 只显示这一次。', 'success');
    } catch (error) {
      createError.textContent = errorMessage(error, '连接创建失败。');
    } finally {
      submit.disabled = false;
      submit.textContent = state.status?.running ? '创建连接凭据' : '检查服务并创建连接';
    }
  }
  form.addEventListener('submit', submitCreate);
  void loadPicker(true);
  return { element: form, submit, map: () => map, load: () => loadPicker(true) };
}

function showSecret() {
  if (!ui || !state.credential) return;
  ui.secret.hidden = false;
  ui.secretUrl.value = state.credential.url;
  ui.secretToken.value = state.credential.token;
  ui.secretToken.type = 'password';
  ui.testResult.textContent = '';
  ui.installResult.textContent = '';
  codexConfig();
  ui.secret.scrollIntoView({ block: 'nearest' });
}

function codexConfig() {
  if (!state.credential || !ui) return false;
  const name = ui.serverName.value;
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(name)) {
    ui.codex.disabled = ui.copyConfig.disabled = true;
    ui.installResult.textContent = 'MCP 名称请填写 1–64 个英文字母、数字、下划线或短横线。';
    ui.codex.value = '';
    return false;
  }
  ui.codex.disabled = ui.copyConfig.disabled = false;
  ui.installResult.textContent = '';
  const scope = state.credential.scope || {};
  let text = `[mcp_servers.${name}]\nurl = ${JSON.stringify(state.credential.url)}\nhttp_headers = { Authorization = ${JSON.stringify('Bearer ' + state.credential.token)} }\ntool_timeout_sec = 240\n`;
  for (const [flag, tool] of [['send', 'send_qq_message'], ['manage', 'manage_qq_group']]) {
    if (scope[flag]) text += `\n[mcp_servers.${name}.tools.${tool}]\napproval_mode = "approve"\n`;
  }
  if (scope.chat) {
    for (const tool of ['start_chat_session', 'get_chat_session', 'list_chat_sessions', 'wait_chat_messages',
      'plan_chat_reply', 'send_chat_reply', 'send_chat_message', 'stop_chat_session', 'list_chat_groups',
      'list_chat_personas', 'get_chat_learning', 'claim_chat_learning', 'submit_chat_learning', 'select_chat_expressions']) {
      text += `\n[mcp_servers.${name}.tools.${tool}]\napproval_mode = "approve"\n`;
    }
  }
  for (const [flag, tools] of [
    ['chat_reactions', ['react_to_chat_message']],
    ['chat_images', ['read_chat_image', 'list_chat_stickers', 'read_chat_sticker', 'note_chat_sticker']],
    ['chat_sticker_send', ['send_chat_sticker']],
    ['chat_sticker_collect', ['collect_chat_sticker']],
    ['chat_voice', ['read_chat_attachment', 'send_chat_message']],
    ['chat_video', ['read_chat_attachment']],
    ['chat_files', ['read_chat_attachment', 'upload_qq_file', 'manage_qq_file']],
  ]) {
    if (scope[flag]) for (const tool of tools) text += `\n[mcp_servers.${name}.tools.${tool}]\napproval_mode = "approve"\n`;
  }
  ui.codex.value = text;
  return true;
}

async function testConnection() {
  if (!state.credential) return;
  ui.test.disabled = true;
  ui.testResult.textContent = '正在验证服务、授权和工具…';
  try {
    const result = await api('/api/mcp/test', { method: 'POST', body: { token: state.credential.token }, timeout: 30000 });
    ui.testResult.textContent = `检测通过 · ${result.tools} 个可用工具 · ${result.onebot ? 'OneBot 群资料可用' : '当前未启用 OneBot 群资料'}。本次未调用模型。`;
    ui.testResult.className = 'status-line success';
  } catch (error) {
    ui.testResult.textContent = '检测未通过：' + errorMessage(error);
    ui.testResult.className = 'status-line error';
  } finally { ui.test.disabled = false; }
}

async function installCodex() {
  if (!codexConfig() || !state.credential) return;
  ui.install.disabled = true;
  ui.serverName.disabled = true;
  try {
    await api('/api/mcp/connections/' + encodeURIComponent(state.credential.id) + '/codex', {
      method: 'POST', body: { token: state.credential.token, server_name: ui.serverName.value },
    });
    ui.installResult.textContent = '已写入本机 Codex：' + ui.serverName.value + '。请在 Codex 的 MCP 设置中重新连接；若仍未出现，请重启 Codex。';
    ui.installResult.className = 'status-line success';
  } catch (error) {
    ui.installResult.textContent = errorMessage(error, '写入 Codex 失败；可复制配置手动添加。');
    ui.installResult.className = 'status-line error';
  } finally { ui.install.disabled = false; ui.serverName.disabled = false; }
}

/* ---------- chats / learning ---------- */
async function refreshChats() {
  if (!ui || state.chatsLoading) return;
  state.chatsLoading = true;
  try {
    const data = await api('/api/mcp/chat-overview', { timeout: 16000 });
    state.chats = data;
    renderChats();
  } catch (error) {
    if (ui) ui.chatStatus.textContent = '暂时无法更新群聊状态：' + errorMessage(error) + '。已有记录保留，稍后自动重试。';
  } finally { state.chatsLoading = false; }
}

function learningThreshold(library) {
  if (!library) return '';
  return library.checked_expressions < 10
    ? `${library.checked_expressions}/10，继续积累后才用于聊天`
    : `${library.checked_expressions} 条已通过检查，可按语境使用`;
}

function renderChats() {
  if (!ui || !state.chats) return;
  const data = state.chats;
  const active = (data.groups || []).reduce((count, group) => count + (group.active?.length || 0), 0);
  ui.chatSummary.textContent = active ? `${active} 个持续聊天会话正在运行` : (data.groups || []).length ? '暂无进行中的会话 · 学习记录已保留' : '在 Agent 对话中开始持续聊天';
  ui.chatEvent.textContent = data.receiver ? `实时连接：${data.receiver.note || data.receiver.state}` : '';
  ui.chatStatus.textContent = active ? `${active} 个持续聊天会话 · 潜水时也继续积累` : '暂无正在运行的群聊。开始后接续原账号、原群的积累。';
  ui.stopAll.disabled = !active;
  ui.chatSessions.replaceChildren();
  if (!(data.groups || []).length) {
    ui.chatSessions.append(emptyState('chat', '还没有持续群聊记录', '请在已连接的 Agent 对话中指定群聊和人格开始。Tulpa 不会自动启动模型。', null, true));
    return;
  }
  for (const group of data.groups) {
    const card = h('article', { class: 'mcp-group' },
      h('div', { class: 'mcp-group-head' },
        h('h3', { text: group.name }),
        h('span', { class: 'badge ' + (group.active?.length ? 'ok' : ''), text: group.active?.length ? '进行中' : '已停止' })),
      h('p', { text: `机器人 QQ ${group.account} · 群号 ${group.group}` }));
    for (const run of group.active || []) {
      const phase = run.turn_process?.phase;
      const chatState = ['REPLYING', 'SENDING'].includes(phase) ? '准备回复' : '潜水';
      card.append(h('div', { class: 'mcp-active-run' },
        h('p', { text: `${chatState} · ${run.persona_name || '自定义人格'} · 会话 ${String(run.id).slice(0, 8)}` }),
        h('button', {
          class: 'quiet-button compact', type: 'button', text: '停止持续水群',
          onclick: async event => {
            event.currentTarget.disabled = true;
            try { await api('/api/mcp/chats/' + encodeURIComponent(run.id) + '/stop', { method: 'POST', body: {} }); await refreshChats(); }
            catch (error) { toast(errorMessage(error), 'bad'); event.currentTarget.disabled = false; }
          },
        })));
    }
    const learning = group.learning;
    const library = learning?.library;
    if (library) {
      card.append(h('div', { class: 'mcp-learning-stats' },
        h('p', { text: '学习 · ' + group.learning_state }),
        h('p', { text: '表达 · ' + learningThreshold(library) }),
        h('p', { text: `黑话 · 已掌握 ${library.known_jargon} · 待观察 ${library.observing_jargon}` })));
      if (library.pending_expressions) card.append(h('p', { class: 'mcp-muted', text: `${library.pending_expressions} 条旧表达已保留，需重新核对群友原话，暂不参与回复。` }));
      card.append(h('p', { class: 'mcp-muted', text: '本群最近完成学习：' + (library.last_completed_at ? fmtFullTime(library.last_completed_at) : '尚无') }));
      if (group.active?.length) card.append(h('p', { class: 'mcp-muted', text: `本次运行：已完成 ${learning.run?.completed_batches || 0} 批 · 正在积累 ${learning.run?.buffered || 0} 条新消息` }));
    } else {
      card.append(h('p', { class: 'mcp-warning', text: group.error || '暂时无法读取学习状态，请检查连接。' }));
    }
    card.append(h('div', { class: 'mcp-group-actions' },
      h('button', { class: 'quiet-button compact', type: 'button', text: '查看学习记录', disabled: !learning, onclick: () => void openLearning(group) })));
    if ((group.active || []).length) {
      const details = h('details', {}, h('summary', { text: '当前会话详情' }));
      for (const run of group.active) details.append(h('p', { text: `${run.connection_name || '连接'} · ${run.id}` }), h('pre', { text: run.persona || '' }), run.gap_note ? h('p', { class: 'mcp-warning', text: run.gap_note }) : null);
      card.append(details);
    }
    if ((group.history || []).length) {
      const history = h('details', {}, h('summary', { text: `历史会话（${group.history.length}）` }));
      for (const row of group.history) history.append(h('p', { class: 'mcp-muted', text: `${row.persona_name || '自定义人格'} · ${row.connection_name} · 已停止 · ${fmtFullTime(row.updated)}` }));
      card.append(history);
    }
    ui.chatSessions.append(card);
  }
}

async function openLearning(group) {
  let panel = null;
  let loading = false;
  let changing = false;
  const summary = h('p', { class: 'mcp-muted', text: '正在读取学习记录…' });
  const error = h('p', { class: 'mcp-warning' });
  const records = h('div', {});
  const query = '/api/mcp/chat-library?conversation_id=' + encodeURIComponent(group.conversation_id);
  const advanced = h('details', { class: 'disclosure' }, h('summary', { text: '高级设置与检查详情' }));
  const advancedBody = h('div', { class: 'disclosure-body' });
  const paused = h('input', { type: 'checkbox' });
  const pauseLabel = h('label', { class: 'check-row' }, paused, h('span', { text: '暂停这个账号和群的学习（保留已有积累）' }));
  const budget = h('input', { type: 'number', min: 2, max: 100, step: 1, 'aria-label': '每小时学习阶段预算' });
  const saveBudget = h('button', { class: 'quiet-button compact', type: 'button', text: '保存预算' });
  const technical = h('pre', { class: 'mcp-mono' });
  advancedBody.append(pauseLabel, h('div', { class: 'mcp-learning-tools' }, h('label', {}, '每小时最多学习阶段', budget), saveBudget),
    h('p', { class: 'mcp-muted', text: '通过检查的表达默认可用。提取与自检分次调用，但模型可能记得前文，并非独立核验；不保证每条语义判断正确。' }), technical);
  advanced.append(advancedBody);
  const body = h('div', {},
    h('p', { class: 'mcp-muted', text: `机器人 QQ ${group.account} · 群号 ${group.group}` }),
    summary, error, advanced, records);
  panel = openDialog({
    title: group.name + ' · 学习记录', subtitle: '按账号和群号保存；停止会话不会清空积累', body, wide: true,
    onClose: () => clearInterval(state.learningTimer),
  });

  async function change(changeBody) {
    if (changing) return;
    changing = true;
    error.textContent = '';
    try {
      await api('/api/mcp/chat-library', { method: 'POST', body: { conversation_id: group.conversation_id, change: changeBody } });
      await load(true);
      await refreshChats();
    } catch (err) { error.textContent = errorMessage(err, '学习设置未能保存，可稍后重试。'); }
    finally { changing = false; }
  }
  paused.addEventListener('change', () => void change({ enabled: !paused.checked }));
  saveBudget.addEventListener('click', () => void change({ hourly_calls: Number(budget.value) }));

  async function load(force = false) {
    if (loading || !panel?.dialog?.open) return;
    loading = true;
    try {
      const data = await api(query, { timeout: 16000 });
      const library = data.library || {};
      summary.textContent = `表达：${learningThreshold(library)}。${library.pending_expressions ? `另有 ${library.pending_expressions} 条旧记录保留待复核，暂不用于聊天。` : ''}黑话：已掌握 ${library.known_jargon}，待观察 ${library.observing_jargon}。最近完成学习：${library.last_completed_at ? fmtFullTime(library.last_completed_at) : '尚无'}。`;
      if (document.activeElement !== paused) paused.checked = !data.enabled;
      if (document.activeElement !== budget) budget.value = data.hourly_call_budget || 12;
      technical.textContent = JSON.stringify({
        independence: data.independence, run: data.run, calls_last_hour: data.calls_last_hour,
        hourly_call_budget: data.hourly_call_budget, last_failure: data.last_failure,
      }, null, 2);
      error.textContent = '';
      if (!force && records.contains(document.activeElement)) return;
      records.replaceChildren();
      for (const [kind, title] of [['expressions', '表达方式'], ['jargon', '群内用语']]) {
        const section = h('section', {}, h('h3', { text: title }));
        if ((kind === 'expressions' ? data.expression_count : data.jargon_count) > 30) {
          section.append(h('p', { class: 'mcp-muted', text: '显示最近更新的 30 条记录。完整库仍保留，数量统计包含全部条目。' }));
        }
        if (!(data[kind] || []).length) section.append(h('p', { class: 'mcp-muted', text: '还没有记录，聊天中会逐步积累。' }));
        for (const item of data[kind] || []) {
          const line = h('article', { class: 'mcp-learning-record' });
          if (kind === 'expressions') {
            line.append(h('h4', { text: item.situation || '表达场景' }), h('p', { text: item.style || '' }),
              h('small', { text: !item.enabled ? '已停用' : item.evidence_version === 2 ? '群友表达 · 已通过自检' : '旧记录待复核 · 暂不用于聊天' }));
            if (item.evidence_version === 2 && item.surface_form) line.append(h('p', { class: 'mcp-muted', text: '可观察形式：' + item.surface_form }));
          } else {
            line.append(h('h4', { text: item.term }), h('p', { text: item.meaning || '还需观察用法，暂不作为确定释义' }),
              h('small', { text: !item.enabled ? '已停用' : item.manual ? '人工释义' : item.is_jargon ? '已掌握' : '待观察' }));
          }
          line.append(h('button', {
            class: 'quiet-button compact', type: 'button', text: item.enabled ? '停用此条' : '启用此条',
            onclick: () => void change({ kind, id: item.id, enabled: !item.enabled }),
          }));
          const info = h('details', {}, h('summary', { text: '检查详情' }),
            h('p', { text: `有效批次命中 ${item.count} · ${item.independence === 'manual' ? '人工确认' : '顺序自检，非独立核验'}` }));
          if (kind === 'jargon') {
            info.append(h('p', { class: 'mcp-muted', text: item.last_inference_count ? `最近释义检查：第 ${item.last_inference_count} 次命中。` : '尚未进行释义检查，首次需要 4 个有效批次命中。' }));
            const meaning = h('textarea', { maxLength: 600, 'aria-label': item.term + ' 的人工释义', value: item.meaning || '' });
            info.append(meaning, h('button', {
              class: 'quiet-button compact', type: 'button', text: '保存人工释义',
              onclick: () => void change({ kind, id: item.id, meaning: meaning.value }),
            }));
          }
          line.append(info);
          section.append(line);
        }
        records.append(section);
      }
    } catch (err) {
      error.textContent = errorMessage(err, '学习记录读取失败，将自动重试。');
    } finally { loading = false; }
  }

  await load();
  clearInterval(state.learningTimer);
  state.learningTimer = setInterval(() => { if (!document.hidden && panel?.dialog?.open && !changing) void load(); }, 3000);
}

/* ---------- operations ---------- */
async function loadOperations() {
  try {
    const data = await api('/api/mcp/operations', { timeout: 14000 });
    state.operations = data.operations || [];
    if (ui) {
      ui.operations.replaceChildren();
      if (!state.operations.length) ui.operations.append(h('p', { class: 'mcp-muted', text: '暂无发送或群管理操作记录。' }));
      for (const row of state.operations) {
        ui.operations.append(h('div', { class: 'mcp-operation' },
          h('strong', { text: `${row.connection_name || '连接'} · ${({ SUCCEEDED: '已完成', FAILED: '失败', UNKNOWN: '结果未知', EXECUTING: '执行中' })[row.state] || row.state}` }),
          h('pre', { text: row.summary || '' }),
          h('p', { text: `${fmtFullTime(row.created)} · ${row.result?.note || ''}` })));
      }
    }
  } catch (error) {
    if (ui) ui.operations.append(h('p', { class: 'mcp-warning', text: errorMessage(error, '操作记录读取失败。') }));
  }
}

/* ---------- page ---------- */
function buildPage() {
  const serviceCard = h('div', { class: 'mcp-service-card' });
  const serviceState = h('span', { class: 'service-state' });
  const serviceTitle = h('strong');
  const serviceDetail = h('p');
  const serviceToggle = h('button', { class: 'quiet-button compact', type: 'button', text: '开启服务' });
  const servicePort = h('input', { type: 'number', min: 1024, max: 65535, value: 18777, 'aria-label': 'MCP 服务端口' });
  const serviceEnabled = h('input', { type: 'checkbox' });
  const saveServiceButton = h('button', { class: 'quiet-button compact', type: 'button', text: '应用高级设置' });
  const dataStatus = h('p', { class: 'mcp-muted' });
  const onebotStatus = h('p', { class: 'mcp-muted' });
  const connections = h('div', {});
  const audit = h('div', {});
  serviceCard.append(serviceState, h('div', { class: 'grow' }, serviceTitle, serviceDetail),
    h('div', { class: 'mcp-service-actions' }, serviceToggle));
  serviceToggle.addEventListener('click', () => void saveService(!state.status?.running, Number(servicePort.value)));
  saveServiceButton.addEventListener('click', () => void saveService(serviceEnabled.checked, Number(servicePort.value)));
  const servicePanel = h('section', { class: 'mcp-section', dataset: { section: 'service' } },
    h('div', { class: 'mcp-section-head' }, h('h2', { text: '本机 MCP 服务' }), h('span', { class: 'spacer' }),
      h('button', { class: 'quiet-button compact', type: 'button', onclick: () => void refreshService().catch(error => toast(errorMessage(error), 'bad')) }, icon('refresh'), '刷新')),
    serviceCard,
    h('details', { class: 'data-details' },
      h('summary', { text: '高级服务设置' }),
      h('p', { class: 'field-hint', text: '通常无需修改。停用服务会断开外部 Agent；修改端口后需要同步修改客户端连接地址。' }),
      h('div', { class: 'mcp-service-settings' },
        h('label', {}, serviceEnabled, h('span', { text: '启用本机 MCP 服务' })),
        h('label', {}, '端口', servicePort), saveServiceButton)),
    h('div', { class: 'section' },
      h('div', { class: 'section-head' }, h('h2', { text: '资料与连接前提' })),
      h('div', { class: 'card pad' }, h('p', { class: 'mcp-muted', text: '' }), dataStatus, onebotStatus)),
    h('div', { class: 'section' },
      h('div', { class: 'section-head' }, h('h2', { text: '已授权连接' }),
        h('div', { class: 'section-head-actions' }, h('button', { class: 'quiet-button compact', type: 'button', text: '新建连接', onclick: () => activate('create') }))),
      connections));

  const createPanel = buildCreatePanel();
  const secret = h('div', { class: 'mcp-secret', hidden: true },
    h('h3', { text: '连接凭据（只显示这一次）' }),
    h('p', { text: 'Token 只显示这一次。关闭或刷新后无法找回；丢失时撤销并重新创建。' }),
    h('label', {}, '本机地址', h('input', { type: 'text', readonly: true, id: 'mcp-url' })),
    h('label', {}, '访问 Token', h('input', { type: 'password', readonly: true, autocomplete: 'off', id: 'mcp-token' })),
    h('div', { class: 'mcp-secret-actions' },
      h('button', { class: 'quiet-button compact', type: 'button', text: '显示 / 隐藏 Token', onclick: () => { const input = $('#mcp-token'); input.type = input.type === 'password' ? 'text' : 'password'; } }),
      h('button', { class: 'quiet-button compact', type: 'button', text: '检测连接', id: 'mcp-test' }),
      h('button', { class: 'quiet-button compact', type: 'button', text: '写入本机 Codex 配置', id: 'mcp-install-codex' }),
      h('button', { class: 'quiet-button compact', type: 'button', text: '复制 Codex 配置', id: 'mcp-copy' })),
    h('label', { class: 'field', style: { marginTop: '8px' } }, h('span', { text: 'Codex 中的 MCP 名称' }),
      h('input', { type: 'text', maxLength: 64, pattern: '[A-Za-z0-9_-]{1,64}', value: 'tulpa', id: 'mcp-server-name', autocomplete: 'off', spellcheck: 'false' })),
    h('p', { class: 'field-hint', text: '支持英文字母、数字、下划线和短横线。已有同名连接时不会覆盖。' }),
    h('textarea', { readonly: true, rows: 6, id: 'mcp-config', 'aria-label': 'Codex MCP 连接配置' }),
    h('p', { class: 'status-line', id: 'mcp-test-result', role: 'status' }),
    h('p', { class: 'status-line', id: 'mcp-install-result', role: 'status' }));
  const createSection = h('section', { class: 'mcp-section', dataset: { section: 'create' } },
    h('div', { class: 'mcp-section-head' }, h('h2', { text: '新建外部 Agent 授权' }),
      h('p', { class: 'mcp-muted', text: '仅为当前 Tulpa 的本地资料授权；外部 Agent 读到的内容可能发给它自己的模型服务。' })),
    h('div', { class: 'card pad' }, createPanel.element, secret));

  const chatSummary = h('p', { class: 'mcp-muted', role: 'status' });
  const chatEvent = h('p', { class: 'mcp-muted' });
  const chatStatus = h('p', { class: 'status-line', role: 'status' });
  const chatSessions = h('div', {});
  const stopAll = h('button', {
    class: 'quiet-button compact danger-text', type: 'button', text: '停止全部群聊', disabled: true,
    onclick: async () => {
      try { await api('/api/mcp/chats/all/stop', { method: 'POST', body: {} }); await refreshChats(); }
      catch (error) { toast(errorMessage(error), 'bad'); }
    },
  });
  const chatsSection = h('section', { class: 'mcp-section', dataset: { section: 'chats' } },
    h('div', { class: 'mcp-section-head' }, h('div', {}, h('h2', { text: '群聊与学习' }), chatSummary),
      h('span', { class: 'spacer' }),
      h('div', { class: 'mcp-actions' },
        h('button', { class: 'quiet-button compact', type: 'button', text: '刷新', onclick: () => void refreshChats() }),
        stopAll)),
    h('div', { class: 'card pad' },
      h('p', { class: 'mcp-muted', text: '请在已连接的 Agent 对话中发起持续聊天，例如“用小鲸鱼2号在某群持续聊天”。这里查看状态、停止会话和管理学习记录。保持 QQ、OneBot 事件服务和外部 Agent 运行。' }),
      chatEvent, chatStatus, chatSessions));

  const operations = h('div', {});
  const operationsSection = h('section', { class: 'mcp-section', dataset: { section: 'operations' } },
    h('div', { class: 'mcp-section-head' }, h('div', {}, h('h2', { text: '操作记录' }), h('p', { class: 'mcp-muted', text: '发送和群管理回执；结果未知时先到 QQ 核对，不自动重试。' })),
      h('span', { class: 'spacer' }),
      h('button', { class: 'quiet-button compact', type: 'button', text: '刷新', onclick: () => void loadOperations() })),
    h('div', { class: 'card pad' }, operations),
    h('div', { class: 'section' },
      h('div', { class: 'section-head' }, h('h2', { text: '最近访问记录' })),
      h('p', { class: 'section-note', text: '只记录工具、耗时和读取数量，不记录查询词、正文或 Token。' }),
      h('div', { class: 'card pad' }, audit)));

  const tabs = h('div', { class: 'pill-tabs', role: 'tablist', 'aria-label': '外部 Agent 视图' },
    TABS.map(([id, label]) => h('button', { type: 'button', role: 'tab', dataset: { section: id }, text: label })));
  tabs.querySelectorAll('button').forEach(button => button.addEventListener('click', () => activate(button.dataset.section)));
  const head = h('header', { class: 'page-head' },
    h('div', { class: 'page-head-copy' },
      h('h2', { text: '外部 Agent / MCP' }),
      h('p', { text: '让 Codex、DeepSeek Harness 等 MCP 客户端使用你明确授权的本地资料与 QQ 操作。' })));
  const page = h('div', { class: 'view-scroll' }, h('div', { class: 'page mcp-page' }, head, tabs,
    servicePanel, createSection, chatsSection, operationsSection));
  ui = { page, tabs, serviceCard, serviceState, serviceTitle, serviceDetail, serviceToggle, servicePort, serviceEnabled,
    dataStatus, onebotStatus, connections, audit, secret, secretUrl: null, secretToken: null, testResult: null,
    installResult: null, codex: null, copyConfig: null, install: null, serverName: null, test: null,
    chatSummary, chatEvent, chatStatus, chatSessions, stopAll, operations, createStatus: createPanel.element.querySelector('.mcp-create-status') };
  // Wire dynamic secret controls before the page is attached to the document.
  ui.secretUrl = secret.querySelector('#mcp-url');
  ui.secretToken = secret.querySelector('#mcp-token');
  ui.testResult = secret.querySelector('#mcp-test-result');
  ui.installResult = secret.querySelector('#mcp-install-result');
  ui.codex = secret.querySelector('#mcp-config');
  ui.serverName = secret.querySelector('#mcp-server-name');
  ui.test = secret.querySelector('#mcp-test');
  ui.install = secret.querySelector('#mcp-install-codex');
  ui.copyConfig = secret.querySelector('#mcp-copy');
  ui.test.addEventListener('click', () => void testConnection());
  ui.install.addEventListener('click', () => void installCodex());
  ui.copyConfig.addEventListener('click', async () => {
    if (!codexConfig()) return;
    try { await copyText(ui.codex.value); ui.copyConfig.textContent = '已复制'; toast('Codex 配置已复制。', 'success'); }
    catch { toast('复制失败，可以选中配置手动复制。', 'warn'); }
  });
  ui.serverName.addEventListener('input', codexConfig);
  return page;
}

function activate(tab) {
  if (!ui) return;
  state.tab = ['service', 'create', 'chats', 'operations'].includes(tab) ? tab : 'service';
  ui.tabs.querySelectorAll('button').forEach(button => button.setAttribute('aria-selected', String(button.dataset.section === state.tab)));
  ui.page.querySelectorAll('.mcp-section').forEach(section => { section.hidden = section.dataset.section !== state.tab; });
  if (state.tab === 'chats') void refreshChats();
  if (state.tab === 'operations') void loadOperations();
  if (state.tab === 'service' && !state.status) void refreshService().catch(() => {});
}

registerView('agents', {
  title: '外部 Agent / MCP',
  subtitle: '服务、授权、群聊学习与操作记录',
  async mount(root, params, query) {
    state.mounted = true;
    state.credential = null;
    state.selected = new Map();
    state.liveGroups = null;
    state.status = null;
    state.chats = null;
    const shell = h('div', { class: 'view-scroll' }, loadingState('正在读取 MCP 设置…'));
    root.append(shell);
    try {
      const [permissions, status] = await Promise.all([
        api('/api/mcp/permissions', { timeout: 12000 }),
        api('/api/mcp', { timeout: 16000 }),
      ]);
      state.permissions = permissions.items || [];
      state.status = status;
      state.connections = status.connections || [];
      state.recent = status.recent || [];
      const page = buildPage();
      shell.replaceWith(page);
      renderService();
      activate(query.tab || 'service');
      const offs = [
        on('job-done', () => { if (state.tab === 'chats') void refreshChats(); }),
      ];
      state.chatTimer = setInterval(() => {
        if (document.hidden || !state.mounted) return;
        if (state.tab === 'chats') void refreshChats();
      }, 3000);
      const serviceTimer = setInterval(() => {
        if (document.hidden || !state.mounted) return;
        if (state.tab === 'service') void refreshService().catch(() => {});
      }, 15000);
      return () => {
        state.mounted = false;
        offs.forEach(off => off());
        clearInterval(state.chatTimer);
        clearInterval(state.learningTimer);
        clearInterval(serviceTimer);
        state.credential = null;
        ui = null;
      };
    } catch (error) {
      shell.replaceChildren(h('div', { class: 'page' },
        h('div', { class: 'card', style: { maxWidth: '620px', margin: '8vh auto' } },
          emptyState('alert', 'MCP 设置暂时无法读取', errorMessage(error, '请确认本机服务仍在运行。'), null))));
      return () => { state.mounted = false; ui = null; };
    }
  },
});
