"""User-selected optional component jobs; no installation on status requests."""
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import threading
import uuid

from fastapi import HTTPException
from .config import ROOT
from .platform_lock import file_lock
from .platform_process import owned_popen, terminate_group


def component_status(root=ROOT):
    root=Path(root)
    from .linux_service import service_status
    from .optional_components import VoiceModelInstaller
    voice=VoiceModelInstaller(root).status()
    executable=root/('tools/whispercpp/Release/whisper-cli.exe' if os.name=='nt' else 'tools/whispercpp/bin/whisper-cli')
    specs={'agent':['deepseek_harness'],'ocr':['rapidocr','onnxruntime'],'office':['docling','pypdf'],'readers':['Crypto','zstandard','numpy']}
    result={name:dict(installed=all(importlib.util.find_spec(m) is not None for m in modules),modules=modules) for name,modules in specs.items()}
    result['readers']['installed'] &= (root/'tools/reader-manifest.json').is_file()
    result['ocr']['installed'] &= all((root/'.cache/rapidocr'/name).is_file() for name in
        ('PP-OCRv6_det_small.onnx','PP-OCRv6_rec_small.onnx','ch_ppocr_mobile_v2.0_cls_mobile.onnx'))
    result['readers'].update(architecture=platform.machine(),active_key_supported=platform.machine() in ('x86_64','AMD64'),
        detail='Linux 自动启动取钥按上游 x86_64 已验证版本开放；其他架构可使用经数据库验证的已有密钥。')
    if sys.platform == 'darwin':
        result['readers'].update(active_key_supported=False,
            detail='macOS 支持已有密钥与只读导入；主动恢复需另装隔离组件并核对客户端版本，见 macos/README.md。')
    result['voice']=dict(installed=voice['status']=='installed' and executable.is_file(),model=voice,
        executable=executable.is_file(),build_tools=all(shutil.which(p) for p in ('cmake','c++','cc')) or
        all(importlib.util.find_spec(p) is not None for p in ('cmake','ninja','ziglang')))
    result['service']=service_status(root)
    return result


class ComponentJobs:
    def __init__(self,root=ROOT):
        self.root=Path(root);self.lock=threading.RLock();self.jobs={};self.process=None;self.closed=False

    def start(self,name,*,command=None):
        if command is None and name not in ('readers','voice','ocr','office'):raise ValueError('未知组件。')
        with self.lock:
            if self.closed:raise HTTPException(503,'服务正在退出。')
            if any(j['status']=='running' for j in self.jobs.values()):raise HTTPException(409,'已有组件安装任务正在运行。')
            jid=uuid.uuid4().hex
            self.jobs[jid]=dict(id=jid,status='running',detail='正在安装 '+name,component=name)
            for old in list(self.jobs)[:-50]:self.jobs.pop(old,None)
            threading.Thread(target=self._run,args=(jid,name,command),daemon=True,name='component-install').start()
        return dict(job_id=jid)

    def extract(self,source,body):
        if source not in ('qq','wechat') or set(body)-{'account','method','confirm_start_client'}:raise ValueError('取钥参数无效。')
        method=body.get('method','passive')
        if method not in ('passive','active'):raise ValueError('取钥模式无效。')
        if method=='active' and body.get('confirm_start_client') is not True:raise ValueError('主动取钥需要明确确认启动客户端。')
        if method=='active' and platform.machine() not in ('x86_64','AMD64'):
            raise ValueError('当前架构尚未验证主动取钥，请使用已验证密钥或被动读取。')
        account=body.get('account')
        from .client_accounts import account_id
        if account is not None:account=account_id(account)
        command=[sys.executable,str(self.root/'scripts/extract_native_keys.py'),source,'--method',method]
        if account:command+=['--account',account]
        if method=='active':command+=['--confirm-start-client']
        return self.start('native-'+source,command=command)

    def _run(self,jid,name,command=None):
        proc=None
        try:
            with file_lock(self.root/'.tmp/component-install.lock'):
                command=command or [sys.executable,str(self.root/'scripts/install_linux_component.py'),name]
                with self.lock:
                    if self.closed:raise ValueError('安装已取消。')
                    self.process=owned_popen(command,cwd=self.root,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                    proc=self.process
                code=proc.wait(timeout=3600)
                if code:raise ValueError('本机任务未完成，请检查依赖、客户端版本与读取权限，并在 CLI 重试以查看诊断。')
                self.jobs[jid].update(status='completed',detail='本机任务已完成')
        except Exception as exc:
            self.jobs[jid].update(status='failed',error=str(exc) if isinstance(exc,ValueError) else '安装进程失败（'+type(exc).__name__+'）。',detail='原有聊天数据保留')
        finally:
            with self.lock:
                if proc and proc.poll() is None:terminate_group(proc)
                if self.process is proc:self.process=None

    def status(self,jid):
        with self.lock:
            if jid not in self.jobs:raise HTTPException(404,'安装任务不存在，服务可能已重启；请刷新组件状态。')
            return dict(self.jobs[jid])

    def close(self):
        with self.lock:
            self.closed=True
            if self.process and self.process.poll() is None:terminate_group(self.process)
