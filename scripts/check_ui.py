"""Regression check through the running Gradio queue, including browser nulls.

Run with --with-api to additionally check one real cloud answer.
"""
import argparse
import json
import sys
import uuid
from pathlib import Path

import httpx

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from chatlocal.config import ROOT


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--with-api',action='store_true')
    parser.add_argument('--with-refresh',action='store_true',help='实际读取本机 QQ/微信并导入')
    args=parser.parse_args()
    with httpx.Client(base_url='http://127.0.0.1:7860/legacy',trust_env=False,timeout=120) as client:
        config=client.get('/config').json()
        ids={d['api_name']:d['id'] for d in config['dependencies']}
        def call(name,data):
            session=uuid.uuid4().hex
            joined=client.post('/gradio_api/queue/join',json={
                'fn_index':ids[name],'data':data,'session_hash':session,
                'event_data':None,'trigger_id':None})
            joined.raise_for_status()
            with client.stream('GET','/gradio_api/queue/data',params={'session_hash':session}) as stream:
                for line in stream.iter_lines():
                    if not line.startswith('data: '):
                        continue
                    event=json.loads(line[6:])
                    if event.get('msg')=='process_completed':
                        assert event.get('success'),f'{name}: callback failed'
                        return event['output']['data']
            raise AssertionError('No completion event')

        # Exactly the reported question; all untouched optional fields are null.
        request=['中秋节放假安排',['qq','wechat'],None,None,None,None]
        result=call('search_action',request)
        assert '未调用 API' in result[0] and 'SHA256' in result[2]
        assert '中秋' in result[2] and '<details' in result[2]
        for question in (None,''):
            result=call('ask_action',[question,['qq','wechat'],None,None,None,None])
            assert '问题不能为空' in result[0]
        result=call('import_local',[None,'自动识别',None,None])
        assert '请填写导入文件或目录' in result[0]
        checks=['null filters local search','empty question validation','empty import path validation']
        state=call('refresh',[])
        assert '已导入消息时间' in state[0]
        if args.with_refresh:
            result=call('read_latest',[])
            assert 'QQ 更新完成' in result[0] and '微信 更新完成' in result[0],result[0]
            assert '已导入消息时间' in result[1]
            folder=ROOT/'reports/private'
            folder.mkdir(parents=True,exist_ok=True)
            (folder/'ui-read-latest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            checks.append('live QQ/WeChat refresh and import')
            print(result[0],flush=True)
        if args.with_api:
            result=call('ask_action',request)
            assert '模型：deepseek-flash' in result[0]
            assert '<details' in result[0] and 'SHA256' in result[2]
            folder=ROOT/'reports/private'
            folder.mkdir(parents=True,exist_ok=True)
            (folder/'ui-null-fields-answer.html').write_text(
                '<meta charset="utf-8"><h1>中秋节放假安排（可选字段全部留空）</h1>'+''.join(result),encoding='utf-8')
            checks.append('null filters real DeepSeek answer with evidence')
        print(json.dumps({'passed':checks,'cloud_called':args.with_api},ensure_ascii=False))


if __name__=='__main__':
    main()
