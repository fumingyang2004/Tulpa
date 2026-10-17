"""Display unnamed WeChat groups using members from the same contact snapshot."""


def add_unnamed_groups(conn, names, self_id=''):
    tables={row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'chat_room','chatroom_member','contact'} <= tables:
        return
    groups={}
    for room, member, nickname, remark in conn.execute('''
        SELECT r.username,c.username,c.nick_name,c.remark FROM chat_room r
        JOIN chatroom_member m ON m.room_id=r.id JOIN contact c ON c.id=m.member_id
        ORDER BY r.id,m.rowid'''):
        if names.get(room,room) not in ('',room):
            continue
        groups.setdefault(room,{})[member]=remark or nickname or member
    for room, members in groups.items():
        labels=[label for member,label in members.items() if member!=self_id]
        if labels:
            names[room]='、'.join(labels[:3])+('等' if len(labels)>3 else '')+f'（{len(members)}人群）'
