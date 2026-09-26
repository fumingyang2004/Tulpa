"""Read-only, on-demand interaction examples from the existing message archive.

No growth counters, profiles, episode writes or provider calls. The FTS index and
timeline belong to the existing Store; native quote lookup has one small index.
"""
import json
import re

from .retrieval import scope_sql
from .store import tokens
from .tulpa import effective, fingerprint, signals

QUOTE_SOURCE = "CAST(json_extract(CASE WHEN json_valid(reply_to) THEN reply_to ELSE '{}' END,'$.source_id') AS TEXT)"
QUOTE_CONTENT = "json_extract(CASE WHEN json_valid(reply_to) THEN reply_to ELSE '{}' END,'$.content')"
TEXT_QUOTE = f"is_self=1 AND COALESCE({QUOTE_SOURCE},'')='' AND {QUOTE_CONTENT} IS NOT NULL"


def initialize(db):
    db.execute(f'''CREATE INDEX IF NOT EXISTS msg_reply_source ON messages
      (platform,conversation_id,{QUOTE_SOURCE}) WHERE is_self=1''')
    db.execute(f'''CREATE INDEX IF NOT EXISTS msg_reply_text ON messages
      (platform,conversation_id,{QUOTE_CONTENT},timestamp) WHERE {TEXT_QUOTE}''')


def words(text):
    return list(dict.fromkeys(w for w in tokens(text).split()
                             if len(w) >= 2 and re.search(r'\w', w)))[:24]


def rank(episode, target, instruction):
    incoming = episode.get('_incoming_text', '')
    outgoing = episode.get('_outgoing_text', '')
    query = target['content'] + ' ' + instruction
    scenes, intents = signals(query)
    old_scenes, _ = signals(incoming)
    _, old_intents = signals(outgoing)
    overlap = len(set(words(query)) & set(words(incoming)))
    similar = bool(set(scenes) & set(old_scenes)) or bool(overlap)
    same = episode.get('person_id') == target.get('sender_id')
    tier = 3 if same and similar else 2 if same else 1 if similar else 0
    return (tier, len(set(intents) & set(old_intents)), overlap, episode.get('timestamp', 0))


