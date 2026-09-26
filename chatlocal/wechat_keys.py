"""Bounded legacy WCDB raw-key fallback, after the installed primary reader.

Some WeChat versions keep SQLCipher x'key+salt' literals directly in memory.
Only keys authenticated against the selected local DB pages are returned. No
process dumps, candidate strings, account IDs or keys go to diagnostic logs.
"""
import ctypes
import re
import sys
import time

LITERAL=re.compile(rb"[xX]'([0-9a-fA-F]{64}(?:[0-9a-fA-F]{32})?)'")
WIDE_LITERAL=re.compile(rb"[xX]\x00'\x00((?:[0-9a-fA-F]\x00){64}(?:(?:[0-9a-fA-F]\x00){32})?)'\x00")
MAX_BYTES=1024**3
MAX_SECONDS=25
MAX_CANDIDATES=512
CHUNK=1024**2


def candidates(chunk):
    for match in LITERAL.finditer(chunk):yield bytes.fromhex(match.group(1).decode('ascii'))
    for match in WIDE_LITERAL.finditer(chunk):yield bytes.fromhex(match.group(1).replace(b'\0',b'').decode('ascii'))


def authenticated_keys(chunks,pages,verify,stats):
    """Pure streaming matcher shared by the Windows reader and isolated tests."""
    found={};seen=set();started=time.monotonic()
    for chunk in chunks:
        if time.monotonic()-started>MAX_SECONDS:stats['limited']=True;break
        for material in candidates(chunk):
            if material in seen:continue
            if len(seen)>=MAX_CANDIDATES:stats['limited']=True;return found
            seen.add(material)
            key=material[:32]
            for rel,page in pages.items():
                if rel in found:continue
                # A salt-bearing candidate must match this DB, not another
                # account or a similarly named database. HMAC remains decisive.
                if len(material)==48 and material[32:]!=page[:16]:continue
                if verify(key,page):found[rel]=key
            if len(found)==len(pages):return found
    return found


def memory_chunks(reader,wx,stats):
    k32=wx._k32
    k32.CloseHandle.argtypes=[ctypes.c_void_p]
    k32.CloseHandle.restype=ctypes.c_int
    deadline=time.monotonic()+MAX_SECONDS
    for pid in reader._find_weixin_pids():
        stats['processes']+=1
        handle=k32.OpenProcess(0x0010|0x0400,False,pid)
        if not handle:stats['blocked']+=1;continue
        try:
            address=0
            while address<0x800000000000:
                if time.monotonic()>deadline or stats['bytes']>=MAX_BYTES:stats['limited']=True;return
                mbi=wx._MBI()
                if not k32.VirtualQueryEx(handle,ctypes.c_void_p(address),ctypes.byref(mbi),ctypes.sizeof(mbi)):break
                base=mbi.BaseAddress or 0;size=mbi.RegionSize;end=base+size
                if end<=address:break
                address=end
                if mbi.State!=0x1000 or mbi.Protect&0x100 or not ((mbi.Protect&0xff)&0xee):continue
                tail=b''
                for offset in range(0,size,CHUNK):
                    if time.monotonic()>deadline or stats['bytes']>=MAX_BYTES:stats['limited']=True;return
                    length=min(CHUNK,size-offset,MAX_BYTES-stats['bytes'])
                    buffer=ctypes.create_string_buffer(length);count=ctypes.c_size_t()
                    stats['bytes']+=length
                    success=k32.ReadProcessMemory(handle,ctypes.c_void_p(base+offset),buffer,length,ctypes.byref(count))
                    if not success or not count.value:tail=b'';continue
                    data=buffer.raw[:count.value]
                    yield tail+data
                    tail=data[-256:] if count.value==length else b''
        finally:k32.CloseHandle(handle)


def recover_legacy_keys(reader,existing):
    from wechatauto import db as wx
    pages={}
    for rel,path,_ in reader._db_files:
        if rel in existing:continue
        try:
            with open(path,'rb') as stream:page=stream.read(wx.PAGE_SZ)
        except OSError:continue
        if len(page)==wx.PAGE_SZ and not page.startswith(b'SQLite format 3\0'):pages[rel]=page
    if not pages:return {}
    stats=dict(processes=0,blocked=0,bytes=0,limited=False)
    chunks=memory_chunks(reader,wx,stats)
    try:found=authenticated_keys(chunks,pages,wx._verify_enc_key,stats)
    finally:chunks.close()
    # Aggregate, shareable facts only. Never print exception repr/candidates.
    print('[Tulpa] 微信旧版密钥兼容检查：进程=%d，无法读取=%d，扫描=%.1f MiB，验证通过=%d/%d，达到上限=%s' %
          (stats['processes'],stats['blocked'],stats['bytes']/1024**2,len(found),len(pages),stats['limited']),file=sys.stderr)
    # Returning no keys must leave the upstream cfg/account-selection fallbacks
    # available. Failure is reported only when the reader finally opens a DB.
    return found
