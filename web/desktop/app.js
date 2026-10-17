'use strict';

/* Tulpa front-end core: DOM helpers, API, shell, router, dialogs, jobs, theme, home. */

const SVG_NS = 'http://www.w3.org/2000/svg';
export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

export function h(tag, attrs, ...children) {
  const node = document.createElement(tag);
  if (attrs && (typeof attrs !== 'object' || attrs instanceof Node || Array.isArray(attrs))) {
    children.unshift(attrs);
    attrs = null;
  }
  if (attrs) {
    for (const [key, value] of Object.entries(attrs)) {
      if (value === undefined || value === null || value === false) continue;
      if (key === 'class' || key === 'className') node.className = value;
      else if (key === 'text') node.textContent = value;
      else if (key === 'html') node.innerHTML = value;
      else if (key === 'dataset') Object.assign(node.dataset, value);
      else if (key === 'style' && typeof value === 'object') Object.assign(node.style, value);
      else if (key === 'ref' && typeof value === 'function') value(node);
      else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2).toLowerCase(), value);
      else if (['checked', 'value', 'disabled', 'hidden', 'selected', 'readOnly', 'multiple', 'required'].includes(key)) node[key] = value;
      else node.setAttribute(key, value === true ? '' : String(value));
    }
  }
  appendChildren(node, children);
  return node;
}

export function appendChildren(node, children) {
  for (const child of children) {
    if (child === null || child === undefined || child === false || child === true) continue;
    if (Array.isArray(child)) appendChildren(node, child);
    else if (child instanceof Node) node.append(child);
    else node.append(document.createTextNode(String(child)));
  }
  return node;
}

export function clear(node) {
  if (node) node.replaceChildren();
  return node;
}

export function icon(name, cls = '') {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('aria-hidden', 'true');
  if (cls) svg.setAttribute('class', cls);
  const use = document.createElementNS(SVG_NS, 'use');
  use.setAttribute('href', '#i-' + name);
  svg.append(use);
  return svg;
}

export function frag(...children) {
  const f = document.createDocumentFragment();
  appendChildren(f, children);
  return f;
}

export function fmtNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toLocaleString('zh-CN') : '—';
}

export function fmtBytes(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n < 0) return '大小未知';
  if (n < 1024) return n + ' B';
  if (n < 1048576) return (n / 1024).toFixed(1) + ' KiB';
  if (n < 1073741824) return (n / 1048576).toFixed(1) + ' MiB';
  return (n / 1073741824).toFixed(2) + ' GiB';
}

export function parseTime(value) {
  if (value === null || value === undefined || value === '') return null;
  if (value instanceof Date) return value;
  if (typeof value === 'number') return new Date(value < 1e12 ? value * 1000 : value);
  const numeric = Number(value);
  if (Number.isFinite(numeric) && String(value).trim() === String(numeric)) return new Date(numeric < 1e12 ? numeric * 1000 : numeric);
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function fmtTime(value, { seconds = false, zone = '' } = {}) {
  const date = parseTime(value);
  if (!date) return '尚未记录';
  const options = { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false };
  if (seconds) options.second = '2-digit';
  if (zone) options.timeZone = zone;
  try { return new Intl.DateTimeFormat('zh-CN', options).format(date); } catch { return date.toLocaleString('zh-CN'); }
}

export function fmtFullTime(value) {
  const date = parseTime(value);
  if (!date) return '尚未记录';
  return date.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });
}

export function fmtShortDate(value) {
  const date = parseTime(value);
  if (!date) return '—';
  return date.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' });
}

export function relativeTime(value) {
  const date = parseTime(value);
  if (!date) return '尚未记录';
  const delta = (Date.now() - date.getTime()) / 1000;
  if (delta < 60) return '刚刚';
  if (delta < 3600) return Math.floor(delta / 60) + ' 分钟前';
  if (delta < 86400) return Math.floor(delta / 3600) + ' 小时前';
  if (delta < 86400 * 7) return Math.floor(delta / 86400) + ' 天前';
  return fmtShortDate(date);
}

