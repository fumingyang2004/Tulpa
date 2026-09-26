import html
import json

from .llm import INSUFFICIENT
from .normalize import display_time,safe_reply
from .media import public_media

PLATFORMS = {'qq':'QQ', 'wechat':'微信'}


def esc(value):
    return html.escape(str(value))


def label(message):
    return f"[{PLATFORMS[message['platform']]} · {message['conversation']} · {display_time(message['timestamp'])}]"


def message_html(message):
    identity = '本人' if message['is_self'] == 1 else '他人' if message['is_self'] == 0 else '身份未标定'
    media=[]
    for item in public_media(message['id'],message.get('media',[])):
        if item['available']:
            caption=('缩略图' if item.get('thumbnail') else '动态表情 / GIF' if item.get('animated') else '表情包' if item['kind']=='sticker' else '图片')
            media.append('<figure class="evidence-media"><a class="open-image" href="'+esc(item['url'])+'" target="_blank" rel="noopener"><img loading="lazy" src="'+esc(item['url'])+'" alt="'+caption+'" style="max-width:260px;max-height:240px;object-fit:contain"></a><figcaption>'+caption+'</figcaption></figure>')
        else:media.append('<p class="media-unavailable">媒体不可用 · 本机未缓存或暂无法解密</p>')
    reply=safe_reply(message.get('reply_to'))
    quote='<blockquote class="reply-quote">'+esc((reply.get('sender','')+'：' if reply.get('sender') else '')+reply.get('content',''))+'</blockquote>' if reply else ''
    voice=message.get('voice')
    if voice:
        media.append('<div class="voice-evidence"><small>[语音转写] 本地机器识别，可能有误听</small><p>'+esc(voice.get('transcript') or '尚未取得转写；打开上下文可试听或按需转写。')+'</p></div>')
    return (f'<div class="source-message" style="margin:10px 0"><b>M{message["id"]} {esc(label(message))}</b>'
            f'<div>{esc(message["sender"])} · {identity}</div>'
            f'{quote}<pre style="white-space:pre-wrap;overflow-wrap:anywhere;font:inherit">{esc(message["content"])}</pre>'+''.join(media)+
            f'<a class="open-chat" data-message-id="{message["id"]}" href="/?anchor={message["id"]}">查看上下文</a> '
            f'<button type="button" class="collect-evidence source-button" data-source-type="message" data-source-id="{message["id"]}">保存证据</button> '
            f'<button type="button" class="watch-evidence source-button" data-message-id="{message["id"]}">关注这条线索</button></div>')


def scope_text(bundle):
    p = bundle['plan']
    start = display_time(p['start']) if p['start'] else '不限'
    end = display_time(p['end']) if p['end'] else '不限'
    mode = {'search':'关键词检索', 'tasks':'请求/任务候选', 'promises':'本人承诺候选', 'summary':'时间范围抽样'}[p['mode']]
    text = (f'{mode}｜北京时间 {start} → {end}（结束时刻不含）｜'
            f'范围内 {bundle["scope_count"]} 条，匹配 {bundle["match_count"]} 条，'
            f'选取 {len(bundle["seed_ids"])} 条命中，含前后文共 {len(bundle["messages"])} 条，'
            f'上下文约 {bundle["context_chars"]} 字符。')
    if p['keywords']:
        text += ' 关键词：' + '、'.join(p['keywords']) + '。'
    if bundle['incomplete']:
        text += ' 已达到抽样/长度限制，结论只覆盖展示的相关片段，不是完整审计。'
    if bundle['unknown_self']:
        text += f' 范围内 {bundle["unknown_self"]} 条发送者是否本人未知。'
    return text


