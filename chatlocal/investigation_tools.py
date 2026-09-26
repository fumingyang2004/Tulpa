"""Investigation primitives use the same scope, budgets and evidence registry."""
import json
import uuid

from .collections import Collections,TYPES
from .investigation_sql import SCHEMA


def schemas(tool):
    string=dict(type='string')
    return [
        tool('query_communication_db','受限只读SQL，用于现有搜索工具难以表达的统计/结构化调查。'+SCHEMA,
            dict(sql=dict(type='string',maxLength=8000),max_rows=dict(type='integer',minimum=1,maximum=100,default=40)),['sql']),
        tool('get_interaction_threads','提取2–6名同平台稳定账号的真正交互线索，先find_people确认账号。'
            'strict只取原生reply/完整quote/明确@账号；contextual另含短时间连续交替发言候选，不能当确定对话。'
            'participants是sender_id字符串数组。返回原文和关系依据，仍须结合语境。query为空格分词OR主题过滤。',
            dict(platform=dict(type='string',enum=['qq','wechat']),participants=dict(type='array',items=string,minItems=2,maxItems=6),
                mode=dict(type='string',enum=['strict','contextual']),conversation_ids=dict(type='array',items=string,maxItems=20),
                start=string,end=string,query=string,offset=dict(type='integer',minimum=0,maximum=1000),
                limit=dict(type='integer',minimum=1,maximum=10,default=5)),['platform','participants']),
        tool('get_group_knowledge','按需通过已连接的 OneBot 刷新并读取QQ群公告/精华，每轮每群最多刷新一次；group_id用工具确认的会话ID或群号。'
            '刷新失败会说明并保留旧缓存；无缓存不表示群没有公告。正文证据K编号填analysis_evidence_ids。'
            '精华canonical_message_id只有经过唯一身份匹配才给出，get_context后才能作聊天原文引用。'
            '加精时间不是发言时间，原时间未知时不进入限定日期查询。',
            dict(group_id=string,types=dict(type='array',items=dict(type='string',enum=['notice','essence']),minItems=1,maxItems=2),
                offset=dict(type='integer',minimum=0,maximum=10000),limit=dict(type='integer',minimum=1,maximum=20)),['group_id']),
        tool('evidence_collection','用户希望留下/保存资料时创建或添加证据集合；只持久化引用，无自动监控。'
            'action=create/list/get/add/rename。create可同时带items原子创建并保存；空集合会明确返回added=0。'
            'items仅可使用本轮工具实际返回且当前范围可访问的来源。'
            'source_type message/voice的source_id为消息数字字符串；media为消息ID:图片index；'
            'artifact_source为F编号去F；artifact_chunk为文件ID:chunkID（如12:34）；qq_notice/qq_essence为K编号去K。'
            'get返回引用目录，不等于读过全部原文；按类型继续get_context/inspect_image/transcribe_voice/read_file_chunks。'
            '创建/保存操作返回的S编号可填analysis_evidence_ids，仅证明保存动作，不证明被保存内容。',
            dict(action=dict(type='string',enum=['create','list','get','add','rename']),title=dict(type='string',maxLength=100),
                collection_id=string,offset=dict(type='integer',minimum=0,maximum=10000),
                items=dict(type='array',minItems=1,maxItems=30,items=dict(type='object',additionalProperties=False,
                    properties=dict(source_type=dict(type='string',enum=list(TYPES)),source_id=string),required=['source_type','source_id']))),['action']),
    ]


