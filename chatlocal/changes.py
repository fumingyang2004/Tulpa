"""Transactional local change journal. Source timestamps are not cursors."""
import uuid


def initialize(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS local_change_meta(id INTEGER PRIMARY KEY CHECK(id=1),epoch TEXT NOT NULL,high_water INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE IF NOT EXISTS local_changes(
        seq INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT NOT NULL,source_type TEXT NOT NULL,
        source_id TEXT NOT NULL,platform TEXT NOT NULL,conversation_id TEXT NOT NULL,
        source_timestamp INTEGER,created_at REAL NOT NULL DEFAULT (unixepoch('subsec')));
      CREATE INDEX IF NOT EXISTS change_scope ON local_changes(platform,conversation_id,seq);
      CREATE TRIGGER IF NOT EXISTS local_change_water AFTER INSERT ON local_changes BEGIN
        UPDATE local_change_meta SET high_water=NEW.seq WHERE id=1;
      END;
    ''')
    db.execute('INSERT OR IGNORE INTO local_change_meta(id,epoch) VALUES(1,?)',(uuid.uuid4().hex,))
    def trigger(table,pk,source,platform,cid,ts,columns):
        for op,prefix,kind in [('INSERT','NEW','added'),('UPDATE','NEW','updated'),('DELETE','OLD','deleted')]:
            condition=' WHEN '+ ' OR '.join(f'OLD.{c} IS NOT NEW.{c}' for c in columns) if op=='UPDATE' else ''
            def expr(v):return v if v.startswith("'") else prefix+'.'+v
            db.execute(f'''CREATE TRIGGER IF NOT EXISTS journal_{table}_{op.lower()} AFTER {op} ON {table}{condition}
              BEGIN INSERT INTO local_changes(kind,source_type,source_id,platform,conversation_id,source_timestamp)
              VALUES('{kind}',{expr(source)},{prefix}.{pk},{expr(platform)},{expr(cid)},{expr(ts)}); END''')
    trigger('messages','id',"'message'",'platform','conversation_id','timestamp',
            ['content','media_json','reply_to','sender','sender_id','is_self','timestamp','conversation_id','platform'])
    db.execute('''CREATE TRIGGER IF NOT EXISTS journal_message_metadata AFTER UPDATE ON messages
      WHEN OLD.conversation IS NOT NEW.conversation OR OLD.conversation_type IS NOT NEW.conversation_type OR OLD.source_id IS NOT NEW.source_id
      BEGIN INSERT INTO local_changes(kind,source_type,source_id,platform,conversation_id,source_timestamp)
      VALUES('metadata_changed','message',NEW.id,NEW.platform,NEW.conversation_id,NEW.timestamp); END''')
    trigger('artifact_sources','id',"'artifact_source'",'platform','conversation_id','timestamp',
            ['sha256','filename','metadata_json','availability','remote_status','message_id','timestamp'])
    for op,prefix in [('INSERT','NEW'),('UPDATE','NEW'),('DELETE','OLD')]:
        condition=' WHEN OLD.content IS NOT NEW.content OR OLD.metadata_json IS NOT NEW.metadata_json OR OLD.timestamp IS NOT NEW.timestamp' if op=='UPDATE' else ''
        db.execute(f'''CREATE TRIGGER IF NOT EXISTS journal_knowledge_{op.lower()} AFTER {op} ON qq_knowledge{condition} BEGIN
          INSERT INTO local_changes(kind,source_type,source_id,platform,conversation_id,source_timestamp)
          VALUES('{op.lower()}','qq_'||{prefix}.kind,{prefix}.id,'qq',{prefix}.conversation_id,{prefix}.timestamp); END''')
    # Transcripts, including late completion of an old voice, retain source scope.
    for op,prefix in [('INSERT','NEW'),('UPDATE','NEW'),('DELETE','OLD')]:
        condition=' WHEN OLD.transcript IS NOT NEW.transcript OR OLD.audio_sha256 IS NOT NEW.audio_sha256 OR OLD.profile IS NOT NEW.profile' if op=='UPDATE' else ''
        db.execute(f'''CREATE TRIGGER IF NOT EXISTS journal_voice_{op.lower()} AFTER {op} ON voice_sources{condition} BEGIN
          INSERT INTO local_changes(kind,source_type,source_id,platform,conversation_id,source_timestamp)
          SELECT 'voice_{op.lower()}','voice',m.id,m.platform,m.conversation_id,m.timestamp FROM messages m WHERE m.id={prefix}.message_id; END''')
    db.execute('''CREATE TRIGGER IF NOT EXISTS journal_parse AFTER UPDATE ON artifacts
      WHEN OLD.parse_status IS NOT NEW.parse_status OR OLD.parser IS NOT NEW.parser OR OLD.parse_note IS NOT NEW.parse_note BEGIN
      INSERT INTO local_changes(kind,source_type,source_id,platform,conversation_id,source_timestamp)
      SELECT 'parse_changed','artifact_source',id,platform,conversation_id,timestamp FROM artifact_sources WHERE sha256=NEW.sha256; END''')
    for op,prefix in [('INSERT','NEW'),('UPDATE','NEW'),('DELETE','OLD')]:
        condition=' WHEN OLD.text IS NOT NEW.text OR OLD.locator IS NOT NEW.locator' if op=='UPDATE' else ''
        db.execute(f'''CREATE TRIGGER IF NOT EXISTS journal_chunk_{op.lower()} AFTER {op} ON artifact_chunks{condition} BEGIN
          INSERT INTO local_changes(kind,source_type,source_id,platform,conversation_id,source_timestamp)
          SELECT 'chunks_changed','artifact_source',id,platform,conversation_id,timestamp FROM artifact_sources WHERE sha256={prefix}.sha256; END''')


def water(db):
    return dict(db.execute('SELECT epoch,high_water FROM local_change_meta WHERE id=1').fetchone())
