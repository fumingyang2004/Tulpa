"""One-use desktop launch tickets; the permanent admin token never enters a URL."""
import hashlib
import secrets
import threading
import time


class DesktopSessions:
    def __init__(self, root):
        self.cookie = 'tulpa_desktop_' + hashlib.sha256(str(root).encode()).hexdigest()[:16]
        self.tickets = {}
        self.sessions = {}
        self.lock = threading.Lock()

    def _prune(self):
        now = time.monotonic()
        for items in (self.tickets, self.sessions):
            for key, expiry in list(items.items()):
                if expiry <= now:
                    del items[key]

    def issue(self):
        with self.lock:
            self._prune()
            if len(self.tickets) >= 32:
                raise ValueError('桌面启动请求过多，请一分钟后重试。')
            ticket = secrets.token_urlsafe(32)
            self.tickets[ticket] = time.monotonic() + 60
            return ticket

    def exchange(self, ticket):
        with self.lock:
            self._prune()
            if not isinstance(ticket, str) or self.tickets.pop(ticket, None) is None:
                return None
            if len(self.sessions) >= 32:
                del self.sessions[next(iter(self.sessions))]
            session = secrets.token_urlsafe(32)
            self.sessions[session] = time.monotonic() + 86400
            return session

    def valid(self, session):
        with self.lock:
            self._prune()
            return bool(session and session in self.sessions)
