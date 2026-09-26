"""Consume pinned reader metadata and local client caches; never download media."""
import collections
import hashlib
import io
import json
import re
import os
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET
from PIL import Image

from chatlocal.config import DATA, ROOT
from chatlocal.media import cache_image,unavailable,omitted_sticker,MAX_MEDIA_BYTES


def xml_message(content):
    if not isinstance(content,str) or len(content)>1024*1024: return None
    try: return ET.fromstring(content[content.index('<'):])
    except (ValueError,ET.ParseError): return None


class QQMedia:
    def __init__(self,q,source,load_stickers=True):
        self.q=q
        self.load_stickers=load_stickers
        self.index=collections.defaultdict(list)
        self.root=root=Path(source).parent/'nt_data'
        for folder in (('Pic','Emoji') if load_stickers else ('Pic',)):
            for path in (root/folder).rglob('*'):
                if path.is_file():
                    for key in {path.name.lower(),path.stem.lower(),path.stem.lower().split('_')[0]}:
                        self.index[key].append(path)
        self.cache={}

    def current_paths(self,md5,filename,timestamp=None):
        paths=list(self.index.get(str(md5).lower(),[]))+list(self.index.get(filename.lower(),[]))
        # The message commonly precedes its attachment. Probe only its month and
        # resource hash; never walk all historical pictures for each retry.
        if timestamp is not None and re.fullmatch('[a-fA-F0-9]{32}',str(md5)):
            from chatlocal.normalize import stamp
            try:month=datetime.fromtimestamp(stamp(timestamp)/1000).strftime('%Y-%m')
            except (TypeError,ValueError,OverflowError,OSError):return paths
            for kind in ('Ori','Thumb'):
                folder=self.root/'Pic'/month/kind
                paths.extend(p for p in folder.glob(md5.lower()+'*')
                             if p.stem.lower().split('_')[0]==md5.lower())
        return list(dict.fromkeys(paths))

    def resolve(self,kind,md5=None,filename='',timestamp=None):
        """Recover one declared resource; failed/incomplete files remain retryable."""
        key=(md5,filename,kind)
        if not self.load_stickers and (kind in ('sticker','gif') or filename.lower().endswith('.gif')):
            return omitted_sticker(kind,md5=md5,filename=filename)
        cached=self.cache.get(key)
        if cached and cached['status']!='unavailable':return cached
        item=unavailable(kind,md5=md5,filename=filename)
        candidates=[]
        for path in self.current_paths(md5,filename,timestamp):
            try:
                size=path.stat().st_size
                if path.is_file() and 0<size<=MAX_MEDIA_BYTES:candidates.append((path,size))
            except OSError:continue
        candidates.sort(key=lambda pair:('Ori' not in pair[0].parts,'Thumb' in pair[0].parts,-pair[1]))
        for path,_ in candidates:
            try:
                item=cache_image(path.read_bytes(),kind,load_stickers=self.load_stickers,md5=md5,filename=filename,
                    thumbnail='Thumb' in path.parts,recovered_by='chatlog-keeper protobuf + NTQQ local cache')
                break
            except (OSError,ValueError,Image.DecompressionBombError):continue
        self.cache[key]=item
        return item

    def parse(self,blob,subtype,timestamp=None):
        images=[];texts=[];quoted_texts=[];has_reply=False;reply_identity={}
        def walk(data,depth=0,quoted=False):
            nonlocal has_reply
            if depth>4 or not isinstance(data,bytes): return
            fields=self.q._proto_parse(data)
            def value(n,default=None):return fields.get(n,[(None,default)])[0][1]
            if self.q._NTQQ_MSG_REPLY_CONTAINER in fields:
                # Native quote header, not nested text element IDs (45001).
                for name,number in (('seq',47402),('sender',47403),('time',47404)):
                    v=value(number)
                    if type(v) is int and v>0:reply_identity[name]=v
            def text(n):
                v=value(n,b'');return v.decode('utf-8','replace') if isinstance(v,bytes) else ''
            filename=text(self.q._NTQQ_MSG_IMAGE_FILENAME)
            md5=value(self.q._NTQQ_MSG_FILE_MD5_BIN)
            if isinstance(md5,bytes):md5=md5.hex()
            width=value(self.q._NTQQ_MSG_IMAGE_WIDTH,0);height=value(self.q._NTQQ_MSG_IMAGE_HEIGHT,0)
            face=value(self.q._NTQQ_MSG_FACE_MD5)
            if not md5 and isinstance(face,bytes):md5=face.hex()
            if not md5:md5=text(self.q._NTQQ_MSG_FACE_HASH)
            if not quoted and (width and height or filename.lower().endswith(('.jpg','.jpeg','.png','.gif','.webp')) or face):
                kind='sticker' if subtype==4096 or face else 'image'
                images.append(self.resolve(kind,md5,filename,timestamp))
            for typ,body in fields.get(self.q._NTQQ_MSG_TEXT_PRIMARY,[]):
                if typ=='bytes' and isinstance(body,bytes):
                    (quoted_texts if quoted else texts).append(body.decode('utf-8','replace'))
            for number in (self.q._NTQQ_MSG_OUTER_WRAPPER,self.q._NTQQ_MSG_REPLY_CONTAINER):
                for typ,raw in fields.get(number,[]):
                    if typ=='bytes':
                        if number==self.q._NTQQ_MSG_REPLY_CONTAINER:has_reply=True
                        walk(raw,depth+1,quoted or number==self.q._NTQQ_MSG_REPLY_CONTAINER)
        walk(blob)
        # A quote may span text, @mention and whitespace elements. Retain the
        # entire preview; a trailing blank must not overwrite preceding text.
        quote=' '.join(part.strip() for part in quoted_texts if part.strip())
        reply=dict(content=quote[:2000] or '[引用消息正文不可用]') if has_reply else None
        if reply and len(reply_identity)==3:reply['_native_quote']=reply_identity
        # Mixed messages can repeat the same element wrapper; retain distinct images.
        unique={json.dumps(x,sort_keys=True):x for x in images}
        return ' '.join(texts),list(unique.values())[:16],reply


