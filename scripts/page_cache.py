"""Reuse verified plaintext blocks, always reconstructing the current base + WAL.

This is a disposable reader cache, never the imported message database. A
manifest is invalidated BEFORE any mutation and published only after validation.
Old WAL-touched blocks are restored from the current encrypted main file even
when its bytes did not change: WAL reset/shrink must not preserve stale pages.
"""
import hashlib
import json
import os
from pathlib import Path
from reader_metrics import count, measure

PAGE = 4096
BLOCK = 64 * 1024
VERSION = 1


def signature(path):
    stat = Path(path).stat()
    return [stat.st_size, stat.st_mtime_ns]


def update_pages(source, destination, decode, *, identity, offset=0, apply_wal, validate):
    source, destination = Path(source), Path(destination)
    manifest = destination.with_suffix(destination.suffix+'.pages.json')
    old = {}
    try:
        saved = json.loads(manifest.read_text(encoding='utf-8'))
        if (isinstance(saved, dict) and saved.get('version') == VERSION and saved.get('identity') == identity
                and isinstance(saved.get('blocks'),list) and all(isinstance(b,list) and len(b)==2 and
                    all(isinstance(v,str) and len(v)==64 for v in b) for b in saved['blocks'])
                and isinstance(saved.get('wal_blocks'),list) and all(type(n) is int and n>=0 for n in saved['wal_blocks'])
                and saved.get('output') == signature(destination)):
            old = saved
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    source_before = signature(source)
    size = source_before[0]-offset
    if size < PAGE or size % PAGE:
        raise ValueError('加密数据库页不完整；未推进进度。')
    manifest.unlink(missing_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    previous = old.get('blocks', [])
    dirty = set(old.get('wal_blocks', []))
    blocks = []
    with measure('page_cache'), source.open('rb') as src, destination.open('r+b' if old else 'w+b') as out:
        src.seek(offset)
        index = 0
        while encrypted := src.read(BLOCK):
            enc_hash = hashlib.sha256(encrypted).hexdigest()
            prior = previous[index] if index < len(previous) else None
            out.seek(index*BLOCK)
            # Verify plaintext too, so a damaged cache never changes evidence.
            plain = out.read(len(encrypted)) if prior and index not in dirty and prior[0] == enc_hash else None
            if plain is not None and len(plain) == len(encrypted) and hashlib.sha256(plain).hexdigest() == prior[1]:
                plain_hash = prior[1]
                count('reused_pages', len(encrypted)//PAGE)
            else:
                parts = []
                for start in range(0, len(encrypted), PAGE):
                    page_no = (index*BLOCK+start)//PAGE+1
                    value = decode(encrypted[start:start+PAGE], page_no)
                    if value is None or len(value) != PAGE:
                        raise ValueError('数据库页认证失败；未推进进度。')
                    parts.append(value)
                plain = b''.join(parts)
                out.seek(index*BLOCK)
                out.write(plain)
                plain_hash = hashlib.sha256(plain).hexdigest()
                count('decrypted_pages', len(parts))
            blocks.append([enc_hash, plain_hash])
            index += 1
        out.truncate(size)
        out.flush()
    with measure('wal'):
        touched = apply_wal(destination)
    if signature(source) != source_before:
        raise ValueError('读取副本在解密期间变化；未推进进度。')
    with measure('validate'):
        validate(destination)
    pending = manifest.with_suffix('.tmp')
    pending.write_text(json.dumps(dict(version=VERSION, identity=identity, blocks=blocks,
        wal_blocks=sorted({(p-1)*PAGE//BLOCK for p in touched}), output=signature(destination))), encoding='utf-8')
    os.replace(pending, manifest)