def identity_html(store):
    from .identity import group_identities,IDENTITY_NOTE
    groups=group_identities(store);known=[g for g in groups if g['identities']]
    parts=[f'<details class="identity-catalog"><summary>我的群内身份 · {len(known)} 个群已识别</summary>',
           '<p>'+esc(IDENTITY_NOTE)+'</p><div class="identity-list">']
    for group in known:
        ids=list(dict.fromkeys(i['sender_id'] or '未保留账号ID' for i in group['identities']))
        names=list(dict.fromkeys(i['name'] for i in group['identities']))
        latest=max(i['last_seen'] for i in group['identities'])
        parts.append('<div class="identity-row"><b>'+esc(PLATFORMS[group['platform']]+' · '+group['conversation'])+
            '</b><div>本人账号 ID：'+esc(' / '.join(ids))+'</div><div>已知群内显示名：'+esc(' / '.join(names))+
            '</div><small>最近的本人消息：'+esc(display_time(latest))+'</small></div>')
    parts.append('</div>')
    unknown=[g for g in groups if not g['identities']]
    if unknown:
        parts.append(f'<details><summary>另 {len(unknown)} 个群暂无可用的本人发言身份记录</summary><ul>')
        parts.extend('<li>'+esc(PLATFORMS[g['platform']]+' · '+g['conversation'])+'</li>' for g in unknown)
        parts.append('</ul></details>')
    return ''.join(parts)+'</details>'


REJECTION_REASONS = {
    'invalid_analysis_citation':'SQL统计、QQ资料或保存回执不是本轮实际取得的来源。',
    'untranscribed_voice':'引用了尚未转写的语音，不能根据占位符判断其内容。',
    'invalid_file_citation':'文件引用不是本轮实际读取的来源或原文片段。',
    'unread_context':'部分引用消息的前后文未完整交给模型，未达到本轮上下文检查要求。',
    'uninspected_image':'把尚未成功识别的图片用作了内容依据；目前无法核实图中内容。',
    'invalid_text_or_citation':'结论格式或引用不符合要求，例如引用了本轮未读取的消息、缺少引用或文字过长。',
    'invalid_attachment':'附件引用不符合要求，例如不属于本轮消息、没有媒体或不在依据所在会话。',
    'invalid_claim':'模型没有按约定格式提供这项结论。',
    'claim_limit':'超过了本轮可采用的结论数量上限。',
    'historical_rejection':'旧版本只记录了省略结果，没有保存具体检查原因。',
}


def display_answer(text,result):
    """Display is independent of evidence admission and downstream memory."""
    value=text.strip()
    if value.startswith('```'):
        value=value.removeprefix('```json').removeprefix('```').removesuffix('```').strip()
    try:
        raw=json.loads(value)
        if not isinstance(raw,dict) or not isinstance(raw.get('claims'),list):raise ValueError()
    except (ValueError,TypeError):
        return dict(display_claims=[dict(text=text,evidence_ids=[],_unverified_reason='invalid_claim')]) if text.strip() else {}
    rejected={d['claim']:d for d in result.get('rejection_details',[]) if type(d.get('claim')) is int}
    accepted=iter(result.get('claims',[]));shown=[]
    for index,item in enumerate(raw['claims']):
        if index in rejected:
            shown.append(dict(text=item.get('text') if isinstance(item,dict) and isinstance(item.get('text'),str) else json.dumps(item,ensure_ascii=False),
                              evidence_ids=[],_unverified_reason=rejected[index]['reason'],_raw_claim=item))
        else:
            verified=next(accepted,None)
            if verified is not None:shown.append(verified)
            else:shown.append(dict(text=item.get('text','') if isinstance(item,dict) else str(item),evidence_ids=[],_unverified_reason='invalid_claim'))
    return dict(display_claims=shown)


def unverified_html(text,reason):
    return ('<div class="claim unverified-claim"><p class="claim-text" style="white-space:pre-wrap">'+esc(text)+'</p>'
            '<p class="unverified-note">未通过检查 · '+esc(REJECTION_REASONS.get(reason,REJECTION_REASONS['historical_rejection']))+'</p></div>')


