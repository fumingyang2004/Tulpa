"""Bounded, read-only Windows QQ passphrase scanning and page authentication.

Installed as chatlog_keeper._tulpa_passive_keys. No hooks, injection, dumps,
network requests or unverified keys. Diagnostics contain counters and codes only.
"""
import ctypes
from ctypes import wintypes as wt
import hashlib
import hmac
import json
import logging
import re
import struct
import time

CHUNK_BYTES = 1024 * 1024
MAX_BYTES = 2 * 1024**3
MAX_REGIONS = 65536
MAX_CANDIDATES = 8192
MAX_SECONDS = 120.0
READABLE = {0x02, 0x04, 0x08, 0x20, 0x40, 0x80}
WRITABLE = {0x04, 0x08, 0x40, 0x80}
# Match maximal ASCII runs, not suffixes of longer strings. 34 carry bytes
# preserve both the left delimiter and a 32-byte candidate crossing a chunk.
CANDIDATES = re.compile(rb'(?<![\x20-\x7e])([\x20-\x7e]{16}|[\x20-\x7e]{32})\x00')
LOGGER = logging.getLogger('qq-reader.passive')


class MemoryBasicInformation(ctypes.Structure):
    _fields_ = [('BaseAddress', ctypes.c_uint64), ('AllocationBase', ctypes.c_uint64),
                ('AllocationProtect', wt.DWORD), ('alignment1', wt.DWORD),
                ('RegionSize', ctypes.c_uint64), ('State', wt.DWORD),
                ('Protect', wt.DWORD), ('Type', wt.DWORD), ('alignment2', wt.DWORD)]


def verify_key(candidate, db_raw, combos):
    """Same page-1 HMAC checks as upstream; derive each KDF only once per key."""
    try:
        if isinstance(candidate, str):
            candidate = candidate.encode('ascii')
        if len(db_raw) == 4096:
            page = db_raw
        elif len(db_raw) >= 5120:
            page = db_raw[1024:5120]
        else:
            return False
        salt = page[:16]
        mac_salt = bytes(value ^ 0x3a for value in salt)
        mac_keys = {}
        for kdf, algorithm, reserve in combos:
            if kdf not in ('sha1', 'sha512') or algorithm not in ('sha1', 'sha512'):
                continue
            length = 20 if algorithm == 'sha1' else 64
            if not 16+length <= reserve < 4096-16:
                continue
            if kdf not in mac_keys:
                aes_key = hashlib.pbkdf2_hmac(kdf, candidate, salt, 4000, dklen=32)
                mac_keys[kdf] = hashlib.pbkdf2_hmac(kdf, aes_key, mac_salt, 2, dklen=32)
            offset = 4096-reserve
            stored = page[offset+16:offset+16+length]
            computed = hmac.new(mac_keys[kdf], page[16:offset+16]+struct.pack('<I', 1), algorithm).digest()
            if hmac.compare_digest(computed, stored):
                return True
        return False
    except (ValueError, TypeError, OverflowError, UnicodeError):
        return False


def region_inventory(kernel, handle, deadline, stats, raise_denied, last_error):
    regions = []
    address = 0
    for _ in range(MAX_REGIONS):
        if time.monotonic() >= deadline:
            stats['stop'] = 'timeout'
            break
        mbi = MemoryBasicInformation()
        if not kernel.VirtualQueryEx(handle, ctypes.c_void_p(address), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            code = last_error(kernel)
            if code in (5, 1314):
                stats['stop'] = 'permission_denied'
            raise_denied(code)
            break
        base, size = int(mbi.BaseAddress), int(mbi.RegionSize)
        end = base+size
        if end <= address:
            stats['stop'] = 'invalid_region'
            break
        stats['regions_queried'] += 1
        if mbi.State == 0x1000 and not mbi.Protect & 0x100 and (mbi.Protect & 0xff) in READABLE and size > 0:
            # Heap/private writable data first; still inspect other readable
            # mappings when budget remains. Never allocate a whole region.
            priority = (mbi.Type != 0x20000, (mbi.Protect & 0xff) not in WRITABLE)
            regions.append((priority, base, size))
        address = end
        if address >= 0x7fffffffffff:
            break
    else:
        stats['stop'] = 'region_limit'
    return sorted(regions)


def scan(pid, db_path, timeout_s, *, kernel, read_verification, verify, raise_denied, last_error):
    """Read at most 1 MiB at a time; check time inside matching and verification."""
    start = time.monotonic()
    budget = min(MAX_SECONDS, max(0.1, float(timeout_s if timeout_s is not None else MAX_SECONDS)))
    deadline = start+budget
    stats = dict(pid=int(pid), regions_queried=0, regions_read=0, bytes_read=0,
                 candidates=0, read_failures=0, verified=False, stop='not_found')
    handle = None
    try:
        page = read_verification(db_path) if db_path is not None else None
        if not page or len(page) < 5120:
            stats['stop'] = 'database_header_unavailable'
            return None
        handle = kernel.OpenProcess(0x0010 | 0x0400, False, pid)
        if not handle:
            code = last_error(kernel)
            stats['stop'] = 'permission_denied' if code in (5, 1314) else 'process_unavailable'
            raise_denied(code)
            return None
        regions = region_inventory(kernel, handle, deadline, stats, raise_denied, last_error)
        seen = set()
        attempted = 0
        for _, base, size in regions:
            stats['regions_read'] += 1
            tail = b''
            for offset in range(0, size, CHUNK_BYTES):
                if time.monotonic() >= deadline:
                    stats['stop'] = 'timeout'
                    return None
                if attempted >= MAX_BYTES:
                    stats['stop'] = 'byte_limit'
                    return None
                length = min(CHUNK_BYTES, size-offset, MAX_BYTES-attempted)
                buffer = ctypes.create_string_buffer(length)
                count = ctypes.c_size_t()
                success = kernel.ReadProcessMemory(handle, ctypes.c_void_p(base+offset), buffer, length, ctypes.byref(count))
                attempted += length
                if not success:
                    code = last_error(kernel)
                    if code in (5, 1314):
                        stats['stop'] = 'permission_denied'
                    raise_denied(code)
                    stats['read_failures'] += 1
                actual = min(length, count.value)
                stats['bytes_read'] += actual
                if not actual:
                    tail = b''
                    continue
                data = buffer.raw[:actual]
                chunk = tail+data
                # Position zero of a carried prefix may be the suffix of a
                # longer ASCII run. It was already considered in the prior chunk.
                skip_zero = bool(tail) or offset > 0
                for match in CANDIDATES.finditer(chunk):
                    if skip_zero and match.start(1) == 0:
                        continue
                    if time.monotonic() >= deadline:
                        stats['stop'] = 'timeout'
                        return None
                    candidate = match.group(1)
                    if candidate in seen:
                        continue
                    if len(seen) >= MAX_CANDIDATES:
                        stats['stop'] = 'candidate_limit'
                        return None
                    seen.add(candidate)
                    stats['candidates'] += 1
                    if verify(candidate, page):
                        stats.update(verified=True, stop='verified')
                        return candidate
                tail = data[-34:] if actual == length else b''
        return None
    except Exception as exc:
        if stats['stop'] == 'permission_denied':
            raise
        stats.update(stop='scan_error', error_type=type(exc).__name__)
        return None
    finally:
        if handle:
            kernel.CloseHandle(handle)
        stats['seconds'] = round(time.monotonic()-start, 3)
        # Do not log candidates, addresses, page bytes, paths or account IDs.
        LOGGER.warning('QQ_SCAN %s', json.dumps(stats, separators=(',', ':')))
