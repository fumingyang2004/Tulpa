import { JSDOM } from 'jsdom';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = path.resolve(fileURLToPath(new URL('../../', import.meta.url)));
const web = path.join(root, 'web', 'desktop');
const html = fs.readFileSync(path.join(web, 'index.html'), 'utf8').replace(/<script type="module"[^>]*><\/script>/, '');
const dom = new JSDOM(html, { url: 'http://127.0.0.1:7861/', runScripts: 'outside-only', pretendToBeVisual: true });
const { window } = dom;

const errors = [];
window.addEventListener('error', event => errors.push('window.error: ' + (event.error?.stack || event.message)));
window.addEventListener('unhandledrejection', event => errors.push('unhandledrejection: ' + (event.reason?.stack || event.reason)));

window.matchMedia = () => ({
  matches: false,
  media: '(prefers-color-scheme: dark)',
  addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
});
window.scrollTo = () => {};
window.requestAnimationFrame = callback => setTimeout(callback, 0);
window.cancelAnimationFrame = id => clearTimeout(id);
window.HTMLElement.prototype.scrollIntoView = () => {};
window.HTMLElement.prototype.scrollTo = function scrollTo() {};
window.CSS = window.CSS || {};
window.CSS.escape = value => String(value).replace(/[^a-zA-Z0-9_-]/g, ch => '\\' + ch);
if (!window.DialogElement && window.HTMLDialogElement) {
  window.HTMLDialogElement.prototype.showModal = function showModal() { this.open = true; };
  window.HTMLDialogElement.prototype.close = function close() { this.open = false; this.dispatchEvent(new window.Event('close')); };
}

const json = (window, data, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  headers: { get: name => String(name).toLowerCase() === 'content-type' ? 'application/json' : '' },
  async json() { return JSON.parse(JSON.stringify(data)); },
  async text() { return JSON.stringify(data); },
});

import { responses, permissions } from './fixtures.mjs';

const requests = [];
const captured = [];
window.fetch = async (input, options = {}) => {
  const url = new URL(String(input), 'http://127.0.0.1:7861/');
  requests.push((options.method || 'GET') + ' ' + url.pathname + url.search);
  captured.push({ url: url.pathname + url.search, method: options.method || 'GET', headers: options.headers || {}, body: options.body });
  if (url.pathname === '/legacy/gradio_api/queue/join') return json(window, { event_id: 'evt-1' });
  if (url.pathname === '/legacy/gradio_api/queue/data') {
    const payload = 'data: ' + JSON.stringify({ event_id: 'evt-1', msg: 'process_completed', success: true, output: { data: [{ choices: [], value: null }] } }) + '\n\n';
    const stream = new globalThis.ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(payload));
        controller.close();
      },
    });
    return { ok: true, status: 200, headers: { get: () => 'text/event-stream' }, body: stream };
  }
  const result = responses(url.pathname);
  return json(window, result.data, result.status || 200);
};

for (const [key, value] of Object.entries({
  window, document: window.document, location: window.location, history: window.history,
  localStorage: window.localStorage, navigator: window.navigator, matchMedia: window.matchMedia,
  Node: window.Node, Element: window.Element, HTMLElement: window.HTMLElement, DocumentFragment: window.DocumentFragment,
  Event: window.Event, CustomEvent: window.CustomEvent, EventTarget: window.EventTarget,
  HashChangeEvent: window.HashChangeEvent, KeyboardEvent: window.KeyboardEvent,
  NodeList: window.NodeList, HTMLCollection: window.HTMLCollection, DOMParser: window.DOMParser,
  getComputedStyle: window.getComputedStyle, CSS: window.CSS,
  requestAnimationFrame: window.requestAnimationFrame, cancelAnimationFrame: window.cancelAnimationFrame,
  scrollTo: window.scrollTo, innerWidth: 900, innerHeight: 650,
  matchMedia: window.matchMedia, AbortSignal: globalThis.AbortSignal, AbortController: globalThis.AbortController,
  fetch: window.fetch,
})) {
  try { globalThis[key] = value; } catch { Object.defineProperty(globalThis, key, { value, configurable: true }); }
}

process.on('unhandledRejection', reason => errors.push('process unhandledRejection: ' + (reason?.stack || reason)));

