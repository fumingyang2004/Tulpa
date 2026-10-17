"""Native QQ/WeChat sources: paths and keys, accounts, catalog, import, live sync, cleanup.

Reading the local clients never sends anything. Keys stay on the backend; the UI
only shows whether they are configured. Deletion always goes through the native
delete-preview token and a separate confirmation of the exact counts.
"""
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Static

from . import model
from .base import View, esc, fill_table, make_table
from .forms import by_value, ChoiceModal, Field, FormModal, date_text, int_in, required
from .jobs import follow, stop_job
from .modals import ConfirmModal

PLATFORMS = ('qq', 'wechat')
LIVE_MODES = (('database', '监听新增消息'), ('off', '关闭'))
REFRESH_MINUTES = ((0, '关闭'), (5, '5 分钟'), (15, '15 分钟'), (30, '30 分钟'), (60, '1 小时'), (360, '6 小时'),
                   (720, '12 小时'), (1440, '1 天'), (4320, '3 天'), (10080, '7 天'))
LIVE_STATES = dict(starting='启动中', off='已关闭', client_stopped='客户端未运行', needs_baseline='需先读取一次历史',
                   receiving='读取中', listening='监听中', retrying='等待重试', degraded='读取异常')


def conv_label(c):
    return f'{model.platform_label(c.get("platform"))} · {model.one_line(c.get("conversation") or c.get("conversation_id"), 40)}'


