import json
import os
import subprocess
import sys
import time
import threading
from pathlib import Path

from .config import ROOT, settings, local_path, agent_options
from .store import Store
from .retrieval import make_plan, retrieve
from .llm import answer, ProviderError
from .render import answer_html, evidence_html, esc, scope_text, identity_html
from .normalize import display_time
from .agent import run_agent
from .agent_tools import LABELS
from .agent_profiles import profile_config,profile_label
from .read_options import READ_LIMITS,read_options,read_description
from .agent_sessions import Sessions
from .appearance import WELCOME, FOCUS_COMPOSER_JS
from .reasoning import reasoning_html

import gradio as gr


def build_app(store=None,sessions=None):
    store = store or Store()
    sessions = sessions or Sessions()
    running = {}
    session_guard = threading.Lock()

    def transcript(sid):
        messages=[]
        for turn in sessions.history(sid):
            result=turn['record'].get('result',{})
            bundle=turn['record'].get('bundle')
            content=answer_html(result,bundle,compact=True) if bundle else ''
            if result.get('error') and not bundle:
                content='<p class="answer-note">'+esc(result['error'])+'</p>'+content
            elif not content:
                pending='正在查找相关消息…' if sid in running else '这一轮未完成，可以重新提问。'
                content='<p class="pending-answer">'+pending+'</p>'
            record=turn['record']
            content=reasoning_html(record.get('reasoning'))+content
            if record.get('events'):
                events=record['events']
                steps=[progress_line(e) if isinstance(e,dict) else str(e) for e in events]
                calls=sum(isinstance(e,dict) and e.get('type')=='tool_start' for e in events)
                duration=record.get('seconds')
                caption=(f'用时 {duration:g} 秒' if isinstance(duration,(float,int)) else '查询过程')
                if calls: caption+=f' · {calls} 次查询'
                content=('<details class="turn-activity"><summary>'+esc(caption)+'</summary><pre>'+
                         esc('\n'.join(line for line in steps if line))+'</pre>'+agent_scope(record)+'</details>'+content)
            # Render only escaped text and our own evidence HTML. Model/chat
            # Markdown must not become external images, links, or executable HTML.
            user_content='<div class="user-text">'+esc(turn['question'])+'</div>'
            # Keep each HTML block on one Markdown source line. A blank line
            # inside untrusted text must not reopen Markdown image/link parsing.
            as_chat_html=lambda value:value.replace('\r','&#13;').replace('\n','&#10;')
            messages += [dict(role='user',content=as_chat_html(user_content)),
                         dict(role='assistant',content=as_chat_html(content))]
        return messages

    def live_status(text='',busy=False):
        if not text: return ''
        return ('<div class="live-status'+(' busy' if busy else '')+'" role="status" aria-live="polite">'
                '<span class="status-dot" aria-hidden="true"></span><span>'+esc(text)+'</span></div>')

    def progress_line(event):
        kind=event['type']
        if kind=='model_reasoning': return ''
        if kind=='model_start':
            effort=event.get('reasoning_effort')
            return (f'请求模型 {event["request"]}/{event.get("request_limit",6)}（本次上下文约 {event["characters"]} 字符）'
                    +(f' · 思考强度 {effort}' if effort else ''))
        if kind=='tool_start':
            return LABELS.get(event['name'],'查询')+'：'+json.dumps(event.get('arguments',{}),ensure_ascii=False)
        if kind=='tool_end':
            result=event['result']
            if result.get('error'): return '查询反馈：'+result['error']
            if event['name']=='find_people':
                return f'找到 {result.get("candidate_count",0)} 个账号候选'+('；存在同名，请结合会话区分。' if result.get('ambiguous') else '；可按账号读取各会话发言。')
            if event.get('name')=='inspect_image':
                provider={'ocr':'本地OCR','deepseek':'DeepSeek 原生识图'}.get(result.get('provider'),'视觉API')
                return f'图片识别完成 · {provider} · {len(result.get("ocr",""))}字'+(' · 复用本地识别缓存' if result.get('cached') else '')
            rows=result.get('messages',result.get('conversations',result.get('platforms',result.get('media',[]))))
            return f'查询完成：返回 {len(rows)} 项'+('，还有更多结果。' if result.get('has_more') else '。')
        return event.get('text','')

    def agent_scope(record):
        bundle=record.get('bundle',{})
        if not bundle: return ''
        text=(f'本轮 API 请求 {record.get("requests",0)} 次 · 取得 {len(bundle["messages"])} 条消息 · '
              f'证据约 {bundle["context_chars"]} 字符 · 用量 {record.get("usage",{}).get("total_tokens",0)} tokens。'
              ' 原始消息范围受当前筛选限制；检索不代表覆盖全部记录。')
        if record.get('budget'):
            budget=record['budget']
            if record.get('profile'):
                text+=f' 本轮强度：{profile_label(record["profile"])}。'
            text+=f' 本轮预算：{budget["max_requests"]} 次模型请求 / {budget["max_tool_calls"]} 次工具调用 / {budget["max_seconds"]} 秒。'
        if record.get('vision_mode'):
            text+=' 识图模式：'+('DeepSeek 原生识图。' if record['vision_mode']=='native' else '本地 OCR。')
        if bundle.get('plan',{}).get('snapshot_max_id') is not None:
            text+=' 本轮仅查询开始回答时已入库的消息；刷新后新增的消息可在下一轮查询。'
        vision_calls=[r for r in bundle.get('inspections',{}).values() if r.get('image_uploaded') and not r.get('cached')]
        if vision_calls:text+=f' 另有识图 API {len(vision_calls)} 次，报告用量 {sum(r.get("usage",{}).get("total_tokens",0) for r in vision_calls)} tokens。'
        return '<p>'+esc(text)+'</p>'

    def agent_action(question,platforms,conversations,start,end,keywords,sid,profile=None,vision_mode=None):
        tid=None
        cancelled=threading.Event()
        progress=[]
        reasoning={}
        try:
            if not platforms: raise ValueError('请至少选择一个平台。')
            plan=make_plan(question,platforms,conversations,start,end,keywords)
            config=profile_config(settings(),profile)
            from .vision import with_vision_mode
            config=with_vision_mode(config,vision_mode)
            sid=sid or sessions.create()
            with session_guard:
                if sid in running: raise ValueError('当前对话正在回答，请等待或停止本轮。')
                running[sid]=cancelled
            memory=sessions.memory(sid)
            memory['keyword_hint']=(keywords or '').strip()
            options=dict(platforms=platforms,conversations=conversations,start=start,end=end,keywords=keywords)
            if profile is not None:options['profile']=profile
            if vision_mode is not None:options['vision_mode']=vision_mode
            tid=sessions.start(sid,question.strip(),options)
            from .group_admin import GroupAdmin
            management=dict(session_id=sid,turn_id=tid,policy=GroupAdmin(store).policy(sid))
            history=transcript(sid)
            yield history,'准备查询…','','','',sid,gr.update(choices=sessions.choices(),value=sid,interactive=False),gr.update(value='',interactive=False),gr.update(interactive=False),gr.update(interactive=False),live_status('正在查找聊天里的线索…',True),gr.update(visible=True)
            for event in run_agent(store,plan,question,memory,cancelled,config=config,include_reasoning=True,management_context=management,show_unverified=True):
                if event['type']=='done':
                    record=event['record']
                    if profile is not None:record['profile']=profile
                    if vision_mode is not None:record['vision_mode']=vision_mode
                    sessions.finish(tid,event['status'],record)
                    result,bundle=record['result'],record['bundle']
                    if result.get('error'): progress.append(result['error'])
                    else: progress.append('本轮完成，未通过检查的段落已在正文下方注明。' if result.get('rejected') else '本轮完成，引用已由本地原文生成。')
                    answer_view=answer_html(result,bundle)
                    status_text=result.get('error') or ('已回答 · 点击回答中的“原文”查看依据' if result.get('claims') else '没有足够证据 · 可以换个问法或调整范围')
                    if result.get('display_claims'):status_text='已回答 · 未通过检查的内容已在对应段落下方注明' if result.get('rejected') or result.get('error') else '已回答'
                    yield transcript(sid),'\n'.join(progress),answer_view,agent_scope(record),evidence_html(store,bundle),sid,gr.update(choices=sessions.choices(),value=sid,interactive=True),gr.update(value='',interactive=True),gr.update(interactive=True),gr.update(interactive=True),live_status(status_text),gr.update(visible=False)
                else:
                    if event['type']=='model_reasoning':
                        number=event['request']
                        reasoning[number]=reasoning.get(number,'')+event['delta']
                        content=reasoning_html([dict(request=n,text=t) for n,t in sorted(reasoning.items())])
                        content+='<p class="pending-answer">正在查找相关消息…</p>'
                        history=history[:-1]+[dict(role='assistant',content=content.replace('\r','&#13;').replace('\n','&#10;'))]
                    line=progress_line(event)
                    if line: progress.append(line)
                    brief=(LABELS.get(event.get('name'),'正在查询相关消息')+'…' if event['type']=='tool_start'
                           else ('正在思考并核对线索…' if event.get('reasoning_effort') else '正在整理线索…') if event['type']=='model_start' else line)
                    yield history,'\n'.join(progress),gr.skip(),gr.skip(),gr.skip(),sid,gr.skip(),gr.skip(),gr.skip(),gr.skip(),live_status(brief[:100],True) if brief else gr.skip(),gr.skip()
        except (ValueError,OSError,RuntimeError) as error:
            text=str(error) if isinstance(error,ValueError) else '本轮运行失败；请重试。'
            if tid:
                from .group_admin import GroupAdmin
                GroupAdmin(store).cancel(sid,tid)
            if tid: sessions.finish(tid,'error',dict(result={'claims':[],'insufficient':True,'error':text},events=progress,
                reasoning=[dict(request=n,text=t) for n,t in sorted(reasoning.items())]))
            yield transcript(sid) if sid else [],text,'<p>'+esc(text)+'</p>','','',sid,gr.update(choices=sessions.choices(),value=sid,interactive=True),gr.update(interactive=True),gr.update(interactive=True),gr.update(interactive=True),live_status(text),gr.update(visible=False)
        finally:
            cancelled.set()
            with session_guard:
                if sid and running.get(sid) is cancelled: running.pop(sid,None)

    def stop_agent(sid):
        if sid in running:
            running[sid].set()
            from .group_admin import GroupAdmin
            GroupAdmin(store).cancel(sid)
            text='正在停止本轮；已取得的原文证据会保留。'
            return text,live_status(text,True)
        text='当前没有运行中的回答。'
        return text,live_status(text)

    def new_session():
        sid=sessions.create()
        return sid,gr.update(choices=sessions.choices(),value=sid,interactive=True),[],'','','','','','',gr.update(visible=False),gr.update(open=False)

    def refresh_sessions():
        return gr.update(choices=sessions.choices())

    def delete_session(target_sid,sid):
        with session_guard:
            if target_sid in running:
                raise gr.Error('此对话正在回答，请先停止回答并等待结束，再删除。')
            try:removed=sessions.delete(target_sid)
            except ValueError as exc:raise gr.Error(str(exc)) from None
            from .group_admin import GroupAdmin
            GroupAdmin(store).cancel(target_sid)
        cleared=target_sid==sid
        current=None if cleared else sid
        result=dict(deleted=removed,deleted_id=target_sid,cleared_current=cleared)
        head=(result,current,gr.update(choices=sessions.choices(),value=current,interactive=True))
        if cleared:
            return head+([], '', '', '', '', '', '',gr.update(visible=False),gr.update(open=False))
        return head+tuple(gr.skip() for _ in range(9))

    def load_session(sid):
        rows=sessions.history(sid)
        record=rows[-1]['record'] if rows else {}
        bundle=record.get('bundle')
        options=rows[-1]['options'] if rows else {}
        events=record.get('events',[])
        progress='\n'.join(progress_line(e) if isinstance(e,dict) else str(e) for e in events)
        return (sid,transcript(sid),answer_html(record['result'],bundle) if bundle else '',agent_scope(record),
                evidence_html(store,bundle) if bundle else '',progress,'',
                options.get('platforms',['qq','wechat']),options.get('conversations') or [],
                options.get('start') or '',options.get('end') or '',options.get('keywords') or '',
                live_status('已恢复对话 · 可继续追问，或展开回答中的原文') if rows else '',
                gr.update(visible=False),gr.update(open=False))

    def refresh():
        stats = {r['platform']:r for r in store.stats()}
        lines = []
        for platform, name in [('qq','QQ'),('wechat','微信')]:
            row = stats.get(platform)
            if not row:
                lines.append('<div class="data-card"><strong>'+name+'</strong>尚未导入</div>')
            else:
                lines.append('<div class="data-card"><strong>'+name+'</strong>'+esc(
                    f'{row["messages"]:,} 条消息 · {row["conversations"]} 个会话 · {row["media_messages"]:,} 条含媒体 · {row["sticker_placeholders"]:,} 条表情占位')+
                    '<small>已导入消息时间<br>'+esc(display_time(row['first']))+'<br>至 '+esc(display_time(row['last']))+'</small></div>')
        qq_coverage=store.latest_qq_coverage()
        if qq_coverage:
            read_scope=qq_coverage.get('read_scope',{})
            window=f'回溯 {qq_coverage["days"]} 天'
            if read_scope.get('date_range'):window='不限开始时间'
            if read_scope.get('start'):window='从 '+read_scope['start']
            if read_scope.get('end'):window+='，截至 '+read_scope['end']+'（日期含当天）'
            if read_scope.get('conversations'):window+=f'；指定 {len(read_scope["conversations"])} 个会话'
            amount=('所选日期范围内全部可读取消息。仅限本机已有记录。' if qq_coverage.get('all_messages') else
                f'每会话最多 {qq_coverage["per_chat"]} 条文本，另取最多 {qq_coverage["media_window"]} 条支持的非文本消息。日期和条数共同限制，不代表完整历史。')
            lines.append('<p>'+esc(f'最近 QQ 自动读取：{window}；{amount}')+'</p>')
        elif 'qq' in stats:
            lines.append('<p>QQ 当前导入来源未记录自动读取范围；下方选项仅控制下一次读取。</p>')
        coverage=store.latest_wechat_coverage()
        if coverage:
            scope=('全部可识别普通会话' if coverage.get('selection')=='all_supported_local_chats' else '指定会话')
            note=(f'最近微信导入：读取{scope} {coverage.get("exported_chats",0)} 个；'+
                  ('所选日期范围内全部原始消息，' if coverage.get('per_chat_limit') is None else f'每会话最多最近 {coverage.get("per_chat_limit",500)} 条原始消息，')+
                  f'{coverage.get("limited_chats",0)} 个会话有更早记录未导出。支持文本、图片、表情与可解析引用，不代表完整历史。')
            if coverage.get('start') or coverage.get('end'):
                note+=f' 本次日期：{coverage.get("start") or "不限开始"} 至 {coverage.get("end") or "现在"}（日期含当天）；条数上限在日期筛选之后应用。'
            lines.append('<p>'+esc(note)+'</p>')
            unresolved=coverage.get('excluded',{}).get('unresolved',0)
            if unresolved:
                lines.append('<p>'+esc(f'另有 {unresolved} 个会话无法识别名称，未导出。')+'</p>')
        elif 'wechat' in stats:
            lines.append('<p>微信旧导入未记录读取范围；请读取最新聊天记录更新范围说明。</p>')
        cfg = settings()
        lines.append('<p>'+esc(f'模型：{cfg["MODEL"]} · Key：'+('已填写' if cfg['API_KEY'] else '未填写（可先本地检索）'))+'</p>')
        try:
            from .agent import thinking_options
            budget=agent_options(cfg)
            mode=thinking_options(cfg,budget)
            effort=mode.get('reasoning_effort') or ('关闭' if mode else '由兼容服务决定')
            lines.append('<p>'+esc(f'Agent 配置默认：{effort} · 每轮最多 {budget["max_requests"]} 次模型请求 / '
                f'{budget["max_tool_calls"]} 次工具调用 / {budget["max_seconds"]} 秒；主界面以滑块所选档位为准。')+'</p>')
        except ValueError as exc:
            lines.append('<p>'+esc(str(exc))+'</p>')
        from .vision import vision_settings
        visual=vision_settings()
        lines.append('<p>'+esc('高级界面/定时关注的看图默认：'+('本地中文 OCR（不上传图片）' if visual['VISION_PROVIDER']=='ocr' else
            f'视觉 API · {visual["VISION_MODEL"] or "尚未填写模型"}（按需发送指定图片）')+'；聊天提问以输入栏识图开关为准。')+'</p>')
        choices = [(f'{r["platform"]} · {r["conversation"]} ({r["count"]})',
                    json.dumps([r['platform'],r['conversation_id']], ensure_ascii=False))
                   for r in store.conversations()]
        lines.append(identity_html(store))
        return ''.join(lines), gr.update(choices=choices,value=[])

    def read_latest(load_stickers=True,qq_days=30,qq_per_chat=500,wechat_per_chat=500):
        from .sync import ingestion_lock
        try:
            with ingestion_lock():
                yield from read_latest_unlocked(load_stickers,qq_days,qq_per_chat,wechat_per_chat)
        except ValueError as exc:
            yield str(exc),gr.skip(),gr.skip()

    def read_latest_unlocked(load_stickers=True,qq_days=30,qq_per_chat=500,wechat_per_chat=500):
        try:
            if not isinstance(load_stickers,bool):raise ValueError('加载表情包必须为开或关')
            options=read_options(qq_days,qq_per_chat,wechat_per_chat)
        except ValueError as error:
            yield str(error),gr.skip(),gr.skip()
            return
        completed=['本次读取设置：'+read_description(options)]
        for platform,name in [('qq','QQ'),('wechat','微信')]:
            yield '\n'.join(completed+[f'正在读取 {name} 的最新记录，请稍候…']),gr.skip(),gr.skip()
            try:
                env=dict(os.environ,PYTHONUTF8='1')
                limits=(['--days',str(options['qq_days']),'--per-chat',str(options['qq_per_chat'])]
                        if platform=='qq' else ['--limit',str(options['wechat_per_chat'])])
                process=subprocess.run(
                    [sys.executable,str(ROOT/'scripts'/f'export_{platform}.py'),'--refresh']+limits+([] if load_stickers else ['--no-stickers']),
                    cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace',
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                if process.returncode:
                    if '100 MB' in (process.stderr or ''):
                        completed.append(f'{name} 导出超过单文件 100 MB 上限，原有记录保留。请缩小读取天数或每会话条数后重试。')
                    else:
                        completed.append(f'{name} 读取失败，原有记录保留。请确认客户端已登录；若数据库正忙，可稍后重试。')
                else:
                    result=store.import_file(ROOT/'imports'/f'{platform}-real.json',load_stickers=load_stickers)
                    completed.append(f'{name} 更新完成（{display_time(int(time.time()*1000))}）：新增 {result["imported"]} 条消息，已有 {result["duplicate"]} 条记录，{result["sticker_placeholders"]} 条消息使用 [动画表情] 占位。')
                    cleanup=result.get('cache_cleanup')
                    if cleanup:
                        freed=cleanup.get('released_bytes',0)/(1024**3)
                        completed.append(f'QQ 读取缓存已释放 {freed:.2f} GiB。'+cleanup['reason'] if freed else cleanup['reason'])
            except (ValueError,OSError):
                completed.append(f'{name} 更新失败，原有记录保留。可在终端运行 refresh-data.ps1 查看具体原因。')
            state,choices=refresh()
            yield '\n'.join(completed),state,choices

    def import_local(path, platform, conversation, self_ids,load_stickers=True):
        from .sync import ingestion_lock
        try:
            with ingestion_lock():return import_local_unlocked(path,platform,conversation,self_ids,load_stickers)
        except ValueError as exc:
            state,choices=refresh()
            return str(exc),state,choices

    def import_local_unlocked(path, platform, conversation, self_ids,load_stickers=True):
        try:
            if not (path or '').strip():
                raise ValueError('请填写导入文件或目录。')
            path = local_path(path)
            paths = sorted(p for p in path.rglob('*') if p.suffix.lower() in ('.json','.jsonl','.ndjson')) if path.is_dir() else [path]
            if not paths:
                raise ValueError('目录中没有 JSON/JSONL 文件。')
            results = []
            for file in paths:
                try:
                    results.append(store.import_file(file, '' if platform=='自动识别' else platform, conversation, self_ids,load_stickers=load_stickers))
                except (ValueError, OSError, json.JSONDecodeError) as error:
                    results.append({'file':file.name,'error':str(error)})
            state, choices = refresh()
            return json.dumps(results, ensure_ascii=False, indent=2), state, choices
        except (ValueError,OSError) as error:
            state, choices = refresh()
            return str(error), state, choices

    def run(question, platforms, conversations, start, end, keywords, use_llm):
        try:
            if not platforms:
                raise ValueError('请至少选择一个平台。')
            plan = make_plan(question, platforms, conversations, start, end, keywords)
            bundle = retrieve(store, plan)
            evidence = evidence_html(store,bundle)
            summary = esc(scope_text(bundle))
            if not bundle['messages']:
                yield '<p>根据当前已导入记录无法确认。请检查时间范围、会话或检索关键词。</p>', summary, evidence
                return
            if not use_llm:
                yield '<p>已完成本地检索，未调用 API。下方可展开原文；如需模型分析，请发送问题。</p>', summary, evidence
                return
            yield '<p>已检索到证据，正在请求模型…</p>', summary, evidence
            result = answer(question,bundle)
            yield answer_html(result,bundle),summary,evidence
        except (ValueError,OSError) as error:
            # No raw HTTP bodies or keys are surfaced.
            yield '<p>'+esc(error)+'</p>', locals().get('summary',''), locals().get('evidence','')

    def range_summary(platforms,conversations,start,end,keywords):
        names=' + '.join({'qq':'QQ','wechat':'微信'}.get(p,p) for p in (platforms or [])) or '未选择平台'
        selected=f'{len(conversations)} 个会话' if conversations else '全部会话'
        dates=f'{start or "不限"} → {end or "现在"}' if start or end else '时间随问题'
        hint=' · 已设置关键词' if (keywords or '').strip() else ''
        text=f'{names} · {selected} · {dates}{hint}'
        return '<p title="'+esc(text)+'">'+esc(text)+'</p>'

    initial, conversation_choices = refresh()
    with gr.Blocks(title='Tulpa', analytics_enabled=False, fill_height=True,
                   fill_width=True, delete_cache=(3600,86400)) as app:
        session_state=gr.State(None)
        with gr.Sidebar(label='对话与设置',width=272,elem_id='chat-sidebar'):
            gr.HTML('<div class="sidebar-brand"><span class="brand-mark" aria-hidden="true">聊</span>Tulpa</div>')
            new_button=gr.Button('＋  新对话',size='sm',elem_id='new-chat')
            session_picker=gr.Radio(choices=sessions.choices(),label='最近对话',value=None,
                                    interactive=True,container=False,elem_id='session-list')
            with gr.Accordion('搜索范围',open=False,elem_classes='sidebar-section'):
                platforms=gr.CheckboxGroup([('QQ','qq'),('微信','wechat')],value=['qq','wechat'],label='平台')
                conversations=gr.Dropdown(choices=conversation_choices['choices'],value=[],multiselect=True,
                    label='会话',info='留空搜索全部已导入会话')
                start=gr.Textbox(value='',label='开始时间',placeholder='YYYY-MM-DD 或 YYYY-MM-DD HH:mm')
                end=gr.Textbox(value='',label='结束时间',placeholder='留空为现在，日期包含当天')
                keywords=gr.Textbox(value='',label='关键词提示',placeholder='可选，用空格分隔')
                gr.Markdown('采用北京时间。时间留空时按问题识别；“最近”默认过去 7 天。')
            with gr.Accordion('聊天数据',open=not bool(store.stats()),elem_classes='sidebar-section'):
                status=gr.HTML(initial,elem_id='data-status')
                gr.Markdown('**下一次从客户端读取**。QQ 同时受天数及条数限制；微信按条数读取。扩大天数不会取消条数截断，聊天窗口可见也不保证本机数据库已缓存。')
                read_controls=[]
                for rule in READ_LIMITS.values():
                    read_controls.append(gr.Number(value=rule['default'],minimum=rule['min'],maximum=rule['max'],step=1,
                        label=rule['label'],info=f'{rule["min"]}–{rule["max"]}；默认 {rule["default"]}'))
                gr.Markdown('QQ 的条数上限分别用于文本与支持的非文本消息；微信上限包含所有原始类型，再筛选支持类型。未覆盖的旧记录保留。')
                load_stickers=gr.Checkbox(value=False,label='加载表情包（含 GIF / 动态图片）',
                    info='关闭后，本次读取或导入中的表情包显示为 [动画表情]；普通静态图片保留。')
                read_button=gr.Button('读取最新聊天记录',size='sm')
                refresh_button=gr.Button('仅刷新已导入状态',size='sm')
                gr.Markdown('时间是已导入的最新消息时间，不是同步时钟。保持 QQ、微信登录后手动读取；不调用 API。')
                read_progress=gr.Textbox(label='读取进度',value='',interactive=False,lines=2,max_lines=5)
                with gr.Accordion('从文件导入',open=False):
                    gr.Markdown('将 JSON / JSONL 导出放入 `C:\\Lab0921\\imports`。仅导入文件已有记录，不使用上方天数/条数设置，不会补读客户端历史。单文件上限 100 MB。支持 QCE、wx-cli、ChatLab、WeFlow 和统一格式。')
                    import_path=gr.Textbox(value='imports',label='文件或目录')
                    import_platform=gr.Dropdown(['自动识别','qq','wechat'],value='自动识别',label='导入平台')
                    import_conversation=gr.Textbox(value='',label='会话名（文件缺失时填写）')
                    self_ids=gr.Textbox(value='',label='本人账号 ID（可选，逗号分隔）')
                    import_button=gr.Button('导入记录',size='sm')
                    import_result=gr.Textbox(label='导入结果',lines=3,max_lines=6,interactive=False)
            gr.HTML('<div class="sidebar-note">对话与原始记录保存在这台电脑上</div>')

        with gr.Column(elem_id='chat-workspace'):
            gr.HTML('<div class="chat-topbar-inner"><div class="chat-title">Tulpa<span>QQ / 微信</span></div>'
                    '<div class="local-badge">本地记录</div></div>',elem_id='chat-topbar')
            chatbot=gr.Chatbot(label='对话',show_label=False,height=None,min_height=120,scale=1,
                render_markdown=True,sanitize_html=True,allow_tags=False,layout='bubble',
                latex_delimiters=[],buttons=['copy'],feedback_options=[],placeholder=WELCOME,elem_id='chat-thread')
            with gr.Row(elem_id='quick-prompts'):
                quick_missed=gr.Button('我错过了什么？',size='sm',min_width=140)
                quick_promises=gr.Button('我答应过什么？',size='sm',min_width=140)
                quick_summary=gr.Button('总结一段时间',size='sm',min_width=140)
            with gr.Column(elem_id='composer-area'):
                with gr.Row(elem_id='composer-context'):
                    filter_hint=gr.HTML(range_summary(['qq','wechat'],[],None,None,None),elem_id='range-summary',scale=1)
                with gr.Accordion('过程与证据',open=False,elem_id='turn-details') as detail_panel:
                    with gr.Tabs():
                        with gr.Tab('查询过程'):
                            progress_output=gr.Textbox(label='实际查询过程',show_label=False,lines=5,max_lines=8,interactive=False)
                            scope=gr.HTML(label='本次检索范围')
                        with gr.Tab('原文证据'):
                            gr.Markdown('最近一轮的全部检索证据。历史回答中的原文可直接展开。')
                            evidence=gr.HTML()
                        with gr.Tab('本地检索 / 单次回答'):
                            gr.Markdown('使用下方输入框中的问题；结果不加入多轮对话。')
                            with gr.Row():
                                search_button=gr.Button('只做本地检索',size='sm')
                                ask_button=gr.Button('单次证据回答',size='sm')
                            output=gr.HTML()
                            gr.Markdown('检索到的完整来源见“原文证据”。')
                status_line=gr.HTML('',elem_id='live-status')
                with gr.Group(elem_id='composer'):
                    question=gr.Textbox(value='',label='向聊天记录提问',show_label=False,container=False,
                        lines=1,max_lines=6,placeholder='向聊天记录提问，也可以接着追问…',
                        autofocus=True,elem_id='question-box')
                    with gr.Row(elem_id='composer-actions'):
                        gr.HTML('Enter 发送 · Shift + Enter 换行',elem_id='composer-hint',scale=1)
                        stop_button=gr.Button('停止',size='sm',visible=False,scale=0,elem_id='stop-button')
                        agent_button=gr.Button('发送 ↑',variant='primary',size='sm',scale=0,elem_id='send-button')
                gr.HTML('仅将检索到的相关片段发送给 API · 回答请结合原文核对',elem_id='privacy-note')

        inputs=[question,platforms,conversations,start,end,keywords]
        # Gradio recognizes generator functions, so wrappers must also yield.
        def search_action(*args):
            yield from run(*args,False)
        def ask_action(*args):
            yield from run(*args,True)
        search_button.click(search_action,inputs=inputs,outputs=[output,scope,evidence],api_visibility='private',show_progress='minimal')
        ask_button.click(ask_action,inputs=inputs,outputs=[output,scope,evidence],api_visibility='private',concurrency_limit=1,show_progress='minimal')
        agent_outputs=[chatbot,progress_output,output,scope,evidence,session_state,session_picker,question,new_button,agent_button,status_line,stop_button]
        # Keep the existing seven-input API and legacy controls compatible.
        preset_input=gr.Textbox(value='deep',visible=False)
        preset_button=gr.Button(visible=False)
        def agent_action_preset(question,platforms,conversations,start,end,keywords,sid,profile):
            yield from agent_action(question,platforms,conversations,start,end,keywords,sid,profile)
        preset_button.click(agent_action_preset,inputs=inputs+[session_state,preset_input],outputs=agent_outputs,
                            api_visibility='private',concurrency_limit=1,concurrency_id='agent',show_progress='hidden')
        vision_input=gr.Textbox(value='ocr',visible=False)
        controls_button=gr.Button(visible=False)
        def agent_action_controls(question,platforms,conversations,start,end,keywords,sid,profile,vision_mode):
            yield from agent_action(question,platforms,conversations,start,end,keywords,sid,profile,vision_mode)
        controls_button.click(agent_action_controls,inputs=inputs+[session_state,preset_input,vision_input],outputs=agent_outputs,
                              api_visibility='private',concurrency_limit=1,concurrency_id='agent',show_progress='hidden')
        agent_button.click(agent_action,inputs=inputs+[session_state],outputs=agent_outputs,
                           api_visibility='private',concurrency_limit=1,concurrency_id='agent',show_progress='hidden')
        question.submit(agent_action,inputs=inputs+[session_state],outputs=agent_outputs,
                        api_name='agent_submit',api_visibility='private',concurrency_limit=1,concurrency_id='agent',show_progress='hidden')
        stop_button.click(stop_agent,inputs=session_state,outputs=[progress_output,status_line],queue=False,api_visibility='private')
        new_button.click(new_session,outputs=[session_state,session_picker,chatbot,output,scope,evidence,progress_output,question,status_line,stop_button,detail_panel],api_visibility='private',show_progress='hidden').then(
            None,js=FOCUS_COMPOSER_JS,queue=False)
        session_picker.input(load_session,inputs=session_picker,
            outputs=[session_state,chatbot,output,scope,evidence,progress_output,question,platforms,conversations,start,end,keywords,status_line,stop_button,detail_panel],api_visibility='private',show_progress='hidden').then(
            None,js=FOCUS_COMPOSER_JS,queue=False)
        app.load(refresh_sessions,outputs=session_picker,api_visibility='private')
        delete_target=gr.Textbox(visible=False)
        delete_result=gr.JSON(visible=False)
        delete_button=gr.Button(visible=False)
        delete_button.click(delete_session,inputs=[delete_target,session_state],
            outputs=[delete_result,session_state,session_picker,chatbot,output,scope,evidence,progress_output,question,status_line,stop_button,detail_panel],
            api_visibility='private',show_progress='hidden')
        # Keep the old import/read endpoints and add explicit per-operation options.
        def import_local_options(path,platform,conversation,self_ids,load_stickers):
            return import_local(path,platform,conversation,self_ids,load_stickers)
        def read_latest_options(load_stickers):
            yield from read_latest(load_stickers)
        def read_with_limits(load_stickers,qq_days,qq_per_chat,wechat_per_chat):
            yield from read_latest(load_stickers,qq_days,qq_per_chat,wechat_per_chat)
        legacy_import_button=gr.Button(visible=False)
        legacy_read_button=gr.Button(visible=False)
        legacy_options_button=gr.Button(visible=False)
        legacy_import_button.click(import_local,inputs=[import_path,import_platform,import_conversation,self_ids],outputs=[import_result,status,conversations],api_visibility='private',concurrency_id='read_latest',concurrency_limit=1)
        import_button.click(import_local_options,inputs=[import_path,import_platform,import_conversation,self_ids,load_stickers],outputs=[import_result,status,conversations],api_visibility='private',concurrency_id='read_latest',concurrency_limit=1)
        refresh_button.click(refresh,outputs=[status,conversations],api_visibility='private')
        legacy_read_button.click(read_latest,outputs=[read_progress,status,conversations],api_visibility='private',
                          concurrency_limit=1,concurrency_id='read_latest')
        legacy_options_button.click(read_latest_options,inputs=[load_stickers],outputs=[read_progress,status,conversations],api_visibility='private',
                          concurrency_limit=1,concurrency_id='read_latest')
        read_button.click(read_with_limits,inputs=[load_stickers]+read_controls,outputs=[read_progress,status,conversations],api_visibility='private',
                          concurrency_limit=1,concurrency_id='read_latest')
        gr.on([component.change for component in inputs[1:]],range_summary,inputs=inputs[1:],
              outputs=filter_hint,api_visibility='private',queue=False,show_progress='hidden')
        for button,text in [(quick_missed,'What did I miss? / 我错过了什么？'),(quick_promises,'我最近答应别人做过什么？'),(quick_summary,'总结选定时间范围')]:
            button.click(lambda text=text:text,outputs=question,api_visibility='private',show_progress='hidden').then(
                None,js=FOCUS_COMPOSER_JS,queue=False)
    return app
