"""Local client import controls and preview-bound deletion, on the shared job queue."""
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from fastapi import HTTPException,Request
from .config import ROOT
from .import_scope import bounds,get_scope,save_scope,validate_scope
from .read_options import read_options
from .sync import ingestion_lock,message_commit_lock
from .data_delete import preview,purge
from .client_accounts import resolve_account,accounts_view,account_id,bound_account


def run_reader(platform,arguments,output,*,store=None,progress=None,cancelled=None):
    if store is not None:
        from .import_pipeline import run_batches
        return run_batches(store,platform,arguments,output,progress=progress,cancelled=cancelled)
    result=subprocess.run([sys.executable,str(ROOT/'scripts'/f'export_{platform}.py'),
        '--refresh','--output',str(output),*arguments],cwd=ROOT,
        env=dict(os.environ,PYTHONUTF8='1'),capture_output=True,text=True,encoding='utf-8',errors='replace',
        timeout=600,creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        log=ROOT/'reports/private'/f'data-{platform}-error.log'
        log.parent.mkdir(parents=True,exist_ok=True)
        log.write_text(result.stderr,encoding='utf-8')
        if 'database remained active during' in result.stderr:
            raise ValueError('数据库在复制期间更新；请重试，未推进进度。')
        errors=[line.split('ValueError:',1)[1].strip() for line in result.stderr.splitlines() if line.startswith('ValueError:')]
        if platform=='wechat' and '数据库无可用密钥:' in result.stderr:
            raise ValueError('微信数据库密钥未取得：请在已登录微信中打开目标聊天后重试。若仍失败，请提供微信版本及 reports/private/data-wechat-error.log；这与模型 API Key 无关。')
        if platform=='qq' and 'isolated QQ SQLite helper' in result.stderr:
            raise ValueError('QQ 大数据库读取器自检未通过；请更新 Tulpa 修复包。诊断位于 reports/private/data-qq-error.log。')
        if platform=='qq' and 'isolated QQ SQLite query failed' in result.stderr:
            raise ValueError('QQ 数据库已打开，但消息查询失败。请导出运行诊断，或查看 reports/private/data-qq-error.log；这不是登录或 API Key 错误。')
        if '本地数据库副本暂时被占用或权限异常，无法重建。' in result.stderr:
            raise ValueError('本地数据库副本被占用或权限异常，暂时无法重建。请完全关闭 Tulpa 后重新打开；客户端和已导入记录保留。')
        raise ValueError(errors[-1][:240] if errors else '客户端读取失败，请保持登录后重试；本机已保存诊断。')
    # Read the exporter's small final receipt, not a second copy of the whole
    # export. Missing/legacy receipts are fine; arbitrary stdout is not shown.
    for line in reversed(result.stdout.splitlines()):
        try:receipt=json.loads(line)
        except ValueError:continue
        if isinstance(receipt,dict) and type(receipt.get('messages')) is int:
            return {k:receipt[k] for k in ('skipped','invalid_timestamps')
                    if type(receipt.get(k)) is int and 0<=receipt[k]<=10**9}
    return {}


def read_clients(store,scope,limits,ranges,stickers,independent,progress,*,all_messages=None,cancelled=None):
    """One explicit import includes bounded history + opted-in media enrichment.
    Their outcomes remain separate: restored media cannot conceal a failed read.
    """
    from .media_refresh import refresh_for_import
    from datetime import datetime
    from .normalize import TZ
    started=datetime.now(TZ).isoformat();results=[]
    with ingestion_lock(commit=False),tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='read-') as folder:
        saved_scope=get_scope(store)
        for platform,item in scope.items():
            if not item['enabled']:
                saved_scope[platform]=dict(item)
        for platform,item in scope.items():
            if not item['enabled']:continue
            if cancelled and cancelled.is_set():
                results.append(dict(platform=platform,status='error',added=0,duplicate=0,detail='用户已停止，未开始此平台'))
                continue
            label='QQ' if platform=='qq' else '微信'
            progress(f'正在读取 {label} 的选定范围（表情包：{"开启" if stickers else "关闭"}）…')
            if platform=='qq':
                args=['--date-range'] if independent else ['--days',str(limits['qq_days'])]
                args+=['--per-chat',str(limits['qq_per_chat'])]
            else:args=['--limit',str(limits['wechat_per_chat'])]
            if (all_messages or {}).get(platform):args.append('--all-messages')
            begin=ranges[platform].get('start','');end=ranges[platform].get('end','')
            for cid in item['conversations'] or []:args+=['--chat',cid]
            if begin:args+=['--start',begin]
            if end:args+=['--end',end]
            if not stickers:args+=['--no-stickers']
            entry=dict(platform=platform,status='ok',added=0,duplicate=0)
            output=Path(folder)/(platform+'-real.json')
            read_receipt={}
            try:
                account=resolve_account(store,platform,item.get('account'))
                args+=['--account',account]
                saved_scope[platform]=dict(item,account=account)
                save_scope(store,saved_scope)
                for attempt in range(3):
                    try:read_receipt=run_reader(platform,args,output,store=store,progress=progress,cancelled=cancelled) or {};break
                    except ValueError as exc:
                        if attempt==2 or not any(s in str(exc) for s in ('数据库在复制期间更新','database remained active during')):raise
                        progress(f'{label} 客户端正在写入，重试读取（{attempt+2}/3）；已提交批次保留。')
                # The cold reader needn't block live commits for its entire run.
                for attempt in range(30):
                    try:
                        with message_commit_lock():result=store.import_file(output,load_stickers=stickers,complete_job=read_receipt.get('job_id'))
                        break
                    except ValueError as exc:
                        if attempt==29 or '正在读取或导入数据' not in str(exc):raise
                        time.sleep(.1)
                # The streaming reader has already committed each data batch;
                # this final small envelope records coverage/labels only.
                entry.update(added=result['imported']+read_receipt.get('imported',0),
                    duplicate=result['duplicate']+read_receipt.get('duplicate',0),
                    skipped=result.get('skipped',0)+read_receipt.get('skipped',0))
                if job_id:=read_receipt.get('job_id'):
                    entry['job_id']=job_id
                if invalid:=read_receipt.get('payload',read_receipt).get('invalid_timestamps',0):
                    entry['invalid_timestamps']=invalid
                    entry['warning']=f'已排除 {invalid} 条时间戳无效的源记录（不占会话条数上限）；正常消息已导入，未伪造日期。'
                    progress(label+'：'+entry['warning'])
                progress(f"{label} 新增 {entry['added']} 条，已有 {entry['duplicate']} 条；开始核对媒体。" if stickers else f"{label} 新增 {entry['added']} 条，已有 {entry['duplicate']} 条。")
            except Exception as exc:
                entry.update(status='error',detail=str(exc) if isinstance(exc,ValueError) else '读取或导入未完成，请重试')
                partial=getattr(exc,'result',read_receipt)
                if partial.get('job_id'):entry.update(added=partial['imported'],duplicate=partial['duplicate'],job_id=partial['job_id'])
                progress(f"{label} 聊天记录读取失败：{entry['detail']}")
            if stickers and not (cancelled and cancelled.is_set()):
                # Explicit opt-in also repairs previously omitted rows outside
                # the latest-N export. Existing archives suffice even if the
                # client snapshot is busy; that is reported as partial success.
                repair_begin=begin
                if platform=='qq' and not independent and not repair_begin:
                    repair_begin=datetime.fromtimestamp(time.time()-limits['qq_days']*86400,TZ).strftime('%Y-%m-%d')
                try:
                    progress(f'{label} 正在补载同一会话、日期范围内已导入的表情包（不受新消息条数上限影响）…')
                    entry['media']=refresh_for_import(store,platform,item['conversations'],repair_begin,end,progress)
                except Exception as exc:
                    entry['media_error']=str(exc) if isinstance(exc,ValueError) else '媒体补载未完成，请重试'
                    if entry['status']=='ok':entry['status']='partial'
                    progress(f"{label} 媒体补载失败：{entry['media_error']}")
            results.append(entry)
    summaries=[]
    for r in results:
        label='QQ' if r['platform']=='qq' else '微信'
        text=f"{label} 聊天读取失败：{r['detail']}" if r['status']=='error' else f"{label} 新增 {r['added']} 条，已有 {r['duplicate']} 条"
        if r.get('warning'):text+='；'+r['warning']
        if 'media' in r:text+='；媒体补载：'+r['media']['summary']
        if 'media_error' in r:text+='；媒体补载失败：'+r['media_error']
        summaries.append(text)
    status='ok' if all(r['status']=='ok' for r in results) else 'error' if all(r['status']=='error' and not r.get('media',{}).get('updated') for r in results) else 'partial'
    prefix='部分完成（聊天读取与媒体恢复结果分别如下）：\n' if status=='partial' else '读取未完成：\n' if status=='error' else ''
    receipt=dict(summary=prefix+'\n'.join(summaries),status=status,platforms=results,started_at=started,finished_at=datetime.now(TZ).isoformat(),
                 options=dict(scope=scope,limits=limits,ranges=ranges,load_stickers=stickers,all_messages=all_messages or {}))
    # Private receipt lets diagnosis match the actual request instead of the
    # mtime of a stale error log. Never contains chat/media bodies or keys.
    try:
        path=store.path.parent/'read-latest.json';path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    except OSError:pass
    return receipt


