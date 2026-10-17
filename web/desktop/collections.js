'use strict';

/* Evidence reference sets and QQ group knowledge (notices / essence). */
import {
  h, icon, api, app, registerView, registerAction, navigate, setViewTitle, toast, errorMessage,
  openDialog, promptDialog, confirmDialog, fieldLabel, emptyState, loadingState, fmtNumber, fmtFullTime,
} from './app.js';

const SOURCE_NAMES = {
  message: '聊天消息', media: '图片 / 表情', voice: '原生语音',
  artifact_source: '文件来源', artifact_chunk: '文件片段',
  qq_notice: 'QQ 公告', qq_essence: 'QQ 精华',
};

const state = {
  tab: 'collections',
  collections: [],
  totalCollections: 0,
  nextCollections: null,
  currentId: null,
  current: null,
  knowledgeGroups: [],
  knowledgeGroup: '',
  knowledgeItems: [],
  knowledgeNext: null,
  knowledgeStatus: null,
  knowledgeItem: null,
  mounted: false,
};

let ui = null;
let collectionToken = 0;
let knowledgeToken = 0;
let pollTimer = null;

function sourceLink(sourceType, source) {
  if (!source) return null;
  if (sourceType === 'qq_notice' || sourceType === 'qq_essence') {
    return h('button', {
      class: 'source-button open-knowledge', type: 'button', text: '打开资料',
      dataset: { knowledgeId: String(source.id) },
    });
  }
  if (String(sourceType).startsWith('artifact_') && source.url?.startsWith('/?file=')) {
    const params = new URLSearchParams(source.url.slice(2));
    return h('button', {
      class: 'source-button open-artifact', type: 'button', text: '打开文件来源',
      dataset: { sourceId: String(Number(params.get('file'))) },
    });
  }
  if (source.message_id) {
    return h('a', {
      class: 'source-button open-chat', href: '/?anchor=' + source.message_id,
      dataset: { messageId: String(source.message_id) }, text: '打开原始来源',
    });
  }
  if (source.url && source.url.startsWith('/?file=')) {
    const params = new URLSearchParams(source.url.slice(2));
    return h('button', {
      class: 'source-button open-artifact', type: 'button', text: '打开文件来源',
      dataset: { sourceId: String(Number(params.get('file'))) },
    });
  }
  if (source.url) return h('a', { class: 'source-button', href: source.url, target: '_blank', rel: 'noopener', text: '打开原始来源' });
  return null;
}

function itemView(item) {
  const wrapper = h('article', { class: 'collection-item' },
    h('strong', { text: SOURCE_NAMES[item.source_type] || item.source_type }));
  if (item.status === 'available') {
    const source = item.source || {};
    wrapper.append(
      h('p', { class: 'wrap-anywhere', text: source.title || source.filename || '原始来源' }),
      h('p', { text: [source.platform === 'qq' ? 'QQ' : source.platform === 'wechat' ? '微信' : '', source.conversation, source.sender || source.publisher].filter(Boolean).join(' · ') }));
    if (source.excerpt) wrapper.append(h('pre', { text: source.excerpt }));
    if (source.locator) wrapper.append(h('p', { class: 'field-hint', text: JSON.stringify(source.locator) }));
    if (source.media?.available) {
      wrapper.append(h('a', { class: 'open-image', href: source.media.url, target: '_blank', rel: 'noopener' },
        h('img', { src: source.media.url, alt: '证据图片', loading: 'lazy' })));
    }
    const actions = h('div', { class: 'collection-actions' });
    const link = sourceLink(item.source_type, source);
    if (link) actions.append(link);
    actions.append(h('button', {
      class: 'source-button', type: 'button', text: '移除引用',
      onclick: () => void removeItem(state.currentId, item.id),
    }));
    wrapper.append(actions);
  } else {
    wrapper.append(h('p', { text: item.reason || '原始来源不可用或已变化。' }));
    wrapper.append(h('div', { class: 'collection-actions' }, h('button', {
      class: 'source-button', type: 'button', text: '移除引用',
      onclick: () => void removeItem(state.currentId, item.id),
    })));
  }
  return wrapper;
}

