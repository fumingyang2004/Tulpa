'use strict';

/* Application boot and settings: appearance, model, QQ connection, desktop and about. */
import {
  h, icon, api, app, registerView, navigate, setViewTitle, bootApp, getAppearance, setAppearance, on,
  toast, errorMessage, fieldLabel, checkbox, segmented, emptyState, loadingState, fmtFullTime,
} from './app.js';
import { mountSnowLuma } from './snowluma.js';

import './chat.js';
import './live.js';
import './watch.js';
import './data.js';
import './artifacts.js';
import './collections.js';
import './tulpa.js';
import './workspaces.js';
import './mcp.js';

let ui = null;
let cleanup = [];
let status = null;
let preferences = null;
let snow = null;
let savingModel = false;
let savingOneBot = false;

const SECTION_NAMES = {
  appearance: '外观', model: '模型', onebot: 'QQ 连接', snowluma: 'SL 托管', desktop: '桌面', about: '关于',
};

function activate(section) {
  if (!ui) return;
  if (!SECTION_NAMES[section]) section = 'appearance';
  ui.nav.querySelectorAll('button').forEach(button => {
    if (button.dataset.section === section) button.setAttribute('aria-current', 'true');
    else button.removeAttribute('aria-current');
  });
  ui.content.querySelectorAll('.settings-section').forEach(node => {
    node.classList.toggle('active', node.dataset.section === section);
  });
}

function fillOneBot(settings) {
  if (!ui) return;
  ui.senderUrl.value = settings.sender_url || '';
  ui.senderToken.value = '';
  ui.senderToken.placeholder = settings.has_sender_token ? '已保存 · 留空保留' : '访问 Token（如接口需要）';
  ui.eventsUrl.value = settings.events_url || '';
  ui.eventsToken.value = '';
  ui.eventsToken.placeholder = settings.has_events_token ? '已保存 · 留空保留' : 'WebSocket 节点 Token';
  ui.onebotCurrent.textContent = settings.sender_url
    ? `当前保存的 HTTP：${settings.sender_url}${settings.events_url ? '；事件：' + settings.events_url : '；未配置事件连接。'}`
    : '尚未配置 OneBot。本地聊天查询不受影响。';
  if (settings.managed_onebot) ui.onebotCurrent.textContent += ' 当前由 SL 托管提供连接。';
}

function fillSettings(settings) {
  if (!ui || !settings) return;
  status = { ...(status || {}), settings };
  ui.apiBase.value = settings.api_base || '';
  ui.model.value = settings.model || '';
  ui.apiKey.value = '';
  ui.apiKey.required = !settings.has_api_key;
  ui.apiKey.placeholder = settings.has_api_key ? '已保存 · 留空保留现有密钥' : '输入 API Key';
  ui.keyHint.textContent = settings.has_api_key ? '密钥已保存在本机，不会回显。' : '密钥不会随发布包分发。';
  ui.modelState.textContent = settings.configured ? '已配置 · ' + (settings.model || '') : '尚未配置';
  ui.modelState.className = 'badge ' + (settings.configured ? 'ok' : 'warn');
  fillOneBot(settings);
}

async function loadStatus() {
  const [loaded, prefs] = await Promise.all([
    api('/api/desktop/status', { timeout: 15000 }),
    api('/api/desktop/preferences', { timeout: 12000 }),
  ]);
  status = loaded;
  preferences = prefs;
  if (ui) {
    fillSettings(loaded.settings);
    ui.background.input.checked = Boolean(prefs.background);
    ui.desktopPrivilege.hidden = !loaded.owned;
    ui.storage.textContent = `Tulpa ${loaded.version} · 数据保存在 ${loaded.storage}`;
    ui.version.textContent = loaded.version || '—';
    ui.storagePath.textContent = loaded.storage || '—';
    ui.productLine.textContent = `${loaded.product || 'Tulpa'} ${loaded.version || ''} · ${loaded.app || 'ChatWeave'}`;
    ui.managedNote.hidden = !loaded.owned;
  }
}

