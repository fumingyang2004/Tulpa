"""Synthetic HMAC and Windows memory API regression; never scans real QQ."""
import ctypes
import hashlib
import hmac
import io
import json
import logging
import os
from pathlib import Path
import struct
import sys
import tempfile
import time
from types import ModuleType
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'tools/qq-reader')]
import qq_passive_keys as passive
from reader_patches import patch_qq_passive_scan
from chatlog_keeper import qq_db as original


def page_for(key, combo):
    kdf, algorithm, reserve=combo
    page=bytearray(hashlib.sha512(b'fixture page').digest()*64)
    salt=bytes(page[:16]);mixed=bytes(n^0x3a for n in salt)
    aes=hashlib.pbkdf2_hmac(kdf,key,salt,4000,dklen=32)
    mac=hashlib.pbkdf2_hmac(kdf,aes,mixed,2,dklen=32)
    tag=hmac.new(mac,bytes(page[16:4096-reserve+16])+struct.pack('<I',1),algorithm).digest()
    page[4096-reserve+16:4096-reserve+16+len(tag)]=tag
    return b'\0'*1024+bytes(page)


class FakeMemory:
    def __init__(self,size,parts=(),denied=False):
        self.size=size;self.parts=parts;self.denied=denied
        self.reads=[];self.closed=0;self.error=0
    def OpenProcess(self,*args):
        self.error=5 if self.denied else 0
        return 0 if self.denied else 11
    def VirtualQueryEx(self,handle,address,output,length):
        if (address.value or 0)>=self.size:
            self.error=87
            return 0
        info=passive.MemoryBasicInformation()
        info.BaseAddress=0;info.RegionSize=self.size;info.State=0x1000
        info.Protect=4;info.Type=0x20000
        ctypes.memmove(output,ctypes.byref(info),ctypes.sizeof(info))
        return ctypes.sizeof(info)
    def ReadProcessMemory(self,handle,address,buffer,length,count):
        offset=address.value or 0;self.reads.append((offset,length))
        for start,value in self.parts:
            lo=max(start,offset);hi=min(start+len(value),offset+length)
            if lo<hi:ctypes.memmove(ctypes.addressof(buffer)+lo-offset,value[lo-start:hi-start],hi-lo)
        ctypes.cast(count,ctypes.POINTER(ctypes.c_size_t)).contents.value=length
        return 1
    def CloseHandle(self,handle):self.closed+=1


