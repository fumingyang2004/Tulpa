"""Read-only live chat routes with isolated messages; no cloud/client writes."""
import json,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from chatlocal.store import Store
from chatlocal.chat_view import install_chat_routes


def main():
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='chat-browser-') as tmp:
        folder=Path(tmp);store=Store(folder/'db.sqlite3');path=folder/'messages.json'
        def put(rows):path.write_text(json.dumps(rows),encoding='utf-8');store.import_file(path)
        def row(n,platform='qq',cid='course',timestamp=None):
            return dict(platform=platform,conversation_id=cid,conversation='课程群' if cid=='course' else '微信好友',sender='老师',
                content=f'消息 {n}',timestamp=timestamp or f'2026-09-24 10:{n//60:02}:{n%60:02}',is_self=n%2==0,source_id=str(n))
        put([row(n) for n in range(60)]+[row(1,'wechat','friend','2026-09-24 11:00:00')])
        app=FastAPI();install_chat_routes(app,store);client=TestClient(app)
        def get(route,**args):
            r=client.get('/api/'+route,params=args);assert r.status_code==200,r.text;return r.json()
        listing=get('chat-conversations',limit=1)
        assert listing['conversations'][0]['platform']=='wechat' and listing['has_more']
        second=get('chat-conversations',limit=1,offset=1)['conversations'][0]
        assert second['preview']=='消息 59' and second['count']==60
        assert get('chat-conversations',query='课程',platform='qq')['conversations']==[second]
        assert get('chat-conversations',since_seq=listing['high_water'],epoch=listing['epoch'])['unchanged']
        page=get('chat-context',platform='qq',conversation_id='course',before=20,after=0)
        assert page['messages'][-1]['content']=='消息 59' and not page['has_after']
        def params(p,follow=False):
            first,last=p['messages'][0],p['messages'][-1]
            return dict(platform=p['platform'],conversation_id=p['conversation_id'],first_timestamp=first['timestamp'],first_id=first['id'],
                last_timestamp=last['timestamp'],last_id=last['id'],follow=follow,epoch=p['epoch'],since_seq=p['high_water'])
        frozen=params(page);assert get('chat-updates',**frozen)['unchanged']
        put([row(60)])
        # Reading history stays put but advertises the new tail; following appends.
        history=get('chat-updates',**frozen)
        assert [m['id'] for m in history['messages']]==[m['id'] for m in page['messages']] and history['has_after']
        live=get('chat-updates',**dict(frozen,follow=True))
        assert live['messages'][-1]['content']=='消息 60' and not live['has_after']
        # Late arrivals inside the displayed timestamps, updates and anchor deletion.
        put([row(500,timestamp='2026-09-24 10:00:45')])
        with store.connect() as db:
            db.execute("UPDATE messages SET content='已更正 <script>不可执行</script>',media_json=? WHERE id=?",
                (json.dumps([dict(kind='image',format='png',width=24,height=24)]),page['messages'][2]['id']))
            db.execute('DELETE FROM provenance WHERE message_id=?',(page['anchor_message_id'],))
            db.execute('DELETE FROM messages WHERE id=?',(page['anchor_message_id'],))
        history=get('chat-updates',**frozen)
        assert any(m['content']=='消息 500' for m in history['messages'])
        assert any(m['content'].startswith('已更正') and not m['media'][0]['available'] for m in history['messages'])
        assert page['anchor_message_id'] not in [m['id'] for m in history['messages']]
        assert all(m['conversation_id']=='course' for m in history['messages'])
        missing=dict(frozen,first_timestamp=page['messages'][-1]['timestamp'],first_id=page['messages'][-1]['id'])
        empty=get('chat-updates',**missing)
        assert not empty['messages'] and empty['has_after'] and empty['latest_message_id']
        # A different conversation can advance the poll watermark without sending this window again.
        next_params=dict(frozen,since_seq=history['high_water'])
        put([row(2,'wechat','friend','2026-09-24 11:01:00')])
        assert get('chat-updates',**next_params)['unchanged']
        assert get('chat-conversations')['conversations'][0]['preview']=='消息 2'
        assert get('chat-updates',**dict(next_params,epoch='replaced'))['messages']
        assert get('chat-updates',**dict(next_params,since_seq=-1))['messages']
        put([row(n) for n in range(61,301)])
        burst=get('chat-updates',**dict(frozen,follow=True))
        assert len(burst['messages'])==200 and burst['messages'][-1]['content']=='消息 300' and burst['has_before']
        for route,args in [('chat-conversations',{}),('chat-updates',frozen)]:
            assert client.get('/api/'+route,params=args,headers={'Origin':'https://example.com'}).status_code==403
        assert client.get('/api/chat-conversations',params={'limit':201}).status_code==400
        assert client.get('/api/chat-updates',params=dict(frozen,platform='bad')).status_code==400
        assert client.get('/api/chat-context',params={'anchor_message_id':1,'platform':'wechat'}).status_code==404
    print('PASS: last-message directory/search/paging, no-change fast path, live tail vs fixed history, late arrivals, edits/media/deletion, scope, epoch reset, 200-row bound and origin checks. Synthetic only.')


if __name__=='__main__':main()