function renderCollectionList() {
  if (!ui) return;
  ui.collectionList.replaceChildren();
  if (!state.collections.length) {
    ui.collectionList.append(h('p', { class: 'field-hint', text: '还没有证据集合。新建一个，然后从回答、聊天原文或文件中选择“保存证据”。' }));
  }
  for (const collection of state.collections) {
    ui.collectionList.append(h('button', {
      class: 'collection-entry', type: 'button', 'aria-current': String(collection.id === state.currentId),
      onclick: () => void showCollection(collection.id),
    }, h('strong', { text: collection.title }), h('small', { text: `${collection.item_count} 项 · ${fmtFullTime(collection.updated_at)}` })));
  }
  if (state.nextCollections !== null) {
    ui.collectionList.append(h('button', {
      class: 'quiet-button compact', type: 'button', text: '更多集合',
      onclick: () => void loadCollections(true, state.nextCollections),
    }));
  }
  ui.collectionCount.textContent = String(state.totalCollections || state.collections.length);
}

function renderCollectionDetail(collection, append = false) {
  if (!ui) return;
  if (!append) {
    ui.collectionDetail.replaceChildren(
      h('div', { class: 'row between' },
        h('div', {}, h('h2', { text: collection.title }), h('p', { text: `${collection.item_count} 项参考 · 只保存来源引用，不复制正文或文件。` })),
        h('div', { class: 'row' },
          h('button', { class: 'quiet-button compact', type: 'button', text: '重命名', onclick: () => void renameCollection(collection) }),
          h('button', { class: 'quiet-button compact danger-text', type: 'button', text: '删除集合', onclick: () => void deleteCollection(collection) }))));
  }
  const items = ui.collectionDetail.querySelector('.collection-items') || h('div', { class: 'collection-items' });
  if (!items.isConnected) ui.collectionDetail.append(items);
  for (const item of collection.items || []) items.append(itemView(item));
  if (!append && !(collection.items || []).length) items.append(emptyState('collection', '这个集合还没有引用', '从聊天原文、图片、语音、文件片段或 QQ 群资料中保存来源。', null, true));
  if (collection.next_offset !== null && collection.next_offset !== undefined) {
    const more = h('button', {
      class: 'quiet-button compact', type: 'button', text: '更多证据',
      onclick: async () => {
        more.disabled = true;
        try {
          const next = await api(`/api/collections/${encodeURIComponent(collection.id)}?offset=${collection.next_offset}`);
          renderCollectionDetail(next, true);
        } catch (error) { toast(errorMessage(error), 'bad'); more.disabled = false; }
      },
    });
    items.append(more);
  }
}

async function loadCollections(append = false, offset = 0) {
  if (!ui) return;
  const token = ++collectionToken;
  try {
    const data = await api('/api/collections?offset=' + offset, { timeout: 16000 });
    if (token !== collectionToken || !ui) return;
    if (!append) state.collections = [];
    state.collections = state.collections.concat(data.collections || []);
    state.totalCollections = Number(data.total) || state.collections.length;
    state.nextCollections = data.next_offset;
    renderCollectionList();
  } catch (error) {
    if (token === collectionToken && ui) toast(errorMessage(error, '证据集合读取失败。'), 'bad');
  }
}

async function showCollection(id) {
  if (!ui) return;
  const token = ++collectionToken;
  ui.collectionDetail.replaceChildren(loadingState('正在读取集合…'));
  try {
    const data = await api('/api/collections/' + encodeURIComponent(id), { timeout: 16000 });
    if (token !== collectionToken || !ui) return;
    state.currentId = id;
    state.current = data;
    renderCollectionList();
    ui.collectionDetail.replaceChildren();
    renderCollectionDetail(data);
  } catch (error) {
    if (token === collectionToken && ui) {
      ui.collectionDetail.replaceChildren(emptyState('alert', '集合无法读取', errorMessage(error), null, true));
    }
  }
}

async function createCollection() {
  const title = await promptDialog({ title: '新建证据集合', label: '集合名称（1–100 字）', maxLength: 100, confirmText: '创建' });
  if (!title) return;
  try {
    const data = await api('/api/collections', { method: 'POST', body: { title } });
    await loadCollections();
    await showCollection(data.id);
    toast('证据集合已创建。', 'success');
  } catch (error) { toast(errorMessage(error, '集合创建失败。'), 'bad'); }
}

async function renameCollection(collection) {
  const title = await promptDialog({ title: '重命名集合', label: '集合名称', value: collection.title, maxLength: 100, confirmText: '保存' });
  if (!title || title === collection.title) return;
  try {
    await api('/api/collections/' + encodeURIComponent(collection.id), { method: 'PATCH', body: { title } });
    await loadCollections();
    await showCollection(collection.id);
  } catch (error) { toast(errorMessage(error, '重命名失败。'), 'bad'); }
}

