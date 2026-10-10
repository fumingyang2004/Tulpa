"""Bounded literal rediscovery of known terms in peer-only batch material.

Independently implemented from the recent-term matching mechanism described in
doc/MCP_LANGUAGE_LEARNING.md; no upstream code or prompts are copied.
"""
import re
from functools import lru_cache


@lru_cache(maxsize=256)
def _pattern(term):
    folded = term.casefold()
    literal = re.escape(folded)
    # Chinese compounds are commonly embedded in sentences without spaces.
    # For other terms, avoid counting e.g. "cat" inside "concatenate".
    if any('\u3400' <= c <= '\u9fff' or '\U00020000' <= c <= '\U0003134f' for c in folded):
        return re.compile(literal)
    return re.compile(r'(?<!\w)' + literal + r'(?!\w)')


def find_known_terms(terms, messages):
    """At most one witnessed source per term; callers bound terms and material."""
    found = []
    texts = [(m['source_id'], m['text'].casefold()) for m in messages]
    for term in terms:
        pattern = _pattern(term)
        source = next((source for source, body in texts if pattern.search(body)), None)
        if source is not None:
            found.append(dict(term=term, source_id=source))
    return found