def withheld_html(result,messages):
    current=list(result.get('rejection_details') or [])
    # Old records may have only counts. Be explicit rather than fabricating text.
    for _ in range(max(0,result.get('rejected',0)-len(current))):
        current.append(dict(reason='historical_rejection'))
    items=[(d,False) for d in current]
    if not result.get('claims') and not current:
        items += [(d,True) for d in result.get('earlier_rejections',[])]
    if not items:return ''
    parts=['<div class="withheld-claims">']
    for detail,earlier in items:
        parts.append('<section class="unverified-claim">')
        affected=[mid for mid in detail.get('message_ids',[]) if type(mid) is int]
        if 'raw_claim' not in detail:
            parts.append('<p class="answer-note">这条旧记录没有保留原始结论，暂时无法展示正文。</p></section>')
            continue
        raw=detail['raw_claim']
        if isinstance(raw,dict) and isinstance(raw.get('text'),str):
            parts.append('<p class="claim-text" style="white-space:pre-wrap">'+esc(raw['text'])+'</p>')
        else:
            parts.append('<pre class="withheld-text" style="white-space:pre-wrap">'+esc(json.dumps(raw,ensure_ascii=False,indent=2))+'</pre>')
        parts.append('<p class="unverified-note">未通过检查 · '+esc(REJECTION_REASONS.get(detail.get('reason'),REJECTION_REASONS['historical_rejection']))+'</p>')
        if affected:
            parts.append('<p class="answer-note">未通过检查的引用：'+esc('、'.join(f'M{mid}' for mid in affected))+'。</p>')
        refs=[]
        if isinstance(raw,dict):
            for field in ('evidence_ids','attachment_ids'):
                values=raw.get(field,[])
                if isinstance(values,list):refs.extend(mid for mid in values if type(mid) is int)
        refs=list(dict.fromkeys(refs))
        known=[mid for mid in refs if mid in messages]
        unknown=[mid for mid in refs if mid not in messages]
        if unknown:
            parts.append('<p class="answer-note">这些引用未在本轮读取，无法作为原文定位：'+esc('、'.join(f'M{mid}' for mid in unknown))+'。</p>')
        if known:
            parts.append('<details class="withheld-sources"><summary>查看相关消息与上下文（供核对）</summary>'
                         '<p class="answer-note">这些是模型引用的消息，不代表它们已经证明上述结论。</p>')
            parts.extend(message_html(messages[mid]) for mid in known)
            parts.append('</details>')
        parts.append('<details class="withheld-raw"><summary>查看原始输出及引用</summary>'
                     '<pre style="white-space:pre-wrap;overflow-wrap:anywhere">'+esc(json.dumps(raw,ensure_ascii=False,indent=2))+'</pre></details></section>')
    return ''.join(parts)+'</div>'


