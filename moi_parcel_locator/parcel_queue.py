"""Complete each parcel's preparation and PNG before starting the next parcel."""
from collections import deque
import weakref
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QObject, QTimer


class ParcelQueue(QObject):
    def __init__(self, parent):
        super().__init__(parent)
        self.pending = deque()
        self.active = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.pump)

    def submit(self, job):
        if self.active is job or any(ref() is job for ref in self.pending):
            return
        self.pending.append(weakref.ref(job))
        self.pump()

    def pump(self):
        if self.active is not None:
            return
        self.timer.stop()
        while self.pending:
            job = self.pending.popleft()()
            if job is None or sip.isdeleted(job) or job.cancelled or not job.pending.intersection(('locate', 'fill')):
                continue
            self.active = job
            job.start_map_work()
            return

    def discard(self, job):
        self.pending = deque(ref for ref in self.pending if ref() is not None and ref() is not job)
        if self.active is job:
            self.active = None
        if self.active is None and self.pending:
            self.timer.start(0)

    def clear(self):
        self.timer.stop()
        self.pending.clear()
        self.active = None

    def stop_waiting(self, reason):
        """A persistent 429 must not cause every queued parcel to repeat retries."""
        waiting, self.pending = self.pending, deque()
        for ref in waiting:
            job = ref()
            if job is not None and not sip.isdeleted(job) and not job.cancelled:
                for key in ('locate', 'fill'):
                    if key in job.pending:
                        job.failed(key, reason)
