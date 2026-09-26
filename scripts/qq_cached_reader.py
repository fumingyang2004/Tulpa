"""Small adapter around chatlog-keeper's page authentication and WAL recovery."""
import hashlib
import os
from page_cache import update_pages
from reader_metrics import count, timed


@timed('decrypt')
def decrypt_database(q, source, key, destination):
    from chatlog_keeper.core._wal import apply_wal
    key = key.encode('ascii') if isinstance(key, str) else bytes(key)
    with source.open('rb') as handle:
        handle.seek(q._NTQQ_WRAPPER_SIZE)
        first = handle.read(q._NTQQ_PAGE_SIZE)
        handle.seek(q._NTQQ_WRAPPER_SIZE + (q._NTQQ_WRAPPED_LOCK_PAGE_NO-1)*q._NTQQ_PAGE_SIZE)
        lock_page = handle.read(q._NTQQ_PAGE_SIZE)
    resolved = q._resolve_qq_cipher(key, first)
    if resolved is None:
        raise ValueError('QQ 数据库认证失败；未导入。')
    kdf, algo, reserve = resolved
    salt = first[:16]
    aes_key = hashlib.pbkdf2_hmac(kdf, key, salt, 4000, dklen=32)
    mac_key = hashlib.pbkdf2_hmac(kdf, aes_key, bytes(v^0x3a for v in salt), 2, dklen=32)
    shifted = q._is_wrapped_windows_lock_placeholder(lock_page, page_no=q._NTQQ_WRAPPED_LOCK_PAGE_NO,
        source_wrapper_size=q._NTQQ_WRAPPER_SIZE, platform_name=os.name)
    def decrypt(page, page_no):
        return q._decrypt_qq_page(page, page_no=page_no, salt=salt, aes_key=aes_key,
            mac_key=mac_key, hmac_algo=algo, reserve=reserve)
    def main_page(page, page_no):
        plain = decrypt(page, page_no)
        if plain is None and q._is_wrapped_windows_lock_placeholder(page, page_no=page_no,
                source_wrapper_size=q._NTQQ_WRAPPER_SIZE, platform_name=os.name):
            return b'\0'*q._NTQQ_PAGE_SIZE
        return plain
    def wal(target):
        touched = set()
        def decode(page, number):
            touched.add(number)
            return decrypt(page, number)
        count('wal_frames', apply_wal(source.with_name(source.name+'-wal'), target, decode,
            shm_path=source.with_name(source.name+'-shm'), expected_page_size=q._NTQQ_PAGE_SIZE))
        return touched
    def validate(target):
        conn = q._open_qq_sqlite_connection(q._QQDecryptedDatabase(target, shifted))
        try:
            # The isolated Windows shifted-lock helper already runs this check.
            if not shifted and conn.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise ValueError('QQ 解密副本完整性检查失败；未推进进度。')
        finally:
            conn.close()
    update_pages(source, destination, main_page, offset=q._NTQQ_WRAPPER_SIZE,
        identity=hashlib.sha256(b'qq-cache-v1'+key+salt).hexdigest(), apply_wal=wal, validate=validate)
    return q._QQDecryptedDatabase(destination, shifted)