export function debounce(fn, wait = 180) {
  let timer = null;
  const wrapped = (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
  wrapped.cancel = () => clearTimeout(timer);
  return wrapped;
}

export function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

export function titleFromSessionLabel(label) {
  const text = String(label || '');
  const match = /^\d{2}-\d{2}T\d{2}:\d{2}\s*·\s*(.*)$/.exec(text);
  return match ? match[1] : text;
}

export function sessionStamp(label) {
  const match = /^(\d{2}-\d{2})T(\d{2}:\d{2})/.exec(String(label || ''));
  return match ? `${match[1]} ${match[2]}` : '';
}

export function safeText(value, fallback = '') {
  return typeof value === 'string' && value ? value : fallback;
}

export function errorMessage(error, fallback = '操作未完成，请稍后重试。') {
  if (error instanceof ApiError) return error.detail || fallback;
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

/* ---------- API ---------- */
const UI_HEADERS = {
  'X-ChatWeave-UI': '1',
  'X-Reply-UI': '1',
  'X-Tulpa-UI': '1',
  'X-Group-Admin-UI': '1',
};

export class ApiError extends Error {
  constructor(message, status = 0, payload = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.payload = payload;
    this.detail = message;
  }
}

export async function api(path, { method = 'GET', body, headers = {}, timeout = 20000, raw = false } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  const verb = String(method || 'GET').toUpperCase();
  const write = !['GET', 'HEAD', 'OPTIONS'].includes(verb);
  try {
    const response = await fetch(path, {
      method: verb,
      cache: 'no-store',
      credentials: 'same-origin',
      signal: controller.signal,
      headers: {
        // The management middleware rejects every non-GET request without a
        // JSON content type, including bodyless DELETE/POST calls.
        ...(write || body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        ...(write ? UI_HEADERS : {}),
        ...headers,
      },
      body: write ? JSON.stringify(body === undefined ? {} : body) : (body === undefined ? undefined : JSON.stringify(body)),
    });
    if (raw) {
      if (!response.ok) throw new ApiError('请求未完成（HTTP ' + response.status + '）。', response.status);
      return response;
    }
    let data = null;
    const type = response.headers.get('content-type') || '';
    if (type.includes('application/json')) {
      try { data = await response.json(); } catch { data = null; }
    } else {
      try { data = await response.text(); } catch { data = null; }
    }
    if (!response.ok) {
      let detail = '';
      if (data && typeof data === 'object') {
        if (typeof data.detail === 'string') detail = data.detail;
        else if (typeof data.message === 'string') detail = data.message;
        else if (typeof data.error === 'string') detail = data.error;
      }
      if (!detail) detail = response.status === 401 ? '本机管理凭据无效，请从 Tulpa 重新打开窗口。'
        : response.status === 403 ? '仅允许本机同源操作。'
        : response.status === 404 ? '请求的资料或页面不存在。'
        : response.status === 409 ? '当前有冲突的任务或设置，请稍后重试。'
        : '请求未完成（HTTP ' + response.status + '）。';
      throw new ApiError(detail, response.status, data);
    }
    return data;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error && error.name === 'AbortError') throw new ApiError('请求超时，请检查本机服务是否仍在运行。', 0);
    throw new ApiError('无法连接本机服务，请确认 Tulpa 仍在运行。', 0);
  } finally {
    clearTimeout(timer);
  }
}

/* ---------- events ---------- */
const bus = new EventTarget();
export function emit(name, detail = null) {
  bus.dispatchEvent(new CustomEvent(name, { detail }));
}
export function on(name, fn) {
  const handler = event => fn(event.detail);
  bus.addEventListener(name, handler);
  return () => bus.removeEventListener(name, handler);
}

/* ---------- state ---------- */
export const app = {
  ready: false,
  views: new Map(),
  current: null,
  route: { name: 'home', params: [], query: {} },
  sessions: [],
  currentSession: null,
  global: { overview: null, desktop: null, mcp: null, live: null, managed: null, ok: false, at: 0 },
  jobs: new Map(),
  jobPollers: new Set(),
  activeJob: null,
  pendingNewChat: false,
  liveThrough: 0,
  actions: new Map(),
  appearance: 'system',
};

const actions = app.actions;
export function registerAction(name, fn) { actions.set(name, fn); }

/* ---------- toast ---------- */
let toastSequence = 0;
export function toast(message, tone = '') {
  const host = $('#toast');
  if (!host) return;
  const text = String(message ?? '').trim();
  if (!text) return;
  const node = h('div', { class: 'toast ' + tone, 'data-toast': String(++toastSequence), role: 'status' },
    icon(tone === 'bad' ? 'alert' : tone === 'success' ? 'check' : tone === 'warn' ? 'alert' : 'info'),
    h('span', { text }));
  host.append(node);
  while (host.children.length > 3) host.firstElementChild?.remove();
  setTimeout(() => {
    node.classList.add('out');
    setTimeout(() => node.remove(), 200);
  }, tone === 'bad' ? 7000 : 4800);
}

export function reportError(error, fallback = '操作未完成，请稍后重试。') {
  const message = errorMessage(error, fallback);
  toast(message, 'bad');
  return message;
}

/* ---------- dialogs ---------- */
const dialogRoot = () => $('#dialog-root');

export function openDialog({ title = '', subtitle = '', body = null, footer = null, side = false, wide = false, small = false, className = '', onClose = null } = {}) {
  const dialog = h('dialog', {
    class: 'dialog' + (side ? ' side-dialog' : '') + (wide ? ' wide' : '') + (small ? ' small' : '') + (className ? ' ' + className : ''),
    'aria-label': title || '对话框',
  });
  const headingId = 'dlg-title-' + Math.random().toString(16).slice(2);
  const head = h('header', { class: 'dialog-head' },
    h('div', { class: 'grow' },
      h('h2', { id: headingId, text: title }),
      subtitle ? h('p', { text: subtitle }) : null),
    h('button', { class: 'icon-button', type: 'button', 'aria-label': '关闭', title: '关闭', onclick: () => dialog.close() }, icon('close')));
  dialog.setAttribute('aria-labelledby', headingId);
  const bodyNode = h('div', { class: 'dialog-body' }, body);
  dialog.append(head, bodyNode);
  if (footer) dialog.append(footer);
  dialog.addEventListener('close', () => {
    dialog.remove();
    if (onClose) { try { onClose(); } catch { /* presentation only */ } }
  });
  dialog.addEventListener('click', event => {
    if (event.target !== dialog) return;
    const rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
  });
  (dialogRoot() || document.body).append(dialog);
  if (!dialog.open) dialog.showModal();
  return { dialog, body: bodyNode, head, footer, close: () => dialog.close() };
}

export function confirmDialog({ title = '请确认', message = '', details = '', confirmText = '确认', cancelText = '取消', danger = false } = {}) {
  return new Promise(resolve => {
    let settled = false;
    let ref = null;
    const finish = value => {
      if (settled) return;
      settled = true;
      resolve(value);
      if (ref && ref.dialog.open) ref.dialog.close();
    };
    const body = h('div', { class: 'stack' },
      message ? h('p', { text: message, style: { lineHeight: '1.7', fontSize: '12px' } }) : null,
      details ? h('pre', { class: 'code-block', text: details }) : null);
    const footer = h('footer', { class: 'dialog-foot' },
      h('span', { class: 'spacer' }),
      h('button', { class: 'quiet-button', type: 'button', text: cancelText, onclick: () => finish(false) }),
      h('button', { class: (danger ? 'danger-button' : 'primary-button'), type: 'button', text: confirmText, onclick: () => finish(true) }));
    ref = openDialog({ title, body, footer, small: true, onClose: () => finish(false) });
    requestAnimationFrame(() => footer.querySelector('button:last-child')?.focus());
  });
}

export function promptDialog({ title = '输入', label = '', value = '', placeholder = '', maxLength = 200, confirmText = '保存', multiline = false, allowEmpty = false } = {}) {
  return new Promise(resolve => {
    let settled = false;
    let ref = null;
    const finish = result => {
      if (settled) return;
      settled = true;
      resolve(result);
      if (ref && ref.dialog.open) ref.dialog.close();
    };
    const input = multiline
      ? h('textarea', { rows: 5, maxLength, placeholder, value })
      : h('input', { type: 'text', maxLength, placeholder, value });
    const body = h('form', { class: 'stack', onsubmit: event => { event.preventDefault(); submit(); } },
      label ? h('label', { class: 'field' }, h('span', { text: label }), input) : input);
    const footer = h('footer', { class: 'dialog-foot' },
      h('span', { class: 'spacer' }),
      h('button', { class: 'quiet-button', type: 'button', text: '取消', onclick: () => finish(null) }),
      h('button', { class: 'primary-button', type: 'submit', text: confirmText, onclick: event => { event.preventDefault(); submit(); } }));
    function submit() {
      const next = input.value.trim();
      if (!next && !allowEmpty) { toast('请填写内容。', 'warn'); input.focus(); return; }
      finish(next);
    }
    ref = openDialog({ title, body, footer, small: true, onClose: () => finish(null) });
    requestAnimationFrame(() => { input.focus(); input.select?.(); });
  });
}

export function openImage(url, alt = '聊天图片') {
  const dialog = $('#image-dialog');
  if (!dialog || !url) return;
  const image = $('#image-preview');
  image.src = url;
  image.alt = alt;
  const link = $('#image-open-tab');
  if (link) link.href = url;
  if (!dialog.open) dialog.showModal();
}

/* ---------- popovers ---------- */
let activePopover = null;
export function closePopover() {
  if (!activePopover) return;
  const { node, cleanup } = activePopover;
  activePopover = null;
  cleanup?.();
  node.remove();
}
export function openPopover(anchor, content, { align = 'start', label = '' } = {}) {
  closePopover();
  const node = h('div', { class: 'popover', role: 'menu', 'aria-label': label || '菜单' }, content);
  document.body.append(node);
  const rect = anchor.getBoundingClientRect();
  const width = node.offsetWidth;
  let left = align === 'end' ? rect.right - width : rect.left;
  left = Math.max(8, Math.min(left, window.innerWidth - width - 8));
  let top = rect.bottom + 5;
  if (top + node.offsetHeight > window.innerHeight - 8) top = Math.max(8, rect.top - node.offsetHeight - 5);
  node.style.left = left + 'px';
  node.style.top = top + 'px';
  const onPointer = event => {
    if (!node.contains(event.target) && !anchor.contains(event.target)) closePopover();
  };
  const onKey = event => {
    if (event.key === 'Escape') { event.stopPropagation(); closePopover(); anchor.focus?.(); }
  };
  const onScroll = event => { if (event.target !== node) closePopover(); };
  setTimeout(() => document.addEventListener('pointerdown', onPointer, true), 0);
  document.addEventListener('keydown', onKey, true);
  window.addEventListener('scroll', onScroll, true);
  activePopover = {
    node,
    cleanup() {
      document.removeEventListener('pointerdown', onPointer, true);
      document.removeEventListener('keydown', onKey, true);
      window.removeEventListener('scroll', onScroll, true);
    },
  };
  requestAnimationFrame(() => node.querySelector('button:not(:disabled)')?.focus());
  return node;
}

/* ---------- form helpers ---------- */
export function field(label, control, hint = '', attrs = {}) {
  const id = control.id || ('field-' + Math.random().toString(16).slice(2));
  if (!control.id) control.id = id;
  return h('label', { class: 'field', for: id, ...attrs },
    h('span', { text: label }),
    control,
    hint ? h('small', { text: hint }) : null);
}

export function fieldLabel(text, control, hint = '') {
  const id = control.id || ('field-' + Math.random().toString(16).slice(2));
  if (!control.id) control.id = id;
  return h('div', { class: 'field' },
    h('label', { class: 'field-label', for: id, text }),
    control,
    hint ? h('small', { class: 'field-hint', text: hint }) : null);
}

export function option(value, label, extra = {}) {
  return h('option', { value, ...extra, text: label });
}

export function selectControl(options, value, attrs = {}) {
  const select = h('select', attrs);
  for (const item of options) {
    if (Array.isArray(item)) select.append(option(item[0], item[1]));
    else select.append(option(item.value, item.label, item.disabled ? { disabled: true } : {}));
  }
  if (value !== undefined && value !== null) select.value = String(value);
  return select;
}

export function checkbox(label, checked = false, onChange = null, attrs = {}) {
  const input = h('input', { type: 'checkbox', checked: Boolean(checked), ...attrs });
  if (onChange) input.addEventListener('change', () => onChange(input.checked));
  const wrap = h('label', { class: 'check-row' }, input, h('span', { text: label }));
  wrap.input = input;
  return wrap;
}

export function switchControl(label, checked = false, onChange = null, attrs = {}) {
  const button = h('button', { class: 'switch', type: 'button', 'aria-checked': String(Boolean(checked)), ...attrs },
    h('span', { class: 'switch-track', 'aria-hidden': 'true' }),
    h('span', { class: 'switch-label', text: label }));
  button.addEventListener('click', () => {
    if (button.disabled) return;
    const next = button.getAttribute('aria-checked') !== 'true';
    button.setAttribute('aria-checked', String(next));
    onChange?.(next);
  });
  button.setChecked = value => button.setAttribute('aria-checked', String(Boolean(value)));
  return button;
}

export function segmented(options, value, onChange, attrs = {}) {
  const wrap = h('div', { class: 'segmented', role: 'group', ...attrs });
  const buttons = new Map();
  for (const item of options) {
    const [id, label] = Array.isArray(item) ? item : [item.value, item.label];
    const button = h('button', { type: 'button', text: label, 'aria-pressed': String(String(id) === String(value)) });
    button.addEventListener('click', () => {
      for (const [key, node] of buttons) node.setAttribute('aria-pressed', String(key === String(id)));
      onChange?.(id);
    });
    buttons.set(String(id), button);
    wrap.append(button);
  }
  wrap.setValue = id => { for (const [key, node] of buttons) node.setAttribute('aria-pressed', String(key === String(id))); };
  return wrap;
}

export function emptyState(iconName, title, description = '', actions = null, compact = false) {
  return h('div', { class: 'empty-state' + (compact ? ' compact' : '') },
    iconName ? h('div', { class: 'empty-icon' }, icon(iconName)) : null,
    h('strong', { text: title }),
    description ? h('p', { text: description }) : null,
    actions ? h('div', { class: 'row wrap', style: { marginTop: '4px' } }, actions) : null);
}

export function loadingState(text = '正在读取…') {
  return h('div', { class: 'loading-state' }, h('span', { class: 'spinner' }), h('span', { text }));
}

export function skeletonBlock(lines = 4) {
  const block = h('div', { class: 'card pad', style: { marginBottom: '10px' } });
  for (let index = 0; index < lines; index += 1) {
    block.append(h('div', { class: 'skeleton skeleton-line' + (index === lines - 1 ? ' w60' : '') }));
  }
  return block;
}

export function notice(text, tone = 'info', strong = '') {
  return h('div', { class: 'notice ' + tone }, icon(tone === 'warn' || tone === 'danger' ? 'alert' : tone === 'success' ? 'check' : 'info'),
    h('div', {}, strong ? h('strong', { text: strong }) : null, h('span', { text })));
}

export function copyText(text) {
  return navigator.clipboard.writeText(text).catch(() => {
    const area = h('textarea', { style: { position: 'fixed', opacity: '0' } });
    area.value = text;
    document.body.append(area);
    area.select();
    const ok = document.execCommand('copy');
    area.remove();
    if (!ok) throw new Error('复制失败');
  });
}

/* ---------- theme / appearance ---------- */
const APPEARANCES = ['system', 'light', 'dark'];
const THEME_KEY = 'tulpa.appearance';
const systemMedia = window.matchMedia('(prefers-color-scheme: dark)');
let nativeReady = false;
let appearanceRevision = 0;

function resolvedTheme() {
  return app.appearance === 'system' ? (systemMedia.matches ? 'dark' : 'light') : app.appearance;
}

export function appliesDark() { return resolvedTheme() === 'dark'; }

export function applyAppearance() {
  const root = document.documentElement;
  root.dataset.theme = resolvedTheme();
  root.dataset.appearance = app.appearance;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', resolvedTheme() === 'dark' ? '#17181a' : '#f5f3ed');
  updateAppearanceControls();
}

function updateAppearanceControls() {
  const dark = resolvedTheme() === 'dark';
  const quickIcon = $('#appearance-quick-icon use');
  if (quickIcon) quickIcon.setAttribute('href', dark ? '#i-sun' : '#i-moon');
  const quickLabel = $('#appearance-quick-label');
  if (quickLabel) quickLabel.textContent = dark ? '浅色' : '深色';
  const quick = $('#appearance-quick');
  if (quick) quick.title = dark ? '切换到浅色' : '切换到深色';
  const topUse = $('#topbar-theme use');
  if (topUse) topUse.setAttribute('href', dark ? '#i-sun' : '#i-moon');
  const top = $('#topbar-theme');
  if (top) top.title = dark ? '切换到浅色' : '切换到深色';
  $$('[data-appearance]').forEach(button => {
    button.setAttribute('aria-pressed', String(button.dataset.appearance === app.appearance));
  });
}

export function getAppearance() { return app.appearance; }

export async function setAppearance(value, { persist = true, quiet = false } = {}) {
  if (!APPEARANCES.includes(value)) return;
  appearanceRevision += 1;
  app.appearance = value;
  applyAppearance();
  emit('appearance', value);
  if (!persist) return;
  try {
    if (nativeReady && window.TulpaDesktopUI) {
      await window.TulpaDesktopUI.call('set_appearance', value);
      window.TulpaDesktopUI.appearance = value;
    } else {
      localStorage.setItem(THEME_KEY, value);
    }
  } catch {
    if (!quiet) toast('本次已切换外观，但保存失败。', 'warn');
  }
}

function restoreNativeAppearance() {
  if (!window.TulpaDesktopUI) return;
  nativeReady = true;
  const revision = appearanceRevision;
  try {
    const value = window.TulpaDesktopUI.appearance;
    if (revision === appearanceRevision && APPEARANCES.includes(value) && value !== app.appearance) {
      app.appearance = value;
      applyAppearance();
      emit('appearance', value);
    }
  } catch {
    /* Browser or shell without the appearance property keeps the local choice. */
  }
}

function initAppearance() {
  let saved = 'system';
  try {
    const value = localStorage.getItem(THEME_KEY);
    if (APPEARANCES.includes(value)) saved = value;
  } catch { /* storage may be unavailable */ }
  app.appearance = saved;
  applyAppearance();
  systemMedia.addEventListener?.('change', () => { if (app.appearance === 'system') { applyAppearance(); emit('appearance', 'system'); } });
  window.addEventListener('tulpa-native-ready', restoreNativeAppearance);
  restoreNativeAppearance();
  window.addEventListener('storage', event => {
    if (nativeReady || event.key !== THEME_KEY) return;
    if (APPEARANCES.includes(event.newValue)) { app.appearance = event.newValue; applyAppearance(); emit('appearance', event.newValue); }
  });
}

export function toggleAppearance() {
  setAppearance(appliesDark() ? 'light' : 'dark');
}

/* ---------- Gradio bridge (existing chat callbacks) ---------- */
const GRADIO = '/legacy';
export const gradio = {
  initialized: false,
  initializing: null,
  error: '',
  hash: (crypto.randomUUID ? crypto.randomUUID() : 'sid-' + Date.now() + '-' + Math.random().toString(16).slice(2)),
  functions: {},

  async init(force = false) {
    if (this.initialized && !force) return this.functions;
    if (this.initializing) return this.initializing;
    this.initializing = (async () => {
      try {
        const response = await fetch(GRADIO + '/config', { cache: 'no-store', credentials: 'same-origin' });
        if (!response.ok) throw new Error('内置对话服务未启用。');
        const config = await response.json();
        const map = {};
        for (const dependency of config.dependencies || []) {
          if (dependency.api_name) map[dependency.api_name] = dependency.id;
        }
        this.functions = map;
        this.initialized = true;
        this.error = '';
        return map;
      } catch (error) {
        this.initialized = false;
        this.error = error instanceof Error ? error.message : '内置对话服务未启用。';
        throw new ApiError(this.error);
      } finally {
        this.initializing = null;
      }
    })();
    return this.initializing;
  },

  async run(name, data = [], { timeout = 25000 } = {}) {
    await this.init();
    if (!(name in this.functions)) throw new ApiError('服务尚未提供“' + name + '”回调。');
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch(GRADIO + '/gradio_api/run/' + encodeURIComponent(name), {
        method: 'POST',
        cache: 'no-store',
        credentials: 'same-origin',
        signal: controller.signal,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ data, fn_index: this.functions[name], session_hash: this.hash }),
      });
      if (!response.ok) throw new ApiError('聊天数据状态读取失败，请稍后重试。');
      const value = await response.json();
      return value.data;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (error && error.name === 'AbortError') throw new ApiError('读取聊天状态超时，请稍后重试。');
      throw new ApiError('连接内置对话服务失败，请确认本机服务正在运行。');
    } finally {
      clearTimeout(timer);
    }
  },

  async call(name, data = [], update = null) {
    await this.init();
    if (!(name in this.functions)) throw new ApiError('服务尚未提供“' + name + '”回调，请确认已启用完整界面。');
    if (name === 'refresh') {
      const result = await this.run(name, data);
      update?.(result, true);
      return result;
    }
    const joined = await fetch(GRADIO + '/gradio_api/queue/join', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        fn_index: this.functions[name], data, session_hash: this.hash,
        simple_format: true, event_data: null, trigger_id: null,
      }),
    });
    if (!joined.ok) throw new ApiError('连接服务失败，请确认本机服务正在运行。');
    const { event_id: eventId } = await joined.json();
    const stream = await fetch(GRADIO + '/gradio_api/queue/data?session_hash=' + encodeURIComponent(this.hash), {
      cache: 'no-store', credentials: 'same-origin',
    });
    if (!stream.ok || !stream.body) throw new ApiError('无法接收结果，请刷新后重试。');
    const reader = stream.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    try {
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) throw new ApiError('连接中断，已保存的对话可以从左侧恢复。');
        buffer += decoder.decode(chunk.value, { stream: true });
        let end;
        while ((end = buffer.indexOf('\n')) >= 0) {
          const line = buffer.slice(0, end).trimEnd();
          buffer = buffer.slice(end + 1);
          if (!line.startsWith('data: ')) continue;
          let event;
          try { event = JSON.parse(line.slice(6)); } catch { continue; }
          if (event.event_id && event.event_id !== eventId) continue;
          if (event.msg === 'process_generating' || event.msg === 'process_completed') {
            if (event.success === false) throw new ApiError('本次操作未完成，请稍后重试。');
            const result = event.output?.data;
            if (result) update?.(result, event.msg === 'process_completed');
            if (event.msg === 'process_completed') return result;
          }
          if (event.msg === 'unexpected_error') throw new ApiError('本机服务发生错误，请稍后重试。');
        }
      }
    } finally {
      reader.cancel().catch(() => {});
    }
  },

  async stop() {
    await this.init();
    if (!('stop_agent' in this.functions)) return;
    const response = await fetch(GRADIO + '/gradio_api/run/stop_agent', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ data: [null], fn_index: this.functions.stop_agent, session_hash: this.hash }),
    });
    if (!response.ok) throw new ApiError('停止请求未送达，请再试一次。');
  },
};

