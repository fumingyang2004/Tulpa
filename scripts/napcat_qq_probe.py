"""Bounded, read-only NapCat history experiment; NOT a Tulpa backend.

No production settings, databases, ID mappings, or clients are changed. Live
mode is explicit and reads credentials/scope from a private local JSON file.
Only aggregate observations leave memory. See doc/NAPCAT_QQ_EVALUATION.md.
"""
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import argparse
import hashlib
import http.client
import ipaddress
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
TZ = timezone(timedelta(hours=8))
READ_ACTIONS = frozenset({
    'get_login_info', 'get_version_info',
    'get_group_msg_history', 'get_friend_msg_history',
    'get_msg', 'get_image', 'get_status', 'get_group_info',
    '_get_group_notice', 'get_essence_msg_list',
    'get_group_file_system_info', 'get_group_root_files', 'get_group_file_url',
})
MAX_RESPONSE = 4 * 1024 * 1024
SEGMENT_KINDS = frozenset(('text', 'image', 'reply', 'file', 'record', 'video',
                           'at', 'face', 'mface', 'forward', 'node', 'json', 'xml', 'markdown'))


class ProbeError(Exception):
    """Only fixed error codes, never third-party errors, URLs, or message text."""


def require(ok, code):
    if not ok:
        raise ProbeError(code)


def numeric_id(value):
    require(type(value) in (str, int), 'invalid_id')
    value = str(value)
    require(bool(re.fullmatch(r'[0-9]{1,20}', value)) and int(value) > 0, 'invalid_id')
    return value


@dataclass(frozen=True)
class Spec:
    account: str
    peer: str
    kind: str
    day: str
    match_text: str
    count: int = 20
    max_pages: int = 3
    deadline_seconds: int = 30
    start_native_id: str = ''

    def __post_init__(self):
        numeric_id(self.account)
        numeric_id(self.peer)
        require(self.kind in ('group', 'private'), 'invalid_peer_kind')
        try:
            require(date.fromisoformat(self.day).isoformat() == self.day, 'invalid_day')
        except (ValueError, TypeError):
            raise ProbeError('invalid_day') from None
        require(isinstance(self.match_text, str) and 0 < len(self.match_text.strip()) <= 1024,
                'invalid_match_text')
        for value, low, high in ((self.count, 2, 100), (self.max_pages, 1, 20),
                                 (self.deadline_seconds, 1, 60)):
            require(type(value) is int and low <= value <= high, 'invalid_budget')
        if self.start_native_id:
            require(int(numeric_id(self.start_native_id)) > 2147483647, 'not_a_native_id')

    @property
    def bounds(self):
        start = datetime.combine(date.fromisoformat(self.day), datetime.min.time(), TZ)
        return int(start.timestamp()), int((start + timedelta(days=1)).timestamp())


