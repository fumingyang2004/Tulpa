"""Small reproducible adaptations to the pinned, read-only upstream modules."""

def patch_qq_configured_root(data):
    """Keep a per-install custom QQ source across detection, imports and restarts."""
    marker = b'# Tulpa explicit QQ source v1 (scripts/reader_patches.py).'
    if marker in data:
        if data.count(marker) != 1:
            raise ValueError('Duplicate QQ source adaptation')
        return data
    before = b'    from chatlog_keeper.core._paths import all_drive_roots, candidate_documents_roots'
    if data.count(before) != 1:
        raise ValueError('QQ directory discovery changed; review custom source adaptation')
    addition = b'''    # Tulpa explicit QQ source v1 (scripts/reader_patches.py).
    # Local installation setting only. Never silently fall back to another account.
    import json as _source_json
    _manual = os.environ.get("CHATLOG_QQ_DATA_ROOT", "").strip()
    _setting = Path(__file__).resolve().parents[3] / "data" / "qq-source-root.json"
    if not _manual and _setting.exists():
        try:
            with _setting.open("r", encoding="utf-8-sig") as _stream:
                _raw = _stream.read(16385)
            if len(_raw) > 16384:
                raise ValueError()
            _value = _source_json.loads(_raw)
            if _value.get("format") != "tulpa-qq-source-v1":
                raise ValueError()
            _manual = _value["path"]
            if not isinstance(_manual, str) or not _manual.strip():
                raise ValueError()
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            raise ValueError("Invalid saved QQ source directory; run the QQ path repair again") from None
    if _manual:
        _root = Path(_manual)
        if not _root.is_absolute() or not _root.is_dir():
            raise ValueError("Configured QQ source directory is unavailable; no fallback account was selected")
        return _root

'''
    newline = b'\r\n' if b'\r\n' in data else b'\n'
    return data.replace(before, addition.replace(b'\n', newline) + before)


def patch_qq_passive_scan(data):
    """Keep upstream account/HMAC checks, replace slow Windows memory traversal."""
    marker = b'# Tulpa bounded QQ passive scan v1 (scripts/qq_passive_keys.py).'
    if marker in data:
        if data.count(marker) != 1:
            raise ValueError('Duplicate QQ passive-scan adaptation')
        return data
    crlf = b'\r\n' in data
    data = data.replace(b'\r\n', b'\n')
    start = data.index(b'def _scan_memory_for_key(')
    stop = data.index(b'\ndef extract_key_from_qq(', start)
    windows = data.index(b'    kernel32 = _windows_kernel32()\n', start, stop)
    replacement = b'''    # Tulpa bounded QQ passive scan v1 (scripts/qq_passive_keys.py).
    from chatlog_keeper._tulpa_passive_keys import scan
    return scan(pid, db_path, timeout_s, kernel=_windows_kernel32(),
                read_verification=_read_qq_verification_bytes, verify=_verify_key_qq,
                raise_denied=_raise_if_windows_access_denied, last_error=_windows_last_error)

'''
    data = data[:windows] + replacement + data[stop:]
    before = b'''    for kdf, hmac_algo, reserve in _VERIFY_COMBOS:
        if _verify_key_qq_with_algo(passphrase, db_raw, kdf, hmac_algo, reserve):
            return True
    return False'''
    after = b'''    from chatlog_keeper._tulpa_passive_keys import verify_key
    return verify_key(passphrase, db_raw, _VERIFY_COMBOS)'''
    if data.count(before) != 1:
        raise ValueError('QQ verification changed; review HMAC-preserving optimization')
    data = data.replace(before, after)
    before = b'                for pid in pids:'
    if data.count(before) != 1:
        raise ValueError('QQ process schedule changed')
    data = data.replace(before, b'                for scan_index, pid in enumerate(pids):')
    before = b'timeout_s=min(per_process_budget, remaining))'
    if data.count(before) != 1:
        raise ValueError('QQ passive-scan budget changed')
    data = data.replace(before, b'timeout_s=min(per_process_budget, remaining / max(1, len(pids)-scan_index)))')
    data = data.replace(b'QQ running but key extraction failed (try Admin or different PID).',
        b'QQ is running but no key was verified; see QQ_SCAN stage/counter summaries.')
    data = data.replace(b'No key available (QQ.exe not running and no cached key).',
        b'No verified QQ key available after passive scan/cache checks; this does not imply QQ is closed.')
    compile(data, 'qq_db.py', 'exec')
    return data.replace(b'\n', b'\r\n') if crlf else data

