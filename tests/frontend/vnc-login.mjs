import { JSDOM } from 'jsdom';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = path.resolve(fileURLToPath(new URL('../../', import.meta.url)));
const web = path.join(root, 'web', 'desktop');
const html = fs.readFileSync(path.join(web, 'index.html'), 'utf8').replace(/<script type="module"[^>]*><\/script>/, '');
const dom = new JSDOM(html, { url: 'http://127.0.0.1:7861/', runScripts: 'outside-only', pretendToBeVisual: true });
const { window } = dom;
const document = window.document;

window.matchMedia = () => ({
  matches: false, media: '(prefers-color-scheme: dark)',
  addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
});
window.scrollTo = () => {};
window.requestAnimationFrame = callback => setTimeout(callback, 0);
window.cancelAnimationFrame = id => clearTimeout(id);
window.HTMLElement.prototype.scrollIntoView = () => {};
window.HTMLElement.prototype.scrollTo = function scrollTo() {};
window.CSS = window.CSS || {};
window.CSS.escape = value => String(value).replace(/[^a-zA-Z0-9_-]/g, ch => '\\' + ch);

for (const [key, value] of Object.entries({
  window, document, location: window.location, history: window.history,
  localStorage: window.localStorage, navigator: window.navigator, matchMedia: window.matchMedia,
  Node: window.Node, Element: window.Element, HTMLElement: window.HTMLElement, DocumentFragment: window.DocumentFragment,
  Event: window.Event, CustomEvent: window.CustomEvent, EventTarget: window.EventTarget,
  NodeList: window.NodeList, HTMLCollection: window.HTMLCollection, DOMParser: window.DOMParser,
  getComputedStyle: window.getComputedStyle, CSS: window.CSS,
  requestAnimationFrame: window.requestAnimationFrame, cancelAnimationFrame: window.cancelAnimationFrame,
  scrollTo: window.scrollTo, innerWidth: 900, innerHeight: 650,
  AbortSignal: globalThis.AbortSignal, AbortController: globalThis.AbortController,
})) {
  try { globalThis[key] = value; } catch { Object.defineProperty(globalThis, key, { value, configurable: true }); }
}

const status = {
  backend: 'docker', supported_backends: ['docker'],
  ok: true, enabled: false, phase: 'idle', phase_label: '空闲', available: true,
  consent_current: false, has_consent: false, version: '1.14.22', security_note: '',
  agreements: [], scope: [], login_available: true, qq_ready: false,
};
const json = data => ({
  ok: true, status: 200,
  headers: { get: name => String(name).toLowerCase() === 'content-type' ? 'application/json' : '' },
  async json() { return JSON.parse(JSON.stringify(data)); },
});
let loginPayload = null;
const requests = [];
window.fetch = async (input, options = {}) => {
  const url = new URL(String(input), 'http://127.0.0.1:7861/');
  requests.push((options.method || 'GET') + ' ' + url.pathname);
  if (url.pathname === '/api/desktop/snowluma/managed') return json(status);
  if (url.pathname === '/api/desktop/snowluma/managed/docker-login') return json(loginPayload);
  return json({});
};
globalThis.fetch = window.fetch;

const errors = [];
const check = (ok, message) => { if (!ok) errors.push(message); };
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const button = text => Array.from(document.querySelectorAll('button')).find(node => node.textContent.includes(text));

let popupCount = 0;
window.open = () => { popupCount += 1; return null; };
const nativeClicks = [];
window.HTMLAnchorElement.prototype.click = function click() { nativeClicks.push(this.href); };

const { mountSnowLuma } = await import(pathToFileURL(path.join(web, 'snowluma.js')).href);
const host = document.createElement('div');
document.body.append(host);
const snow = mountSnowLuma(host, { onSaved: async () => {} });
await wait(60);

check(button('打开扫码登录') && !button('打开扫码登录').hidden, 'login button should be visible');
check(button('打开扫码登录').disabled === false, 'login button should be enabled after initial refresh');

/* Native bridge: opening failed, but address/password must still be shown. */
let bridgeCalls = 0;
window.TulpaDesktopUI = {
  call: async action => {
    bridgeCalls += 1;
    check(action === 'open_qq_login', 'native bridge must be open_qq_login, got ' + action);
    return {
      ok: true, novnc_url: 'http://127.0.0.1:6080/vnc.html?token=a', password: 'p@ss',
      browser_opened: false, browser_error: '系统浏览器启动失败。',
    };
  },
};
button('打开扫码登录').click();
button('打开扫码登录').click(); // double click must not repeat the request
await wait(80);
const feedback = document.querySelector('.snow-feedback');
const loginBox = document.querySelector('.snow-login');
const address = loginBox.querySelector('input[aria-label="QQ 桌面登录地址"]');
const password = loginBox.querySelector('input[aria-label="VNC 密码"]');
const nativeOpen = button('在浏览器中打开 QQ 桌面') || Array.from(document.querySelectorAll('a')).find(node => node.textContent.includes('在浏览器中打开 QQ 桌面'));

