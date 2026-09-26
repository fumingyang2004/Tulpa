"""Key path checks with explicitly synthetic data; never touches the personal DB."""
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from chatlocal.config import ROOT
from chatlocal.store import Store
from chatlocal.retrieval import make_plan,retrieve
from chatlocal.normalize import TZ,stamp
from chatlocal.llm import validate_answer,answer
from chatlocal.render import evidence_html,answer_html


def main():
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='smoke-') as folder:
        folder=Path(folder)
        store=Store(folder/'test.sqlite3')
        qce={'chatInfo':{'name':'合成测试 QQ 群','type':'group','peerUid':'g1','selfUid':'me'},'messages':[
            {'id':'q1','timestamp':stamp('2026-09-19 12:00'),'sender':{'uid':'alice','name':'张三'},'content':{'text':'请帮我周一提交星桥项目文档。'}},
            {'id':'q2','timestamp':stamp('2026-09-19 12:01'),'sender':{'uid':'me','name':'测试本人'},'content':{'text':'好的，我来整理星桥项目文档。'}},
            {'id':'q3','timestamp':stamp('2026-09-20 12:00'),'sender':{'uid':'alice','name':'张三'},'content':{'text':'文档已经收到。<script>alert(1)</script>'}}]}
        wx={'chat':'合成测试微信群','username':'g1@chatroom','messages':[
            {'local_id':1,'timestamp':stamp('2026-09-19 13:00')//1000,'sender':'李四','sender_username':'li','type':'文本','content':'星桥项目改到周二开会，请带进度说明。'},
            {'local_id':2,'timestamp':stamp('2026-09-19 13:01')//1000,'sender':'未标定本人','type':'文本','content':'我来准备，但身份没有标定。'},
            {'local_id':3,'timestamp':stamp('2026-09-19 13:02')//1000,'sender':'李四','type':'图片','content':'[图片]'}]}
        for name,data in [('qq.json',qce),('wechat.json',wx)]:
            path=folder/name
            path.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
            result=store.import_file(path)
            assert result['imported'] in (2,3)
            assert store.import_file(path,self_ids=None)['imported']==0
        assert sum(s['messages'] for s in store.stats())==5
        now=datetime(2026,9,21,15,tzinfo=TZ)
        query=retrieve(store,make_plan('之前星桥项目怎么讨论的？',now=now))
        assert {m['platform'] for m in query['messages']}=={'qq','wechat'}
        null_plan=make_plan('之前星桥项目怎么讨论的？',conversations=None,start=None,end=None,keywords=None,now=now)
        assert vars(null_plan)==vars(make_plan('之前星桥项目怎么讨论的？',now=now))
        assert retrieve(store,null_plan)['seed_ids']==query['seed_ids']
        try:
            make_plan(None,keywords=None,now=now)
            raise AssertionError('Empty question accepted')
        except ValueError as error:
            assert '问题不能为空' in str(error)
        request=retrieve(store,make_plan('最近别人让我做什么任务？',now=now))
        assert {m['platform'] for m in request['messages']}=={'qq','wechat'}
        promise=retrieve(store,make_plan('我最近答应别人做过什么？',now=now))
        assert promise['seed_ids']==[2],promise
        limited=retrieve(store,make_plan('总结选定时间范围',start='2026-09-20',end='2026-09-20',now=now))
        assert [m['id'] for m in limited['messages']]==[3]
        selection=json.dumps(['qq','group:g1'])
        selected=retrieve(store,make_plan('星桥',conversations=[selection],now=now))
        assert all(m['platform']=='qq' for m in selected['messages'])
        absent=retrieve(store,make_plan('完全不存在关键词abcdefgh',now=now))
        assert not absent['messages']
        assert answer('absent',absent)['api_called'] is False
        validated=validate_answer({'claims':[{'text':'valid','evidence_ids':[1]},{'text':'invalid','evidence_ids':[999]}]},query['messages'])
        assert len(validated['claims'])==1 and validated['rejected']==1
        assert '查看附近聊天' in answer_html(validated,query)
        rendered=evidence_html(store,query)
        assert '<script>' not in rendered and '&lt;script&gt;' in rendered
        assert 'SHA256' in rendered and '/messages/0' in rendered
        malformed=folder/'bad.json'
        malformed.write_text(json.dumps([{'platform':'qq','conversation':'x','sender':'y','timestamp':'invalid','content':'x'}]),encoding='utf-8')
        try:
            store.import_file(malformed)
            raise AssertionError('invalid input accepted')
        except ValueError:
            pass
        assert sum(s['messages'] for s in store.stats())==5
        assert query['context_chars']<=24000 and len(query['messages'])<=100
        print('PASS: 关键路径（合成数据）：多格式导入、幂等、中文跨平台检索、请求/本人承诺、时间/会话隔离、上下文、无命中不调用 API、伪造引用拒绝、HTML 转义、原文定位、导入错误回滚。')


if __name__=='__main__':
    main()