/* ---------- jobs ---------- */
const JOB_LABELS = {
  sync: '刷新消息', read: '读取聊天记录', catalog: '更新会话目录', delete: '删除聊天记录',
  'media-refresh': '刷新媒体', check: '更新关注', question: '回答关注问题', unknown: '处理中',
};
function jobLabel(job) {
  return job.meta?.label || job.label || JOB_LABELS[job.kind] || '处理中';
}
function jobDetail(job) {
  if (job.error) return job.error;
  if (job.progress) {
    const p = job.progress;
    const stageNames = {
      preparing: '准备读取器', snapshot: '制作快照', decrypt: '验证并解密数据', strip_header: '准备数据库',
      inventory: '比较消息', reading: '读取消息', waiting_for_commit: '等待入库', exported: '核对结果', committing: '写入消息',
    };
    const platform = p.platform === 'qq' ? 'QQ' : p.platform === 'wechat' ? '微信' : '';
    const stage = stageNames[p.stage] || job.events?.at(-1) || '处理中';
    return [platform, stage].filter(Boolean).join(' · ') + (p.imported ? ` · 新增 ${fmtNumber(p.imported)} 条` : '');
  }
  return job.events?.at(-1) || '正在准备…';
}
function jobPercent(job) {
  const p = job.progress;
  if (!p || !p.expected) return null;
  return Math.max(0, Math.min(100, Math.round((Number(p.scanned || 0) / Number(p.expected)) * 100)));
}

