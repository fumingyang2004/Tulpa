"""Read-only UI projections. Never summarize drafts or call the model."""
import csv
import io
import re
from collections import Counter
from urllib.parse import urlsplit


def plain(text):
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    return re.sub(r'[*`]', '', text).strip()


def link_key(url):
    parts = urlsplit(url)
    return parts.path + '?' + parts.query


def sections(output):
    """Extract existing authored paragraphs, not new model interpretations."""
    refs = {link_key(r.get('url', '')): r for r in output['refs'] if r.get('url')}
    result = []
    if output['format'] == 'csv':
        rows = csv.DictReader(io.StringIO(output['body'].lstrip('\ufeff')))
        for row in rows:
            if not row.get('说明'):
                continue
            kind = row.get('类型', '')
            if kind not in ('fact', 'uncertainty'):
                continue
            urls = (row.get('原始证据') or '').split(' | ')
            result.append(dict(text=plain(row['说明']), kind=kind,
                               refs=[refs[link_key(u)] for u in urls if link_key(u) in refs]))
        return result
    for block in re.split(r'\n\s*\n', output['body']):
        block = block.strip()
        if block == '---':
            break  # generated coverage footer is not a finding
        if not block or block.startswith('#') or block.startswith('**用户要求'):
            continue
        if block.startswith('来源：'):
            if result:
                urls = re.findall(r'\]\(([^)]+)\)', block)
                result[-1]['refs'] = [refs[link_key(u)] for u in urls if link_key(u) in refs]
            continue
        uncertain = block.startswith('**待确认／覆盖限制：**')
        supplied=block.startswith('**用户补充（未独立核实）：**')
        text = plain(block.removeprefix('**待确认／覆盖限制：**').removeprefix('**用户补充（未独立核实）：**'))
        result.append(dict(text=text, kind='user_fact' if supplied else 'uncertainty' if uncertain else 'fact', refs=[]))
    return result


def source_counts(refs):
    messages, files, other = set(), set(), set()
    for r in refs:
        if not r.get('available'):
            continue
        kind, sid = r['source_type'], r['source_id']
        if kind in ('message', 'voice', 'media'):
            messages.add(sid.split(':')[0])
        elif kind.startswith('artifact_'):
            files.add(sid.split(':')[0])
        else:
            other.add((kind, sid))
    return dict(messages=len(messages), files=len(files), other=len(other),
                unavailable=sum(not r.get('available') for r in refs),
                changed=sum(bool(r.get('changed')) for r in refs))


def material_view(layer, wid):
    plan = layer.plan(wid)
    result = []
    for r in layer.materials(wid):
        item = dict(r)
        if r['available']:
            try:
                src = layer.collections.resolve(r['source_type'], r['source_id'], plan)
                item.update(title=src.get('title') or '聊天资料', platform=src.get('platform'),
                            conversation=src.get('conversation') or '', sender=src.get('sender') or '')
            except ValueError:
                item = dict(source_type=r['source_type'], source_id=r['source_id'], state=r['state'],
                            available=False, reason='')
        result.append(item)
    return result


