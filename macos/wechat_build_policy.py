"""Fail-closed local policy for the inspected official WeChat 269602 bundle.

Inspection is read-only. Registration permits upstream to prepare an isolated
copy; it does not prepare, launch, sign, or capture anything itself. The caller
must verify the complete recovery-source manifest before importing that source
and obtain authorization for the separate active-recovery operation.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import plistlib
import stat
import subprocess
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
APP = Path('/Applications/WeChat.app')
MODULE_FILE = ROOT / 'tools/macos-key-reader/chatlog_keeper/macos_debug_app.py'
MODULE_SHA256 = '866a5395166700493d1b3606f417b8f2082acd10949ca3e161ef7c345a1d3816'
CLIENT = ('4.1.13', '269602')
TEAM = '5A4RE8SF68'
IDENTIFIER = 'com.tencent.xinWeChat'
APPLICATION_IDENTIFIER = TEAM + '.' + IDENTIFIER
EXECUTABLE_SHA256 = 'a9df8843c8ee9a7e018f4a3580ef7ce39f2e1227e9174b18cdd1f087b9969ae8'
DYLIB_SHA256 = '6e82322680d7747020c305f67e932b235fe68b22578c24d9c524dabb31a4694a'
AUTHORITIES = (
    'Apple Mac OS Application Signing',
    'Apple Worldwide Developer Relations Certification Authority',
    'Apple Root CA',
)
EXPECTED_ENTITLEMENTS = {
    'com.apple.application-identifier': APPLICATION_IDENTIFIER,
    'com.apple.developer.team-identifier': TEAM,
    'com.apple.security.app-sandbox': True,
    'com.apple.security.application-groups': [APPLICATION_IDENTIFIER],
    'com.apple.security.cs.allow-jit': True,
    'com.apple.security.device.audio-input': True,
    'com.apple.security.device.camera': True,
    'com.apple.security.files.bookmarks.app-scope': True,
    'com.apple.security.files.downloads.read-write': True,
    'com.apple.security.files.user-selected.read-write': True,
    'com.apple.security.network.client': True,
    'com.apple.security.network.server': True,
    'com.apple.security.personal-information.location': True,
    'com.apple.security.personal-information.photos-library': True,
    'com.apple.security.print': True,
}


class PolicyRejected(ValueError):
    """Only controlled, key-free error codes leave this preflight."""


def _stable_sha256(path: Path) -> str:
    try:
        if path.resolve(strict=True) != path or path.is_symlink():
            raise PolicyRejected('wechat_policy_symlink_rejected')
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise PolicyRejected('wechat_policy_file_type_rejected')
            digest = hashlib.sha256()
            while chunk := os.read(descriptor, 1 << 20):
                digest.update(chunk)
            after = os.fstat(descriptor)
            entry = path.lstat()
            identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
            if identity(before) != identity(after) or identity(after) != identity(entry):
                raise PolicyRejected('wechat_policy_source_changed')
            return digest.hexdigest()
        finally:
            os.close(descriptor)
    except OSError:
        raise PolicyRejected('wechat_policy_file_unavailable') from None


def _codesign(arguments: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ['/usr/bin/codesign', *arguments, str(APP)],
            capture_output=True, timeout=90,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise PolicyRejected('wechat_policy_signature_check_unavailable') from None


def read_only_preflight() -> dict:
    """Verify the exact inspected original; never creates or launches a copy."""
    try:
        if APP.resolve(strict=True) != APP or not APP.is_dir():
            raise PolicyRejected('wechat_policy_app_path_rejected')
        info = plistlib.loads((APP / 'Contents/Info.plist').read_bytes())
    except (OSError, plistlib.InvalidFileException):
        raise PolicyRejected('wechat_policy_metadata_unavailable') from None
    if not isinstance(info, dict) or (
        info.get('CFBundleShortVersionString'), info.get('CFBundleVersion')
    ) != CLIENT or info.get('CFBundleIdentifier') != IDENTIFIER or info.get('CFBundleExecutable') != 'WeChat':
        raise PolicyRejected('wechat_policy_unsupported_client')
    if _codesign(['--verify', '--deep', '--strict']).returncode != 0:
        raise PolicyRejected('wechat_policy_invalid_signature')
    signature = _codesign(['-dv', '--verbose=4'])
    signature_lines = (signature.stdout + signature.stderr).decode('utf-8', 'replace').splitlines()
    if signature.returncode != 0 or (
        'Identifier=' + IDENTIFIER not in signature_lines
        or 'TeamIdentifier=' + TEAM not in signature_lines
        or tuple(line.removeprefix('Authority=') for line in signature_lines if line.startswith('Authority=')) != AUTHORITIES
        or not any(line.startswith('CodeDirectory ') and '(runtime)' in line for line in signature_lines)
    ):
        raise PolicyRejected('wechat_policy_signing_identity_rejected')
    for relative, expected in (
        ('Contents/MacOS/WeChat', EXECUTABLE_SHA256),
        ('Contents/Resources/wechat.dylib', DYLIB_SHA256),
    ):
        if _stable_sha256(APP / relative) != expected:
            raise PolicyRejected('wechat_policy_binary_mismatch')
    entitlement_result = _codesign(['-d', '--entitlements', ':-'])
    raw = entitlement_result.stdout + entitlement_result.stderr
    start, end = raw.find(b'<?xml'), raw.find(b'</plist>')
    try:
        if entitlement_result.returncode != 0 or start < 0 or end < start:
            raise PolicyRejected('wechat_policy_entitlements_unavailable')
        entitlements = plistlib.loads(raw[start:end + len(b'</plist>')])
    except plistlib.InvalidFileException:
        raise PolicyRejected('wechat_policy_entitlements_unavailable') from None
    # Require types as well as values: Python considers True equal to integer 1.
    if plistlib.dumps(entitlements, sort_keys=True) != plistlib.dumps(EXPECTED_ENTITLEMENTS, sort_keys=True):
        raise PolicyRejected('wechat_policy_entitlements_rejected')
    if _codesign(['--verify', '--deep', '--strict']).returncode != 0:
        raise PolicyRejected('wechat_policy_source_changed')
    return {
        'ok': True, 'version': CLIENT[0], 'build': CLIENT[1],
        'signature_valid': True, 'binaries_pinned': True,
        'entitlements_exact': True, 'requires_team_identifier': True,
        'allow_xplayer_registration': False,
    }


def register_verified_policy(module: ModuleType) -> dict:
    """Register only this local build after source and original-package checks."""
    if not isinstance(module, ModuleType) or module.__name__ != 'chatlog_keeper.macos_debug_app':
        raise PolicyRejected('wechat_policy_module_rejected')
    try:
        source = Path(module.__file__)
    except (TypeError, AttributeError):
        raise PolicyRejected('wechat_policy_module_rejected') from None
    if source != MODULE_FILE or _stable_sha256(source) != MODULE_SHA256:
        raise PolicyRejected('wechat_policy_module_rejected')
    if getattr(module, '_APPS', {}).get('wechat') != (APP, 'WeChat'):
        raise PolicyRejected('wechat_policy_app_binding_rejected')
    policy_class = getattr(module, '_WeChatAdHocPolicy', None)
    policies = getattr(module, '_WECHAT_AD_HOC_POLICIES', None)
    if not callable(policy_class) or not isinstance(policies, dict):
        raise PolicyRejected('wechat_policy_module_rejected')
    policy = policy_class()
    if policy.may_omit_team_identifier is not False or policy.allow_xplayer_registration is not False:
        raise PolicyRejected('wechat_policy_capabilities_rejected')
    if CLIENT in policies and policies[CLIENT] != policy:
        raise PolicyRejected('wechat_policy_existing_policy_rejected')
    result = read_only_preflight()
    policies[CLIENT] = policy
    module._WECHAT_AD_HOC_SUPPORTED_CLIENTS = frozenset(policies)
    return {**result, 'policy_registered': True}
