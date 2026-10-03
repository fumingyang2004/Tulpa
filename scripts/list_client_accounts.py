"""List account directory names only; no keys, snapshots or message databases opened."""
import contextlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def discover(platform):
    if platform == 'qq':
        sys.path.insert(0, str(ROOT/'tools/qq-reader'))
        from chatlog_keeper.qq_db import find_qq_data_root, find_qq_account_databases
        root = find_qq_data_root()
        return list(find_qq_account_databases(root)) if root else []
    if platform == 'wechat':
        sys.path.insert(0, str(ROOT/'tools/wechat-reader'))
        from wechatauto.db import auto_detect_db_dir, _find_account_dirs
        root = auto_detect_db_dir()
        return [Path(p).name for p in _find_account_dirs(root)] if root else []
    raise ValueError('Invalid platform')


if __name__ == '__main__':
    with contextlib.redirect_stdout(sys.stderr):
        accounts = discover(sys.argv[1])
    print(json.dumps(accounts, ensure_ascii=False))
