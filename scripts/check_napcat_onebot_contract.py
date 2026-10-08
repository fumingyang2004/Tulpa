"""Existing Tulpa receiver against synthetic NapCat-style WebSocket frames.

No QQ process, settings, HTTP account, or upstream source code is executed.
The printed auth classification is an observation, not a compatibility pass
for every NapCat feature. All events and credentials below are invented.
"""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from websockets.sync.server import serve
from chatlocal.onebot_events import EventReceiver
from chatlocal.mcp_chat_media import image_ref


def check(reject):
    delivered, states, headers = [], [], []

    def handle(ws):
        headers.append(ws.request.headers.get('Authorization') == 'Bearer synthetic-event-token')
        if reject:
            # NapCat authorizes after upgrade, unlike HTTP-handshake 401/403.
            ws.send(json.dumps({'status': 'failed', 'retcode': 1403, 'data': None,
                                'message': 'synthetic rejection'}))
            ws.close()
            return
        ws.send(json.dumps({'post_type': 'meta_event', 'meta_event_type': 'lifecycle', 'self_id': 111}))
        ws.send(json.dumps({'post_type': 'message', 'message_type': 'group', 'self_id': 111,
                            'group_id': 222, 'user_id': 333, 'message_id': 700,
                            'message': [{'type': 'text', 'data': {'text': 'synthetic event'}}]}))
        for _ in ws:
            pass

    server = serve(handle, '127.0.0.1', 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    receiver = EventReceiver(delivered.append, states.append,
        config_factory=lambda: dict(url=f'ws://127.0.0.1:{server.socket.getsockname()[1]}/',
                                    token='synthetic-event-token'),
        client_factory=lambda **kw: SimpleNamespace(login=lambda: '111'))
    receiver.start()
    try:
        deadline = time.monotonic()+4
        while time.monotonic() < deadline:
            if reject and any(state in ('disconnected', 'auth_error') for state in states):
                break
            if not reject and any(event.get('message_id') == 700 for event in delivered):
                break
            time.sleep(.01)
        state = receiver.status()['state']
        assert headers and all(headers)
        if reject:
            assert not delivered and 'connected' not in states
        else:
            assert state == 'connected' and any(event.get('message_id') == 700 for event in delivered)
        return dict(state=state, messages_delivered=sum(event.get('post_type') == 'message' for event in delivered))
    finally:
        receiver.stop()
        server.shutdown()
        thread.join(3)


def rotated_file_ids():
    from chatlocal.store import Store
    from chatlocal.artifact_onebot import upsert
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(dir=root/'.tmp', prefix='napcat-file-id-') as name:
        store = Store(Path(name)/'fixture.sqlite3')
        descriptor = dict(file_name='synthetic.txt', file_size=8, upload_time=1772140800, uploader=333)
        with store.connect() as db:
            # Two enumerations of the same native file can yield new opaque IDs.
            upsert(db, '111:group:222', 'Synthetic group', dict(descriptor, file_id='synthetic-first'))
            upsert(db, '111:group:222', 'Synthetic group', dict(descriptor, file_id='synthetic-refreshed'))
            count = db.execute('SELECT count(*) FROM artifact_sources').fetchone()[0]
        assert count >= 1
        return count


if __name__ == '__main__':
    success, denied = check(False), check(True)
    native_face = dict(resId='synthetic-res', url='https://gchat.qpic.cn/synthetic.png', desc='synthetic')
    assert image_ref(native_face) == {'url': native_face['url']}
    print(json.dumps(dict(synthetic=True, lifecycle_and_group=success,
                          post_upgrade_auth_rejection=denied,
                          auth_error_classified=denied['state'] == 'auth_error',
                          artifact_records_after_rotated_file_id=rotated_file_ids(),
                          all_napcat_features_validated=False), indent=2))