class LocalHTTP:
    """No proxies, redirects, query-string credentials, or arbitrary actions."""
    def __init__(self, endpoint, token, timeout=5):
        require(isinstance(endpoint, str) and len(endpoint) <= 4096, 'invalid_endpoint')
        try:
            url = urlsplit(endpoint)
            require(url.scheme == 'http' and ipaddress.ip_address(url.hostname).is_loopback,
                    'loopback_http_required')
            require(not url.username and not url.password and not url.query and not url.fragment,
                    'invalid_endpoint')
            require(bool(re.fullmatch(r'[/a-zA-Z0-9_-]*', url.path)), 'invalid_endpoint')
            self.host, self.port, self.prefix = url.hostname, url.port or 80, url.path.rstrip('/')
        except (ValueError, TypeError):
            raise ProbeError('invalid_endpoint') from None
        require(isinstance(token, str) and len(token) <= 4096 and
                all(32 <= ord(c) < 127 for c in token), 'invalid_token')
        require(type(timeout) is int and 1 <= timeout <= 15, 'invalid_timeout')
        self.token, self.timeout = token, timeout
        self.calls, self.response_bytes = 0, 0

    def call(self, action, params, remaining=None):
        require(action in READ_ACTIONS, 'action_not_readonly')
        self.calls += 1
        timeout = min(self.timeout, remaining) if remaining is not None else self.timeout
        require(timeout > 0, 'time_budget')
        connection = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        expires = time.monotonic() + timeout
        try:
            body = json.dumps(params).encode('utf-8')
            headers = {'Content-Type': 'application/json'}
            if self.token:
                headers['Authorization'] = 'Bearer ' + self.token
            connection.request('POST', self.prefix + '/' + action, body, headers)
            response = connection.getresponse()
            require(response.status not in (401, 403), 'http_auth_denied')
            require(not 300 <= response.status < 400, 'redirect_refused')
            require(response.status == 200, 'http_error')
            # read1 plus a wall-clock deadline also bounds a slow streaming body.
            chunks, total = [], 0
            while True:
                left = expires - time.monotonic()
                require(left > 0, 'time_budget')
                if connection.sock:
                    connection.sock.settimeout(left)
                chunk = response.read1(min(65536, MAX_RESPONSE + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                require(total <= MAX_RESPONSE, 'response_too_large')
                chunks.append(chunk)
            self.response_bytes += total
            result = json.loads(b''.join(chunks))
            require(isinstance(result, dict), 'invalid_envelope')
            require(result.get('status') == 'ok' and result.get('retcode') == 0, 'onebot_failed')
            return result.get('data')
        except ProbeError:
            raise
        except (OSError, ValueError, http.client.HTTPException):
            raise ProbeError('transport_or_json_error') from None
        finally:
            connection.close()


def observe(row, spec):
    require(isinstance(row, dict), 'invalid_message')
    require(str(row.get('self_id')) == spec.account, 'account_changed')
    require(row.get('message_type') == spec.kind, 'peer_kind_changed')
    sender = numeric_id(row.get('user_id'))
    if spec.kind == 'group':
        require(str(row.get('group_id')) == spec.peer, 'peer_changed')
    else:
        require(sender in (spec.account, spec.peer) and not row.get('group_id'), 'peer_changed')
    mid = numeric_id(row.get('message_id'))
    stamp = row.get('time')
    require(type(stamp) is int and 946684800 <= stamp < 4102444800, 'invalid_timestamp')
    seq = row.get('real_seq')
    seq = int(seq) if re.fullmatch(r'[0-9]{1,20}', str(seq)) else None
    segments = row.get('message')
    require(isinstance(segments, list) and len(segments) <= 1000, 'array_messages_required')
    texts, kinds, references = [], Counter(), []
    for segment in segments:
        require(isinstance(segment, dict) and isinstance(segment.get('data'), dict), 'invalid_segment')
        kind, data = segment.get('type'), segment['data']
        require(isinstance(kind, str), 'invalid_segment')
        kinds[kind if kind in SEGMENT_KINDS else 'other'] += 1
        if kind == 'text':
            require(isinstance(data.get('text'), str), 'invalid_text')
            texts.append(data['text'])
        if kind == 'reply':
            references.append(numeric_id(data.get('id')))
    text = ''.join(texts)
    # URLs may expire or be absent with disable_get_url. Do not treat their
    # refresh as ID collision. This digest is NOT full media integrity proof.
    identity = [sender, stamp, seq, text, sorted(kinds.items()), references]
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).digest()
    start, end = spec.bounds
    return dict(mid=mid, stamp=stamp, seq=seq, digest=digest, kinds=kinds,
                references=references, match=start <= stamp < end and spec.match_text in text)


def probe(client, spec):
    """Walk backwards within one session. No persisted cursor or short-ID reuse."""
    began = time.monotonic()
    report = dict(format='tulpa-napcat-probe-v1', mode='bounded_readonly',
                  target_day=spec.day, peer_kind=spec.kind, pages=0, unique_messages=0,
                  overlaps=0, target_found=False, complete_history=False,
                  durable_resume_supported=False, stop_reason='page_budget',
                  ordering=[], sequence_jumps=0, short_pages=0, segment_counts={},
                  references_seen=0, references_in_window=0,
                  account_verified=False, provider_verified=False,
                  data_dir_binding='not_independently_verified',
                  private_peer_binding='response_has_no_recipient' if spec.kind == 'private' else None,
                  api_calls=0, history_calls=0, page_ms=[], first_page_ms=None)
    seen, orderings, segment_counts, references = {}, set(), Counter(), []

    def call(action, params):
        left = spec.deadline_seconds - (time.monotonic() - began)
        require(left > 0, 'time_budget')
        report['api_calls'] += 1
        if action.endswith('_msg_history'):
            report['history_calls'] += 1
        return client.call(action, params, remaining=left)

    try:
        login = call('get_login_info', {})
        require(isinstance(login, dict) and str(login.get('user_id')) == spec.account,
                'account_mismatch')
        report['account_verified'] = True
        version = call('get_version_info', {})
        require(isinstance(version, dict) and version.get('app_name') == 'NapCat.Onebot',
                'not_napcat')
        report['provider_verified'] = True
        value = str(version.get('app_version', ''))
        report['provider_version'] = value if re.fullmatch(r'v?\d+(?:\.\d+){1,3}', value) else 'unrecognized'
        report['protocol_v11'] = version.get('protocol_version') == 'v11'
        cursor, previous_oldest = spec.start_native_id or '0', None
        action = 'get_group_msg_history' if spec.kind == 'group' else 'get_friend_msg_history'
        for _ in range(spec.max_pages):
            params = {'group_id' if spec.kind == 'group' else 'user_id': spec.peer,
                      'message_seq': cursor, 'count': spec.count, 'reverse_order': True,
                      'disable_get_url': True, 'parse_mult_msg': False, 'quick_reply': True}
            tick = time.monotonic()
            page = call(action, params)
            report['page_ms'].append(round((time.monotonic() - tick) * 1000, 3))
            require(isinstance(page, dict) and isinstance(page.get('messages'), list), 'invalid_page')
            rows = page['messages']
            require(len(rows) <= spec.count, 'overfull_page')
            report['pages'] += 1
            if report['first_page_ms'] is None:
                report['first_page_ms'] = round((time.monotonic() - began) * 1000, 3)
            if not rows:
                report['stop_reason'] = 'empty_page_not_proof_of_exhaustion'
                break
            observations = [observe(row, spec) for row in rows]
            report['short_pages'] += len(rows) < spec.count
            stamps = [x['stamp'] for x in observations]
            orderings.add('ascending' if stamps == sorted(stamps) else
                          'descending' if stamps == sorted(stamps, reverse=True) else 'mixed')
            all_sequenced = all(x['seq'] is not None for x in observations)
            require(all_sequenced, 'sequence_missing_cannot_choose_cursor')
            sequences = {x['seq'] for x in observations}
            require(len(sequences) == len({x['mid'] for x in observations}), 'sequence_ambiguous')
            added = 0
            for item in observations:
                prior = seen.get(item['mid'])
                if prior:
                    require(prior['digest'] == item['digest'], 'id_conflict_or_edit')
                    report['overlaps'] += 1
                    continue
                seen[item['mid']] = item
                added += 1
                segment_counts.update(item['kinds'])
                references.extend(item['references'])
                report['target_found'] |= item['match']
            oldest = min(observations, key=lambda x: x['seq'])
            if report['target_found']:
                report['stop_reason'] = 'target_found_sample_only'
                break
            if not added or (previous_oldest is not None and oldest['seq'] >= previous_oldest):
                report['stop_reason'] = 'nonprogress_or_direction_mismatch'
                break
            # Reaching an earlier timestamp is only an observation. Continue up
            # to the page budget: imported/server history can be out of order.
            previous_oldest, cursor = oldest['seq'], oldest['mid']
    except ProbeError as exc:
        report['stop_reason'] = str(exc)
    report['unique_messages'] = len(seen)
    report['ordering'] = sorted(orderings)
    report['segment_counts'] = dict(segment_counts)
    report['references_seen'] = len(references)
    report['references_in_window'] = sum(ref in seen for ref in references)
    seqs = sorted({x['seq'] for x in seen.values() if x['seq'] is not None})
    report['sequence_jumps'] = sum(b > a + 1 for a, b in zip(seqs, seqs[1:]))
    report['sequence_jumps_prove_missing_messages'] = False
    if seen:
        stamps = [x['stamp'] for x in seen.values()]
        report['observed_days'] = [datetime.fromtimestamp(t, TZ).date().isoformat()
                                   for t in (min(stamps), max(stamps))]
    report['elapsed_ms'] = round((time.monotonic() - began) * 1000, 3)
    report['response_bytes'] = getattr(client, 'response_bytes', None)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Explicitly opt into authorized local reads')
    parser.add_argument('--spec', type=Path, help='Private JSON; never put token or sample in command arguments')
    args = parser.parse_args()
    if not args.live or not args.spec:
        parser.error('Live reading requires --live --spec; offline tests: scripts/check_napcat_qq_probe.py')
    try:
        require(args.spec.stat().st_size <= 16384, 'spec_too_large')
        raw = json.loads(args.spec.read_text('utf-8-sig'))
        require(isinstance(raw, dict), 'invalid_spec')
        require(raw.get('data_dir_confirmed') is True, 'confirm_qq_data_dir_first')
        client = LocalHTTP(raw['endpoint'], raw.get('token', ''), raw.get('timeout', 5))
        spec = Spec(account=numeric_id(raw['expected_account']), peer=numeric_id(raw['peer_id']),
                    kind=raw['kind'], day=raw['day'], match_text=raw['match_text'],
                    count=raw.get('count', 20), max_pages=raw.get('max_pages', 3),
                    deadline_seconds=raw.get('deadline_seconds', 30),
                    start_native_id=raw.get('start_native_id', ''))
        result = probe(client, spec)
        result['data_dir_binding'] = 'operator_confirmed_not_independently_verified'
    except ProbeError as exc:
        result = {'format': 'tulpa-napcat-probe-v1', 'stop_reason': str(exc), 'complete_history': False}
    except (OSError, ValueError, KeyError, TypeError):
        result = {'format': 'tulpa-napcat-probe-v1', 'stop_reason': 'invalid_local_spec', 'complete_history': False}
    folder = ROOT / 'reports/private'
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / ('napcat-qq-probe-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.json')
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get('target_found') else 2


if __name__ == '__main__':
    raise SystemExit(main())