await import(pathToFileURL(path.join(web, 'desktop.js')).href);
await new Promise(resolve => setTimeout(resolve, 700));

function snapshot(label) {
  const title = window.document.querySelector('#view-title')?.textContent;
  const root = window.document.querySelector('#view-root');
  const text = (root?.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 110);
  console.log(`${label}: title=${JSON.stringify(title)} root=${root?.children.length || 0} text=${JSON.stringify(text)}`);
  if (!root || !root.children.length) errors.push(label + ': view root is empty');
  if ((root?.textContent || '').includes('页面暂时无法打开') || (root?.textContent || '').includes('前端未能启动')) {
    errors.push(label + ': error state rendered');
  }
}

snapshot('boot/home');
const core = await import(pathToFileURL(path.join(web, 'app.js')).href);
for (const route of ['chat', 'chats', 'files', 'collections', 'memory', 'workspaces', 'watches', 'data', 'agents', 'settings', 'home']) {
  core.navigate(route);
  await new Promise(resolve => setTimeout(resolve, 550));
  snapshot(route);
}

const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const byText = (selector, text) => Array.from(window.document.querySelectorAll(selector)).find(node => node.textContent.includes(text));
const closeDialogs = () => {
  for (const dialog of window.document.querySelectorAll('dialog[open]')) dialog.close();
  for (const popover of window.document.querySelectorAll('.popover')) popover.remove();
};

// Settings appearance interaction.
core.navigate('settings');
await wait(350);
const darkButton = byText('.settings-section[data-section="appearance"] .segmented button', '深色');
if (!darkButton) errors.push('settings: appearance dark button missing');
else {
  darkButton.click();
  if (window.document.documentElement.dataset.theme !== 'dark') errors.push('settings: dark theme not applied');
}

// Data read section renders real controls and reports validation.
core.navigate('data', { section: 'read' });
await wait(500);
if (window.document.querySelectorAll('.data-section[data-section="read"] .read-platform').length !== 2) errors.push('data: read panels missing');
const readButton = byText('.data-section[data-section="read"] .primary-button', '按以上设置读取');
if (readButton) {
  readButton.click();
  await wait(30);
  const status = window.document.querySelector('.data-section[data-section="read"] [data-read-status]');
  if (!status || !/账号|平台|条数/.test(status.textContent)) errors.push('data: read validation did not report a reason');
}
const sourceSelect = window.document.querySelector('[data-native-source="qq"]');
if (!sourceSelect || sourceSelect.options.length !== 3) errors.push('data: QQ source selector missing');
const sourceInfo = window.document.querySelector('.native-source-card .native-source-info')?.textContent || '';
if (!/本机数据库/.test(sourceInfo) || !/需要本机数据库密钥/.test(sourceInfo)) errors.push('data: native source status not rendered');
const sourceSave = byText('.native-source-card .quiet-button', '保存 QQ 来源');
if (sourceSave) {
  sourceSave.click();
  await wait(160);
  const put = captured.find(item => item.method === 'PUT' && item.url === '/api/terminal/native/qq');
  if (!put) errors.push('data: QQ source save did not issue PUT');
  else {
    let payload = null;
    try { payload = JSON.parse(put.body || '{}'); } catch { payload = null; }
    if (!payload || payload.source !== 'auto' || payload.data_root !== '/tmp/qq-data') errors.push('data: QQ source save did not preserve roots');
  }
} else errors.push('data: QQ source save button missing');

// api() must send JSON (and an empty object) for bodyless writes.
await core.api('/api/empty-write', { method: 'DELETE' });
const emptyWrite = captured.at(-1);
if (!emptyWrite || emptyWrite.method !== 'DELETE' || emptyWrite.body !== '{}') errors.push('api: bodyless DELETE did not send empty JSON');
if (emptyWrite && emptyWrite.headers['Content-Type'] !== 'application/json') errors.push('api: bodyless DELETE missing JSON content type');

