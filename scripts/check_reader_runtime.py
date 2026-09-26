"""Real packaged QQ child process + isolated WeChat key authentication fixtures."""
import argparse
from contextlib import closing
import ctypes
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import struct
import subprocess
import sys
import tempfile
from unittest.mock import patch
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]


def qq_child(proxy_path,database):
    # Exercise the export query shipped beside this exact proxy, including a
    # relocated overlay installation, not a newer query from the dev checkout.
    sys.path.insert(0,str(Path(proxy_path).resolve().parents[3]/'scripts'))
    from qq_message_types import history_query, MIN_MESSAGE_TIME
    spec=importlib.util.spec_from_file_location('qq_proxy',proxy_path)
    proxy=importlib.util.module_from_spec(spec);spec.loader.exec_module(proxy)
    # Conda, standard venv and embedded Python locate SQLite differently.
    # Inspect the already loaded main SQLite, never load a second DLL just
    # to check it (and never load the QQ child's DLL into this process).
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetModuleHandleW.argtypes=[ctypes.c_wchar_p]
    kernel.GetModuleHandleW.restype=ctypes.c_void_p
    handle=kernel.GetModuleHandleW('sqlite3.dll')
    assert handle,'Main SQLite module not loaded'
    dll=ctypes.WinDLL('sqlite3.dll',handle=handle)
    dll.sqlite3_libversion.restype=ctypes.c_char_p
    assert dll.sqlite3_libversion().decode()==sqlite3.sqlite_version
    def pending():return dll.sqlite3_test_control(ctypes.c_int(11),ctypes.c_uint(0))
    assert pending()==0x40000000
    conn=proxy.IsolatedQQSQLiteConnection(Path(database))
    try:
        assert conn.verification['sqlite_version']=='3.53.2'
        assert conn.execute('SELECT name FROM buddy_list WHERE id=?',(1,)).fetchall()==[('fixture',)]
        spec=SimpleNamespace(table='group_msg_table',conversation_column='40027',table_rank=0,conversation_type='group')
        base=1800000000
        query,params=history_query(spec,since=base+100,until=base+200,peers=['A'],limit=1)
        rows=conn.execute(query,params).fetchall()
        assert {r[0] for r in rows}=={2,4},rows  # one newest text + one newest media, selected chat/date only
        query,params=history_query(spec,since=None,until=base+200,peers=[],limit=2)
        assert {r[0] for r in conn.execute(query,params).fetchall()}=={1,2,3,4,6}
        query,params=history_query(spec,since=None,until=base+200,peers=[],limit=10000)
        assert {r[0] for r in conn.execute(query,params).fetchall()}=={1,2,3,4,6}
        query,params=history_query(spec,since=None,until=base+200,peers=['A'],limit=1,count_invalid=True)
        assert conn.execute(query,params).fetchone()[0]==3
        query,params=history_query(spec,since=MIN_MESSAGE_TIME,until=base+200,peers=['A'],limit=1,count_invalid=True)
        assert conn.execute(query,params).fetchone()[0]==0
        for sql in ('DELETE FROM buddy_list','SELECT * FROM private_table',
                    "SELECT load_extension('fixture')",'SELECT * FROM buddy_list; DELETE FROM buddy_list',
                    'WITH x AS (SELECT * FROM buddy_list) SELECT * FROM x'):
            try:conn.execute(sql).fetchall()
            except sqlite3.DatabaseError:pass
            else:raise AssertionError('write/unapproved table allowed')
    finally:conn.close()
    assert pending()==0x40000000
    with Path(database).open('r+b') as stream:
        stream.seek((262144-1)*4096);stream.write(b'bad')
    try:proxy.IsolatedQQSQLiteConnection(Path(database))
    except proxy.QQShiftedSQLiteError as exc:assert 'database_lock_page_invalid' in str(exc)
    else:raise AssertionError('invalid lock page accepted')
    print(json.dumps(dict(qq='pass',main_sqlite=sqlite3.sqlite_version,child_sqlite='3.53.2',main_pending_byte=pending())))


