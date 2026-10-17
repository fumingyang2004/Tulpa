'use strict';

/* Group-management approvals for the active chat session. */
import { $, h, icon, api, toast, errorMessage } from './app.js';

const STATUS = {
  PENDING: ['等待审批', 'warn'],
  EXECUTING: ['正在执行', 'info'],
  SUCCEEDED: ['已执行', 'ok'],
  REJECTED: ['已拒绝', ''],
  FAILED: ['未执行成功', 'bad'],
  UNKNOWN: ['结果未知，请在 QQ 核对', 'warn'],
  EXPIRED: ['审批已过期', ''],
  CANCELLED: ['已取消', ''],
};

function noteFor(mode) {
  if (mode === 'allow') return '之后明确要求的群管理操作会自动允许并保留记录；已有待审批操作仍需逐项选择。';
  if (mode === 'deny') return '之后提出的群管理操作将默认拒绝，不执行。';
  return '群管理操作执行前会显示具体内容，等你允许或拒绝。';
}

export function createGroupAdmin(host, { getSessionId, ensureSession } = {}) {
  let disposed = false;
  let sessionId = getSessionId?.() || null;
  let operations = [];
  let available = false;
  let policy = 'ask';
  let loading = false;
  let changing = false;
  const busy = new Set();
  let lastSignature = '';
  let timer = null;

  const strip = h('div', { class: 'group-admin-strip' },
    h('span', { class: 'group-admin-strip-label' }, icon('lock'), '群管理审批'),
    h('select', {
      class: 'group-admin-policy', 'aria-label': '本对话群管理审批模式',
      onchange: event => void changePolicy(event.currentTarget.value),
    },
      h('option', { value: 'ask', text: '每次询问' }),
      h('option', { value: 'allow', text: '默认允许' }),
      h('option', { value: 'deny', text: '默认拒绝' })),
    h('span', { class: 'group-admin-strip-note', text: noteFor('ask') }));
  const cards = h('div', { class: 'group-admin-cards' });
  const panel = h('div', { class: 'group-admin-panel', hidden: true }, strip, cards);
  host.append(panel);

  async function changePolicy(mode) {
    if (changing || !['ask', 'allow', 'deny'].includes(mode)) return;
    changing = true;
    const select = strip.querySelector('select');
    select.disabled = true;
    try {
      if (!sessionId) {
        const next = await ensureSession?.();
        if (next) sessionId = next;
      }
      if (!sessionId) throw new Error('请先新建或打开一个对话。');
      await api('/api/group-admin/policy', { method: 'PUT', body: { session_id: sessionId, mode } });
      policy = mode;
      strip.querySelector('.group-admin-strip-note').textContent = noteFor(mode);
      toast('群管理审批模式已更新。', 'success');
      void refresh(true);
    } catch (error) {
      select.value = policy;
      toast(errorMessage(error, '群管理审批模式未能保存。'), 'bad');
    } finally {
      changing = false;
      select.disabled = false;
    }
  }

  async function decide(item, allow) {
    if (!item || busy.has(item.id) || sessionId !== item.session_id) return;
    busy.add(item.id);
    render();
    try {
      const result = await api('/api/group-admin/' + encodeURIComponent(item.id) + '/decision', {
        method: 'POST', body: { session_id: item.session_id, allow, token: item.approval_token },
      });
      if (sessionId === item.session_id) {
        operations = operations.map(row => row.id === item.id ? result : row);
        toast(STATUS[result.status]?.[0] || '群管理操作已更新。', result.status === 'SUCCEEDED' ? 'success' : '');
      }
    } catch (error) {
      toast(errorMessage(error, '群管理操作未能完成。'), 'bad');
    } finally {
      busy.delete(item.id);
      render();
      void refresh(true);
    }
  }

  function operationCard(item) {
    const [label, tone] = STATUS[item.status] || [item.status, ''];
    const card = h('article', { class: 'group-admin-card' },
      h('div', { class: 'group-admin-head' },
        h('strong', { text: '当前操作' }),
        h('span', { class: 'badge ' + tone, text: label })),
      h('p', { class: 'group-admin-summary', text: item.summary || '群管理操作' }),
      item.target ? h('small', { text: '使用 QQ ' + (item.target.account || '') + ' · ' + (item.target.actor_role === 'owner' ? '群主' : '管理员') }) : null);
    if (item.status === 'PENDING') {
      const actions = h('div', { class: 'group-admin-actions' },
        h('button', {
          class: 'primary-button compact', type: 'button', text: '允许', disabled: busy.has(item.id),
          onclick: () => void decide(item, true),
        }),
        h('button', {
          class: 'quiet-button compact', type: 'button', text: '拒绝', disabled: busy.has(item.id),
          onclick: () => void decide(item, false),
        }));
      card.append(actions);
    }
    return card;
  }

  function signature() {
    return JSON.stringify([sessionId, available, policy, operations, [...busy]]);
  }

  function render() {
    if (disposed) return;
    const next = signature();
    if (next === lastSignature) return;
    lastSignature = next;
    panel.hidden = !available;
    if (!available) return;
    const select = strip.querySelector('select');
    if (!changing) select.value = policy;
    strip.querySelector('.group-admin-strip-note').textContent = noteFor(policy);
    const pending = operations.filter(item => ['PENDING', 'EXECUTING'].includes(item.status));
    const recent = operations.filter(item => !['PENDING', 'EXECUTING'].includes(item.status)).slice(0, 8);
    cards.replaceChildren();
    for (const item of pending) cards.append(operationCard(item));
    if (recent.length) {
      const details = h('details', { class: 'group-admin-history' },
        h('summary', { text: '最近操作 · ' + (STATUS[recent[0].status]?.[0] || recent[0].status) }));
      for (const item of recent) {
        details.append(h('div', { class: 'group-admin-history-row' },
          h('p', { text: item.summary || '群管理操作' }),
          h('small', {
            text: (STATUS[item.status]?.[0] || item.status)
              + (item.decision === 'default_allow' ? ' · 默认允许' : item.decision === 'default_deny' ? ' · 默认拒绝' : '')
              + (item.detail ? ' · ' + item.detail : ''),
          })));
      }
      cards.append(details);
    }
    panel.hidden = !available;
  }

  async function refresh(force = false) {
    if (disposed || loading) return;
    const current = getSessionId?.() || null;
    if (current !== sessionId) {
      sessionId = current;
      operations = [];
      lastSignature = '';
      render();
    }
    loading = true;
    try {
      const data = await api('/api/group-admin' + (sessionId ? '?session_id=' + encodeURIComponent(sessionId) : ''), { timeout: 12000 });
      if (disposed) return;
      if ((getSessionId?.() || null) !== sessionId) return;
      available = Boolean(data.available);
      policy = data.policy?.mode || 'ask';
      operations = Array.isArray(data.operations) ? data.operations : [];
      if (force) lastSignature = '';
      render();
    } catch {
      // Approvals are optional; local chat must stay usable when the probe fails.
    } finally {
      loading = false;
    }
  }

  timer = setInterval(() => { if (!document.hidden) void refresh(); }, 1500);
  const onVisibility = () => { if (!document.hidden) void refresh(true); };
  document.addEventListener('visibilitychange', onVisibility);
  void refresh(true);

  return {
    refresh: () => refresh(true),
    setSession(id) {
      sessionId = id || null;
      operations = [];
      lastSignature = '';
      void refresh(true);
    },
    destroy() {
      disposed = true;
      clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisibility);
      panel.remove();
    },
  };
}
