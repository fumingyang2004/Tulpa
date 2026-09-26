"""A Watch trigger asks the saved question through the ordinary chat Agent."""
import json
import threading
import time
from .agent_profiles import profile_config
from .config import settings
from .retrieval import make_plan
from .sync_state import now
from .watch_agent import conversation_memory,scheduled_question


class TaskCancel:
    """Agent cleanup stops its child without overwriting user cancellation."""
    def __init__(self,parent):self.parent=parent;self.local=threading.Event()
    def is_set(self):return self.local.is_set() or bool(self.parent and self.parent.is_set())
    def set(self):self.local.set()
    def wait(self,timeout=None):
        end=None if timeout is None else time.monotonic()+timeout
        while not self.is_set():
            left=None if end is None else end-time.monotonic()
            if left is not None and left<=0:return False
            self.local.wait(.1 if left is None else min(.1,left))
        return True


def check_question(watches,card_id,profile,emit,agent,run_id,cancelled,data_status,refresh_notice):
    store=watches.store;card=watches.get(card_id)
    plan=make_plan(card['watch_query'],card['platform_scope'],card['conversation_scope'])
    with store.connect() as db:
        plan.snapshot_max_id=db.execute('SELECT coalesce(max(id),0) FROM messages').fetchone()[0]
    record={}
    result=dict(mode='scheduled_question',data_status=data_status,refresh_notice=refresh_notice,
                from_cursor=card['cursor'],snapshot_max_id=plan.snapshot_max_id)
    try:
        if cancelled and cancelled.is_set():raise ValueError('已停止更新，之前的回答保留。')
        emit(dict(type='status',text='正在就关注的问题查询一次，按需搜索相关消息…'))
        final=None
        # Exactly the same prompt, tools, evidence validation and budget as chat.
        # No list of incremental IDs, forced paging or per-message review count.
        for event in agent(store,plan,scheduled_question(card),memory=dict(conversation_memory(store,card),read_only=True),
                           config=profile_config(settings(),profile),cancel=TaskCancel(cancelled)):
            if event['type']=='done':final=event;record=event['record']
            else:emit(event)
        if cancelled and cancelled.is_set():raise ValueError('已停止更新，之前的回答保留。')
        if not final or final['status']!='completed':
            raise ValueError(record.get('result',{}).get('error') or '本次回答未完成，可以重试。')
        answer=record['result'];claims=answer.get('claims',[])
        # This is the latest answer, not an assertion that every message was read.
        result.update(answer=answer,requests=record.get('requests',0),queried_at=now())
        evidence=sorted({mid for claim in claims for mid in claim.get('evidence_ids',[])})
        with store.connect() as db:
            changed=db.execute('''UPDATE watch_cards SET last_checked_at=?,baseline_summary=?,baseline=?,evidence_ids=?,
                uncertainties='[]',cursor=?,last_result=?,error='' WHERE id=? AND cursor=? AND last_checked_at IS ? AND revision=?''',
                (result['queried_at'],'\n'.join(c['text'] for c in claims),json.dumps(claims,ensure_ascii=False),json.dumps(evidence),
                 plan.snapshot_max_id,json.dumps(result,ensure_ascii=False),card_id,card['cursor'],card['last_checked_at'],card['revision'])).rowcount
            if not changed:raise ValueError('关注设置已改变，请重新查询。')
            db.execute("UPDATE watch_runs SET status='completed',record=?,result=? WHERE id=?",
                (json.dumps(record,ensure_ascii=False),json.dumps(result,ensure_ascii=False),run_id))
    except Exception as exc:
        result['error']=str(exc) if isinstance(exc,ValueError) else '本次回答未完成，可以重试。'
        with store.connect() as db:
            db.execute('UPDATE watch_cards SET last_result=? WHERE id=? AND revision=? AND cursor=?',
                (json.dumps(result,ensure_ascii=False),card_id,card['revision'],card['cursor']))
            db.execute("UPDATE watch_runs SET status='error',record=?,result=? WHERE id=?",
                (json.dumps(record,ensure_ascii=False),json.dumps(result,ensure_ascii=False),run_id))
        raise
    return watches.get(card_id)