export async function monitorJob(jobId, meta = {}) {
  if (!jobId) return null;
  if (app.jobPollers.has(jobId)) return app.jobs.get(jobId) || null;
  const initial = app.jobs.get(jobId) || {};
  const job = Object.assign({ id: jobId, status: 'running', events: [], result: null, error: null }, initial, meta);
  app.jobs.set(jobId, job);
  app.jobPollers.add(jobId);
  app.activeJob = job;
  renderJobStatus();
  emit('job', job);
  let failures = 0;
  try {
    while (true) {
      await sleep(failures ? 2200 : 900);
      if (!app.jobPollers.has(jobId)) break;
      try {
        const data = await api('/api/watch-jobs/' + encodeURIComponent(jobId), { timeout: 16000 });
        Object.assign(job, data);
        failures = 0;
      } catch (error) {
        failures += 1;
        if (failures >= 4) {
          job.status = 'error';
          job.error = errorMessage(error, '无法读取任务状态，请到数据页重试。');
          break;
        }
        continue;
      }
      emit('job', job);
      renderJobStatus();
      if (job.status !== 'running') break;
    }
  } finally {
    app.jobPollers.delete(jobId);
    job.finishedAt = Date.now();
    if (job.status === 'running') job.status = 'error';
    emit('job', job);
    renderJobStatus();
    emit('job-done', { job });
    if (job.status === 'error') toast(job.error || jobDetail(job) || '任务未完成。', 'bad');
    else {
      const summary = job.result?.summary || (Array.isArray(job.result) ? job.result.map(r => `${r.platform === 'qq' ? 'QQ' : '微信'} ${r.status === 'ok' ? '+' + r.added : r.detail || '未完成'}`).join(' / ') : '');
      if (summary && meta.toast !== false) toast(summary, 'success');
    }
    app.activeJob = [...app.jobs.values()].filter(item => item.status === 'running').at(-1) || null;
    renderJobStatus();
    if (![...app.jobs.values()].some(item => item.status === 'running')) {
      setTimeout(() => { emit('data-changed'); refreshGlobal({ silent: true }); }, 160);
    }
    const finished = [...app.jobs.values()].filter(item => item.status !== 'running').sort((a, b) => (b.finishedAt || 0) - (a.finishedAt || 0));
    for (const old of finished.slice(12)) app.jobs.delete(old.id);
  }
  return job;
}

export async function startJob(path, body, meta = {}) {
  const result = await api(path, { method: 'POST', body, timeout: 30000 });
  if (result?.job_id) {
    monitorJob(result.job_id, { ...meta, kind: result.kind || meta.kind });
  }
  return result;
}

export async function attachRunningJobs() {
  try {
    const status = await api('/api/refresh-status', { timeout: 12000 });
    for (const job of status.running_jobs || []) {
      if (!app.jobPollers.has(job.id)) monitorJob(job.id, { kind: job.kind, cardId: job.card_id });
    }
  } catch { /* the status endpoint is optional before first use */ }
}

function renderJobStatus() {
  const bar = $('#statusbar');
  if (!bar) return;
  const running = [...app.jobs.values()].filter(job => job.status === 'running');
  const job = running.at(-1);
  if (!job) { bar.hidden = true; return; }
  app.activeJob = job;
  bar.hidden = false;
  $('#statusbar-label').textContent = jobLabel(job);
  $('#statusbar-detail').textContent = jobDetail(job);
  const progress = jobPercent(job);
  const track = $('#statusbar-progress');
  track.classList.toggle('indeterminate', progress === null);
  const fill = track.firstElementChild;
  if (progress === null) fill.style.width = '';
  else fill.style.width = progress + '%';
  const stop = $('#statusbar-stop');
  stop.hidden = false;
  stop.disabled = false;
}

export async function stopActiveJob() {
  const job = app.activeJob || [...app.jobs.values()].find(item => item.status === 'running');
  if (!job) return;
  try {
    await api('/api/watch-jobs/' + encodeURIComponent(job.id) + '/stop', { method: 'POST', body: {} });
    toast('已请求停止，已完成的批次会保留。', 'warn');
  } catch (error) { reportError(error); }
}

export function openJobDialog(job = app.activeJob) {
  if (!job) return;
  const status = h('p', { class: 'status-line' });
  const progress = h('div', { class: 'progress' }, h('span'));
  const events = h('pre', { class: 'job-events' });
  const metrics = h('div', { class: 'job-metrics' });
  const body = h('div', { class: 'stack' }, status, progress, metrics, events);
  const stop = h('button', {
    class: 'quiet-button danger-text', type: 'button', text: '停止任务',
    onclick: async () => { await stopActiveJob(); },
  });
  const ref = openDialog({
    title: jobLabel(job), subtitle: '任务编号 ' + job.id.slice(0, 8), body,
    footer: h('footer', { class: 'dialog-foot' }, h('span', { class: 'spacer' }), stop),
    onClose: () => unsubscribe(),
  });
  const unsubscribe = on('job', updated => {
    if (!updated || updated.id !== job.id) return;
    Object.assign(job, updated);
    paint();
  });
  function paint() {
    status.textContent = job.status === 'running' ? '正在运行 · ' + jobDetail(job)
      : job.status === 'error' ? '未完成 · ' + (job.error || jobDetail(job)) : '已完成 · ' + (job.result?.summary || jobDetail(job));
    status.className = 'status-line ' + (job.status === 'error' ? 'error' : job.status === 'running' ? '' : 'success');
    const percent = jobPercent(job);
    progress.classList.toggle('indeterminate', percent === null);
    progress.firstElementChild.style.width = percent === null ? '' : percent + '%';
    stop.hidden = job.status !== 'running';
    events.textContent = (job.events || []).join('\n') || '尚无过程记录。';
    metrics.replaceChildren();
    const p = job.progress;
    if (p) {
      for (const [label, value] of [
        ['新增', fmtNumber(p.imported || 0)], ['已有', fmtNumber(p.duplicate || 0)],
        ['跳过', fmtNumber(p.skipped || 0)], ['用时', Math.round(p.elapsed_seconds || 0) + ' 秒'],
      ]) metrics.append(h('div', {}, h('strong', { text: value }), h('span', { text: label })));
    }
  }
  paint();
  return ref;
}

/* ---------- global status ---------- */
function statusDot(state) {
  return h('span', { class: 'status-dot ' + (state || '') });
}

function chip(label, state, route, query = {}) {
  return h('button', {
    class: 'status-chip', type: 'button', title: label,
    onclick: () => navigate(route, query),
  }, statusDot(state), h('span', { text: label }));
}

/* Kept local so app.js has no import cycle with the route module in live.js. */
function liveSummary(snapshot) {
  if (!snapshot?.platforms) return { label: '实时状态未知', state: '' };
  const active = snapshot.platforms.filter(item => item.status !== 'off');
  if (!active.length) return { label: '实时读取关闭', state: '' };
  const problematic = active.find(item => ['degraded', 'retrying', 'client_stopped', 'needs_baseline', 'unsupported'].includes(item.status));
  if (problematic) {
    const name = problematic.platform === 'qq' ? 'QQ' : '微信';
    const labels = { degraded: '读取受限', retrying: '重试中', client_stopped: '客户端未运行', needs_baseline: '待首次导入', unsupported: '未适配' };
    return { label: name + ' ' + (labels[problematic.status] || '状态异常'), state: 'warn' };
  }
  const names = active.map(item => item.platform === 'qq' ? 'QQ' : '微信').join(' / ');
  return { label: names + ' 监听中', state: 'ok' };
}

export function updateShellStatus() {
  const global = app.global;
  const health = $('#health-text');
  const dot = $('#health-dot');
  if (health) health.textContent = global.ok ? '本机服务已连接' : '本机服务连接中';
  if (dot) dot.className = 'status-dot ' + (global.ok ? 'ok' : '');
  const host = $('#topbar-status');
  if (!host) return;
  const live = liveSummary(global.live);
  const mcp = global.mcp?.running
    ? { label: 'MCP 运行中', state: 'ok' }
    : { label: 'MCP 未开启', state: '' };
  host.replaceChildren(
    chip(live.label, live.state, 'data', { section: 'live' }),
    chip(mcp.label, mcp.state, 'agents'),
  );
  const title = host.firstElementChild;
  if (title) title.classList.add('secondary');
}

export async function refreshGlobal({ silent = true } = {}) {
  const results = await Promise.allSettled([
    api('/api/terminal/overview', { timeout: 15000 }),
    api('/api/desktop/status', { timeout: 15000 }),
    api('/api/mcp', { timeout: 15000 }),
    api('/api/live-status', { timeout: 12000 }),
  ]);
  const names = ['overview', 'desktop', 'mcp', 'live'];
  let ok = false;
  results.forEach((result, index) => {
    if (result.status === 'fulfilled') {
      app.global[names[index]] = result.value;
      ok = true;
    }
  });
  app.global.ok = ok;
  app.global.at = Date.now();
  updateShellStatus();
  emit('global', app.global);
  if (!ok && !silent) toast('本机服务暂时不可用，请确认 Tulpa 仍在运行。', 'bad');
  return app.global;
}

