"""Synthetic WeChat policy/recovery checks; never launches or signs any app."""
from __future__ import annotations

from contextlib import ExitStack, redirect_stdout
from dataclasses import dataclass
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import plistlib
import stat
import struct
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'macos'), str(ROOT/'tools/macos-key-reader'), str(ROOT/'tools/wechat-reader')]
import wechat_build_policy as policy
import reader_recovery as runner
from chatlog_keeper import active_key as active, wechat_db as crypto
from chatlog_keeper.core._secrets import write_secret_text
from wechatauto.db import WeChatDB, _verify_enc_key


def rejected(call, code):
    try:
        call()
    except policy.PolicyRejected as error:
        assert str(error) == code
    else:
        raise AssertionError('policy accepted an invalid fixture')


def policy_checks(folder):
    app = folder/'WeChat.app'
    executable = app/'Contents/MacOS/WeChat'
    dylib = app/'Contents/Resources/wechat.dylib'
    for path in (executable, dylib):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(('fixture-'+path.name).encode())
    info_path = app/'Contents/Info.plist'
    info = dict(CFBundleShortVersionString=policy.CLIENT[0], CFBundleVersion=policy.CLIENT[1],
                CFBundleIdentifier=policy.IDENTIFIER, CFBundleExecutable='WeChat')
    info_path.write_bytes(plistlib.dumps(info))
    state = {'verify': True, 'identity': True, 'runtime': True,
             'entitlements': policy.EXPECTED_ENTITLEMENTS.copy()}

    def codesign(arguments):
        assert arguments in (['--verify', '--deep', '--strict'], ['-dv', '--verbose=4'], ['-d', '--entitlements', ':-'])
        if arguments[0] == '--verify':
            return subprocess.CompletedProcess([], 0 if state['verify'] else 1, b'', b'')
        if arguments[0] == '-dv':
            text = '\n'.join(['Identifier='+policy.IDENTIFIER,
                              'TeamIdentifier='+ (policy.TEAM if state['identity'] else 'OTHER'),
                              *['Authority='+name for name in policy.AUTHORITIES],
                              'CodeDirectory v=20500 flags=0x10000'+ ('(runtime)' if state['runtime'] else '')])
            return subprocess.CompletedProcess([], 0, b'', text.encode())
        return subprocess.CompletedProcess([], 0, plistlib.dumps(state['entitlements']), b'')

    with ExitStack() as stack:
        stack.enter_context(patch.object(policy, 'APP', app))
        stack.enter_context(patch.object(policy, 'EXECUTABLE_SHA256', hashlib.sha256(executable.read_bytes()).hexdigest()))
        stack.enter_context(patch.object(policy, 'DYLIB_SHA256', hashlib.sha256(dylib.read_bytes()).hexdigest()))
        stack.enter_context(patch.object(policy, '_codesign', side_effect=codesign))
        original = {path: path.read_bytes() for path in (info_path, executable, dylib)}
        assert policy.read_only_preflight()['ok']
        for field, value in [('CFBundleShortVersionString', '4.1.14'), ('CFBundleVersion', '269603'),
                             ('CFBundleIdentifier', 'different.app'), ('CFBundleExecutable', 'Other')]:
            info_path.write_bytes(plistlib.dumps({**info, field: value}))
            rejected(policy.read_only_preflight, 'wechat_policy_unsupported_client')
            info_path.write_bytes(original[info_path])
        state['verify'] = False
        rejected(policy.read_only_preflight, 'wechat_policy_invalid_signature')
        state['verify'] = True
        for field in ('identity', 'runtime'):
            state[field] = False
            rejected(policy.read_only_preflight, 'wechat_policy_signing_identity_rejected')
            state[field] = True
        for entitlements in ({**policy.EXPECTED_ENTITLEMENTS, 'com.apple.security.get-task-allow': True},
                             {**policy.EXPECTED_ENTITLEMENTS, 'com.apple.security.app-sandbox': 1},
                             {k: v for k, v in policy.EXPECTED_ENTITLEMENTS.items() if k != 'com.apple.developer.team-identifier'}):
            state['entitlements'] = entitlements
            rejected(policy.read_only_preflight, 'wechat_policy_entitlements_rejected')
        state['entitlements'] = policy.EXPECTED_ENTITLEMENTS.copy()
        for path in (executable, dylib):
            path.write_bytes(b'tampered fixture')
            rejected(policy.read_only_preflight, 'wechat_policy_binary_mismatch')
            path.write_bytes(original[path])
        link = folder/'symlink'
        link.symlink_to(executable)
        rejected(lambda: policy._stable_sha256(link), 'wechat_policy_symlink_rejected')

        source = folder/'macos_debug_app.py'
        source.write_text('# synthetic policy registration fixture\n')
        @dataclass(frozen=True)
        class AdHocPolicy:
            may_omit_team_identifier: bool = False
            allow_xplayer_registration: bool = False
        module = ModuleType('chatlog_keeper.macos_debug_app')
        module.__file__ = str(source)
        module._APPS = {'wechat': (app, 'WeChat')}
        module._WeChatAdHocPolicy = AdHocPolicy
        module._WECHAT_AD_HOC_POLICIES = {}
        stack.enter_context(patch.object(policy, 'MODULE_FILE', source))
        stack.enter_context(patch.object(policy, 'MODULE_SHA256', hashlib.sha256(source.read_bytes()).hexdigest()))
        assert policy.register_verified_policy(module)['policy_registered']
        assert module._WECHAT_AD_HOC_POLICIES[policy.CLIENT] == AdHocPolicy()
        assert policy.register_verified_policy(module)['policy_registered']
        rejected(lambda: policy.register_verified_policy(SimpleNamespace()), 'wechat_policy_module_rejected')
        with patch.object(module, '__name__', 'other.module'):
            rejected(lambda: policy.register_verified_policy(module), 'wechat_policy_module_rejected')
        with patch.object(module, '__file__', str(link)):
            rejected(lambda: policy.register_verified_policy(module), 'wechat_policy_module_rejected')
        source.write_text('# changed source\n')
        rejected(lambda: policy.register_verified_policy(module), 'wechat_policy_module_rejected')
        source.write_text('# synthetic policy registration fixture\n')
        with patch.object(module, '_APPS', {'wechat': (app, 'Other')}):
            rejected(lambda: policy.register_verified_policy(module), 'wechat_policy_app_binding_rejected')
        with patch.object(module, '_WeChatAdHocPolicy', lambda: AdHocPolicy(True)):
            rejected(lambda: policy.register_verified_policy(module), 'wechat_policy_capabilities_rejected')
        with patch.object(module, '_WECHAT_AD_HOC_POLICIES', {policy.CLIENT: AdHocPolicy(True)}):
            rejected(lambda: policy.register_verified_policy(module), 'wechat_policy_existing_policy_rejected')
        assert all(path.read_bytes() == content for path, content in original.items())
    print('PASS exact build policy: version, signature, hashes, entitlement types, module binding and capability rejection')