async function deleteCollection(collection) {
  const ok = await confirmDialog({
    title: '删除证据集合', message: `删除“${collection.title}”及其全部引用？`, details: '原始聊天、文件、图片和语音保留。',
    confirmText: '删除集合', danger: true,
  });
  if (!ok) return;
  try {
    await api('/api/collections/' + encodeURIComponent(collection.id), { method: 'DELETE' });
    state.currentId = null;
    state.current = null;
    if (ui) ui.collectionDetail.replaceChildren(emptyState('collection', '选择或新建一个集合', '集合只保存原始资料引用。', null));
    await loadCollections();
    toast('集合已删除。', 'success');
  } catch (error) { toast(errorMessage(error, '集合删除失败。'), 'bad'); }
}

async function removeItem(collectionId, itemId) {
  if (!collectionId) return;
  try {
    await api(`/api/collections/${encodeURIComponent(collectionId)}/items/${encodeURIComponent(itemId)}`, { method: 'DELETE' });
    await showCollection(collectionId);
    await loadCollections();
  } catch (error) { toast(errorMessage(error, '移除引用失败。'), 'bad'); }
}

/* ---------- save to collection ---------- */
export async function openCollectDialog(sourceType, sourceId) {
  if (!sourceType || sourceId === undefined || sourceId === null || sourceId === '') {
    toast('无法识别要保存的来源。', 'warn');
    return;
  }
  let data;
  try { data = await api('/api/collections', { timeout: 12000 }); }
  catch (error) { toast(errorMessage(error, '集合列表读取失败。'), 'bad'); return; }
  const select = h('select', { 'aria-label': '选择证据集合' });
  select.append(h('option', { value: '', text: '请选择集合' }));
  for (const collection of data.collections || []) select.append(h('option', { value: collection.id, text: collection.title }));
  if (state.currentId && (data.collections || []).some(item => item.id === state.currentId)) select.value = state.currentId;
  const name = h('input', { type: 'text', maxLength: 100, placeholder: '或输入新集合名称' });
  const status = h('p', { class: 'field-hint', role: 'status' });
  let next = data.next_offset;
  const more = h('button', {
    class: 'quiet-button compact', type: 'button', text: '加载更多集合', hidden: next === null,
    onclick: async () => {
      try {
        const page = await api('/api/collections?offset=' + next);
        for (const collection of page.collections || []) select.append(h('option', { value: collection.id, text: collection.title }));
        next = page.next_offset;
        more.hidden = next === null;
      } catch (error) { toast(errorMessage(error), 'bad'); }
    },
  });
  let dialog = null;
  const save = h('button', {
    class: 'primary-button', type: 'button', text: '保存引用',
    onclick: async () => {
      save.disabled = true;
      status.textContent = '正在保存…';
      try {
        let cid = select.value;
        if (name.value.trim()) {
          const created = await api('/api/collections', { method: 'POST', body: { title: name.value.trim() } });
          cid = created.id;
        }
        if (!cid) throw new Error('请选择集合或填写新集合名称。');
        await api(`/api/collections/${encodeURIComponent(cid)}/items`, {
          method: 'POST', body: { items: [{ source_type: sourceType, source_id: String(sourceId) }] },
        });
        dialog?.close();
        toast('已保存来源引用，原资料保留在原处。', 'success');
        if (ui && state.mounted) {
          await loadCollections();
          await showCollection(cid);
        }
      } catch (error) {
        status.textContent = errorMessage(error, '保存失败。');
        save.disabled = false;
      }
    },
  });
  dialog = openDialog({
    title: '保存到证据集合', subtitle: '只保存来源引用，不复制原文或文件',
    body: h('div', {},
      fieldLabel('选择集合', select, '最多 2000 项 / 集合；可稍后整理或移除。'),
      more,
      fieldLabel('新集合', name),
      status),
    footer: h('footer', { class: 'dialog-foot' },
      h('button', { class: 'quiet-button', type: 'button', text: '取消', onclick: () => dialog?.close() }),
      h('span', { class: 'spacer' }), save),
    small: true,
  });
}

