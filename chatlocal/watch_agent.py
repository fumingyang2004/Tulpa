"""Small conversational memory for Watch questions; no separate Agent contract."""
import json
from .agent_sessions import turn_memory


def conversation_memory(store,card):
    """Reuse ordinary chat summaries, never replay all messages or tool logs."""
    with store.connect() as db:
        rows=db.execute("""SELECT question,status,record FROM watch_runs
            WHERE card_id=? AND revision=? AND status='completed'
            AND kind IN ('check','question') ORDER BY id DESC LIMIT 6""",
            (card['id'],card['revision'])).fetchall()
    turns=[]
    for row in rows:
        record=json.loads(row['record'])
        result=record.get('result',{})
        # Read old saved cards without keeping their former analysis protocol.
        if 'watch' in result:
            record=dict(record,result=dict(claims=result['watch'].get('current_state',[])))
        if not record:continue
        item=turn_memory(dict(record=record,question=row['question'] or card['watch_query'],status=row['status']))
        if len(json.dumps(turns+[item],ensure_ascii=False))>6000:break
        turns.insert(0,item)
    turns.insert(0,dict(question='本对话的关注问题：'+card['watch_query'],status='completed',claims=[],
                       query_state=dict(seed_message_ids=card['seed_ids'][:40])))
    return dict(previous_turns=turns)


def scheduled_question(card):
    question=card['watch_query']
    if card['last_checked_at']:
        question+='\n\n请再查一次这个问题。上次成功询问时间：'+card['last_checked_at']+'。优先查找之后与问题相关的新进展，必要时回查旧证据。'
    return question