def check():
    key=b'{QQ-fixture-key}'
    assert len(key)==16
    # Every representable upstream layout retains exactly the same HMAC result.
    for combo in original._VERIFY_COMBOS:
        if combo[2]<16+(20 if combo[1]=='sha1' else 64):continue
        page=page_for(key,combo)
        assert original._verify_key_qq_with_algo(key,page,*combo)
        assert passive.verify_key(key,page,original._VERIFY_COMBOS)
        assert passive.verify_key(key,page[1024:],original._VERIFY_COMBOS)
        assert not passive.verify_key(b'wrong-test-key!!',page,original._VERIFY_COMBOS)
        corrupt=bytearray(page);corrupt[1100]^=1
        assert not passive.verify_key(key,corrupt,original._VERIFY_COMBOS)
    page=page_for(key,original._VERIFY_COMBOS[0])
    with patch.object(passive.hashlib,'pbkdf2_hmac',wraps=hashlib.pbkdf2_hmac) as derive:
        assert not passive.verify_key(b'wrong-test-key!!',page,original._VERIFY_COMBOS)
        assert derive.call_count==4,derive.call_count
    assert not passive.verify_key(key,b'bad header',original._VERIFY_COMBOS)
    verified=lambda candidate,data:passive.verify_key(candidate,data,original._VERIFY_COMBOS)
    # Supply a path sentinel; the scanner must never include its value in logs.
    def scan(memory,budget=10,verify=verified):
        return passive.scan(123,Path('private-path-sentinel'),budget,kernel=memory,
            read_verification=lambda _:page,verify=verify,raise_denied=original._raise_if_windows_access_denied,
            last_error=lambda kernel:kernel.error)
    logs=io.StringIO();handler=logging.StreamHandler(logs);passive.LOGGER.addHandler(handler)
    try:
        split=passive.CHUNK_BYTES-8
        memory=FakeMemory(201*1024**2,[(split,key+b'\0')])
        assert scan(memory)==key
        assert memory.closed==1 and max(n for _,n in memory.reads)<=passive.CHUNK_BYTES
        assert len(memory.reads)==2,'Large regions are chunked, not skipped'
        key32=key*2;page32=page_for(key32,original._VERIFY_COMBOS[0])
        memory32=FakeMemory(201*1024**2,[(passive.CHUNK_BYTES-12,key32+b'\0')])
        assert passive.scan(123,Path('private-path-sentinel'),10,kernel=memory32,
            read_verification=lambda _:page32,verify=verified,
            raise_denied=original._raise_if_windows_access_denied,
            last_error=lambda kernel:kernel.error)==key32
        # The left boundary of the carry must not invent a 32-byte candidate.
        memory=FakeMemory(2*passive.CHUNK_BYTES,[(passive.CHUNK_BYTES-72,b'A'*70+b'\0')])
        seen=[]
        assert scan(memory,verify=lambda candidate,_:seen.append(candidate) or False) is None
        assert not seen,'A suffix of a longer string is not a key candidate'
        memory=FakeMemory(1024,[(32,key+b'\0'),(100,key+b'\0')])
        seen=[];scan(memory,verify=lambda candidate,_:seen.append(candidate) or False)
        assert seen==[key]
        denied=FakeMemory(100,denied=True)
        try:scan(denied)
        except original.ProcessMemoryAccessDenied:pass
        else:raise AssertionError('Permission denials must retain their specific code')
        assert 'permission_denied' in logs.getvalue() and denied.closed==0
        clock=[0.0];seen=[]
        def expensive(candidate,_):
            seen.append(candidate);clock[0]+=.06;return False
        many=FakeMemory(1024,[(32*i,('fixture-%08d'%i).encode()+b'\0') for i in range(10)])
        with patch.object(passive.time,'monotonic',side_effect=lambda:clock[0]):
            scan(many,budget=.1,verify=expensive)
        assert len(seen)==2 and 'timeout' in logs.getvalue(),'Check time inside a candidate-heavy chunk'
        with patch.object(passive,'MAX_CANDIDATES',1):
            scan(many,verify=lambda *_:False)
        assert 'candidate_limit' in logs.getvalue()
        with patch.object(passive,'MAX_BYTES',passive.CHUNK_BYTES):
            scan(FakeMemory(2*passive.CHUNK_BYTES))
        assert 'byte_limit' in logs.getvalue()
        log=logs.getvalue()
        assert key.decode() not in log and 'private-path-sentinel' not in log
        assert 'fixture-00000000' not in log and 'AAAAAAAAAAAAAAAA' not in log
    finally:
        passive.LOGGER.removeHandler(handler)
    # Test the actual patched QQDBReader schedule and public error semantics.
    source=Path(original.__file__).read_bytes()
    changed=patch_qq_passive_scan(source)
    assert patch_qq_passive_scan(changed)==changed
    patched=ModuleType('chatlog_keeper._passive_fixture');patched.__file__=original.__file__
    with patch.dict(sys.modules,{patched.__name__:patched,'chatlog_keeper._tulpa_passive_keys':passive}):
        exec(compile(changed,patched.__file__,'exec'),patched.__dict__)
        clock=[0.0];visits=[]
        def no_key(pid,db_path,timeout_s):
            visits.append((pid,timeout_s));clock[0]+=timeout_s;return None
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='qq-key-schedule-') as tmp:
            root=Path(tmp);database=root/'123/nt_qq/nt_db/nt_msg.db';database.parent.mkdir(parents=True)
            database.write_bytes(page)
            with patch.object(patched,'_get_qq_pids',return_value=[100,101,102]), \
                 patch.object(patched,'extract_key_from_qq',side_effect=no_key), \
                 patch.object(patched,'load_cached_key_for_account',return_value=None), \
                 patch.object(time,'monotonic',side_effect=lambda:clock[0]), \
                 patch.dict(os.environ,{'CHATLOG_QQ_SCAN_TOTAL_S':'120','CHATLOG_QQ_SCAN_TIMEOUT_S':'120','CHATLOG_QQ_FORCE_LIVE_KEY':'0'}):
                reader=patched.QQDBReader(data_root=root,account_id='123',live_key_timeout_s=75)
                assert not reader.is_available()
            assert [pid for pid,_ in visits]==[100,101,102] and sum(t for _,t in visits)<=75,visits
        assert patched._verify_key_qq(key,page)
    # Comparative CPU measurement on identical wrong candidates and page bytes.
    wrong=b'wrong-test-key!!';iterations=12
    started=time.perf_counter()
    for _ in range(iterations):
        any(original._verify_key_qq_with_algo(wrong,page,*combo) for combo in original._VERIFY_COMBOS)
    old=time.perf_counter()-started
    started=time.perf_counter()
    for _ in range(iterations):passive.verify_key(wrong,page,original._VERIFY_COMBOS)
    new=time.perf_counter()-started
    print(json.dumps(dict(passed=True,source='synthetic page and Win32 memory fixtures',
        hmac_layouts='upstream-compatible',derived_calls_per_wrong_candidate=4,
        old_candidate_ms=round(old*1000/iterations,3),new_candidate_ms=round(new*1000/iterations,3),
        checks=['large memory region','chunk boundary','no suffix false match','dedup','permission code',
            'in-chunk timeout','candidate and byte caps','redacted logs','per-process budget fairness']),ensure_ascii=False))


if __name__=='__main__':check()
