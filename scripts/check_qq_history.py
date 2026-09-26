"""Native QQ SQL -> actual protobuf decoder -> JSON -> Store, isolated data.

Only client discovery/key extraction/decryption are fixtures. No client access,
cloud requests or personal data; --package tests the exact shipped Python code.
"""
import argparse
from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import redirect_stdout


def protobuf(field, text):
    def varint(n):
        result=bytearray()
        while n>127:result.append((n&127)|128);n>>=7
        result.append(n);return bytes(result)
    raw=text.encode('utf-8')
    return varint((field<<3)|2)+varint(len(raw))+raw


def check(root):
    sys.path[:0]=[str(root),str(root/'scripts')]
    import export_qq as exporter
    from chatlocal.store import Store
    from chatlocal.data_routes import read_clients,run_reader
    q=exporter.q
    with tempfile.TemporaryDirectory(dir=root/'.tmp',prefix='qq-history-') as tmp:
        folder=Path(tmp);data=folder/'data';work=data/'qq-reader/decrypted';work.mkdir(parents=True)
        db_path=work/'messages.db';now=1780000000
        with closing(sqlite3.connect(db_path)) as db:
            for spec in q._QQ_MESSAGE_TABLE_SPECS:
                db.execute(f'CREATE TABLE "{spec.table}" ("40001" INTEGER,"40003" INTEGER,"40050" INTEGER,"40090" TEXT,"40033" INTEGER,"40800" BLOB,"{spec.conversation_column}" TEXT,"40020" TEXT,"40011" INTEGER,"40012" INTEGER)')
                for uid,stamp in ((1,now),(2,0),(3,-1),(4,1),(5,now+10)):
                    body=protobuf(45101,'fixture text '+str(uid))
                    db.execute(f'INSERT INTO "{spec.table}" VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (uid,uid,stamp,'fixture sender',100,body,'200','uid_fixture',2,1))
            db.commit()
        original_hash=hashlib.sha256(db_path.read_bytes()).hexdigest()
        # Exactly reproduce the reported upstream failure; recovery=True does
        # not make a zero timestamp acceptable to the native record decoder.
        row=(2,0,'fixture sender',100,protobuf(45101,'fixture'),'200',0,2,'direct','uid_fixture')
        try:q._qq_message_page_record(row,account_id='100',conversation_id='200',buddy_map={},group_map={},group_name_map={},recover_unreadable_rows=True)
        except RuntimeError as exc:assert str(exc)=='QQ message timestamp is invalid'
        else:raise AssertionError('Expected the reported timestamp failure')
        output=folder/'export.json'
        meta=dict(account='100',source=str(folder/'client/nt_db'),snapshot_at=now,fingerprint={})
        reader=SimpleNamespace(is_available=lambda:True,key=b'fixture',db_path=folder/'client/nt_db/nt_msg.db')
        args=SimpleNamespace(account=None,refresh=True,days=30,per_chat=500,date_range=True,start='',end='',
            chat=None,list=False,no_stickers=True,incremental_request=None,output=str(output))
        with patch('chatlocal.config.DB',folder/'chats.sqlite3'),patch.object(exporter,'DATA',data),patch.object(exporter,'snapshot',return_value=meta),\
             patch.object(q,'QQDBReader',return_value=reader),patch.object(q,'_query_account_qq_number',return_value='100'),\
             patch('qq_cached_reader.decrypt_database',return_value=q._QQDecryptedDatabase(db_path,False)),\
             patch.object(exporter,'cache_mode',return_value='balanced'),patch.object(exporter.time,'time',return_value=now+100),\
             redirect_stdout(io.StringIO()) as captured:
            exporter.export(args)
            payload=json.loads(output.read_text('utf-8'))
            assert payload['invalid_timestamps']==6 and payload['skipped']==6,payload
            assert len(payload['messages'])==4 and {m['source_id'] for m in payload['messages']}=={'1','5'},payload
            assert all(m['content'].startswith('fixture text ') and m['timestamp']>=now for m in payload['messages'])
            store=Store(folder/'chats.sqlite3')
            assert store.import_file(output)['imported']==4
            assert store.import_file(output)['imported']==0
            assert store.import_file(output)['duplicate']==4
            # The same production receipt must make exclusions visible through
            # the import route; it must not turn into an opaque "read failed".
            stdout=captured.getvalue()
            with patch('chatlocal.data_routes.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=stdout,stderr='')):
                receipt=run_reader('qq',[],output)
            assert receipt==dict(skipped=6,invalid_timestamps=6),receipt
            def prepared_reader(platform,arguments,destination,**kwargs):
                destination.write_bytes(output.read_bytes());return receipt
            events=[]
            with patch('chatlocal.data_routes.run_reader',side_effect=prepared_reader):
                result=read_clients(store,{'qq':dict(enabled=True,conversations=None),'wechat':dict(enabled=False,conversations=None)},
                    dict(qq_per_chat=500),{'qq':dict(start='',end='')},False,True,events.append)
            assert result['status']=='ok' and result['platforms'][0]['duplicate']==4,result
            assert '6' in result['summary'] and '时间戳无效' in result['summary']
            assert json.loads((folder/'read-latest.json').read_text('utf-8'))['platforms'][0]['invalid_timestamps']==6
            # Named date bounds remove invalid rows naturally; selected chat
            # and per-chat cap remain intact. Both empty date fields are tested above.
            args.start='2026-01-01';args.end='2026-12-31';args.chat=['100:direct:200'];args.per_chat=1
            exporter.export(args)
            limited=json.loads(output.read_text('utf-8'))
            assert limited['invalid_timestamps']==0 and len(limited['messages'])==1
            assert limited['messages'][0]['source_id']=='5' and limited['messages'][0]['conversation_type']=='direct'
        assert hashlib.sha256(db_path.read_bytes()).hexdigest()==original_hash
    print('PASS QQ history: reproduced zero-time failure; blank/date/selected-chat bounds, real protobuf decode, normal rows imported, no duplicate, UI receipt with exclusions; source unchanged. Isolated fixture.')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--package',type=Path,default=Path(__file__).resolve().parents[1])
    check(parser.parse_args().package.resolve())