def home_view(layer, wid):
    """UI-only endpoint: no journal/cursor writes, no extra Agent context."""
    w = layer.get(wid)
    with layer.store.connect() as db:
        heads = [dict(r) for r in db.execute('''SELECT o.id,o.name,o.head,o.deleted,v.scope_epoch
            FROM workspace_outputs o LEFT JOIN workspace_versions v ON v.output_id=o.id AND v.version=o.head
            WHERE o.workspace_id=? ORDER BY v.created_at DESC,o.id''', (wid,))]
        # Only feed-backed relevant candidates; date backfills (seq=0) aren't new events.
        events = db.execute('''SELECT l.kind,l.source_type,count(DISTINCT l.source_id) n,max(l.created_at) at
            FROM workspace_candidates c JOIN local_changes l ON l.seq=c.seq
            WHERE c.workspace_id=? AND c.scope_epoch=? AND c.seq>? AND c.decision IN ('pending','processed')
            AND NOT EXISTS(SELECT 1 FROM workspace_materials m WHERE m.workspace_id=c.workspace_id
              AND m.state='excluded' AND ((m.source_type=c.source_type AND m.source_id=c.source_id)
              OR (m.source_type='message' AND c.source_type IN ('media','voice') AND m.source_id=c.source_id)
              OR (m.source_type='artifact_source' AND c.source_type='artifact_chunk' AND m.source_id=substr(c.source_id,1,instr(c.source_id,':')-1))))
            GROUP BY l.kind,l.source_type''', (wid, w['scope_epoch'], w['applied_seq'])).fetchall()
        task_rows = db.execute('''SELECT id,question,state,created_at,
            json_extract(record_json,'$.seconds') seconds,json_extract(record_json,'$.requests') requests,
            json_extract(record_json,'$.usage.total_tokens') tokens,
            coalesce(json_extract(record_json,'$.notice'),json_extract(record_json,'$.error'),
                     json_extract(record_json,'$.note'),json_extract(record_json,'$.continuation'),'') result,
            json_array_length(coalesce(json_extract(record_json,'$.drafts_saved'),'[]')) drafts,
            (SELECT count(*) FROM json_each(events_json) WHERE json_extract(value,'$.type')='tool_start') tools
            FROM workspace_tasks WHERE workspace_id=? AND scope_epoch=? ORDER BY created_at DESC LIMIT 50''',
            (wid, w['scope_epoch'])).fetchall()
        pending_proposals = db.execute("SELECT count(*) FROM workspace_proposals WHERE workspace_id=? AND scope_epoch=? AND state IN ('draft','pending')", (wid,w['scope_epoch'])).fetchone()[0]
    outputs, facts, index_facts, questions, supplements = [], [], [], [], []
    for head in heads:
        if head['deleted'] or not head['head'] or head['scope_epoch'] != w['scope_epoch']:
            continue
        try:
            value = layer.output(wid, head['id'])
        except ValueError:
            continue  # range/source can change while this read is in flight
        parts = sections(value)
        authored = value['origin'] == 'user' or not value['refs']
        counts = source_counts(value['refs'])
        facts_in_output = [p for p in parts if p['kind'] == 'fact']
        summary = (facts_in_output[0]['text'] if facts_in_output else value['summary'])
        if len(summary) > 180:
            summary = summary[:180] + '…'
        item = dict(id=value['id'], name=value['name'], format=value['format'], version=value['version'],
                    updated_at=value['created_at'], origin=value['origin'], summary=summary,
                    sources=counts, refs=value['refs'],resource_kind=value['resource_kind'], type='当前状态' if value['resource_kind']=='state' else '笔记' if value['resource_kind']=='note' else '资料索引' if value['format']=='csv' else '调查报告')
        outputs.append(item)
        for p in parts:
            if not p['text']:
                continue
            row = dict(p, output_id=value['id'], output_name=value['name'], version=value['version'],
                       updated_at=value['created_at'])
            usable = all(r.get('available') and not r.get('changed') for r in p['refs'])
            if p['kind']=='user_fact':
                supplements.append(dict(row,stale=not usable))
            elif p['kind'] == 'uncertainty' and value['origin'] != 'user':
                questions.append(dict(row, stale=not usable))
            elif not authored and p['refs'] and usable:
                (facts if value['format']=='md' else index_facts).append(row)
    materials = material_view(layer, wid)
    confirmed = sum(r['state']=='accepted' and r['available'] for r in materials)
    tasks = [dict(t,seconds=t['seconds'] or 0,requests=t['requests'] or 0,tokens=t['tokens'] or 0) for t in task_rows]
    change_items = []
    for e in events:
        kind, typ = e['kind'],e['source_type']
        label = ('新增聊天' if kind=='added' and typ=='message' else
                 '新增文件来源' if kind=='added' and typ=='artifact_source' else
                 '来源删除' if kind in ('deleted','delete','voice_delete') else
                 '语音转写或状态变化' if typ=='voice' else
                 '文件解析变化' if kind in ('parse_changed','chunks_changed') else
                 '消息或媒体更新' if typ=='message' else '资料更新')
        change_items.append(dict(label=label, count=e['n'], at=e['at'], kind=kind, source_type=typ))
    # Reject a mixed snapshot if scope changed during source resolution.
    if layer.get(wid)['scope_epoch'] != w['scope_epoch']:
        raise ValueError('工作区范围刚刚改变，请刷新页面。')
    states={o['id'] for o in outputs if o['resource_kind']=='state'}
    facts = [r for r in facts if r['output_id'] in states] if states else facts or index_facts
    if states:
        questions=[r for r in questions if r['output_id'] in states];supplements=[r for r in supplements if r['output_id'] in states]
    return dict(outputs=outputs, understanding=facts[:8], understanding_total=len(facts),
                user_supplements=supplements,current_state=next((o for o in outputs if o['resource_kind']=='state'),None),
                questions=questions[:12], question_total=len(questions),
                confirmed_materials=confirmed, material_states=dict(Counter(r['state'] for r in materials)),
                materials=[r for r in materials if r['state']!='excluded'][:4],
                tasks=tasks, proposals=pending_proposals,
                recent=dict(items=change_items, since_seq=w['applied_seq'], gap=bool(w['gap'])),
                scope_epoch=w['scope_epoch'])