/* ---------- QQ knowledge ---------- */
function switchTab(tab) {
  state.tab = tab === 'knowledge' ? 'knowledge' : 'collections';
  if (!ui) return;
  ui.tabCollections.setAttribute('aria-selected', String(state.tab === 'collections'));
  ui.tabKnowledge.setAttribute('aria-selected', String(state.tab === 'knowledge'));
  ui.collectionsPanel.hidden = state.tab !== 'collections';
  ui.knowledgePanel.hidden = state.tab !== 'knowledge';
  if (state.tab === 'knowledge') void loadKnowledgeStatus();
}

async function loadKnowledgeStatus() {
  if (!ui) return;
  try {
    const data = await api('/api/knowledge/status', { timeout: 16000 });
    state.knowledgeStatus = data;
    state.knowledgeGroups = data.groups || [];
    ui.knowledgeStatus.textContent = data.configured ? data.note : '未配置本机 OneBot / NapCat。公告与精华暂不能同步；其他聊天能力正常。';
    const selected = state.knowledgeGroup || state.knowledgeGroups[0]?.conversation_id || '';
    state.knowledgeGroup = selected;
    ui.knowledgeGroup.replaceChildren();
    if (!state.knowledgeGroups.length) ui.knowledgeGroup.append(h('option', { value: '', text: '没有已导入的 QQ 群' }));
    for (const group of state.knowledgeGroups) ui.knowledgeGroup.append(h('option', { value: group.conversation_id, text: group.conversation }));
    ui.knowledgeGroup.value = selected;
    updateKnowledgeSync();
    if (selected && !state.knowledgeItems.length) await loadKnowledgeItems();
  } catch (error) {
    if (ui) ui.knowledgeStatus.textContent = errorMessage(error, 'QQ 群资料状态读取失败。');
  }
}

function updateKnowledgeSync() {
  if (!ui) return;
  const data = state.knowledgeStatus;
  const group = state.knowledgeGroups.find(item => item.conversation_id === state.knowledgeGroup);
  const row = data?.sync?.find(item => item.conversation_id === state.knowledgeGroup);
  ui.knowledgePeriodic.input.checked = Boolean(row?.enabled);
  ui.knowledgePeriodic.input.disabled = !data?.configured || !group;
  ui.knowledgeRefresh.disabled = !data?.configured || !group || Boolean(data?.state?.running);
  ui.knowledgeNote.textContent = group ? (row ? `同步记录：${row.status || '待同步'} · 最近成功 ${row.last_success ? fmtFullTime(row.last_success) : '尚无'}` : '此群尚未同步') : '请选择一个已导入的 QQ 群。';
  if (data?.state?.running) {
    ui.knowledgeStatus.textContent = '正在同步 QQ 群公告 / 精华，可继续正常对话…';
    if (!pollTimer) pollTimer = setTimeout(async () => { pollTimer = null; await loadKnowledgeStatus(); }, 1200);
  } else if (data?.state?.error) {
    ui.knowledgeStatus.textContent = data.state.error;
  } else if (data?.state?.result) {
    ui.knowledgeStatus.textContent = typeof data.state.result === 'string' ? data.state.result : JSON.stringify(data.state.result);
  }
}

async function loadKnowledgeItems(append = false, offset = 0) {
  if (!ui) return;
  const group = state.knowledgeGroup;
  if (!group) return;
  const token = ++knowledgeToken;
  try {
    const data = await api('/api/knowledge?' + new URLSearchParams({ conversation_id: group, offset }), { timeout: 16000 });
    if (token !== knowledgeToken || !ui) return;
    if (!append) state.knowledgeItems = [];
    state.knowledgeItems = state.knowledgeItems.concat(data.sources || []);
    state.knowledgeNext = data.next_offset;
    renderKnowledgeItems();
  } catch (error) {
    if (token === knowledgeToken && ui) ui.knowledgeList.replaceChildren(emptyState('alert', '资料读取失败', errorMessage(error), null, true));
  }
}

