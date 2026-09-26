"""Per-turn OCR/native routing, cache isolation and real UI callback wiring. Offline."""
import json,sys,tempfile
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import httpx
from PIL import Image
from chatlocal.vision import VisionProvider,with_vision_mode,turn_vision_settings
from chatlocal.llm import validate_answer
from chatlocal.store import Store
from chatlocal.agent_sessions import Sessions
from chatlocal.ui import build_app
from chatlocal.watches import Watches
from chatlocal.agent_tools import ChatTools


def main():
    cfg=dict(API_BASE='https://api.deepseek.com',API_KEY='fixture-key',MODEL='deepseek-flash')
    native=turn_vision_settings(with_vision_mode(cfg,'native'))
    ocr=turn_vision_settings(with_vision_mode(cfg,'ocr'))
    assert native['VISION_API_KEY']==cfg['API_KEY'] and native['VISION_MODEL']==cfg['MODEL']
    assert ocr['VISION_PROVIDER']=='ocr' and not ocr['VISION_API_KEY'] and 'VISION_MODE' not in cfg
    for bad in (True,1,'',{},'unknown'):
        try:with_vision_mode(cfg,bad);raise AssertionError('Invalid mode accepted')
        except ValueError:pass
    calls=[]
    def response(request):
        body=json.loads(request.content);calls.append(body)
        assert request.headers['authorization']=='Bearer fixture-key'
        return httpx.Response(200,json=dict(choices=[dict(message=dict(content=json.dumps(dict(type='drawing',ocr='',description='一个红色方块',notes='fixture'))))],usage=dict(total_tokens=42)))
    Client=httpx.Client
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='vision-check-') as tmp:
        folder=Path(tmp);picture=folder/'sample.png';Image.new('RGB',(32,32),'red').save(picture)
        with patch('chatlocal.vision.DATA',folder),patch('chatlocal.vision.media_path',return_value=picture),patch('chatlocal.vision.httpx.Client',side_effect=lambda **kw:Client(transport=httpx.MockTransport(response),**kw)):
            with patch.object(VisionProvider,'_ocr',side_effect=AssertionError('Native mode used OCR')):
                result=VisionProvider(native).inspect({},'图中是什么？')
                cached=VisionProvider(native).inspect({},'图中是什么？')
            assert result['provider']=='deepseek' and result['image_uploaded'] and result['usage']['total_tokens']==42
            assert cached['cached'] and not cached['image_uploaded'] and len(calls)==1
            assert calls[0]['model']=='deepseek-flash' and calls[0]['thinking']=={'type':'disabled'}
            assert calls[0]['messages'][1]['content'][-1]['image_url']['url'].startswith('data:image/png;base64,')
            with patch.object(VisionProvider,'_cloud',side_effect=AssertionError('OCR attempted upload')),patch.object(VisionProvider,'_ocr',return_value={'ocr':'OCR测试','description':'本地文字'}):
                local=VisionProvider(ocr).inspect({},'图中是什么？')
                assert local['provider']=='ocr' and not local['cached'] and not local['image_uploaded']
            assert len(calls)==1
        evidence=[dict(id=1,media=[{}],content='')]
        claim=dict(claims=[dict(text='红色方块',evidence_ids=[1])])
        assert validate_answer(claim,evidence,inspections={'1:0':dict(result,message_id=1)})['claims']
        assert not validate_answer(claim,evidence,inspections={})['claims']
        store=Store(folder/'chats.sqlite3');sessions=Sessions(folder/'sessions.sqlite3');received=[]
        def agent(store,plan,question,*args,config,**kwargs):
            received.append(config)
            yield dict(type='done',status='completed',record=dict(result=dict(claims=[],insufficient=True),events=[],
                bundle=ChatTools(store,plan).bundle(),requests=0,usage={}))
        with patch('chatlocal.ui.Sessions',return_value=sessions),patch('chatlocal.ui.settings',return_value=cfg),patch('chatlocal.ui.run_agent',side_effect=agent):
            app=build_app(store);fns={fn.api_name:fn.fn for fn in app.fns.values()}
            for mode in ('native','ocr'):
                sid=sessions.create();output=list(fns['agent_action_controls']('看图',['qq'],[],'','','',sid,'deep',mode))[-1]
                turn=sessions.history(sid)[-1]
                assert turn['options']['vision_mode']==turn['record']['vision_mode']==mode
                assert received[-1]['VISION_PROVIDER']==('deepseek' if mode=='native' else 'ocr')
                assert ('DeepSeek 原生识图' if mode=='native' else '本地 OCR') in output[3]
            app.close()
        watches=Watches(store);card=watches.create('主题','看图',['qq'],[])
        with patch('chatlocal.watches.settings',return_value=cfg):
            watches.ask(card['id'],'看图',vision_mode='native',agent=agent)
            assert received[-1]['VISION_PROVIDER']=='deepseek'
            watches.ask(card['id'],'继续',vision_mode='ocr',agent=agent)
            assert received[-1]['VISION_PROVIDER']=='ocr'
        assert 'VISION_MODE' not in cfg
    print('PASS: native image payload/current credentials, OCR never uploads, separate caches, visual-only evidence, UI callback + watch follow-up mode, immutable global settings. No cloud.')


if __name__=='__main__':main()
