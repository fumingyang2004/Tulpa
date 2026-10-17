'use strict';

/* SnowLuma hosting: local SnowLuma (Windows/Linux) and Docker, with inspect,
   consent, start, QQ process selection, login and recovery. */
import { h, icon, api, errorMessage, copyText } from './app.js';

function isSafeVnc(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'http:' && url.hostname === '127.0.0.1' && !url.username && !url.password;
  } catch { return false; }
}

function hasNativeUI() {
  return typeof window !== 'undefined' && window.TulpaDesktopUI && typeof window.TulpaDesktopUI.call === 'function';
}

async function managedRequest(action = '', body) {
  const path = '/api/desktop/snowluma/managed' + (action ? '/' + action : '');
  const response = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    cache: 'no-store',
    credentials: 'same-origin',
    headers: {
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...(body !== undefined ? { 'X-ChatWeave-UI': '1' } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(60000),
  });
  let data = null;
  try { data = await response.json(); } catch { data = null; }
  if (!response.ok || data?.ok === false) {
    const error = new Error(data?.message || data?.detail || 'SnowLuma 托管操作未完成。');
    error.code = data?.code;
    throw error;
  }
  return data;
}

const DOCKER_DESCRIPTION = 'SnowLuma 通过 Docker（SL Docker）运行。先启动 Colima 或 Docker Desktop，再检测运行环境；首次连接会下载固定版本镜像，并在系统浏览器扫码登录 QQ。';
const LOCAL_DESCRIPTION = '选择已下载并解压的 SnowLuma 本机完整版文件夹。Tulpa 只在该目录内备份并写入配置，不会下载或复制运行包；请在本机 QQ 窗口扫码登录。';
const DOCKER_CONSENT = '我已阅读并同意 SnowLuma 协议及上述 Docker 托管操作';
const LOCAL_CONSENT = '我已阅读并同意 SnowLuma 协议及上述本机配置与托管操作';

