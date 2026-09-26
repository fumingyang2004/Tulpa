import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import jieba

from .normalize import TZ, stamp, display_time

STOP = set('之前 关于 怎么 怎样 什么 哪些 有没有 最近 过去 一周 一下 事情 讨论 记录 聊天 帮我 查找 找到 请问 我 你 他 她 的 了 是 在 和 与 里 中 过 吗 呢 地 得 做 提到 曾经 一件 一个 时候 我们 他们 内容 微信 qq wechat'.split())
TASK_TERMS = ['麻烦', '帮忙', '请', '记得', '需要', '能否', '能不能', '可以帮', '帮我', '截止', '提交', '任务', '尽快', '安排', '发我', '给我', '要你', '辛苦', '有空', '打电话', '看一下', '带我', '过来']
PROMISE_TERMS = ['我来', '我会', '我负责', '我去', '答应', '保证', '没问题', '好的', '可以', '收到', '明天', '发你', '给你', '转你']


@dataclass
class Plan:
    mode: str = 'search'
    keywords: list = field(default_factory=list)
    start: int | None = None
    end: int | None = None
    platforms: list = field(default_factory=lambda: ['qq', 'wechat'])
    conversations: list = field(default_factory=list)
    snapshot_max_id: int | None = None


def date_bound(value, end=False):
    if not str(value or '').strip():
        return None
    value = str(value).strip()
    result = stamp(value)
    if end and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        result += 86400000
    return result


def make_plan(question, platforms=None, conversations=None, start='', end='', keywords='', now=None):
    now = now or datetime.now(TZ)
    p = Plan(platforms=platforms or ['qq', 'wechat'], conversations=conversations or [])
    p.start, p.end = date_bound(start), date_bound(end, True)
    # Untouched Gradio textboxes can submit JSON null, not an empty string.
    q = (question or '').strip()
    keywords = (keywords or '').strip()
    if not q or len(q) > 2000:
        raise ValueError('问题不能为空，且不能超过 2000 字。')
    if any(s in q.lower() for s in ('总结', '概括', '错过', 'what did i miss', '值得关注')):
        p.mode = 'summary'
    elif any(s in q for s in ('我答应', '我最近答应', '我承诺', '我负责')):
        p.mode = 'promises'
    elif any(s in q for s in ('任务', '请求', '让我', '要我', '别人提出', '待办')):
        p.mode = 'tasks'
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    dates = re.findall(r'\d{4}-\d{2}-\d{2}', q)
    if p.start is None and dates:
        p.start = date_bound(dates[0])
    if p.end is None and dates:
        p.end = date_bound(dates[-1], True)
    if p.start is None:
        if '今天' in q:
            p.start = int(day.timestamp()*1000)
        elif '昨天' in q:
            p.start = int((day-timedelta(days=1)).timestamp()*1000)
            p.end = p.end or int(day.timestamp()*1000)
        elif '本周' in q:
            p.start = int((day-timedelta(days=day.weekday())).timestamp()*1000)
        elif '上周' in q:
            monday = day-timedelta(days=day.weekday())
            p.start = int((monday-timedelta(days=7)).timestamp()*1000)
            p.end = p.end or int(monday.timestamp()*1000)
        elif match := re.search(r'(?:过去|最近)(\d+)天', q):
            p.start = int((now-timedelta(days=min(int(match[1]),3650))).timestamp()*1000)
        elif (any(s in q.lower() for s in ('一周', '7天', '七天', '错过', 'miss', '过去一星期'))
              or ('最近' in q and not re.search(r'最近(?:的)?\s*(?:\d+|[一二两三四五六七八九十百]+)\s*[条个则]',q))):
            p.start = int((now-timedelta(days=7)).timestamp()*1000)
    if p.end is None:
        p.end = int(now.timestamp()*1000) + 1
    if p.start is not None and p.start >= p.end:
        raise ValueError('开始时间必须早于结束时间。结束日期包含当天，精确结束时间不包含该时刻。')
    if keywords:
        p.keywords = re.findall(r'[\w\u4e00-\u9fff]+', keywords.lower())[:12]
    elif p.mode == 'search':
        quoted = re.findall(r'[“"「](.*?)[”"」]', q)
        p.keywords = quoted or [t.lower() for t in jieba.cut(re.sub(r'\d{4}-\d{2}-\d{2}', '', q))
                               if len(t.strip()) >= 2 and t.lower() not in STOP
                               and re.search(r'[\w\u4e00-\u9fff]', t)]
        p.keywords = list(dict.fromkeys(p.keywords))[:12]
    elif p.mode == 'tasks':
        p.keywords = TASK_TERMS
    elif p.mode == 'promises':
        p.keywords = PROMISE_TERMS
    return p


