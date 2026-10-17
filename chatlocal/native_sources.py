"""Explicit native client roots and private key-file references, per installation."""
import json
import os
from pathlib import Path
import stat

from .config import ROOT
from .snowluma_secure import atomic_json
from .platform_lock import file_lock


def source_settings(root=ROOT):
    path=Path(root)/'data/native-sources.json'
    raw=json.loads(path.read_text('utf-8')) if path.exists() else {}
    return dict(platforms={p:dict(source=raw.get(p,{}).get('source','auto'),data_root=raw.get(p,{}).get('data_root',''),
        keys_file=raw.get(p,{}).get('keys_file',''),configured=bool(raw.get(p,{}).get('data_root')))
        for p in ('qq','wechat')})


def save_source(platform,body,root=ROOT):
    if platform not in ('qq','wechat') or set(body)-{'data_root','keys_file','source'} or not body:
        raise ValueError('请选择平台及客户端数据根目录。')
    previous=source_settings(root)['platforms'][platform]
    source=body.get('source',previous.get('source','auto'))
    if source not in ('auto','database','snowluma') or platform!='qq' and source=='snowluma':
        raise ValueError('请选择有效的数据来源。')
    values=dict(source=source)
    for key in ('data_root','keys_file'):
        value=body.get(key,previous.get(key,''))
        if not isinstance(value,str) or any(ord(c)<32 for c in value):raise ValueError('本机路径无效。')
        if value:
            path=Path(value).expanduser()
            if not path.is_absolute():raise ValueError('请使用绝对路径。')
            path=path.resolve(strict=True)
            if key=='data_root' and not path.is_dir():raise ValueError('数据根目录不存在。')
            if key=='keys_file':read_keys(path)
            value=str(path)
        values[key]=value
    root=Path(root)
    with file_lock(root/'.tmp/native-sources.lock',blocking=True):
        all_values=source_settings(root)['platforms'];all_values[platform]=values
        atomic_json(root/'data/native-sources.json',all_values)
    return source_settings(root)


def configured_root(platform):
    value=source_settings()['platforms'][platform]['data_root']
    if not value:return None
    path=Path(value)
    if not path.is_dir():raise ValueError('配置的客户端目录不可用；未自动切换其他来源。')
    return path


def read_keys(path):
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    with os.fdopen(fd,'rb') as stream:
        st=os.fstat(stream.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_size>1024*1024:raise ValueError('密钥文件格式或大小无效。')
        if os.name!='nt' and (st.st_uid!=os.getuid() or st.st_mode&0o077):raise ValueError('密钥文件须归当前用户所有，权限为 600。')
        value=json.loads(stream.read())
    if not isinstance(value,dict):raise ValueError('密钥文件须为 JSON 对象。')
    return value


def supplied_keys(platform):
    value=source_settings()['platforms'][platform]['keys_file']
    return read_keys(value) if value else {}
