"""Server-rendered Watch dialogue: local citations, escaped text, existing renderer."""
from .render import esc,message_html,answer_html
from .agent_tools import LABELS


def claims_html(store,claims):
    parts=[]
    for claim in claims:
        parts.append('<div class="claim"><p class="claim-text">'+esc(claim['text'])+'</p>')
        ids=list(dict.fromkeys(claim.get('evidence_ids',[])+claim.get('attachment_ids',[])))
        if ids:
            parts.append(f'<details class="watch-citations"><summary>{len(ids)} 条原文 · 查看上下文</summary>')
            for mid in ids:
                message=store.message(mid)
                if message:parts.append(message_html(message))
                else:parts.append('<p class="answer-note">原始消息 M'+str(mid)+' 已删除或不可用。</p>')
            parts.append('</details>')
        parts.append('</div>')
    return ''.join(parts)


def entry_view(store,row):
    record=row['record'];result=row['result'];kind=row['kind']
    content=''
    if kind in ('setup','settings'):
        content='<p>'+esc(result.get('note',''))+'</p>'
    elif (kind=='question' or result.get('mode')=='scheduled_question') and record.get('bundle'):
        bundle=record['bundle']
        if 'contexts' in bundle:bundle['contexts']={int(k):v for k,v in bundle['contexts'].items()}
        content=answer_html(record.get('result',{}),bundle,compact=True)
    elif row['status'] in ('completed','partial') or result.get('checked',0)>0:
        # Older records predate timeline metadata; keep their verified content.
        output=result or record.get('result',{}).get('watch',{})
        initial=output.get('initial',False)
        claims=output.get('current_state',[]) if initial else output.get('changes',[])
        if not claims and not result:claims=output.get('current_state',[])
        if initial:content='<p class="watch-event-intro">已整理当前已知情况。</p>'
        content+=claims_html(store,claims)
        uncertain=output.get('new_uncertainties',output.get('uncertainties',[]))
        if uncertain:content+='<p>以下信息仍需确认：</p>'+claims_html(store,uncertain)
        if not claims and not uncertain:
            content+=('<p>在本次已核对的本地记录中，没有发现新的相关进展。</p>' if row['status']=='completed' or output.get('checked') else '<p>本轮尚未形成通过检查的状态更新，不能判断是否有新进展。</p>')
        if output.get('note'):content+='<p class="answer-note">'+esc(output['note'])+'</p>'
        if not initial and output.get('current_state'):
            content+='<details class="watch-baseline"><summary>检查后的完整已知状态</summary>'+claims_html(store,output['current_state'])+'</details>'
    if row['status']=='running':content='<p class="pending-answer">正在查找和核对消息…</p>'
    elif result.get('error'):content+='<p class="answer-note">'+esc(result['error'])+'</p>'
    elif row['status'] not in ('completed','running') and not content:
        content='<p class="answer-note">这一轮未完成，原有结论与检查进度保留。</p>'
    if result.get('refresh_notice'):content='<p class="watch-refresh-note">'+esc(result['refresh_notice'])+'</p>'+content
    if 'budget_used' in result:
        used=result['budget_used']
        content+='<p class="answer-note">'+esc(f'本轮分析用时 {used.get("seconds",0)} 秒 · {used.get("max_requests",0)} 次模型请求 · {used.get("max_tool_calls",0)} 次工具调用')+'</p>'
    lines=[]
    for event in record.get('events',[]):
        if isinstance(event,str):lines.append(event)
        elif event.get('type')=='tool_start':lines.append(LABELS.get(event['name'],event['name']))
        elif event.get('type')=='model_start':lines.append(f"请求模型 {event['request']}")
        elif event.get('text'):lines.append(event['text'])
    if lines:content='<details class="turn-activity"><summary>查看查询过程</summary><pre>'+esc('\n'.join(lines))+'</pre></details>'+content
    return {k:row[k] for k in ('id','created_at','status','kind','question','revision','trigger')}|dict(html=content)