def install_data_routes(app,store,start,idle,same_origin):
    previews={}

    @app.post('/api/data/media-refresh')
    def media_refresh(request:Request,body:dict):
        same_origin(request)
        from .media_refresh import selection,refresh
        try:selection(body)
        except (TypeError,ValueError) as exc:raise HTTPException(400,str(exc)) from None
        try:return start(lambda progress:refresh(store,body,progress),kind='media-refresh')
        except ValueError as exc:raise HTTPException(409,str(exc)) from None

    @app.get('/api/data/settings')
    def settings(request:Request):
        same_origin(request)
        imported=store.conversations();catalog={(r['platform'],r['conversation_id']):dict(r) for r in imported}
        with store.connect() as db:
            for row in db.execute("SELECT value FROM client_settings WHERE name LIKE 'catalog_%'"):
                for item in json.loads(row[0]):catalog[(item['platform'],item['conversation_id'])]=item
            pending=[dict(r) for r in db.execute("SELECT id,platform,status,updated_at FROM import_jobs WHERE status!='completed' ORDER BY updated_at DESC LIMIT 10")]
        owners={}
        for p in ('qq','wechat'):
            try:owners[p]=bound_account(store,p) or get_scope(store)[p].get('account')
            except ValueError:owners[p]=None
        return dict(scope=get_scope(store),conversations=list(catalog.values()),imported=imported,pending_imports=pending,catalog_accounts=owners)

    @app.get('/api/data/accounts')
    def accounts(request:Request):
        same_origin(request)
        return accounts_view(store)

    @app.post('/api/data/catalog')
    def catalog(request:Request,body:dict):
        same_origin(request)
        platforms=body.get('platforms')
        if not isinstance(platforms,list) or not platforms or any(p not in ('qq','wechat') for p in platforms):raise HTTPException(400,'请选择读取平台')
        accounts=body.get('accounts',{})
        try:
            if not isinstance(accounts,dict) or any(p not in ('qq','wechat') for p in accounts):raise ValueError('无效账号选择')
            accounts={p:account_id(v) for p,v in accounts.items()}
        except ValueError as exc:raise HTTPException(400,str(exc)) from None
        def work(progress):
            results=[]
            with ingestion_lock(),tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='catalog-') as folder:
                for platform in dict.fromkeys(platforms):
                    progress(f'正在读取 {platform} 会话目录（不导入消息）…')
                    try:
                        account=resolve_account(store,platform,accounts.get(platform))
                        output=Path(folder)/(platform+'.json');run_reader(platform,['--list','--account',account],output)
                        rows=json.loads(output.read_text(encoding='utf-8'))['conversations']
                        scope=get_scope(store)
                        if scope[platform].get('account') not in (None,account):scope[platform]['conversations']=None
                        scope[platform]['account']=account
                        save_scope(store,scope)
                        with store.connect() as db:
                            db.execute('INSERT OR REPLACE INTO client_settings VALUES(?,?)',('catalog_'+platform,json.dumps(rows)))
                            if platform=='qq':
                                from .qq_labels import apply_labels
                                for account in {r['conversation_id'].split(':',1)[0] for r in rows}:
                                    apply_labels(db,account,rows)
                        results.append(f'{platform}：{len(rows)} 个会话')
                    except Exception as exc:results.append(f'{platform}：'+(str(exc) if isinstance(exc,ValueError) else '目录读取未完成，请重试'))
            return dict(summary='；'.join(results))
        try:return start(work,kind='catalog')
        except ValueError as exc:raise HTTPException(409,str(exc)) from None

    @app.post('/api/data/read')
    def read(request:Request,body:dict):
        same_origin(request)
        try:
            scope=validate_scope(body.get('scope'));limits=read_options(**body.get('limits',{}))
            independent='ranges' in body
            ranges=body.get('ranges') if independent else {p:dict(start=body.get('start',''),end=body.get('end','')) for p in scope}
            if not isinstance(ranges,dict) or set(ranges)!={'qq','wechat'}:raise ValueError('请分别指定 QQ 和微信的日期范围')
            for platform,window in ranges.items():
                if not isinstance(window,dict):raise ValueError('无效日期范围')
                if scope[platform]['enabled']:bounds(window.get('start',''),window.get('end',''))
            stickers=body.get('load_stickers',False)
            if type(stickers) is not bool:raise ValueError('表情包开关无效')
            all_messages=body.get('all_messages',{})
            if not isinstance(all_messages,dict) or any(p not in ('qq','wechat') or type(v) is not bool for p,v in all_messages.items()):raise ValueError('读取全部消息设置无效')
        except (ValueError,TypeError) as exc:raise HTTPException(400,str(exc)) from None
        import threading
        cancelled=threading.Event()
        def work(progress):return read_clients(store,scope,limits,ranges,stickers,independent,progress,all_messages=all_messages,cancelled=cancelled)
        try:return start(work,kind='read',cancelled=cancelled)
        except ValueError as exc:raise HTTPException(409,str(exc)) from None

    @app.post('/api/data/delete-preview')
    def deletion_preview(request:Request,body:dict):
        same_origin(request)
        try:
            idle();result=preview(store,body)
            token=uuid.uuid4().hex
            for old in list(previews):
                if previews[old]['expires']<time.monotonic():previews.pop(old)
            if len(previews)>=50:previews.pop(next(iter(previews)))
            previews[token]=dict(body=body,fingerprint=result.pop('fingerprint'),expires=time.monotonic()+600)
            return dict(token=token,**result)
        except ValueError as exc:raise HTTPException(400,str(exc)) from None

    @app.post('/api/data/delete')
    def deletion(request:Request,body:dict):
        same_origin(request)
        item=previews.get(body.get('token',''))
        if not item or item['expires']<time.monotonic():raise HTTPException(409,'删除预览已过期，请重新预览')
        def work(progress):
            with ingestion_lock():return purge(store,item['body'],item['fingerprint'],progress)
        try:
            job=start(work,kind='delete');previews.pop(body['token'],None);return job
        except ValueError as exc:raise HTTPException(409,str(exc)) from None
