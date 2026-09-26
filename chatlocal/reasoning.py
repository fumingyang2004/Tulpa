"""Provider-exposed reasoning, kept separate from answers and tool logs."""
import html
import threading
import time


class ReasoningCapture:
    def __init__(self, events, *, enabled=True):
        self.events = events
        self.enabled = enabled
        self.lock = threading.Lock()
        self.parts = {}
        self.pending = {}
        self.last_emit = {}

    def append(self, request, text):
        if not self.enabled or not isinstance(text, str) or not text:
            return
        with self.lock:
            self.parts.setdefault(request, []).append(text)
            self.pending.setdefault(request, []).append(text)
            if time.monotonic() - self.last_emit.get(request, 0) >= .25:
                self._flush(request)

    def _flush(self, request):
        parts = self.pending.pop(request, [])
        if parts:
            self.events.put(dict(type='model_reasoning', request=request, delta=''.join(parts)))
            self.last_emit[request] = time.monotonic()

    def flush(self, request):
        with self.lock:
            self._flush(request)

    def snapshot(self):
        with self.lock:
            return [dict(request=n, text=''.join(parts)) for n, parts in sorted(self.parts.items())]


def reasoning_html(items):
    """Plain escaped text: never interpret provider reasoning as Markdown/HTML."""
    sections = []
    for item in items or []:
        if not isinstance(item, dict) or not isinstance(item.get('text'), str) or not item['text']:
            continue
        sections.append('<section class="reasoning-request"><div class="reasoning-request-label">请求 '
                        + html.escape(str(item.get('request', ''))) + '</div><pre>'
                        + html.escape(item['text']) + '</pre></section>')
    if not sections:
        return ''
    return ('<details class="model-reasoning" open><summary>思考过程'
            '<span>模型返回内容</span></summary><div class="reasoning-body">'
            + ''.join(sections) + '</div></details>')
