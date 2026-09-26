"""Watch persistence and orchestration; analysis uses the existing run_agent."""
import json
import threading
import uuid
import time
from contextlib import contextmanager
from .agent import run_agent
from .agent_profiles import profile_config
from .config import settings
from .retrieval import Plan,scope_sql,make_plan
from .watch_agent import conversation_memory
from .sync import sync_messages
from .sync_state import now


class Watches:
    def __init__(self,store):
        self.store=store
        self.lock=threading.Lock()
        with store.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS watch_cards(
                id TEXT PRIMARY KEY,title TEXT NOT NULL,watch_query TEXT NOT NULL,
                platform_scope TEXT NOT NULL,conversation_scope TEXT NOT NULL,
                created_at TEXT NOT NULL,last_checked_at TEXT,baseline_summary TEXT NOT NULL DEFAULT '',
                evidence_ids TEXT NOT NULL DEFAULT '[]',status TEXT NOT NULL DEFAULT 'active',
                cursor INTEGER NOT NULL DEFAULT 0,baseline TEXT NOT NULL DEFAULT '[]',
                uncertainties TEXT NOT NULL DEFAULT '[]',last_result TEXT NOT NULL DEFAULT '{}',
                error TEXT NOT NULL DEFAULT '',seed_ids TEXT NOT NULL DEFAULT '[]');
              CREATE TABLE IF NOT EXISTS watch_runs(
                id INTEGER PRIMARY KEY,card_id TEXT NOT NULL REFERENCES watch_cards(id),
                created_at TEXT NOT NULL,status TEXT NOT NULL,record TEXT NOT NULL);
            ''')
            migrations={
                'watch_cards':dict(schedule_minutes='INTEGER NOT NULL DEFAULT 0',next_run_at='REAL',
                    last_attempt_at='TEXT',profile="TEXT NOT NULL DEFAULT 'deep'",load_stickers='INTEGER NOT NULL DEFAULT 0',revision='INTEGER NOT NULL DEFAULT 1',
                    message_threshold='INTEGER NOT NULL DEFAULT 0',message_trigger_cursor='INTEGER NOT NULL DEFAULT 0'),
                'watch_runs':dict(kind="TEXT NOT NULL DEFAULT 'check'",question="TEXT NOT NULL DEFAULT ''",
                    result="TEXT NOT NULL DEFAULT '{}'",revision='INTEGER NOT NULL DEFAULT 1',trigger="TEXT NOT NULL DEFAULT 'manual'")}
            for table,columns in migrations.items():
                existing={r[1] for r in db.execute(f'PRAGMA table_info({table})')}
                for name,definition in columns.items():
                    if name not in existing:db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
            db.execute('CREATE INDEX IF NOT EXISTS watch_timeline ON watch_runs(card_id,id)')
            # One-time opt-in reset requested when automatic ingestion controls
            # were separated. Subsequent explicit choices remain persistent.
            if not db.execute("SELECT 1 FROM client_settings WHERE name='automatic_stickers_opt_in_v1'").fetchone():
                db.execute('UPDATE watch_cards SET load_stickers=0')
                row=db.execute("SELECT value FROM client_settings WHERE name='live_ingestion'").fetchone()
                if row:
                    live=json.loads(row[0]);live.pop('reconcile_minutes',None);live['load_stickers']=False
                    db.execute("UPDATE client_settings SET value=? WHERE name='live_ingestion'",(json.dumps(live),))
                db.execute("INSERT INTO client_settings VALUES('automatic_stickers_opt_in_v1','true')")

    @contextmanager
    def exclusive(self):
        if not self.lock.acquire(False):raise ValueError('关注卡正在运行，请等待完成后再操作。')
        try:yield
        finally:self.lock.release()

    @staticmethod
    def decode(row):
        card=dict(row)
        for key in ('platform_scope','conversation_scope','evidence_ids','baseline','uncertainties','last_result','seed_ids'):
            card[key]=json.loads(card[key])
        return card

    def list(self):
        with self.store.connect() as db:
            return [self.decode(r) for r in db.execute('SELECT * FROM watch_cards ORDER BY created_at DESC')]

    def get(self,card_id):
        with self.store.connect() as db:row=db.execute('SELECT * FROM watch_cards WHERE id=?',(card_id,)).fetchone()
        if not row:raise ValueError('关注卡不存在')
        return self.decode(row)

    def validate(self,title,query,platforms,conversations,schedule_minutes=0,profile='deep',load_stickers=False,message_threshold=0):
        if not isinstance(title,str) or not 1<=len(title.strip())<=100:raise ValueError('标题需为1–100字')
        if not isinstance(query,str) or not 1<=len(query.strip())<=1600:raise ValueError('关注内容需为1–1600字')
        if not isinstance(platforms,list) or not platforms or any(p not in ('qq','wechat') for p in platforms):raise ValueError('请选择有效平台')
        if not isinstance(conversations,list) or len(conversations)>200:raise ValueError('会话范围无效')
        known={(r['platform'],r['conversation_id']) for r in self.store.conversations()}
        for value in conversations:
            try:pair=json.loads(value)
            except (TypeError,ValueError):raise ValueError('会话范围无效') from None
            if not isinstance(pair,list) or len(pair)!=2 or not all(isinstance(p,str) for p in pair) or tuple(pair) not in known or pair[0] not in platforms:raise ValueError('会话不在选定平台内')
        if type(schedule_minutes) is not int or schedule_minutes!=0 and not 30<=schedule_minutes<=10080:raise ValueError('检查间隔须为30分钟至7天，或选择仅手动。')
        if type(load_stickers) is not bool:raise ValueError('表情包设置无效')
        if type(message_threshold) is not int or not 0<=message_threshold<=100000:raise ValueError('消息触发条数须为1–100000，或设为0关闭。')
        if not isinstance(profile,str):raise ValueError('分析强度无效')
        profile_config({},profile)

    def create(self,title,query,platforms,conversations,seed_ids=(),*,schedule_minutes=0,profile='deep',load_stickers=False,message_threshold=0):
        self.validate(title,query,platforms,conversations,schedule_minutes,profile,load_stickers,message_threshold)
        plan=Plan(platforms=platforms,conversations=conversations)
        where,args=scope_sql(plan)
        if not isinstance(seed_ids,(list,tuple)) or len(seed_ids)>20 or any(type(i) is not int for i in seed_ids):raise ValueError('初始证据无效')
        with self.store.connect() as db:
            for mid in seed_ids:
                if not db.execute(f'SELECT 1 FROM messages m WHERE {where} AND id=?',args+[mid]).fetchone():raise ValueError('初始证据超出范围')
            card_id=uuid.uuid4().hex
            db.execute('''INSERT INTO watch_cards(id,title,watch_query,platform_scope,conversation_scope,created_at,seed_ids,
                schedule_minutes,next_run_at,profile,load_stickers,message_threshold) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                (card_id,title.strip(),query.strip(),json.dumps(platforms),json.dumps(conversations),now(),json.dumps(seed_ids),
                 schedule_minutes,time.time()+schedule_minutes*60 if schedule_minutes else None,profile,int(load_stickers),message_threshold))
            self.add_run(db,card_id,'setup',query.strip(),1,result={'note':'已创建关注，接下来建立当前已知状态。'})
        return self.get(card_id)

    @staticmethod
    def add_run(db,card_id,kind,question,revision,*,result=None,status='completed',trigger='manual'):
        return db.execute('''INSERT INTO watch_runs(card_id,created_at,status,record,kind,question,result,revision,trigger)
            VALUES(?,?,?,'{}',?,?,?,?,?)''',(card_id,now(),status,kind,question,json.dumps(result or {},ensure_ascii=False),revision,trigger)).lastrowid

    def update(self,card_id,title,query,platforms,conversations,*,schedule_minutes=0,profile='deep',load_stickers=False,message_threshold=0):
        self.validate(title,query,platforms,conversations,schedule_minutes,profile,load_stickers,message_threshold)
        with self.exclusive(),self.store.connect() as db:
            card=self.get(card_id)
            pairs=lambda values:{tuple(json.loads(v)) for v in values}
            reset=query.strip()!=card['watch_query'] or set(platforms)!=set(card['platform_scope']) or pairs(conversations)!=pairs(card['conversation_scope'])
            revision=card['revision']+int(reset)
            next_at=card['next_run_at']
            if schedule_minutes!=card['schedule_minutes']:
                next_at=time.time()+schedule_minutes*60 if schedule_minutes and card['status']=='active' else None
            db.execute('''UPDATE watch_cards SET title=?,watch_query=?,platform_scope=?,conversation_scope=?,
                schedule_minutes=?,next_run_at=?,profile=?,load_stickers=?,revision=?,message_threshold=?,message_trigger_cursor=? WHERE id=?''',
                (title.strip(),query.strip(),json.dumps(platforms),json.dumps(conversations),schedule_minutes,next_at,profile,int(load_stickers),revision,
                 message_threshold,0 if reset or message_threshold!=card['message_threshold'] else card['message_trigger_cursor'],card_id))
            if reset:
                db.execute("""UPDATE watch_cards SET last_checked_at=NULL,baseline_summary='',evidence_ids='[]',cursor=0,
                    baseline='[]',uncertainties='[]',last_result='{}',error='',seed_ids='[]' WHERE id=?""",(card_id,))
            labels={json.dumps([r['platform'],r['conversation_id']]):r['conversation'] for r in self.store.conversations()}
            scope='、'.join(labels.get(json.dumps(json.loads(v)),json.loads(v)[1]) for v in conversations) or '所选平台的全部会话'
            triggers=([f'每 {schedule_minutes} 分钟'] if schedule_minutes else [])+([f'累计 {message_threshold} 条新消息'] if message_threshold else [])
            note=f'关注范围：{scope}。'+('或'.join(triggers)+'时检查。' if triggers else '仅手动检查。')
            if reset:note+=' 主题或范围已改变，旧消息仅作历史记录；下一次检查将重新建立当前结论。'
            self.add_run(db,card_id,'settings','更新关注设置',revision,result=dict(note=note))
        return self.get(card_id)

    def pause(self,card_id,paused):
        with self.exclusive(),self.store.connect() as db:
            card=self.get(card_id)
            next_at=time.time()+card['schedule_minutes']*60 if not paused and card['schedule_minutes'] else None
            db.execute('UPDATE watch_cards SET status=?,next_run_at=? WHERE id=?',('paused' if paused else 'active',next_at,card_id))
            self.add_run(db,card_id,'settings','暂停关注' if paused else '恢复关注',card['revision'],result=dict(note='已暂停自动检查，仍可追问。' if paused else '已恢复关注。'))

    def message_counts(self,card,db=None):
        if db is None:
            with self.store.connect() as db:return self.message_counts(card,db)
        if not card['last_checked_at']:return dict(pending_messages=0,trigger_messages=0,upper=card['cursor'])
        where,args=scope_sql(Plan(platforms=card['platform_scope'],conversations=card['conversation_scope']))
        row=db.execute(f'''SELECT count(*),coalesce(sum(id>?),0),coalesce(max(id),?) FROM messages m
            WHERE {where} AND id>?''',[max(card['cursor'],card['message_trigger_cursor']),card['cursor']]+args+[card['cursor']]).fetchone()
        return dict(pending_messages=row[0],trigger_messages=row[1],upper=row[2])

    def due_reason(self,card,at,db):
        if card['status']!='active':return None
        if card['schedule_minutes'] and card['next_run_at'] is not None and card['next_run_at']<=at:return 'scheduled'
        if card['message_threshold'] and self.message_counts(card,db)['trigger_messages']>=card['message_threshold']:return 'message_count'
        return None

    def due(self,at=None):
        at=time.time() if at is None else at
        with self.store.connect() as db:
            rows=db.execute("SELECT * FROM watch_cards WHERE status='active' AND (schedule_minutes>0 OR message_threshold>0) ORDER BY coalesce(last_attempt_at,created_at),id").fetchall()
            return [r['id'] for r in rows if self.due_reason(self.decode(r),at,db)][:1]

    def claim_due(self,card_id,at=None):
        # Claim both triggers together. Unchanged failed/backlogged input must
        # not cause a paid retry every scheduler tick, including after restart.
        at=time.time() if at is None else at
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM watch_cards WHERE id=?',(card_id,)).fetchone()
            if not row:return None
            card=self.decode(row);reason=self.due_reason(card,at,db)
            if not reason:return None
            db.execute('UPDATE watch_cards SET next_run_at=?,message_trigger_cursor=?,last_attempt_at=? WHERE id=?',
                (at+card['schedule_minutes']*60 if card['schedule_minutes'] else None,self.message_counts(card,db)['upper'],now(),card_id))
            return reason

    def timeline(self,card_id,before=None,limit=40):
        self.get(card_id)
        with self.store.connect() as db:
            rows=list(db.execute('SELECT * FROM watch_runs WHERE card_id=?'+(' AND id<?' if before else '')+' ORDER BY id DESC LIMIT ?',
                [card_id]+([before] if before else [])+[limit+1]))
        more=len(rows)>limit;rows=rows[:limit]
        items=[dict(r,record=json.loads(r['record']),result=json.loads(r['result'])) for r in reversed(rows)]
        return items,items[0]['id'] if more else None

    def delete(self,card_id):
        """Remove the card and its runs atomically; imported evidence stays intact."""
        if not isinstance(card_id,str) or not card_id:raise ValueError('请选择要删除的关注卡。')
        if not self.lock.acquire(False):raise ValueError('关注卡正在检查，请等待完成后再删除。')
        try:
            with self.store.connect() as db:
                db.execute('DELETE FROM watch_runs WHERE card_id=?',(card_id,))
                return bool(db.execute('DELETE FROM watch_cards WHERE id=?',(card_id,)).rowcount)
        finally:self.lock.release()

    def check(self,card_id,*,refresh=True,load_stickers=False,profile='deep',progress=None,agent=run_agent,syncer=sync_messages,trigger='manual',cancelled=None):
        if not self.lock.acquire(False):raise ValueError('另一张关注卡正在检查，请稍候')
        run_id=None
        try:
            card=self.get(card_id)
            if card['status']=='paused':raise ValueError('此关注卡已暂停，请先恢复')
            with self.store.connect() as db:
                run_id=self.add_run(db,card_id,'check',card['watch_query'],card['revision'],status='running',trigger=trigger)
                db.execute('UPDATE watch_cards SET last_attempt_at=? WHERE id=?',(now(),card_id))
            return self._check(card_id,refresh,load_stickers,profile,progress,agent,syncer,run_id,cancelled)
        except Exception as exc:
            error=str(exc) if isinstance(exc,ValueError) else '检查未完成，基线和进度保留。'
            with self.store.connect() as db:
                db.execute('UPDATE watch_cards SET error=? WHERE id=?',(error,card_id))
                if run_id:
                    prior=json.loads(db.execute('SELECT result FROM watch_runs WHERE id=?',(run_id,)).fetchone()[0])
                    db.execute("UPDATE watch_runs SET status='error',result=? WHERE id=?",(json.dumps(dict(prior,error=error),ensure_ascii=False),run_id))
            raise
        finally:self.lock.release()

    def _check(self,card_id,refresh,load_stickers,profile,progress,agent,syncer,run_id,cancelled):
        card=self.get(card_id)
        if card['status']=='paused':raise ValueError('此关注卡已暂停，请先恢复')
        emit=progress or (lambda event:None)
        data_status=[];refresh_notice=''
        if refresh:
            from .import_scope import watch_refresh_plan
            data_status=watch_refresh_plan(self.store,card['platform_scope'],card['conversation_scope'])
            platforms=[r['platform'] for r in data_status if r['can_refresh']]
            synced=[]
            if cancelled and cancelled.is_set():raise ValueError('已停止检查，基线与进度保留。')
            if platforms:
                try:
                    synced=syncer(self.store,platforms,load_stickers=load_stickers,progress=lambda t:emit(dict(type='status',text=t)))
                except Exception:
                    # Refresh can be busy/offline. Its cursor remains owned by
                    # the sync transaction; locally ingested evidence is usable.
                    synced=[]
            by_platform={r.get('platform'):r for r in synced if isinstance(r,dict)}
            notices=[]
            for item in data_status:
                label='QQ' if item['platform']=='qq' else '微信'
                if not item.pop('can_refresh'):
                    item['status']='skipped';notices.append(label+'：本次导入选择未覆盖关注会话，未额外读取客户端')
                elif by_platform.get(item['platform'],{}).get('status')!='ok':
                    item['status']='error';notices.append(label+'：客户端刷新未完成')
                else:
                    item['status']='ok'
                    if item['coverage']=='partial':notices.append(label+'：本次客户端刷新仅覆盖部分关注会话')
            if notices:
                refresh_notice='；'.join(notices)+'。继续按关注卡范围检查本地已入库消息（包括实时读取的消息）；本轮结果不代表客户端消息已同步完整。'
                emit(dict(type='status',text=refresh_notice))
            with self.store.connect() as db:
                db.execute('UPDATE watch_runs SET result=? WHERE id=?',(json.dumps(dict(data_status=data_status,refresh_notice=refresh_notice),ensure_ascii=False),run_id))
        if cancelled and cancelled.is_set():raise ValueError('已停止检查，基线与进度保留。')
        from .watch_check import check_question
        return check_question(self,card_id,profile,emit,agent,run_id,cancelled,data_status,refresh_notice)

    def ask(self,card_id,question,*,profile='deep',vision_mode=None,progress=None,cancelled=None,agent=run_agent):
        if not isinstance(question,str) or not 1<=len(question.strip())<=6000:raise ValueError('请输入1–6000字的问题')
        with self.exclusive():
            card=self.get(card_id);emit=progress or (lambda event:None)
            plan=make_plan(question,card['platform_scope'],card['conversation_scope'])
            config=profile_config(settings(),profile)
            from .vision import with_vision_mode
            config=with_vision_mode(config,vision_mode)
            with self.store.connect() as db:
                rid=self.add_run(db,card_id,'question',question.strip(),card['revision'],status='running')
            # This is an ordinary, evidence-checked Agent turn. A follow-up never
            # silently changes the monitoring scope or its baseline/checkpoint.
            memory=conversation_memory(self.store,card)
            try:
                final=None
                for event in agent(self.store,plan,question,memory=memory,cancel=cancelled,config=config):
                    if event['type']=='done':final=event
                    else:emit(event)
                if not final:raise ValueError('回答未完成，请重试。')
                if vision_mode is not None:final['record']['vision_mode']=vision_mode
                with self.store.connect() as db:
                    db.execute('UPDATE watch_runs SET status=?,record=? WHERE id=?',(final['status'],json.dumps(final['record'],ensure_ascii=False),rid))
                if final['status']!='completed':raise ValueError(final['record'].get('result',{}).get('error') or '回答已停止或未完成，可重试。')
            except Exception as exc:
                error=str(exc) if isinstance(exc,ValueError) else '回答未完成，请重试。'
                with self.store.connect() as db:db.execute("UPDATE watch_runs SET status='error',result=? WHERE id=?",(json.dumps(dict(error=error),ensure_ascii=False),rid))
                raise
            return self.get(card_id)