def encrypted_page(master, salt):
    page_key = hashlib.pbkdf2_hmac('sha512', master, salt, 256000, dklen=32)
    page = bytearray(salt+bytes((i % 251 for i in range(4096-16))))
    mac_key = hashlib.pbkdf2_hmac('sha512', page_key, bytes(x ^ 0x3a for x in salt), 2, dklen=32)
    digest = hmac.new(mac_key, page[16:4032], hashlib.sha512)
    digest.update(struct.pack('<I', 1))
    page[4032:] = digest.digest()
    return bytes(page), page_key


def fake_module(name, **attributes):
    module = ModuleType(name)
    module.__dict__.update(attributes)
    return module


def recovery_checks(folder):
    master = bytes(range(32))
    assert len(master) == 32
    account = folder/'fixture_account'
    dbs = [account/'db_storage/message/message_0.db', account/'db_storage/contact/contact.db']
    expected = {}
    for index, path in enumerate(dbs):
        path.parent.mkdir(parents=True, exist_ok=True)
        page, key = encrypted_page(master, bytes([index+11])*16)
        path.write_bytes(page)
        assert crypto._effective_page_key(master, page) == key
        assert _verify_enc_key(key, page)
        assert not _verify_enc_key(master, page)
        expected[path.relative_to(account/'db_storage').as_posix()] = key.hex()
    missing = account/'db_storage/invalid.db'
    missing.write_bytes(b'not an encrypted page')
    active_mock = Mock(return_value=master)
    save_master = Mock(side_effect=lambda key, name, path: name == account.name and crypto._verify_key_v4(key, path.read_bytes()))
    modules = {
        'chatlog_keeper': fake_module('chatlog_keeper'),
        'chatlog_keeper.wechat_db': fake_module('chatlog_keeper.wechat_db',
            find_weixin_data_root=lambda: folder, find_wxid_dirs=Mock(return_value=[account]),
            find_msg_databases=lambda _: [dbs[0]], load_cached_wechat_key_for_account=Mock(return_value=master),
            _read_stable_page1=lambda path: path.read_bytes(), _verify_key_v4=crypto._verify_key_v4,
            _effective_page_key=crypto._effective_page_key, _get_weixin_pids=Mock(return_value=[]),
            save_cached_wechat_key_for_account=save_master),
        'chatlog_keeper.active_key': fake_module('chatlog_keeper.active_key', extract_wechat_key_active=active_mock),
        'chatlog_keeper.macos_debug_app': fake_module('chatlog_keeper.macos_debug_app', last_error=lambda: 'debug_copy_cleanup_failed'),
        'chatlog_keeper.macos_key': fake_module('chatlog_keeper.macos_key', last_error=lambda: '', ensure_helper=Mock(return_value=Path('fixture-helper'))),
        'chatlog_keeper.macos_wechat_capture': fake_module('chatlog_keeper.macos_wechat_capture', last_error=lambda: '', ensure_capture_library=Mock(return_value=Path('fixture-library'))),
        'chatlog_keeper.core._secrets': fake_module('chatlog_keeper.core._secrets', write_secret_text=write_secret_text),
    }
    for name, module in modules.items():
        if name.startswith('chatlog_keeper.') and name.count('.') == 1:
            setattr(modules['chatlog_keeper'], name.rsplit('.', 1)[1], module)
    fake_crypto = modules['chatlog_keeper.wechat_db']
    output = io.StringIO()
    with ExitStack() as stack:
        stack.enter_context(patch.dict(sys.modules, modules))
        stack.enter_context(patch.object(runner, 'ROOT', folder))
        stack.enter_context(patch.object(runner, 'verify_source', return_value=None))
        stack.enter_context(patch.object(runner, 'client_info', return_value={'signature_valid': True}))
        stack.enter_context(patch.object(policy, 'register_verified_policy', return_value={'ok': True}))
        stack.enter_context(patch.object(sys, 'path', sys.path.copy()))
        stack.enter_context(patch.dict(os.environ))
        stack.enter_context(patch('logging.disable'))
        stack.enter_context(redirect_stdout(output))
        result = runner.recover_wechat()
        assert result['ok'] and result['verified_databases'] == 2
        active_mock.assert_not_called()
        target = folder/'data/wechat-reader/keys'/(account.name+'.json')
        assert json.loads(target.read_text()) == expected
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
        cached = WeChatDB.__new__(WeChatDB)._load_key_cache(str(target))
        assert {name: key.hex() for name, key in cached.items()} == expected
        fake_crypto.load_cached_wechat_key_for_account.return_value = b'x'*32
        result = runner.recover_wechat()
        assert result['ok'] and active_mock.call_count == 1
        active_mock.return_value = None
        result = runner.recover_wechat()
        assert not result['ok'] and result['error'] == 'debug_copy_cleanup_failed'
        assert json.loads(target.read_text()) == expected
        other = folder/'other_account'
        (other/'db_storage').mkdir(parents=True)
        fake_crypto.find_wxid_dirs.return_value = [account, other]
        previous = active_mock.call_count
        try:
            runner.recover_wechat(preflight=True)
        except ValueError as error:
            assert 'one WeChat account' in str(error)
        else:
            raise AssertionError('multiple accounts were accepted')
        assert active_mock.call_count == previous
        modules['chatlog_keeper.macos_key'].ensure_helper.assert_not_called()
        modules['chatlog_keeper.macos_wechat_capture'].ensure_capture_library.assert_not_called()
    assert master.hex() not in output.getvalue()
    assert all(value not in output.getvalue() for value in expected.values())
    print('PASS recovery fixtures: all accounts rejected before capture, verified per-database keys, private cache, cache-only reuse and safe output')
    return dbs[0], master


