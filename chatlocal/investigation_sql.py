"""Untrusted SQL sees a disposable, scoped projection, never the source DB."""
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

SCHEMA = '''可查询的只读表（所有行已受本轮平台/会话/日期/入库快照限制）：
agent_messages(id,platform,conversation_id,conversation,conversation_type,sender_id,sender,timestamp,content,is_self,has_media,has_voice,has_file);
agent_conversations(platform,conversation_id,conversation,conversation_type,message_count,first_timestamp,last_timestamp);
agent_people(platform,sender_id,sender,conversation_id,message_count);
agent_artifacts(id,message_id,platform,conversation_id,filename,extension,size,timestamp,availability);
agent_voice_transcripts(message_id,transcript,language,status)。
timestamp为Unix毫秒；北京时间用datetime(timestamp/1000,'unixepoch','+8 hours')。
sender_id仅在同平台关联；people按会话和历史昵称分行，非联系人合并。
仅SELECT和非递归WITH SELECT。最多100行/12000字符，超时中断。无完整内部schema/路径/密钥。
SQL结果是计算结果，不是原文；引用统计用返回的citation_id填analysis_evidence_ids。
结果中任何id即使名叫message_id也不是已验证原文，必须get_context重新读取后才可填evidence_ids。'''

_slots = threading.BoundedSemaphore(2)