async function saveModel() {
  if (!ui || savingModel) return;
  savingModel = true;
  ui.modelSave.disabled = true;
  ui.modelStatus.textContent = '正在保存…';
  ui.modelStatus.className = 'status-line';
  try {
    const body = { api_base: ui.apiBase.value.trim(), model: ui.model.value.trim() };
    if (ui.apiKey.value) body.api_key = ui.apiKey.value;
    if (!body.api_base) throw new Error('请填写 API 地址。');
    if (!/^https?:\/\//i.test(body.api_base)) throw new Error('API 地址须以 http:// 或 https:// 开头。');
    if (!body.model) throw new Error('请填写模型名称。');
    const settings = await api('/api/desktop/settings', { method: 'PUT', body });
    fillSettings(settings);
    ui.modelStatus.textContent = '模型设置已保存，对下一次提问生效。';
    ui.modelStatus.className = 'status-line success';
    toast('模型设置已保存。', 'success');
  } catch (error) {
    ui.modelStatus.textContent = errorMessage(error, '模型设置未能保存。');
    ui.modelStatus.className = 'status-line error';
  } finally {
    savingModel = false;
    ui.modelSave.disabled = false;
  }
}

async function testOneBot() {
  if (!ui) return;
  ui.onebotTest.disabled = true;
  ui.onebotStatus.textContent = '正在检测已保存的 OneBot 连接…';
  ui.onebotStatus.className = 'status-line';
  try {
    const result = await api('/api/desktop/onebot/test', { method: 'POST', body: {}, timeout: 25000 });
    ui.onebotStatus.textContent = result.message || (result.ok ? '连接检测通过。' : '连接检测未通过。');
    ui.onebotStatus.className = 'status-line ' + (result.ok ? 'success' : 'warn');
  } catch (error) {
    ui.onebotStatus.textContent = errorMessage(error, 'OneBot 检测未完成。');
    ui.onebotStatus.className = 'status-line error';
  } finally {
    ui.onebotTest.disabled = false;
  }
}

async function saveOneBot() {
  if (!ui || savingOneBot) return;
  savingOneBot = true;
  ui.onebotSave.disabled = true;
  ui.onebotStatus.textContent = '正在保存连接并检测…';
  ui.onebotStatus.className = 'status-line';
  try {
    const body = {
      sender_url: ui.senderUrl.value.trim(),
      sender_token: ui.senderToken.value,
      events_url: ui.eventsUrl.value.trim(),
      events_token: ui.eventsToken.value,
    };
    if (body.sender_url && !/^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?(\/|$)/i.test(body.sender_url)) {
      throw new Error('QQ 发送接口须为本机 localhost 地址。');
    }
    if (body.events_url && !/^wss?:\/\//i.test(body.events_url)) throw new Error('实时事件地址须使用 ws:// 或 wss://。');
    const settings = await api('/api/desktop/settings', { method: 'PUT', body });
    fillSettings(settings);
    ui.onebotStatus.textContent = '连接设置已保存。';
    ui.onebotStatus.className = 'status-line success';
    await testOneBot();
    toast('OneBot 设置已保存。', 'success');
  } catch (error) {
    ui.onebotStatus.textContent = errorMessage(error, 'OneBot 设置未能保存。');
    ui.onebotStatus.className = 'status-line error';
  } finally {
    savingOneBot = false;
    ui.onebotSave.disabled = false;
  }
}

async function saveBackground() {
  if (!ui) return;
  try {
    await api('/api/desktop/preferences', { method: 'PUT', body: { background: ui.background.input.checked } });
    toast('桌面偏好已保存。', 'success');
  } catch (error) { toast(errorMessage(error, '桌面偏好未能保存。'), 'bad'); }
}

function buildSettings() {
  const appearance = segmented([
    ['system', '跟随系统'], ['light', '浅色'], ['dark', '深色 · 夜间'],
  ], getAppearance(), value => void setAppearance(value), { 'aria-label': '外观模式' });
  const appearanceNote = h('p', { class: 'field-hint', text: window.TulpaDesktopUI
    ? '选择会保存在项目数据中，并同步原生窗口与标题栏。'
    : '选择保存在此浏览器；原生窗口由系统保存。' });
  const appearanceSection = h('section', { class: 'settings-section', dataset: { section: 'appearance' } },
    h('div', { class: 'settings-card' },
      h('div', { class: 'settings-card-head' }, h('h3', { text: '外观' }), h('p', { text: '浅色、深色或跟随系统。所有页面和对话内容使用同一套语义颜色。' })),
      h('div', { class: 'settings-card-body' },
        h('div', { class: 'settings-row' }, h('div', { class: 'settings-row-copy' }, h('strong', { text: '主题' }), h('small', { text: '标题栏、弹窗、消息和状态颜色会同步切换。' })), appearance),
        appearanceNote,
        h('p', { class: 'settings-footnote', text: '外观设置会自动保存，切换只影响界面显示。' }))));

  const apiBase = h('input', { type: 'url', maxLength: 2048, placeholder: '例如 https://api.deepseek.com', spellcheck: 'false' });
  const apiKey = h('input', { type: 'password', maxLength: 4096, autocomplete: 'new-password' });
  const model = h('input', { type: 'text', maxLength: 200, placeholder: '例如 deepseek-flash', spellcheck: 'false' });
  const keyHint = h('small', { class: 'field-hint' });
  const modelState = h('span', { class: 'badge', text: '正在读取' });
  const modelStatus = h('p', { class: 'status-line', role: 'status' });
  const modelSave = h('button', { class: 'primary-button', type: 'button', text: '保存模型设置', onclick: () => void saveModel() });
  const modelSection = h('section', { class: 'settings-section', dataset: { section: 'model' } },
    h('div', { class: 'settings-card' },
      h('div', { class: 'settings-card-head' }, h('h3', { text: '内置聊天模型' }), h('p', { text: '内置对话和外部 Agent 共用本机资料。只连接外部 Agent 时，无需填写模型 API Key。' })),
      h('div', { class: 'settings-card-body' },
        h('div', { class: 'row between', style: { marginBottom: '8px' } }, h('span', { class: 'field-hint', text: '当前状态' }), modelState),
        fieldLabel('API 地址', apiBase, '填写基础地址，无需添加 /chat/completions。'),
        fieldLabel('API Key', apiKey),
        keyHint,
        fieldLabel('模型名称', model, '须与服务商提供的模型 ID 一致；接口需支持流式输出与工具调用。'),
        h('div', { class: 'form-actions' }, modelSave, modelStatus))),
    h('p', { class: 'settings-footnote', text: '密钥只保存在项目本机的 .env 中；前端不会读取或回显已保存的密钥。' }));

  const senderUrl = h('input', { type: 'url', maxLength: 2048, placeholder: 'http://127.0.0.1:3000' });
  const senderToken = h('input', { type: 'password', maxLength: 4096, autocomplete: 'new-password' });
  const eventsUrl = h('input', { type: 'url', maxLength: 2048, placeholder: 'ws://127.0.0.1:3001' });
  const eventsToken = h('input', { type: 'password', maxLength: 4096, autocomplete: 'new-password' });
  const onebotCurrent = h('p', { class: 'field-hint' });
  const onebotStatus = h('p', { class: 'status-line onebot-status', role: 'status' });
  const onebotSave = h('button', { class: 'primary-button', type: 'button', text: '保存并检测', onclick: () => void saveOneBot() });
  const onebotTest = h('button', { class: 'quiet-button', type: 'button', text: '检测已保存的连接', onclick: () => void testOneBot() });
  const snowMount = h('div');
  const onebotSection = h('section', { class: 'settings-section', dataset: { section: 'onebot' } },
    h('div', { class: 'settings-card' },
      h('div', { class: 'settings-card-head' }, h('h3', { text: 'QQ 扩展 / OneBot（可选）' }), h('p', { text: '用于实时群聊、QQ 回复、群管理和外部 Agent 的群资料读取。本地聊天检索不受影响。' })),
      h('div', { class: 'settings-card-body' },
        h('div', { class: 'form-actions' },
          h('button', { class: 'quiet-button compact', type: 'button', onclick: () => activate('snowluma') }, icon('database'), '打开 SL 托管'),
          h('span', { class: 'field-hint', text: '推荐通过 SL 托管连接 QQ：macOS 使用 Docker（SL Docker），Windows 使用本机 SnowLuma；下方手动连接用于已有 OneBot。' })),
        h('details', { class: 'data-details' },
          h('summary', { text: '高级设置：手动配置连接' }),
          fieldLabel('本机 OneBot 地址', senderUrl, '仅允许本机 localhost。'),
          fieldLabel('访问 Token', senderToken, '只保存在本机，不提供给外部 Agent。留空保留已存 Token。'),
          fieldLabel('实时事件地址 · 持续群聊', eventsUrl, '在 SnowLuma 开启正向 WebSocket 服务。'),
          fieldLabel('事件 Token', eventsToken, '可能与 HTTP Token 不同；留空保留。'),
          h('div', { class: 'form-actions' }, onebotSave, onebotTest),
          onebotStatus),
        onebotCurrent)));

  const snowSection = h('section', { class: 'settings-section', dataset: { section: 'snowluma' } },
    h('div', { class: 'settings-card' },
      h('div', { class: 'settings-card-head' }, h('h3', { text: 'SL 托管（SnowLuma）' }), h('p', { text: 'macOS 使用 SL Docker，Windows 使用本机 SnowLuma，Linux 可选择。检测运行环境、阅读并同意协议后开始托管；停止或撤销会保留登录数据。' })),
      h('div', { class: 'settings-card-body' }, snowMount)));

  const background = checkbox('关闭窗口后留在托盘，继续提供服务', false);
  background.input.addEventListener('change', () => void saveBackground());
  const managedNote = h('p', { class: 'field-hint', id: 'desktop-managed-note', text: '当前窗口由 Tulpa 桌面服务启动；托盘行为由原生壳控制。' });
  const desktopPrivilege = h('div', { class: 'settings-card' },
    h('div', { class: 'settings-card-head' }, h('h3', { text: '桌面与托盘' }), h('p', { text: '仅桌面服务启动的窗口可以设置后台行为。' })),
    h('div', { class: 'settings-card-body' }, background, managedNote));
  const storage = h('p', { class: 'field-hint' });
  const desktopSection = h('section', { class: 'settings-section', dataset: { section: 'desktop' } },
    desktopPrivilege,
    h('div', { class: 'settings-card' },
      h('div', { class: 'settings-card-head' }, h('h3', { text: '本机数据与诊断' }), h('p', { text: '诊断文件包含系统与客户端版本、权限和错误类别，不包含聊天或密钥。' })),
      h('div', { class: 'settings-card-body' },
        storage,
        h('div', { class: 'form-actions' },
          h('a', { class: 'quiet-button', href: '/api/desktop/diagnostics', download: 'Tulpa-diagnostics.json' }, icon('download'), '导出运行诊断'),
          h('a', { class: 'quiet-button', href: '/legacy', target: '_blank', rel: 'noopener' }, icon('external'), '打开高级工具')))));

  const version = h('span', { class: 'badge', text: '—' });
  const storagePath = h('span', { class: 'mono', text: '—' });
  const productLine = h('p', { class: 'field-hint' });
  const aboutSection = h('section', { class: 'settings-section', dataset: { section: 'about' } },
    h('div', { class: 'settings-card' },
      h('div', { class: 'settings-card-head' }, h('div', { class: 'row' }, h('h3', { text: '关于 Tulpa' }), version), h('p', { text: '本机聊天资料、证据与外部 Agent 授权工作台。' })),
      h('div', { class: 'settings-card-body' },
        productLine,
        h('dl', { class: 'settings-kv' },
          h('dt', { text: '数据目录' }), h('dd', {}, storagePath),
          h('dt', { text: '前端' }), h('dd', { text: 'Claude Code（DeepSeek deepseek-flash[1M]）重构版' }),
          h('dt', { text: '隐私' }), h('dd', { text: '聊天原文、附件与凭证保存在本机；只在提问或外部 Agent 调用时按范围发送必要片段。' })),
        h('p', { class: 'settings-footnote', text: '回答由模型生成，引用原文由本地程序读取。模型结论与原文的语义一致性仍需核对。' }))));

  const nav = h('nav', { class: 'settings-nav', 'aria-label': '设置分类' },
    h('div', { class: 'subnav' },
      Object.entries(SECTION_NAMES).map(([id, label]) => h('button', { type: 'button', dataset: { section: id }, onclick: () => activate(id) }, label))));
  const content = h('div', { class: 'settings-content' }, appearanceSection, modelSection, onebotSection, snowSection, desktopSection, aboutSection);
  const page = h('div', { class: 'view-scroll' }, h('div', { class: 'page' },
    h('header', { class: 'page-head' },
      h('div', { class: 'page-head-copy' }, h('h2', { text: '设置' }), h('p', { text: '外观、模型与 QQ 连接都只保存在本机。' }))),
    h('div', { class: 'settings-layout' }, nav, content)));
  ui = { page, nav, content, appearance, appearanceNote, apiBase, apiKey, model, keyHint, modelState, modelStatus, modelSave,
    senderUrl, senderToken, eventsUrl, eventsToken, onebotCurrent, onebotStatus, onebotSave, onebotTest,
    background, managedNote, desktopPrivilege, storage, version, storagePath, productLine, snowMount, snowSection };
  snow = mountSnowLuma(snowMount, {
    onSaved: async settings => { if (settings) fillSettings(settings); },
  });
  return page;
}

registerView('settings', {
  title: '设置',
  subtitle: '外观、模型与 QQ 连接',
  async mount(root, params, query) {
    const shell = h('div', { class: 'view-scroll' }, loadingState('正在读取设置…'));
    root.append(shell);
    buildSettings();
    shell.replaceWith(ui.page);
    activate(query.section || 'appearance');
    const offs = [
      on('appearance', value => ui?.appearance.setValue(value)),
    ];
    try {
      await loadStatus();
    } catch (error) {
      ui.modelStatus.textContent = errorMessage(error, '设置状态暂时无法读取。');
      ui.modelStatus.className = 'status-line error';
    }
    window.addEventListener('focus', onWindowFocus);
    return () => {
      offs.forEach(off => off());
      window.removeEventListener('focus', onWindowFocus);
      snow?.destroy();
      snow = null;
      ui = null;
      status = null;
      preferences = null;
    };
  },
});

function onWindowFocus() {
  if (ui && document.visibilityState === 'visible') void loadStatus().catch(() => {});
}

// Boot after all views and actions have been registered.
bootApp().catch(error => {
  console.error(error);
  const root = document.querySelector('#view-root');
  if (root) {
    root.replaceChildren(h('div', { class: 'view-scroll' }, h('div', { class: 'page' },
      h('div', { class: 'card', style: { maxWidth: '620px', margin: '8vh auto' } },
        emptyState('alert', 'Tulpa 前端未能启动', errorMessage(error, '请刷新窗口或重新启动本机服务。'), null)))));
  }
});