// MCP create form renders every permission including advanced flags.
core.navigate('agents');
await wait(400);
const createTab = byText('.pill-tabs button', '新建授权');
createTab?.click();
await wait(50);
if (window.document.querySelectorAll('.mcp-permission input').length < permissions.length) errors.push('mcp: permission catalog incomplete');
const chatLabel = Array.from(window.document.querySelectorAll('.mcp-permission')).find(node => node.textContent.trim().startsWith('持续聊天'));
const sendLabel = Array.from(window.document.querySelectorAll('.mcp-permission')).find(node => node.textContent.trim().startsWith('直接发送 QQ 消息'));
const imagesLabel = Array.from(window.document.querySelectorAll('.mcp-permission')).find(node => node.textContent.trim().startsWith('聊天看图和表情目录'));
if (!chatLabel || !sendLabel || !imagesLabel) errors.push('mcp: chat/send/media permissions missing');
else {
  chatLabel.querySelector('input').click();
  if (!sendLabel.querySelector('input').checked) errors.push('mcp: enabling chat did not enable send');
  if (imagesLabel.querySelector('input').disabled) errors.push('mcp: chat media stayed disabled after enabling chat');
  sendLabel.querySelector('input').click();
  if (chatLabel.querySelector('input').checked) errors.push('mcp: unchecking send left chat enabled');
  if (!imagesLabel.querySelector('input').disabled || imagesLabel.querySelector('input').checked) errors.push('mcp: unchecking send left chat media enabled');
}
const createSubmit = byText('.mcp-create-form button', '创建连接');
if (createSubmit) {
  createSubmit.click();
  await wait(30);
  const message = window.document.querySelector('.mcp-create-form .mcp-warning')?.textContent || '';
  if (!message.includes('连接名称')) errors.push('mcp: create validation did not report missing name');
}

// Watch and workspace creation dialogs.
core.navigate('watches');
await wait(350);
byText('.watch-list-page .primary-button', '新建关注')?.click();
await wait(80);
if (!window.document.querySelector('dialog[open]')?.textContent.includes('新建关注')) errors.push('watch: create dialog did not open');
closeDialogs();

core.navigate('workspaces');
await wait(350);
byText('.ws-list-page .primary-button', '新建工作区')?.click();
await wait(120);
if (!window.document.querySelector('dialog[open]')?.textContent.includes('新建工作区')) errors.push('workspace: create dialog did not open');
closeDialogs();

core.navigate('collections');
await wait(350);
byText('.page-head-actions .primary-button', '新建集合')?.click();
await wait(50);
if (!window.document.querySelector('dialog[open]')?.textContent.includes('新建证据集合')) errors.push('collections: create dialog did not open');
closeDialogs();

// Chat range and intensity controls.
core.navigate('chat');
await wait(450);
if (!window.document.querySelector('#chat-messages')) errors.push('chat: thread did not mount');
console.log('chat diagnostics:', JSON.stringify({
  note: window.document.querySelector('.chat-welcome-note')?.textContent,
  rangeDisabled: window.document.querySelector('.chat-context .chip')?.disabled,
  intensityDisabled: Array.from(window.document.querySelectorAll('.composer-chip')).map(node => [node.textContent.trim(), node.disabled]),
  model: window.document.querySelector('.model-label')?.textContent,
  topbar: window.document.querySelector('.chat-topbar')?.innerHTML?.slice(0, 300),
  chips: Array.from(window.document.querySelectorAll('.chip')).map(node => node.outerHTML.slice(0, 80)),
}));
window.document.querySelector('.composer-toolbar button.chip')?.click();
await wait(200);
if (!window.document.querySelector('dialog[open]')?.textContent.includes('搜索范围')) errors.push('chat: range dialog did not open');
closeDialogs();
const intensity = byText('.composer-chip', '深入');
intensity?.click();
await wait(50);
if (!window.document.querySelector('.popover')) errors.push('chat: intensity popover did not open');
closeDialogs();

// Router: cleanup on navigation, stale async mounts, and thrown mounts.
let cleanups = 0;
core.registerView('smoke-cleanup', {
  title: '测试清理',
  mount(root) {
    root.append(window.document.createTextNode('CLEANUP'));
    return () => { cleanups += 1; };
  },
});
core.navigate('smoke-cleanup');
await wait(60);
core.navigate('home');
await wait(60);
if (cleanups !== 1) errors.push('router: mount cleanup was not invoked (got ' + cleanups + ')');