def candidates(tools, target, instruction=''):
    """Discover bounded seeds, then reconstruct complete, scoped input/output turns."""
    if target['conversation_type'] not in ('group', 'direct'):
        return []
    where, params = scope_sql(tools.plan)
    where += ' AND m.platform=? AND m.conversation_id=? AND m.timestamp<?'
    params += [target['platform'], target['conversation_id'], target['timestamp']]
    found = {}
    with tools.store.connect() as db:
        def query(extra='', args=(), limit=40, order='m.timestamp DESC,m.id DESC'):
            return [dict(r) for r in db.execute(f'''SELECT m.* FROM messages m
              WHERE {where} {extra} ORDER BY {order} LIMIT ?''', [*params, *args, limit])]

        seeds = {}
        terms = words(target['content'] + ' ' + instruction)
        if terms:
            match = ' OR '.join('"' + w.replace('"', '""') + '"' for w in terms)
            # Ranked FTS over existing normalized terms, restricted to this conversation.
            for r in db.execute(f'''SELECT m.* FROM message_fts JOIN messages m ON m.id=message_fts.rowid
              WHERE message_fts MATCH ? AND {where} ORDER BY bm25(message_fts) LIMIT 40''', [match, *params]):
                seeds[r['id']] = dict(r)
        for r in query('AND m.is_self=0 AND m.sender_id=?', [target.get('sender_id', '')]):
            seeds.setdefault(r['id'], r)
        for r in query('AND m.is_self=1'):
            seeds.setdefault(r['id'], r)

        def add(incoming, outgoing, context=(), linkage='private_turn'):
            rows = [*incoming, *outgoing, *context]
            if not incoming or not outgoing or any(not effective(r) for r in incoming + outgoing):
                return
            person = incoming[-1]['sender_id']
            if not person or any(r['is_self'] != 0 or r['sender_id'] != person for r in incoming):
                return
            if any(r['is_self'] != 1 for r in outgoing):
                return
            if target['conversation_type'] == 'direct' and target['is_self'] == 0 and person != target['sender_id']:
                return
            key = outgoing[0]['id']
            found[key] = dict(incoming=[r['id'] for r in incoming], outgoing=[r['id'] for r in outgoing],
                message_ids=[r['id'] for r in rows], person_id=person, timestamp=outgoing[-1]['timestamp'],
                origin='archive', linkage=linkage, fingerprints={r['id']: fingerprint(r) for r in rows},
                _incoming_text=' '.join(r['content'] for r in incoming),
                _outgoing_text=' '.join(r['content'] for r in outgoing))

        def quote(outgoing):
            try:
                ref = json.loads(outgoing.get('reply_to') or '{}')
                source = str(ref.get('source_id') or '') if isinstance(ref, dict) else ''
            except (ValueError, TypeError):
                return
            if not isinstance(ref, dict):
                return
            if source:
                # A missing/ambiguous native ID must not silently retarget by text.
                matches = db.execute('''SELECT id FROM messages WHERE platform=? AND conversation_id=?
                  AND source_id=? AND timestamp<=? AND id!=? LIMIT 2''',
                  (target['platform'], target['conversation_id'], source, outgoing['timestamp'], outgoing['id'])).fetchall()
                linkage = 'native_quote'
            else:
                text = ref.get('content')
                # Legacy QQ archives often contain a genuine quote preview without
                # its native ID. Match the *entire* preview, never a fuzzy prefix,
                # adjacent turn or mention. The normalizer truncates at 2000 chars.
                if (not isinstance(text, str) or not 2 <= len(text) < 2000
                        or re.fullmatch(r'\s*\[.*\]\s*', text, re.S)):
                    return
                terms = words(text)
                if not terms:
                    return
                match = ' AND '.join('"' + w.replace('"', '""') + '"' for w in terms)
                # Check ambiguity across all earlier source rows, BEFORE applying
                # scope or sender filters. FTS only accelerates exact equality.
                matches = db.execute('''SELECT m.id FROM message_fts JOIN messages m ON m.id=message_fts.rowid
                  WHERE message_fts MATCH ? AND m.platform=? AND m.conversation_id=?
                  AND m.content=? AND m.timestamp<=? AND m.id!=? LIMIT 2''',
                  (match, target['platform'], target['conversation_id'], text, outgoing['timestamp'], outgoing['id'])).fetchall()
                linkage = 'quoted_text_exact'
            if len(matches) != 1:
                return
            incoming = query('AND m.id=?', [matches[0]['id']], limit=1)
            if incoming and incoming[0]['is_self'] == 0:
                # A stored author hint can veto a text match, not disambiguate
                # repeated messages by nickname. Stable identity comes from source.
                author = str(ref.get('sender') or '').strip()
                if not source and author and author not in (incoming[0]['sender_id'], incoming[0]['sender']):
                    return
                add(incoming, [outgoing], linkage=linkage)

        for seed in seeds.values():
            if not effective(seed):
                continue
            if target['conversation_type'] == 'group':
                if seed['is_self'] == 1:
                    quote(seed)
                else:
                    if seed['source_id']:
                        for outgoing in query(f'AND m.is_self=1 AND {QUOTE_SOURCE}=?', [str(seed['source_id'])], limit=8):
                            quote(outgoing)
                    # Also find older text-only quoted replies outside the recent
                    # self-message seeds, using the small partial quote index.
                    for outgoing in query(f'AND {TEXT_QUOTE} AND {QUOTE_CONTENT}=? AND m.timestamp>=?',
                                          [seed['content'], seed['timestamp']], limit=8):
                        quote(outgoing)
                continue

            # A private exchange: one incoming block followed by one outgoing block.
            # Bound work to 25 messages on each side; reject truncated blocks, media /
            # unknown-sender boundaries and exchanges spanning more than 30 minutes.
            before = query('AND (m.timestamp<? OR (m.timestamp=? AND m.id<=?))',
                           [seed['timestamp'], seed['timestamp'], seed['id']], limit=25)
            after = query('AND (m.timestamp>? OR (m.timestamp=? AND m.id>?))',
                          [seed['timestamp'], seed['timestamp'], seed['id']], limit=25,
                          order='m.timestamp,m.id')
            rows = list(reversed(before)) + after
            at = len(before) - 1
            start = at
            if seed['is_self'] == 1:
                while start >= 0 and rows[start]['is_self'] == 1 and effective(rows[start]):
                    start -= 1
            else:
                while at + 1 < len(rows) and rows[at + 1]['is_self'] == 0 and effective(rows[at + 1]):
                    at += 1
                start = at
            if start < 0 or rows[start]['is_self'] != 0 or not effective(rows[start]):
                continue
            split = start + 1
            while start > 0 and rows[start - 1]['is_self'] == 0 and effective(rows[start - 1]):
                start -= 1
            end = split
            while end < len(rows) and rows[end]['is_self'] == 1 and effective(rows[end]):
                end += 1
            if end == split or (start == 0 and len(before) == 25) or (end == len(rows) and len(after) == 25):
                continue
            if rows[end - 1]['timestamp'] - rows[start]['timestamp'] > 1800000:
                continue
            add(rows[start:split], rows[split:end])
    return sorted(found.values(), key=lambda e: rank(e, target, instruction), reverse=True)[:64]
