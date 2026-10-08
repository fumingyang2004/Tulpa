"""Same synthetic search hits: existing Store vs index-free history scan.

NOT a native NapCat benchmark. No QQ, snapshot/decryption, HTTP/WS transport,
server startup, or client/OS cold-cache costs are included. Each mode starts
in a fresh Python process; five warm queries follow. Results stay in .tmp.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

from napcat_qq_fixture import HistoryFixture, messages
from napcat_qq_probe import TZ

ROOT = Path(__file__).resolve().parents[1]


def peak_working_set():
    if os.name != 'nt':
        return None
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
            'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
            'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]
    count = Counters()
    count.cb = ctypes.sizeof(count)
    kernel, psapi = ctypes.WinDLL('kernel32'), ctypes.WinDLL('psapi')
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(count), count.cb):
        return count.PeakWorkingSetSize
    return None


def child(mode):
    began = time.perf_counter()
    groups = {}
    for peer in ('222', '223', '224', '225'):
        rows = messages(500, peer)
        for n, row in enumerate(rows, 1):
            row['user_id'] = 333 if n % 2 else 444
            row['message'][0]['data']['text'] = 'fixtureanchor synthetic hit' if n % 79 == 0 else f'synthetic row {n}'
        groups[peer] = rows
    base = int(datetime(2026, 2, 27, tzinfo=TZ).timestamp())
    start, end = base+100, base+360
    expected = {(peer, str(9000000000000000000+int(row['real_seq'])))
                for peer, rows in groups.items() for row in rows
                if start <= row['time'] < end and row['user_id'] == 333
                and 'fixtureanchor' in row['message'][0]['data']['text']}
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp', prefix='napcat-bench-') as folder:
        folder = Path(folder)
        requests = []
        transferred = []
        if mode == 'store':
            sys.path.insert(0, str(ROOT))
            from chatlocal.store import Store
            from chatlocal.agent_tools import ChatTools
            from chatlocal.retrieval import Plan
            normalized = [dict(platform='qq', conversation_id=f'111:group:{peer}',
                conversation='Synthetic group', conversation_type='group', sender='Synthetic sender',
                sender_id=str(row['user_id']), timestamp=row['time'],
                source_id=str(9000000000000000000+int(row['real_seq'])),
                content=row['message'][0]['data']['text']) for peer, rows in groups.items() for row in rows]
            source = folder/'input.json'
            source.write_text(json.dumps(normalized), encoding='utf-8')
            store = Store(folder/'store.sqlite3')
            receipt = store.import_file(source)
            assert receipt['imported'] == 2000
            with store.connect() as db:
                identity = {row['id']: (row['conversation_id'].split(':')[-1], row['source_id'])
                            for row in db.execute('SELECT id,conversation_id,source_id FROM messages')}

            def query():
                tools = ChatTools(store, Plan(platforms=['qq'], start=start*1000, end=end*1000))
                result = tools.execute('search_messages', dict(query='fixtureanchor', platform='qq', sender_id='333', limit=20))
                requests.append(0)
                transferred.append(0)
                return {identity[mid] for mid in result['hit_ids']}
        else:
            clients = {peer: HistoryFixture(rows) for peer, rows in groups.items()}

            def query():
                hits, pages, total_bytes = set(), 0, 0
                for peer, client in clients.items():
                    # Deliberately re-scan: no historical index/cache in this mode.
                    client.restart()
                    before = client.response_bytes
                    cursor, seen = '0', set()
                    while True:
                        rows = client.call('get_group_msg_history',
                            dict(group_id=peer, message_seq=cursor, count=100, reverse_order=True))['messages']
                        pages += 1
                        unseen = [row for row in rows if row['message_id'] not in seen]
                        if not unseen:
                            break
                        seen.update(row['message_id'] for row in unseen)
                        hits.update((peer, str(9000000000000000000+int(row['real_seq'])))
                                    for row in unseen if start <= row['time'] < end and row['user_id'] == 333
                                    and 'fixtureanchor' in row['message'][0]['data']['text'])
                        cursor = str(min(rows, key=lambda row: int(row['real_seq']))['message_id'])
                    total_bytes += client.response_bytes-before
                requests.append(pages)
                transferred.append(total_bytes)
                return hits
        ready_ms = (time.perf_counter()-began)*1000
        times = []
        for _ in range(6):
            tick = time.perf_counter()
            actual = query()
            times.append((time.perf_counter()-tick)*1000)
            assert actual == expected, 'Synthetic hit-set mismatch'
        files = list(folder.rglob('*'))
        persisted = sum(p.stat().st_size for p in files if p.is_file() and p.name != 'input.json')
        result = dict(mode=mode, synthetic=True, conversations=4, messages=2000, matches=len(expected),
                      ready_ms=round(ready_ms, 2), first_query_ms=round(times[0], 2),
                      first_result_ms=round(ready_ms+times[0], 2), hot_median_ms=round(statistics.median(times[1:]), 2),
                      persistent_client_bytes=persisted,
                      process_peak_working_set_bytes=peak_working_set(),
                      history_requests_per_query=requests, simulated_response_bytes=transferred,
                      limitations='Client-only synthetic experiment; no native QQ/NapCat or network costs. '
                                  'Hit sets compared, not full return structures or original-history coverage.')
        print(json.dumps(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--child', choices=['store', 'api_scan'])
    args = parser.parse_args()
    if args.child:
        child(args.child)
        return
    output = []
    for mode in ('store', 'api_scan'):
        began = time.perf_counter()
        proc = subprocess.run([sys.executable, __file__, '--child', mode], cwd=ROOT,
                              env=dict(os.environ, PYTHONUTF8='1'), capture_output=True, text=True, timeout=90)
        if proc.returncode:
            raise RuntimeError(proc.stderr)  # Entirely synthetic inputs.
        result = json.loads(proc.stdout)
        result['process_wall_ms'] = round((time.perf_counter()-began)*1000, 2)
        output.append(result)
    folder = ROOT/'.tmp/napcat-regression-20261007'
    folder.mkdir(parents=True, exist_ok=True)
    (folder/'benchmark.json').write_text(json.dumps(output, indent=2), encoding='utf-8')
    print(json.dumps(output, indent=2))


if __name__ == '__main__':
    main()
