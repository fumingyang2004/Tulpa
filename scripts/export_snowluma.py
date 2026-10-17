"""The normal QQ import worker entry point, using managed SnowLuma history."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from chatlocal.config import local_path
    from chatlocal.snowluma_history import export_history
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--account')
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--chat', action='append')
    parser.add_argument('--start', default='')
    parser.add_argument('--end', default='')
    parser.add_argument('--date-range', action='store_true')
    parser.add_argument('--days', type=int, default=30)
    parser.add_argument('--per-chat', type=int, default=500)
    parser.add_argument('--output', default='imports/qq-real.json')
    parser.add_argument('--no-stickers', action='store_true')
    parser.add_argument('--incremental-request')
    parser.add_argument('--batch-dir')
    parser.add_argument('--all-messages', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.days <= 3650 or not 1 <= args.per_chat <= 10000:
        raise ValueError('days 范围 1–3650，per-chat 范围 1–10000')
    if args.all_messages and not args.batch_dir:
        raise ValueError('读取全部消息需要分批导入模式')
    if args.batch_dir: os.environ['CHATWEAVE_BATCH_DIR'] = str(local_path(args.batch_dir))
    print(json.dumps(export_history(args), ensure_ascii=False))


if __name__ == '__main__': main()
