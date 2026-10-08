"""Isolated sticker lifecycle/permission/cost checks. Only loopback mock OneBot."""
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
import hashlib
import io
import json
from pathlib import Path
import socket
import statistics
import sys
import tempfile
import threading
import time
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent))
from check_mcp_chat_media import Events, StickerBot, eventually, ROOT
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from chatlocal.store import Store
from chatlocal.mcp_routes import install_mcp_routes
from chatlocal.mcp_chat_media import ChatMedia
from chatlocal.mcp_sticker_library import scope_key
from chatlocal.onebot import OneBotError, OneBotUncertain, invalidate_availability
from chatlocal.message_sender import SendError, SendUncertain


def picture(seed):
    out=io.BytesIO();Image.new('RGB',(80,60),(seed%256,(seed*47)%256,(seed*13)%256)).save(out,'PNG')
    return out.getvalue()


def main():
    events=Events();upstream=ThreadingHTTPServer(('127.0.0.1',0),StickerBot)
    StickerBot.sent=[];StickerBot.collected=[];StickerBot.uncertain=False
    threading.Thread(target=upstream.serve_forever,daemon=True).start()
    metrics={}
    try:
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='sticker-library-') as tmp,patch.dict('os.environ',{},clear=True):
            folder=Path(tmp);store=Store(folder/'chats.sqlite3')
            (folder/'.env').write_text(f'REPLY_ONEBOT_URL=http://127.0.0.1:{upstream.server_port}\nREPLY_ONEBOT_WS_URL=ws://127.0.0.1:{events.port}\nREPLY_ONEBOT_WS_TOKEN=event-fixture\n','utf-8')
            app=FastAPI();svc=install_mcp_routes(app,store);access=svc.access;tools=svc.tools
            with patch('chatlocal.onebot.ROOT',folder),patch('chatlocal.message_sender.ROOT',folder),TestClient(app) as ui:
                invalidate_availability()
                with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                assert ui.put('/api/mcp',json={'enabled':True,'port':port},headers={'X-ChatWeave-UI':'1'}).json()['running']
                cfg=dict(name='isolated-library',platforms=['qq'],conversations=[['qq','111:group:222'],['qq','111:group:223']],send=True,chat=True,chat_images=True,chat_sticker_send=True,chat_sticker_collect=True)
                g=access.create(cfg);grant=access.authorize(g['token'],source=False)
                def tool(name,conn=g,**args):return tools.call(conn['token'],name,args,threading.Event())
                def start(key,conn=g,cid='111:group:222'):
                    r=tool('start_chat_session',conn=conn,conversation_id=cid,persona_preset='little_whale',idempotency_key=key)
                    assert not r.isError,r
                    return r.structuredContent['session']['id']
                sid=start('library-start')
                eventually(lambda:tools.chat.receiver.status()['state']=='connected')
                media=tools.chat.media;lib=media.library;row=tools.chat.row(sid)
                def invoke(name,**args):return media.call(grant,name,dict(session_id=sid,**args),threading.Event())[0]
                def rejected(fn):
                    try:fn()
                    except ValueError:return
                    raise AssertionError('Operation unexpectedly allowed')
                def view(seed,description=None):
                    aid=media.upsert(sid,'fixture:'+str(seed),dict(url=f'https://gchat.qpic.cn/{seed}.png'))
                    with patch('chatlocal.mcp_chat_media.download',return_value=picture(seed)):
                        invoke('read_chat_sticker',sticker_id=aid)
                    if description is not None:invoke('note_chat_sticker',sticker_id=aid,description=description,tags=['开心'] if seed==1 else ['轮换'])
                    return aid
                def collect(aid,key,**args):return invoke('collect_chat_sticker',sticker_id=aid,idempotency_key=key,**args)
                def item(aid):return lib.find(grant,row,media.asset(sid,aid)['digest'])
                # Cold empty state, tools hidden for old grants, no native DB use.
                assert media.candidates(grant,row,[])[1]['code']=='empty'
                old=access.create({**cfg,'chat_images':False,'chat_sticker_send':False,'chat_sticker_collect':False})
                assert 'read_chat_image' not in {s['name'] for s in tools.schemas(access.authorize(old['token'],source=False))}
                aid=media.upsert(sid,'unseen',dict(url='https://gchat.qpic.cn/unknown.png'))
                rejected(lambda:invoke('send_chat_sticker',sticker_id=aid,idempotency_key='must-see-first'))
                # Viewed event can send without collecting; receipt and usage atomic/idempotent.
                a=view(1,'红色方块，开心庆祝')
                assert not item(a)['local_bytes']
                result=invoke('send_chat_sticker',sticker_id=a,idempotency_key='direct-no-collect')
                assert result['state']=='SUCCEEDED' and not StickerBot.collected
                assert invoke('send_chat_sticker',sticker_id=a,idempotency_key='direct-no-collect')['id']==result['id']
                assert item(a)['use_count']==1 and len(StickerBot.sent)==1
                assert media.candidates(grant,row,[{'content':'开心'}])[0]==[]
                # Single-call collect + understanding, content-level dedup across request ids.
                combined=collect(a,'collect-combined',description='红色方块，开心庆祝',tags=['开心'],emotion='高兴',usage='庆祝',avoid='严肃坏消息')
                assert combined['state']=='SUCCEEDED' and all(combined['result'][k]['state'] in ('SAVED','SUCCEEDED') for k in ('qq','local','notes')),combined
                again=collect(a,'collect-another-key')
                assert again['result']['qq']['deduplicated'] and len(StickerBot.collected)==1
                rejected(lambda:collect(a,'collect-combined',description='different'))
                # Definitive QQ failure retains local original and notes; unknown never retried.
                b=view(2)
                with patch.object(media,'client',side_effect=OneBotError('fixture unavailable')):
                    partial=collect(b,'qq-unavailable',description='绿色方块',tags=['开心'])
                assert partial['state']=='PARTIAL' and partial['result']['local']['state']=='SAVED' and partial['result']['qq']['state']=='FAILED'
                assert collect(b,'qq-retry-new-key')['state']=='SUCCEEDED'
                c=view(3,'蓝色方块')
                with patch.object(lib,'pin',side_effect=OSError('fixture disk full')):
                    partial=collect(c,'local-unavailable')
                assert partial['state']=='PARTIAL' and partial['result']['qq']['state']=='SUCCEEDED' and partial['result']['local']['state']=='FAILED'
                before=len(StickerBot.collected)
                assert collect(c,'restore-local')['state']=='SUCCEEDED' and len(StickerBot.collected)==before
                d=view(4,'紫色方块')
                with patch.object(lib,'note',side_effect=OSError('fixture disk full')):
                    partial=collect(d,'note-unavailable',description='紫色方块')
                assert partial['state']=='PARTIAL' and partial['result']['notes']['state']=='FAILED'
                e=view(5,'黄色方块');StickerBot.uncertain=True
                uncertain=collect(e,'collection-unknown')
                assert uncertain['state']=='UNKNOWN' and uncertain['result']['local']['state']=='SAVED'
                count=len(StickerBot.collected)
                assert collect(e,'collection-unknown-new-key')['state']=='UNKNOWN' and len(StickerBot.collected)==count
                StickerBot.uncertain=False
                # Concurrent content dedup, including different request keys.
                f=view(6,'灰色方块');before=len(StickerBot.collected)
                with ThreadPoolExecutor(2) as pool:
                    receipts=list(pool.map(lambda n:collect(f,'concurrent-'+str(n)),range(2)))
                assert len(StickerBot.collected)==before+1,receipts
                assert any(r['state']=='SUCCEEDED' for r in receipts)
                # Capacity failure cannot silently evict pinned images.
                h=view(7,'橙色方块');lib.policy['scope_mib']=0
                cap=collect(h,'capacity-rejected');lib.policy['scope_mib']=128
                assert cap['state']=='PARTIAL' and cap['result']['local']['code']=='local_capacity'
                local_only=view(8)
                with patch.object(media,'client',side_effect=AssertionError('Local-only collection contacted QQ')):
                    protocol_result=tool('collect_chat_sticker',session_id=sid,sticker_id=local_only,idempotency_key='local-only',save_qq=False,description='本地原件',emotion='平静',usage='休息',avoid='严肃场景',uncertainty='')
                    assert not protocol_result.isError,protocol_result
                    local_result=protocol_result.structuredContent
                assert local_result['state']=='SUCCEEDED' and local_result['result']['qq']['state']=='SKIPPED'
                # QQ id mapping must not replace actual viewing; a reused remote id changes hash.
                remote=media.upsert(sid,'qq:reused',dict(url='https://gchat.qpic.cn/reused.png'))
                with patch('chatlocal.mcp_chat_media.download',return_value=picture(20)):invoke('read_chat_sticker',sticker_id=remote)
                old_digest=media.asset(sid,remote)['digest']
                with patch('chatlocal.mcp_chat_media.download',return_value=picture(21)):invoke('read_chat_sticker',sticker_id=remote)
                assert media.asset(sid,remote)['digest']!=old_digest
                # Missing volatile resource becomes unavailable; changed downloaded bytes cannot send.
                evicted=view(22,'缓存样本');old_item=item(evicted)
                (media.root/(old_item['digest']+'.bin')).unlink()
                assert lib.verified_bytes(grant,row,old_item)[1]=='cache_missing'
                with patch('chatlocal.mcp_chat_media.download',return_value=picture(23)):
                    rejected(lambda:invoke('send_chat_sticker',sticker_id=evicted,idempotency_key='changed-cannot-send'))
                # Pinned original missing/corrupted never silently falls back to LRU.
                saved=item(b);path=lib.path(g['id'],'111',saved['digest']);original=path.read_bytes()
                path.write_bytes(picture(24));assert lib.verified_bytes(grant,row,saved)[1]=='hash_changed'
                path.unlink();assert lib.verified_bytes(grant,row,saved)[1]=='local_missing'
                local=invoke('list_chat_stickers',source='library',query='绿色')
                assert len(local['items'])==1 and not local['items'][0]['sendable']
                rejected(lambda:invoke('send_chat_sticker',sticker_id=local['items'][0]['sticker_id'],idempotency_key='missing-no-send'))
                path.write_bytes(original)
                # Uncertainty is retained but not promoted into familiar direct-use candidates.
                uncertain_note=view(25)
                collect(uncertain_note,'uncertain-understanding',description='画面模糊',uncertainty='文字看不清')
                assert not item(uncertain_note)['ready']
                # Known library proof survives session stop/restart; grant/account boundaries stay.
                original_digest=item(a)['digest'];old_sid=sid
                tools.chat.stop(sid,g['id'],'fixture restart')
                sid=start('library-next-session',cid='111:group:223');row=tools.chat.row(sid)
                eventually(lambda:tools.chat.receiver.status()['state']=='connected')
                tools.chat.media=ChatMedia(tools.chat);media=tools.chat.media;lib=media.library
                with access.connect() as db:db.execute('UPDATE sticker_library SET last_attempt=0 WHERE grant_id=?',(g['id'],))
                with patch.object(media,'client',side_effect=AssertionError('Candidate contacted QQ')),patch('chatlocal.mcp_chat_media.download',side_effect=AssertionError('Candidate downloaded image')):
                    candidates,status=media.candidates(grant,row,[{'content':'大家开心庆祝'}])
                assert candidates and any(x['reason']=='context' for x in candidates),status
                red=next(x for x in candidates if '红色' in x['model_note'])
                assert red['resource']=='local' and red['use_count']==1 and red['sendable']
                assert media.asset(sid,red['sticker_id'])['digest']==original_digest
                assert invoke('send_chat_sticker',sticker_id=red['sticker_id'],idempotency_key='reused-after-restart')['state']=='SUCCEEDED'
                assert lib.find(grant,row,original_digest)['use_count']==2
                # A second grant cannot obtain any of this gallery, even same account/group.
                g2=access.create(cfg);sid2=start('other-grant',conn=g2);row2=tools.chat.row(sid2);grant2=access.authorize(g2['token'],source=False)
                assert media.candidates(grant2,row2,[])[0]==[]
                assert lib.find(grant,dict(row,conversation_id='999:group:223'),original_digest) is None
                rejected(lambda:media.call(grant2,'read_chat_sticker',dict(session_id=sid2,sticker_id=red['sticker_id']),threading.Event()))
                assert not lib.valid(dict(grant,scope={**grant['scope'],'conversations':['["qq","111:group:223"]']}),lib.find(grant,row,original_digest))
                # Confirmed failure and UNKNOWN do not count, unknown cannot use a fresh key.
                fail=view(30,'失败样本')
                sender=tools.actions.sender_factory()
                sender_call=sender.call
                def fail_send(action,payload,**kw):
                    if action=='send_group_msg':raise SendError('fixture rejected')
                    return sender_call(action,payload,**kw)
                def unknown_send(action,payload,**kw):
                    if action=='send_group_msg':raise SendUncertain('fixture unknown')
                    return sender_call(action,payload,**kw)
                with patch.object(sender,'call',side_effect=fail_send),patch.object(tools.actions,'sender_factory',return_value=sender):
                    failed=invoke('send_chat_sticker',sticker_id=fail,idempotency_key='failed-send')
                assert failed['state']=='FAILED' and item(fail)['use_count']==0
                with patch.object(sender,'call',side_effect=unknown_send),patch.object(tools.actions,'sender_factory',return_value=sender):
                    unknown=invoke('send_chat_sticker',sticker_id=fail,idempotency_key='unknown-send')
                assert unknown['state']=='UNKNOWN' and item(fail)['use_count']==0
                unknown_list=invoke('list_chat_stickers',source='library',query='失败样本')['items'][0]
                assert not unknown_list['sendable'] and unknown_list['last_send_state']=='UNKNOWN'
                rejected(lambda:invoke('send_chat_sticker',sticker_id=fail,idempotency_key='unknown-new-key'))
                tools.actions.recover();tools.chat.media=ChatMedia(tools.chat);media=tools.chat.media;lib=media.library
                assert invoke('send_chat_sticker',sticker_id=fail,idempotency_key='unknown-send')['state']=='UNKNOWN'
                # Cooldown, popularity and rotation; no remote work with a large indexed library.
                for seed in range(40,60):view(seed,'轮换方块 '+str(seed))
                with access.connect() as db:
                    db.execute('UPDATE sticker_library SET use_count=20 WHERE grant_id=? AND digest=?',(g['id'],item(view(61,'常用方块'))['digest']))
                all_rotation=set()
                for n in range(6):
                    with patch('chatlocal.mcp_sticker_library.time.time',return_value=time.time()+n*1800):
                        selected,diag=media.candidates(grant,row,[])
                    all_rotation.update(x['sticker_id'] for x in selected if x['reason']=='rotation')
                    assert any('常用' in x['model_note'] for x in selected)
                assert len(all_rotation)>1
                # 1,000 metadata-only assets should not displace understood candidates.
                with access.connect() as db:
                    for n in range(1000):
                        digest=hashlib.sha256(str(n).encode()).hexdigest()
                        db.execute('''INSERT OR IGNORE INTO sticker_library(grant_id,account,digest,scope_key,source_cid,source_ref,origin,seen_at,info,updated)
                            VALUES(?,?,?,?,?,?,?,?,?,?)''',(g['id'],'111',digest,scope_key(grant),row['conversation_id'],'{}','fixture',time.time(),'{}',time.time()))
                elapsed=[];max_size=0
                with patch.object(store,'connect',side_effect=AssertionError('Native DB accessed')),patch.object(media,'client',side_effect=AssertionError('Candidate contacted QQ')),patch('chatlocal.mcp_chat_media.download',side_effect=AssertionError('Candidate downloaded')):
                    for _ in range(25):
                        started=time.perf_counter();selected,diag=media.candidates(grant,row,[{'content':'开心庆祝'}]);elapsed.append((time.perf_counter()-started)*1000)
                        assert len(selected)<=4 and diag['examined']<=32 and diag['hash_checks']<=12 and not diag['remote_calls']
                        max_size=max(max_size,len(json.dumps(dict(items=selected,status=diag),ensure_ascii=False).encode()))
                assert statistics.median(elapsed)<250 and max_size<9000
                with access.connect() as db:
                    plan=[r[3] for r in db.execute('EXPLAIN QUERY PLAN SELECT * FROM sticker_library WHERE grant_id=? AND account=? AND scope_key=? AND ready=1 ORDER BY use_count DESC,digest LIMIT 8',(g['id'],'111',scope_key(grant)))]
                    assert any('sticker_ready_frequent' in p for p in plan),plan
                metrics=dict(samples=25,library_rows_at_least=1020,median_ms=round(statistics.median(elapsed),2),max_ms=round(max(elapsed),2),rounds_per_second=round(25000/sum(elapsed),2),candidate_payload_max_bytes=max_size,remote_calls=0)
                assert diag['previous_offer']=='no_send_observed'
                assert invoke('list_chat_stickers',source='library',query='nonexistent-token')['matched']==0
                # Prompt guidance really reaches little_whale, without editing the persona.
                context=tool('get_chat_session',session_id=sid).structuredContent['chat_prompt']
                assert context['persona_preset']=='little_whale' and not context['examples']
                assert any('collect_chat_sticker' in h for h in context['reminders'])
                assert context['sticker_status']['remote_calls']==0
                # Catalogue TTL reused across sessions, forced refresh has a minimum interval.
                catalogue=invoke('list_chat_stickers');assert not catalogue['catalog_cache_hit']
                assert invoke('list_chat_stickers',refresh=True)['refresh_limited']
                other_row=tools.chat.row(sid2)
                assert media.call(grant2,'list_chat_stickers',dict(session_id=sid2),threading.Event())[0]['catalog_cache_hit'] is False
                # A new session under the SAME grant reuses the catalog, not its handles.
                tools.chat.stop(sid2,g2['id'],'fixture finished')
                same_sid=start('same-grant-catalog',cid='111:group:222')
                same=media.call(grant,'list_chat_stickers',dict(session_id=same_sid),threading.Event())[0]
                assert same['catalog_cache_hit'] and same['items'][0]['sticker_id']!=catalogue['items'][0]['sticker_id']
                tools.chat.stop(same_sid,g['id'],'fixture finished')
                with patch.object(media.chat.receiver,'status',return_value={'state':'reconnecting','account':'111'}):
                    assert media.candidates(grant,row,[])[1]['code']=='source_unavailable'
                    rejected(lambda:collect(view(70),'offline-collect'))
                with patch.object(media.chat.receiver,'status',return_value={'state':'connected','account':'999'}):
                    assert media.candidates(grant,row,[])[0]==[]
                    rejected(lambda:invoke('read_chat_sticker',sticker_id=red['sticker_id']))
                with access.connect() as db:
                    db.executemany('INSERT INTO calls(grant_id,tool,at,status) VALUES(?,?,?,?)',[(g['id'],'send_chat_sticker',time.time(),'ok')]*12)
                assert media.candidates(grant,row,[])[1]['code']=='send_rate_limited'
                readonly=access.create({**cfg,'chat_sticker_send':False,'chat_sticker_collect':False});rsid=start('read-only-gallery',conn=readonly)
                rgrant=access.authorize(readonly['token'],source=False)
                assert media.candidates(rgrant,tools.chat.row(rsid),[])[1]['code']=='permission_or_session_unavailable'
                assert tool('collect_chat_sticker',conn=readonly,session_id=rsid,sticker_id=red['sticker_id'],idempotency_key='no-collect-grant').isError
                access.revoke(g['id'])
                rejected(lambda:invoke('read_chat_sticker',sticker_id=red['sticker_id']))
                rejected(lambda:collect(red['sticker_id'],'revoked-collect'))
                assert media.candidates(grant,row,[])[0]==[]
                with access.connect() as db:
                    log='\n'.join(r['metrics'] for r in db.execute('SELECT metrics FROM sticker_diagnostics'))
                    assert 'qpic' not in log and 'base64' not in log and 'event-fixture' not in log
    finally:
        StickerBot.release.set();events.close();upstream.shutdown();upstream.server_close();invalidate_availability()
    print('PASS: scoped durable pixel proof, unknown/changed/missing rejection, direct send, independent partial collection outcomes, concurrent content dedup, notes/usage/restart, cold/hot candidates, rotation/cooldown, revoke/account/scope isolation, no native history or QQ network during candidates. No real sends/collections.')
    print(json.dumps(metrics,ensure_ascii=False,sort_keys=True))


if __name__=='__main__':main()