def check_qq(runtime,proxy):
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='qq-reader-runtime-') as name:
        db=Path(name)/'fixture.db'
        with closing(sqlite3.connect(db)) as conn:
            conn.execute('PRAGMA page_size=4096')
            conn.execute('CREATE TABLE buddy_list(id INTEGER,name TEXT)')
            conn.execute('CREATE TABLE private_table(id INTEGER)')
            conn.execute('INSERT INTO buddy_list VALUES(1,?)',('fixture',))
            conn.execute('CREATE TABLE group_msg_table("40001","40050","40090","40033","40800","40027","40020","40012","40011")')
            base=1800000000
            conn.executemany('INSERT INTO group_msg_table VALUES(?,?,?,?,?,?,?,?,?)',[
                (1,base+100,'fixture','fixture',b'','A',0,1,2),(2,base+110,'fixture','fixture',b'','A',0,1,2),
                (3,base+120,'fixture','fixture',b'','A',0,2,2),(4,base+130,'fixture','fixture',b'','A',0,3,2),
                (5,base+200,'fixture','fixture',b'','A',0,1,2),(6,base+110,'fixture','fixture',b'','B',0,1,2),
                (7,base+140,'fixture','fixture',b'','A',0,99,2),
                (8,0,'fixture','fixture',b'','A',0,1,2),(9,-1,'fixture','fixture',b'','A',0,1,2),
                (10,1,'fixture','fixture',b'','A',0,1,2),(11,0,'fixture','fixture',b'','B',0,1,2)])
            conn.commit()
        # Sparse tail exercises the >1 GiB lock-page attestation with ~12 KiB
        # actual fixture data, not a copy of any user's QQ database.
        subprocess.run(['fsutil','sparse','setflag',str(db)],check=True,capture_output=True)
        with db.open('r+b') as stream:stream.truncate(262144*4096)
        result=subprocess.run([str(runtime),str(Path(__file__).resolve()),'--qq-child',str(proxy),str(db)],
                              capture_output=True,text=True,timeout=40)
        assert result.returncode==0,result.stderr or result.stdout
        print(result.stdout.strip())


def check_wechat():
    sys.path[:0]=[str(ROOT),str(ROOT/'tools/wechat-reader')]
    from chatlocal import wechat_keys as fallback
    from wechatauto.db import _verify_enc_key
    key=bytes(range(32));salt=bytes(range(16))
    page=salt+bytes(4016)
    mac_key=hashlib.pbkdf2_hmac('sha512',key,bytes(x^0x3a for x in salt),2,dklen=32)
    page+=hmac.new(mac_key,page[16:]+struct.pack('<I',1),hashlib.sha512).digest()
    assert len(page)==4096 and _verify_enc_key(key,page)
    literal="x'"+(key+salt).hex()+"'"
    stats={'limited':False}
    for chunk in (literal.encode(),literal.encode('utf-16le'),("x'"+key.hex()+"'").encode()):
        assert fallback.authenticated_keys([chunk],{'fixture':page},_verify_enc_key,stats)=={'fixture':key}
    wrong_salt=("x'"+(key+b'X'*16).hex()+"'").encode()
    wrong_key=("x'"+(b'X'*32+salt).hex()+"'").encode()
    assert fallback.authenticated_keys([wrong_salt,wrong_key],{'fixture':page},_verify_enc_key,stats)=={}
    with patch.object(fallback,'MAX_CANDIDATES',1):
        assert fallback.authenticated_keys([wrong_key,literal.encode()],{'fixture':page},_verify_enc_key,stats)=={}
        assert stats['limited']
    # An inaccessible process is a no-key result; later upstream cfg/account
    # recovery remains reachable. Logs must not expose candidate key material.
    import contextlib,io
    from types import SimpleNamespace
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='wechat-key-fixture-') as name:
        db=Path(name)/'fixture.db';db.write_bytes(page)
        reader=SimpleNamespace(_db_files=[('fixture',db,None)])
        def none(*args):yield from ()
        captured=io.StringIO()
        with patch.object(fallback,'memory_chunks',none),contextlib.redirect_stderr(captured):
            assert fallback.recover_legacy_keys(reader,{})=={}
        assert key.hex() not in captured.getvalue()
    print('PASS WeChat: ASCII/UTF16 raw keys, real page HMAC, wrong key/salt rejected, bounded candidates, no-key fallback preserved. Synthetic; not a 4.1.5 client acceptance.')


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--qq-child':qq_child(*sys.argv[2:])
    else:
        parser=argparse.ArgumentParser();parser.add_argument('--runtime',type=Path,default=Path(sys.executable))
        parser.add_argument('--proxy',type=Path,default=ROOT/'tools/qq-reader/chatlog_keeper/_qq_sqlite_proxy.py');args=parser.parse_args()
        check_qq(args.runtime.resolve(),args.proxy.resolve());check_wechat()