def resolve_qq_quote(db,spec,peer,reply):
    """Match a native quote only by exact scoped sequence + author + time.

    Text previews and element IDs are never enough to link an interaction.
    The private parser header is removed before serialization.
    """
    if not reply:return reply
    reply=dict(reply);identity=reply.pop('_native_quote',None)
    if not identity:return reply
    # 40027 is the indexed peer key for both native tables. Using group_code
    # alone would scan the entire QQ database for every quoted message.
    rows=db.execute(f'''SELECT "40001" FROM "{spec.table}" WHERE "40027"=? AND "{spec.conversation_column}"=?
      AND "40003"=? AND "40033"=? AND "40050"=? LIMIT 2''',
      (int(peer),peer,identity['seq'],identity['sender'],identity['time'])).fetchall()
    if len(rows)==1 and rows[0][0] is not None:reply['source_id']=str(rows[0][0])
    return reply


class WeChatMedia:
    def __init__(self,reader,metadata,load_stickers=True,*,live=False):
        self.load_stickers=load_stickers
        self.live=live
        from wechatauto.media import MediaDownloader
        root=Path(metadata['source_db']).parent
        self.root=root
        proxy=SimpleNamespace(account_dir=str(root),account=reader.account,wxid=reader.wxid,
            cfg_dword=reader.cfg_dword,workdir=str(DATA/'wechat-reader/work'),_find_weixin_pids=reader._find_weixin_pids)
        self.decoder=MediaDownloader(proxy,save_dir=str(DATA/'media'))
        self.index=collections.defaultdict(list)
        self.cache={}
        self.key=None;self.xor=None
        if live:
            # No process scan, recursive attachment walk or key probe here.
            # Locate a new message's own image and validate the cached key on it.
            self.index_walk=None;self.next_index=0
            return
        derived=self.decoder._derive_cfg_key()
        self.key=derived[0] if derived else self.decoder._load_persisted_key()
        if not self.key:self.key=self.decoder._scan_aes_key(monitor=False)
        self.xor=derived[1] if derived else self.decoder._install_xor_key()
        if self.key:self.decoder._persist_key(self.key)
        self.index=collections.defaultdict(list)
        for folder in (root/'msg/attach',root/'cache'):
            for path in folder.rglob('*'):
                if path.is_file():self.index[path.stem.lower().split('_')[0]].append(path)
        self.cache={}

    def live_image_key(self,path):
        # The key file is produced by the existing manual media/import path.
        # Re-read when an image is retried so recovery needs no worker restart.
        try:
            saved=json.loads(Path(self.decoder._key_store()).read_text(encoding='utf-8'))
            key=saved.get(self.decoder.db.account)
            if not isinstance(key,str) or len(key)!=16 or not key.isascii():return None
            self.decoder._key_probe=None
            if not self.decoder._probe_ct(str(path)):return None
            return key if self.decoder._validate_key(key) else None
        except (OSError,ValueError,TypeError,AttributeError):return None

    def advance_index(self):
        """Incremental fallback for unusual layouts; never block a poll on a walk."""
        if not self.live or time.monotonic()<self.next_index:return
        if self.index_walk is None:
            def walk():
                for base in (self.root/'msg/attach',self.root/'cache'):
                    for folder,dirs,files in os.walk(base,followlinks=False):
                        for name in files:yield Path(folder)/name
            self.index_walk=walk()
        until=time.monotonic()+.02
        for _ in range(256):
            path=next(self.index_walk,None)
            if path is None:
                self.index_walk=None;self.next_index=time.monotonic()+60
                break
            key=path.stem.lower().split('_')[0]
            if re.fullmatch('[a-f0-9]{32}',key) and path not in self.index[key]:self.index[key].append(path)
            if time.monotonic()>=until:break

    def current_paths(self,message,md5):
        """Probe this conversation/month directly; the startup index can be stale.

        Consume the upstream attachment layout, never invoke a download. The
        hashed conversation and validated resource ID cannot introduce paths.
        """
        paths=list(self.index.get(str(md5).lower(),[]))
        if not isinstance(md5,str) or not re.fullmatch('[a-fA-F0-9]{32}',md5):return paths
        chat=message.get('chat')
        try:month=datetime.fromtimestamp(int(message['create_time'])).strftime('%Y-%m')
        except (KeyError,TypeError,ValueError,OverflowError,OSError):return paths
        if not isinstance(chat,str) or not chat:return paths
        peer=hashlib.md5(chat.encode()).hexdigest()
        folder=self.root/'msg/attach'/peer/month/'Img'
        paths.extend(folder/(md5.lower()+suffix+'.dat') for suffix in ('_h','','_t'))
        paths.append(self.root/'cache'/month/'Message'/peer/'Bubble'/(md5.lower()+'_b.dat'))
        return list(dict.fromkeys(paths))

    def parse(self,message):
        kind=message['type_code'] & 0xffffffff
        xml=xml_message(message.get('content',''))
        reply=None
        if kind==49 and xml is not None:
            app=xml.find('.//appmsg');ref=xml.find('.//refermsg')
            if app is not None and ref is not None:
                quoted=ref.findtext('content','')
                if quoted.lstrip().startswith('<'):quoted='[引用非文本消息]'
                reply=dict(source_id=ref.findtext('svrid',''),sender=ref.findtext('displayname',''),content=quoted[:2000])
                return app.findtext('title',''),[],reply
        if kind not in (3,47):return None,[],None
        media_kind='sticker' if kind==47 else 'image'
        if kind==47 and not self.load_stickers:
            return '',[omitted_sticker('sticker')],None
        md5=message.get('md5')
        element=xml.find('.//emoji' if kind==47 else './/img') if xml is not None else None
        if not md5 and element is not None:md5=element.get('md5')
        key=(md5,media_kind)
        # Only successful/explicitly omitted media is cached. Missing or partially
        # written attachments can become readable after the message is committed.
        if key not in self.cache or self.cache[key]['status']=='unavailable':
            item=unavailable(media_kind,md5=md5)
            paths=[]
            for path in self.current_paths(message,md5):
                try:
                    size=path.stat().st_size
                    if path.is_file() and 0<size<=MAX_MEDIA_BYTES:paths.append((path,size))
                except OSError:continue
            paths=[p for p,size in sorted(paths,key=lambda pair:(pair[0].stem.endswith('_t'),not pair[0].stem.endswith('_h'),-pair[1]))]
            for path in paths:
                try:
                    if path.stat().st_size>MAX_MEDIA_BYTES:continue
                    data=path.read_bytes()
                    if path.suffix.lower()=='.dat':
                        if self.live:
                            self.key=self.live_image_key(path) if data[:6]==b'\x07\x08V2\x08\x07' else None
                            self.xor=self.decoder._derive_xor_key(str(path))
                        if data[:6]==b'\x07\x08V2\x08\x07' and not self.key:continue
                        data=self.decoder.decrypt_image(str(path),aes_key=self.key,xor_key=self.xor)
                    if not data:continue
                    original='wxgf' if data.startswith(b'wxgf') else None
                    if original:
                        # Upstream's WXGF/HEVC still-image conversion. Temporary
                        # files follow config's project-local TEMP/TMP settings.
                        data=self.decoder._wxgf_to_jpg(data)
                    if not data:continue
                    item=cache_image(data,media_kind,load_stickers=self.load_stickers,md5=md5,thumbnail=path.stem.endswith('_t'),
                        original_format=original,recovered_by='wechatauto-replica local media')
                    break
                except (OSError,ValueError,RuntimeError,Image.DecompressionBombError):continue
            self.cache[key]=item
        return '',[self.cache[key]],None