let staleCleanups = 0;
core.registerView('smoke-slow', {
  title: '慢页面',
  async mount(root) {
    await wait(170);
    root.append(window.document.createTextNode('STALE'));
    return () => { staleCleanups += 1; };
  },
});
core.registerView('smoke-fast', {
  title: '快页面',
  async mount(root) {
    root.append(window.document.createTextNode('FAST'));
    return () => {};
  },
});
core.navigate('smoke-slow');
await wait(20);
core.navigate('smoke-fast');
await wait(260);
const routedText = window.document.querySelector('#view-root')?.textContent || '';
if (!routedText.includes('FAST') || routedText.includes('STALE')) errors.push('router: stale mount wrote into the active page (' + JSON.stringify(routedText) + ')');
if (staleCleanups !== 1) errors.push('router: stale mount cleanup not invoked (got ' + staleCleanups + ')');

// Same-view race: a slow first mount owns module-level ui; a second mount must
// wait for its late cleanup, and a superseded queued mount must be skipped.
let raceMounts = 0;
let raceUI = null;
const raceCleanups = [];
let releaseRaceMount;
const raceGate = new Promise(resolve => { releaseRaceMount = resolve; });
core.registerView('smoke-race', {
  title: '同类竞态',
  async mount(root) {
    const seq = ++raceMounts;
    const tab = window.document.createElement('button');
    tab.textContent = '语音';
    const section = window.document.createElement('div');
    section.textContent = 'VOICE-' + seq;
    section.hidden = true;
    tab.addEventListener('click', () => {
      // Mirrors data.js: stale handlers must not act on the current page.
      if (raceUI && raceUI.seq === seq) section.hidden = false;
    });
    root.append(tab, section);
    if (seq === 1) await raceGate;
    raceUI = { seq, section };
    return () => {
      raceCleanups.push(seq);
      if (raceUI && raceUI.seq === seq) raceUI = null;
    };
  },
});
core.navigate('smoke-race');
await wait(20);
core.navigate('home');
await wait(20);
core.navigate('smoke-race'); // queued behind slow mount 1
await wait(10);
core.navigate('home');
await wait(10);
core.navigate('smoke-race'); // newest queued mount; previous queued one expired
await wait(30);
if (raceMounts !== 1) errors.push('router: queued same-view mount started before the stale mount settled (got ' + raceMounts + ')');
releaseRaceMount();
await wait(180);
if (raceMounts !== 2) errors.push('router: expired queued same-view mount was not skipped (mounts=' + raceMounts + ')');
if (!raceUI || raceUI.seq !== 2) errors.push('router: stale same-view cleanup clobbered the active instance (ui=' + JSON.stringify(raceUI && raceUI.seq) + ')');
if (raceCleanups.join(',') !== '1') errors.push('router: same-view cleanup ran wrong times (' + raceCleanups.join(',') + ')');
const raceTab = window.document.querySelector('#view-root button');
raceTab?.click();
const raceSection = window.document.querySelector('#view-root .route-host div');
if (!raceSection || raceSection.hidden) errors.push('router: interaction on the newest same-view instance is broken (voiceVisible=' + String(!!raceSection && !raceSection.hidden) + ')');

core.registerView('smoke-throw', {
  title: '错误页面',
  async mount(root) {
    root.append(window.document.createTextNode('PARTIAL'));
    throw new Error('smoke failure');
  },
});
core.navigate('smoke-throw');
await wait(60);
if (!(window.document.querySelector('#view-root')?.textContent || '').includes('这个页面暂时无法打开')) errors.push('router: thrown mount did not render the error state');
core.navigate('home');
await wait(60);
if ((window.document.querySelector('#view-root')?.textContent || '').includes('PARTIAL')) errors.push('router: partial mount DOM survived navigation');
const homeCopy = window.document.querySelector('.home-head p')?.textContent || '';
if (!homeCopy.includes('外部 Agent 有')) errors.push('home: active sessions label still implies watches');

core.navigate('home');
await wait(300);
console.log('requests:', requests.length, 'errors:', errors.length);
if (errors.length) {
  for (const error of errors) console.error('ERROR:', error);
  process.exitCode = 1;
}
window.close();
setTimeout(() => process.exit(process.exitCode || 0), 50);