let liveTimer = null;
let liveBusy = false;
async function pollLive() {
  if (document.hidden || liveBusy) return;
  liveBusy = true;
  try {
    const data = await api('/api/live-status', { timeout: 9000 });
    app.global.live = data;
    app.global.ok = true;
    updateShellStatus();
    emit('live', data);
    const upper = Math.max(0, ...(data.platforms || []).map(item => Number(item.last_id) || 0));
    if (upper > app.liveThrough) {
      app.liveThrough = upper;
      emit('data-changed');
      api('/api/live-visible', { method: 'POST', body: { through_id: upper }, timeout: 9000 }).catch(() => {});
    }
  } catch {
    // A transient loopback failure should not erase the last known state.
  } finally {
    liveBusy = false;
  }
}

export function startStatusLoop() {
  clearInterval(liveTimer);
  liveTimer = setInterval(() => {
    if (document.hidden) return;
    void pollLive();
    if (Date.now() - app.global.at > 25000) void refreshGlobal();
  }, 3000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) { void pollLive(); void refreshGlobal(); }
  });
  void pollLive();
  void refreshGlobal();
}

/* ---------- sidebar history ---------- */
export function setSessions(choices, currentId = null) {
  app.sessions = (choices || []).map(item => Array.isArray(item) ? { label: item[0], id: item[1] } : { label: item.label, id: item.value });
  if (currentId !== undefined) app.currentSession = currentId;
  renderSidebarHistory();
}
export function setCurrentSession(id) {
  app.currentSession = id || null;
  renderSidebarHistory();
}
export function setWatchCount(count) {
  const node = $('#watch-nav-count');
  if (!node) return;
  const value = Number(count) || 0;
  node.hidden = !value;
  node.textContent = value > 99 ? '99+' : String(value);
}

function renderSidebarHistory() {
  const host = $('#history-list');
  if (!host) return;
  const query = ($('#history-search')?.value || '').trim().toLocaleLowerCase();
  const label = h('div');
  let shown = 0;
  for (const item of app.sessions) {
    const title = titleFromSessionLabel(item.label);
    if (query && !title.toLocaleLowerCase().includes(query)) continue;
    shown += 1;
    const button = h('button', {
      class: 'history-item', type: 'button', title: item.label.replace('T', ' '),
      'aria-current': String(item.id === app.currentSession),
      onclick: () => emit('session-open', item.id),
    },
      h('strong', { text: title || '新对话' }),
      h('small', { text: sessionStamp(item.label) || '本机对话' }));
    const remove = h('button', {
      class: 'icon-button history-delete', type: 'button', 'aria-label': '删除对话：' + title,
      title: '删除对话',
      onclick: event => { event.stopPropagation(); emit('session-delete', { id: item.id, title }); },
    }, icon('trash'));
    label.append(h('div', { class: 'history-row' }, button, remove));
  }
  if (!shown) label.append(h('p', { class: 'history-empty', text: query ? '没有找到相关对话。' : '还没有对话。直接输入问题开始。' }));
  host.replaceChildren(label);
}

function activeNavName(name) {
  if (name === 'home') return 'home';
  if (name === 'chat' || name === 'chats') return name;
  if (name === 'watches') return 'watches';
  if (name === 'workspaces') return 'workspaces';
  if (name === 'collections') return 'collections';
  return name;
}

function updateNav(name) {
  const active = activeNavName(name);
  $$('#sidebar-nav .nav-item').forEach(button => {
    if (button.dataset.route === active) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  const isChat = name === 'chat';
  $('#sidebar-history').hidden = !isChat;
  if (!isChat) $('#sidebar-history').dataset.route = '';
}

/* ---------- router ---------- */
export function registerView(name, definition) {
  app.views.set(name, definition);
}

export function parseRoute() {
  const raw = location.hash.replace(/^#\/?/, '');
  const [pathPart, queryPart = ''] = raw.split('?');
  const segments = pathPart.split('/').filter(Boolean).map(decodeURIComponent);
  const query = {};
  const params = new URLSearchParams(queryPart);
  for (const [key, value] of params.entries()) query[key] = value;
  return { name: segments[0] || 'home', params: segments.slice(1), query };
}

function stringifyRoute(route, query = {}) {
  const parts = String(route).split('/').filter(Boolean).map(encodeURIComponent);
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query || {})) {
    if (value !== undefined && value !== null && value !== '') params.set(key, value);
  }
  return '#/' + parts.join('/') + (params.toString() ? '?' + params.toString() : '');
}

export function navigate(route, query = {}, { replace = false } = {}) {
  const target = stringifyRoute(route, query);
  closePopover();
  closeMobileSidebar();
  if (location.hash === target) return;
  if (replace) {
    history.replaceState(null, '', target);
    void renderRoute();
  } else {
    location.hash = target;
  }
}

export function setViewTitle(title, subtitle = '') {
  const titleNode = $('#view-title');
  const subNode = $('#view-subtitle');
  if (titleNode) titleNode.textContent = title || 'Tulpa';
  if (subNode) subNode.textContent = subtitle || '';
  document.title = title ? 'Tulpa · ' + title : 'Tulpa';
}

function renderRouteError(host, error) {
  const message = errorMessage(error, '页面暂时无法加载，请稍后重试。');
  clear(host);
  host.append(h('div', { class: 'view-scroll' }, h('div', { class: 'page' },
    h('div', { class: 'card', style: { maxWidth: '620px', margin: '8vh auto' } },
      emptyState('alert', '这个页面暂时无法打开', message,
        h('button', { class: 'quiet-button', type: 'button', text: '重新加载', onclick: () => renderRoute({ force: true }) }))))));
}

// Views such as data.js keep their UI in module-level state. Two overlapping
// mounts of the same view would therefore write each other's globals, and the
// stale mount's cleanup could reset an already-finished newer instance. Serialize
// lifecycle by view name: a new mount waits for the previous same-view attempt
// (including its late cleanup) before touching its module state. Different views
// never wait for each other.
const viewLifecycles = new Map();

function finishViewLifecycle(record) {
  const finish = record.finishLifecycle;
  record.finishLifecycle = null;
  if (finish) finish();
}

function disposeRoute(record) {
  if (!record) return Promise.resolve();
  const firstDispose = !record.disposed;
  record.disposed = true;
  record.host?.remove();
  // A mount that was still pending when navigation happened can only return
  // its cleanup later. Keep the record disposable so that cleanup still runs,
  // exactly once, when the stale mount settles.
  if (firstDispose && typeof record.cleanup !== 'function' && typeof record.unmount === 'function') {
    const unmount = record.unmount;
    record.unmount = null;
    try { unmount(); } catch { /* a view must not block navigation */ }
  }
  const cleanup = record.cleanup;
  record.cleanup = null;
  let result;
  if (typeof cleanup === 'function') {
    try { result = cleanup(); } catch { /* a view must not block navigation */ }
  }
  // A pending mount has no cleanup yet. Its lifecycle stays queued until the
  // mount settles and this function runs again with the returned cleanup.
  return Promise.resolve(result).catch(() => {}).then(() => {
    if (!record.pending) finishViewLifecycle(record);
  });
}

let renderToken = 0;
let renderedFor = '';
export async function renderRoute({ force = false, focus = false } = {}) {
  const token = ++renderToken;
  const parsed = parseRoute();
  const viewName = app.views.has(parsed.name) ? parsed.name : 'home';
  const view = app.views.get(viewName);
  const key = location.hash || '#/home';
  if (!force && renderedFor === key && app.current) return;
  renderedFor = key;
  // Every navigation owns a fresh host. A stale async mount can only write
  // into its detached host and its cleanup is invoked when it settles.
  disposeRoute(app.current);
  const root = $('#view-root');
  if (!root) return;
  const host = h('div', { class: 'route-host' });
  root.replaceChildren(host);
  const record = {
    name: parsed.name, view, params: parsed.params, query: parsed.query,
    host, cleanup: null, unmount: typeof view?.unmount === 'function' ? () => view.unmount() : null,
    mounted: false, disposed: false, pending: true, error: null, finishLifecycle: null,
  };
  // Queue behind the previous lifecycle of this same view, but create and
  // register the new record immediately so navigation UI stays responsive.
  const previous = viewLifecycles.get(viewName) || Promise.resolve();
  const lifecycle = new Promise(resolve => { record.finishLifecycle = resolve; });
  viewLifecycles.set(viewName, lifecycle);
  app.current = record;
  app.route = parsed;
  updateNav(parsed.name);
  setViewTitle(view?.title || 'Tulpa', view?.subtitle || '');
  if (!view) {
    record.pending = false;
    renderRouteError(host, new Error('页面不存在。'));
    finishViewLifecycle(record);
    return;
  }
  let started = false;
  try {
    try { await previous; } catch { /* a failed predecessor must not block this view */ }
    // A queued mount that was superseded while waiting never starts.
    if (record.disposed || token !== renderToken) return;
    started = true;
    try {
      const cleanup = await view.mount(host, parsed.params, parsed.query);
      record.cleanup = typeof cleanup === 'function' ? cleanup : null;
      record.pending = false;
      if (record.disposed || token !== renderToken) {
        await disposeRoute(record);
        return;
      }
      record.mounted = true;
      emit('route', parsed);
      if (focus) root.focus({ preventScroll: true });
    } catch (error) {
      record.pending = false;
      if (record.disposed || token !== renderToken) {
        await disposeRoute(record);
        return;
      }
      record.error = error;
      record.mounted = true;
      renderRouteError(host, error);
    }
  } finally {
    // Only release the queue here when a mount never began (or began but was
    // stopped before it could report pending=false). Successful live records
    // stay queued until navigation disposes them and their cleanup finishes.
    if (!started || record.pending) {
      record.pending = false;
      finishViewLifecycle(record);
    }
  }
}

function initialDeepLink() {
  if (location.hash) return null;
  const query = new URLSearchParams(location.search);
  if (query.has('anchor') && /^\d+$/.test(query.get('anchor'))) return stringifyRoute('chats', { anchor: query.get('anchor') });
  if (query.has('file') && /^\d+$/.test(query.get('file'))) return stringifyRoute('files', { file: query.get('file'), chunk: query.get('chunk') || '' });
  if (query.has('collection')) return stringifyRoute('collections', { collection: query.get('collection') });
  if (query.has('knowledge')) return stringifyRoute('collections', { knowledge: query.get('knowledge') });
  if (query.has('workspace')) return stringifyRoute('workspaces/' + query.get('workspace'), { task: query.get('task') || '' });
  return null;
}

/* ---------- boot / shell ---------- */
export function closeMobileSidebar() {
  document.body.classList.remove('sidebar-open');
  const scrim = $('#sidebar-scrim');
  if (scrim) scrim.hidden = true;
}

const NAV_SHORTCUTS = ['home', 'chat', 'chats', 'watches', 'files', 'collections', 'memory', 'workspaces', 'data'];

function initShell() {
  $$('#sidebar-nav .nav-item').forEach(button => {
    button.addEventListener('click', () => navigate(button.dataset.route));
  });
  $('#quick-new-chat')?.addEventListener('click', requestNewChat);
  $('#history-new')?.addEventListener('click', requestNewChat);
  $('#open-palette')?.addEventListener('click', openPalette);
  $('#menu-button')?.addEventListener('click', () => {
    if (matchMedia('(max-width: 740px)').matches) {
      const open = !document.body.classList.contains('sidebar-open');
      document.body.classList.toggle('sidebar-open', open);
      $('#sidebar-scrim').hidden = !open;
    } else {
      document.body.classList.remove('sidebar-collapsed');
      try { localStorage.setItem('tulpa.sidebarCollapsed', 'false'); } catch { /* local choice */ }
    }
  });
  $('#sidebar-scrim')?.addEventListener('click', closeMobileSidebar);
  $('#sidebar-collapse')?.addEventListener('click', () => {
    document.body.classList.add('sidebar-collapsed');
    try { localStorage.setItem('tulpa.sidebarCollapsed', 'true'); } catch { /* local choice */ }
  });
  try { if (localStorage.getItem('tulpa.sidebarCollapsed') === 'true' && !matchMedia('(max-width: 740px)').matches) document.body.classList.add('sidebar-collapsed'); } catch { /* local choice */ }
  $('#sidebar-health')?.addEventListener('click', () => navigate('data'));
  $('#topbar-settings')?.addEventListener('click', () => navigate('settings'));
  $('#topbar-theme')?.addEventListener('click', toggleAppearance);
  $('#appearance-quick')?.addEventListener('click', toggleAppearance);
  $('#help-shortcuts')?.addEventListener('click', () => $('#shortcuts-dialog')?.showModal());
  $('#statusbar-open')?.addEventListener('click', () => openJobDialog());
  $('#statusbar-stop')?.addEventListener('click', () => void stopActiveJob());
  $$('#shortcuts-dialog [data-close-dialog], #image-dialog [data-close-dialog]').forEach(button => {
    button.addEventListener('click', () => button.closest('dialog')?.close());
  });
  $('#toggle-history-search')?.addEventListener('click', () => {
    const wrap = $('#history-search-wrap');
    if (!wrap) return;
    wrap.hidden = !wrap.hidden;
    if (!wrap.hidden) $('#history-search').focus();
    else { $('#history-search').value = ''; renderSidebarHistory(); }
  });
  $('#history-search')?.addEventListener('input', renderSidebarHistory);
  window.addEventListener('resize', closeMobileSidebar);
  document.addEventListener('click', handleDelegatedClick);
  document.addEventListener('keydown', handleGlobalKeydown);
}

function isEditable(target) {
  return target instanceof HTMLElement && (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName));
}

