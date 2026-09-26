"""Read-only local chat/media routes. Files are addressed by message ID, not path."""
from fastapi import HTTPException,Request
from fastapi.responses import FileResponse

from .media import public_media,media_path,media_at,hydrate
from .store import Store
from .normalize import display_time
from .changes import water


def install_chat_routes(app,store=None):
    store=store or Store()

    def same_origin(request):
        origin=request.headers.get('origin')
        if origin and origin!=str(request.base_url).rstrip('/'):
            raise HTTPException(403,'仅允许本机同源页面访问')
        if request.headers.get('sec-fetch-site')=='cross-site':raise HTTPException(403)

    def items(db,rows):
        result=[]
        from .voice import public as public_voice
        for row in rows:
            row=dict(row) if 'media' in dict(row) else hydrate(row)
            item={k:row[k] for k in ('id','platform','conversation_id','conversation','sender','timestamp','content','is_self','source_id','reply_to')}
            item.update(time=display_time(row['timestamp']),media=public_media(row['id'],row.get('media',[])),voice=public_voice(store,row['id']))
            item['files']=[dict(r) for r in db.execute('SELECT id,filename,availability FROM artifact_sources WHERE message_id=?',(row['id'],))]
            ref=row.get('reply_to')
            if ref and ref.get('source_id'):
                found=db.execute('SELECT id FROM messages WHERE platform=? AND conversation_id=? AND source_id=?',
                    (row['platform'],row['conversation_id'],str(ref['source_id']))).fetchone()
                if found:item['reply_to']=dict(ref,message_id=found[0])
            result.append(item)
        return result

    def edges(db,platform,conversation_id,rows):
        params=[platform,conversation_id]
        last=db.execute('SELECT id,timestamp,conversation FROM messages WHERE platform=? AND conversation_id=? ORDER BY timestamp DESC,id DESC LIMIT 1',params).fetchone()
        earlier=later=False
        if rows:
            earlier=bool(db.execute('SELECT 1 FROM messages WHERE platform=? AND conversation_id=? AND (timestamp,id)<(?,?) LIMIT 1',params+[rows[0]['timestamp'],rows[0]['id']]).fetchone())
            later=bool(db.execute('SELECT 1 FROM messages WHERE platform=? AND conversation_id=? AND (timestamp,id)>(?,?) LIMIT 1',params+[rows[-1]['timestamp'],rows[-1]['id']]).fetchone())
        elif last:later=True
        return dict(has_before=earlier,has_after=later,latest_message_id=last['id'] if last else None,
                    latest_timestamp=last['timestamp'] if last else None,conversation=last['conversation'] if last else '')

    @app.get('/api/chat-conversations')
    def conversations(request:Request,query:str='',platform:str|None=None,offset:int=0,limit:int=100,
                      since_seq:int=-1,epoch:str=''):
        same_origin(request)
        if platform not in (None,'qq','wechat') or not 0<=offset or not 1<=limit<=200 or len(query)>200:
            raise HTTPException(400,'会话筛选无效')
        with store.connect() as db:
            db.execute('BEGIN')
            version=water(db)
            if epoch==version['epoch'] and since_seq==version['high_water']:
                return dict(unchanged=True,**version)
            where="platform=?" if platform else '1=1';args=[platform] if platform else []
            # Timeline index supplies the actual last row, never max(content).
            rows=db.execute(f'''WITH groups AS (
                SELECT platform,conversation_id,count(*) count FROM messages WHERE {where} GROUP BY platform,conversation_id)
                SELECT m.id,m.platform,m.conversation_id,m.conversation,m.sender,m.is_self,m.timestamp,
                    substr(m.content,1,160) content,m.media_json,g.count
                FROM groups g JOIN messages m ON m.id=(SELECT id FROM messages
                    WHERE platform=g.platform AND conversation_id=g.conversation_id ORDER BY timestamp DESC,id DESC LIMIT 1)
                WHERE instr(lower(m.conversation),lower(?))>0
                ORDER BY m.timestamp DESC,m.id DESC LIMIT ? OFFSET ?''',args+[query,limit+1,offset]).fetchall()
            choices=[]
            for row in rows[:limit]:
                preview=' '.join(row['content'].split())
                if not preview:preview='[图片 / 表情]' if row['media_json']!='[]' else '[消息]'
                choices.append(dict(platform=row['platform'],conversation_id=row['conversation_id'],conversation=row['conversation'],
                    count=row['count'],last_message_id=row['id'],timestamp=row['timestamp'],time=display_time(row['timestamp']),
                    preview=preview[:140],sender='我' if row['is_self']==1 else row['sender']))
            return dict(conversations=choices,has_more=len(rows)>limit,next_offset=offset+limit,**version)

    @app.get('/api/chat-updates')
    def updates(request:Request,platform:str,conversation_id:str,first_timestamp:int,first_id:int,
                last_timestamp:int,last_id:int,follow:bool=False,since_seq:int=-1,epoch:str=''):
        same_origin(request)
        if platform not in ('qq','wechat') or not conversation_id or first_id<1 or last_id<1 or (first_timestamp,first_id)>(last_timestamp,last_id):
            raise HTTPException(400,'聊天窗口范围无效')
        with store.connect() as db:
            db.execute('BEGIN');version=water(db)
            changed=since_seq<0 or epoch!=version['epoch'] or since_seq>version['high_water'] or bool(db.execute(
                'SELECT 1 FROM local_changes WHERE platform=? AND conversation_id=? AND seq>? LIMIT 1',
                (platform,conversation_id,since_seq)).fetchone())
            if not changed:return dict(unchanged=True,**version)
            params=[platform,conversation_id]
            if follow:
                rows=list(reversed(db.execute('SELECT * FROM messages WHERE platform=? AND conversation_id=? ORDER BY timestamp DESC,id DESC LIMIT 200',params).fetchall()))
            else:
                # Keep the exact displayed time window even if its anchor is deleted.
                rows=db.execute('''SELECT * FROM messages WHERE platform=? AND conversation_id=?
                    AND (timestamp,id)>=(?,?) AND (timestamp,id)<=(?,?) ORDER BY timestamp,id LIMIT 200''',
                    params+[first_timestamp,first_id,last_timestamp,last_id]).fetchall()
            return dict(platform=platform,conversation_id=conversation_id,messages=items(db,rows),
                        **edges(db,platform,conversation_id,rows),**version)

    @app.get('/api/chat-context')
    def context(request:Request,anchor_message_id:int|None=None,platform:str|None=None,
                conversation_id:str|None=None,before:int=20,after:int=20):
        same_origin(request)
        if not 0<=before<=50 or not 0<=after<=50:raise HTTPException(400,'上下文范围为0–50')
        # Capture before reading the page: a concurrent commit will be polled next.
        with store.connect() as db:version=water(db)
        if anchor_message_id is None:
            if platform not in ('qq','wechat') or not conversation_id:raise HTTPException(400,'缺少会话')
            with store.connect() as db:
                last=db.execute('SELECT id FROM messages WHERE platform=? AND conversation_id=? ORDER BY timestamp DESC,id DESC LIMIT 1',(platform,conversation_id)).fetchone()
            if not last:raise HTTPException(404,'会话暂无已导入记录')
            anchor_message_id=last[0]
        anchor=store.message(anchor_message_id)
        if not anchor or (platform and platform!=anchor['platform']) or (conversation_id and conversation_id!=anchor['conversation_id']):
            raise HTTPException(404,'消息不属于指定会话')
        rows=store.context(anchor_message_id,radius=max(before,after))
        position=next((i for i,row in enumerate(rows) if row['id']==anchor_message_id),None)
        if position is None:raise HTTPException(404,'原消息已删除，请选择其他消息或会话')
        rows=rows[max(0,position-before):position+after+1]
        with store.connect() as db:
            return dict(platform=anchor['platform'],conversation_id=anchor['conversation_id'],anchor_message_id=anchor_message_id,
                        messages=items(db,rows),**edges(db,anchor['platform'],anchor['conversation_id'],rows),**version)

    @app.get('/media/{message_id}/{index}')
    def media(request:Request,message_id:int,index:int):
        same_origin(request)
        message=store.message(message_id)
        item=media_at(message['media'],index) if message else None
        if item is None:raise HTTPException(404,'媒体不可用')
        try:path=media_path(item)
        except ValueError:path=None
        if not path:raise HTTPException(404,'媒体不可用')
        return FileResponse(path,media_type=item['mime'],headers={'Cache-Control':'no-store',
            'X-Content-Type-Options':'nosniff','Cross-Origin-Resource-Policy':'same-origin'})
