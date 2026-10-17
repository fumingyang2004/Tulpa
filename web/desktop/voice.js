'use strict';

/* Local voice playback, transcription and voice-ingestion settings. */
import { h, icon, api, toast, errorMessage, fieldLabel, selectControl, option } from './app.js';

const LABELS = {
  unprepared: '尚未转写',
  prepared: '音频可播放 · 尚未转写',
  queued: '已排队',
  running: '本地处理中',
  ready: '转写完成',
  failed: '转写失败 · 可重试',
  missing: '媒体不可用 · 本机未找到原音频',
};

export function renderVoiceMessage(messageId, value) {
  const box = h('div', { class: 'voice-message' });
  function paint(data) {
    box.replaceChildren();
    const label = h('p', { class: 'field-hint', text: '语音 · ' + (data.duration_ms ? (data.duration_ms / 1000).toFixed(1) + ' 秒 · ' : '') + (LABELS[data.status] || data.status) });
    box.append(label);
    if (data.playable && data.audio_url) {
      const audio = h('audio', { controls: true, preload: 'none', src: data.audio_url });
      box.append(audio);
    }
    if (data.transcript) {
      box.append(h('p', { text: '[语音转写] ' + data.transcript }));
      box.append(h('small', { text: '本地机器识别，可能有误听；重要信息请试听核对。' }));
    }
    if (data.error) box.append(h('p', { class: 'field-hint', text: data.error }));
    const busy = ['queued', 'running'].includes(data.status);
    const actions = h('div', { class: 'row wrap', style: { marginTop: '5px' } });
    const add = (text, action) => {
      const button = h('button', {
        class: 'source-button', type: 'button', text, disabled: busy,
        onclick: async () => {
          button.disabled = true;
          try { paint(await api('/api/voice/' + messageId, { method: 'POST', body: { action, retry: true } })); }
          catch (error) { toast(errorMessage(error, '语音操作未完成。'), 'bad'); button.disabled = false; }
        },
      });
      actions.append(button);
    };
    if (!data.playable) add('准备播放（不转写）', 'prepare');
    add(data.status === 'ready' ? '重新检查转写（优先复用缓存）' : ['failed', 'missing'].includes(data.status) ? '重试本地转写' : '转写语音', 'transcribe');
    box.append(actions);
    if (busy) {
      setTimeout(async () => {
        if (!box.isConnected) return;
        try { paint(await api('/api/voice/' + messageId, { timeout: 12000 })); }
        catch { label.textContent = '暂时无法读取语音状态。'; }
      }, 1600);
    }
  }
  paint(value);
  return box;
}

