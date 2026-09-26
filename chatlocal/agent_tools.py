"""Bounded, read-only chat tools shared by the agent runtime and checks."""
import json
import threading
import sqlite3
from dataclasses import replace

from .normalize import display_time,safe_reply
from .retrieval import Plan, date_bound, scope_sql
from .store import tokens
from .media import hydrate,public_media,media_at
from .identity import group_identities,classify_mention,IDENTITY_NOTE,MENTION_NOTE
from .people import directory as people_directory,find as find_people,public_person,NOTE as PEOPLE_NOTE,fold


def tool(name, description, properties=None, required=()):
    return dict(name=name, description=description, parameters=dict(
        type='object', properties=properties or {}, required=list(required), additionalProperties=False))


TEXT = {'type': 'string'}
NUMBER = {'type': 'integer'}
PAGE = dict(type='integer', minimum=1, maximum=20, default=12, description='每页1–20条，默认12')
OFFSET = dict(type='integer', minimum=0, maximum=10000, default=0, description='分页偏移，沿用next_offset；默认0')
OVERVIEW_TERMS = ('通知','截止','提交','报名','改到','调整','变更','安排','会议','作业','课程','放假')
FILTERS = {
    'platform': {'type': 'string', 'enum': ['qq', 'wechat']},
    'conversation_id': TEXT,
    'start': dict(type='string', description='北京时间 YYYY-MM-DD 或 YYYY-MM-DD HH:MM；仅能缩小用户范围'),
    'end': dict(type='string', description='结束日期包含当天；精确时刻不含'),
    'limit': PAGE,
    'offset': OFFSET,
    'has_media':dict(type='boolean',description='true只查有图片/表情的消息，false只查无媒体消息；不填则全部'),
    'has_voice':dict(type='boolean',description='true只查原生语音，false排除语音；语音文字仅在已转写后才能按关键词搜到'),
}
SCHEMAS = [
    tool('transcribe_voice','按需理解一条 QQ/微信原生语音。使用本地 ASR，不上传音频；已转写则复用结果。'
         '仅可处理当前用户范围内消息；返回机器转写和原始消息ID，可能有误听。关键上下文是语音时应主动调用。每轮最多3条。',
         {'message_id':NUMBER},['message_id']),
    tool('get_messages_since','按需读取某个本地消息ID之后入库的消息，含迟到的旧消息。仍受当前平台、会话、日期和快照限制。'
         '适合补查新入库记录；通常先按问题搜索相关内容，无需逐条读完全部消息。',
         dict(FILTERS,after_id=dict(type='integer',minimum=0)),['after_id']),
    tool('get_data_status', '查看当前允许查询范围内的已导入消息数及首尾时间。最新消息时间不是实时同步时间。'),
    tool('find_people','按姓名、备注、群昵称或精确账号ID寻找人物。查询某人的沟通/各群昵称时先用此工具，不把姓名当正文关键词。'
         '用同平台相同sender_id连接各群历史昵称和私聊；同名不同账号分别列出，不跨平台认人。'
         '多个候选须按会话等线索区分，不可自动选第一个。返回目录，无正文，下一步read_person_messages。',
         {'query':TEXT,'platform':FILTERS['platform'],'conversation_id':TEXT,
          'limit':dict(type='integer',minimum=1,maximum=10,default=5),'offset':OFFSET},['query']),
    tool('read_person_messages','按find_people确认的平台和账号读取沟通，不要求正文出现其昵称。'
         '默认跨允许会话均衡读取：私聊包含双方发言，群聊命中此账号发言并附附近上下文。'
         '群发言不能自动视为与本人互动；请核对上下文和@线索。可限定conversation_type或conversation_id，query可选正文主题。'
         '翻页沿用snapshot_max_id和next_offset；只返回有限样本。',
         dict(FILTERS,sender_id=TEXT,query=TEXT,
              conversation_type=dict(type='string',enum=['direct','group']),
              snapshot_max_id=dict(type='integer',minimum=0)),['platform','sender_id']),
    tool('get_my_identity','查询本人在各群的稳定账号ID与该群显示名，基于明确标为本人发送的已导入记录。'
         '身份元数据可早于问题日期，但不返回日期外正文；仍限制用户的平台和会话。按群名query可筛选。',
         {'platform':FILTERS['platform'],'conversation_id':TEXT,'query':TEXT,'limit':PAGE,'offset':OFFSET}),
    tool('find_mentions','查找可能@本人的群消息，自动使用同平台同群的本人账号与显示名，无需先遍历群列表。'
         '结果目前是昵称/账号文本匹配候选，缺少原生@目标ID；不可断言一定@本人或未匹配的都不是本人。'
         '日期/平台/会话仍受用户范围限制；include_all=true附带@全体，默认只读本人候选。',
         {k:FILTERS[k] for k in ('platform','conversation_id','start','end','limit','offset')} |
         {'include_all':dict(type='boolean'),'snapshot_max_id':dict(type='integer',minimum=0)}),
    tool('list_conversations', '按会话名称寻找群或联系人，每页最多20项。只返回会话目录，不含消息正文，不能用于回答聊天内容。'
         '获得会话ID后应read_conversation/search_messages；跨会话概览直接read_overview，无需翻完目录。',
         {'query': TEXT, 'platform': FILTERS['platform'], 'limit': PAGE, 'offset': OFFSET}),
    tool('read_overview', '读取当前用户选定范围的有限消息样本，适合“我错过了什么”或时间段概览。'
         '按平台、会话、日期分散取样，优先通知/安排等文本线索，兼顾近期消息；不是完整审计，也不知道客户端已读状态。'
         '返回真实消息ID和正文；根据线索再search_messages/get_context查证，不要反复翻会话目录。',
         {'limit':dict(type='integer',minimum=1,maximum=40,default=32,description='默认32条，最多40条；留出补查预算'),
          'offset':OFFSET,'snapshot_max_id':dict(type='integer',minimum=0,description='翻页沿用返回值，新概览不填')}),
    tool('search_messages', '搜索 QQ/微信文本。关键词用空格分隔，任一关键词匹配。无结果可换同义词。'
         '返回命中及有限相邻对话，hit_ids标记命中，context_windows按时间串起各命中的上下文。'
         '请结合整段理解谁在谈什么；窗口截断或指代/起因不明时get_context扩大阅读，不能仅凭命中句断言。',
         dict(FILTERS, query=dict(type='string',description='正文关键词；指定sender_id或has_media=true时可为空'),
              sender_id=dict(type='string',description='精确发送者账号，须同时指定platform；昵称用find_people解析'),
              author={'type':'string','enum':['any','self','others']}), ['query']),
    tool('get_context', '读取一条已知消息及同会话前后各若干条消息，含图片信息。仍受用户时间/平台/会话范围限制。',
         {'message_id': NUMBER, 'radius': dict(type='integer',minimum=0,maximum=8,default=3, description='0–8，默认 3')}, ['message_id']),
    tool('get_media','获取消息的本地图片/表情信息与可用状态，不读取图片内容；需要理解内容请调用 inspect_image。',
         {'message_id':NUMBER},['message_id']),
    tool('inspect_image','按需查看一条已定位消息的指定图片。根据本轮vision_mode使用本地OCR或DeepSeek原生视觉；云端只接收该图和附近少量聊天。'
         'GIF最多抽3帧。OCR不能解释无文字表情的语义；不要猜图。每轮最多3张。',
         {'message_id':NUMBER,'index':dict(type='integer',description='图片序号，默认0'),
          'question':dict(type='string',description='希望从图中确认的具体问题')},['message_id']),
    tool('read_conversation', '读取指定会话的文本和媒体信息；最新N条用 order=newest,limit=N（最多50）。'
         '继续翻页沿用返回的 snapshot_max_id、order 和 next_offset；默认 oldest 从最早开始。'
         '若返回 has_more，则不能把当前页说成完整会话。',
         dict(FILTERS,limit=dict(type='integer',minimum=1,maximum=50,default=12,description='1–50，默认12；最新50条可一次读取'),
              order=dict(type='string',enum=['oldest','newest'],description='最早优先或最新优先'),
              snapshot_max_id=dict(type='integer',description='继续翻页时沿用上一页返回值，避免新导入消息导致页重复；新查询不填')),
         ['platform','conversation_id']),
]
LABELS = {'get_data_status':'检查数据范围','list_conversations':'查找会话',
          'transcribe_voice':'本地转写语音',
          'find_people':'核对人物账号与别名','read_person_messages':'按账号读取沟通',
          'get_messages_since':'读取关注卡新增消息',
          'get_my_identity':'核对本人的群内身份','find_mentions':'查找可能@本人的消息',
          'read_overview':'读取跨会话概览',
          'search_messages':'搜索聊天记录','get_context':'展开附近聊天','read_conversation':'读取会话时间段',
          'get_media':'查看图片信息','inspect_image':'按需识别图片'}