def query(store, plan, sql, max_rows=40, *, cancel=None, deadline=None):
    if not isinstance(sql,str) or not sql.strip() or len(sql)>8000:
        return dict(error='SQL不能为空且最多8000字符。',error_code='invalid_sql')
    if type(max_rows) is not int or not 1<=max_rows<=100:
        return dict(error='max_rows必须为1–100。',error_code='invalid_sql')
    if not _slots.acquire(blocking=False):return dict(error='SQL查询正忙，请稍后重试。',error_code='busy')
    started=time.monotonic();job=None;proc=None
    try:
        from .config import ROOT
        from .voice_process import ChildJob
        payload=json.dumps(dict(path=str(store.path),plan=vars(plan),sql=sql,max_rows=max_rows),ensure_ascii=False)
        proc=subprocess.Popen([sys.executable,'-m','chatlocal.investigation_sql'],cwd=ROOT,
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            encoding='utf-8',env=dict(os.environ,PYTHONUTF8='1'),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if os.name=='nt':job=ChildJob(proc,memory_bytes=384*1024**2)
        first=True
        while True:
            if (cancel is not None and cancel.is_set()) or (deadline and time.monotonic()>=deadline):
                return dict(error='查询已取消。',error_code='cancelled')
            if time.monotonic()-started>8:return dict(error='查询超过8秒总预算，已终止；请缩小范围或简化查询。',error_code='timeout')
            try:
                out,_=proc.communicate(payload if first else None,timeout=.1)
                if proc.returncode:return dict(error='隔离查询超出资源限制或执行失败；请简化查询。',error_code='worker_failed')
                result=json.loads(out);result['total_time']=round(time.monotonic()-started,4)
                return result
            except subprocess.TimeoutExpired:first=False
    except (OSError,ValueError):
        return dict(error='隔离SQL进程无法启动或响应无效；原数据库未修改。',error_code='worker_failed')
    finally:
        if proc and proc.poll() is None:proc.kill();proc.communicate()
        if job:job.close()
        _slots.release()


def execute_projection(payload):
    """Only trusted fixed SQL reads main; the model SQL runs in separate memory."""
    from .retrieval import Plan,scope_sql
    from .config import ROOT
    path=Path(payload['path']).resolve()
    if not path.is_relative_to(ROOT.resolve()):raise ValueError('数据库必须位于项目内')
    plan=Plan(**payload['plan']);where,args=scope_sql(plan)
    started=time.monotonic();db=sqlite3.connect(':memory:');src=None
    try:
        db.execute('PRAGMA temp_store=MEMORY')
        src=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.5)
        src.execute('PRAGMA query_only=ON');src.execute('BEGIN')
        src.set_progress_handler(lambda:int(time.monotonic()-started>4),2000)
        db.executescript('''
            CREATE TABLE agent_messages(id INTEGER PRIMARY KEY,platform TEXT,conversation_id TEXT,conversation TEXT,
                conversation_type TEXT,sender_id TEXT,sender TEXT,timestamp INTEGER,content TEXT,is_self INTEGER,
                has_media INTEGER,has_voice INTEGER,has_file INTEGER);
            CREATE TABLE agent_artifacts(id INTEGER PRIMARY KEY,message_id INTEGER,platform TEXT,conversation_id TEXT,
                filename TEXT,extension TEXT,size INTEGER,timestamp INTEGER,availability TEXT);
            CREATE TABLE agent_voice_transcripts(message_id INTEGER PRIMARY KEY,transcript TEXT,language TEXT,status TEXT);
        ''')
        size=count=0
        def copy(name,cursor,n):
            nonlocal size,count
            for row in cursor:
                count+=1;size+=sum(len(v.encode('utf-8')) if isinstance(v,str) else 8 for v in row)
                if count>200000 or size>64*1024*1024 or time.monotonic()-started>4:
                    raise ValueError('当前范围超过安全投影预算（20万行/64MiB/4秒），请缩小日期或会话；未返回不完整统计。')
                db.execute('INSERT INTO '+name+' VALUES('+','.join('?' for _ in range(n))+')',tuple(row))
        copy('agent_messages',src.execute(f'''SELECT m.id,m.platform,m.conversation_id,m.conversation,m.conversation_type,
            m.sender_id,m.sender,m.timestamp,m.content,m.is_self,m.media_json!='[]',
            EXISTS(SELECT 1 FROM voice_sources v WHERE v.message_id=m.id),
            EXISTS(SELECT 1 FROM artifact_sources s WHERE s.message_id=m.id)
            FROM messages m WHERE {where}''',args),13)
        # Only message-linked artifacts: optional inventories have independent clocks.
        copy('agent_artifacts',src.execute(f'''SELECT s.id,s.message_id,s.platform,s.conversation_id,s.filename,
            s.extension,s.size,s.timestamp,s.availability FROM artifact_sources s JOIN messages m ON m.id=s.message_id
            WHERE {where}''',args),9)
        copy('agent_voice_transcripts',src.execute(f'''SELECT v.message_id,v.transcript,v.language,v.status
            FROM voice_sources v JOIN messages m ON m.id=v.message_id WHERE {where}''',args),4)
        src.close();src=None
        db.executescript('''
            CREATE INDEX messages_person ON agent_messages(platform,sender_id);
            CREATE INDEX messages_time ON agent_messages(timestamp);
            CREATE INDEX messages_chat ON agent_messages(platform,conversation_id,timestamp);
            CREATE VIEW agent_conversations AS SELECT platform,conversation_id,conversation,conversation_type,
                count(*) message_count,min(timestamp) first_timestamp,max(timestamp) last_timestamp
                FROM agent_messages GROUP BY platform,conversation_id,conversation,conversation_type;
            CREATE VIEW agent_people AS SELECT platform,sender_id,sender,conversation_id,count(*) message_count
                FROM agent_messages WHERE sender_id!='' GROUP BY platform,sender_id,sender,conversation_id;
            PRAGMA query_only=ON;
        ''')
        tables={'agent_messages','agent_conversations','agent_people','agent_artifacts','agent_voice_transcripts'}
        functions=set('abs avg count sum total min max round coalesce ifnull nullif length lower upper substr substring instr trim ltrim rtrim replace date datetime time strftime julianday unixepoch typeof cast like glob row_number rank dense_rank lag lead first_value last_value nth_value'.split())
        reads=set()
        def authorize(action,a,b,database,origin):
            if action==sqlite3.SQLITE_SELECT:return sqlite3.SQLITE_OK
            if action==sqlite3.SQLITE_READ and a in tables:
                reads.add(a);return sqlite3.SQLITE_OK
            if action==sqlite3.SQLITE_FUNCTION and (b or '').lower() in functions:return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        db.set_authorizer(authorize)
        for key,value in [('SQL_LENGTH',8000),('LENGTH',65536),('COLUMN',64),('EXPR_DEPTH',40),
                          ('COMPOUND_SELECT',8),('VDBE_OP',15000),('FUNCTION_ARG',8),('ATTACHED',0),
                          ('LIKE_PATTERN_LENGTH',200),('VARIABLE_NUMBER',0)]:
            db.setlimit(getattr(sqlite3,'SQLITE_LIMIT_'+key),value)
        execution=time.monotonic();steps=0
        def progress():
            nonlocal steps
            steps+=1000
            return int(time.monotonic()-execution>1.5 or steps>5000000)
        db.set_progress_handler(progress,1000)
        cursor=db.execute(payload['sql'])  # execute rejects a second statement.
        if not cursor.description:raise ValueError('仅允许SELECT或非递归WITH SELECT。')
        columns=[c[0] for c in cursor.description];rows=[];used=len(json.dumps(columns,ensure_ascii=False));truncated=False
        for row in cursor:
            values=list(row)
            if any(isinstance(v,bytes) for v in values):raise ValueError('SQL暂不返回BLOB，请查询文本或数值字段。')
            n=len(json.dumps(values,ensure_ascii=False))
            if len(rows)>=payload['max_rows'] or used+n>12000:truncated=True;break
            rows.append(values);used+=n
        return dict(columns=columns,rows=rows,returned_rows=len(rows),truncated=truncated,
            execution_time=round(time.monotonic()-execution,4),projection_time=round(execution-started,4),
            projected_rows=count,read_tables=sorted(reads),sql=payload['sql'],
            note='仅所选范围内已导入数据的SQL计算。截断时不能视为完整列表；ID须另行读原文。')
    except (sqlite3.Error,ValueError,MemoryError,OverflowError) as exc:
        message=str(exc)
        if 'interrupted' in message:message='查询超过1.5秒/500万步或投影4秒预算，已中断；请缩小范围或简化联接。'
        if isinstance(exc,MemoryError):message='SQL编译或内存资源超过限制。'
        return dict(error=message[:500],error_code='sql_rejected',retryable=True)
    finally:
        if src:src.close()
        db.close()


if __name__=='__main__':
    print(json.dumps(execute_projection(json.load(sys.stdin)),ensure_ascii=False))