function renderKnowledgeItems() {
  if (!ui) return;
  ui.knowledgeList.replaceChildren();
  if (!state.knowledgeItems.length) {
    ui.knowledgeList.append(emptyState('collection', '暂无群公告或精华', '配置 OneBot 后，可选择群同步一次；未返回的旧条目不会自动删除。', null, true));
    return;
  }
  for (const item of state.knowledgeItems) {
    ui.knowledgeList.append(h('div', { class: 'knowledge-source' },
      h('span', { class: 'badge ' + (item.kind === 'notice' ? 'info' : 'accent'), text: item.kind === 'notice' ? '公告' : '精华' }),
      h('span', { text: item.content ? item.content.slice(0, 120) : '非文本资料' }),
      h('button', { class: 'source-button', type: 'button', text: '查看', onclick: () => void showKnowledge(item.id) })));
  }
  if (state.knowledgeNext !== null && state.knowledgeNext !== undefined) {
    ui.knowledgeList.append(h('button', {
      class: 'quiet-button compact', type: 'button', text: '更多资料',
      onclick: () => void loadKnowledgeItems(true, state.knowledgeNext),
    }));
  }
}

async function showKnowledge(id) {
  try {
    const item = await api('/api/knowledge/' + id, { timeout: 14000 });
    state.knowledgeItem = item;
    const body = h('div', {},
      h('p', { class: 'field-hint', text: `${item.conversation} · ${item.publisher} · ${item.time}` }),
      h('pre', { class: 'code-block', text: item.content || '（无文字内容）' }),
      h('p', { class: 'field-hint', text: `${item.provenance} · 原始编号 ${item.native_id} · 缓存于 ${fmtFullTime(item.fetched_at)}` }),
      item.image_count ? h('p', { class: 'field-hint', text: `${item.image_count} 张图片的元数据；未下载或理解。` }) : null,
      h('div', { class: 'form-actions' },
        h('button', {
          class: 'quiet-button', type: 'button', text: '保存这条资料',
          onclick: () => void openCollectDialog('qq_' + item.kind, String(item.id)),
        }),
        item.canonical_message_id ? h('a', { class: 'quiet-button open-chat', href: '/?anchor=' + item.canonical_message_id, dataset: { messageId: String(item.canonical_message_id) } }, '打开原聊天') : null,
        !item.canonical_message_id && item.kind === 'essence' ? h('span', { class: 'field-hint', text: '尚未核对到原始聊天，不会猜测消息位置。' }) : null));
    openDialog({ title: item.kind === 'notice' ? 'QQ 群公告' : 'QQ 精华', subtitle: item.conversation, body, wide: true });
  } catch (error) { toast(errorMessage(error, 'QQ 资料读取失败。'), 'bad'); }
}

async function syncKnowledge() {
  const group = state.knowledgeGroup;
  if (!group) return;
  try {
    const periodic = state.knowledgeStatus?.sync?.some(item => item.conversation_id === group && item.enabled);
    await api('/api/knowledge/refresh', { method: 'POST', body: { conversation_id: group, periodic: Boolean(periodic) } });
    toast('已开始同步群公告 / 精华。', 'success');
    await loadKnowledgeStatus();
  } catch (error) { toast(errorMessage(error, '同步未开始。'), 'bad'); }
}

async function toggleKnowledgeSchedule() {
  const group = state.knowledgeGroup;
  if (!group) return;
  try {
    await api('/api/knowledge/schedule', {
      method: 'POST', body: { conversation_id: group, enabled: ui.knowledgePeriodic.input.checked },
    });
    await loadKnowledgeStatus();
    toast('群资料同步计划已保存。', 'success');
  } catch (error) { toast(errorMessage(error, '同步计划未能保存。'), 'bad'); }
}

