"""Bounded concurrency and adaptive pacing, separately for each NLSC host."""
import math
import time
import weakref
from collections import deque
from datetime import timezone
from email.utils import parsedate_to_datetime
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QObject, QTimer, QCoreApplication


def retry_after_seconds(value, now=None):
    text = str(value or '').strip()
    if text.isdigit():
        try:
            return min(int(text), 2147483647)
        except ValueError:
            return None
    try:
        date = parsedate_to_datetime(text)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return max(0, date.timestamp() - (time.time() if now is None else now))
    except (ValueError, TypeError, OverflowError):
        return None


class RequestQueue(QObject):
    BASE_INTERVAL = 0.25
    MAX_ACTIVE = 2

    def __init__(self, parent=None, clock=time.monotonic, capacity=MAX_ACTIVE):
        super().__init__(parent)
        self.clock = clock
        self.pending = deque()
        self.active = {}
        self.key_ready_at = {}
        self.next_start = 0
        self.last_start = 0
        self.blocked_until = 0
        self.interval = self.BASE_INTERVAL
        self.capacity = capacity
        self.max_active = capacity
        self.rate_limits = 0
        self.strikes = 0
        self.successes = 0
        self.pumping = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.pump)

    def submit(self, client, endpoint, fields):
        token = id(client)
        self.pending = deque(entry for entry in self.pending if entry[0] != token)
        self.pending.append((token, weakref.ref(client), endpoint, fields))
        self.notify_waiting(client)
        self.pump()

    def notify_waiting(self, client):
        delay = max(0, self.blocked_until - self.clock())
        text = ('此 NLSC 服務限流，至少等待 %d 秒後繼續' % math.ceil(delay)
                if delay else '等待 NLSC 查詢佇列')
        client.queue_state.emit(True, text)

    def pump(self):
        if self.pumping:
            return
        self.pumping = True
        try:
            self.timer.stop()
            # Stable ordering: map work overtakes queued attributes, while all
            # requests still share the host's spacing and Retry-After limit.
            self.pending = deque(sorted(self.pending, key=lambda entry:
                getattr(entry[1](), 'request_priority', 10)))
            key_wakeup = None
            # Scan once so shared section waiters do not block unrelated parcels.
            for _ in range(len(self.pending)):
                if not self.pending:
                    break
                entry = self.pending.popleft()
                token, reference, endpoint, fields = entry
                client = reference()
                if client is None or sip.isdeleted(client) or client.context is None:
                    continue
                if not client.can_dispatch():
                    self.pending.append(entry)
                    continue
                cached = client.cached_response(endpoint, fields)
                if cached is not None:
                    client.queue_state.emit(False, '')
                    if client.context is not None:
                        try:
                            client._success(cached)
                        except Exception as exc:
                            client._fail(200, '', str(exc))
                    continue
                key = client.shared_request_key(endpoint, fields)
                if key is not None and key in self.active.values():
                    self.pending.append(entry)
                    continue
                ready_at = self.key_ready_at.get(key, 0)
                if ready_at > self.clock():
                    self.pending.append(entry)
                    key_wakeup = ready_at if key_wakeup is None else min(key_wakeup, ready_at)
                    continue
                if len(self.active) >= self.max_active:
                    self.pending.appendleft(entry)
                    break
                delay = max(self.next_start, self.blocked_until) - self.clock()
                if delay > 0:
                    self.pending.appendleft(entry)
                    self.timer.start(min(2147483647, max(1, math.ceil(delay*1000))))
                    break
                self.active[token] = key
                self.last_start = self.clock()
                self.next_start = self.last_start + self.interval
                client.queue_state.emit(False, '')
                if client.context is None or token not in self.active:
                    self.discard(token)
                    continue
                spacing = client.request_interval(endpoint, fields)
                if key is not None and spacing > 0:
                    self.key_ready_at[key] = self.last_start + spacing
                client._send_request(endpoint, fields)
            if key_wakeup is not None and self.pending:
                wait_ms = min(2147483647, max(1, math.ceil((key_wakeup-self.clock())*1000)))
                if not self.timer.isActive() or wait_ms < self.timer.remainingTime():
                    self.timer.start(wait_ms)
        finally:
            self.pumping = False

    def completed(self, client, success=False):
        was_active = id(client) in self.active
        self.active.pop(id(client), None)
        if was_active and success and self.clock() >= self.blocked_until:
            self.successes += 1
            if self.successes >= 8:
                self.successes = 0
                self.strikes = max(0, self.strikes-1)
                self.interval = max(self.BASE_INTERVAL, self.interval/2)
                self.next_start = min(self.next_start, self.last_start+self.interval)
                if self.interval == self.BASE_INTERVAL:
                    self.max_active = self.capacity
        # Do not dispatch another request inside a reply's completion callback.
        if self.pending:
            self.timer.start(1)

    def discard(self, token):
        # Qt can destroy the application-owned queue before its clients.
        if sip.isdeleted(self) or sip.isdeleted(self.timer):
            return
        self.pending = deque(entry for entry in self.pending if entry[0] != token)
        self.active.pop(token, None)
        if self.pending and len(self.active) < self.max_active:
            self.timer.start(1)
        elif not self.pending:
            self.timer.stop()

    def rate_limited(self, retry_after=''):
        self.rate_limits += 1
        now = self.clock()
        # Concurrent replies from the same burst should cause one slowdown.
        if now >= self.blocked_until:
            self.strikes += 1
            self.interval = min(2.0, max(self.BASE_INTERVAL, self.interval)*2)
            self.max_active = 1
            fallback = min(30, 2 * 2**min(self.strikes-1, 4))
        else:
            fallback = self.blocked_until-now
        self.successes = 0
        specified = retry_after_seconds(retry_after)
        delay = specified if specified is not None else fallback
        self.blocked_until = max(self.blocked_until, now + delay)
        for _, reference, _, _ in list(self.pending):
            client = reference()
            if client is not None and not sip.isdeleted(client) and client.context is not None:
                self.notify_waiting(client)
        return delay


_queues = {}


def request_queue(host='landmaps.nlsc.gov.tw'):
    # Keep all paths on a host under the same limit. Different services may run
    # together; never split a throttled host into several independent queues.
    queue = _queues.get(host)
    if queue is None or sip.isdeleted(queue):
        queue = _queues[host] = RequestQueue(QCoreApplication.instance(),
            capacity=1 if host == 'landmaps.nlsc.gov.tw' else RequestQueue.MAX_ACTIVE)
    return queue
