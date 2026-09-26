"""Isolated actual read API: old placeholders outside latest-N, scope and failure.
Client export is a fixture; local cache recovery/import/HTTP are real. No cloud.
"""
import io,json,sys,tempfile,time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from chatlocal.store import Store
from chatlocal import media as cache
from chatlocal.watch_routes import install_watch_routes
from reader_media import QQMedia
from refresh_media import recover

with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='import-stickers-') as tmp:
    folder=Path(tmp);store=Store(folder/'messages.sqlite3')
    parser=QQMedia(SimpleNamespace(),folder/'client/nt_db',load_stickers=True)
    path=parser.root/'Pic/2026-09/Ori'/('a'*32+'.gif');path.parent.mkdir(parents=True)
    image=io.BytesIO();Image.new('RGB',(20,20),'red').save(image,'GIF');path.write_bytes(image.getvalue())
    media=[cache.omitted_sticker('sticker',md5='a'*32,filename='face.gif')]
    def row(sid,cid='a',date='2026-09-02',platform='qq',items=None):
        return dict(platform=platform,conversation_id=f'test:group:{cid}',conversation='fixture',sender='甲',
                    content='',source_id=str(sid),timestamp=date,is_self=False,media=items or media)
    initial=[row(1),row(2,'b'),row(3,date='2026-08-01'),row(4,platform='wechat')]
    source=folder/'initial.json';source.write_text(json.dumps(dict(messages=initial)),encoding='utf8');store.import_file(source,load_stickers=False)
    actual_import=store.import_file
    reader_calls=[];recoveries=[];mode=['ok'];retries=[0]
    def reader(platform,args,output,**kwargs):
        reader_calls.append((platform,args));retries[0]+=1
        if mode[0]=='busy' and retries[0]<3:raise ValueError('数据库在复制期间更新；请重试，未推进进度。')
        if mode[0]=='fail' and platform=='qq':raise ValueError('客户端读取失败（隔离测试）')
        # The newest 1 message does not include the old sticker at all.
        output.write_text(json.dumps(dict(messages=[dict(row(100,platform=platform),content='最新文本',media=[])])),encoding='utf8')
    def worker(request,output):
        payload=json.loads(request.read_text('utf8'));recoveries.append(payload)
        output.write_text(json.dumps(recover(payload,parser,'test')),encoding='utf8')
    # Save the real function before patching the data route's late import.
    from chatlocal.media_refresh import refresh_for_import as real_repair
    def repair(store,platform,conversations,start,end,progress):
        return real_repair(store,platform,conversations,start,end,progress,runner=worker)
    app=FastAPI();install_watch_routes(app,store)
    body=dict(scope={'qq':dict(enabled=True,conversations=['test:group:a']),'wechat':dict(enabled=False,conversations=None)},
              limits=dict(qq_per_chat=1,wechat_per_chat=1),ranges={p:dict(start='2026-09-01',end='2026-09-30') for p in ('qq','wechat')},load_stickers=True)
    def wait(client,jid):
        for _ in range(250):
            value=client.get('/api/watch-jobs/'+jid).json()
            if value['status']!='running':return value
            time.sleep(.02)
        raise AssertionError('timeout')
    with patch.object(cache,'DATA',folder/'cache'),patch('chatlocal.data_routes.run_reader',side_effect=reader),patch('chatlocal.media_refresh.refresh_for_import',side_effect=repair),TestClient(app) as client:
        mode[0]='busy'
        result=wait(client,client.post('/api/data/read',json=body).json()['job_id'])
        assert result['status']=='completed',result
        assert retries[0]==3 and result['result']['platforms'][0]['media']['recovered']==1
        assert store.message(1)['media'][0]['status']=='available'
        assert all(store.message(n)['content']==cache.STICKER_PLACEHOLDER for n in (2,3,4))
        assert len(recoveries)==1 and [m['_message_id'] for m in recoveries[0]['messages']]==[1]
        assert reader_calls[-1][1][reader_calls[-1][1].index('--per-chat')+1]=='1'
        result=wait(client,client.post('/api/data/read',json=body).json()['job_id'])
        assert result['result']['platforms'][0]['added']==0 and result['result']['platforms'][0]['media']['recovered']==0
        # Reset only the isolated fixture, then explicitly opt out.
        actual_import(source,load_stickers=False);body['load_stickers']=False
        count=len(recoveries);result=wait(client,client.post('/api/data/read',json=body).json()['job_id'])
        assert len(recoveries)==count and store.message(1)['content']==cache.STICKER_PLACEHOLDER
        # QQ read failure cannot hide media success or prevent WeChat import.
        body['load_stickers']=True;body['scope']['wechat']=dict(enabled=True,conversations=['test:group:b'])
        mode[0]='fail';result=wait(client,client.post('/api/data/read',json=body).json()['job_id'])
        assert result['status']=='error' and result['result']['status']=='partial',result
        assert result['result']['platforms'][0]['status']=='error' and result['result']['platforms'][0]['media']['recovered']==1
        assert result['result']['platforms'][1]['status']=='ok'
        assert '聊天读取失败' in result['error'] and '媒体补载' in result['error']
        assert json.loads((folder/'read-latest.json').read_text('utf8'))['options']['load_stickers'] is True
print('PASS: actual import route repairs old sticker outside latest-N; scope/date/platform, retries, opt-out, idempotency and honest partial failure. Fixture export, local recovery, no cloud.')