/* ---------- page ---------- */
function buildPage() {
  const tabCollections = h('button', { type: 'button', role: 'tab', 'aria-selected': 'true', text: '证据集合' });
  const tabKnowledge = h('button', { type: 'button', role: 'tab', 'aria-selected': 'false', text: 'QQ 群资料' });
  const collectionList = h('aside', { class: 'collection-list', 'aria-label': '证据集合列表' });
  const collectionCount = h('span', { class: 'badge', text: '0' });
  const collectionDetail = h('section', { class: 'collection-detail' },
    emptyState('collection', '选择或新建一个集合', '只保存原始资料的引用；删除集合或移除项目不会删除聊天和文件。'));
  const collectionsPanel = h('div', { class: 'collections-layout' },
    h('div', { class: 'collection-list-wrap' },
      h('div', { class: 'collection-list-head' }, h('strong', { text: '我的集合' }), collectionCount,
        h('button', { class: 'icon-button', type: 'button', 'aria-label': '新建集合', title: '新建集合', onclick: () => void createCollection() }, icon('plus'))),
      collectionList),
    collectionDetail);

  const knowledgeStatus = h('p', { class: 'field-hint', role: 'status' });
  const knowledgeGroup = h('select', { 'aria-label': 'QQ 群' });
  const periodicInput = h('input', { type: 'checkbox' });
  const knowledgePeriodic = h('label', { class: 'check-row' }, periodicInput, h('span', { text: '此群在启动及每 6 小时同步' }));
  knowledgePeriodic.input = periodicInput;
  const knowledgeRefresh = h('button', { class: 'quiet-button compact', type: 'button', onclick: () => void syncKnowledge() }, '同步所选群公告 / 精华');
  const knowledgeNote = h('p', { class: 'field-hint' });
  const knowledgeList = h('div');
  const knowledgePanel = h('div', { hidden: true },
    h('div', { class: 'section' },
      h('div', { class: 'section-head' }, h('h2', { text: 'QQ 群公告 / 精华' }),
        h('div', { class: 'section-head-actions' }, knowledgeRefresh)),
      knowledgeStatus,
      h('div', { class: 'knowledge-controls' }, knowledgeGroup, knowledgePeriodic),
      knowledgeNote,
      knowledgeList));

  const head = h('header', { class: 'page-head' },
    h('div', { class: 'page-head-copy' },
      h('h2', { text: '证据集合' }),
      h('p', { text: '把分散在聊天、文件和 QQ 资料中的来源收在一起；原始资料不会被复制。' })),
    h('div', { class: 'page-head-actions' },
      h('button', { class: 'primary-button', type: 'button', onclick: () => void createCollection() }, icon('plus'), '新建集合'),
      h('button', { class: 'quiet-button', type: 'button', onclick: () => void loadCollections() }, icon('refresh'), '刷新')));
  const tabs = h('div', { class: 'pill-tabs', role: 'tablist', 'aria-label': '证据视图' }, tabCollections, tabKnowledge);
  const page = h('div', { class: 'view-scroll' }, h('div', { class: 'page' }, head, tabs, collectionsPanel, knowledgePanel));
  ui = { page, tabCollections, tabKnowledge, collectionsPanel, knowledgePanel, collectionList, collectionCount,
    collectionDetail, knowledgeStatus, knowledgeGroup, knowledgePeriodic, knowledgeRefresh, knowledgeNote, knowledgeList };
  tabCollections.addEventListener('click', () => switchTab('collections'));
  tabKnowledge.addEventListener('click', () => switchTab('knowledge'));
  knowledgeGroup.addEventListener('change', () => {
    state.knowledgeGroup = knowledgeGroup.value;
    state.knowledgeItems = [];
    state.knowledgeNext = null;
    updateKnowledgeSync();
    void loadKnowledgeItems();
  });
  knowledgePeriodic.input.addEventListener('change', () => void toggleKnowledgeSchedule());
  knowledgeRefresh.addEventListener('click', () => void syncKnowledge());
  return page;
}

registerView('collections', {
  title: '证据集合',
  subtitle: '原始来源引用与 QQ 群资料',
  async mount(root, params, query) {
    state.mounted = true;
    state.currentId = query.collection || null;
    const shell = buildPage();
    root.append(shell);
    try {
      await loadCollections();
      if (query.collection) await showCollection(query.collection);
      if (query.knowledge) {
        switchTab('knowledge');
        await showKnowledge(Number(query.knowledge));
      } else switchTab(query.tab === 'knowledge' ? 'knowledge' : 'collections');
    } catch (error) {
      toast(errorMessage(error, '证据集合暂时无法读取。'), 'bad');
    }
    return () => {
      state.mounted = false;
      collectionToken += 1;
      knowledgeToken += 1;
      clearTimeout(pollTimer);
      pollTimer = null;
      ui = null;
      state.collections = [];
      state.knowledgeItems = [];
    };
  },
});

/* ---------- global actions ---------- */
registerAction('collect', (sourceType, sourceId) => void openCollectDialog(sourceType, sourceId));

registerAction('open-collection', id => {
  if (app.current?.name === 'collections' && ui) {
    switchTab('collections');
    void loadCollections().then(() => showCollection(id));
  } else {
    navigate('collections', { collection: id });
  }
});

registerAction('open-knowledge', id => {
  if (app.current?.name === 'collections' && ui) {
    switchTab('knowledge');
    void loadKnowledgeStatus().then(() => showKnowledge(Number(id)));
  } else {
    navigate('collections', { knowledge: id });
  }
});
