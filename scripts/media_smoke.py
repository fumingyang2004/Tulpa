"""Key media paths on isolated synthetic records. No real chat or cloud requests."""
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from PIL import Image
from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from chatlocal.store import Store
from chatlocal.media import cache_image,hydrate
from chatlocal.normalize import safe_reply
from chatlocal.llm import validate_answer
from chatlocal.agent_tools import ChatTools
from chatlocal.retrieval import make_plan
from chatlocal.vision import frames_for,VisionProvider
from chatlocal.render import answer_html
from chatlocal.chat_view import install_chat_routes


def main():
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='media-check-') as folder:
        folder=Path(folder);store=Store(folder/'test.sqlite3')
        encoded=io.BytesIO()
        images=[Image.new('RGB',(24,24),color) for color in ('red','green','blue','yellow')]
        images[0].save(encoded,format='GIF',save_all=True,append_images=images[1:],duration=80,loop=0)
        gif=cache_image(encoded.getvalue(),'sticker')
        row=dict(platform='qq',conversation='合成媒体群',conversation_id='q',sender='甲',timestamp='2026-09-20 12:00',content='',is_self=False,source_id='image',media=[gif])
        rows=[dict(row,content='<script>chat()</script>',source_id='text',media=[]),row,
              dict(row,source_id='missing',media=[{'kind':'image'}]),
              dict(row,platform='wechat',conversation_id='wx',source_id='other'),
              dict(row,source_id='reply',media=[],content='回复',reply_to={'source_id':'text','sender':'甲','content':'原文'})]
        path=folder/'input.json';path.write_text(json.dumps(rows),encoding='utf-8')
        with patch.object(VisionProvider,'inspect',side_effect=AssertionError('Import must not inspect images')):
            assert store.import_file(path)['imported']==5
            assert store.import_file(path)['imported']==0
        plan=make_plan('看看图片',platforms=['qq'])
        tools=ChatTools(store,plan)
        found=tools.execute('search_messages',{'query':'','has_media':True})
        assert set(found['hit_ids'])=={2,3}
        # Media filtering selects hits; adjacent text/replies provide context,
        # while the unselected WeChat platform must stay excluded.
        assert {m['id'] for m in found['messages']}=={1,2,3,5}
        wire=json.dumps(found)
        assert 'local_path' not in wire and 'source_id' not in wire and 'data/media' not in wire
        assert tools.execute('get_media',{'message_id':2})['media'][0]['kind']=='gif'
        assert tools.execute('get_media',{'message_id':4}).get('error')
        assert tools.execute('inspect_image',{'message_id':3}).get('error')
        frames,total=frames_for(ROOT/gif['local_path'])
        assert total==4 and len(frames)==3
        quoted={'content':'<msg><img aeskey="synthetic-secret"/></msg>','source_id':'x','raw':'not-for-api'}
        assert safe_reply(quoted)=={'content':'[引用非文本消息]','source_id':'x'}
        assert safe_reply({'content':'sender:\n&lt;msg&gt;&lt;img aeskey="synthetic"/&gt;&lt;/msg&gt;'})['content']=='[引用非文本消息]'
        assert 'synthetic-secret' not in json.dumps(hydrate({'reply_to':json.dumps(quoted)}))
        claim={'claims':[{'text':'图中有测试文字','evidence_ids':[2]}]}
        evidence=list(tools.messages.values())
        assert not validate_answer(claim,evidence,inspections={})['claims']
        assert validate_answer(claim,evidence,inspections={'2:0':{'message_id':2,'provider':'ocr','ocr':'测试文字'}})['claims']
        sent=[]
        def fake_vision(request):
            sent.append(json.loads(request.content))
            return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'type':'screenshot','ocr':'测试文字','description':'合成图','notes':'fixture'})}}]})
        actual_client=httpx.Client
        config=dict(VISION_PROVIDER='openai',VISION_API_BASE='http://127.0.0.1:9999/v1',VISION_API_KEY='fixture-key',VISION_MODEL='fixture-vision')
        with patch('chatlocal.vision.httpx.Client',side_effect=lambda **kw:actual_client(transport=httpx.MockTransport(fake_vision),**kw)):
            result=VisionProvider(config)._cloud(frames[:1],'测试问题',[{'content':'附近合成消息'}])
        assert result['image_uploaded'] and result['ocr']=='测试文字'
        user=sent[0]['messages'][1]['content']
        assert sum(item['type']=='image_url' for item in user)==1
        assert '附近合成消息' in user[0]['text'] and 'local_path' not in json.dumps(sent)
        assert not VisionProvider(dict(config,VISION_API_KEY=''))._cloud(frames[:1],'',()).get('image_uploaded')
        bundle=tools.bundle()
        rendered=answer_html({'claims':[{'text':'合成图片','evidence_ids':[2]}],'insufficient':False},bundle)
        assert '/media/2/0' in rendered and '查看上下文' in rendered
        assert '<script>' not in rendered
        app=FastAPI()
        with patch('chatlocal.chat_view.Store',return_value=store):install_chat_routes(app)
        client=TestClient(app)
        page=client.get('/api/chat-context',params={'anchor_message_id':2,'platform':'qq','conversation_id':'q'}).json()
        assert page['anchor_message_id']==2 and len(page['messages'])==4
        assert any(m['id']==3 and not m['media'][0]['available'] for m in page['messages'])
        assert page['messages'][-1]['reply_to']['message_id']==1
        assert client.get('/api/chat-context',params={'anchor_message_id':2,'platform':'wechat'}).status_code==404
        assert client.get('/api/chat-context',params={'anchor_message_id':2},headers={'Origin':'https://example.com'}).status_code==403
        response=client.get('/media/2/0');assert response.status_code==200 and response.headers['content-type']=='image/gif'
        assert client.get('/media/3/0').status_code==404
        assert client.get('/media/2/0',headers={'sec-fetch-site':'cross-site'}).status_code==403
        print('PASS: media import/dedup without vision, real GIF bytes/frames, absent media, scope isolation, safe metadata, escaped evidence, context anchors and local media HTTP boundaries. No cloud API.')


if __name__=='__main__':main()
