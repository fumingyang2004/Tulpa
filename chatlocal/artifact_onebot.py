"""Optional local OneBot read-only adapter; no installation or QQ injection."""
import hashlib
import hmac
import json
import os
import time
from urllib.parse import urlparse

import httpx
from dotenv import dotenv_values
from .config import ROOT, DATA
from .artifacts import ArtifactError
from .artifact_metadata import clean_descriptor


def config():
    values=dotenv_values(ROOT/'.env')
    result={key:os.environ.get(key,values.get(key) or '').strip() for key in
            ('ARTIFACT_ONEBOT_URL','ARTIFACT_ONEBOT_TOKEN','ARTIFACT_ONEBOT_EVENT_SECRET')}
    from .onebot import configuration
    shared=configuration()
    result.update(ARTIFACT_ONEBOT_URL=shared['url'],ARTIFACT_ONEBOT_TOKEN=shared['token'])
    return result


class OneBot:
    def __init__(self,client=None):
        self.client=client
        self.config=config();self.url=self.config['ARTIFACT_ONEBOT_URL'].rstrip('/')
        if client is not None:self.url=client.url
        parsed=urlparse(self.url)
        if not self.url:raise ArtifactError('未配置本机 OneBot。当前只发现本地文件消息，不代表完整QQ群文件目录。')
        if parsed.scheme not in ('http','https') or parsed.hostname not in ('127.0.0.1','localhost','::1') or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ArtifactError('OneBot接口须为本机localhost地址。')

    def call(self,action,payload):
        if action not in {'get_login_info','get_version_info','get_group_file_system_info','get_group_root_files','get_group_files_by_folder','get_group_file_url',
                          '_get_group_notice','get_essence_msg_list'}:
            raise ArtifactError('不允许调用非只读OneBot接口。')
        if self.client is not None:
            from .onebot import OneBotError
            try:return self.client.call(action,payload)
            except OneBotError as exc:raise ArtifactError(str(exc)) from None
        try:
            with httpx.Client(timeout=20,trust_env=False) as client:
                response=client.post(self.url+'/'+action,json=payload,headers={'Authorization':'Bearer '+self.config['ARTIFACT_ONEBOT_TOKEN']})
                response.raise_for_status()
                if len(response.content)>8*1024*1024:raise ArtifactError('目录响应超过8 MiB上限。')
                result=response.json()
                if result.get('status')!='ok' or result.get('retcode')!=0:raise ArtifactError('OneBot只读接口返回失败，原目录保留。')
                return result.get('data')
        except (httpx.HTTPError,ValueError,TypeError) as exc:
            if isinstance(exc,ArtifactError):raise
            raise ArtifactError('无法读取OneBot接口，请检查本机地址、Token和登录状态。') from None

    def account(self,cid):
        parts=cid.split(':')
        if len(parts)!=3 or parts[1]!='group' or not parts[2].isdigit():raise ArtifactError('需要当前QQ群会话ID。')
        info=self.call('get_login_info',{})
        if str(info.get('user_id'))!=parts[0]:raise ArtifactError('OneBot账号与所选QQ来源账号不一致。')
        return parts[2]

    def reconcile(self,store,cid,name):
        group=self.account(cid);started=time.time()
        summary=self.call('get_group_file_system_info',{'group_id':group})
        count=summary.get('file_count')
        if type(count) is not int or not 0<=count<=10000:raise ArtifactError('群文件数量未知或超过10000，不能确认完整目录。')
        queue=[('', '/')];visited=set();files={}
        while queue:
            folder,path=queue.pop(0)
            if folder in visited:raise ArtifactError('目录循环，未替换原目录。')
            visited.add(folder)
            if len(visited)>300 or time.time()-started>120:raise ArtifactError('目录超过300文件夹/120秒预算，未替换原目录。')
            payload=dict(group_id=group,file_count=10000)
            if folder:payload['folder_id']=folder
            data=self.call('get_group_files_by_folder' if folder else 'get_group_root_files',payload)
            if not isinstance(data,dict) or not isinstance(data.get('files'),list) or not isinstance(data.get('folders'),list):
                raise ArtifactError('接口目录格式不兼容。')
            for f in data['files']:
                if not f.get('file_id') or not f.get('file_name'):raise ArtifactError('接口未提供稳定file_id或文件名。')
                f=dict(f,folder=path);files[str(f['file_id'])]=f
            for child in data['folders']:
                fid=child.get('folder_id');label=child.get('folder_name')
                if not isinstance(fid,str) or not isinstance(label,str):raise ArtifactError('文件夹字段不兼容。')
                queue.append((fid,path+label+'/'))
        if len(files)!=count:raise ArtifactError(f'目录仅返回{len(files)}个文件，服务报告{count}个；可能被截断，未标记旧文件删除。')
        with store.connect() as db:
            db.execute("UPDATE artifact_sources SET remote_status='absent' WHERE platform='qq' AND source_type='qq_group_file' AND conversation_id=?",(cid,))
            for f in files.values():upsert(db,cid,name,f)
            db.execute('''INSERT INTO artifact_inventory(platform,conversation_id,conversation,status,last_success,last_attempt,detail)
                VALUES('qq',?,?,'ok',?,?,?) ON CONFLICT(platform,conversation_id) DO UPDATE SET status='ok',last_success=excluded.last_success,
                last_attempt=excluded.last_attempt,detail=excluded.detail''',(cid,name,time.time(),started,f'{len(files)}个文件，{len(visited)}个目录；仅元数据'))
        return dict(files=len(files),folders=len(visited),seconds=round(time.time()-started,3))

    def download(self,row,target,cap):
        try:return self._download(row,target,cap)
        except httpx.HTTPError:raise ArtifactError('QQ文件下载连接失败，未缓存；稍后重试将重新取得地址。') from None

    def _download(self,row,target,cap):
        group=self.account(row['conversation_id'])
        result=self.call('get_group_file_url',dict(group_id=group,file_id=row['file_id']))
        url=result.get('url') if isinstance(result,dict) else None
        started=time.monotonic()
        with httpx.Client(timeout=30,trust_env=False) as client:
            for _ in range(5):
                parsed=urlparse(url or '')
                host=(parsed.hostname or '').lower()
                if parsed.scheme not in ('http','https') or parsed.username or parsed.password or parsed.port not in (None,80,443) or not any(host==d or host.endswith('.'+d) for d in ('qq.com','qpic.cn','gtimg.com','gtimg.cn')):
                    raise ArtifactError('接口下载地址不属于允许的QQ资源域名，未下载。')
                with client.stream('GET',url) as response:
                    if response.is_redirect:
                        from urllib.parse import urljoin
                        url=urljoin(url,response.headers['location']);continue
                    try:response.raise_for_status()
                    except httpx.HTTPError:raise ArtifactError('QQ文件暂无法下载，可能已过期或被删除。') from None
                    length=response.headers.get('content-length')
                    if length and int(length)>cap:raise ArtifactError('下载文件超过大小限制。')
                    size=0
                    with target.open('wb') as out:
                        for block in response.iter_bytes(65536):
                            size+=len(block)
                            if size>cap or time.monotonic()-started>90:raise ArtifactError('下载超过大小/时间预算，已停止。')
                            out.write(block)
                    return
        raise ArtifactError('下载跳转次数过多。')


