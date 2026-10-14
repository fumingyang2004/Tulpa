"""Read-only preflight; active recovery requires an explicit CLI option.

Active mode opens a private debug-enabled copy. Obtain user approval for that
specific action before invoking it. It never changes the installed clients.
"""
import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import plistlib
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'tools/macos-key-reader'
COMMIT = '2225294fd0761e10ce15540f21e7dbb2ac304c3c'
MANIFEST_SHA256 = '34dbed9c49b78a6ebd2df9c5c90dd07e8839397b28dd8890cac77d9a4c937329'


def client_info(name):
    app = Path('/Applications')/(name+'.app')
    if not app.is_dir():
        return {'installed': False}
    info = plistlib.loads((app/'Contents/Info.plist').read_bytes())
    verified = subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)],
                              capture_output=True, timeout=90)
    return {'installed': True, 'version': info.get('CFBundleShortVersionString'),
            'build': info.get('CFBundleVersion'), 'signature_valid': verified.returncode == 0}


def verify_source():
    raw_manifest = (SOURCE/'manifest.json').read_bytes()
    if hashlib.sha256(raw_manifest).hexdigest() != MANIFEST_SHA256:
        raise ValueError('Recovery manifest changed')
    manifest = json.loads(raw_manifest)
    if manifest['commit'] != COMMIT:
        raise ValueError('Recovery source commit mismatch')
    for name, expected in manifest['files'].items():
        path = SOURCE/name
        if not path.resolve().is_relative_to(SOURCE.resolve()) or path.is_symlink():
            raise ValueError('Invalid source path')
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Recovery source changed')


def recover_qq(resume=False):
    if not client_info('QQ').get('signature_valid'):
        raise ValueError('QQ signature verification failed')
    verify_source()
    os.environ['CHATLOG_KEEPER_DATA_DIR'] = str(ROOT/'data/qq-reader')
    sys.path.insert(0, str(SOURCE))
    from chatlog_keeper import qq_db, active_key, macos_debug_app, macos_key
    data_root = qq_db.find_qq_data_root()
    accounts = qq_db.find_qq_account_databases(data_root) if data_root else {}
    if len(accounts) != 1:
        raise ValueError('Expected one QQ account; select account before recovery')
    account, database = next(iter(accounts.items()))
    if resume:
        processes = subprocess.check_output(['ps', '-axo', 'pid=,comm='], text=True)
        candidates = []
        for line in processes.splitlines():
            fields = line.strip().split(None, 1)
            if len(fields) != 2:
                continue
            executable = Path(fields[1])
            if fields[1] == '/Applications/QQ.app/Contents/MacOS/QQ':
                raise ValueError('Daily QQ still running')
            if executable.name == 'QQ' and executable.parent.name == 'MacOS' and executable.is_relative_to(ROOT/'data/qq-reader/debug-apps'):
                candidates.append((int(fields[0]), executable))
        if len(candidates) != 1:
            raise ValueError('Expected one authorized private copy')
        pid, executable = candidates[0]
        if not macos_debug_app._validate_prepared_debug_copy('qq', executable.parents[2]):
            raise ValueError('Private copy changed')
        identity = macos_key.process_identity(pid)
        if not identity or identity[0] != os.fsencode(executable):
            raise ValueError('Private process changed')
        raw = qq_db._read_qq_verification_bytes(database)
        key = macos_key.extract_verified('qq', pid, lambda candidate: qq_db._verify_key_qq(candidate, raw),
            primary_verify=lambda candidate: qq_db._verify_key_qq_with_algo(candidate, raw, 'sha512', 'sha1', 48),
            timeout=120, expected_identity=identity)
    else:
        if qq_db._get_qq_pids():
            raise ValueError('Quit the daily QQ normally before active recovery')
        key = active_key.extract_qq_key_active(db_path=str(database), timeout=120)
    if not key:
        return {'ok': False, 'error': macos_debug_app.last_error() or macos_key.last_error() or 'no_verified_key'}
    # Do not persist stale candidates, or expose a key in argv/stdout.
    raw = qq_db._read_qq_verification_bytes(database)
    if not raw or not qq_db._verify_key_qq(key, raw):
        return {'ok': False, 'error': 'source_changed'}
    saved = qq_db.save_cached_key_for_account(key, account)
    return {'ok': bool(saved), 'key_saved': bool(saved), 'original_client_unchanged': True}


