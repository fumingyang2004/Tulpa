"""Authenticated synthetic Mac snapshots; never opens or scans a real client."""
import ctypes
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'scripts'), str(ROOT/'tools/wechat-reader')]


def encrypted_db(folder, salt, key):
    from check_live_runtime import sqlite_pages
    from Crypto.Cipher import AES
    with tempfile.TemporaryDirectory(dir=folder, prefix='plain-fixture-') as name:
        pages = sqlite_pages(Path(name), 80)
    iv = bytes(range(16))
    mac_key = hashlib.pbkdf2_hmac('sha512', key, bytes(v ^ 0x3a for v in salt), 2, 32)
    out = []
    for number, plain in pages.items():
        prefix = 16 if number == 1 else 0
        body = AES.new(key, AES.MODE_CBC, iv).encrypt(bytes(plain[prefix:4016]))
        tag = hmac.new(mac_key, body+iv+struct.pack('<I', number), hashlib.sha512).digest()
        out.append((salt if prefix else b'')+body+iv+tag)
    return b''.join(out)


def key_cache(folder, account, data):
    path = folder/(account+'.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding='utf-8')
    return path


def rejects(action):
    try:
        action()
    except (ValueError, RuntimeError):
        return
    raise AssertionError('Invalid or ambiguous account was accepted')


def check_cache(folder, wx):
    accounts = folder/'accounts'
    work = folder/'work'
    work.mkdir()
    account_a, account_b = 'wxid_fixture_aaaa', 'wxid_fixture_bbbb'
    rel = 'message/message_0.db'
    salt, key = bytes(range(16)), bytes(range(32))
    page = encrypted_db(folder, salt, key)
    paths = {}
    for account in (account_a, account_b):
        paths[account] = accounts/account/'db_storage'/rel
        paths[account].parent.mkdir(parents=True)
        paths[account].write_bytes(page)
    cache = folder/'bound-keys'
    good = key_cache(cache, account_a, {rel: key.hex()})
    generic = work/'keys.json'
    generic.write_text(json.dumps({rel: key.hex()}), encoding='utf-8')
    with patch.dict(os.environ, {'WECHATAUTO_KEYS_DIR': str(cache)}):
        fail = AssertionError('Mac reader tried a Windows or cross-account recovery path')
        with patch.object(wx.WeChatDB, 'extract_keys', side_effect=fail), \
             patch.object(wx.WeChatDB, 'extract_master_key', side_effect=fail), \
             patch.object(wx.WeChatDB, '_try_other_accounts', side_effect=fail):
            reader = wx.WeChatDB(db_dir=str(accounts), account=account_a,
                                 keys_file=str(generic), workdir=str(work))
            assert reader._keys == {rel: key} and reader.master_key is None
            assert good.stat().st_mode & 0o777 == 0o600
            assert generic.stat().st_mode & 0o777 == 0o600
            with reader._open(rel) as db:
                assert db.execute('SELECT length(value) FROM fixture').fetchall()[0][0] == 15000
                try:
                    db.execute('DELETE FROM fixture')
                except wx.sqlite3.OperationalError:
                    pass
                else:
                    raise AssertionError('Decrypted reader connection allowed a write')
            # These intentionally identical encrypted bytes prove that HMAC
            # alone cannot identify an account. No generic/other-account cache.
            other = wx.WeChatDB(db_dir=str(accounts), account=account_b,
                                keys_file=str(generic), workdir=str(work))
            assert not other._keys and other.account == account_b
            rejects(lambda: other._open(rel))
            assert not (cache/(account_b+'.json')).exists()
            rejects(lambda: wx.WeChatDB(db_dir=str(accounts)))
            rejects(lambda: wx.WeChatDB(db_dir=str(accounts), account='../fixture'))
            rejects(lambda: wx.WeChatDB(db_dir=str(accounts), account='missing'))
            alias = accounts/'wxid_alias_zzzz'
            alias.symlink_to(accounts/account_a, target_is_directory=True)
            rejects(lambda: wx.WeChatDB(db_dir=str(accounts), account=alias.name))
            linked = accounts/account_a/'db_storage/message/linked.db'
            linked.symlink_to(paths[account_b])
            assert 'message/linked.db' not in {r for r, _, _ in reader._collect_db_files()}
            linked.unlink()
            alias.unlink()
            # An incorrect account cache is preserved rather than replaced by
            # empty results; a valid generic cache still cannot rescue it.
            bad = key_cache(cache, account_b, {rel: (b'x'*32).hex()})
            before = bad.read_bytes()
            other = wx.WeChatDB(db_dir=str(accounts), account=account_b,
                                keys_file=str(generic), workdir=str(work))
            assert not other._keys and bad.read_bytes() == before
            # Invalid JSON structure and candidate shape must be safe failures.
            bad.write_text('[]', encoding='utf-8')
            assert not wx.WeChatDB(db_dir=str(accounts), account=account_b)._keys
            bad.write_text(json.dumps({rel: 5, 'other': 'not-hex'}), encoding='utf-8')
            assert not wx.WeChatDB(db_dir=str(accounts), account=account_b)._keys
            # Standard-mode legacy 48-byte entries normalize to the tested
            # 32-byte page key, without treating a master as an AES page key.
            key_cache(cache, account_a, {rel: (key+b'z'*16).hex()})
            normalized = wx.WeChatDB(db_dir=str(accounts), account=account_a,
                                     keys_file=str(generic), workdir=str(work))
            assert normalized._keys == {rel: key}
            assert all(len(v) == 64 for v in json.loads(good.read_text()).values())
            # Tampering the first-page authenticator invalidates cached keys.
            broken = bytearray(page)
            broken[4095] ^= 1
            paths[account_a].write_bytes(broken)
            assert not wx.WeChatDB(db_dir=str(accounts), account=account_a)._keys
            paths[account_a].write_bytes(page)
    assert all(paths[a].read_bytes() == page for a in paths)


def check_master(folder, wx):
    account = 'wxid_master_cccc'
    root = folder/'master-accounts'
    master = b'm'*32
    expected = {}
    for i in range(2):
        rel = f'message/message_{i}.db'
        salt = bytes([i+5])*16
        page_key = hashlib.pbkdf2_hmac('sha512', master, salt, wx.WeChatDB.KDF_ITER, 32)
        expected[rel] = page_key
        path = root/account/'db_storage'/rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encrypted_db(folder, salt, page_key))
    cache = folder/'master-keys'
    with patch.dict(os.environ, {'WECHATAUTO_KEYS_DIR': str(cache)}):
        reader = wx.WeChatDB(db_dir=str(root), account=account, master_key=master.hex(),
                             workdir=str(folder/'master-work'))
        assert reader._keys == expected and reader.master_key is None
        saved = key_cache_path = cache/(account+'.json')
        values = json.loads(saved.read_text())
        assert values == {r: k.hex() for r, k in expected.items()}
        assert master.hex() not in values.values()
        before = saved.read_bytes()
        # Authentication rejects the wrong explicit master without losing the
        # independently verified account's already saved page keys.
        again = wx.WeChatDB(db_dir=str(root), account=account, master_key=(b'w'*32).hex(),
                            workdir=str(folder/'master-work'))
        assert again._keys == expected and saved.read_bytes() == before
        key_cache_path.unlink()
        rejected = wx.WeChatDB(db_dir=str(root), account=account, master_key=(b'w'*32).hex(),
                               workdir=str(folder/'master-work'))
        assert not rejected._keys and not key_cache_path.exists()
        rejects(lambda: wx.WeChatDB(db_dir=str(root), account=account, master_key='bad'))