class InvestigationTools:
    def _analysis_admit(self,item,key=None):
        if not hasattr(self,'analysis_evidence'):self.analysis_evidence={}
        key=key or 'Q'+uuid.uuid4().hex[:12]
        result=dict(item,citation_id=key)
        size=len(json.dumps(result,ensure_ascii=False))
        if self.used_chars+size>self.max_chars:
            self.incomplete=True
            return dict(error='本轮证据字符预算不足，结果未提供给模型。')
        self.used_chars+=size;self.analysis_evidence[key]=result
        return result

    def _query_communication_db(self,args):
        from .investigation_sql import query
        result=query(self.store,self.plan,args['sql'],args.get('max_rows',40),
            cancel=getattr(self,'cancel',None),deadline=getattr(self,'deadline',None))
        if result.get('error'):return result
        # A literal SELECT 123 AS message_id cannot admit 123 into message evidence.
        return self._analysis_admit(dict(result,kind='sql',scope=vars(self.plan)))

    def _get_interaction_threads(self,args):
        from .interactions import extract
        where,params=self._scope({k:args[k] for k in ('platform','start','end') if k in args})
        try:
            result=extract(self.store,where,params,args['participants'],mode=args.get('mode','strict'),
                conversation_ids=args.get('conversation_ids',()),query=args.get('query',''),offset=args.get('offset',0),limit=args.get('limit',5))
        except ValueError as exc:return dict(error=str(exc))
        for thread in result['threads']:
            originals=thread['messages'];thread['messages']=self._admit(originals)
            sent={r['id'] for r in thread['messages']}
            thread['budget_limited']=len(sent)<len(originals)
            thread['relations']=[e for e in thread['relations'] if all(mid in sent for mid in e['ids'])]
        result['threads']=[t for t in result['threads'] if t['relations']]
        if args.get('mode','strict')=='strict' and not result['match_count']:
            result['next_step']='没有显式关系不表示没有交流；请再用contextual检查一次连续对话候选。'
            result['suggested_call']=dict(name='get_interaction_threads',arguments=dict(args,mode='contextual'))
        return result

    def _get_group_knowledge(self,args):
        from .group_knowledge import public,scope
        from .artifact_onebot import config
        where,values=self._scope({'platform':'qq'})
        with self.store.connect() as db:
            cids=[r[0] for r in db.execute(f'''SELECT DISTINCT conversation_id FROM messages m WHERE {where}
                AND m.conversation_type='group' AND (m.conversation_id=? OR m.conversation_id LIKE ?)''',
                values+[args['group_id'],'%:group:'+args['group_id']])]
            if len(cids)!=1:return dict(error='当前范围不能唯一确定这个QQ群，请使用完整conversation_id。')
        refresh_result=None
        if getattr(self,'qq_client',None) and ('knowledge',cids[0]) not in self.qq_refreshed:
            from .group_knowledge import refresh
            from .artifacts import ArtifactError
            try:refresh_result=refresh(self.store,cids[0],client=self.qq_client)
            except ArtifactError as exc:refresh_result=dict(error=str(exc))
            self.qq_refreshed.add(('knowledge',cids[0]))
            with self.store.connect() as db:
                self.knowledge_snapshot=db.execute('SELECT coalesce(max(id),0) FROM qq_knowledge').fetchone()[0]
        with self.store.connect() as db:
            kw,kp=scope(self.plan);types=args.get('types',['notice','essence']);offset=args.get('offset',0);limit=args.get('limit',12)
            rows=db.execute(f'''SELECT * FROM qq_knowledge k WHERE {kw} AND k.conversation_id=?
                AND k.kind IN ({','.join('?' for _ in types)}) AND k.id<=? ORDER BY k.timestamp DESC,k.id DESC LIMIT ? OFFSET ?''',
                kp+[cids[0],*types,self.knowledge_snapshot,limit+1,offset]).fetchall()
        entries=[]
        for row in rows[:limit]:
            entry=self._analysis_admit(public(row),'K'+str(row['id']))
            if not entry.get('error'):entries.append(entry)
        return dict(sources=entries,has_more=len(rows)>limit,next_offset=offset+limit if len(rows)>limit else None,
            refresh=refresh_result,
            onebot_configured=bool(config()['ARTIFACT_ONEBOT_URL']),
            note=('本轮已按需刷新或复用本轮缓存；请结合 refresh 判断最新读取是否成功。' if getattr(self,'qq_client',None) else '读取本地缓存；当前不能获取新公告/精华。')+
                 '资料图片仅元数据，未识别。')

    def _evidence_collection(self,args):
        layer=Collections(self.store);action=args['action'];cid=args.get('collection_id')
        if self.read_only and action in ('create','add','rename'):
            return dict(error='关注卡后台检查不会自动创建或修改证据集合；请在普通对话中保存证据。')
        if action=='list':return layer.list(args.get('offset',0),20)
        if action in ('get','add','rename') and not cid:raise ValueError('需要collection_id')
        if action=='get':
            result=layer.get(cid,offset=args.get('offset',0),limit=15,plan=self.plan)
            # A reference catalogue is not an evidence-admission side channel.
            for item in result['items']:
                if 'source' in item:
                    item['source']={k:v for k,v in item['source'].items() if k in ('message_id','platform','conversation_id','conversation','sender','timestamp','filename','chunk_id','url','citation_id')}
            return result
        if action in ('create','add'):
            for item in args.get('items',[]):
                kind,key=item['source_type'],item['source_id'];allowed=False
                if kind in ('message','media','voice'):
                    try:
                        mid=int(key.split(':')[0]);m=self.messages.get(mid)
                        allowed=bool(m)
                        if kind=='voice':allowed=allowed and bool(m.get('voice'))
                        if kind=='media':allowed=allowed and len(key.split(':'))==2 and any(v['index']==int(key.split(':')[1]) for v in m.get('media',[]))
                    except (ValueError,TypeError):allowed=False
                elif kind.startswith('artifact_'):
                    file_key='F'+key.replace(':',':C')
                    allowed=file_key in getattr(self,'file_evidence',{})
                elif kind.startswith('qq_'):
                    source=getattr(self,'analysis_evidence',{}).get('K'+key,{})
                    allowed=source.get('source_type')==kind
                if not allowed:return dict(error='只能添加本轮实际返回并验证过的来源；请先读取该消息或文件。')
        if action=='create':result=layer.create(args.get('title'),args.get('items'),plan=self.plan)
        elif action=='rename':result=layer.rename(cid,args.get('title'))
        else:result=layer.add(cid,args.get('items'),plan=self.plan)
        receipt=self._analysis_admit(dict(result,kind='collection',operation=action),key='S'+uuid.uuid4().hex[:12])
        # If budget was exhausted the operation still happened: expose minimal receipt,
        # so a retry cannot silently create a second collection.
        return receipt if not receipt.get('error') else dict(result,note='操作已成功；本轮证据预算不足，未产生可引用的操作回执。')
