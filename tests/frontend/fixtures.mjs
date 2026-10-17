// Synthetic data only; no real accounts, model calls or network writes.
export const permissions = [
  ['media', '提供历史图片', false, 'conversation'], ['prepare', '下载文件和提取文字', false, 'conversation'],
  ['voice', '本地转写语音', false, 'conversation'], ['onebot', '查询 QQ 群资料', false, 'conversation'],
  ['send', '直接发送 QQ 消息', false, 'conversation'], ['manage', 'QQ 群管理', false, 'conversation'],
  ['chat', '持续聊天', false, 'conversation'], ['chat_images', '聊天看图和表情目录', false, 'conversation'],
  ['chat_sticker_send', '发送表情', false, 'conversation'], ['chat_sticker_collect', '收藏表情', false, 'conversation'],
  ['chat_reactions', '消息表情回应', false, 'conversation'],
  ['chat_voice', '实时语音读取、转写和发送', true, 'conversation'],
  ['chat_video', '实时视频读取和发送', true, 'conversation'],
  ['chat_files', '实时文件读取、发送及文件管理', true, 'conversation'],
  ['qq_messages', '授权会话的撤回、转发、卡片及已读操作', true, 'conversation'],
  ['qq_interactions', '戳一戳、资料点赞、群签到', true, 'conversation'],
  ['qq_friends', '好友目录、申请、备注、分组及删除', true, 'account'],
  ['qq_profile', '个人资料、头像、签名及在线状态', true, 'account'],
  ['qq_qzone', 'QQ 空间读取、发表、删除、点赞及评论', true, 'account'],
  ['qq_albums', '授权群的相册浏览、上传、评论、点赞及删除', true, 'conversation'],
].map(([id, label, extended, scope]) => ({ id, label, extended, scope }));

export const profiles = ['quick', 'standard', 'deep', 'thorough'].map((id, index) => ({ id, label: ['快速', '标准', '深入', '充分'][index], description: '', budget: { max_tool_calls: 10, max_seconds: 300, max_requests: 8, max_messages: 100, max_evidence_chars: 24000, max_output_tokens: 4096, reasoning_effort: 'low' } }));

