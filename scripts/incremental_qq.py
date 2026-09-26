"""QQ rowid is message UID, NOT an insertion counter. Compare ID inventories.

Only compact integer identities are scanned; only unseen rows are decoded and
their media restored. Compressed inventories commit atomically with messages.
"""
from array import array
import hashlib
import zlib
from pathlib import Path
from reader_metrics import timed
from qq_message_types import SUPPORTED_SQL

METHOD='native-id-inventory-v1'


@timed('inventory')
def delta_rows(db,spec,request,request_path):
    checkpoint=request['checkpoint'];current=[r[0] for r in db.execute(f'SELECT rowid FROM "{spec.table}" ORDER BY rowid')]
    prior=None
    if checkpoint.get('method')==METHOD:
        file=request.get('inventory',{}).get(spec.table)
        if not file:raise ValueError('QQ 原生消息身份目录缺失，未推进进度，请补读后重新接续。')
        packed=Path(file).read_bytes()
        if hashlib.sha256(packed).hexdigest()!=checkpoint['cursors'][spec.table]['inventory_sha256']:
            raise ValueError('QQ 身份目录校验失败，未推进进度。')
        values=array('q');values.frombytes(zlib.decompress(packed));prior=set(values)
        current_set=set(current)
        if prior-current_set:raise ValueError('QQ 原有消息已被清理或数据库被替换；请补读历史后重新接续。')
        new=sorted(current_set-prior)
    else:new=None
    packed=zlib.compress(array('q',current).tobytes(),level=3)
    Path(request_path).with_name(spec.table+'.next.bin').write_bytes(packed)
    sequences=[dict(conversation_id=str(row[0]),last_seq=row[1],last_timestamp=row[2]) for row in db.execute(
        f'SELECT "{spec.conversation_column}",max("40003"),max("40050") FROM "{spec.table}" GROUP BY "{spec.conversation_column}"')]
    cursor=dict(count=len(current),inventory_sha256=hashlib.sha256(packed).hexdigest(),conversations=sequences)
    select=f'''SELECT "40001","40050","40090","40033","40800","{spec.conversation_column}",
        {spec.table_rank},rowid,'{spec.conversation_type}',"40020","40012","40011" FROM "{spec.table}"'''
    supported=SUPPORTED_SQL
    selected=request.get('conversations')
    peers=[value.split(':',2)[2] for value in selected or []
           if value.startswith(checkpoint.get('account', '')+':'+spec.conversation_type+':')]
    # During bootstrap the account is provided by the exporter, not checkpoint.
    if selected is not None and not checkpoint.get('account'):
        peers=[value.split(':',2)[2] for value in selected if len(value.split(':',2))==3 and value.split(':',2)[1]==spec.conversation_type]
    scope=f' AND "{spec.conversation_column}" IN ({",".join("?" for _ in peers)})' if peers else ''
    def rows():
        if selected is not None and not peers:return
        if new is None:
            yield from db.execute(select+f' WHERE {supported} AND "40050">=? {scope} ORDER BY "40050",rowid',(request['bootstrap_since'],*peers))
        else:
            for offset in range(0,len(new),800):
                page=new[offset:offset+800]
                yield from db.execute(select+f' WHERE {supported} AND rowid IN ({",".join("?" for _ in page)}) {scope} ORDER BY "40050",rowid',[*page,*peers])
    return rows(),cursor