def upsert(db,cid,name,f):
    if not isinstance(f.get('file_id'),str) or not f['file_id'].strip():raise ArtifactError('群文件缺少稳定file_id。')
    d=clean_descriptor(dict(filename=f.get('file_name'),size=f.get('file_size')))
    if not d:raise ArtifactError('群文件名称无效。')
    now=time.time();ts=f.get('upload_time')
    # Keep only metadata, never temporary download URLs.
    metadata={k:f[k] for k in ('modify_time','dead_time','download_times') if k in f}
    db.execute('''INSERT INTO artifact_sources(source_key,platform,source_type,conversation_id,conversation,sender,sender_id,
        file_id,busid,folder,filename,extension,size,timestamp,metadata_json,availability,remote_status,discovered_at,last_seen_at)
        VALUES(?,'qq','qq_group_file',?,?,?,?,?,?,?,?,?,?,?,?, 'METADATA_ONLY','present',?,?)
        ON CONFLICT(source_key) DO UPDATE SET filename=excluded.filename,folder=excluded.folder,sender=excluded.sender,
        extension=excluded.extension,sender_id=excluded.sender_id,busid=excluded.busid,timestamp=excluded.timestamp,
        availability=CASE WHEN artifact_sources.size IS excluded.size THEN artifact_sources.availability ELSE 'METADATA_ONLY' END,
        size=excluded.size,metadata_json=excluded.metadata_json,remote_status='present',last_seen_at=excluded.last_seen_at,
        sha256=CASE WHEN artifact_sources.size IS excluded.size THEN artifact_sources.sha256 ELSE NULL END''',
        (f'qq_group_file:{cid}:{f["file_id"]}',cid,name,str(f.get('uploader_name') or f.get('uploader') or '未知上传者'),
         str(f.get('uploader') or ''),str(f['file_id']),str(f.get('busid','')),f.get('folder','/'),d['filename'],d['extension'],
         d.get('size'),int(ts)*1000 if isinstance(ts,(int,float)) and ts>0 else None,json.dumps(metadata),now,now))


def receive_event(store,body,signature):
    secret=config()['ARTIFACT_ONEBOT_EVENT_SECRET']
    if not secret or not hmac.compare_digest('sha1='+hmac.new(secret.encode(),body,hashlib.sha1).hexdigest(),signature or ''):
        raise ArtifactError('事件签名无效。')
    event=json.loads(body)
    if event.get('post_type')!='notice' or event.get('notice_type')!='group_upload':return dict(ignored=True)
    cid=f"{event.get('self_id')}:group:{event.get('group_id')}"
    with store.connect() as db:
        known=db.execute("SELECT conversation FROM artifact_inventory WHERE platform='qq' AND conversation_id=?",(cid,)).fetchone()
        if not known:return dict(ignored=True,note='未启用此群的目录同步')
        f=event.get('file',{})
        upsert(db,cid,known[0],dict(file_id=f.get('id'),file_name=f.get('name'),file_size=f.get('size'),busid=f.get('busid'),
            uploader=event.get('user_id'),upload_time=event.get('time')))
    return dict(accepted=True)
