"""Durable tasks; execution is the existing Harness, not a second agent loop."""
import json
import threading
import time

from .agent import run_agent
from .agent_profiles import profile_config
from .config import settings
from .vision import with_vision_mode
from .workspaces import Workspaces, dump, uid
from .changes import water
from .workspace_ide import task_intent


class WorkspaceRunner:
    def __init__(self,store,agent=run_agent):
        self.store=store;self.layer=Workspaces(store);self.agent=agent;self.active={};self.lock=threading.Lock()
    def recover(self):
        with self.store.connect() as db:
            db.execute("UPDATE workspace_tasks SET state='interrupted',finished_at=? WHERE state='running'",(time.time(),))
    def task(self,wid,tid):
        w=self.layer.get(wid)
        with self.store.connect() as db:r=db.execute('SELECT * FROM workspace_tasks WHERE id=? AND workspace_id=?',(tid,wid)).fetchone()
        if not r:raise ValueError('任务不存在。')
        r=dict(r)
        if r['scope_epoch']!=w['scope_epoch']:
            r.update(question='旧范围任务已隔离',events_json='[]',record_json='{}')
        r['events']=json.loads(r.pop('events_json'));r['record']=json.loads(r.pop('record_json'))
        # Private raw tool snapshots stay in SQLite. Browser gets safe trace + validated claims.
        return r
    def start(self,wid,question,*,mode='auto',profile='deep',vision='ocr',trigger='user'):
        question=str(question or '').strip()
        if not question or len(question)>2000:raise ValueError('任务不能为空，最多2000字。')
        mode=task_intent(question,mode)
        config=with_vision_mode(profile_config(settings(),profile),vision)
        self.layer.discover(wid);w=self.layer.get(wid)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE');wraw=self.layer._get(db,wid)
            if wraw['scope_epoch']!=w['scope_epoch']:raise ValueError('设置已变化，请重试。')
            if db.execute("SELECT 1 FROM workspace_tasks WHERE workspace_id=? AND state='running'",(wid,)).fetchone():raise ValueError('该工作区已有任务执行中。')
            count=db.execute("SELECT count(*) FROM workspace_candidates WHERE workspace_id=? AND scope_epoch=? AND decision='pending'",(wid,w['scope_epoch'])).fetchone()[0]
            if mode=='changes' and w['gap']:raise ValueError('变化流存在缺口，请先指定日期补查并确认重建接续点。')
            tid=uid();start=water(db)['high_water'];empty=mode=='changes' and not count
            record=dict(result=dict(claims=[],insufficient=False),note='没有新的待整理候选，未调用模型。关键词未命中项仍可补查。',requests=0,usage={},seconds=0) if empty else {}
            db.execute('INSERT INTO workspace_tasks(id,workspace_id,scope_epoch,question,mode,state,start_seq,created_at,record_json,finished_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
              (tid,wid,w['scope_epoch'],question,mode,'completed' if empty else 'running',start,time.time(),dump(record),time.time() if empty else None))
        if empty:return dict(task_id=tid,state='completed',api_called=False)
        cancel=threading.Event();context=dict(id=wid,task_id=tid,scope_epoch=w['scope_epoch'],start_seq=start,decisions={},intent=mode,trigger=trigger)
        with self.lock:self.active[tid]=cancel
        threading.Thread(target=self._work,args=(w,tid,question,mode,config,cancel,context),daemon=True,name='workspace-task').start()
        return dict(task_id=tid,state='running')
    def _work(self,w,tid,question,mode,config,cancel,context):
        wid=w['id'];events=[];record={};status='error'
        try:
            # A few prior turns carry referents only; authoritative state is read
            # from applied products. Old-scope conversations stay sealed.
            with self.store.connect() as db:
                prior=db.execute('SELECT question,state,record_json FROM workspace_tasks WHERE workspace_id=? AND scope_epoch=? AND id!=? ORDER BY created_at DESC LIMIT 3',(wid,w['scope_epoch'],tid)).fetchall()
            memory=dict(previous_turns=[dict(question=r['question'],status=r['state'],claims=json.loads(r['record_json']).get('result',{}).get('claims',[])[:4],query_state={}) for r in reversed(prior)])
            for event in self.agent(self.store,self.layer.plan(wid),question,memory=memory,cancel=cancel,config=config,workspace_context=context):
                if event['type']=='done':record=event['record'];status=event['status'];break
                safe={k:event[k] for k in ('type','name','text','request','request_limit','characters','reasoning_effort') if k in event}
                if event['type']=='tool_end':
                    result=event.get('result',{});safe['summary']=dict(error=result.get('error'),messages=len(result.get('messages',[])),files=len(result.get('files',[])),remaining_chars=result.get('remaining_chars'))
                events.append(safe)
                with self.store.connect() as db:db.execute('UPDATE workspace_tasks SET events_json=? WHERE id=?',(dump(events[-200:]),tid))
            if cancel.is_set() and status!='completed':status='cancelled'
            # Keep a compact durable result; no unbounded dialogue replay on next task.
            compact={k:record[k] for k in ('result','requests','usage','seconds','budget') if k in record}
            compact['trace']=record.get('events',[])
            compact['staged_decisions']=context['decisions']
            compact['coverage']=dict(messages=len(record.get('bundle',{}).get('messages',[])),file_chunks=len(record.get('bundle',{}).get('file_evidence',{})),
                                      incomplete=record.get('bundle',{}).get('incomplete',True),candidate_decisions=len(context['decisions']))
            compact['continuation']='可继续任务；未处理候选和草稿保留。原文检索为预算内覆盖，不保证完整。'
            with self.store.connect() as db:
                produced=[dict(r) for r in db.execute('SELECT id,output_id,format,name,base_version FROM workspace_proposals WHERE task_id=?',(tid,))]
            compact['drafts_saved']=produced
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE');latest=self.layer._get(db,wid)
                if latest['scope_epoch']!=w['scope_epoch']:status='scope_changed'
                db.execute('UPDATE workspace_tasks SET state=?,finished_at=?,record_json=?,events_json=? WHERE id=?',(status,time.time(),dump(compact),dump(events),tid))
                db.execute("UPDATE workspace_proposals SET state=? WHERE task_id=?",('pending' if status=='completed' else 'draft',tid))
                if status=='completed':
                    for cid,d in context['decisions'].items():
                        db.execute("UPDATE workspace_candidates SET decision=? WHERE workspace_id=? AND scope_epoch=? AND id=? AND decision='pending' AND seq<=?",(d['decision'],wid,w['scope_epoch'],cid,context['start_seq']))
                    pending=db.execute("SELECT min(seq) FROM workspace_candidates WHERE workspace_id=? AND scope_epoch=? AND decision='pending' AND seq>0",(wid,w['scope_epoch'])).fetchone()[0]
                    seq=min(latest['discovery_cursor'],context['start_seq'],pending-1 if pending else context['start_seq'])
                    db.execute('UPDATE workspaces SET processed_seq=max(processed_seq,?),updated_at=? WHERE id=?',(seq,time.time(),wid))
                    self.layer.audit(db,wid,'task_completed',dict(task_id=tid,start_seq=context['start_seq'],processed_seq=seq,decisions=context['decisions']))
            # Every proposal, including new products and collected materials,
            # remains pending. Only the explicit user apply route commits it.
        except Exception as exc:
            # Never persist provider text, filesystem paths, credentials, or tracebacks.
            with self.store.connect() as db:db.execute("UPDATE workspace_tasks SET state='error',finished_at=?,record_json=? WHERE id=?",(time.time(),dump(dict(error='任务执行失败，草稿和候选保留，可继续。',error_type=type(exc).__name__)),tid))
        finally:
            with self.lock:self.active.pop(tid,None)
    def stop(self,wid,tid):
        self.task(wid,tid)
        with self.lock:
            if tid in self.active:self.active[tid].set()
        return dict(stopping=True)

    def maintain(self,now=None):
        now=time.time() if now is None else now
        with self.store.connect() as db:due=[dict(r) for r in db.execute('SELECT * FROM workspace_schedules WHERE minutes>0 AND next_at<=?',(now,))]
        for item in due:
            wid=item['workspace_id']
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute("SELECT 1 FROM workspace_tasks WHERE workspace_id=? AND state='running'",(wid,)).fetchone():continue
                claimed=db.execute('UPDATE workspace_schedules SET next_at=?,last_at=?,error=? WHERE workspace_id=? AND minutes>0 AND next_at=?',(now+item['minutes']*60,now,'',wid,item['next_at'])).rowcount
            if not claimed:continue
            try:
                result=self.start(wid,'整理最近变化，核实当前状态；只提出修改建议，等待用户确认。',mode='changes',profile=item['profile'],vision=item['vision'],trigger='scheduled')
                with self.store.connect() as db:db.execute('UPDATE workspace_schedules SET last_task_id=? WHERE workspace_id=?',(result['task_id'],wid))
            except Exception:
                with self.store.connect() as db:db.execute("UPDATE workspace_schedules SET error='本次维护未完成；候选保留，可手动重试或等待下一次计划。' WHERE workspace_id=?",(wid,))
