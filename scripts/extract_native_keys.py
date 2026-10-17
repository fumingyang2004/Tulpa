"""Explicit local-account key acquisition. Never print or return key material."""
import argparse
import json
import os
from pathlib import Path
import platform
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tools/qq-reader'),str(ROOT/'tools/wechat-reader')]
os.environ['CHATLOG_KEEPER_DATA_DIR']=str(ROOT/'data/qq-reader')


def extract(source,account=None,method='passive',confirm=False):
    from chatlocal.native_sources import configured_root
    from chatlocal.client_accounts import choose_account
    from chatlog_keeper import qq_db as q,wechat_db as wx
    if source=='qq':
        root=configured_root(source) or q.find_qq_data_root()
        accounts=q.find_qq_account_databases(root) if root else {}
    else:
        root=configured_root(source) or wx.find_weixin_data_root()
        accounts={p.name:p for p in wx.find_wxid_dirs(root)} if root else {}
    account=choose_account(source,accounts,account)
    if method=='active':
        if not confirm:raise ValueError('主动取钥会启动一次官方客户端；请正常退出原客户端后使用 --confirm-start-client。')
        if sys.platform == 'darwin':
            sys.path.insert(0, str(ROOT/'macos'))
            from reader_recovery import recover_qq, recover_wechat
            result = recover_qq() if source == 'qq' else recover_wechat()
            return dict(result, account=account, method=method)
        from chatlog_keeper import linux_key
        if platform.machine() not in ('x86_64','AMD64'):
            raise ValueError('上游主动取钥未验证当前 CPU 架构，请使用已有密钥 JSON 或被动读取。')
        if source=='qq':
            key=linux_key.extract_qq_passphrase_active(db_path=str(accounts[account]))
            ok=bool(key and q.save_cached_key_for_account(key,account))
        else:
            databases=wx.find_msg_databases(accounts[account])
            if not databases:raise ValueError('所选账号没有消息数据库。')
            key=linux_key.extract_wechat_key_active(db_path=str(databases[0]))
            ok=bool(key and wx.save_cached_wechat_key_for_account(key,account))
        return dict(ok=ok,account=account,method=method,code='' if ok else linux_key.last_error() or 'key_unavailable')
    if source=='qq':
        reader=q.QQDBReader(data_root=root,account_id=account,live_key_timeout_s=30)
        reader.initialize();ok=bool(reader.key)
    else:
        from wechatauto.db import WeChatDB
        os.environ['WECHATAUTO_KEYS_DIR']=str(ROOT/'data/wechat-reader/keys')
        reader=WeChatDB(db_dir=str(root),account=account,workdir=str(ROOT/'data/wechat-reader/work'),keys_file=str(ROOT/'data/wechat-reader/keys.json'))
        ok=bool(reader._keys)
    return dict(ok=ok,account=account,method=method,code='' if ok else 'key_unavailable')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',choices=['qq','wechat'])
    p.add_argument('--account');p.add_argument('--method',choices=['passive','active'],default='passive')
    p.add_argument('--confirm-start-client',action='store_true');args=p.parse_args()
    result=extract(args.source,args.account,args.method,args.confirm_start_client)
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result['ok'] else 1


if __name__=='__main__':raise SystemExit(main())