def patch_qq_proxy(data):
    before=b'    return [sys.executable, "-I", "-u", str(helper), *arguments]'
    after=b'''    # Desktop has a separately pinned stdlib-only Python/SQLite process.
    # Do not change the SQLite DLL or pending byte of the application itself.
    dedicated = Path(sys.executable).parent / "qq-sqlite" / "python.exe"
    executable = str(dedicated) if dedicated.is_file() else sys.executable
    return [executable, "-I", "-u", str(helper), *arguments]'''
    if data.count(before)!=1:raise ValueError('QQ helper command changed; review desktop runtime adaptation')
    data=data.replace(before,after)
    before=b'            raise QQShiftedSQLiteError("isolated QQ SQLite helper refused the database")'
    after=b'''            reason = response.get("error", "unknown")
            # Only a small protocol error code, never a path/database value.
            if not isinstance(reason, str) or not reason.replace("_", "").isalnum() or len(reason)>80:
                reason = "unknown"
            raise QQShiftedSQLiteError("isolated QQ SQLite helper refused the database (" + reason + ")")'''
    if data.count(before)!=1:raise ValueError('QQ helper refusal changed; review diagnostic adaptation')
    return patch_qq_query_diagnostic(data.replace(before,after))


def patch_qq_query_diagnostic(data):
    before=b'                raise sqlite3.DatabaseError("isolated QQ SQLite query failed")'
    after=b'''                reason = response.get("error", "unknown")
                if not isinstance(reason, str) or not reason.replace("_", "").isalnum() or len(reason)>80:
                    reason = "unknown"
                raise sqlite3.DatabaseError("isolated QQ SQLite query failed (" + reason + ")")'''
    if data.count(before)!=1:raise ValueError('QQ query error changed; review sanitized diagnostic')
    return data.replace(before,after)


def patch_wechat_db(data):
    before=b'''            if len(keys) >= len(self._db_files):
                break
        return keys

    def _collect_key_candidates'''
    after=b'''            if len(keys) >= len(self._db_files):
                break
        if len(keys) < len(self._db_files):
            from chatlocal.wechat_keys import recover_legacy_keys
            keys.update(recover_legacy_keys(self, keys))
        return keys

    def _collect_key_candidates'''
    # The downloaded upstream bytes have LF line endings.
    if data.count(before)!=1:raise ValueError('WeChat extraction changed; review legacy-key fallback')
    notice=b'# Modified by Tulpa contributors: bounded legacy key recovery; see scripts/reader_patches.py.\n'
    return notice+data.replace(before,after)


def patch_qq_helper(data):
    # One pure window function used by our historical export; do not permit
    # WITH/RECURSIVE, arbitrary functions/tables, writes or extension loading.
    before=b'_ALLOWED_SQL_FUNCTIONS = frozenset({"count", "max", "nullif", "trim"})'
    after=b'_ALLOWED_SQL_FUNCTIONS = frozenset({"count", "max", "nullif", "trim", "row_number"})'
    if data.count(before)!=1:raise ValueError('QQ function allowlist changed; review history-window adaptation')
    return patch_qq_directory_tables(data.replace(before,after))


def patch_qq_directory_tables(data):
    # Fixed, known schema generations only; no arbitrary table/SQL access.
    before=b'        "profile_info_v6",'
    after=b'        "profile_info",\n'+b''.join(f'        "profile_info_v{i}",\n'.encode() for i in range(1,6))+before
    if data.count(before)!=1:raise ValueError('QQ directory allowlist changed')
    if b'        "profile_info_v5",' in data:return data
    return data.replace(before,after)
