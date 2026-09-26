"""Durable source cursors; committed in the same transaction as message inserts."""
import json
from datetime import datetime
from .normalize import TZ


def now():
    return datetime.now(TZ).isoformat(timespec='seconds')


def initialize(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS sync_state(
        platform TEXT PRIMARY KEY, checkpoint TEXT NOT NULL DEFAULT '{}',
        last_sync_at TEXT, last_attempt_at TEXT, status TEXT NOT NULL DEFAULT 'never',
        added INTEGER NOT NULL DEFAULT 0, detail TEXT NOT NULL DEFAULT '尚未增量刷新');
      CREATE TABLE IF NOT EXISTS sync_inventory(
        platform TEXT NOT NULL,name TEXT NOT NULL,data BLOB NOT NULL,
        PRIMARY KEY(platform,name));
    ''')
    for platform in ('qq','wechat'):
        db.execute('INSERT OR IGNORE INTO sync_state(platform) VALUES(?)',(platform,))


def commit_checkpoint(db, sync, added):
    platform=sync['platform']
    previous=db.execute('SELECT checkpoint FROM sync_state WHERE platform=?',(platform,)).fetchone()
    if previous is None or json.loads(previous[0]) != sync['previous']:
        raise ValueError('同步进度已改变，本批未导入；请重新刷新。')
    for name,data in sync.get('inventory',{}).items():
        db.execute('INSERT OR REPLACE INTO sync_inventory VALUES(?,?,?)',(platform,name,data))
    db.execute('''UPDATE sync_state SET checkpoint=?,last_sync_at=?,last_attempt_at=?,
        status='ok',added=?,detail=? WHERE platform=?''',
        (json.dumps(sync['next'],ensure_ascii=False),now(),now(),added,sync['detail'],platform))
