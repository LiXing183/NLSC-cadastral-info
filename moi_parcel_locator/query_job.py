"""One independent request group for each submitted point or parcel."""
from qgis.PyQt.QtCore import QObject, QTimer, pyqtSignal
from .nlsc_point import NlscPointLookup
from .nlsc_fill import NlscFillLookup
from .nlsc_lookup import NlscLookup, NlscSectionLookup
from .nlsc_building import NlscBuildingLookup
from .nlsc_number import NlscNumberLookup
from .building_list import nlsc_buildings
from .parcel_view import union_extents


class QueryJob(QObject):
    changed = pyqtSignal(object)
    positioned = pyqtSignal(object, float, float)

    def __init__(self, number, parent):
        super().__init__(parent)
        self.number = number
        self.owner = parent
        self.identity = None
        self.center = None
        self.needs_location = False
        self.extent = None
        self.fill_requested = False
        self.images, self.rows, self.buildings = [], [], []
        self.groups, self.errors = {}, {}
        self.notice = ''
        self.pending = set()
        self.cancelled = False
        self.interrupted = set()
        self.build_clients, self.build_results = {}, {}
        self.stage_timers = {}
        self.stage_remaining = {}
        self.stage_progress = {}
        self.point = NlscPointLookup(self)
        self.fill = NlscFillLookup(self)
        self.land = NlscLookup(self)
        self.section = NlscSectionLookup(self)
        self.locator = NlscNumberLookup(self)
        self.point.identified.connect(lambda data: self.identified(data) if 'point' in self.pending and not self.cancelled else None)
        self.fill.images_ready.connect(self.image_ready)
        self.land.ready.connect(lambda groups: self.group_ready('land', groups))
        self.section.ready.connect(lambda groups: self.group_ready('section', groups))
        self.locator.located.connect(self.located)
        for key, client in self.clients().items():
            if key in ('point', 'fill', 'locate'):
                client.request_priority = 1 if key == 'point' else 0
            else:
                client.dispatch_allowed = self.owner.details_allowed
            client.failed.connect(lambda message, k=key: self.failed(k, message))
            client.progress.connect(lambda message, k=key: self.progress(k, message))
            client.queue_state.connect(lambda waiting, message, k=key: self.stage_waiting(k, waiting, message))

    def clients(self):
        return dict(point=self.point, fill=self.fill, land=self.land,
                    section=self.section, locate=self.locator, **self.build_clients)

    def start_stage(self, key, start):
        if self.cancelled or key not in self.pending:
            return
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(30000)
        timer.timeout.connect(lambda: self.stage_timeout(key))
        self.stage_timers[key] = timer
        self.stage_remaining[key] = 30000
        timer.start()
        self.changed.emit(self)
        try:
            start()
        except Exception as exc:
            self.failed(key, str(exc))

    def stage_waiting(self, key, waiting, message):
        if self.cancelled or key not in self.pending or key not in self.stage_timers:
            return
        timer = self.stage_timers[key]
        if waiting:
            if timer.isActive():
                self.stage_remaining[key] = max(1, timer.remainingTime())
                timer.stop()
            self.stage_progress[key] = '排隊：%s；%s' % (self.stage_name(key), message)
        else:
            self.stage_progress.pop(key, None)
            timer.start(self.stage_remaining[key])
        self.changed.emit(self)

    def stage_timeout(self, key):
        if self.cancelled or key not in self.pending:
            return
        self.clients()[key].cancel()
        self.failed(key, self.stage_name(key) + ' 超過 30 秒，已停止；已完成資料保留。')

    @staticmethod
    def stage_name(key):
        return {'point':'座標查地號', 'fill':'NLSC 填色', 'land':'NLSC 土地資料',
                'section':'NLSC 測繪資訊', 'detail':'NLSC 補充資料', 'locate':'地號定位'}.get(key, '建物 ' + key.removeprefix('build:'))

    def status_text(self):
        if self.cancelled:
            return '已取消'
        if not self.pending:
            return '部分失敗' if self.errors else '完成'
        keys = sorted(self.pending)
        if 'locate' in keys and 'fill' in keys:
            keys.remove('fill')  # Fill awaits preparation, not another HTTP request.
        return '、'.join(self.stage_progress.get(key) or
                        ('查詢：' if key in self.stage_timers else '排隊：') + self.stage_name(key)
                        for key in keys)

    def progress(self, key, message):
        if not self.cancelled and key in self.pending:
            self.stage_progress[key] = message
            self.changed.emit(self)

    def start_point(self, x, y):
        self.pending = {'point'}
        self.owner.queue_point(self, x, y)

    def start_number(self, data):
        self.needs_location = True
        self.pending.add('locate')
        if self.identified(data) is False:
            return

    def identified(self, data):
        if self.cancelled:
            return
        if self.owner.reuse_parcel(data, self):
            return False
        self.identity = dict(data, _query_id=self.number)
        self.pending.discard('point')
        self.stop_stage_timer('point')
        self.pending.update(('fill', 'land', 'section'))
        self.changed.emit(self)
        self.owner.parcel_queue.submit(self)

    def start_attributes(self):
        if self.cancelled or self.identity is None:
            return
        for key in ('land', 'section'):
            if key in self.pending and key not in self.stage_timers:
                self.start_stage(key, lambda k=key: self.clients()[k].start_parcel(self.identity))

    def start_map_work(self):
        if self.cancelled:
            return
        if 'locate' in self.pending:
            self.start_stage('locate', lambda: self.locator.start_parcel(self.identity))
        elif 'fill' in self.pending:
            self.start_stage('fill', lambda: self.fill.start_parcel(self.identity))

    def finish(self, key):
        if self.cancelled:
            return
        self.pending.discard(key)
        self.stop_stage_timer(key)
        self.changed.emit(self)
        if key in ('locate', 'fill') and not self.pending.intersection(('locate', 'fill')):
            self.owner.parcel_queue.discard(self)

    def stop_stage_timer(self, key):
        self.stage_remaining.pop(key, None)
        self.stage_progress.pop(key, None)
        timer = self.stage_timers.pop(key, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()

    def resume(self):
        """Reuse a cancelled record, restarting only its unfinished stages."""
        if not self.cancelled or self.identity is None:
            return
        self.cancelled = False
        keys, self.interrupted = self.interrupted, set()
        # Building details remain on demand after reopening.
        for key in keys:
            if key.startswith('build:'):
                client = self.build_clients.pop(key, None)
                if client is not None:
                    client.deleteLater()
        self.pending.update(key for key in keys if not key.startswith('build:'))
        self.changed.emit(self)
        if self.pending.intersection(('locate', 'fill')):
            self.owner.parcel_queue.submit(self)

    def image_ready(self, images, label):
        if self.cancelled or 'fill' not in self.pending:
            return
        self.images = images
        if self.extent is None:
            self.extent = union_extents(extent for extent, png in images)
        self.finish('fill')

    def retry_failed_fill(self):
        """An explicit requery may repair fill without repeating valid attributes."""
        return bool(self.retry_failed_queries(('fill',)))

    def retry_failed_queries(self, keys=('fill', 'land', 'section', 'locate')):
        if self.cancelled or self.identity is None:
            return 0
        keys = [key for key in keys if key in self.errors and key not in self.pending]
        if 'fill' in keys and self.needs_location and self.center is None and 'locate' not in keys and 'locate' not in self.pending:
            keys.append('locate')
        for key in keys:
            self.errors.pop(key, None)
        self.pending.update(keys)
        if any(key in ('locate', 'fill') for key in keys):
            self.owner.parcel_queue.submit(self)
        self.changed.emit(self)
        return len(keys)

    def group_ready(self, key, groups):
        if self.cancelled or key not in self.pending:
            return
        self.groups.update(groups)
        if key == 'land':
            self.buildings = nlsc_buildings(self.groups.get('建號清單', []), self.identity)
        self.finish(key)

    @staticmethod
    def building_key(data):
        return 'build:' + str(data['sectNo']) + '/' + str(data['buildNo'])

    def request_building(self, data):
        key = self.building_key(data)
        if key in self.errors and key in self.build_clients:
            self.build_clients.pop(key).deleteLater()
            self.errors.pop(key, None)
        if key in self.build_clients or self.cancelled:
            return
        client = NlscBuildingLookup(self)
        client.dispatch_allowed = self.owner.details_allowed
        self.build_clients[key] = client
        self.pending.add(key)
        client.building_ready.connect(lambda rows, notice: self.build_ready(key, rows, notice))
        client.failed.connect(lambda message: self.failed(key, message))
        client.progress.connect(lambda message: self.progress(key, message))
        client.queue_state.connect(lambda waiting, message: self.stage_waiting(key, waiting, message))
        self.owner.detail_queue.append((self, key, lambda: client.start_building(data)))
        self.owner.pump_details()
        self.changed.emit(self)

    def build_ready(self, key, rows, notice):
        if self.cancelled or key not in self.pending:
            return
        self.build_results[key] = (rows, notice)
        self.finish(key)

    def located(self, x, y):
        if not self.cancelled and 'locate' in self.pending:
            self.center = (x, y)
            self.extent = self.locator.extent or self.extent
            self.positioned.emit(self, x, y)
            self.finish('locate')
            if 'fill' in self.pending and self.owner.parcel_queue.active is self:
                self.start_map_work()

    def failed(self, key, message):
        if self.cancelled or key not in self.pending:
            return
        self.errors[key] = message
        client = self.clients().get(key)
        if key in ('locate', 'fill') and client is not None and client.last_status == 429 and client.rate_retries >= 4:
            self.owner.parcel_queue.stop_waiting('NLSC 持續限流，已停止本批定位與填色；已完成資料保留，請稍後重新查詢未完成地號。')
        if key == 'locate' and 'fill' in self.pending:
            self.errors['fill'] = 'NLSC 填色未完成：此筆地號定位未成功，請重試此筆。'
            self.finish('fill')
        self.finish(key)

    def cancel(self):
        if self.cancelled:
            return
        self.interrupted = set(self.pending)
        self.cancelled = True
        self.owner.parcel_queue.discard(self)
        for timer in self.stage_timers.values():
            timer.stop()
            timer.deleteLater()
        self.stage_timers.clear()
        self.stage_remaining.clear()
        self.stage_progress.clear()
        for client in self.clients().values():
            client.cancel()
        self.pending.clear()