def cleanup_checks(database, master):
    """Exercise upstream orchestration with all process/library calls mocked."""
    for failure in ('none', 'process', 'channel', 'cancel', 'exception'):
        terminate = Mock(return_value=failure != 'process')
        close = Mock(return_value=failure != 'channel')
        reader = Mock(return_value=[master])
        if failure == 'exception':
            reader.side_effect = RuntimeError('synthetic capture exception')
        channel = SimpleNamespace(library_path=Path('fixture-library'), library_identity=('fixture',),
            path=Path('fixture-fifo'), identity=('fixture',), invalid=False,
            read_candidates=reader, close=close)
        package = fake_module('chatlog_keeper')
        fake_crypto = fake_module('chatlog_keeper.wechat_db', _read_stable_page1=lambda _: database.read_bytes(),
                                 _verify_key_v4=crypto._verify_key_v4)
        package.wechat_db = fake_crypto
        modules = {
            'chatlog_keeper': package, 'chatlog_keeper.wechat_db': fake_crypto,
            'chatlog_keeper.macos_debug_app': fake_module('chatlog_keeper.macos_debug_app',
                clear_last_error=lambda: None, debug_copy_process_identity=lambda *_: ('fixture',),
                launch_debug_copy=Mock(return_value=42), terminate_debug_copy=terminate,
                validate_debug_copy_process=lambda *_: True),
            'chatlog_keeper.macos_key': fake_module('chatlog_keeper.macos_key',
                clear_last_error=lambda: None, ensure_helper=lambda: Path('fixture-helper'),
                extract_verified=Mock(side_effect=AssertionError('should use fixture startup capture'))),
            'chatlog_keeper.macos_wechat_capture': fake_module('chatlog_keeper.macos_wechat_capture',
                clear_last_error=lambda: None, create_capture_channel=lambda *args, **kwargs: channel,
                ensure_capture_library=lambda: Path('fixture-library')),
        }
        with patch.dict(sys.modules, modules), patch.object(active, '_is_macos_host', return_value=True), \
                patch.object(active, '_wechat_active_oracle_paths', return_value=[database]):
            if failure == 'exception':
                try:
                    active.extract_wechat_key_active(db_path=str(database), timeout=1)
                except RuntimeError:
                    pass
                else:
                    raise AssertionError('capture exception swallowed')
            else:
                result = active.extract_wechat_key_active(db_path=str(database), timeout=1,
                    _cancel_requested=lambda: failure == 'cancel')
                assert result == (master if failure == 'none' else None)
        terminate.assert_called_once_with('wechat', 42)
        close.assert_called_once()
    print('PASS startup cleanup: exact private process and capture channel closed on success, failure, cancellation and exceptions')


if __name__ == '__main__':
    (ROOT/'.tmp').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp', prefix='wechat-policy-fixture-') as temporary:
        folder = Path(temporary)
        policy_checks(folder)
        database, master = recovery_checks(folder)
        cleanup_checks(database, master)
