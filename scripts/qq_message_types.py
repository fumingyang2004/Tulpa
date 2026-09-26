"""Native QQ candidate types shared by historical, incremental and live readers."""

from datetime import datetime, timedelta, timezone

# Match normalize.stamp's existing 2000-2100 (UTC+8) import boundary. Native
# QQ timestamps are seconds. Never replace an invalid time with the current time.
_TZ = timezone(timedelta(hours=8))
MIN_MESSAGE_TIME = int(datetime(2000, 1, 1, tzinfo=_TZ).timestamp())
MAX_MESSAGE_TIME = int(datetime(2101, 1, 1, tzinfo=_TZ).timestamp())
VALID_TIME_SQL = f'("40050">={MIN_MESSAGE_TIME} AND "40050"<{MAX_MESSAGE_TIME})'

REPLY_SQL = '("40011"=9 AND "40012"=33)'
# Native class 2 includes mixed text + multiple pictures (3), and mixed
# text/pictures/face elements (19). Rejecting the envelope loses its text too.
# All readers reuse the element parser and its per-element sticker policy.
CONTENT_SUBTYPES = (1,2,3,17,19,4096)
SUPPORTED_SQL = f'(("40011"=2 AND "40012" IN ({",".join(map(str,CONTENT_SUBTYPES))})) OR {REPLY_SQL} OR "40011" IN (3,6,7))'


def is_reply_candidate(kind, subtype):
    return kind == 9 and subtype == 33


def supported(kind, subtype):
    return (kind == 2 and subtype in CONTENT_SUBTYPES) or is_reply_candidate(kind, subtype) or kind in (3,6,7)


def history_query(spec, *, since, until, peers, limit, count_invalid=False):
    """The same per-conversation text/media window on normal and isolated SQLite.

    Keep the statement a SELECT for the read-only child's SQL gate. Only native
    reader table/column constants become identifiers; user values stay bound.
    """
    clause=f' AND "{spec.conversation_column}" IN ({",".join("?" for _ in peers)})' if peers else ''
    lower=' AND "40050">=?' if since is not None else ''
    scope=f'"40050"<? {lower} {clause} AND {SUPPORTED_SQL}'
    params=tuple([until]+([since] if since is not None else [])+list(peers))
    if count_invalid:
        # Count only records otherwise selected by this exact source scope,
        # before the per-chat cap. No bodies/identities need to be exported.
        return f'SELECT count(*) FROM "{spec.table}" WHERE {scope} AND NOT {VALID_TIME_SQL}',params
    if limit is None:
        return f'''SELECT "40001","40050","40090","40033","40800","{spec.conversation_column}",
            {spec.table_rank},rowid,'{spec.conversation_type}',"40020","40012","40011"
            FROM "{spec.table}" WHERE {scope} AND {VALID_TIME_SQL} ORDER BY rowid''',params
    query=f'''SELECT "40001","40050","40090","40033","40800","{spec.conversation_column}",
        {spec.table_rank},source_rowid,'{spec.conversation_type}',"40020","40012","40011"
        FROM (SELECT *,rowid AS source_rowid,row_number() OVER (
            PARTITION BY "{spec.conversation_column}",("40012"=1) ORDER BY "40050" DESC,rowid DESC) AS n
          FROM "{spec.table}" WHERE {scope} AND {VALID_TIME_SQL}) AS ranked
        WHERE n<=?'''
    return query,params+(limit,)