function handleGlobalKeydown(event) {
  const meta = event.metaKey || event.ctrlKey;
  if (meta && event.key.toLowerCase() === 'k') { event.preventDefault(); openPalette(); return; }
  if (meta && event.key === ',') { event.preventDefault(); navigate('settings'); return; }
  if (meta && event.key.toLowerCase() === 'n') { event.preventDefault(); requestNewChat(); return; }
  if (meta && /^[1-9]$/.test(event.key)) {
    const target = NAV_SHORTCUTS[Number(event.key) - 1];
    if (target) { event.preventDefault(); navigate(target); }
  }
  if (event.key === 'Escape' && !isEditable(event.target)) {
    if (document.body.classList.contains('sidebar-open')) { event.preventDefault(); closeMobileSidebar(); }
  }
}

function messageIdFrom(element) {
  const direct = Number(element.dataset.messageId);
  if (Number.isSafeInteger(direct) && direct > 0) return direct;
  try {
    const url = new URL(element.getAttribute('href') || '', location.origin);
    const value = Number(url.searchParams.get('anchor'));
    if (Number.isSafeInteger(value) && value > 0) return value;
  } catch { /* not a message link */ }
  return null;
}

function handleDelegatedClick(event) {
  const target = event.target instanceof Element ? event.target : null;
  if (!target) return;
  const image = target.closest('a.open-image, .evidence-media img, .original-bubble figure img');
  if (image) {
    const url = image.getAttribute('href') || image.getAttribute('src');
    if (url) { event.preventDefault(); openImage(url, image.getAttribute('alt') || '聊天图片'); return; }
  }
  const watchEvidence = target.closest('.watch-evidence');
  if (watchEvidence) {
    event.preventDefault();
    const mid = Number(watchEvidence.dataset.messageId);
    const action = actions.get('watch-evidence');
    if (action && mid) action(mid);
    else if (mid) navigate('watches', { seed: mid });
    return;
  }
  const chatLink = target.closest('a.open-chat, a[data-message-id], button.open-chat');
  if (chatLink) {
    const mid = messageIdFrom(chatLink);
    if (mid) {
      event.preventDefault();
      const action = actions.get('open-chat');
      if (action) action(mid);
      else navigate('chats', { anchor: mid });
      return;
    }
  }
  const fileLink = target.closest('.open-artifact');
  if (fileLink) {
    event.preventDefault();
    const id = Number(fileLink.dataset.sourceId);
    const action = actions.get('open-file');
    if (action) action(id);
    else if (id) navigate('files', { file: id });
    return;
  }
  const collect = target.closest('.collect-evidence');
  if (collect) {
    event.preventDefault();
    const action = actions.get('collect');
    if (action) action(collect.dataset.sourceType, collect.dataset.sourceId);
    else toast('证据集合页面尚未载入。', 'warn');
    return;
  }
  const collection = target.closest('.open-collection');
  if (collection) {
    event.preventDefault();
    const id = collection.dataset.collectionId;
    const action = actions.get('open-collection');
    if (action) action(id);
    else navigate('collections', { collection: id });
    return;
  }
  const knowledge = target.closest('.open-knowledge');
  if (knowledge) {
    event.preventDefault();
    const action = actions.get('open-knowledge');
    if (action) action(knowledge.dataset.knowledgeId);
    else navigate('collections', { knowledge: knowledge.dataset.knowledgeId });
    return;
  }
  const reply = target.closest('.reply-trigger');
  if (reply) {
    event.preventDefault();
    const action = actions.get('reply-message');
    const mid = Number(reply.closest('[data-message-id]')?.dataset.messageId);
    if (action && mid) action(mid);
  }
}

export function requestNewChat() {
  closeMobileSidebar();
  if (app.current?.name === 'chat' && app.ready) { emit('session-new'); return; }
  app.pendingNewChat = true;
  if (app.current?.name !== 'chat') navigate('chat');
  else emit('session-new');
}

function renderStatusChips() {
  updateShellStatus();
}

/* ---------- command palette ---------- */
let paletteItems = [];
let paletteIndex = 0;
let paletteRecent = null;

const NAV_ITEMS = [
  ['home', '工作台', '运行状态与最近活动', 'home'],
  ['chat', '对话', '向聊天记录提问', 'chat'],
  ['chats', '聊天记录', '浏览 QQ / 微信原文', 'chats'],
  ['watches', '关注', '定期检查与独立线程', 'watch'],
  ['files', '资料 / 文件', '附件与解析正文', 'files'],
  ['collections', '证据集合', '保存原始来源引用', 'collection'],
  ['memory', '记忆', '长期形成的社交记忆', 'memory'],
  ['workspaces', '工作区', '持续调查与成果版本', 'workspace'],
  ['data', '数据与同步', '读取、实时与导入', 'data'],
  ['agents', '外部 Agent', 'MCP 授权与群聊学习', 'agents'],
  ['settings', '设置', '外观、模型与 QQ 连接', 'settings'],
];

