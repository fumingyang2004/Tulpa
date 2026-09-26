"""Small reproducible adaptations to the pinned, read-only upstream modules."""

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
