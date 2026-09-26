import argparse
import json
from pathlib import Path

from .store import Store
from .retrieval import make_plan, retrieve
from .llm import answer
from .render import scope_text


def main():
    parser = argparse.ArgumentParser(description='本机聊天导入 / 检索 / 问答')
    sub = parser.add_subparsers(dest='command',required=True)
    imp = sub.add_parser('import')
    imp.add_argument('path')
    imp.add_argument('--platform',default='')
    imp.add_argument('--conversation',default='')
    imp.add_argument('--self-ids',default='')
    imp.add_argument('--no-stickers',action='store_true',help='表情包及动态图仅保留 [动画表情]')
    sub.add_parser('status')
    for name in ('search','ask'):
        cmd=sub.add_parser(name)
        cmd.add_argument('question')
        cmd.add_argument('--start',default='')
        cmd.add_argument('--end',default='')
        cmd.add_argument('--keywords',default='')
        cmd.add_argument('--platform',action='append',choices=['qq','wechat'])
    args=parser.parse_args()
    store=Store()
    if args.command=='import':
        result=store.import_file(args.path,args.platform,args.conversation,args.self_ids,load_stickers=not args.no_stickers)
    elif args.command=='status':
        result=store.stats()
    else:
        bundle=retrieve(store,make_plan(args.question,platforms=args.platform,start=args.start,end=args.end,keywords=args.keywords))
        result={'scope':scope_text(bundle),'evidence':bundle['messages']}
        if args.command=='ask':
            result['answer']=answer(args.question,bundle)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