async function paletteData() {
  const items = [];
  for (const [route, label, hint, iconName] of NAV_ITEMS) {
    items.push({ group: '页面', label, hint, iconName, run: () => navigate(route) });
  }
  items.push({ group: '操作', label: '新建对话', hint: '⌘N', iconName: 'plus', run: () => requestNewChat() });
  items.push({ group: '操作', label: '刷新 QQ / 微信消息', hint: '本机读取', iconName: 'refresh', run: () => void quickSync() });
  items.push({ group: '操作', label: '切换浅色 / 深色', hint: '外观', iconName: appliesDark() ? 'sun' : 'moon', run: () => toggleAppearance() });
  items.push({ group: '操作', label: '键盘快捷键', hint: '⌘ /', iconName: 'keyboard', run: () => $('#shortcuts-dialog')?.showModal() });
  const sessions = app.sessions.length ? app.sessions : [];
  for (const session of sessions.slice(0, 12)) {
    items.push({
      group: '最近对话', label: titleFromSessionLabel(session.label), hint: sessionStamp(session.label),
      iconName: 'chat', run: () => { navigate('chat'); setTimeout(() => emit('session-open', session.id), 80); },
    });
  }
  if (!sessions.length) {
    if (paletteRecent === null) {
      try {
        const data = await api('/api/chat-conversations?limit=8', { timeout: 8000 });
        paletteRecent = data.conversations || [];
      } catch { paletteRecent = []; }
    }
    for (const row of paletteRecent.slice(0, 10)) {
      items.push({
        group: '最近聊天', label: row.conversation, hint: (row.platform === 'qq' ? 'QQ' : '微信') + ' · ' + row.time,
        iconName: 'chats', run: () => navigate('chats', { platform: row.platform, conversation_id: row.conversation_id }),
      });
    }
  }
  return items;
}

function renderPalette() {
  const host = $('#palette-results');
  const query = ($('#palette-input')?.value || '').trim().toLocaleLowerCase();
  const filtered = paletteItems.filter(item => !query || (item.group + ' ' + item.label + ' ' + (item.hint || '')).toLocaleLowerCase().includes(query));
  if (paletteIndex >= filtered.length) paletteIndex = Math.max(0, filtered.length - 1);
  host.replaceChildren();
  if (!filtered.length) { host.append(h('div', { class: 'palette-empty', text: '没有匹配的功能或对话。' })); return; }
  let lastGroup = '';
  filtered.forEach((item, index) => {
    if (item.group !== lastGroup) { host.append(h('div', { class: 'palette-group', text: item.group })); lastGroup = item.group; }
    const button = h('button', {
      class: 'palette-item' + (index === paletteIndex ? ' active' : ''), type: 'button',
      role: 'option', 'aria-selected': String(index === paletteIndex),
      onclick: () => runPaletteItem(item),
    }, icon(item.iconName), h('span', { text: item.label }), item.hint ? h('small', { text: item.hint }) : null);
    host.append(button);
  });
  host.querySelector('.palette-item.active')?.scrollIntoView({ block: 'nearest' });
}

function runPaletteItem(item) {
  const dialog = $('#command-palette');
  if (dialog?.open) dialog.close();
  item.run?.();
}

export async function openPalette() {
  const dialog = $('#command-palette');
  if (!dialog) return;
  paletteItems = await paletteData();
  paletteIndex = 0;
  const input = $('#palette-input');
  if (input) input.value = '';
  renderPalette();
  if (!dialog.open) dialog.showModal();
  requestAnimationFrame(() => input?.focus());
}

function initPalette() {
  const input = $('#palette-input');
  const dialog = $('#command-palette');
  if (!input || !dialog) return;
  input.addEventListener('input', () => { paletteIndex = 0; renderPalette(); });
  input.addEventListener('keydown', event => {
    const filtered = paletteItems.filter(item => {
      const query = input.value.trim().toLocaleLowerCase();
      return !query || (item.group + ' ' + item.label + ' ' + (item.hint || '')).toLocaleLowerCase().includes(query);
    });
    if (event.key === 'ArrowDown') { event.preventDefault(); paletteIndex = Math.min(filtered.length - 1, paletteIndex + 1); renderPalette(); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); paletteIndex = Math.max(0, paletteIndex - 1); renderPalette(); }
    else if (event.key === 'Enter') { event.preventDefault(); if (filtered[paletteIndex]) runPaletteItem(filtered[paletteIndex]); }
  });
  dialog.addEventListener('close', () => { paletteRecent = null; });
}

export async function quickSync() {
  let stickers = false;
  try { stickers = localStorage.getItem('tulpa.loadStickers') === 'true'; } catch { /* local preference */ }
  try {
    await startJob('/api/sync', { load_stickers: stickers }, { label: '刷新消息' });
  } catch (error) { reportError(error); }
}

/* ---------- home view ---------- */
function greetingDate() {
  const now = new Date();
  const date = now.toLocaleDateString('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' });
  return date;
}

function homeGreeting(hour = new Date().getHours()) {
  if (hour < 6) return '夜深了';
  if (hour < 11) return '上午好';
  if (hour < 14) return '中午好';
  if (hour < 18) return '下午好';
  return '晚上好';
}

function platformCount(value) {
  return value ? fmtNumber(value) : '—';
}

function miniStatus(platform, status) {
  const labels = {
    running: ['刷新中', 'warn'], ok: ['已刷新', 'ok'], error: ['刷新失败', 'bad'], never: ['尚未刷新', ''],
    idle: ['待刷新', ''], unsupported: ['未适配', 'warn'],
  };
  return labels[status] || [status || '未知', ''];
}