check(bridgeCalls === 1, 'double click should only call the native bridge once, got ' + bridgeCalls);
check(feedback && !feedback.hidden, 'operation feedback should stay visible');
check(/未能自动打开系统浏览器/.test(feedback?.textContent || ''), 'failed open must be reported as failure');
check(/系统浏览器启动失败/.test(feedback?.textContent || ''), 'safe browser_error should be shown');
check(feedback?.className.includes('warn'), 'failure feedback should be warn-toned');
check(loginBox && !loginBox.hidden, 'login panel should be visible');
check(address?.value === 'http://127.0.0.1:6080/vnc.html?token=a', 'address should be shown read-only');
check(address?.readOnly === true, 'address must be read-only');
check(password?.value === 'p@ss', 'real password should be shown');
check(password?.type === 'password', 'password must stay hidden by default');
check(nativeOpen && nativeOpen.tagName === 'BUTTON' && !nativeOpen.hidden, 'native window should offer a bridge button, not a target=_blank link');
check(popupCount === 0, 'failed native open must not auto-open a page');
check(nativeClicks.length === 0, 'no anchor click loop is allowed');

/* Polling refresh must not overwrite the operation feedback. */
await snow.refresh();
await wait(30);
check(/未能自动打开系统浏览器/.test(feedback.textContent), 'polling must not overwrite the latest operation error');

/* Hiding login info clears address and password. */
button('隐藏登录信息').click();
check(loginBox.hidden, 'login panel should hide');
check(address.value === '', 'hidden login info must clear the address');
check(password.value === '', 'hidden login info must clear the password');
check(/已隐藏登录地址与密码/.test(feedback.textContent), 'hiding should acknowledge cleanup');

/* Regular browser: keep a real safe loopback link, no automatic opening. */
delete window.TulpaDesktopUI;
loginPayload = { ok: true, novnc_url: 'http://127.0.0.1:6080/vnc.html?token=b', password: 'pw2' };
const clipboard = [];
Object.defineProperty(window.navigator, 'clipboard', {
  configurable: true,
  value: { writeText: async text => { clipboard.push(text); } },
});
button('打开扫码登录').click();
await wait(80);
const linkIndex = Array.from(document.querySelectorAll('a')).findIndex(node => node.textContent.includes('在浏览器中打开 QQ 桌面'));
const link = document.querySelectorAll('a')[linkIndex];
check(linkIndex >= 0 && !link.hidden, 'regular browser should show a real link');
check(link?.getAttribute('href') === 'http://127.0.0.1:6080/vnc.html?token=b', 'regular browser link must point at the safe loopback URL');
check(link?.target === '_blank' && /noopener/.test(link?.rel || ''), 'regular browser link must open safely');
check(!/已通过系统浏览器打开/.test(feedback.textContent), 'regular browser must not claim the system browser was opened');
check(popupCount === 0 && nativeClicks.length === 0, 'login rendering/polling must not auto-open pages');
await snow.refresh();
await wait(30);
check(popupCount === 0 && nativeClicks.length === 0, 'refresh must not auto-open pages');

button('复制地址').click();
await wait(40);
check(clipboard[0] === 'http://127.0.0.1:6080/vnc.html?token=b', 'copy button should copy the login address');
check(/已复制登录地址/.test(feedback.textContent), 'copy success should be reported');

/* A late bridge reply after reset must not write into the hidden panel. */
let resolveBridge;
window.TulpaDesktopUI = { call: () => new Promise(resolve => { resolveBridge = resolve; }) };
button('打开扫码登录').click();
await wait(10);
snow.reset();
await wait(10);
resolveBridge({ ok: true, novnc_url: 'http://127.0.0.1:6080/vnc.html?late=1', password: 'late', browser_opened: true });
await wait(50);
check(loginBox.hidden, 'late native reply must not reopen a reset login panel');
check(address.value === '' && password.value === '', 'late native reply must not restore cleared credentials');
check(!/已通过系统浏览器打开/.test(feedback.textContent), 'late native reply must not report success after reset');

snow.destroy();
check(host.children.length === 0, 'destroy should remove the mounted UI');

if (errors.length) {
  console.error('FAIL');
  for (const error of errors) console.error('- ' + error);
  process.exit(1);
}
console.log('VNC login behavior OK');