def check_media(folder, wx):
    from datetime import datetime
    from types import SimpleNamespace
    from Crypto.Cipher import AES
    from PIL import Image
    import reader_media
    from chatlocal import media as image_cache
    from wechatauto.media import MediaDownloader
    account = 'wxid_media_dddd'
    account_dir = folder/account
    base = account_dir/'db_storage'
    base.mkdir(parents=True)
    now = int(time.time())
    chat, digest = 'fixture@chatroom', 'a'*32
    target = account_dir/'msg/attach'/hashlib.md5(chat.encode()).hexdigest()/datetime.fromtimestamp(now).strftime('%Y-%m')/'Img'/(digest+'_h.dat')
    target.parent.mkdir(parents=True)
    image = io.BytesIO()
    Image.new('RGB', (20, 12), 'red').save(image, 'JPEG')
    jpeg = image.getvalue()
    aes_key = '0123456789abcdef'
    cipher = AES.new(aes_key.encode(), AES.MODE_ECB).encrypt(jpeg[:16]+bytes([16])*16)
    target.write_bytes(b'\x07\x08V2\x08\x07'+struct.pack('<II', 16, 2)+b'\0'+cipher+jpeg[16:-2]+bytes(v ^ 0x88 for v in jpeg[-2:]))
    local = folder/'media-cache'
    reader = SimpleNamespace(account=account, wxid=account, cfg_dword=None,
                             _find_weixin_pids=lambda: (_ for _ in ()).throw(AssertionError('Windows process enumeration')))
    raw = dict(type_code=3, chat=chat, md5=digest, create_time=now, content='')
    with patch.object(reader_media, 'DATA', local), patch.object(image_cache, 'DATA', local), \
         patch.object(MediaDownloader, '_scan_aes_key', side_effect=AssertionError('Mac media attempted process scanning')):
        media = reader_media.WeChatMedia(reader, dict(source_db=str(base)))
        assert media.parse(raw)[1][0]['status'] == 'unavailable'
        store = local/'wechat-reader/work/image_keys.json'
        store.parent.mkdir(parents=True, exist_ok=True)
        # Another account's valid AES key must not be reused.
        store.write_text(json.dumps({'wxid_other_eeee': aes_key}), encoding='utf-8')
        media = reader_media.WeChatMedia(reader, dict(source_db=str(base)))
        assert media.parse(raw)[1][0]['status'] == 'unavailable'
        store.write_text(json.dumps({account: aes_key}), encoding='utf-8')
        media = reader_media.WeChatMedia(reader, dict(source_db=str(base)))
        assert media.parse(raw)[1][0]['status'] == 'available'
        assert media.parse(raw)[1][0]['width'] == 20
    # The pinned media module's own public path also refuses Windows probing.
    decoder = MediaDownloader(reader)
    with patch.object(decoder, '_probe_ct', side_effect=AssertionError('Unexpected image probe before platform guard')):
        assert decoder._scan_aes_key() is None


def main():
    assert sys.platform == 'darwin', 'This check verifies the native Mac adapter'
    fail = AssertionError('Mac module import touched Windows APIs')
    with patch.object(ctypes, 'WinDLL', side_effect=fail, create=True), patch.dict(sys.modules, {'winreg': None}):
        from wechatauto import db as wx
        assert wx._k32 is None and wx.WeChatDB._tulpa_macos_cache_only
        (ROOT/'.tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp', prefix='wechat-macos-check-') as tmp:
            folder = Path(tmp)
            # Directory discovery uses the pinned portable Mac helper only.
            home = folder/'synthetic-home'
            data = home/'Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files'
            (data/'fixture_account/db_storage').mkdir(parents=True)
            assert wx.auto_detect_db_dir(home=home) == str(data)
            check_cache(folder, wx)
            check_master(folder, wx)
            check_media(folder, wx)
    print('PASS: native Mac import/discovery; page1 HMAC; read-only synthetic decrypt; account-bound cache isolation; wrong/invalid keys; master-to-per-database derivation; media caches and no Windows recovery. No real client or chats.')


if __name__ == '__main__':
    main()