function renderHome(root) {
  const overview = app.global.overview;
  const desktop = app.global.desktop;
  const mcp = app.global.mcp;
  const live = app.global.live;
  const watchCards = root._watchCards || [];
  const recent = root._recent || [];
  const refresh = root._refresh || null;
  const managed = root._managed || null;
  const settings = desktop?.settings || {};
  const counts = overview?.counts || {};
  const platforms = mcp?.data_platforms || {};
  const activeWatches = watchCards.filter(card => card.status !== 'paused');
  const modelText = settings.configured ? (settings.model || '已配置') : '未配置';
  const onebotText = settings.sender_url ? (settings.events_url ? 'HTTP + 实时事件' : '仅 HTTP') : '未配置';
  const livePlatforms = live?.platforms || [];
  const liveEnabled = livePlatforms.filter(item => item.status !== 'off');
  const receiverState = overview?.receiver?.state;
  const receiverLabels = { connected: '事件通道已连接', connecting: '正在连接', verifying: '正在核对账号', unconfigured: '未配置事件通道', stopped: '事件接收已停止', reconnecting: '正在重连' };
  const mcpConnections = mcp?.connections || [];
  const activeConnectionCount = mcpConnections.filter(row => !row.revoked).length;
  const lastSync = (refresh?.platforms || []).map(item => {
    const name = item.platform === 'qq' ? 'QQ' : '微信';
    const [label, tone] = miniStatus(item.platform, item.status);
    return { name, label, tone, detail: item.last_sync_at ? '最近成功 ' + fmtTime(item.last_sync_at) : item.detail || '尚无成功记录' };
  });
  const lastSyncText = lastSync.length ? lastSync.map(x => x.name + ' ' + x.label).join(' · ') : '尚无刷新记录';

  const page = h('div', { class: 'view-scroll' }, h('div', { class: 'page home-page' },
    h('header', { class: 'home-head' },
      h('div', { class: 'home-head-copy' },
        h('div', { class: 'home-eyebrow' }, greetingDate()),
        h('h2', { text: homeGreeting() + '，今天想从聊天里找什么？' }),
        h('p', { text: overview ? `本机已保存 ${fmtNumber(counts.messages || 0)} 条消息，外部 Agent 有 ${fmtNumber(counts.active_sessions || 0)} 个持续聊天会话正在运行。` : '正在读取本机服务状态…' })),
      h('div', { class: 'home-head-actions' },
        h('button', { class: 'primary-button', type: 'button', onclick: requestNewChat }, icon('plus'), '新对话'),
        h('button', { class: 'quiet-button', type: 'button', onclick: () => navigate('chats') }, icon('chats'), '浏览聊天'),
        h('button', { class: 'quiet-button', type: 'button', onclick: () => void quickSync() }, icon('refresh'), '刷新消息'))),
    h('section', { class: 'home-stats', 'aria-label': '本机概览' },
      h('div', { class: 'home-stat' }, h('span', { class: 'stat-label' }, icon('chat'), 'QQ 消息'), h('strong', { text: platformCount(platforms.qq) }), h('small', { text: platforms.qq ? '已导入本机记录' : '尚未导入' })),
      h('div', { class: 'home-stat' }, h('span', { class: 'stat-label' }, icon('chat'), '微信消息'), h('strong', { text: platformCount(platforms.wechat) }), h('small', { text: platforms.wechat ? '已导入本机记录' : '尚未导入' })),
      h('div', { class: 'home-stat' }, h('span', { class: 'stat-label' }, icon('agents'), 'MCP 授权'), h('strong', { text: fmtNumber(activeConnectionCount) }), h('small', { text: mcp?.running ? '服务运行中' : '服务未开启' })),
      h('div', { class: 'home-stat' }, h('span', { class: 'stat-label' }, icon('watch'), '关注中'), h('strong', { text: fmtNumber(activeWatches.length) }), h('small', { text: watchCards.length ? `${watchCards.length} 张关注卡` : '尚未创建' }))),
    h('div', { class: 'home-columns' },
      h('div', { class: 'home-col' },
        h('section', { class: 'section' },
          h('div', { class: 'section-head' }, h('h2', { text: '快速操作' }),
            h('div', { class: 'section-head-actions' }, h('button', { class: 'text-button', type: 'button', text: '全部页面', onclick: openPalette }))),
          h('div', { class: 'home-actions' },
            homeAction('chat', '继续对话', '从已有会话接着问', () => { navigate('chat'); }),
            homeAction('search', '查聊天记录', '按会话浏览 QQ / 微信原文', () => navigate('chats')),
            homeAction('database', '读取新消息', '选择账号、会话和日期', () => navigate('data', { section: 'read' })),
            homeAction('files', '找文件与资料', '按文件名、发送者或正文', () => navigate('files')))),
        h('section', { class: 'card home-recent' },
          h('div', { class: 'card-head' }, h('h3', { text: '最近聊天' }),
            h('div', { class: 'card-head-actions' }, h('button', { class: 'text-button', type: 'button', text: '打开', onclick: () => navigate('chats') }))),
          recent.length ? h('div', { class: 'list' }, recent.map(row => h('button', {
            class: 'list-row', type: 'button',
            onclick: () => navigate('chats', { platform: row.platform, conversation_id: row.conversation_id }),
          }, h('span', { class: 'avatar ' + row.platform }, h('span', { text: row.platform === 'qq' ? 'Q' : '微' })),
            h('span', { class: 'list-row-main' }, h('strong', { text: row.conversation || '未命名会话' }),
              h('p', { text: (row.sender ? row.sender + '：' : '') + (row.preview || '[消息]') })),
            h('span', { class: 'list-row-side' }, h('span', { class: 'muted', style: { fontSize: '9.6px' }, text: row.time?.slice(5, 16) || '' })))))
            : h('p', { class: 'home-empty-inline', text: '还没有已导入的聊天会话。先到“数据与同步”读取本机消息。' }))),
      h('div', { class: 'home-col' },
        h('section', { class: 'card' },
          h('div', { class: 'card-head' }, h('h3', { text: '运行状态' }),
            h('div', { class: 'card-head-actions' }, h('span', { class: 'badge ' + (app.global.ok ? 'ok' : 'warn'), text: app.global.ok ? '本机' : '连接中' }))),
          h('div', { class: 'home-status-list' },
            h('div', { class: 'home-status-row' }, statusDot(settings.configured ? 'ok' : ''), h('div', {}, h('strong', { text: '内置模型' }), h('small', { text: settings.configured ? modelText : '尚未填写 API 地址或密钥' })), h('button', { class: 'badge', type: 'button', text: settings.configured ? '已配置' : '去配置', onclick: () => navigate('settings', { section: 'model' }) })),
            h('div', { class: 'home-status-row' }, statusDot(settings.sender_url ? 'ok' : ''), h('div', {}, h('strong', { text: 'QQ / OneBot' }), h('small', { text: settings.sender_url ? (settings.sender_url + (settings.events_url ? ' · 事件已配置' : ' · 事件未配置')) : '未配置，本地聊天查询仍可用' })), h('button', { class: 'badge', type: 'button', text: '设置', onclick: () => navigate('settings', { section: 'onebot' }) })),
            h('div', { class: 'home-status-row' }, statusDot(liveEnabled.length ? (liveEnabled.some(p => ['degraded', 'retrying', 'client_stopped'].includes(p.status)) ? 'warn' : 'live') : ''), h('div', {}, h('strong', { text: '实时读取' }), h('small', { text: liveEnabled.length ? liveEnabled.map(p => (p.platform === 'qq' ? 'QQ' : '微信') + ' · ' + (p.detail || '监听中')).join('；') : '两项监听均已关闭' })), h('button', { class: 'badge', type: 'button', text: '查看', onclick: () => navigate('data', { section: 'live' }) })),
            h('div', { class: 'home-status-row' }, statusDot(mcp?.running ? 'ok' : ''), h('div', {}, h('strong', { text: '外部 Agent / MCP' }), h('small', { text: mcp?.running ? `${mcp.url || '本机服务'} · ${activeConnectionCount} 个有效连接` : (mcp?.error || '服务尚未开启') })), h('button', { class: 'badge', type: 'button', text: '管理', onclick: () => navigate('agents') })),
            managed ? h('div', { class: 'home-status-row' }, statusDot(managed.qq_ready ? 'ok' : managed.enabled ? 'warn' : ''), h('div', {}, h('strong', { text: 'SnowLuma 托管' }), h('small', { text: managed.enabled ? `${managed.phase_label || '托管中'}${managed.account ? ' · QQ ' + managed.account : ''}${managed.message ? ' · ' + managed.message : ''}` : '尚未启用 SL Docker 托管' })), h('button', { class: 'badge', type: 'button', text: '打开', onclick: () => navigate('settings', { section: 'snowluma' }) })) : null,
            receiverState ? h('div', { class: 'home-status-row' }, statusDot(receiverState === 'connected' ? 'ok' : receiverState === 'stopped' ? '' : 'warn'), h('div', {}, h('strong', { text: '实时事件通道' }), h('small', { text: (receiverLabels[receiverState] || receiverState) + (overview?.receiver?.note ? ' · ' + overview.receiver.note : '') })), null) : null)),
        h('section', { class: 'card' },
          h('div', { class: 'card-head' }, h('h3', { text: '关注中' }), h('div', { class: 'card-head-actions' }, h('button', { class: 'text-button', type: 'button', text: '管理', onclick: () => navigate('watches') }))),
          watchCards.length ? h('div', {}, watchCards.slice(0, 4).map(card => h('button', {
            class: 'home-watch-row', type: 'button', style: { width: '100%', border: '0', borderBottom: '1px solid var(--line-soft)', background: 'transparent', textAlign: 'left' },
            onclick: () => navigate('watches/' + card.id),
          }, h('div', {}, h('strong', { text: card.title }), h('small', { text: card.status === 'paused' ? '已暂停' : (card.error ? '回答未完成' : '最近检查 ' + relativeTime(card.last_checked_at)) })), h('span', { class: 'badge ' + (card.status === 'paused' ? '' : 'ok'), text: card.status === 'paused' ? '暂停' : '关注' })))) : h('p', { class: 'home-empty-inline', text: '还没有关注卡。可以从回答或任意聊天原文创建。' })),
        h('section', { class: 'card' },
          h('div', { class: 'card-head' }, h('h3', { text: '同步状态' }), h('div', { class: 'card-head-actions' }, h('button', { class: 'text-button', type: 'button', text: '数据页', onclick: () => navigate('data') }))),
          h('div', { class: 'home-status-list' },
            lastSync.length ? lastSync.map(item => h('div', { class: 'home-status-row' }, statusDot(item.tone === 'ok' ? 'ok' : item.tone === 'bad' ? 'bad' : item.tone === 'warn' ? 'warn' : ''), h('div', {}, h('strong', { text: item.name + ' · ' + item.label }), h('small', { text: item.detail })), null))
              : h('p', { class: 'home-empty-inline', text: lastSyncText })))))));

  return page;
}

function homeAction(iconName, title, description, onclick) {
  return h('button', { class: 'home-action', type: 'button', onclick },
    h('span', { class: 'action-icon' }, icon(iconName)),
    h('div', {}, h('strong', { text: title }), h('small', { text: description })));
}

registerView('home', {
  title: '工作台',
  subtitle: '本机聊天资料与运行状态',
  async mount(root) {
    let disposed = false;
    let loadSeq = 0;
    const host = h('div', { class: 'view-scroll' });
    root.append(host);
    clear(host);
    host.append(h('div', { class: 'page home-page' },
      h('div', { class: 'home-head' }, h('div', { class: 'home-head-copy' }, h('div', { class: 'home-eyebrow', text: '正在加载' }), h('h2', { text: '正在读取本机状态…' }))),
      h('div', { class: 'home-stats' }, skeletonBlock(2), skeletonBlock(2), skeletonBlock(2), skeletonBlock(2)),
      h('div', { class: 'home-columns' }, h('div', { class: 'home-col' }, skeletonBlock(5), skeletonBlock(4)), h('div', { class: 'home-col' }, skeletonBlock(6), skeletonBlock(4)))));

    async function load() {
      if (disposed) return;
      const token = ++loadSeq;
      const [overview, desktop, mcp, cards, recent, refresh, managed] = await Promise.allSettled([
        api('/api/terminal/overview', { timeout: 15000 }),
        api('/api/desktop/status', { timeout: 15000 }),
        api('/api/mcp', { timeout: 12000 }),
        api('/api/watch-cards', { timeout: 15000 }),
        api('/api/chat-conversations?limit=6', { timeout: 12000 }),
        api('/api/refresh-status', { timeout: 12000 }),
        api('/api/desktop/snowluma/managed', { timeout: 15000 }),
      ]);
      if (disposed || token !== loadSeq) return;
      const value = result => result.status === 'fulfilled' ? result.value : null;
      if (value(overview)) app.global.overview = value(overview);
      if (value(desktop)) app.global.desktop = value(desktop);
      if (value(mcp)) app.global.mcp = value(mcp);
      host._watchCards = value(cards)?.cards || [];
      host._recent = value(recent)?.conversations || [];
      host._refresh = value(refresh);
      host._managed = value(managed);
      updateShellStatus();
      setWatchCount(host._watchCards.filter(card => card.status !== 'paused').length);
      const page = renderHome(host);
      clear(host).append(page);
    }

    const offs = [
      on('data-changed', () => setTimeout(load, 250)),
      on('job-done', () => setTimeout(load, 350)),
    ];
    await load();
    return () => {
      disposed = true;
      loadSeq += 1;
      offs.forEach(off => off());
    };
  },
});

/* ---------- boot ---------- */
export async function bootApp() {
  initAppearance();
  initShell();
  initPalette();
  startStatusLoop();
  await attachRunningJobs();
  const deep = initialDeepLink();
  if (deep) history.replaceState(null, '', deep);
  else if (!location.hash) history.replaceState(null, '', '#/home');
  window.addEventListener('hashchange', () => { void renderRoute({ focus: true }); });
  await renderRoute({ force: true });
  app.ready = true;
  emit('ready', true);
}