export function mountSnowLuma(host, { onSaved = async () => {}, compact = false } = {}) {
  let state = {};
  let preview = null;
  let mode = null;
  let consentAccepted = false;
  let busy = false;
  let disposed = false;
  let ready = false;
  let loginInfo = null;
  let selectedChoice = '';
  let choiceKey = '';
  let actionRevision = 0;
  let loginRevision = 0;

  const description = h('p', { class: 'field-hint' });
  const headingLabel = h('span', { text: ' 连接 QQ · SL 托管' });
  const modeLocal = h('button', { type: 'button', onclick: () => setMode('local') }, '本机 SnowLuma');
  const modeDocker = h('button', { type: 'button', onclick: () => setMode('docker') }, 'Docker（SL Docker）');
  const modeRow = h('div', { class: 'row wrap', style: { margin: '7px 0' } },
    h('span', { class: 'field-hint', text: '托管方式' }),
    h('div', { class: 'segmented', 'aria-label': 'SnowLuma 托管方式' }, modeLocal, modeDocker));
  const modeNote = h('p', { class: 'field-hint snow-mode-note' });
  const folder = h('input', {
    type: 'text', maxLength: 2048, autocomplete: 'off', spellcheck: 'false',
    placeholder: '粘贴已解压的 SnowLuma 文件夹路径', 'aria-label': 'SnowLuma 文件夹',
    style: { flex: '1', minWidth: '180px' },
  });
  const folderRow = h('div', { class: 'row wrap', style: { margin: '6px 0' } },
    h('span', { class: 'field-hint', text: 'SnowLuma 文件夹' }), folder);
  const inspectLabel = h('span', { text: '检测文件夹' });
  const inspect = h('button', { class: 'primary-button compact', type: 'button' }, icon('search'), inspectLabel);
  const status = h('p', { class: 'status-line', role: 'status' });
  const feedback = h('p', { class: 'status-line snow-feedback', role: 'status', 'aria-live': 'polite', hidden: true });
  const phaseLine = h('p', { class: 'field-hint snow-phase' });
  const previewBox = h('div', { class: 'snow-preview', hidden: true });
  const previewLine = h('p');
  const agreements = h('div', { class: 'agreement-list' });
  const previewNote = h('p', { class: 'field-hint' });
  const consentInput = h('input', { type: 'checkbox' });
  const consentText = h('span', { text: DOCKER_CONSENT });
  const consent = h('label', { class: 'check-row' }, consentInput, consentText);
  const start = h('button', { class: 'primary-button compact', type: 'button', text: '同意并开始托管' });
  const login = h('button', { class: 'quiet-button compact', type: 'button', hidden: true }, icon('external'), '打开扫码登录');
  const retry = h('button', { class: 'quiet-button compact', type: 'button', hidden: true, text: '重试' });
  const cancel = h('button', { class: 'quiet-button compact', type: 'button', hidden: true, text: '停止托管' });
  const revoke = h('button', { class: 'quiet-button compact danger-text', type: 'button', hidden: true, text: '撤销授权' });
  const choiceSelect = h('select', { 'aria-label': '选择要绑定的 QQ 进程', style: { flex: '1', minWidth: '180px' } });
  const choose = h('button', { class: 'quiet-button compact', type: 'button', text: '连接所选 QQ' });
  const choiceRow = h('div', { class: 'row wrap', hidden: true, style: { margin: '6px 0' } },
    h('span', { class: 'field-hint', text: 'QQ 进程' }), choiceSelect, choose);
  const loginBox = h('div', { class: 'snow-login', hidden: true });
  const address = h('input', {
    type: 'text', readonly: true, autocomplete: 'off', spellcheck: 'false',
    'aria-label': 'QQ 桌面登录地址', placeholder: '登录地址',
  });
  const copyAddress = h('button', { class: 'quiet-button compact', type: 'button', text: '复制地址' });
  const vncLink = h('a', { class: 'primary-button compact', target: '_blank', rel: 'noopener noreferrer', hidden: true, text: '在浏览器中打开 QQ 桌面' });
  const nativeOpen = h('button', { class: 'primary-button compact', type: 'button', hidden: true }, icon('external'), '在浏览器中打开 QQ 桌面');
  const password = h('input', { type: 'password', readonly: true, autocomplete: 'off', 'aria-label': 'VNC 密码' });
  const showPassword = h('button', { class: 'quiet-button compact', type: 'button', text: '显示密码' });
  const clearLogin = h('button', { class: 'quiet-button compact', type: 'button', text: '隐藏登录信息' });
  const details = h('details', { class: 'disclosure' }, h('summary', { text: '托管状态与安全信息' }));
  const detailsBody = h('div', { class: 'disclosure-body' });

  previewBox.append(previewLine, agreements, previewNote, consent, start);
  loginBox.append(
    h('div', { class: 'row wrap' }, h('span', { class: 'field-hint', text: '登录地址' }), address, copyAddress),
    h('div', { class: 'row wrap', style: { marginTop: '6px' } }, nativeOpen, vncLink),
    h('p', { class: 'field-hint', text: '请在本机浏览器中打开登录地址，输入下方 VNC 密码后扫码登录 QQ。' }),
    h('div', { class: 'row wrap', style: { marginTop: '6px' } }, h('span', { class: 'field-hint', text: 'VNC 密码' }), password, showPassword, clearLogin));
  details.append(detailsBody);
  host.append(
    h('div', { class: 'snowluma-box' },
      h('h4', {}, icon('database'), headingLabel),
      description,
      modeRow, modeNote, folderRow,
      h('div', { class: 'snowluma-actions' }, inspect, login, retry, cancel, revoke),
      choiceRow, phaseLine, status, feedback, previewBox, loginBox, details));

  consentInput.addEventListener('change', () => {
    consentAccepted = Boolean(consentInput.checked);
    setFeedback('');
    render();
  });
  folder.addEventListener('input', () => {
    // A different folder invalidates the inspect receipt and prior consent;
    // an old receipt must never start a newly typed path.
    if (preview && String(preview.folder || '') !== folder.value.trim()) {
      discardPreview();
      setFeedback('文件夹已变更，请重新检测并勾选授权。', 'warn');
    }
    render();
  });
  inspect.addEventListener('click', () => void act('inspect'));
  start.addEventListener('click', () => void act('start'));
  login.addEventListener('click', () => void act('login'));
  nativeOpen.addEventListener('click', () => void act('open-login'));
  retry.addEventListener('click', () => void act('retry'));
  cancel.addEventListener('click', () => void act('cancel'));
  revoke.addEventListener('click', () => void act('revoke'));
  choose.addEventListener('click', () => {
    if (selectedChoice) void act('select');
  });
  choiceSelect.addEventListener('change', () => {
    selectedChoice = choiceSelect.value;
    render();
  });
  showPassword.addEventListener('click', () => {
    password.type = password.type === 'password' ? 'text' : 'password';
    showPassword.textContent = password.type === 'password' ? '显示密码' : '隐藏密码';
  });
  address.addEventListener('click', () => address.select());
  copyAddress.addEventListener('click', async () => {
    const value = loginInfo?.novnc_url;
    if (!isSafeVnc(value)) {
      setFeedback('登录地址不可用，请重新打开扫码登录。', 'warn');
      return;
    }
    const revision = loginRevision;
    try {
      await copyText(value);
      if (!disposed && revision === loginRevision) setFeedback('已复制登录地址。', 'success');
    } catch {
      if (disposed || revision !== loginRevision) return;
      address.focus();
      address.select();
      setFeedback('自动复制失败，请手动选中地址复制。', 'warn');
    }
  });
  clearLogin.addEventListener('click', () => clearLoginInfo());

  function supportedBackends() {
    const list = Array.isArray(state.supported_backends)
      ? state.supported_backends.filter(value => value === 'local' || value === 'docker')
      : [];
    if (list.length) return list;
    if (state.backend === 'local') return ['local'];
    if (state.backend === 'docker') return ['docker'];
    // Only used before the first status response: avoid briefly suggesting
    // Docker on Windows (or hiding the SL Docker entry on macOS).
    const agent = typeof navigator !== 'undefined' ? navigator.userAgent || '' : '';
    if (/Windows/i.test(agent)) return ['local'];
    if (/Macintosh|Mac OS X/i.test(agent)) return ['docker'];
    return ['local', 'docker'];
  }

  function hasInstallation() {
    return Boolean(state.folder || state.image || state.container_id || state.container_name);
  }

  function installationBound() {
    return Boolean(state.enabled || state.has_consent || hasInstallation());
  }

  function activeConsent() {
    return Boolean(state.enabled || state.has_consent);
  }

  function resolveMode(value) {
    const supported = supportedBackends();
    const backend = value?.backend === 'docker' ? 'docker' : value?.backend === 'local' ? 'local' : '';
    const bound = Boolean(value?.enabled || value?.has_consent || value?.folder || value?.image
      || value?.container_id || value?.container_name);
    if (bound && backend) return backend;
    if (mode && supported.includes(mode)) return mode;
    return supported[0] || backend || 'local';
  }

  function expectedPreviewMode() {
    return mode === 'docker' ? 'docker' : 'local';
  }

  function setMode(value) {
    if (busy || disposed || value === mode || installationBound()) return;
    const supported = supportedBackends();
    if (!supported.includes(value)) return;
    mode = value;
    discardPreview();
    clearLoginInfo({ quiet: true });
    setFeedback(value === 'docker'
      ? '已切换到 Docker（SL Docker）托管，请重新检测运行环境。'
      : '已切换到本机 SnowLuma 托管，请重新选择文件夹并检测。');
    render();
  }

  function discardPreview() {
    preview = null;
    consentAccepted = false;
    consentInput.checked = false;
    previewBox.hidden = true;
  }

  function setFeedback(message, tone = '') {
    if (disposed) return;
    feedback.textContent = message || '';
    feedback.className = 'status-line snow-feedback' + (tone ? ' ' + tone : '');
    feedback.hidden = !message;
  }

  function updateLoginControls() {
    const native = hasNativeUI();
    const docker = mode === 'docker';
    const available = Boolean(docker && loginInfo && isSafeVnc(loginInfo.novnc_url));
    nativeOpen.hidden = !(native && available);
    vncLink.hidden = !(!native && available);
    nativeOpen.disabled = busy;
    copyAddress.disabled = busy || !available;
  }

  function clearLoginInfo({ quiet = false } = {}) {
    loginRevision += 1;
    loginInfo = null;
    address.value = '';
    password.value = '';
    password.type = 'password';
    showPassword.textContent = '显示密码';
    vncLink.removeAttribute('href');
    nativeOpen.hidden = true;
    vncLink.hidden = true;
    copyAddress.disabled = true;
    loginBox.hidden = true;
    if (!quiet) setFeedback('已隐藏登录地址与密码。');
  }

  function showLoginInfo(value) {
    if (mode !== 'docker' || !isSafeVnc(value?.novnc_url)) return;
    loginInfo = value;
    address.value = value.novnc_url;
    password.value = value.password || '';
    password.type = 'password';
    showPassword.textContent = '显示密码';
    if (hasNativeUI()) vncLink.removeAttribute('href');
    else vncLink.href = value.novnc_url;
    loginBox.hidden = false;
    updateLoginControls();
  }

  function reportLoginResult(value) {
    if (!hasNativeUI()) {
      setFeedback('登录信息已准备好。请点击“在浏览器中打开 QQ 桌面”或复制登录地址，在本机浏览器中完成扫码。', 'success');
      return;
    }
    if (value.browser_opened === true) {
      setFeedback('已通过系统浏览器打开 QQ 桌面。请在浏览器中输入下方 VNC 密码并扫码登录。', 'success');
    } else if (value.browser_opened === false) {
      const reason = typeof value.browser_error === 'string' ? value.browser_error.trim().slice(0, 200) : '';
      setFeedback('未能自动打开系统浏览器' + (reason ? '：' + reason : '') + '。请复制下方登录地址，在浏览器中手动打开并输入 VNC 密码扫码。', 'warn');
    } else {
      setFeedback('登录信息已准备好。请复制下方登录地址，在本机浏览器中打开并输入 VNC 密码扫码。', 'warn');
    }
  }

  function render(message = '') {
    if (disposed) return;
    if (mode === null) mode = resolveMode(state);
    const supported = supportedBackends();
    const bound = installationBound();
    const locked = busy || bound;
    const expected = expectedPreviewMode();
    if (mode === 'local' && !folder.value && state.folder) folder.value = state.folder;
    modeLocal.hidden = !supported.includes('local');
    modeDocker.hidden = !supported.includes('docker');
    modeLocal.disabled = locked || !supported.includes('local');
    modeDocker.disabled = locked || !supported.includes('docker');
    modeLocal.setAttribute('aria-pressed', mode === 'local' ? 'true' : 'false');
    modeDocker.setAttribute('aria-pressed', mode === 'docker' ? 'true' : 'false');
    if (state.enabled || state.has_consent) {
      modeNote.textContent = mode === 'docker'
        ? '已绑定 SL Docker 托管；为避免混绑实例，不能在此切换托管方式。'
        : '已绑定本机 SnowLuma 托管；为避免混绑实例，不能在此切换托管方式。';
    } else if (hasInstallation()) {
      modeNote.textContent = mode === 'docker'
        ? '已保留 SL Docker 托管绑定。重新检测环境后可再次主动授权；更换托管方式请使用另一份 Tulpa。'
        : '已保留本机 SnowLuma 托管绑定。重新检测同一文件夹后可再次主动授权；更换方式或目录请使用另一份 Tulpa。';
    } else if (supported.length === 1 && supported[0] === 'docker') {
      modeNote.textContent = 'macOS 使用 Docker（SL Docker）托管；本机 SnowLuma 运行包仅适用于 Windows/Linux。';
    } else if (supported.length === 1) {
      modeNote.textContent = '当前平台使用本机 SnowLuma 托管，无需 Docker 或 Colima。';
    } else {
      modeNote.textContent = 'Linux 可选择本机或 Docker；开始托管后会绑定所选方式，不能静默换绑。';
    }
    description.textContent = mode === 'docker' ? DOCKER_DESCRIPTION : LOCAL_DESCRIPTION;
    headingLabel.textContent = mode === 'docker' ? ' 连接 QQ · SL Docker 托管' : ' 连接 QQ · SL 本机托管';
    folderRow.hidden = mode !== 'local';
    folder.disabled = busy || activeConsent() || hasInstallation();
    inspectLabel.textContent = mode === 'docker' ? '检测 Docker' : '检测文件夹';
    inspect.disabled = busy || activeConsent()
      || (mode === 'local' && !folder.value.trim());
    consentText.textContent = expected === 'docker' ? DOCKER_CONSENT : LOCAL_CONSENT;
    consentInput.checked = consentAccepted;
    consentInput.disabled = busy || !preview || preview.mode !== expected;
    login.hidden = mode !== 'docker' || !state.login_available;
    login.disabled = busy;
    choiceRow.hidden = !(mode === 'local' && state.phase === 'choosing' && state.selection_required
      && Array.isArray(state.choices) && state.choices.length);
    if (!choiceRow.hidden) {
      const choices = state.choices || [];
      const nextKey = JSON.stringify(choices.map(row => [row.id, row.account_label, row.pid, row.path]));
      if (nextKey !== choiceKey) {
        choiceKey = nextKey;
        choiceSelect.replaceChildren(new Option('请选择 QQ 进程', ''));
        for (const row of choices) {
          choiceSelect.add(new Option(
            (row.account_label || '账号待验证') + ' · PID ' + row.pid + ' · ' + row.path,
            row.id,
          ));
        }
      }
      if (!choices.some(row => row.id === selectedChoice)) selectedChoice = '';
      choiceSelect.value = selectedChoice;
      choiceSelect.disabled = busy;
      choose.disabled = busy || !selectedChoice;
    }
    retry.hidden = !state.consent_current || !['error', 'cancelled', 'stopped'].includes(state.phase);
    retry.disabled = busy;
    cancel.hidden = !state.enabled || ['ready', 'error', 'revoked', 'stopped'].includes(state.phase);
    cancel.disabled = busy;
    revoke.hidden = !(state.has_consent ?? state.consent_current);
    revoke.disabled = busy;
    start.disabled = busy || !preview || preview.mode !== expected || !consentAccepted;
    start.textContent = state.enabled && !state.qq_ready
      ? '托管进行中…'
      : expected === 'docker' ? '同意并开始 Docker 托管' : '同意并开始本机托管';
    const statusText = message || [state.phase_label, state.account ? 'QQ ' + state.account : '', state.message]
      .filter(Boolean).join(' · ');
    status.textContent = statusText;
    status.className = 'status-line ' + (state.code || /未|无法/.test(statusText) ? 'warn' : state.qq_ready ? 'success' : '');
    if (!state.enabled) phaseLine.textContent = '尚未启用 SL 托管';
    else if (mode === 'docker') {
      phaseLine.textContent = `SL Docker 托管中${state.container_name ? ' · ' + state.container_name : ''}${state.version ? ' · SnowLuma ' + state.version : ''}`;
    } else {
      phaseLine.textContent = `SL 本机托管中${state.version ? ' · SnowLuma ' + state.version : ''}${state.folder ? ' · ' + state.folder : ''}`;
    }
    detailsBody.replaceChildren();
    const rows = [
      ['托管方式', mode === 'docker' ? 'Docker 容器' : '本机 SnowLuma'],
      ['SnowLuma 版本', state.version || '—'],
    ];
    if (mode === 'docker') rows.push(['镜像', state.image || '—'], ['平台', state.platform || '—']);
    else rows.push(['SnowLuma 文件夹', state.folder || '—']);
    rows.push(
      ['QQ 账号', state.account || '尚未连接'],
      ['本机存储', state.storage || '—'],
      ['安全', state.security_note || '—'],
    );
    for (const [key, value] of rows) detailsBody.append(h('p', { class: 'field-hint', text: key + '：' + value }));
    updateLoginControls();
  }

  function showPreview(data) {
    preview = data;
    const kind = data.mode === 'docker' ? 'docker' : 'local';
    previewBox.hidden = false;
    previewLine.textContent = kind === 'docker'
      ? [data.image, data.platform, data.engine_version ? 'Docker ' + data.engine_version : ''].filter(Boolean).join(' · ')
      : [data.folder, data.version ? 'SnowLuma ' + data.version : '',
        data.mode === 'existing' ? '检测到已有连接' : '本机文件夹'].filter(Boolean).join(' · ');
    previewNote.textContent = kind === 'docker'
      ? '授权 Tulpa 下载固定版本官方镜像、管理本实例容器和持久卷、创建随机凭据并在本机开放管理、消息及扫码端口。撤销授权会停止容器，保留登录数据。'
      : '授权 Tulpa 在你选择的文件夹内备份现有配置、初始化或沿用本机凭据、仅监听本机，并绑定一个 QQ 进程。不会重置已有安装的管理密码，不会混接其他实例；撤销授权只停止 Tulpa 启动的服务，保留登录数据。';
    agreements.replaceChildren();
    for (const item of data.agreements || state.agreements || []) {
      let url;
      try { url = new URL(item.url); } catch { continue; }
      if (url.origin !== 'https://github.com' || !url.pathname.startsWith('/SnowLuma/SnowLuma/')) continue;
      agreements.append(h('a', { href: url.href, target: '_blank', rel: 'noopener noreferrer', text: item.title + ' · ' + (item.version || '') }));
    }
    consentAccepted = false;
    consentInput.checked = false;
    if (kind === 'docker') {
      setFeedback('Docker 检测完成。请阅读协议并勾选授权。');
    } else if (data.mode === 'existing') {
      setFeedback((data.message || '检测到已有 SnowLuma 连接。')
        + ' 当前自动托管不会重置其管理密码或接管外部服务；请改用已有 OneBot 连接导入。', 'warn');
    } else {
      setFeedback('文件夹已就绪。请阅读协议并主动勾选授权。');
    }
    render();
  }

  async function applyStatus(data) {
    const previous = state;
    state = data || {};
    if (!busy && previous.consent_fingerprint && state.consent_fingerprint
      && previous.consent_fingerprint !== state.consent_fingerprint) {
      discardPreview();
      setFeedback('授权范围已变化，请重新检测并勾选授权。', 'warn');
    }
    const nextMode = resolveMode(state);
    if (nextMode !== mode) {
      mode = nextMode;
      discardPreview();
      clearLoginInfo({ quiet: true });
    }
    if (state.enabled && state.phase !== 'error') discardPreview();
    if (mode !== 'docker') clearLoginInfo({ quiet: true });
    render();
    if (state.qq_ready && !ready) {
      try {
        const desktop = await api('/api/desktop/status', { timeout: 12000 });
        await onSaved(desktop.settings);
      } catch { /* the connection is still ready even if the settings refresh failed */ }
    }
    ready = Boolean(state.qq_ready);
  }

  async function refresh() {
    if (disposed || busy) return;
    try {
      const data = await managedRequest();
      if (!disposed) await applyStatus(data);
    } catch (error) {
      if (!disposed) render('无法连接本机托管服务：' + errorMessage(error));
    }
  }

  async function act(action) {
    if (busy || disposed) return;
    if (mode === null) mode = resolveMode(state);
    busy = true;
    const revision = ++actionRevision;
    let loginRev = null;
    setFeedback('正在处理…');
    render();
    try {
      if (action === 'inspect') {
        const requested = mode;
        const body = requested === 'docker'
          ? {}
          : { folder: folder.value.trim() };
        if (requested === 'local' && !body.folder) throw new Error('请先填写 SnowLuma 文件夹路径。');
        const data = await managedRequest(requested === 'docker' ? 'docker-inspect' : 'inspect', body);
        if (disposed || revision !== actionRevision) return;
        if (requested === 'local' && data.folder) folder.value = data.folder;
        showPreview(data);
      } else if (action === 'start') {
        const requested = mode;
        const expected = requested === 'docker' ? 'docker' : 'local';
        if (!preview || preview.mode !== expected) throw new Error('请先检测当前托管方式并选择对应环境。');
        if (!consentAccepted) throw new Error('请先阅读协议并主动勾选授权。');
        const data = await managedRequest(requested === 'docker' ? 'docker-start' : 'start', {
          accepted: true,
          fingerprint: preview.consent_fingerprint,
          selection_id: preview.selection_id,
        });
        if (disposed || revision !== actionRevision) return;
        discardPreview();
        await applyStatus(data);
        if (!disposed && revision === actionRevision) {
          setFeedback(requested === 'docker'
            ? '已开始 SL Docker 托管，请等待 QQ 就绪。'
            : '已开始 SL 本机托管。请在本机 QQ 窗口完成扫码；Tulpa 不会显示 VNC。', 'success');
        }
      } else if (action === 'login' || action === 'open-login') {
        if (mode !== 'docker') throw new Error('本机 SnowLuma 托管请直接在本机 QQ 窗口扫码。');
        loginRev = ++loginRevision;
        const native = hasNativeUI();
        const value = native
          ? await window.TulpaDesktopUI.call('open_qq_login')
          : await managedRequest('docker-login', {});
        if (disposed || loginRev !== loginRevision) return;
        if (!value || !isSafeVnc(value.novnc_url)) throw new Error('登录地址无效。');
        showLoginInfo(value);
        reportLoginResult(value);
      } else if (action === 'select') {
        if (mode !== 'local' || !selectedChoice) throw new Error('请选择要绑定的 QQ 进程。');
        const data = await managedRequest('select', { choice_id: selectedChoice });
        if (disposed || revision !== actionRevision) return;
        selectedChoice = '';
        await applyStatus(data);
        if (!disposed && revision === actionRevision) setFeedback('已选择 QQ，正在继续接入。', 'success');
      } else {
        const data = await managedRequest(action, {});
        if (disposed || revision !== actionRevision) return;
        if (action === 'revoke' || action === 'cancel') clearLoginInfo({ quiet: true });
        if (action === 'revoke') {
          discardPreview();
          selectedChoice = '';
        }
        await applyStatus(data);
        if (action === 'select') selectedChoice = '';
        if (!disposed && revision === actionRevision) {
          setFeedback({
            retry: '已请求重试，请稍候。',
            cancel: '已停止 SL 托管。',
            revoke: '已撤销授权，托管服务已停止，登录数据已保留。',
          }[action] || '操作已完成。', action === 'cancel' ? '' : 'success');
        }
      }
    } catch (error) {
      const staleLogin = loginRev !== null && loginRev !== loginRevision;
      if (!disposed && revision === actionRevision && !staleLogin) {
        const code = error?.code;
        if (['selection_expired', 'consent_required', 'terms_changed', 'installation_changed',
          'docker_environment_changed', 'existing_installation', 'folder_unreadable'].includes(code)) {
          discardPreview();
        }
        if (['selection_expired', 'process_changed', 'account_mismatch'].includes(code)) selectedChoice = '';
        setFeedback(errorMessage(error, 'SnowLuma 托管操作未完成。'), 'error');
        if (['backend_bound', 'installation_bound', 'selection_expired', 'process_changed', 'account_mismatch'].includes(code)) {
          runRefreshSoon();
        }
      }
    } finally {
      busy = false;
      if (!disposed) render();
    }
  }

  function runRefreshSoon() {
    // Pick up the server-side state after a rejected action without blocking
    // the action's own error feedback.
    setTimeout(() => { if (!disposed && !busy) void refresh(); }, 50);
  }

  const timer = setInterval(() => {
    if (document.hidden || disposed || !host.isConnected || !host.getClientRects().length) return;
    void refresh();
  }, 2000);
  render();
  void refresh();

  return {
    refresh,
    reset() {
      actionRevision += 1;
      clearLoginInfo({ quiet: true });
      setFeedback('');
      discardPreview();
      selectedChoice = '';
      render();
      void refresh();
    },
    destroy() {
      disposed = true;
      clearInterval(timer);
      clearLoginInfo({ quiet: true });
      host.replaceChildren();
    },
  };
}

export default mountSnowLuma;
