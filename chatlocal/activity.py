"""Protect evidence deletion while an Agent uses it; allow concurrent ingestion."""
import threading
from contextlib import contextmanager

_guard=threading.RLock()
_states={}


@contextmanager
def evidence_access(store,*,delete=False):
    key=str(store.path.resolve())
    with _guard:
        state=_states.setdefault(key,dict(readers=0,deleting=False))
        if state['deleting'] or (delete and state['readers']):
            raise ValueError('聊天正在使用原文或记录正在删除，请等待当前操作完成。')
        if delete:state['deleting']=True
        else:state['readers']+=1
    try:yield
    finally:
        with _guard:
            if delete:state['deleting']=False
            else:state['readers']-=1
            if not state['readers'] and not state['deleting']:_states.pop(key,None)