export const responses = pathname => {
  if (pathname === '/legacy/config') return { data: { dependencies: [
    { id: 0, api_name: 'refresh_sessions' }, { id: 1, api_name: 'refresh' },
    { id: 2, api_name: 'agent_action_controls' }, { id: 3, api_name: 'stop_agent' },
    { id: 4, api_name: 'new_session' }, { id: 5, api_name: 'load_session' },
    { id: 6, api_name: 'delete_session' }, { id: 7, api_name: 'import_local_options' },
  ] } };
  if (pathname === '/legacy/gradio_api/run/refresh') return { data: { data: ['<p>status</p>', { choices: [], value: [] }] } };
  if (pathname === '/api/live-status') return { data: { platforms: [
    { platform: 'qq', status: 'listening', detail: '数据库监听中', last_received: 0, last_id: 0, metrics: {} },
    { platform: 'wechat', status: 'off', detail: '已关闭', last_received: 0, last_id: 0, metrics: {} },
  ], settings: { qq: 'database', wechat: 'off', load_stickers: false, wechat_read_images: true } } };
  if (pathname === '/api/terminal/overview') return { data: { version: '0.5.5', platform: 'Darwin arm64', storage: '/tmp', counts: { messages: 1200, connections: 0, active_sessions: 0 }, capabilities: {}, receiver: { state: 'stopped', note: '', account: '' }, mcp: {}, sl: {} } };
  if (pathname === '/api/desktop/status') return { data: { app: 'ChatWeave', product: 'Tulpa', version: '0.5.5', settings: { api_base: '', model: '', has_api_key: false, configured: false, sender_url: '', has_sender_token: false, events_url: '', has_events_token: false, environment_overrides: [] }, storage: '/tmp/tulpa', busy: false, owned: false } };
  if (pathname === '/api/mcp') return { data: { enabled: false, port: 18777, running: false, url: '', error: '', connections: [], recent: [], storage_label: 'test', data_platforms: { qq: 1200 }, onebot_configured: false } };
  if (pathname === '/api/mcp/permissions') return { data: { items: permissions } };
  if (pathname === '/api/mcp/chat-overview') return { data: { groups: [], receiver: { state: 'stopped', note: '事件接收尚未启动' } } };
  if (pathname === '/api/mcp/operations') return { data: { operations: [] } };
  if (pathname === '/api/mcp/conversations' || pathname === '/api/mcp/live-groups' || pathname === '/api/mcp/live-friends') return { data: { items: [], has_more: false, next_offset: 0 } };
  if (pathname === '/api/desktop/preferences') return { data: { mode: '', background: false } };
  if (pathname === '/api/desktop/snowluma/managed') return { data: { enabled: false, phase: 'idle', phase_label: '空闲', available: false, consent_current: false, has_consent: false, version: '1.14.22', security_note: '', agreements: [], scope: [], login_available: false, qq_ready: false } };
  if (pathname === '/api/refresh-status') return { data: { platforms: [{ platform: 'qq', status: 'never', detail: '尚未刷新', last_sync_at: null, added: 0 }, { platform: 'wechat', status: 'never', detail: '尚未刷新', last_sync_at: null, added: 0 }], auto_refresh: { minutes: 0, load_stickers: false }, running_jobs: [], running_job: null } };
  if (pathname === '/api/chat-conversations') return { data: { conversations: [], has_more: false, next_offset: 0, unchanged: false, high_water: 0, epoch: 'e' } };
  if (pathname === '/api/watch-cards') return { data: { cards: [] } };
  if (pathname === '/api/data/settings') return { data: { scope: { qq: { enabled: true, conversations: null, account: '' }, wechat: { enabled: true, conversations: null, account: '' } }, conversations: [], imported: [], pending_imports: [], catalog_accounts: {} } };
  if (pathname === '/api/data/accounts') return { data: { qq: { accounts: [], selected: '', detail: '未找到 QQ 本机账号数据', supported: true }, wechat: { accounts: [], selected: '', detail: '未找到微信本机账号数据', supported: true } } };
  if (pathname === '/api/terminal/native') return { data: { platforms: {
    qq: { source: 'auto', data_root: '/tmp/qq-data', keys_file: '', configured: true, source_type: 'database', source_label: '本机数据库', requires_key: true },
    wechat: { source: 'auto', data_root: '', keys_file: '', configured: false },
  } } };
  if (pathname === '/api/read-options') return { data: { qq_days: { default: 30, min: 1, max: 3650, label: 'QQ 历史天数' }, qq_per_chat: { default: 500, min: 1, max: 10000, label: 'QQ 每会话条数' }, wechat_per_chat: { default: 500, min: 1, max: 10000, label: '微信每会话条数' } } };
  if (pathname === '/api/voice/settings') return { data: { strategy: 'lazy', counts: {}, queued: 0, local_asr_ready: false, discovery: {}, model: '', threads: 0, max_seconds: 0, max_bytes: 0 } };
  if (pathname === '/api/artifacts/status') return { data: { state: { running: false, result: null, error: null }, counts: {}, unique_artifacts: 0, cache_bytes: 0, inventories: [], conversations: [], onebot_configured: false, note: '测试状态' } };
  if (pathname === '/api/artifacts') return { data: { files: [], has_more: false, match_count: 0, note: '', next_offset: 0 } };
  if (pathname === '/api/collections') return { data: { collections: [], total: 0, next_offset: null } };
  if (pathname === '/api/tulpa') return { data: { control: { enabled: false }, scopes: [], directory: [] } };
  if (pathname === '/api/workspaces') return { data: { workspaces: [] } };
  if (pathname === '/api/vision-options') return { data: { default: 'ocr', native_available: false, note: '无原生识图' } };
  if (pathname === '/api/agent-presets') return { data: { default: 'deep', profiles } };
  if (pathname === '/api/ui-preferences') return { data: { show_reasoning: true } };
  if (pathname === '/api/knowledge/status') return { data: { state: {}, configured: false, groups: [], sync: [], note: '' } };
  return { data: {} };
};