from .artifact_tools import ArtifactTools, schemas as artifact_schemas
SCHEMAS.extend(artifact_schemas(tool,FILTERS,TEXT,PAGE,OFFSET))
LABELS.update(search_files='查找文件资料',prepare_file='按需解析文件',
              search_file_content='搜索文件正文',read_file_chunks='读取文件原文')
from .investigation_tools import InvestigationTools,schemas as investigation_schemas
SCHEMAS.extend(investigation_schemas(tool))
LABELS.update(query_communication_db='受限SQL调查',get_interaction_threads='查找参与者交互',
              get_group_knowledge='读取群公告与精华',evidence_collection='管理证据集合')


from .workspace_tools import WorkspaceTools
from .qq_tools import QQTools
LABELS.update(read_qq_group='读取 QQ 群资料',qq_group_admin='提出群管理操作')


class ChatTools(ArtifactTools,InvestigationTools,WorkspaceTools,QQTools):
    def __init__(self, store, plan, *, max_messages=100, max_chars=24000, max_calls=10,vision_config=None):
        self.store, self.plan = store, plan
        self.max_messages, self.max_chars, self.max_calls = max_messages, max_chars, max_calls
        self.messages, self.originals, self.contexts = {}, {}, {}
        self.used_chars = self.calls = 0
        self.incomplete = False
        self.inspections={}
        self.voice_calls=set()
        self.vision_config=vision_config
        self.message_queries=0
        self.overview_used=False
        self.context_requirements={}
        self.mention_search=None
        self.people={}
        self.people_index=None
        self.lock = threading.RLock()
        self.workspace_context=None
        self.read_only=False
        self.schemas=SCHEMAS
        where, args = scope_sql(plan)
        with store.connect() as db:
            self.scope_count = db.execute(f'SELECT count(*) FROM messages m WHERE {where}',args).fetchone()[0]
            self.knowledge_snapshot=db.execute('SELECT coalesce(max(id),0) FROM qq_knowledge').fetchone()[0]

    def _validate(self, name, args):
        schema = next((t['parameters'] for t in self.schemas if t['name']==name),None)
        if schema is None or not isinstance(args,dict):
            raise ValueError('未知工具或参数格式不正确。')
        if set(args)-set(schema['properties']) or set(schema['required'])-set(args):
            raise ValueError('工具参数缺失或包含未允许的字段。')
        def validate(key,value,spec):
            if spec['type']=='integer' and type(value) is not int:
                raise ValueError(f'{key} 必须是整数。')
            if spec['type']=='integer' and (value<spec.get('minimum',value) or value>spec.get('maximum',value)):
                raise ValueError(f'{key} 允许范围为 {spec.get("minimum",0)}–{spec.get("maximum","不限")}；请修改此参数后重试。')
            if spec['type']=='boolean' and type(value) is not bool:raise ValueError(f'{key} 必须是布尔值。')
            if spec['type']=='string' and (not isinstance(value,str) or len(value)>spec.get('maxLength',600)):
                raise ValueError(f'{key} 必须是不超过 {spec.get("maxLength",600)} 字的字符串。')
            if spec['type']=='array':
                if not isinstance(value,list) or not spec.get('minItems',0)<=len(value)<=spec.get('maxItems',30):
                    raise ValueError(f'{key} 数组长度超出允许范围。')
                for i,item in enumerate(value):validate(f'{key}[{i}]',item,spec['items'])
            if spec['type']=='object':
                if not isinstance(value,dict) or set(value)-set(spec['properties']) or set(spec.get('required',[]))-set(value):
                    raise ValueError(f'{key} 对象字段不符合要求。')
                for k,v in value.items():validate(k,v,spec['properties'][k])
            if 'enum' in spec and value not in spec['enum']:
                raise ValueError(f'{key} 不在允许范围内。')
        for key,value in args.items():
            validate(key,value,schema['properties'][key])
            if key in schema['required'] and isinstance(value,str) and not value.strip() and not (name=='search_messages' and key=='query' and (args.get('has_media') is True or args.get('has_voice') is True or args.get('sender_id'))):
                raise ValueError(f'{key} 不能为空。')
        if args.get('snapshot_max_id',0)<0: raise ValueError('快照 ID 不能为负数。')
        if name=='search_messages' and args.get('sender_id') and not args.get('platform'):
            raise ValueError('按账号搜索必须同时指定platform，不能跨平台合并同号账号。')

    def _scope(self, args):
        plan = replace(self.plan)
        if args.get('platform'):
            if args['platform'] not in plan.platforms:
                raise ValueError('不能查询用户未选择的平台。')
            plan.platforms = [args['platform']]
        where, values = scope_sql(plan)
        if args.get('conversation_id'):
            where += ' AND m.conversation_id=?'
            values.append(args['conversation_id'])
        if 'has_media' in args:where+=" AND m.media_json"+('!=' if args['has_media'] else '=')+"'[]'"
        if 'has_voice' in args:where+=' AND '+('' if args['has_voice'] else 'NOT ')+'EXISTS(SELECT 1 FROM voice_sources v WHERE v.message_id=m.id)'
        for key,op in [('start','>='),('end','<')]:
            if args.get(key):
                value = date_bound(args[key],key=='end')
                where += f' AND m.timestamp{op}?'
                values.append(value)
        return where,values

    def _admit(self, rows, *, char_limit=None):
        """Only this whitelist becomes tool output; provenance and paths stay local."""
        admitted=[]
        for row in rows:
            row=hydrate(row) if 'media' not in dict(row) else dict(row)
            mid=row['id']
            if self.plan.snapshot_max_id is not None and mid>self.plan.snapshot_max_id:continue
            if mid in self.messages:
                admitted.append(self.messages[mid]); continue
            entry={k:row[k] for k in ('id','platform','conversation_id','conversation','conversation_type','sender','is_self')}
            if row.get('sender_id'):entry['sender_id']=row['sender_id']
            content_limit=2000
            entry.update(time=display_time(row['timestamp'])+' +08:00',content=row['content'][:content_limit],
                         truncated=len(row['content'])>content_limit)
            entry['media']=[{k:v for k,v in item.items() if k!='url'} for item in public_media(mid,row.get('media',[]))]
            from .voice import evidence as voice_evidence
            voice=voice_evidence(self.store,mid)
            if voice:
                entry['voice']=voice;row['voice']=voice
                entry['truncated'] |= bool(voice.get('truncated'))
            reply=safe_reply(row.get('reply_to'))
            if reply:
                entry['reply_to']={k:str(reply.get(k,''))[:800] for k in ('sender','content')}
            size=len(json.dumps(entry,ensure_ascii=False))
            ceiling=self.max_chars if char_limit is None else min(self.max_chars,char_limit)
            if len(self.messages)>=self.max_messages or self.used_chars+size>ceiling:
                self.incomplete=True
                continue
            self.messages[mid]=entry
            self.originals[mid]=row
            self.used_chars+=size
            self.incomplete |= entry['truncated']
            admitted.append(entry)
        return admitted

    def _page(self, rows, total, offset):
        selected=self._admit(rows)
        more=offset+len(rows)<total
        self.incomplete |= more
        return dict(messages=selected,match_count=total,has_more=more,
                    next_offset=offset+len(rows) if more else None,
                    budget_limited=len(selected)<len(rows),
                    media_note='media只是资源信息，图片正文尚未读取。问题涉及图中文字或含义时，优先inspect_image查看可用普通图片；反复搜索图中文字不会有结果。',
                    remaining_messages=self.max_messages-len(self.messages),
                    remaining_chars=self.max_chars-self.used_chars)

    def execute(self,name,args):
        with self.lock:
            self.calls+=1
            if self.calls>self.max_calls:
                return {'error':'已达到本轮工具调用上限，请基于已取得证据回答并说明覆盖限制。'}
            try:
                if self.workspace_context and name=='evidence_collection':
                    return {'error':'工作区资料通过 workspace_material/workspace_read 管理；不能访问或修改其他共享集合。'}
                self._validate(name,args)
            except ValueError as exc:
                # Validation messages are authored here; never return DB/provider errors.
                return {'error':str(exc),'error_code':'invalid_arguments'}
            try:
                if name not in ('get_data_status','list_conversations'):
                    self.message_queries+=1
                result=getattr(self,'_'+name)(args)
                if self.workspace_context:self._ws_capture(name,args)
                return result
            except (ValueError,TypeError,KeyError,sqlite3.DatabaseError) as exc:
                if self.workspace_context and name.startswith('workspace_') and isinstance(exc,ValueError):
                    return {'error':str(exc),'error_code':'workspace_validation'}
                return {'error':'查询参数无效、超出允许范围或消息不存在。请检查参数；不能扩大用户选定范围。'}

    def _get_data_status(self,args):
        where,values=self._scope(args)
        with self.store.connect() as db:
            rows=db.execute(f'''SELECT platform,count(*) count,min(timestamp) first,max(timestamp) last
                FROM messages m WHERE {where} GROUP BY platform''',values).fetchall()
        return dict(platforms=[dict(platform=r['platform'],count=r['count'],
                    first=display_time(r['first']),last=display_time(r['last'])) for r in rows],
                    note='仅当前已入库消息，不代表完整客户端历史；最新消息时间不是同步时钟。',scope=vars(self.plan))

    def _people_directory(self):
        if self.people_index is None:self.people_index=people_directory(self.store,self.plan,strict_dates=bool(self.workspace_context))
        return self.people_index

    def _remember_person(self,person):
        self.people[(person['platform'],person['sender_id'])]=dict(platform=person['platform'],sender_id=person['sender_id'],
            aliases=list(dict.fromkeys(a['name'] for a in person['aliases']))[:8])

    def _find_people(self,args):
        self._scope(args)  # Explicit platform must respect the user's hard scope.
        index,unknown=self._people_directory()
        candidates=find_people(index,args['query'],args.get('platform'),args.get('conversation_id'))
        offset,limit=args.get('offset',0),args.get('limit',5)
        page=[public_person(p) for p in candidates[offset:offset+limit]]
        if len(candidates)==1:self._remember_person(candidates[0])
        unresolved=[dict(platform=r['platform'],conversation_id=r['conversation_id'],conversation=r['conversation'],name=r['sender'])
            for r in unknown if fold(args['query']) in fold(r['sender'])
            and (not args.get('platform') or r['platform']==args['platform'])
            and (not args.get('conversation_id') or r['conversation_id']==args['conversation_id'])]
        return dict(people=page,candidate_count=len(candidates),ambiguous=len(candidates)>1,
            has_more=offset+limit<len(candidates),next_offset=offset+limit if offset+limit<len(candidates) else None,
            unresolved_names=unresolved[:10],unresolved_count=len(unresolved),note=PEOPLE_NOTE if not self.workspace_context else
            '工作区身份目录也受当前日期、平台、会话和快照限制；同号别名不跨平台合并，目录不是正文证据。')

    def _read_person_messages(self,args):
        where,values=self._scope(args)
        index,_=self._people_directory()
        person=index.get((args['platform'],args['sender_id']))
        if person is None:raise ValueError('当前允许范围没有该账号身份')
        direct=list(dict.fromkeys(c['conversation_id'] for c in person['direct_conversations']))
        where+=' AND (m.sender_id=?';values.append(args['sender_id'])
        if direct:
            where+=' OR (m.conversation_type=\'direct\' AND m.conversation_id IN ('+','.join('?' for _ in direct)+'))'
            values.extend(direct)
        where+=')'
        if args.get('conversation_type'):
            where+=' AND m.conversation_type=?';values.append(args['conversation_type'])
        query=args.get('query','').strip()
        if query:
            terms=list(dict.fromkeys(query.lower().split()))[:12]
            where+=' AND ('+' OR '.join('instr(lower(m.content),?)>0' for _ in terms)+')';values+=terms
        with self.store.connect() as db:
            snapshot=args.get('snapshot_max_id')
            if snapshot is None:snapshot=db.execute(f'SELECT coalesce(max(id),0) FROM messages m WHERE {where}',values).fetchone()[0]
            where+=' AND m.id<=?';values.append(snapshot)
            total=db.execute(f'SELECT count(*) FROM messages m WHERE {where}',values).fetchone()[0]
            rows=db.execute(f'''WITH candidates AS (
                SELECT m.*,row_number() OVER(PARTITION BY platform,conversation_id ORDER BY timestamp DESC,id DESC) slot
                FROM messages m WHERE {where})
                SELECT * FROM candidates ORDER BY slot,CASE WHEN conversation_type='direct' THEN 0 ELSE 1 END,
                timestamp DESC,id DESC LIMIT ? OFFSET ?''',values+[args.get('limit',12),args.get('offset',0)]).fetchall()
        self._remember_person(person)
        result=self._page(rows,total,args.get('offset',0))
        result.update(person=public_person(person),snapshot_max_id=snapshot,
            note='命中包括该账号发言及其私聊中的本人发言；群聊附近上下文含其他人，不全是与本人沟通。需依据完整上下文判断互动，不能按时间相邻断言。')
        # Freeze adjacent context too when paginating this person's messages.
        return self._search_context(result,args|{'snapshot_max_id':snapshot})

    def _get_messages_since(self,args):
        where,values=self._scope(args)
        where+=' AND m.id>?';values.append(args['after_id'])
        with self.store.connect() as db:
            total=db.execute(f'SELECT count(*) FROM messages m WHERE {where}',values).fetchone()[0]
            rows=db.execute(f'SELECT * FROM messages m WHERE {where} ORDER BY m.id LIMIT ? OFFSET ?',
                values+[args.get('limit',12),args.get('offset',0)]).fetchall()
        return self._search_context(self._page(rows,total,args.get('offset',0)),args)

    def _identity_groups(self,args):
        # Validate explicit platform before the metadata-only date relaxation.
        self._scope(args)
        plan=replace(self.plan,platforms=[args['platform']] if args.get('platform') else self.plan.platforms)
        groups=group_identities(self.store,plan,strict_dates=bool(self.workspace_context))
        if args.get('conversation_id'):groups=[g for g in groups if g['conversation_id']==args['conversation_id']]
        if args.get('query'):groups=[g for g in groups if args['query'].casefold() in g['conversation'].casefold()]
        return groups

    def _get_my_identity(self,args):
        groups=self._identity_groups(args);offset=args.get('offset',0);limit=args.get('limit',12)
        page=groups[offset:offset+limit]
        where,values=self._scope(args)
        ids=[i['source_message_id'] for g in page for i in g['identities'][:8] if i['source_message_id'] is not None]
        rows=[]
        if ids:
            with self.store.connect() as db:
                rows=db.execute(f'SELECT * FROM messages m WHERE {where} AND id IN ({",".join("?" for _ in ids)})',values+ids).fetchall()
        messages=self._admit(rows)
        # Do not expose old message IDs as if their bodies were read this round.
        identities=[dict(g,identities=[{k:v for k,v in i.items() if k!='source_message_id'} for i in g['identities'][:8]],
                         more_identities=len(g['identities'])>8) for g in page]
        return dict(identities=identities,messages=messages,has_more=offset+limit<len(groups),
                    next_offset=offset+limit if offset+limit<len(groups) else None,
                    unknown_groups=sum(not g['identities'] for g in groups),note=IDENTITY_NOTE)

    def _find_mentions(self,args):
        groups={(g['platform'],g['conversation_id']):g for g in self._identity_groups(args)}
        where,values=self._scope(args)
        with self.store.connect() as db:
            snapshot=args.get('snapshot_max_id')
            if snapshot is None:snapshot=db.execute(f'SELECT coalesce(max(id),0) FROM messages m WHERE {where}',values).fetchone()[0]
            rows=db.execute(f'''SELECT * FROM messages m WHERE {where} AND id<=? AND conversation_type='group'
                AND is_self IS NOT 1 AND (instr(content,'@')>0 OR instr(content,'＠')>0)
                ORDER BY timestamp DESC,id DESC''',values+[snapshot]).fetchall()
        candidates=[];matches={};counts=dict(self_candidates=0,all_mentions=0,unresolved=0)
        for row in rows:
            match=classify_mention(row,groups.get((row['platform'],row['conversation_id'])))
            kind=match['kind'];counts[{'self_candidate':'self_candidates','all':'all_mentions','unresolved':'unresolved'}[kind]]+=1
            if kind=='self_candidate' or (kind=='all' and args.get('include_all',False)):
                candidates.append(row);matches[row['id']]=match
        offset=args.get('offset',0);selected=candidates[offset:offset+args.get('limit',12)]
        result=self._page(selected,len(candidates),offset)
        self.mention_search=dict(**counts,groups_without_identity=sum(not g['identities'] for g in groups.values()),
                                 snapshot_max_id=snapshot,native_target_ids_available=False)
        result.update(matches=[matches[m['id']] for m in result['messages']],identity_note=IDENTITY_NOTE,
                      mention_note=MENTION_NOTE,coverage=self.mention_search,snapshot_max_id=snapshot)
        return result

    def _list_conversations(self,args):
        where,values=self._scope(args)
        if args.get('query'):
            where+=' AND instr(lower(m.conversation),?)>0'; values.append(args['query'].lower())
        with self.store.connect() as db:
            rows=[dict(r) for r in db.execute(f'''SELECT platform,conversation_id,max(conversation) conversation,
                max(conversation_type) conversation_type,count(*) count FROM messages m WHERE {where} GROUP BY platform,conversation_id
                ORDER BY count DESC,platform,conversation_id LIMIT ? OFFSET ?''',
                values+[args.get('limit',12)+1,args.get('offset',0)])]
        more=len(rows)>args.get('limit',12)
        return dict(conversations=rows[:args.get('limit',12)],has_more=more,
                    next_offset=args.get('offset',0)+args.get('limit',12) if more else None,
                    note='仅会话目录，不含消息证据。下一步读取目标会话正文；整体概览用read_overview，不必枚举所有会话。')

    def _read_overview(self,args):
        where,values=self._scope({})
        limit,offset=args.get('limit',32),args.get('offset',0)
        # Local heuristics select leads, not facts or a claim that these are unread.
        signals=' OR '.join(f"instr(m.content,'{word}')>0" for word in OVERVIEW_TERMS)
        with self.store.connect() as db:
            snapshot=args.get('snapshot_max_id')
            if snapshot is None:
                snapshot=db.execute(f'SELECT coalesce(max(id),0) FROM messages m WHERE {where}',values).fetchone()[0]
            where+=' AND m.id<=?';values.append(snapshot)
            total=db.execute(f'SELECT count(*) FROM messages m WHERE {where}',values).fetchone()[0]
            conversations=db.execute(f'SELECT count(*) FROM (SELECT 1 FROM messages m WHERE {where} GROUP BY platform,conversation_id)',values).fetchone()[0]
            rows=db.execute(f'''WITH candidates AS (
                SELECT m.*,CASE WHEN {signals} THEN 1 ELSE 0 END AS signal,
                  CAST((timestamp/1000+28800)/86400 AS INTEGER) AS day
                FROM messages m WHERE {where}
              ), days AS (
                SELECT *,row_number() OVER(PARTITION BY platform,conversation_id,day
                  ORDER BY signal DESC,timestamp DESC,id DESC) day_slot FROM candidates
              ), chats AS (
                SELECT *,row_number() OVER(PARTITION BY platform,conversation_id
                  ORDER BY day_slot,signal DESC,timestamp DESC,id DESC) chat_slot FROM days
              ), platforms AS (
                SELECT *,row_number() OVER(PARTITION BY platform
                  ORDER BY chat_slot,signal DESC,timestamp DESC,id DESC) platform_slot FROM chats
              ) SELECT * FROM platforms ORDER BY platform_slot,platform LIMIT ? OFFSET ?''',
                values+[limit,offset]).fetchall()
        result=self._page(rows,total,offset)
        self.overview_used=True
        result.update(snapshot_max_id=snapshot,sampled=len(result['messages'])<total,
            sampled_conversations=len({(m['platform'],m['conversation_id']) for m in result['messages']}),
            total_conversations=conversations,
            note='这是按平台/会话/日期分散的有限样本，优先通知等关键词线索，不代表完整覆盖或真正未读。'
                 '请从样本发现主题，再搜索或展开上下文核对；不要直接认定群通知是用户个人任务。')
        return result

    def _search_messages(self,args):
        query=args['query'].strip()
        if not query and not args.get('has_media') and not args.get('has_voice') and not args.get('sender_id'): raise ValueError('关键词不能为空')
        # Explicit space-separated phrases; jieba provides an additional FTS path.
        terms=list(dict.fromkeys(query.lower().split()))[:12]
        expression=' OR '.join('"'+tokens(k).replace('"','""')+'"' for k in terms if tokens(k).strip())
        if query and not expression: raise ValueError('无有效关键词')
        where,values=self._scope(args)
        if args.get('sender_id'):
            where+=' AND m.sender_id=?';values.append(args['sender_id'])
        if expression:
            where+=' AND (m.id IN (SELECT rowid FROM message_fts WHERE message_fts MATCH ?) OR '+ ' OR '.join('instr(lower(m.content),?)>0' for _ in terms)+')'
            values += [expression]+terms
        if args.get('author')=='self': where+=' AND m.is_self=1'
        elif args.get('author')=='others': where+=' AND (m.is_self=0 OR m.is_self IS NULL)'
        with self.store.connect() as db:
            total=db.execute(f'SELECT count(*) FROM messages m WHERE {where}',values).fetchone()[0]
            rows=db.execute(f'''WITH candidates AS (
                SELECT m.*,row_number() OVER(PARTITION BY platform,conversation_id ORDER BY timestamp DESC,id DESC) slot
                FROM messages m WHERE {where})
                SELECT * FROM candidates ORDER BY slot,platform,timestamp DESC,id DESC LIMIT ? OFFSET ?''',
                values+[args.get('limit',12),args.get('offset',0)]).fetchall()
        result=self._page(rows,total,args.get('offset',0))
        return self._search_context(result,args)

    def _search_context(self,result,args):
        """Send bounded original dialogue with hits, not just UI-only context.

        All hits are admitted first. Context has a separate *smaller* allowance
        within the existing turn budget, leaving room for deliberate followups.
        """
        hits=list(result['messages'])
        result['hit_ids']=[m['id'] for m in hits]
        selected={m['id']:m for m in hits}
        windows=[]
        start,end=self.plan.start,self.plan.end
        if args.get('start'):
            bound=date_bound(args['start']);start=max(start,bound) if start is not None else bound
        if args.get('end'):
            bound=date_bound(args['end'],True);end=min(end,bound) if end is not None else bound
        ceiling=self.used_chars+min(6000,max(0,self.max_chars-self.used_chars)//2)
        # Short private replies most often depend on earlier names and questions.
        # This is a reading priority, never contact identity inference.
        ordered=sorted(hits,key=lambda m:(m['conversation_type']!='direct',len(m['content'])>200))
        for hit in ordered:
            radius=5 if hit['conversation_type']=='direct' else 2
            snapshot=args.get('snapshot_max_id',self.plan.snapshot_max_id)
            if self.plan.snapshot_max_id is not None and snapshot is not None:snapshot=min(snapshot,self.plan.snapshot_max_id)
            nearby=self.store.context(hit['id'],radius=radius,start=start,end=end,max_id=snapshot)
            admitted=self._admit(nearby,char_limit=ceiling)
            selected.update((m['id'],m) for m in admitted)
            sent={m['id'] for m in admitted}
            self.contexts[hit['id']]=nearby
            if len(hit['content'])<=200:
                self.context_requirements.setdefault(hit['id'],set()).update(m['id'] for m in nearby)
            windows.append(dict(anchor_id=hit['id'],message_ids=[m['id'] for m in nearby if m['id'] in sent],
                                radius=radius,budget_limited=len(admitted)<len(nearby)))
        result.update(messages=sorted(selected.values(),key=lambda m:(m['platform'],m['conversation_id'],
                      self.originals[m['id']]['timestamp'],m['id'])),context_windows=windows,
                      context_budget_limited=any(w['budget_limited'] for w in windows),
                      remaining_messages=self.max_messages-len(self.messages),remaining_chars=self.max_chars-self.used_chars,
                      needs_context_ids=sorted(self.unresolved_context_ids()),
                      context_note='命中是线索，不是完整事件。结合相邻对话识别人物、起因、答复和后续；radius只表示附近条数，不保证话题完整。'
                                   'budget_limited表示部分上下文未送入模型；若所需指代仍不明，应缩小查询后get_context补读，不能猜。'
                                   'needs_context_ids中的短消息尚缺上下文，不能据此解释人物、安排或声称无人回应；补读后才能引用，预算不足就省略该线索。'
                                   '分页next_offset按命中条数计算，不按含上下文的messages总数计算。')
        return result

    def unresolved_context_ids(self):
        delivered=self.messages.keys()
        return {mid for mid,needed in self.context_requirements.items() if not needed<=delivered}

    def _get_context(self,args):
        radius=args.get('radius',3)
        if not 0<=radius<=8: raise ValueError('上下文范围不合法')
        where,values=self._scope({})
        with self.store.connect() as db:
            found=db.execute(f'SELECT id FROM messages m WHERE {where} AND m.id=?',values+[args['message_id']]).fetchone()
        if found is None: raise ValueError('消息不在范围内')
        rows=self.store.context(args['message_id'],radius=radius,start=self.plan.start,end=self.plan.end,max_id=self.plan.snapshot_max_id)
        selected=self._admit(rows)
        self.contexts[args['message_id']]=rows
        return dict(messages=selected,budget_limited=len(selected)<len(rows),
                    note='附近已导入消息；未导入的媒体或历史可能缺失。')

    def _scoped_message(self,mid):
        where,values=self._scope({})
        with self.store.connect() as db:
            row=db.execute(f'SELECT * FROM messages m WHERE {where} AND m.id=?',values+[mid]).fetchone()
        if row is None:raise ValueError('消息不在范围内')
        if not self._admit([row]):raise ValueError('消息预算不足')
        return self.originals[mid]

    def _get_media(self,args):
        row=self._scoped_message(args['message_id'])
        return dict(message_id=row['id'],media=self.messages[row['id']]['media'],
                    note='仅资源信息，不代表已经看到图片内容；需要内容时调用 inspect_image。')

    def _transcribe_voice(self,args):
        import time
        from .voice import service,evidence as voice_evidence
        mid=args['message_id'];self._scoped_message(mid)
        if mid not in self.voice_calls and len(self.voice_calls)>=3:return dict(error='本轮最多转写3条语音；可在下一轮继续。')
        if self.max_chars-self.used_chars<1500:return dict(error='本轮证据预算不足，未开始语音转写。')
        self.voice_calls.add(mid)
        timeout=min(210,max(0,getattr(self,'deadline',time.monotonic()+210)-time.monotonic()))
        if timeout<=0 or getattr(self,'cancel',None) is not None and self.cancel.is_set():return dict(error='本轮已停止，未开始语音转写。')
        result=service(self.store).wait(mid,timeout=timeout,cancel=getattr(self,'cancel',None))
        if not result:return dict(error='原语音消息已删除。')
        value=voice_evidence(self.store,mid,limit=min(8000,max(0,self.max_chars-self.used_chars-1000)))
        size=len(json.dumps(value,ensure_ascii=False))
        if size>self.max_chars-self.used_chars:return dict(error='转写已保留在本地，本轮证据预算不足，结果未送入模型。')
        self.used_chars+=size
        self.messages[mid]['voice']=value;self.originals[mid]['voice']=value
        self.incomplete |= bool(value.get('truncated'))
        return dict(message_id=mid,voice=value)

    def _inspect_image(self,args):
        mid=args['message_id'];index=args.get('index',0)
        row=self._scoped_message(mid)
        key=f'{mid}:{index}'
        if key in self.inspections:return self.inspections[key]
        if len(self.inspections)>=3:return dict(error='本轮最多识别3张图片，请在下一轮继续。')
        item=media_at(row.get('media',[]),index)
        if item is None:return dict(error='这条消息没有指定序号的图片。')
        if self.used_chars+1200>self.max_chars:return dict(error='本轮证据字符预算不足，未执行图片分析。')
        context=self._get_context({'message_id':mid,'radius':2})['messages']
        nearby=[{k:m[k] for k in ('sender','time','content')} for m in context]
        from .vision import VisionProvider
        try: result=VisionProvider(self.vision_config).inspect(item,args.get('question',''),nearby)
        except (OSError,ValueError,RuntimeError,ImportError):result=dict(error='图片识别不可用，请检查本地OCR依赖或视觉配置；原消息保留。')
        result.update(message_id=mid,index=index)
        # OCR text is evidence, not instructions; count it against the same budget.
        room=max(0,self.max_chars-self.used_chars-1000)
        if len(result.get('ocr',''))>room:
            result['ocr']=result['ocr'][:room];result['truncated']=True
        size=len(json.dumps(result,ensure_ascii=False))
        if size>self.max_chars-self.used_chars:return dict(error='本轮证据预算已用尽，图片识别结果未送入模型。')
        self.used_chars+=size
        self.inspections[key]=result
        return result

    def _read_conversation(self,args):
        where,values=self._scope(args)
        order=args.get('order','oldest')
        direction='DESC' if order=='newest' else 'ASC'
        with self.store.connect() as db:
            # Bound later pages to this imported snapshot, while still intersecting
            # every request with current user scope. Ties are resolved by message ID.
            snapshot=args.get('snapshot_max_id')
            if snapshot is None:
                snapshot=db.execute(f'SELECT coalesce(max(id),0) FROM messages m WHERE {where}',values).fetchone()[0]
            where+=' AND m.id<=?'
            values.append(snapshot)
            total=db.execute(f'SELECT count(*) FROM messages m WHERE {where}',values).fetchone()[0]
            rows=db.execute(f'SELECT * FROM messages m WHERE {where} ORDER BY timestamp {direction},id {direction} LIMIT ? OFFSET ?',
                            values+[args.get('limit',12),args.get('offset',0)]).fetchall()
        result=self._page(rows,total,args.get('offset',0))
        result.update(order=order,snapshot_max_id=snapshot)
        return result

    def bundle(self):
        with self.lock:
            messages=[dict(row,time=display_time(row['timestamp'])+' +08:00',
                           truncated=self.messages[mid]['truncated']) for mid,row in self.originals.items()]
            contexts={mid:self.contexts.get(mid) or self.store.context(mid,start=self.plan.start,end=self.plan.end,max_id=self.plan.snapshot_max_id)
                      for mid in self.originals}
            return dict(plan=vars(self.plan),messages=messages,contexts=contexts,seed_ids=list(self.originals),
                        file_evidence=getattr(self,'file_evidence',{}),
                        analysis_evidence=getattr(self,'analysis_evidence',{}),
                        scope_count=self.scope_count,match_count=len(messages),unknown_self=0,
                        context_chars=self.used_chars,tool_calls=self.calls,message_count=len(self.messages),
                        incomplete=self.incomplete,inspections=self.inspections,
                        overview=self.overview_used,mention_search=self.mention_search,people=list(self.people.values()))
