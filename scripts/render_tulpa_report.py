"""Render a local, redacted replay comparison without any model calls."""
import argparse
import html
import json
import re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def render(label):
    if not re.fullmatch(r'tulpa(?:-[a-z0-9]+)?',label):raise ValueError('Invalid report label')
    folder=ROOT/'reports/private'/label
    report=json.loads((folder/'report.json').read_text(encoding='utf-8'))
    source=json.loads((folder/'source.json').read_text(encoding='utf-8'))
    memories={m['id']:m for m in json.loads((folder/'memories.json').read_text(encoding='utf-8'))}
    aliases={};counts={'group':0,'direct':0}
    for scope in report['scopes']:
        kind=scope['kind'];counts[kind]+=1
        aliases[scope['name']]=('群聊' if kind=='group' else '联系人')+' '+str(counts[kind])
        aliases[scope['conversation_id']]='[会话账号]'
    for row in source['rows']:
        name=row['sender']
        if name and len(name)>=2 and name not in aliases:
            aliases[name]='本人' if row['is_self']==1 else '参与者'
        if row.get('sender_id'):aliases[row['sender_id']]='[账号]'
    replacements=sorted(((key,value) for key,value in aliases.items() if len(key)>=2),key=lambda p:len(p[0]),reverse=True)
    def escaped(text):
        text=str(text)
        for key,value in replacements:text=text.replace(key,value)
        text=re.sub(r'https?://\S+','[链接]',text)
        text=re.sub(r'(?<!\d)\d{5,}(?!\d)','[号码]',text)
        text=re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}','[邮箱]',text)
        return html.escape(text)
    parts=['''<!doctype html><meta charset="utf-8"><title>Tulpa · 回放对照</title><style>
body{font:16px/1.7 "Segoe UI","Microsoft YaHei",sans-serif;background:#f4f9fe;color:#24405d;max-width:1150px;margin:40px auto;padding:0 24px}h1{font-size:32px}article{background:white;padding:24px;margin:20px 0;border:1px solid #dce8f3;border-radius:18px}h3,h4{margin-top:0}.comparison{display:grid;grid-template-columns:repeat(3,1fr);gap:24px;margin-top:20px}.comparison>div{background:#f5f9fc;border-radius:12px;padding:16px}p{white-space:pre-wrap;overflow-wrap:anywhere}small{color:#668298}summary{cursor:pointer}aside{padding:18px;background:#fff2df;border-radius:12px}@media(max-width:750px){.comparison{grid-template-columns:1fr}}</style>
<h1>Tulpa · 真实历史回复回放</h1><p>仅本机审阅 · 未发送消息 · 回放未写入正式记忆</p>
<p>会话名、已知发送者、账号与链接已替换。文字语义仍可能包含私人信息，这份报告不进入公开仓库。</p>
<aside>功能链路已验证；人格质量仍需审阅。ON 并不总是更接近本人：仍可见过度接梗、擅自断言和答非所问。以下保留全部结果，没有只挑好例子。</aside><h2>成长与自动记忆</h2>''']
    for scope in report['scopes']:
        memory=memories[scope['id']]
        parts.append(f'<article><h3>{escaped(scope["name"])}</h3><small>有效计数 {scope["total"]} · 已整理 {scope["processed"]} · 案例 {scope["episodes"]}</small><p>{escaped(memory["automatic"] or "未达到门槛，不生成画像。")}</p></article>')
    parts.append(f'<h2>{len(report["cases"])} 个 ON / OFF 对照</h2><p>共 {report["messages"]:,} 条真实历史，按时间顺序回放。真实回复在生成完成前隐藏；既往迭代不混入本次结果。</p>')
    for case in report['cases']:
        columns=''.join(f'<div><h4>{title}</h4><p>{escaped(text)}</p></div>' for title,text in [
            ('本人当时的实际回复',case['actual']),('Tulpa OFF',case['off']['reply']),('Tulpa ON',case['on']['reply'])])
        usage=case['on']['tulpa']
        parts.append(f'<article><h3>案例 {case["case"]} · {escaped(case["conversation"])}</h3><details><summary>对方原消息</summary><p>{escaped(case["target"])}</p></details><div class="comparison">{columns}</div><small>ON 读取 {usage["episodes"]} 个案例 · 自动记忆 {"有" if usage["memory_used"] else "无"}</small></article>')
    result=folder/'index.html';result.write_text(''.join(parts),encoding='utf-8');return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--label',default='tulpa-v1')
    print(render(parser.parse_args().label))
