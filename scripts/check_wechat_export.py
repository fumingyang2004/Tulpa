"""Selection regression only: synthetic catalog, no client or cloud access."""
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from scripts.export_wechat import select_chats


def main():
    chats=[dict(username=f'person-{i}',md5=f'hash-{i}',message_count=100)
           for i in range(70)]
    extra=[dict(username='gh_public',md5='public-hash',message_count=1),
           dict(username='filehelper',md5='helper-hash',message_count=1),
           dict(username='unknown-hash',md5='unknown-hash',message_count=1),
           dict(username='empty',md5='empty-hash',message_count=0)]
    selected,coverage=select_chats(chats+extra)
    assert selected==chats, 'Older conversations must not be limited to recent 15/60'
    assert coverage['excluded']==dict(service=2,unresolved=1,empty=1)
    assert select_chats(chats,['person-22','hash-22'])[0]==[chats[22]]
    for catalog,requested in [(extra,None),([],None),(chats,['not-present'])]:
        try:
            select_chats(catalog,requested)
        except ValueError:
            pass
        else:
            raise AssertionError('Empty/invalid selection must never mean export all')
    print('PASS: all 70 ordinary chats, explicit selection, exclusion counts, empty/invalid selection rejected. No client/cloud access.')


if __name__=='__main__':
    main()