class NativeView(View):
    title = '本机数据'
    subtitle = 'QQ / 微信数据来源、历史导入与实时同步；不会发送消息'
    BINDINGS = [Binding('p', 'paths', '路径'), Binding('k', 'keys', '密钥'), Binding('a', 'account', '账号'),
                Binding('c', 'catalog', '会话目录'), Binding('g', 'read', '导入'), Binding('s', 'sync', '增量同步'),
                Binding('l', 'live', '实时'), Binding('t', 'schedule', '定时'), Binding('m', 'media', '补载媒体'),
                Binding('j', 'json_import', 'JSON 导入'), Binding('e', 'reconnect', '重新接续'),
                Binding('i', 'install', '安装读取器'), Binding('delete', 'delete', '删除记录'), Binding('x', 'stop', '停止任务'),
                Binding('v', 'voice', '语音管理')]

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        with Horizontal(classes='cards'):
            yield Static('', classes='card', id='native-qq')
            yield Static('', classes='card', id='native-wechat')
        yield make_table('平台', '会话', '已导入', '会话 ID', id='native-table')
        yield Static('', classes='detail', id='native-job')
        yield Static('p 路径 · k 密钥 · a 账号 · c 读取目录 · g 导入 · s 同步 · l 实时 · t 定时 · m 媒体 · j JSON · '
                     'e 接续 · i 安装 · Del 删除 · x 停止 · v 语音', classes='hint')

    def on_mount(self):
        self.native, self.accounts, self.settings, self.live, self.refresh_status, self.components = {}, {}, {}, {}, {}, {}
        self.current_job = None
        self.query_one('#native-job').display = False

    def refresh_data(self):
        def fetch(c):
            try:
                components = c.get('/components')
            except Exception:  # noqa: BLE001 - component status is informative only
                components = {}
            return (c.get('/native'), c.api_get('/api/data/accounts'), c.api_get('/api/data/settings'),
                    c.api_get('/api/live-status'), c.api_get('/api/refresh-status'), components)
        self.start_load(fetch, self.show, '正在读取本机来源状态…')

    def show(self, result):
        native, accounts, settings, live, refresh, components = (r if isinstance(r, dict) else {} for r in result)
        self.native, self.accounts, self.settings = native.get('platforms') or {}, accounts, settings
        self.live, self.refresh_status, self.components = live, refresh, components
        for p in PLATFORMS:
            self.query_one(f'#native-{p}', Static).update(self.card(p))
        imported = {(c.get('platform'), c.get('conversation_id')): c.get('count') for c in settings.get('imported') or []}
        rows = self.conversations()
        fill_table(self.query_one('#native-table'), [
            (model.pair(c.get('platform'), c.get('conversation_id')),
             (model.platform_label(c.get('platform')), model.one_line(c.get('conversation') or '—', 32),
              str(imported.get((c.get('platform'), c.get('conversation_id')), 0)),
              model.one_line(c.get('conversation_id'), 30))) for c in rows])
        if not rows:
            self.set_state('empty', '尚无会话目录。先确认来源（p）和账号（a），再按 c 读取会话目录（不导入消息）。SL Docker 会自动检测。')
        running = refresh.get('running_jobs') or []
        if running and not self.polling('job'):
            job = running[0]
            follow(self, job.get('id'), box=self.query_one('#native-job'), label=f'后台任务（{job.get("kind")}）',
                   done=lambda j: self.reload_with('info', '后台任务已结束。'))

    def card(self, p):
        src = self.native.get(p) or {}
        acc = self.accounts.get(p) or {}
        live = next((s for s in self.live.get('platforms') or [] if s.get('platform') == p), {})
        sync = next((s for s in self.refresh_status.get('platforms') or [] if s.get('platform') == p), {})
        readers = self.components.get('readers') if isinstance(self.components.get('readers'), dict) else {}
        sl = src.get('source_type') == 'snowluma'
        source = src.get('source_label') or '本机数据库'
        lines = [f'[b #a78bfa]{model.platform_label(p)}[/]',
                 f'[#7f8a9a]来源[/] {esc(source)}',
                 f'[#7f8a9a]数据目录[/] {"由 SL 托管" if sl else esc(model.one_line(src.get("data_root") or "自动检测", 44))}',
                 f'[#7f8a9a]密钥[/] {"无需配置" if sl else "已配置" if src.get("keys_file") else "自动获取 / 未配置"}'
                 f' · [#7f8a9a]读取器[/] {"SL 接口" if sl else "已安装" if readers.get("installed") else "未安装"}',
                 f'[#7f8a9a]账号[/] {esc(acc.get("selected") or "未选择")}（发现 {len(acc.get("accounts") or [])} 个）',
                 f'[#7f8a9a]实时[/] {esc(LIVE_STATES.get(live.get("status"), live.get("status") or "—"))}'
                 f' · {esc(model.one_line(live.get("detail") or "", 40))}',
                 f'[#7f8a9a]上次同步[/] {esc(model.fmt_time(sync.get("last_sync_at")))} · {esc(sync.get("status") or "—")}']
        if acc.get('detail'):
            lines.append(f'[#eab308]{esc(model.one_line(acc["detail"], 60))}[/]')
        return '\n'.join(lines)

    def conversations(self, platform=None):
        rows = [c for c in self.settings.get('conversations') or [] if isinstance(c, dict)]
        return [c for c in rows if platform in (None, c.get('platform'))]

    def imported(self, platform=None):
        rows = [c for c in self.settings.get('imported') or [] if isinstance(c, dict)]
        return [c for c in rows if platform in (None, c.get('platform'))]

    def action_voice(self):
        from .base import selected_key
        from .voice_controls import voice_menu
        key = selected_key(self.query_one('#native-table'))
        highlighted = model.unpair(key) if key else None
        # Discovery reads already imported conversations; the highlighted row is only pre-ticked.
        voice_menu(self, PLATFORMS, lambda p: self.imported(p) or self.conversations(p), highlighted,
                   box=self.query_one('#native-job', Static))

    def pick_platform(self, title, then, lead=''):
        self.app.push_screen(ChoiceModal(title, [(model.platform_label(p), p) for p in PLATFORMS], lead),
                             lambda p: p and then(p))

    def job(self, fn, label, text):
        box = self.query_one('#native-job', Static)

        def ok(result):
            follow(self, (result or {}).get('job_id'), kind='data', box=box, label=label,
                   done=lambda j: self.reload_with('info', f'{label}已结束，状态已刷新。'))
        self.act(fn, ok, text, f'{label}未开始')

    # ---- source paths, keys, readers ------------------------------------

    def action_paths(self):
        def chosen(p):
            src = self.native.get(p) or {}

            def validate(v):
                body = dict(source=v['source'],data_root=v['data_root'].strip())
                if len(body['data_root']) > 2048: raise ValueError('数据目录过长。')
                if v['keys_file'].strip():
                    body['keys_file'] = v['keys_file'].strip()
                return body

            def done(body):
                if body:
                    self.act(lambda c: c.put(f'/native/{p}', body),
                             lambda r: self.reload_with('info', f'{model.platform_label(p)} 来源已保存。'),
                             '正在核对并保存路径…', '保存失败')
            self.app.push_screen(FormModal(f'{model.platform_label(p)} 本机来源', [
                Field('source', '读取来源', 'select', src.get('source') or 'auto',
                      by_value([('auto','自动检测'),('database','本机数据库')]+([('snowluma','SL Docker 历史消息')] if p=='qq' else []))),
                Field('data_root', '数据目录', value=src.get('data_root') or '', placeholder='客户端数据所在的绝对路径'),
                Field('keys_file', '密钥 JSON', value=src.get('keys_file') or '', placeholder='可选：已有密钥文件的绝对路径',
                      hint='只填写路径；密钥内容由后台在本机读取，不会显示。留空表示保留现有设置。'),
            ], validate, '保存'), done)
        self.pick_platform('选择要设置的平台', chosen)

    def action_keys(self):
        readers = self.components.get('readers') if isinstance(self.components.get('readers'), dict) else {}
        active_ok = bool(readers.get('active_key_supported'))

        def chosen(p):
            if (self.native.get(p) or {}).get('source_type') == 'snowluma':
                self.set_state('info', 'SL 历史读取无需数据库密钥；保持 SL 中的 QQ 登录，按 c 读取会话目录。')
                return
            account = (self.accounts.get(p) or {}).get('selected')
            methods = [('passive', '被动读取（不重启客户端）')] + ([('active', '主动捕获（需重启客户端）')] if active_ok else [])

            def validate(v):
                if v['method'] not in ('passive', 'active'):
                    raise ValueError('请选择方式。')
                if v['method'] == 'active' and not v['confirm']:
                    raise ValueError('主动捕获需要勾选确认：会让现有客户端正常退出后重新启动。')
                body = dict(method=v['method'])
                if account:
                    body['account'] = account
                if v['method'] == 'active':
                    body['confirm_start_client'] = True
                return body

            def done(body):
                if body:
                    self.job(lambda c: c.post(f'/native/{p}/extract', body), '密钥获取', '正在启动密钥获取…')
            note = '' if active_ok else f'当前架构（{readers.get("architecture") or "未知"}）不支持主动捕获，只能使用已提供或缓存的密钥。'
            self.app.push_screen(FormModal(f'{model.platform_label(p)} 数据库密钥', [
                Field('method', '方式', 'select', 'passive', by_value(methods)),
                Field('confirm', '我确认：主动捕获会让已运行的客户端正常退出并重新启动', 'check', False),
                Field('note', note or '被动失败后不会自动改用主动捕获。密钥不会显示或返回给界面。', 'note'),
            ], validate, '开始', lead=f'账号：{account or "未选择（由后台按唯一账号决定）"}'), done)
        self.pick_platform('获取哪个平台的密钥', chosen)

    def action_install(self):
        def confirmed(yes):
            if yes:
                box = self.query_one('#native-job', Static)
                self.act(lambda c: c.post('/native/install', {}),
                         lambda r: follow(self, (r or {}).get('job_id'), kind='component', box=box, label='读取器安装',
                                          done=lambda j: self.reload_with('info', '读取器安装任务已结束。')),
                         '正在开始安装…', '安装未开始')
        self.app.push_screen(ConfirmModal('安装本机读取器', '将下载并安装固定版本的 QQ / 微信读取组件到本项目目录。'
                                          '只安装缺失部分，不修改客户端本身。继续吗？', '安装'), confirmed)

    # ---- accounts and catalog -------------------------------------------

    def action_account(self):
        def chosen(p):
            acc = self.accounts.get(p) or {}
            found = acc.get('accounts') or []
            if not found:
                self.set_state('empty', acc.get('detail') or f'未找到 {model.platform_label(p)} 本机账号，请登录客户端后按 r 重新检测。')
                return
            self.app.push_screen(ChoiceModal(f'{model.platform_label(p)} 账号', [(str(a), str(a)) for a in found],
                                             '选择后按 c 读取该账号的会话目录；不会自动切换到其他账号。'),
                                 lambda a: a and self.set_account(p, a))
        self.pick_platform('选择平台', chosen)

    def set_account(self, p, account):
        self.accounts.setdefault(p, {})['selected'] = account
        self.query_one(f'#native-{p}', Static).update(self.card(p))
        self.set_state('info', f'{model.platform_label(p)} 将使用账号 {account}；按 c 读取会话目录。')

    def action_catalog(self):
        def chosen(p):
            body = dict(platforms=[p])
            account = (self.accounts.get(p) or {}).get('selected')
            if account:
                body['accounts'] = {p: account}
            self.job(lambda c: c.api_post('/api/data/catalog', body), '读取会话目录', '正在读取会话目录…')
        self.pick_platform('读取哪个平台的会话目录（不导入消息）', chosen)

    # ---- import -----------------------------------------------------------

    def action_read(self):
        scope = self.settings.get('scope') or {}
        fields = []
        for p in PLATFORMS:
            label = model.platform_label(p)
            current = (scope.get(p) or {}).get('conversations') or []
            fields += [Field(f'{p}_enabled', f'读取 {label}', 'check', (scope.get(p) or {}).get('enabled', p == 'qq')),
                       Field(f'{p}_convs', f'{label} 会话（不选表示全部会话）', 'multi',
                             [model.pair(p, c) for c in current],
                             [(conv_label(c), model.pair(p, c.get('conversation_id'))) for c in self.conversations(p)]),
                       Field(f'{p}_start', f'{label} 开始日期', placeholder='YYYY-MM-DD，可留空'),
                       Field(f'{p}_end', f'{label} 截止日期', placeholder='YYYY-MM-DD，可留空'),
                       Field(f'{p}_all', f'{label}：读取所选范围内全部消息（可能很慢）', 'check', False)]
        fields += [Field('qq_days', 'QQ 历史天数', value='30', hint='未填写 QQ 日期时生效。'),
                   Field('qq_per_chat', 'QQ 每会话条数', value='500'),
                   Field('wechat_per_chat', '微信每会话条数', value='500'),
                   Field('load_stickers', '同时载入表情包 / 图片', 'check', False)]

        def validate(v):
            body_scope, ranges, dated = {}, {}, False
            for p in PLATFORMS:
                convs = [model.unpair(x)[1] for x in v[f'{p}_convs']]
                item = dict(enabled=v[f'{p}_enabled'], conversations=convs or None)
                account = (self.accounts.get(p) or {}).get('selected')
                if account:
                    item['account'] = account
                body_scope[p] = item
                start, end = date_text(v[f'{p}_start'], '开始日期'), date_text(v[f'{p}_end'], '截止日期')
                if start and end and start > end:
                    raise ValueError('开始日期不能晚于截止日期。')
                ranges[p] = dict(start=start, end=end)
                dated = dated or bool(start or end)
            if not any(i['enabled'] for i in body_scope.values()):
                raise ValueError('请至少启用一个平台。')
            body = dict(scope=body_scope, load_stickers=v['load_stickers'],
                        all_messages={p: v[f'{p}_all'] for p in PLATFORMS},
                        limits=dict(qq_days=int_in(v['qq_days'], 'QQ 历史天数', 1, 3650),
                                    qq_per_chat=int_in(v['qq_per_chat'], 'QQ 每会话条数', 1, 10000),
                                    wechat_per_chat=int_in(v['wechat_per_chat'], '微信每会话条数', 1, 10000)))
            if dated:
                body['ranges'] = ranges
            else:
                body.update(start='', end='')
            return body

        def done(body):
            if not body:
                return
            parts = [f'{model.platform_label(p)}：{len(i["conversations"]) if i["conversations"] else "全部"} 个会话'
                     for p, i in body['scope'].items() if i['enabled']]
            if any(body['all_messages'].values()):
                parts.append('包含“全部消息”，读取可能耗时较长。')

            def confirmed(yes):
                if yes:
                    self.job(lambda c: c.api_post('/api/data/read', body), '导入聊天记录', '正在开始导入…')
            self.app.push_screen(ConfirmModal('开始导入', '将从本机客户端读取：\n' + '\n'.join(parts) +
                                              '\n读取只追加本地索引，可按 x 停止，已提交批次保留。', '开始导入'), confirmed)
        self.app.push_screen(FormModal('导入本机聊天记录', fields, validate, '下一步'), done)

    def action_json_import(self):
        def validate(v):
            body = dict(path=required(v['path'], '文件路径', 500))
            if v['platform']:
                body['platform'] = v['platform']
            return body

        def done(body):
            if body:
                self.act(lambda c: c.post('/import', body),
                         lambda r: self.reload_with('info', '导入完成：' + '，'.join(
                             f'{k} {v}' for k, v in (r or {}).items() if isinstance(v, (int, str)))),
                         '正在导入 JSON…', '导入失败')
        self.app.push_screen(FormModal('导入导出文件', [
            Field('path', '文件', placeholder='imports/ 下的 JSON / JSONL 文件，例如 imports/chat.json'),
            Field('platform', '平台', 'select', None, [(model.platform_label(p), p) for p in PLATFORMS],
                  placeholder='自动识别'),
        ], validate, '导入', lead='只接受本项目 imports/ 目录中的现有文件。'), done)

    # ---- live, sync, schedule --------------------------------------------

    def action_live(self):
        current = self.live.get('settings') or {}

        def done(body):
            if body:
                self.act(lambda c: c.api_put('/api/live-settings', body),
                         lambda r: self.reload_with('info', '实时同步设置已保存。'), '正在保存…', '保存失败')
        self.app.push_screen(FormModal('实时同步', [
            Field('qq', 'QQ', 'select', current.get('qq', 'database'), by_value(LIVE_MODES)),
            Field('wechat', '微信', 'select', current.get('wechat', 'database'), by_value(LIVE_MODES)),
            Field('load_stickers', '实时载入表情包 / 图片', 'check', bool(current.get('load_stickers'))),
        ], lambda v: dict(qq=v['qq'] or 'off', wechat=v['wechat'] or 'off', load_stickers=v['load_stickers']), '保存',
            lead='实时同步只读取客户端数据库中的新增消息。'), done)

    def action_sync(self):
        scope = self.settings.get('scope') or {}
        enabled = [p for p in PLATFORMS if (scope.get(p) or {}).get('enabled', True)]

        def validate(v):
            if not v['platforms']:
                raise ValueError('请至少选择一个平台。')
            return dict(platforms=v['platforms'], load_stickers=v['load_stickers'])

        def done(body):
            if body:
                self.job(lambda c: c.api_post('/api/sync', body), '增量同步', '正在开始增量同步…')
        self.app.push_screen(FormModal('增量同步', [
            Field('platforms', '平台', 'multi', enabled, [(model.platform_label(p), p) for p in PLATFORMS]),
            Field('load_stickers', '同时载入表情包 / 图片', 'check', False),
        ], validate, '同步'), done)

    def action_schedule(self):
        current = self.refresh_status.get('auto_refresh') or {}

        def done(body):
            if body:
                self.act(lambda c: c.api_post('/api/auto-refresh', body),
                         lambda r: self.reload_with('info', '定时同步设置已保存。'), '正在保存…', '保存失败')
        self.app.push_screen(FormModal('定时同步', [
            Field('minutes', '间隔', 'select', current.get('minutes', 0), by_value(REFRESH_MINUTES)),
            Field('load_stickers', '定时同步时载入表情包 / 图片', 'check', bool(current.get('load_stickers'))),
        ], lambda v: dict(minutes=v['minutes'] if isinstance(v['minutes'], int) else 0, load_stickers=v['load_stickers']),
            '保存'), done)

    def action_reconnect(self):
        def chosen(p):
            def confirmed(yes):
                if yes:
                    self.act(lambda c: c.api_post('/api/sync/reconnect', dict(platform=p)),
                             lambda r: self.reload_with('info', f'{model.platform_label(p)} 将在下次同步时重新接续。'),
                             '正在重置接续点…', '操作失败')
            self.app.push_screen(ConfirmModal('重新接续', f'重置 {model.platform_label(p)} 的增量接续点？\n'
                                              '下次同步从最近成功导入的快照接续；已导入的消息保留。', '重置'), confirmed)
        self.pick_platform('重新接续哪个平台', chosen, '用于客户端重装或数据库变化后同步卡住的情况。')

    # ---- media and deletion ----------------------------------------------

    def range_form(self, title, p, submit, extra=(), lead=''):
        fields = [Field('conversations', '会话（不选表示该平台全部已导入会话）', 'multi', [],
                        [(conv_label(c), c.get('conversation_id')) for c in self.imported(p)]),
                  Field('start', '开始日期', placeholder='YYYY-MM-DD（必填）'),
                  Field('end', '截止日期', placeholder='YYYY-MM-DD（必填，含当天）'), *extra]

        def validate(v):
            start, end = date_text(v['start'], '开始日期', False), date_text(v['end'], '截止日期', False)
            if start > end:
                raise ValueError('开始日期不能晚于截止日期。')
            body = dict(platform=p, conversations=v['conversations'], start=start, end=end)
            if 'load_stickers' in v:
                body['load_stickers'] = v['load_stickers']
            return body
        return FormModal(title, fields, validate, submit, lead=lead)

    def action_media(self):
        def chosen(p):
            def done(body):
                if body:
                    self.job(lambda c: c.api_post('/api/data/media-refresh', body), '补载媒体', '正在开始补载媒体…')
            self.app.push_screen(self.range_form(f'{model.platform_label(p)} 补载媒体', p, '开始',
                                                 [Field('load_stickers', '包含表情包', 'check', True)]), done)
        self.pick_platform('补载哪个平台的图片 / 表情', chosen)

    def action_delete(self):
        def chosen(p):
            def done(body):
                if body:
                    self.act(lambda c: c.api_post('/api/data/delete-preview', body), self.confirm_delete,
                             '正在计算将删除的范围…', '预览失败')
            self.app.push_screen(self.range_form(f'删除 {model.platform_label(p)} 已导入记录', p, '预览',
                                                 lead='只删除本工具已导入的记录及其独占文件，不影响客户端里的原始聊天。'), done)
        self.pick_platform('删除哪个平台的已导入记录', chosen)

    def confirm_delete(self, preview):
        preview = preview if isinstance(preview, dict) else {}
        token = preview.get('token')
        if not token:
            self.set_state('error', '预览未返回确认令牌，未删除。')
            return
        scope = '全部会话' if preview.get('all_conversations') else f'{preview.get("conversations", 0)} 个会话'
        body = (f'平台：{model.platform_label(preview.get("platform"))}\n范围：{scope}\n'
                f'时间：{preview.get("start")} 至 {preview.get("end")}\n'
                f'将删除 {preview.get("messages", 0)} 条消息、{preview.get("media", 0)} 个媒体引用。\n\n'
                '此操作不可恢复。预览 10 分钟内有效，范围内数据变化时会拒绝删除。')

        def confirmed(yes):
            if yes:
                self.job(lambda c: c.api_post('/api/data/delete', dict(token=token)), '删除记录', '正在删除…')
        self.app.push_screen(ConfirmModal('确认删除已导入记录', body, '永久删除', danger=True), confirmed)

    def action_stop(self):
        stop_job(self)
