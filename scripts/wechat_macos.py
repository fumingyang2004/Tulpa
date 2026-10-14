"""Portable WeChat parser with account-bound, authenticated Mac key caches.

This adapter never starts a client, reads process memory or selects another
account. Key recovery is a separate, explicitly invoked operation. Windows
retains the pinned reader's original implementation.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MACOS_HELPER = ROOT/'tools/qq-reader/chatlog_keeper/core/_macos.py'
MACOS_HELPER_SHA256 = '832a880958380e6cb4bc7b84f55d2543246ead1d80e554c6480e2fbf1ffeea6a'


def data_root(home=None):
    """Use only the reviewed Mac sandbox-root helper, without package mixing."""
    if MACOS_HELPER.is_symlink() or hashlib.sha256(MACOS_HELPER.read_bytes()).hexdigest() != MACOS_HELPER_SHA256:
        raise ValueError('Mac 微信目录检测组件不完整，请重新安装读取组件。')
    spec = importlib.util.spec_from_file_location('_tulpa_wechat_macos_roots', MACOS_HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for root in module.wechat_data_roots(home=home):
        if root.is_dir() and any(p.is_dir() and (p/'db_storage').is_dir() for p in root.iterdir()):
            return str(root)
    return None


def _account(value):
    if not isinstance(value, str) or not value or value in ('.', '..') or len(value) > 200:
        raise ValueError('请选择有效的本机微信账号。')
    if Path(value).name != value or any(c in value for c in '/\\') or any(ord(c) < 32 for c in value):
        raise ValueError('请选择有效的本机微信账号。')
    return value


def _atomic_cache(path, data):
    """Private local cache only; no empty overwrite and no secret diagnostics."""
    path = Path(path)
    encoded = json.dumps(data, sort_keys=True, indent=2).encode('utf-8')
    try:
        if path.read_bytes() == encoded:
            path.chmod(0o600)
            return
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.wechat-keys-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
        temporary.chmod(0o600)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def install(namespace):
    """Install on the pinned module after its portable parser is defined."""
    reader_class = namespace['WeChatDB']
    if getattr(reader_class, '_tulpa_macos_cache_only', False):
        return
    verify = namespace['_verify_enc_key']
    find_accounts = namespace['_find_account_dirs']
    original_init = reader_class.__init__
    original_collect = reader_class._collect_db_files
    original_open = reader_class._open

    def initialize(self, db_dir=None, keys_file=None, workdir=None, account=None, master_key=None):
        db_dir = db_dir or data_root()
        if not db_dir:
            raise RuntimeError('未找到本机微信数据库目录。')
        if account is not None:
            account = _account(account)
            directory = Path(db_dir)/account
            if directory.is_symlink() or (directory/'db_storage').is_symlink() or not (directory/'db_storage').is_dir():
                raise ValueError('所选本机微信账号的数据库目录不可用。')
        original_init(self, db_dir, keys_file, workdir, account, master_key)

    def pick_account(self):
        accounts = [Path(p).name for p in find_accounts(self.db_dir)
                    if not Path(p).is_symlink() and not (Path(p)/'db_storage').is_symlink()]
        if len(accounts) != 1:
            raise ValueError('请明确选择本机微信账号；不会自动改读其他账号。')
        return _account(accounts[0])

    def stable_key_file(self, account=None):
        account = _account(account if account is not None else self.account)
        base = os.environ.get('WECHATAUTO_KEYS_DIR') or str(ROOT/'data/wechat-reader/keys')
        return str(Path(base)/(account+'.json'))

    def collect_files(self):
        base = Path(self.account_dir)/'db_storage'
        files = []
        for rel, path, size in original_collect(self):
            relative = Path(rel)
            if relative.is_absolute() or '..' in relative.parts:
                continue
            current = base
            for part in relative.parts:
                current = current/part
                if current.is_symlink():
                    break
            else:
                files.append((rel, path, size))
        return files

    def load_cache(self, path):
        try:
            saved = json.loads(Path(path).read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError):
            return {}
        if not isinstance(saved, dict):
            return {}
        out = {}
        for rel, hex_key in saved.items():
            if not isinstance(rel, str) or not isinstance(hex_key, str):
                continue
            try:
                key = bytes.fromhex(hex_key)
            except ValueError:
                continue
            if len(key) in (32, 48):
                out[rel] = key
        return out

    def save_keys(self):
        if not self._keys:
            return
        data = {rel: key.hex() for rel, key in self._keys.items()}
        paths = {self.keys_file, self._stable_key_file()}
        for path in paths:
            if path:
                _atomic_cache(path, data)

    def load_keys(self, master_key=None):
        _account(self.account)
        self.master_key = None
        self.cfg_dword = None
        # The generic work cache can belong to a previous account. Cold reads
        # consume only this account's stable cache, authenticated again on the
        # requested DB. The live subclass separately validates its own cache.
        stable = self._stable_key_file()
        cached = self._load_key_cache(stable) if stable else {}
        candidates = {rel.replace('\\', '/'): key for rel, key in cached.items()}
        verified = {}
        for rel, path, _ in self._db_files:
            key = candidates.get(rel.replace('\\', '/'))
            if not key or len(key) not in (32, 48):
                continue
            try:
                with open(path, 'rb') as stream:
                    first = stream.read(namespace['PAGE_SZ'])
            except OSError:
                continue
            if len(key) == 48 and verify(key[:32], first):
                key = key[:32]
            if verify(key, first):
                verified[rel] = key
        if master_key is not None:
            if not isinstance(master_key, str):
                raise ValueError('显式主密钥格式无效。')
            # The existing derivation authenticates every derived AES page
            # key. The master material never becomes a page key/cache entry.
            verified.update(self.derive_keys_from_master(master_key))
        self._keys = verified
        self.unkeyed = [rel for rel, _, _ in self._db_files if rel not in verified]
        if verified:
            save_keys(self)

    def refresh_files(self):
        self._db_files = self._collect_db_files()
        load_keys(self)

    def open_database(self, rel):
        if rel not in self._keys:
            raise RuntimeError('Mac 微信数据库没有有效的已验证缓存密钥；请先完成本机密钥恢复，现有资料保留。')
        return original_open(self, rel)

    namespace['auto_detect_db_dir'] = data_root
    namespace['_registry_data_dirs'] = lambda: []
    reader_class.__init__ = initialize
    reader_class._pick_account = pick_account
    reader_class._collect_db_files = collect_files
    reader_class._stable_key_file = stable_key_file
    reader_class._load_key_cache = load_cache
    reader_class._load_or_extract_keys = load_keys
    reader_class._save_keys = save_keys
    reader_class._refresh_db_files = refresh_files
    reader_class._open = open_database
    reader_class._find_weixin_pids = lambda self: []
    reader_class.extract_master_key = lambda self: None
    reader_class.extract_keys = lambda self: {}
    reader_class._extract_keys_pid = lambda self, pid, tested: {}
    reader_class._collect_key_candidates = lambda self, pid, seen: set()
    reader_class._try_other_accounts = lambda self: False
    reader_class._auto_diagnose_key_failure = lambda self, rel: None
    reader_class._tulpa_macos_cache_only = True
