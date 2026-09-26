"""Cached, authenticated keys only. Cold import owns process key discovery.

Never rescan client memory (including the optional image cfg) in a hot poll.
New shards remain explicit failures until manual import refreshes their keys.
"""
from pathlib import Path
from wechatauto.db import WeChatDB, _verify_enc_key


class LiveWeChatDB(WeChatDB):
    def _cache_paths(self):
        return [Path(p) for p in (self._stable_key_file(), self.keys_file, self.keys_file + '.bak') if p]

    def _load_or_extract_keys(self, master_key=None):
        candidates = {}
        for path in self._cache_paths():
            for rel, key in self._load_key_cache(str(path)).items():
                candidates.setdefault(rel.replace('\\', '/'), []).append(key)
        verified = {}
        for rel, path, _ in self._db_files:
            try:
                with open(path, 'rb') as stream:
                    first = stream.read(4096)
            except OSError:
                continue
            for key in candidates.get(rel.replace('\\', '/'), []):
                if len(key) == 48 and _verify_enc_key(key[:32], first):
                    key = key[:32]
                if _verify_enc_key(key, first):
                    verified[rel] = key
                    break
        self._keys = verified
        self.unkeyed = [rel for rel, _, _ in self._db_files if rel not in verified]
        self._live_cache_stamp = self._cache_stamp()

    def _cache_stamp(self):
        result = []
        for path in self._cache_paths():
            try:
                st = path.stat()
                result.append((st.st_mtime_ns, st.st_size))
            except FileNotFoundError:
                result.append(None)
        return result

    def _refresh_db_files(self):
        current = self._collect_db_files()
        changed = {rel for rel, _, _ in current} != {rel for rel, _, _ in self._db_files}
        self._db_files = current
        if changed or self._cache_stamp() != self._live_cache_stamp:
            self._load_or_extract_keys()
