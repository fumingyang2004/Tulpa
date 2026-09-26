"""Bounded producer protocol for local readers. No message bodies in progress."""
import json
import os
import shutil
import time
from pathlib import Path
from .config import local_path

BATCH_ROWS = 500
BATCH_BYTES = 4 * 1024 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_PENDING = 3
RESERVE_BYTES = 512 * 1024 * 1024


def atomic_json(path, value):
    path = local_path(path)
    staging = path.with_suffix(path.suffix + '.tmp')
    with staging.open('w', encoding='utf-8') as out:
        json.dump(value, out, ensure_ascii=False, separators=(',', ':'))
        out.flush()
        os.fsync(out.fileno())
    for attempt in range(20):
        try:os.replace(staging, path);break
        except PermissionError:
            if attempt==19:raise
            time.sleep(.025)


def stage(name, **counts):
    if not (folder := os.environ.get('CHATWEAVE_BATCH_DIR')):
        return
    allowed = {'exported', 'scanned', 'total', 'skipped', 'batch', 'conversations', 'invalid_timestamps'}
    payload = dict(stage=name, at=time.time(), **{k:v for k,v in counts.items() if k in allowed and isinstance(v, (int, float))})
    atomic_json(local_path(folder) / 'progress.json', payload)


class BatchWriter:
    """At most 500 rows/4 MiB per chunk and three unacknowledged chunks.

    The consumer deletes a chunk only after its transaction receipt is durable.
    Interrupted producers never publish a completion manifest.
    """
    def __init__(self, folder, header):
        self.folder = local_path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.header = header
        self.rows = []
        self.size = self.count = self.number = self.media = self.available = self.unknown = self.texts = 0
        self.conversations = set()
        self.total = None
        self.scanned = 0
        self.last_update = 0

    def __len__(self):
        return self.count

    def observe(self, amount=1):
        if (self.folder/'stop').exists():raise ValueError('读取任务已停止；已完成批次保留')
        self.scanned += amount
        if time.monotonic() - self.last_update >= 1:
            stage('reading', exported=self.count, scanned=self.scanned, total=self.total)
            self.last_update = time.monotonic()

    def append(self, message):
        size = len(json.dumps(message, ensure_ascii=False).encode('utf-8')) + 1
        if size > BATCH_BYTES:
            raise ValueError('单条记录超过 4 MiB 安全限制；已完成批次保留，请缩小范围并检查诊断。')
        if self.rows and (len(self.rows) >= BATCH_ROWS or self.size + size > BATCH_BYTES):
            self.flush()
        self.rows.append(message)
        self.size += size
        self.count += 1
        self.conversations.add(message.get('conversation_id', message.get('chat', '')))
        items = message.get('media', [])
        self.media += len(items)
        self.available += sum(m.get('status') == 'available' for m in items)
        self.unknown += message.get('is_self') is None
        self.texts += (int(message.get('type_code', 0)) & 0xffffffff) == 1 and bool(message.get('content'))

    def flush(self):
        if not self.rows:
            return
        while len(list(self.folder.glob('batch-*.json'))) >= MAX_PENDING:
            if (self.folder/'stop').exists():raise ValueError('读取任务已停止；已完成批次保留')
            lease = self.folder / 'lease'
            if not lease.exists() or time.time() - lease.stat().st_mtime > 90:
                raise ValueError('导入接收端已离线；已生成批次保留，可重新继续。')
            stage('waiting_for_commit', exported=self.count, scanned=self.scanned, total=self.total)
            time.sleep(.2)
        if shutil.disk_usage(self.folder).free < RESERVE_BYTES:
            raise ValueError('磁盘可用空间不足 512 MiB，已暂停；已完成批次保留。')
        head = self.header(self.rows) if callable(self.header) else dict(self.header)
        self.number += 1
        payload = dict(head, messages=self.rows, _partial_export=True)
        if len(json.dumps(payload, ensure_ascii=False).encode('utf-8')) > MAX_FILE_BYTES:
            raise ValueError('单批记录元数据过大；已完成批次保留。')
        atomic_json(self.folder / f'batch-{self.number:08}.json', payload)
        stage('reading', exported=self.count, scanned=self.scanned, total=self.total, batch=self.number)
        self.rows = []
        self.size = 0

    def finish(self, header):
        self.flush()
        payload = dict(header, messages=[], batch_count=self.number, exported_messages=self.count)
        atomic_json(self.folder / 'complete.json', payload)
        stage('exported', exported=self.count, scanned=self.scanned, total=self.total, batch=self.number)
        return payload
