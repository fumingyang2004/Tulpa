"""Use the upstream database reader only to unlock a local snapshot for wx-cli.

No client modification, UI automation, network request or message sending.
"""
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from chatlocal.config import DATA

os.environ['WECHATAUTO_KEYS_DIR'] = str(DATA/'wechat-reader'/'keys')
sys.path.insert(0,str(ROOT/'tools'/'wechat-reader'))
from wechatauto.db import WeChatDB


def main():
    # wx-cli already copied the encrypted files into this folder.
    snapshot = DATA/'wechat-snapshot'
    account = 'local_snapshot'
    reader_root = DATA/'wechat-reader-source'
    target = reader_root/account/'db_storage'
    if not snapshot.is_dir():
        raise SystemExit('先准备 data/wechat-snapshot 数据库副本。')
    if not target.exists():
        shutil.copytree(snapshot,target)
    reader = WeChatDB(db_dir=str(reader_root),account=account,
        workdir=str(DATA/'wechat-reader'/'work'),
        keys_file=str(DATA/'wechat-reader'/'keys.json'))
    keys = {rel.replace('\\','/'):{'enc_key':key[:32].hex()}
            for rel,key in reader._keys.items() if reader._key_works(rel)}
    state = DATA/'wechat-state'
    if keys:
        (state/'all_keys.json').write_text(json.dumps(keys),encoding='utf-8')
    print(json.dumps({'verified_databases':len(keys),'missing':reader.unkeyed},ensure_ascii=False))


if __name__=='__main__':
    main()
