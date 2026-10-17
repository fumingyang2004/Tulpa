'use strict';

/* Reply drafting and approval. The preview token binds the exact visible text. */
import {
  h, icon, api, toast, errorMessage, openDialog, fieldLabel, checkbox, option,
} from './app.js';

const STATUS_TEXT = {
  DRAFT: '草稿已保存，尚未发送。',
  USER_APPROVED: '已批准，准备发送…',
  SENDING: '正在发送，请勿重复操作。',
  SENT: 'QQ 已返回成功回执；桌面 QQ 可能不显示，可在手机 QQ 核对。',
  REJECTED: '已拒绝，未发送。草稿和生成记录已保留。',
  UNKNOWN: '发送结果未知，请到 QQ 核对，本条不会重发。',
};

function replyAddress(target) {
  const parts = /^(\d+):(group|direct):(\d+)$/.exec(target?.conversation_id || '');
  return target?.platform === 'qq' && parts ? { account: parts[1], kind: parts[2], peer: parts[3] } : null;
}

function sameConversation(a, b) {
  return Boolean(a && b && a.platform === b.platform && a.conversation_id === b.conversation_id);
}

export function createReplyController({ openChat, getVisionMode = () => 'ocr', getProfile = () => 'standard', nativeVisionAvailable = () => false, onReceipts = null } = {}) {
  let session = null;
  let busy = false;
  let dirty = false;
  let timer = null;
  let opening = 0;
  let stage = 'instruction';
  let instruction = '';
  let error = '';
  let savePromise = null;
  let ref = null;
  let els = null;
  let currentConversation = null;

  async function replyApi(path, body, method = 'POST') {
    const response = await fetch('/api/replies' + path, {
      method,
      cache: 'no-store',
      credentials: 'same-origin',
      headers: {
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        ...(method !== 'GET' ? { 'X-Reply-UI': '1' } : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data = null;
    try { data = await response.json(); } catch { data = null; }
    if (!response.ok) throw new Error(data?.detail || '回复操作未完成。');
    return data;
  }

  function build() {
    const target = h('p', { class: 'field-hint' });
    const source = h('blockquote', { class: 'reply-source' });
    const editor = h('textarea', { rows: 4, maxLength: 1000, placeholder: '例如：帮我婉拒、调侃一下、不要太正式', 'aria-label': '回复要求或草稿' });
    const editorLabel = h('label', { class: 'field-label', for: editor.id = 'reply-editor', text: '回复要求（可留空）' });
    const profile = h('select', { 'aria-label': '思考强度' },
      option('quick', '快速'), option('standard', '标准'), option('deep', '深入'), option('thorough', '充分'));
    const vision = h('select', { 'aria-label': '图片处理方式' },
      option('ocr', '本地 OCR'), option('native', '原生识图'));
    const generate = h('button', { class: 'quiet-button compact', type: 'button' }, icon('plus'), '生成回复');
    const stop = h('button', { class: 'quiet-button compact', type: 'button', hidden: true }, icon('stop'), '停止生成');
    const adjust = h('button', { class: 'text-button', type: 'button', hidden: true, text: '调整要求' });
    const back = h('button', { class: 'text-button', type: 'button', hidden: true, text: '返回草稿' });
    const copy = h('button', { class: 'text-button', type: 'button', hidden: true }, icon('copy'), '复制');
    const again = h('button', { class: 'text-button', type: 'button', hidden: true, text: '再写一条' });
    const notes = h('p', { class: 'field-hint' });
    const basis = h('p', { class: 'status-line' });
    const evidence = h('div', { class: 'reply-actions' });
    const history = h('div', { class: 'reply-history' });
    const references = h('details', { class: 'disclosure' },
      h('summary', { text: '本轮读取的上下文与过程' }),
      h('div', { class: 'disclosure-body' }, basis, evidence, history));
    const quote = checkbox('引用原消息', false);
    const status = h('p', { id: 'reply-status', role: 'status', 'aria-live': 'polite' });
    const address = h('p', { class: 'field-hint' });
    const sender = h('p', { class: 'field-hint' });
    const approve = h('button', { class: 'primary-button', type: 'button', disabled: true, text: '批准' });
    const reject = h('button', { class: 'quiet-button', type: 'button', text: '拒绝' });

    const body = h('div', { class: 'stack' },
      target,
      h('p', { class: 'status-line', text: '先核对目标消息，再填写要求或直接生成。发送前还需要一次明确的批准。' }),
      source,
      editorLabel,
      editor,
      h('div', { class: 'reply-actions' },
        h('label', {}, '思考强度', profile),
        h('label', {}, '图片', vision),
        generate, stop, adjust, back, copy, again),
      notes,
      references,
      quote,
      h('div', { class: 'reply-review' }, status, address, sender));
    els = { target, source, editor, editorLabel, profile, vision, generate, stop, adjust, back, copy, again,
      notes, basis, evidence, history, references, quote, status, address, sender, approve, reject };
    return body;
  }

  function close() {
    clearTimeout(timer);
    opening += 1;
    saveReplyEdit().catch(() => {});
    if (ref?.dialog?.open) ref.dialog.close();
    ref = null;
    els = null;
    session = null;
    stage = 'instruction';
    instruction = '';
    error = '';
    dirty = false;
    currentConversation = null;
    onReceipts?.();
  }

  async function open(messageId) {
    const token = ++opening;
    clearTimeout(timer);
    if (busy) { toast('请等待当前回复操作完成。', 'warn'); return; }
    try {
      await saveReplyEdit();
      const data = await replyApi('', { message_id: messageId });
      if (token !== opening) return;
      const same = session?.id === data.id;
      currentConversation = { platform: data.target.platform, conversation_id: data.target.conversation_id };
      session = data;
      dirty = false;
      error = '';
      if (!same) {
        stage = data.ai_text ? 'draft' : 'instruction';
        if (!same) instruction = data.instruction || '';
        els = null;
      }
      if (ref?.dialog?.open) { render(); focusEditor(); poll(); return; }
      const body = build();
      const footer = h('footer', { class: 'dialog-foot' },
        els.approve,
        els.reject,
        h('span', { class: 'spacer' }),
        h('span', { class: 'field-hint', text: '拒绝不会发送；批准前会核对正文、目标与版本。' }));
      ref = openDialog({
        title: '帮我回复', subtitle: '草稿只保存在本机', body, footer, side: true, className: 'reply-drawer',
        onClose: () => {
          clearTimeout(timer);
          els = null;
          session = null;
          opening += 1;
          onReceipts?.();
        },
      });
      bind();
      if (!same) {
        els.quote.checked = false;
        els.vision.value = getVisionMode();
        els.profile.value = getProfile();
        els.references.open = false;
      }
      render();
      focusEditor();
      poll();
    } catch (err) {
      toast(errorMessage(err, '回复面板暂时无法打开。'), 'bad');
    }
  }

  function bind() {
    const e = els;
    e.editor.addEventListener('input', () => {
      if (stage === 'instruction') instruction = e.editor.value;
      else dirty = true;
      error = '';
      render();
    });
    e.generate.addEventListener('click', () => void action(async () => {
      if (!session || session.status !== 'DRAFT' || session.active_run) return;
      await saveReplyEdit();
      dirty = false;
      const data = await replyApi('/' + session.id + '/generate', {
        instruction: instruction, profile: e.profile.value, vision: e.vision.value,
      });
      acceptResult(data);
    }));
    e.stop.addEventListener('click', () => void action(async () => {
      if (!session) return;
      await replyApi('/' + session.id + '/stop', {});
    }));
    e.adjust.addEventListener('click', () => {
      if (busy) return;
      stage = 'instruction';
      render();
      focusEditor();
    });
    e.back.addEventListener('click', () => {
      if (busy) return;
      stage = 'draft';
      dirty = false;
      render();
    });
    e.copy.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(e.editor.value); toast('已复制回复。', 'success'); }
      catch { toast('复制失败，可选中文字手动复制。', 'warn'); }
    });
    e.again.addEventListener('click', () => void action(async () => {
      if (!session || !['SENT', 'REJECTED'].includes(session.status)) return;
      const data = await replyApi('', { message_id: session.message_id, fresh: true });
      session = data;
      dirty = false;
      instruction = '';
      stage = 'instruction';
      error = '';
      render();
      focusEditor();
    }));
    e.reject.addEventListener('click', () => void action(async () => {
      if (!session || session.status !== 'DRAFT') return;
      await saveReplyEdit();
      session = await replyApi('/' + session.id + '/reject', { revision: session.revision });
      render();
    }));
    e.approve.addEventListener('click', () => void action(approveReply));
  }

  function focusEditor() {
    requestAnimationFrame(() => els?.editor?.focus());
  }

  async function action(fn) {
    if (busy || !session) return;
    busy = true;
    error = '';
    render();
    try { await fn(); }
    catch (err) { error = errorMessage(err, '回复操作未完成。'); toast(error, 'bad'); }
    finally {
      busy = false;
      render();
      poll();
    }
  }

  function acceptResult(data) {
    if (!data.active_run && !data.error && data.ai_text) {
      stage = 'draft';
      dirty = false;
    }
    session = data;
    render();
    poll();
  }

  function render() {
    const e = els;
    if (!e || !session) return;
    const target = session.target;
    const isDraft = stage === 'draft';
    const active = Boolean(session.active_run);
    const locked = session.status !== 'DRAFT';
    const generationFailed = session.status === 'DRAFT' && session.error && !active &&
      ['error', 'cancelled', 'interrupted'].includes(session.generations?.[0]?.status);
    e.target.textContent = `${target.platform === 'qq' ? 'QQ' : '微信'} · ${target.conversation} · 回复 ${target.sender}`;
    e.source.textContent = target.content || '[媒体消息，请结合识图或转写]';
    if (!isDraft) {
      if (e.editor.value !== instruction) e.editor.value = instruction;
    } else if (!dirty) {
      if (e.editor.value !== session.final_text) e.editor.value = session.final_text || '';
      dirty = false;
    }
    e.editorLabel.textContent = isDraft
      ? (generationFailed ? '上一次回复草稿 · 本次生成未替换' : '回复草稿 · 可直接编辑')
      : '回复要求（可留空）';
    e.editor.maxLength = isDraft ? 2000 : 1000;
    e.editor.placeholder = isDraft ? '在这里编辑准备发送的回复' : '例如：帮我婉拒、调侃一下、不要太正式';
    e.editor.disabled = active || locked || busy;
    e.profile.disabled = active || locked || busy;
    e.vision.disabled = active || locked || busy;
    if (!e.vision.querySelector('option[value="native"]')?.disabled && !nativeVisionAvailable()) {
      const native = e.vision.querySelector('option[value="native"]');
      if (native) native.disabled = true;
    }
    e.generate.disabled = active || locked || busy;
    e.generate.textContent = isDraft ? '重新生成' : '生成回复';
    e.generate.hidden = locked || active;
    e.stop.hidden = !active;
    e.stop.disabled = busy;
    e.adjust.hidden = !isDraft || locked || active;
    e.adjust.disabled = busy;
    e.back.hidden = isDraft || !session.ai_text || locked || active;
    e.back.disabled = busy;
    e.copy.hidden = !isDraft;
    e.copy.disabled = !e.editor.value;
    e.again.hidden = !['SENT', 'REJECTED'].includes(session.status);
    e.again.disabled = busy;
    e.again.textContent = session.status === 'REJECTED' ? '重新起草' : '再写一条';
    e.approve.disabled = !isDraft || active || locked || busy || !session.ai_text || !e.editor.value.trim() || !session.sender?.available;
    e.reject.disabled = locked || busy;
    e.quote.input.disabled = !isDraft || active || locked || busy || !session.sender?.available;
    const savedError = (session.error || '') + (generationFailed && session.ai_text ? ' 当前显示的是上一次草稿，本次未替换。' : '');
    const statusText = error || savedError || (active ? '正在读取上下文并生成回复…' : isDraft
      ? (dirty ? '已编辑，尚未发送。' : '草稿已保存，尚未发送。')
      : (STATUS_TEXT[session.status] || session.status));
    e.status.textContent = statusText;
    e.status.className = 'status-line ' + (error || savedError ? 'error' : session.status === 'SENT' ? 'success' : '');
    const address = replyAddress(target);
    e.address.textContent = address
      ? `QQ ${address.account} → ${address.kind === 'group' ? '群' : '好友'}「${target.conversation}」（${address.peer}）`
      : '';
    e.sender.textContent = locked ? '' : session.sender?.available
      ? '批准将发送上方编辑框中的文字；拒绝不会发送。'
      : (session.sender?.note || '当前记录没有可靠的发送目标。');
    e.notes.textContent = (session.result?.notes || []).join('；');
    let basisText = session.result?.context_note || (session.ai_text ? '旧版草稿保留的来源记录。' : '生成后可查看本轮读取的上下文。');
    if (session.result?.tulpa) {
      const tulpa = session.result.tulpa;
      if (tulpa.history_examples !== undefined) basisText += ` 从已导入聊天读取 ${tulpa.history_examples} 个真实互动案例。`;
      basisText += ' ' + (tulpa.enabled
        ? `Tulpa：参考 ${tulpa.episodes || 0} 个成长案例${tulpa.memory_used ? '、自动记忆' : ''}${tulpa.manual_used ? '、人工修正' : ''}。`
        : 'Tulpa 已关闭，本轮未使用成长记忆或成长案例；历史聊天检索仍可用。');
    }
    e.basis.textContent = basisText;
    e.references.hidden = !(session.generations?.length || session.ai_text);
    e.evidence.replaceChildren();
    const contextIds = session.result?.context_ids || [...(session.result?.evidence_ids || []), ...(session.result?.style_ids || [])];
    const ownIds = session.result?.self_message_ids || session.result?.style_ids || [];
    [...new Set(contextIds)].forEach(mid => {
      const button = h('button', {
        class: 'source-button', type: 'button',
        text: ownIds.includes(mid) ? '本人发言 · M' + mid : '上下文 · M' + mid,
        onclick: () => void action(async () => { await saveReplyEdit(); openChat?.(mid); }),
      });
      e.evidence.append(button);
    });
    e.history.replaceChildren();
    for (const generation of session.generations || []) {
      const details = h('details');
      const summary = h('summary', {
        text: `${generation.status === 'running' ? '生成中' : generation.status === 'completed' ? '已生成' : generation.status} · ${generation.seconds || 0} 秒 · ${generation.requests || 0} 次请求 · ${generation.usage?.total_tokens || 0} tokens`,
      });
      const pre = h('pre');
      pre.textContent = [generation.result?.error || '', generation.result?.reply_text || '',
        ...(generation.events || []).map(event => event.text || event.name || event.type)].filter(Boolean).join('\n');
      details.append(summary, pre);
      e.history.append(details);
    }
  }

  async function saveReplyEdit() {
    if (savePromise) await savePromise;
    if (!session || !dirty) return;
    const id = session.id;
    const text = els?.editor?.value ?? session.final_text ?? '';
    savePromise = replyApi('/' + id, { text, revision: session.revision }, 'PATCH');
    try {
      const data = await savePromise;
      if (session?.id === id) {
        session = data;
        dirty = (els?.editor?.value ?? '') !== text;
        render();
      }
    } finally {
      savePromise = null;
    }
  }

  function poll() {
    clearTimeout(timer);
    if (!session || !ref?.dialog?.open) return;
    const id = session.id;
    const token = opening;
    if (!session.active_run && !['SENDING', 'USER_APPROVED'].includes(session.status)) return;
    timer = setTimeout(async () => {
      try {
        const data = await replyApi('/' + id, undefined, 'GET');
        if (session?.id === id && token === opening && !busy) acceptResult(data);
      } catch (err) {
        if (els && token === opening) els.status.textContent = errorMessage(err);
      }
      poll();
    }, 1200);
  }

  async function approveReply() {
    if (!els || stage !== 'draft' || !session || session.status !== 'DRAFT' || session.active_run ||
        !session.ai_text || !session.sender?.available || !els.editor.value.trim()) return;
    const text = els.editor.value;
    const address = replyAddress(session.target);
    if (!address) throw new Error('所选记录没有可靠的 QQ 发送目标，未发送。');
    await saveReplyEdit();
    const sid = session.id;
    const revision = session.revision;
    const quote = els.quote.input.checked;
    els.status.textContent = '正在核对账号和会话…';
    const preview = await replyApi('/' + sid + '/preview', { revision, quote });
    if (!ref?.dialog?.open) return;
    if (preview.text !== text || preview.revision !== revision || preview.quote !== quote || !preview.destination ||
        ['account', 'kind', 'peer'].some(key => String(preview.destination[key]) !== address[key])) {
      throw new Error('发送核对结果与当前审核内容不一致，未发送。请重新打开草稿。');
    }
    els.status.textContent = '已批准，正在发送…';
    try {
      session = await replyApi('/' + sid + '/send', { token: preview.token, confirm: true });
    } catch (err) {
      session = await replyApi('/' + sid, undefined, 'GET');
      throw err;
    }
    render();
    onReceipts?.();
    onReceipts?.({ platform: session.target.platform, conversation_id: session.target.conversation_id });
  }

  function syncConversation(page) {
    if (!ref?.dialog?.open || !session) return;
    if (!sameConversation(session.target, page)) close();
  }

  function destroy() {
    clearTimeout(timer);
    if (ref?.dialog?.open) ref.dialog.close();
    ref = null; els = null; session = null;
  }

  return { open, close, syncConversation, destroy, isOpen: () => Boolean(ref?.dialog?.open) };
}

export async function loadReplyReceipts(platform, conversationId) {
  if (!platform || !conversationId) return [];
  const response = await fetch('/api/reply-receipts?' + new URLSearchParams({ platform, conversation_id: conversationId }), {
    cache: 'no-store', credentials: 'same-origin',
  });
  if (!response.ok) return [];
  const data = await response.json();
  return Array.isArray(data.receipts) ? data.receipts : [];
}