def scope_sql(plan, alias='m'):
    clauses = [f"{alias}.platform IN ({','.join('?' for _ in plan.platforms)})"]
    values = list(plan.platforms)
    if plan.snapshot_max_id is not None:
        clauses.append(f'{alias}.id<=?');values.append(plan.snapshot_max_id)
    if plan.start is not None:
        clauses.append(f'{alias}.timestamp>=?'); values.append(plan.start)
    if plan.end is not None:
        clauses.append(f'{alias}.timestamp<?'); values.append(plan.end)
    if plan.conversations:
        parts = []
        for selection in plan.conversations:
            platform, conversation_id = json.loads(selection)
            parts.append(f'({alias}.platform=? AND {alias}.conversation_id=?)')
            values += [platform, conversation_id]
        clauses.append('(' + ' OR '.join(parts) + ')')
    return ' AND '.join(clauses), values


def retrieve(store, plan, seed_limit=36, max_messages=100, max_chars=24000):
    where, params = scope_sql(plan)
    hits, match_count = [], 0
    with store.connect() as db:
        scope_count = db.execute(f'SELECT count(*) FROM messages m WHERE {where}', params).fetchone()[0]
        unknown_self = db.execute(f'SELECT count(*) FROM messages m WHERE {where} AND m.is_self IS NULL', params).fetchone()[0]
        match_where, match_args = where, list(params)
        if plan.mode == 'promises':
            match_where += ' AND m.is_self=1'
        elif plan.mode == 'tasks':
            match_where += ' AND (m.is_self=0 OR m.is_self IS NULL)'
        if plan.keywords:
            expression = ' OR '.join('"' + k.replace('"','""') + '"' for k in plan.keywords)
            match_where += ' AND (m.id IN (SELECT rowid FROM message_fts WHERE message_fts MATCH ?) OR ' + ' OR '.join('instr(lower(m.content),?)>0' for _ in plan.keywords) + ')'
            match_args += [expression] + plan.keywords
        elif plan.mode == 'search':
            match_where += ' AND 0'  # Empty keyword queries never disclose a broad sample.
        match_count = db.execute(f'SELECT count(*) FROM messages m WHERE {match_where}', match_args).fetchone()[0]
        # Round-robin over conversation/day, then platform; avoids one busy group monopolizing a summary.
        # Personal-task questions favor direct conversations before public group requests.
        priority = "CASE WHEN conversation_type='direct' THEN 0 ELSE 1 END," if plan.mode in ('tasks','promises') else ''
        hits = [dict(r) for r in db.execute(f'''
          WITH candidates AS (
            SELECT m.*,row_number() OVER(PARTITION BY platform,conversation_id,
              CAST((timestamp/1000+28800)/86400 AS INTEGER) ORDER BY timestamp DESC,id DESC) AS slot
            FROM messages m WHERE {match_where}
          ), balanced AS (
            SELECT *,row_number() OVER(PARTITION BY platform ORDER BY slot,{priority}timestamp DESC,id DESC) AS platform_slot
            FROM candidates
          ) SELECT * FROM balanced ORDER BY platform_slot,platform LIMIT ?''', match_args+[seed_limit])]
    seed_ids = [r['id'] for r in hits]
    messages, contexts, used_chars, clipped = {}, {}, 0, 0
    def add(row):
        nonlocal used_chars, clipped
        if row['id'] in messages:
            return True
        entry = {k: row[k] for k in ('id','platform','conversation_id','conversation','conversation_type','sender','timestamp','content','is_self','source_id')}
        from .media import hydrate
        rich=hydrate(row) if 'media' not in row else row
        entry.update(media=rich.get('media',[]),reply_to=rich.get('reply_to'))
        from .voice import evidence as voice_evidence
        voice=voice_evidence(store,row['id'])
        if voice:
            entry['voice']=voice
            if voice.get('truncated'):entry['truncated']=True
        entry['time'] = display_time(row['timestamp']) + ' +08:00'
        text = entry['content']
        if len(text) > 2000:
            entry['content'] = text[:2000]
            entry['truncated'] = True
        size = len(json.dumps(entry, ensure_ascii=False))
        if len(messages) >= max_messages or used_chars+size > max_chars:
            clipped += 1
            return False
        messages[row['id']] = entry
        used_chars += size
        return True
    for row in hits:
        add(row)
    for row in hits:
        if row['id'] not in messages:
            continue
        nearby = store.context(row['id'], radius=3, start=plan.start, end=plan.end)
        contexts[row['id']] = nearby
        for neighbor in nearby:
            add(neighbor)
    evidence = sorted(messages.values(), key=lambda r:(r['platform'],r['conversation_id'],r['timestamp'],r['id']))
    return dict(plan=vars(plan), scope_count=scope_count, match_count=match_count,
                unknown_self=unknown_self, seed_ids=[i for i in seed_ids if i in messages],
                messages=evidence, contexts=contexts, context_chars=used_chars,
                incomplete=match_count>len(hits) or clipped>0 or any(m.get('truncated') for m in evidence))
