"""Native APSW/encrypted-page fixtures; no client messages or model calls.

Each platform runs in its own process, exactly as the production reader does.
QQ fixture has real overflow pages on both sides of the shifted lock page.
"""
import argparse
import ctypes
import hashlib
import hmac
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def encrypt(page, number, key, platform):
    from Crypto.Cipher import AES
    salt = b'fixture-salt-123'
    iv = b'fixture-iv-12345'
    reserve = 48 if platform == 'qq' else 80
    aes = hashlib.pbkdf2_hmac('sha512', key, salt, 4000, 32) if platform == 'qq' else key
    algo = 'sha1' if platform == 'qq' else 'sha512'
    mac = hashlib.pbkdf2_hmac(algo if platform == 'wechat' else 'sha512', aes, bytes(v ^ 0x3a for v in salt), 2, 32)
    prefix = 16 if number == 1 else 0
    body = AES.new(aes, AES.MODE_CBC, iv).encrypt(bytes(page[prefix:4096-reserve]))
    tag = hmac.new(mac, body + iv + struct.pack('<I',number), algo).digest()
    return (salt if prefix else b'') + body + iv + tag + bytes(reserve-16-len(tag))


def sqlite_pages(folder, reserve):
    import apsw
    path = folder / 'plain.db'
    db = apsw.Connection(str(path))
    number = ctypes.c_int(reserve)
    assert db.file_control('main', apsw.SQLITE_FCNTL_RESERVE_BYTES, ctypes.addressof(number))
    db.execute('PRAGMA page_size=4096')
    db.execute('CREATE TABLE fixture(value TEXT)')
    db.execute('INSERT INTO fixture VALUES(?)', ('a' * 15000,))
    db.close()
    data = path.read_bytes()
    return {i//4096+1: bytearray(data[i:i+4096]) for i in range(0,len(data),4096)}


def qq(folder):
    import apsw
    from live_vfs import ReadView, configure_qq_worker, SourceBusy
    configure_qq_worker()
    pages = sqlite_pages(folder,48)
    # One leaf record, its final four bytes point to the first overflow page.
    assert pages[2][0] == 13 and len(pages) == 5
    assert struct.unpack_from('>I',pages[2],4044)[0] == 3
    relocated = {3:262143,4:262145,5:262146}
    struct.pack_into('>I',pages[2],4044,relocated[3])
    struct.pack_into('>I',pages[1],28,262146)
    for old in relocated:
        next_page = struct.unpack_from('>I',pages[old])[0]
        struct.pack_into('>I',pages[old],0,relocated.get(next_page,0))
    path = folder/'encrypted.db'
    path.touch()
    subprocess.run(['fsutil','sparse','setflag',str(path)],capture_output=True,check=True)
    key = b'fixture-password'
    with path.open('r+b') as stream:
        stream.truncate(1024+262146*4096)
        for old,page in pages.items():
            number = relocated.get(old,old)
            stream.seek(1024+(number-1)*4096);stream.write(encrypt(page,number,key,'qq'))
    before = path.stat().st_mtime_ns
    with ReadView(path,key,'qq') as view:
        assert list(view.db.execute('SELECT value FROM fixture')) == [('a'*15000,)]
        assert view.view.bytes_read < 65536 and view.view.decrypted == 5
        try:view.db.execute("INSERT INTO fixture VALUES('bad')")
        except apsw.ReadOnlyError:pass
        else:raise AssertionError('source write allowed')
        view.view.assert_fresh()
    assert path.stat().st_mtime_ns == before
    # A committed WAL version of an encrypted overflow page must be used.
    from chatlog_keeper.core._wal import _checksum_bytes
    plain = pages[4][:];plain[4:10]=b'update'
    encrypted=encrypt(plain,262145,key,'qq')
    header=struct.pack('>IIIIII',0x377f0682,3007000,4096,0,12,34)
    checksum=_checksum_bytes(header,big_endian=False)
    frame=struct.pack('>II',262145,262146)
    end=_checksum_bytes(frame+encrypted,checksum,big_endian=False)
    wal=Path(str(path)+'-wal')
    wal.write_bytes(header+struct.pack('>II',*checksum)+frame+header[16:24]+struct.pack('>II',*end)+encrypted)
    with ReadView(path,key,'qq') as view:
        result=list(view.db.execute('SELECT value FROM fixture'))[0][0]
        assert result.count('update')==1 and len(result)==15000
        with path.open('r+b') as stream:
            stream.seek(1024+(262144-1)*4096);stream.write(b'x')
        try:view.view.assert_fresh()
        except SourceBusy:pass
        else:raise AssertionError('source race accepted')
    try:ReadView(path,key,'qq')
    except ValueError as exc:assert str(exc)=='qq_live_lock_page_invalid'
    else:raise AssertionError('bad placeholder accepted')
    # The host Python/application SQLite stays standard despite QQ APSW mode.
    import sqlite3
    dll=ctypes.WinDLL('sqlite3.dll')
    assert dll.sqlite3_test_control(11,0)==0x40000000
    print('PASS QQ native encrypted >1 GiB: overflow across lock page, committed WAL, readonly, source fence, invalid lock rejected, main SQLite unchanged; <64 KiB read.')


def wechat(folder):
    from live_wechat import LiveWeChatDB
    from live_vfs import ReadView
    from wechatauto.db import WeChatDB
    from wechatauto.media import MediaDownloader
    from reader_media import WeChatMedia
    import reader_media
    from chatlocal import media as image_cache
    account='wxid_fixture_abcd';base=folder/account/'db_storage';(base/'message').mkdir(parents=True)
    pages=sqlite_pages(folder,80);key=bytes(range(32))
    path=base/'message/message_0.db'
    path.write_bytes(b''.join(encrypt(p,n,key,'wechat') for n,p in pages.items()))
    cache=folder/'keys.json';cache.write_text(json.dumps({'message/message_0.db':(b'x'*32).hex()}))
    Path(str(cache)+'.bak').write_text(json.dumps({'message/message_0.db':key.hex()}))
    fail=AssertionError('hot path attempted expensive key/media scan')
    with patch.object(WeChatDB,'extract_master_key',side_effect=fail),patch.object(WeChatDB,'extract_keys',side_effect=fail), \
         patch.object(WeChatDB,'_stable_key_file',return_value=None),patch.object(WeChatDB,'_save_keys',side_effect=fail):
        reader=LiveWeChatDB(db_dir=str(folder),account=account,keys_file=str(cache),workdir=str(folder/'work'))
        rel=reader._message_dbs()[0];assert reader._keys[rel]==key
        with ReadView(path,key,'wechat') as view:
            assert list(view.db.execute('SELECT length(value) FROM fixture'))==[(15000,)]
        new=base/'message/message_1.db';new.write_bytes(path.read_bytes())
        rels=reader._message_dbs();missing=[r for r in rels if r not in reader._keys];assert len(missing)==1
        cache.write_text(json.dumps({r:key.hex() for r in rels}))
        assert len(reader._message_dbs())==2 and not reader.unkeyed
        with patch.object(Path,'rglob',side_effect=fail),patch.object(MediaDownloader,'_scan_aes_key',side_effect=fail), \
             patch.object(MediaDownloader,'_probe_ct',side_effect=fail),patch.object(MediaDownloader,'_collect_templates',side_effect=fail):
            media=WeChatMedia(reader,dict(source_db=str(base)),load_stickers=False,live=True)
            assert media.parse(dict(type_code=47))[1][0]['status']=='omitted'
        # A V2 attachment arrives later. Use the cached key on this exact image,
        # no global probe or memory scan; retries keep the native message intact.
        import io,time
        from datetime import datetime
        from PIL import Image
        from Crypto.Cipher import AES
        image=io.BytesIO();Image.new('RGB',(20,12),'red').save(image,'JPEG');jpeg=image.getvalue()
        image_key='0123456789abcdef';chat='fixture@chatroom';md5='a'*32;now=int(time.time())
        target=folder/account/'msg/attach'/hashlib.md5(chat.encode()).hexdigest()/datetime.fromtimestamp(now).strftime('%Y-%m')/'Img'/(md5+'_h.dat')
        target.parent.mkdir(parents=True)
        # AES plaintext includes its padding; the final JPEG bytes use XOR.
        cipher=AES.new(image_key.encode(),AES.MODE_ECB).encrypt(jpeg[:16]+bytes([16])*16)
        target.write_bytes(b'\x07\x08V2\x08\x07'+struct.pack('<II',16,2)+b'\0'+cipher+jpeg[16:-2]+bytes(v^0x88 for v in jpeg[-2:]))
        local=folder/'local';keys=local/'wechat-reader/work/image_keys.json';keys.parent.mkdir(parents=True)
        keys.write_text(json.dumps({account:image_key}))
        with patch.object(reader_media,'DATA',local),patch.object(image_cache,'DATA',local), \
             patch.object(MediaDownloader,'_scan_aes_key',side_effect=fail),patch.object(MediaDownloader,'_collect_templates',side_effect=fail):
            media=WeChatMedia(reader,dict(source_db=str(base)),load_stickers=False,live=True)
            raw=dict(type_code=3,chat=chat,md5=md5,create_time=now,content='')
            assert media.parse(raw)[1][0]['status']=='available'
            assert media.parse(raw)[1][0]['width']==20
    print('PASS WeChat native encrypted pages, stale cache fallback, new shard detection/cache reload, no memory scans or whole media walk at startup, stickers default omitted.')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--package',type=Path,default=ROOT);p.add_argument('--child',choices=['qq','wechat']);a=p.parse_args()
    if a.child:
        sys.path[:0]=[str(a.package/'scripts'),str(a.package),str(a.package/'tools/wechat-reader'),str(a.package/'tools/qq-reader')]
        (a.package/'.tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=a.package.resolve()/'.tmp',prefix='live-runtime-') as name:
            (qq if a.child=='qq' else wechat)(Path(name))
    else:
        runtime=a.package/'runtime/python.exe' if (a.package/'runtime/python.exe').exists() else Path(sys.executable)
        for platform in ('qq','wechat'):
            subprocess.run([str(runtime),str(Path(__file__).resolve()),'--package',str(a.package),'--child',platform],check=True,timeout=40,env=dict(os.environ,PYTHONUTF8='1'))
