"""Live cursor and provenance are committed with canonical messages."""
import json
import time


def initialize(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS live_state(
        platform TEXT PRIMARY KEY,checkpoint TEXT NOT NULL DEFAULT '{}',
        last_received REAL,last_commit REAL,last_id INTEGER NOT NULL DEFAULT 0,
        total_added INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE IF NOT EXISTS live_events(
        message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
        source TEXT NOT NULL,verification_state TEXT NOT NULL,
        observed_at REAL NOT NULL,ingested_at REAL NOT NULL,ui_visible_at REAL);
      CREATE TABLE IF NOT EXISTS deleted_message_keys(dedup_key TEXT PRIMARY KEY);
      CREATE TABLE IF NOT EXISTS message_native_ids(
        platform TEXT NOT NULL,conversation_id TEXT NOT NULL,locator TEXT NOT NULL,
        message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
        PRIMARY KEY(platform,conversation_id,locator));
      CREATE TABLE IF NOT EXISTS deleted_native_ids(
        platform TEXT NOT NULL,conversation_id TEXT NOT NULL,locator TEXT NOT NULL,
        PRIMARY KEY(platform,conversation_id,locator));
    ''')
    for p in ('qq','wechat'): db.execute('INSERT OR IGNORE INTO live_state(platform) VALUES(?)',(p,))


def commit(db, transition, added, last_id):
    p=transition['platform']
    previous=json.loads(db.execute('SELECT checkpoint FROM live_state WHERE platform=?',(p,)).fetchone()[0])
    if previous!=transition['previous']: raise ValueError('实时进度已改变，本批未写入。')
    db.execute('''UPDATE live_state SET checkpoint=?,last_commit=?,
        last_received=CASE WHEN ?>0 THEN ? ELSE last_received END,
        last_id=max(last_id,?),total_added=total_added+? WHERE platform=?''',
        (json.dumps(transition['next']),time.time(),added,transition['observed_at'],last_id,added,p))


def record(db, mid, transition):
    db.execute('INSERT OR IGNORE INTO live_events VALUES(?,?,?,?,?,NULL)',
        (mid,transition['platform']+'_database','database_verified',transition['observed_at'],time.time()))
