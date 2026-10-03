"""Explicit client read scope. Dates bound backfill; sync always follows IDs."""
import json
from .retrieval import date_bound


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS client_settings(name TEXT PRIMARY KEY,value TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS message_counter(id INTEGER PRIMARY KEY CHECK(id=1),value INTEGER NOT NULL)')
    db.execute('INSERT OR IGNORE INTO message_counter SELECT 1,coalesce(max(id),0) FROM messages')


def bounds(start='',end=''):
    if not isinstance(start,str) or not isinstance(end,str):raise ValueError('日期必须为文本，留空表示不限')
    lo,hi=date_bound(start),date_bound(end,True)
    if lo is not None and hi is not None and lo>=hi:raise ValueError('开始时间必须早于结束时间')
    return lo,hi


def get_scope(store):
    with store.connect() as db:
        row=db.execute("SELECT value FROM client_settings WHERE name='import_scope'").fetchone()
    return json.loads(row[0]) if row else {p:dict(enabled=True,conversations=None) for p in ('qq','wechat')}


def validate_scope(value):
    if not isinstance(value,dict) or set(value)!= {'qq','wechat'}:raise ValueError('读取范围须指定 QQ 和微信的开关')
    result={}
    for p,item in value.items():
        if not isinstance(item,dict) or type(item.get('enabled')) is not bool:raise ValueError('无效平台开关')
        ids=item.get('conversations')
        if ids is not None and (not isinstance(ids,list) or not ids or len(ids)>500 or any(not isinstance(v,str) or not v.strip() or len(v)>300 for v in ids)):
            raise ValueError('请勾选至少一个会话，或选择全部会话')
        result[p]=dict(enabled=item['enabled'],conversations=list(dict.fromkeys(ids)) if ids is not None else None)
        if item.get('account') is not None:
            from .client_accounts import account_id
            result[p]['account']=account_id(item['account'])
    if not any(v['enabled'] for v in result.values()):raise ValueError('请至少启用一个读取平台')
    return result


def save_scope(store,value):
    value=validate_scope(value)
    with store.connect() as db:
        db.execute("INSERT OR REPLACE INTO client_settings VALUES('import_scope',?)",(json.dumps(value),))
    return value


def watch_refresh_plan(store,platforms,conversations):
    """Describe optional client refresh coverage; never restrict local analysis."""
    scope=get_scope(store)
    pairs=[json.loads(v) for v in conversations]
    result=[]
    for p in platforms:
        wanted={cid for platform,cid in pairs if platform==p}
        # Explicit conversation selection excludes other platforms entirely.
        if pairs and not wanted:continue
        source=scope[p]
        selected=source['conversations']
        if not source['enabled'] or selected is not None and wanted and not wanted.intersection(selected):coverage='none'
        elif selected is None or wanted and wanted.issubset(selected):coverage='full'
        else:coverage='partial'
        result.append(dict(platform=p,coverage=coverage,can_refresh=coverage!='none'))
    return result
