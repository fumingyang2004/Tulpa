"""Synthetic unnamed WeChat group label coverage."""
import sys
from pathlib import Path
sys.path[:0]=[str(Path(__file__).resolve().parent),str(Path(__file__).resolve().parents[1])]
import sqlite3
from chatlocal.wechat_names import add_unnamed_groups
from reader_patches import patch_wechat_group_names

def main():
    with sqlite3.connect(':memory:') as contacts:
        contacts.executescript('''
            CREATE TABLE contact(id INTEGER,username TEXT,nick_name TEXT,remark TEXT);
            CREATE TABLE chat_room(id INTEGER,username TEXT);
            CREATE TABLE chatroom_member(room_id INTEGER,member_id INTEGER);
            INSERT INTO contact VALUES (1,'self','自己',''),(2,'a','甲','联系人备注'),
                (3,'b','乙',''),(4,'c','丙','');
            INSERT INTO chat_room VALUES (1,'unnamed@chatroom'),(2,'named@chatroom');
            INSERT INTO chatroom_member VALUES (1,1),(1,2),(1,3),(1,4),(2,2);
        ''')
        names={'unnamed@chatroom':'unnamed@chatroom','named@chatroom':'正式群名'}
        add_unnamed_groups(contacts,names,'self')
        assert names=={'unnamed@chatroom':'联系人备注、乙、丙（4人群）','named@chatroom':'正式群名'}
        add_unnamed_groups(contacts,names,'self')
        assert names['named@chatroom']=='正式群名'
    with sqlite3.connect(':memory:') as old_contacts:
        names={'existing':'保留'}
        add_unnamed_groups(old_contacts,names)
        assert names=={'existing':'保留'}
    fixture=b'                    idx[u] = r or n or u\n            finally:'
    assert b'add_unnamed_groups(conn, idx, self.wxid)' in patch_wechat_group_names(fixture)
    print('PASS: unnamed group names, contact remarks, self exclusion and existing names')

if __name__=='__main__':main()