export function createVoiceSettings({ getReadScope = null, getDateRange = null } = {}) {
  const strategy = selectControl([
    ['lazy', '按需（默认）· Agent 或点击时转写'],
    ['new', '后台转写新入库语音'],
    ['manual-backfill', '只手动补转写 · 仍可单条按需查看'],
  ]);
  const status = h('p', { class: 'field-hint voice-status', role: 'status' });
  const platform = selectControl([['qq', 'QQ'], ['wechat', '微信']]);
  const result = h('p', { class: 'field-hint', role: 'status' });
  const discover = h('button', { class: 'quiet-button compact', type: 'button', text: '仅发现范围内历史语音' });
  const backfill = h('button', { class: 'quiet-button compact', type: 'button', text: '发现并补转写（最多 100 条）' });
  const save = h('button', { class: 'quiet-button compact', type: 'button', text: '保存语音设置' });
  const componentStatus = h('p', { class: 'field-hint', role: 'status' });
  const componentBar = h('div', { class: 'progress', hidden: true }, h('span'));
  const installComponent = h('button', { class: 'quiet-button compact', type: 'button', text: '安装本地语音模型' });
  const cancelComponent = h('button', { class: 'quiet-button compact', type: 'button', text: '取消下载', hidden: true });
  let disposed = false;
  let polling = null;
  let componentPolling = null;

  const element = h('div', {},
    h('p', { class: 'field-hint', text: '音频只在本机解码和转写。切换策略不会取消已排队的任务。' }),
    fieldLabel('转写时机', strategy),
    h('div', { class: 'form-actions' }, save, status),
    h('p', { class: 'field-hint', text: '发现与补转写使用读取页对应平台的会话范围和起止日期；日期须填写且最多一年。补转写单线程排队，可继续聊天。' }),
    fieldLabel('发现 / 补转写平台', platform),
    h('div', { class: 'form-actions' }, discover, backfill),
    result,
    h('div', { class: 'divider' }),
    h('h4', { text: '本地语音组件' }),
    h('p', { class: 'field-hint', text: '主包不带语音模型。需要时下载约 181 MB，之后可离线使用；不上传语音。' }),
    componentStatus,
    componentBar,
    h('div', { class: 'form-actions' }, installComponent, cancelComponent));

  async function refresh() {
    if (disposed) return;
    try {
      const data = await api('/api/voice/settings', { timeout: 12000 });
      if (disposed) return;
      strategy.value = data.strategy || 'lazy';
      const counts = data.counts || {};
      status.textContent = (data.local_asr_ready ? '本地模型 ' + data.model : '本地 ASR 尚未准备') +
        ` · 队列 ${data.queued || 0} 条 · 已转写 ${counts.ready || 0} 条` +
        (data.discovery?.running ? ' · 正在发现语音…' : data.discovery?.error ? ' · ' + data.discovery.error
          : data.discovery?.result ? ` · 最近发现 ${data.discovery.result.found} 条，新增 ${data.discovery.result.imported} 条` +
            (data.discovery.result.enqueued !== undefined ? `，排队 ${data.discovery.result.enqueued} 条` : '') : '');
      clearTimeout(polling);
      if ((data.queued || 0) > 0 || data.discovery?.running) {
        polling = setTimeout(() => { if (!disposed) void refresh(); }, 2200);
      }
    } catch (error) {
      status.textContent = errorMessage(error, '无法读取语音设置。');
    }
  }

  save.addEventListener('click', async () => {
    save.disabled = true;
    try {
      await api('/api/voice/settings', { method: 'PUT', body: { strategy: strategy.value } });
      toast('语音设置已保存。', 'success');
      await refresh();
    } catch (error) { toast(errorMessage(error, '语音设置未能保存。'), 'bad'); }
    finally { save.disabled = false; }
  });

  async function runBackfill(transcribe) {
    const p = platform.value;
    let dates = null;
    let scope = null;
    try {
      dates = getDateRange?.(p);
      scope = getReadScope?.(p);
    } catch (error) { toast(errorMessage(error), 'warn'); return; }
    if (!dates?.start || !dates?.end) { toast('请先在读取页填写对应平台的起止日期。', 'warn'); return; }
    if (!scope?.enabled) { toast('请先在读取页启用该平台。', 'warn'); return; }
    discover.disabled = backfill.disabled = true;
    result.textContent = transcribe ? '正在发现并排队补转写…' : '正在发现范围内语音…';
    try {
      await api('/api/voice/backfill', {
        method: 'POST',
        body: { platform: p, start: dates.start, end: dates.end, conversations: scope.conversations ?? null, transcribe },
      });
      await refresh();
    } catch (error) {
      result.textContent = errorMessage(error, '语音发现未完成。');
    } finally {
      discover.disabled = backfill.disabled = false;
    }
  }
  discover.addEventListener('click', () => void runBackfill(false));
  backfill.addEventListener('click', () => void runBackfill(true));

  function renderComponent(data) {
    const busy = ['downloading', 'cancelling'].includes(data.status);
    const ready = data.status === 'installed';
    componentStatus.textContent = (data.message || '尚未安装本地语音模型') +
      (busy && data.total ? ` · ${(data.downloaded / 1048576).toFixed(1)} / ${(data.total / 1048576).toFixed(1)} MB` : '');
    componentStatus.className = 'field-hint ' + (data.status === 'failed' ? 'error-text' : ready ? 'success-text' : '');
    componentBar.hidden = !busy;
    componentBar.classList.toggle('indeterminate', !data.total);
    componentBar.firstElementChild.style.width = data.total ? Math.round((data.downloaded / data.total) * 100) + '%' : '';
    installComponent.disabled = busy || ready;
    installComponent.textContent = ready ? '已安装' : busy ? '正在下载…' : '安装本地语音模型';
    cancelComponent.hidden = !busy;
    cancelComponent.disabled = data.status === 'cancelling';
  }
  async function refreshComponent() {
    if (disposed) return;
    try {
      const data = await api('/api/components/voice', { timeout: 12000 });
      if (disposed) return;
      renderComponent(data);
      clearTimeout(componentPolling);
      if (['downloading', 'cancelling'].includes(data.status)) {
        componentPolling = setTimeout(() => { if (!disposed) void refreshComponent(); }, 2000);
      }
    } catch (error) {
      componentStatus.textContent = errorMessage(error, '本地语音组件状态暂时不可用。');
    }
  }
  for (const [button, action] of [[installComponent, 'install'], [cancelComponent, 'cancel']]) {
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        renderComponent(await api('/api/components/voice', { method: 'POST', body: { action } }));
        await refreshComponent();
      } catch (error) { toast(errorMessage(error, '语音组件操作未完成。'), 'bad'); button.disabled = false; }
    });
  }
  void refresh();
  void refreshComponent();
  return {
    element, refresh,
    destroy() { disposed = true; clearTimeout(polling); clearTimeout(componentPolling); },
  };
}