def analysis_html(item):
    kind=item.get('kind')
    if kind=='qq_live':
        operation=item.get('operation')
        if operation:
            return '<p class="answer-note">'+esc(operation['summary']+' · 本次回执：'+operation['status'])+'。最新执行结果见输入框上方的操作记录。</p>'
        return '<details class="citation-group"><summary>QQ 实时资料查询回执</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere">'+esc(json.dumps(item,ensure_ascii=False,indent=2))+'</pre></details>'
    if kind=='sql':
        parts=['<details class="citation-group"><summary>SQL 统计依据 · '+esc(item['citation_id'])+'</summary>',
            '<p class="answer-note">计算结果仅覆盖所选范围内已导入记录；可检查下方查询口径，不等于原文引用。</p>',
            '<pre style="white-space:pre-wrap;overflow-wrap:anywhere">'+esc(item['sql'])+'</pre>',
            '<div style="overflow:auto"><table><thead><tr>'+''.join('<th>'+esc(c)+'</th>' for c in item['columns'])+'</tr></thead><tbody>']
        for row in item['rows']:parts.append('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in row)+'</tr>')
        parts.append('</tbody></table></div><p>'+esc(f"返回 {item['returned_rows']} 行 · {'结果已截断' if item['truncated'] else '本查询结果未截断'} · {item.get('total_time',0)} 秒")+'</p>')
        parts.append('<details><summary>本次统计范围</summary><pre>'+esc(json.dumps(item.get('scope',{}),ensure_ascii=False,indent=2))+'</pre></details></details>')
        return ''.join(parts)
    if kind=='collection':
        return '<p><a class="source-button open-collection" href="/?collection='+esc(item['id'])+'" data-collection-id="'+esc(item['id'])+'">查看证据集合'+esc(f' · 本次新增 {item["added"]} 项' if 'added' in item else '')+'</a></p>'
    if kind in ('notice','essence'):
        label='QQ 群公告' if kind=='notice' else 'QQ 精华'
        result='<details class="citation-group"><summary>'+label+' · '+esc(item['conversation'])+'</summary><p>'+esc(item['publisher']+' · '+item['time'])+'</p><pre style="white-space:pre-wrap">'+esc(item['content'])+'</pre>'
        result+='<p class="answer-note">'+esc(item['provenance']+' · 原始编号 '+item['native_id'])+'</p>'
        result+='<a class="source-button open-knowledge" href="/?knowledge='+str(item['id'])+'" data-knowledge-id="'+str(item['id'])+'">查看来源资料</a>'
        if item.get('canonical_message_id'):result+=f' <a class="open-chat" data-message-id="{item["canonical_message_id"]}" href="/?anchor={item["canonical_message_id"]}">原聊天上下文</a>'
        return result+'</details>'
    return ''


def answer_html(result, bundle, *, compact=False):
    messages = {m['id']:m for m in bundle['messages']}
    parts = ['<div class="chat-answer">']
    displayed=result.get('display_claims')
    has_unverified=bool(displayed or result.get('rejection_details') or result.get('earlier_rejections'))
    if result.get('error') and not has_unverified:
        parts.append('<p class="answer-note">'+esc(result['error'])+'</p>')
        if messages:
            parts.append(f'<p>已检索到 {len(messages)} 条消息，但本轮回答未完成。可以查看本轮来源，或重试。</p>')
    elif result.get('rejected') and not result.get('claims') and not has_unverified:
        parts.append('<p class="answer-note">已找到消息，但这轮回答未通过证据检查；这不表示没有相关记录。原文和查询过程已保留，可重新提问。</p>')
    elif result['insufficient'] and not has_unverified:
        parts.append(f'<p>{INSUFFICIENT}</p>')
    for claim in displayed if displayed is not None else result['claims']:
        if claim.get('_unverified_reason'):
            parts.append(unverified_html(claim['text'],claim['_unverified_reason']))
            continue
        parts.append('<div class="claim"><p class="claim-text" style="white-space:pre-wrap">'+esc(claim['text'])+'</p>')
        for key in claim.get('analysis_evidence_ids',[]):
            item=bundle.get('analysis_evidence',{}).get(key)
            if item:parts.append(analysis_html(item))
        file_ids=claim.get('artifact_evidence_ids',[])
        if file_ids:
            parts.append('<details class="citation-group"><summary>'+str(len(file_ids))+' 处文件证据</summary>')
            for fid in file_ids:
                item=bundle.get('file_evidence',{}).get(fid)
                if not item:continue
                parts.append('<div class="citation-source"><b>'+esc(item['filename'])+'</b><p>'+esc(
                    PLATFORMS[item['platform']]+' · '+item['conversation']+' · '+item['sender']+' · '+item['time'])+'</p>')
                if item.get('locator'):parts.append('<p>'+esc(json.dumps(item['locator'],ensure_ascii=False))+'</p>')
                if item.get('text'):parts.append('<pre style="white-space:pre-wrap;overflow-wrap:anywhere">'+esc(item['text'])+'</pre>')
                if item.get('parse_note'):parts.append('<p class="answer-note">'+esc(item['parse_note'])+'</p>')
                parts.append(f'<button class="source-button open-artifact" data-source-id="{item["id"]}">查看文件与来源</button>')
                if item.get('message_id'):parts.append(f' <a class="open-chat" data-message-id="{item["message_id"]}" href="/?anchor={item["message_id"]}">查看聊天上下文</a>')
                parts.append('</div>')
            parts.append('</details>')
        if compact and claim['evidence_ids']:
            platforms = list(dict.fromkeys(PLATFORMS[messages[mid]['platform']] for mid in claim['evidence_ids']))
            parts.append('<details class="citation-group"><summary>'+esc(
                f'{len(claim["evidence_ids"])} 条原文 · '+ ' / '.join(platforms))+'</summary>')
        for mid in claim['evidence_ids']:
            source = messages[mid]
            nearby = next((rows for rows in bundle.get('contexts',{}).values()
                           if any(m['id']==mid for m in rows)), [])
            # The locally stored context can contain the full original even when
            # the text sent to the provider was truncated.
            original = next((m for m in nearby if m['id']==mid), source)
            parts.append('<div class="citation-source">' if compact else
                         '<details class="citation-source"><summary>'+esc(f'M{mid} {label(source)}')+'</summary>')
            parts.append(message_html(original))
            if nearby:
                parts.append('<details><summary>查看附近聊天（同一会话、所选时间范围内）</summary>')
                parts.extend(message_html(m) for m in nearby)
                parts.append('</details>')
            if source.get('truncated'):
                parts.append('<p>发送给模型的原文仅含前 2000 字；下方证据区保留完整原文。</p>')
            parts.append('</div>' if compact else '</details>')
        if compact and claim['evidence_ids']:
            parts.append('</details>')
        if claim.get('attachment_ids'):
            parts.append('<details class="citation-group attachment-records"><summary>相关附件 · 仅展示发送记录</summary>'
                         '<p>这里展示原始附件，不代表模型已理解图中内容。可打开原图或查看上下文。</p>')
            parts.extend(message_html(messages[mid]) for mid in claim['attachment_ids'])
            parts.append('</details>')
        parts.append('</div>')
    cited_analysis={key for claim in result['claims'] for key in claim.get('analysis_evidence_ids',[])}
    saved={}
    for key,item in bundle.get('analysis_evidence',{}).items():
        if item.get('kind')=='collection' and key not in cited_analysis:saved[item['id']]=item
    parts.extend(analysis_html(item) for item in saved.values())
    if displayed is None:parts.append(withheld_html(result,messages))
    if bundle.get('mention_search'):
        m=bundle['mention_search']
        parts.append('<p class="answer-note">'+esc(f'在本次消息范围内按本人同群昵称/账号文本找到 {m["self_candidates"]} 条候选；'
            f'另有 {m["all_mentions"]} 条全体通知，{m["unresolved"]} 条含 @ 的文本未匹配。'
            '导出未保留原生 @ 目标账号ID，候选及未匹配结果都需核对，不能据此断言无人 @ 本人。')+'</p>')
    if bundle.get('overview') and not result.get('error'):
        parts.append(f'<p class="answer-note">本轮从所选范围的 {bundle["scope_count"]} 条记录中查阅了 {len(messages)} 条，属于有限概览；没有读取客户端已读状态。</p>')
    if result.get('api_called') and not compact:
        parts.append('<p>模型：'+esc(result.get('model',''))+'。引用原文由程序读取；结论与证据的语义一致性仍需核对。</p>')
    return ''.join(parts)+'</div>'


def evidence_html(store, bundle):
    output = ['<div>']
    shown={m['id']:m for m in bundle['messages']}
    for mid in bundle['seed_ids']:
        context = bundle['contexts'].get(mid, bundle['contexts'].get(str(mid), []))
        context=[dict(m,voice=shown[m['id']]['voice']) if shown.get(m['id'],{}).get('voice') else m for m in context]
        selected = next((m for m in context if m['id']==mid), None)
        if selected is None:
            continue
        output.append('<details><summary>'+esc(f'M{mid} {label(selected)}')+'</summary>')
        output.append(message_html(selected))
        output.append('<details><summary>展开前后各 3 条消息（限所选时间范围）</summary>')
        output.extend(message_html(m) for m in context)
        output.append('</details><small>')
        output.append('原始 ID：'+esc(selected['source_id'] or '导出未提供')+'<br>')
        for source in store.provenance(mid):
            output.append(esc(f"{source['filename']} {source['pointer']} | SHA256 {source['hash']} | {source['archive']}")+'<br>')
        output.append('</small></details><hr>')
    return ''.join(output)+'</div>'