def recover_wechat(preflight=False):
    """Account-bound startup capture; never alter the daily application."""
    if not client_info('WeChat').get('signature_valid'):
        raise ValueError('WeChat signature verification failed')
    verify_source()
    os.environ['CHATLOG_KEEPER_DATA_DIR'] = str(ROOT/'data/wechat-reader')
    sys.path.insert(0, str(SOURCE))
    logging.disable(logging.CRITICAL)
    from chatlog_keeper import wechat_db, active_key, macos_debug_app, macos_key, macos_wechat_capture
    from wechat_build_policy import register_verified_policy
    register_verified_policy(macos_debug_app)
    data_root = wechat_db.find_weixin_data_root()
    accounts = [p for p in wechat_db.find_wxid_dirs(data_root) if (p/'db_storage').is_dir()] if data_root else []
    if len(accounts) != 1:
        raise ValueError('Expected one WeChat account; select account before recovery')
    account = accounts[0]
    databases = sorted((account/'db_storage').rglob('*.db'))
    messages = wechat_db.find_msg_databases(account)
    oracle = next((p for p in messages if p.stat().st_size >= 4096), None)
    if oracle is None:
        raise ValueError('No message verification database')
    if preflight:
        helper = macos_key.ensure_helper()
        capture = macos_wechat_capture.ensure_capture_library()
        return dict(ok=bool(helper and capture), supported_build=True,
                    account_count=1, database_count=len(databases), helpers_ready=bool(helper and capture))
    key = wechat_db.load_cached_wechat_key_for_account(account.name)
    page = wechat_db._read_stable_page1(oracle)
    if not key or not wechat_db._verify_key_v4(key, page):
        if wechat_db._get_weixin_pids():
            raise ValueError('Quit the daily WeChat normally before active recovery')
        key = active_key.extract_wechat_key_active(db_path=str(oracle), timeout=180,
            _recovery_notify=lambda: print(json.dumps({'stage':'private_copy_started','login_may_be_required':True}), flush=True))
    if not key:
        return dict(ok=False, error=macos_debug_app.last_error() or macos_wechat_capture.last_error() or macos_key.last_error() or 'no_verified_key')
    if not wechat_db.save_cached_wechat_key_for_account(key, account.name, oracle):
        return dict(ok=False, error='source_changed')
    keys = {}
    for database in databases:
        page = wechat_db._read_stable_page1(database)
        material = wechat_db._effective_page_key(key, page)
        if material:
            keys[database.relative_to(account/'db_storage').as_posix()] = material.hex()
    if oracle.relative_to(account/'db_storage').as_posix() not in keys:
        return dict(ok=False, error='message_key_not_verified')
    from chatlog_keeper.core._secrets import write_secret_text
    target = ROOT/'data/wechat-reader/keys'/(account.name+'.json')
    saved = write_secret_text(target, json.dumps(keys))
    original_valid = client_info('WeChat').get('signature_valid', False)
    return dict(ok=bool(saved and original_valid), key_saved=bool(saved),
                verified_databases=len(keys), database_count=len(databases), original_client_unchanged=original_valid)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--recover-qq', action='store_true')
    parser.add_argument('--resume-qq', action='store_true')
    parser.add_argument('--preflight-wechat', action='store_true')
    parser.add_argument('--recover-wechat', action='store_true')
    args = parser.parse_args()
    try:
        if args.preflight_wechat or args.recover_wechat:
            result = recover_wechat(preflight=args.preflight_wechat)
        elif args.recover_qq or args.resume_qq:
            result = recover_qq(resume=args.resume_qq)
        else:
            result = {'QQ': client_info('QQ'), 'WeChat': client_info('WeChat')}
        print(json.dumps(result, ensure_ascii=False))
    except KeyboardInterrupt:
        # Upstream finally blocks close the private process and capture FIFO.
        print(json.dumps({'ok': False, 'error': 'cancelled'}))
        raise SystemExit(130)
    except Exception as exc:
        # Only controlled error messages; third-party errors may include secrets.
        print(json.dumps({'ok': False, 'error': type(exc).__name__}))
        raise SystemExit(1)
