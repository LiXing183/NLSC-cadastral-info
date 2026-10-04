from qgis.PyQt.QtCore import QObject, Qt, QTimer, QMimeData
from .clipboard_table import table_formats, grid_formats
from qgis.PyQt.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QWidget, QTabWidget, QComboBox, QCheckBox, QSpinBox, QLabel, QTableWidget, QPlainTextEdit, QPushButton, QApplication, QAbstractItemView
from qgis.PyQt.QtGui import QColor
from qgis.core import QgsCoordinateTransform, QgsCoordinateReferenceSystem, QgsProject, QgsTask, QgsApplication
from qgis.gui import QgsMapToolEmitPoint
from .canvas_overlay import ParcelOverlay
from qgis.PyQt.QtWidgets import QSizePolicy
from .query_history import QueryHistory
from .cadastral_layer import CadastralLayer
from .color_selector import ColorSelector
from .nlsc_panel import NlscPanel
from .nlsc_point import NlscPointLookup
from .nlsc_fill import NlscFillLookup
from .query_job import QueryJob
from .nlsc_data import request_fields
from .parcel_view import fit_parcels
from .parcel_queue import ParcelQueue


class ParcelClickTool(QObject):
    MAX_POINT_LOOKUPS = 2

    def __init__(self, iface, action):
        super().__init__(iface.mainWindow())
        self.iface, self.action = iface, action
        self.canvas = iface.mapCanvas()
        self.previous = None
        self.locator_panel = None
        self.tool = QgsMapToolEmitPoint(self.canvas)
        self.point_lookup = NlscPointLookup(self)
        self.point_lookup.identified.connect(self.fill_inputs)
        self.point_lookup.failed.connect(self.failed)
        self.fill_lookup = NlscFillLookup(self)
        self.fill_lookup.images_ready.connect(self.fill_parcel)
        self.fill_lookup.failed.connect(self.fill_error)
        self.from_map = False
        self.overlay = ParcelOverlay(self.canvas)
        self.fill_color = QColor('#00ffff')
        self.fill_warning = ''
        self.pending_identity = None
        self.pending_images = []
        self.jobs = {}
        self.vector_task = None
        QgsProject.instance().cleared.connect(self.cancel_vector_task)
        self.unloaded = False
        self.parcel_queue = ParcelQueue(self)
        self.active_job = None
        self.fit_jobs = []
        self.fit_waiting = False
        self.submitting_batch = False
        self.next_job = 0
        self.history_versions = {}
        self.rendered_buildings = None
        self.detail_queue = []
        self.detail_active = None
        self.detail_timer = QTimer(self)
        self.detail_timer.setSingleShot(True)
        self.detail_timer.setInterval(0)
        self.detail_timer.timeout.connect(self.pump_details)
        self.point_queue = []
        self.point_active = set()
        self.point_queue_timer = QTimer(self)
        self.point_queue_timer.setSingleShot(True)
        self.point_queue_timer.setInterval(0)
        self.point_queue_timer.timeout.connect(self.pump_points)
        self.dialog = QDialog(iface.mainWindow())
        self.dialog.setWindowTitle('NLSC地籍資訊查詢：點選圖面／輸入地號')
        self.dialog.resize(480, 720)
        layout = QVBoxLayout(self.dialog)
        self.main_layout = layout
        mode_row = QHBoxLayout()
        self.point_button = QPushButton('點選圖面')
        self.point_button.clicked.connect(self.activate_point)
        mode_row.addWidget(self.point_button)
        self.cadastral = CadastralLayer(iface, self.map_message)
        add_map = QPushButton('加入地籍圖')
        add_map.clicked.connect(self.cadastral.add)
        mode_row.addWidget(add_map)
        map_color = ColorSelector(QColor('#000000'), '地籍圖顏色')
        self.map_color_selector = map_color
        self.cadastral.color_changed = map_color.set_color
        current_map = self.cadastral.find()
        if current_map is not None:
            map_color.set_color(QColor(current_map.customProperty('moi/dmapsColor', '#000000')))
        map_color.color_selected.connect(self.cadastral.set_color)
        self.map_color_label = QLabel('地籍圖顏色')
        self.map_color_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        map_color_row = QHBoxLayout()
        map_color_row.setSpacing(4)
        map_color_row.addWidget(self.map_color_label)
        map_color_row.addWidget(map_color, 1)
        mode_row.addLayout(map_color_row, 1)
        layout.addLayout(mode_row)
        self.status = QLabel('請在地圖上點選宗地內部。')
        self.status.setWordWrap(True)
        # Keep status text as the message source; only loading renders it.
        self.status.hide()
        self.loading = QLabel('')
        self.loading.setWordWrap(True)
        self.loading.setTextFormat(Qt.TextFormat.PlainText)
        self.loading.setStyleSheet('border: none;')
        layout.addWidget(self.loading)
        self.loading_frame = 0
        self.loading_timer = QTimer(self)
        self.loading_timer.setInterval(350)
        self.loading_timer.timeout.connect(self.animate_loading)
        self.nlsc = NlscPanel()
        self.table = self.nlsc.table
        self.tabs = QTabWidget()
        self.tabs.addTab(self.nlsc, '土地')
        build_page = QWidget()
        build_layout = QVBoxLayout(build_page)
        selector = QHBoxLayout()
        self.buildings = QComboBox()
        self.buildings.currentIndexChanged.connect(self.build_selection_changed)
        self.buildings.activated.connect(self.query_building)
        selector.addWidget(self.buildings)
        build_layout.addLayout(selector)
        self.build_status = QLabel('本筆尚未查詢建物。')
        self.build_status.setWordWrap(True)
        build_layout.addWidget(self.build_status)
        self.build_table = QTableWidget(0, 2)
        self.build_table.setHorizontalHeaderLabels(['欄位', '內容'])
        self.build_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.build_table.horizontalHeader().setStretchLastSection(True)
        build_layout.addWidget(self.build_table, 1)
        self.build_notice = QPlainTextEdit()
        self.build_notice.setReadOnly(True)
        self.build_notice.setFixedHeight(65)
        self.build_notice.hide()
        build_layout.addWidget(self.build_notice)
        self.tabs.addTab(build_page, '建物')
        self.build_page = build_page
        self.tabs.addTab(self.nlsc.section_page, '測繪資訊')
        self.tabs.addTab(self.nlsc.public_page, '公有土地')
        self.history = QueryHistory(self.canvas, lambda: (self.fill_color, self.opacity_spin.value()/100))
        self.history.table.cellDoubleClicked.connect(self.select_history_job)
        self.history.on_remove = self.remove_jobs
        self.history.on_retry = self.retry_failed_jobs
        self.history.contents_changed.connect(self.update_copy_state)
        self.history.contents_changed.connect(self.refresh_message)
        self.tabs.addTab(self.history, '記錄')
        layout.addWidget(self.tabs, 1)
        fill_row = QHBoxLayout()
        self.fill_enabled = QCheckBox('自動填色')
        self.fill_enabled.setChecked(True)
        self.fill_enabled.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.color_button = ColorSelector(self.fill_color, '宗地填色顏色')
        self.color_button.color_selected.connect(self.set_fill_color)
        fill_color_row = QHBoxLayout()
        fill_color_row.setSpacing(4)
        fill_color_row.addWidget(self.fill_enabled)
        fill_color_row.addWidget(self.color_button, 1)
        fill_row.addLayout(fill_color_row, 1)
        opacity_row = QHBoxLayout()
        opacity_row.setSpacing(4)
        opacity_label = QLabel('不透明度')
        opacity_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        opacity_row.addWidget(opacity_label)
        self.opacity_spin = QSpinBox()
        self.opacity_spin.setRange(0, 100)
        self.opacity_spin.setSuffix(' %')
        self.opacity_spin.setValue(30)
        self.opacity_spin.valueChanged.connect(self.restyle)
        opacity_row.addWidget(self.opacity_spin)
        fill_row.addLayout(opacity_row)
        clear_button = QPushButton('清除填色')
        self.clear_fill_button = clear_button
        clear_button.clicked.connect(self.history.clear_fills)
        footer = QGridLayout()
        footer.setColumnStretch(0, 1)
        footer.addLayout(fill_row, 0, 0)
        footer.addWidget(clear_button, 0, 1)
        layout.addLayout(footer)
        self.notice = QPlainTextEdit()
        self.notice.setReadOnly(True)
        self.notice.textChanged.connect(self.refresh_message)
        self.notice.hide()
        self.notice.setFixedHeight(self.notice.fontMetrics().lineSpacing()*3
                                   + int(self.notice.document().documentMargin()*2)
                                   + self.notice.frameWidth()*2)
        layout.addWidget(self.notice)
        self.copy = QPushButton('複製屬性資料')
        self.copy.setEnabled(False)
        self.copy.clicked.connect(self.copy_result)
        self.nlsc.on_changed = self.update_tab_visibility
        self.tabs.currentChanged.connect(self.update_copy_state)
        self.tabs.currentChanged.connect(self.load_selected_building)
        self.update_tab_visibility()

        footer.addWidget(self.copy, 1, 0)
        self.vector_button = QPushButton('生成範圍')
        self.vector_button.setToolTip('將全部顯示中的填色融合成一個圖徵，修整細縫與小孔洞並擬合長直邊；套用預設向量樣式。')
        self.vector_button.clicked.connect(self.vectorize_fills)
        footer.addWidget(self.vector_button, 1, 1)
        source_note = QLabel('資料來源：NLSC國土測繪圖資服務雲。')
        source_note.setWordWrap(True)
        layout.addWidget(source_note)
        self.build_notice.textChanged.connect(self.refresh_message)
        self.tabs.currentChanged.connect(self.refresh_message)
        self.refresh_message()
        self.action.toggled.connect(self.toggle)
        self.tool.canvasClicked.connect(self.clicked)
        self.tool.deactivated.connect(self.deactivated)
        self.dialog.finished.connect(self.closed)
        self.point_lookup.progress.connect(self.set_status)
        self.fill_lookup.progress.connect(self.set_status)

    def set_status(self, text):
        self.status.setText(text)
        self.refresh_message()

    def map_message(self, message):
        self.set_status(message)
        if '失敗' in message or '未完成' in message:
            self.notice.setPlainText(message)
        else:
            self.notice.clear()

    def refresh_message(self, *args):
        direct_errors = []
        if self.notice.toPlainText().strip():
            direct_errors.append(self.notice.toPlainText().strip())
        if self.tabs.currentIndex() == 1 and self.build_notice.toPlainText().strip():
            direct_errors.append(self.build_notice.toPlainText().strip())
        pending = sum(bool(job.pending) and not job.cancelled for job in self.jobs.values())
        if pending or self.submitting_batch:
            # Queued stages are still queries. Report partial completion only
            # after all jobs have settled. Keep immediate input errors visible.
            self.loading.setStyleSheet(('color: #ff0000; ' if direct_errors else '') + 'border: none;')
            self.loading.setText(chr(10).join(dict.fromkeys(direct_errors)) if direct_errors else
                                 '背景查詢 %d 筆' % pending + '.' * max(1, self.loading_frame))
            return
        errors = []
        if self.active_job is not None:
            errors.extend(self.active_job.errors.values())
        errors.extend(direct_errors)
        complete = (self.active_job is not None and not self.active_job.cancelled
                    and self.active_job.identity is not None
                    and not self.active_job.pending
                    and not getattr(self, 'loading_message', '')
                    and ('查詢完成' in self.status.text() or '既有查詢' in self.status.text()))
        summary = self.history.summary()
        if summary:
            self.loading.setStyleSheet('color: #ff8000; border: none;')
            detail = chr(10).join(dict.fromkeys(errors))
            self.loading.setText(summary + ('\n' + detail if detail else ''))
            return
        color = '#ff0000' if errors else ('#00ff00' if complete else None)
        self.loading.setStyleSheet(('color: %s; ' % color if color else '') + 'border: none;')
        self.loading.setText(chr(10).join(dict.fromkeys(errors)) if errors else
                             getattr(self, 'loading_message', '') or self.status.text())

    def toggle(self, checked):
        if checked:
            self.loading_timer.start()
            self.ensure_inputs()
            if self.locator_panel is not None:
                self.locator_panel.cancel_parcel_location()
            self.tabs.setCurrentIndex(0)
            self.previous = self.canvas.mapTool()
            self.canvas.setMapTool(self.tool)
            self.dialog.show()
            self.dialog.raise_()
            self.set_status('請在地圖上點選宗地內部；切換其他地圖工具即可停止。')
        else:
            self.point_lookup.cancel()
            self.fill_lookup.cancel()
            if self.canvas.mapTool() is self.tool:
                previous, self.previous = self.previous, None
                self.canvas.unsetMapTool(self.tool)
                if previous is not None:
                    try:
                        self.canvas.setMapTool(previous)
                    except RuntimeError:
                        pass

    def activate_point(self):
        if self.locator_panel is not None:
            self.locator_panel.cancel_parcel_location()
        self.tabs.setCurrentIndex(0)
        self.action.setChecked(True)
        self.dialog.show()
        self.dialog.raise_()
        self.set_status('請在地圖上點選宗地內部。')

    def show_input(self):
        self.loading_timer.start()
        self.action.setChecked(False)
        self.fill_lookup.cancel()
        self.dialog.show()
        self.dialog.raise_()
        self.ensure_inputs()
        self.locator_panel.parcel_edit.setFocus()
        self.set_status('在上方欄位輸入，按「定位」查詢；也可點選圖面自動帶入。')

    def ensure_inputs(self):
        if self.locator_panel is None:
            from .parcel_locator import ParcelLocatorDialog
            self.locator_panel = ParcelLocatorDialog(self.iface, self.query_numbers, self.begin_number_query)
            self.locator_panel.setWindowFlags(Qt.WindowType.Widget)
            self.locator_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            self.main_layout.insertWidget(0, self.locator_panel)
            self.locator_panel.output.textChanged.connect(self.input_message)
            for combo in (self.locator_panel.county_combo, self.locator_panel.town_combo, self.locator_panel.section_combo):
                combo.activated.connect(self.input_edited)
                combo.lineEdit().textEdited.connect(self.input_edited)
            self.locator_panel.section_code_edit.textEdited.connect(self.input_edited)
            self.locator_panel.parcel_edit.textEdited.connect(self.input_edited)
        if not self.locator_panel.counties_loaded:
            self.locator_panel.load_counties()
        self.locator_panel.ensure_selected_lists()

    def input_edited(self, *args):
        self.active_job = None
        self.fit_jobs, self.fit_waiting = [], False
        self.notice.clear()
        self.build_notice.clear()
        self.point_lookup.cancel()
        self.buildings.clear()
        self.nlsc.clear()
        # Actual user edits cancel stale results; automatic field population is silent.
        self.action.setChecked(False)
        self.fill_lookup.cancel()
        self.locator_panel.parcel_locator.cancel()
        self.locator_panel.image_loader.cancel()
        self.locator_panel.set_parcel_busy(False)
        self.set_status('已更新輸入條件，按「定位」查詢。')

    def animate_loading(self):
        panel = self.locator_panel
        busy = self.fill_lookup.context is not None or self.point_lookup.context is not None or self.nlsc.is_busy()
        background = sum(bool(job.pending) for job in self.jobs.values())
        busy = busy or bool(background)
        if panel is not None:
            busy = busy or getattr(panel, 'county_request', None) is not None or panel.list_loading or panel.parcel_locator.context is not None or panel.image_loader.context is not None
            busy = busy or bool(panel.point_list_refresh and panel.point_list_refresh.pending)
        if busy:
            self.loading_frame = (self.loading_frame % 3)+1
            self.loading_message = ('背景查詢 %d 筆' % background if background else '載入中') + '.'*self.loading_frame
        else:
            self.loading_frame = 0
            self.loading_message = ''
        self.refresh_message()

    def input_message(self):
        text = self.locator_panel.output.document().lastBlock().text()
        if text:
            if '失敗' in text or '未完成' in text:
                self.failed(text)
            else:
                self.set_status(text)

    def fill_inputs(self, data):
        # NLSC supplies identity and image; NLSC supplies additional details.
        self.pending_identity = dict(data)
        if self.locator_panel is not None:
            self.locator_panel.sync_from_map(data)
        self.query_number(data)

    def begin_query(self):
        self.active_job = None
        self.fit_jobs, self.fit_waiting = [], False
        self.rendered_buildings = None
        self.point_lookup.cancel()
        self.nlsc.clear()
        self.pending_identity = None
        self.pending_images = []
        self.fill_lookup.cancel()
        self.overlay.clear()
        self.fill_warning = ''
        self.buildings.clear()
        self.update_tab_visibility()
        self.table.setRowCount(0)
        self.copy.setEnabled(False)
        self.notice.clear()

    def begin_number_query(self):
        if not self.from_map:
            self.action.setChecked(False)
        self.begin_query()

    def reuse_parcel(self, data, incoming=None):
        fields = request_fields(data)
        key = (str(data['office']).upper(), fields['city'], fields['sect'], fields['landno'])
        for existing in list(self.jobs.values()):
            if existing is incoming or existing.identity is None:
                continue
            other = request_fields(existing.identity)
            other_key = (str(existing.identity['office']).upper(), other['city'], other['sect'], other['landno'])
            if key != other_key:
                continue
            if incoming is not None and 'point' in incoming.pending:
                existing.fill_requested = not bool(existing.images)
                self.history.enable_fill(existing.number)
            if incoming is not None:
                incoming.finish('point')
                incoming.cancel()
                self.jobs.pop(incoming.number, None)
                incoming.deleteLater()
            activate = incoming is None or self.active_job is incoming
            if activate:
                self.active_job = existing
            existing.resume()
            existing.retry_failed_queries()
            if activate:
                self.rendered_buildings = None
                self.render_job(existing)
                if existing.center is not None and not self.submitting_batch:
                    self.job_positioned(existing, *existing.center)
                self.set_status('此地號已有記錄，已切換至既有查詢。')
            return True
        return False

    def new_job(self):
        self.next_job += 1
        job = QueryJob(self.next_job, self)
        self.jobs[job.number] = job
        self.active_job = job
        job.changed.connect(self.job_changed)
        job.positioned.connect(self.job_positioned)
        return job

    def retry_failed_jobs(self, numbers):
        count = 0
        for number in numbers:
            job = self.jobs.get(number)
            if job is not None and job.retry_failed_queries():
                count += 1
        self.set_status('已將 %d 筆失敗記錄加入補查佇列，已完成資料保留。' % count if count else
                        '選取記錄沒有可重試的失敗項目。')

    def remove_jobs(self, numbers):
        removed = set(numbers)
        self.fit_jobs = [job for job in self.fit_jobs if job.number not in removed]
        for number in removed:
            job = self.jobs.pop(number, None)
            if job is not None:
                # Mark cancelled before aborting: queued signals must be ignored.
                job.cancel()
                self.history_versions.pop(number, None)
                if self.active_job is job:
                    self.active_job = None
                    self.begin_query()
                    self.set_status('已清除記錄並取消該筆背景查詢。')
                job.deleteLater()
        self.point_queue = [item for item in self.point_queue if item[0].number not in removed]
        self.detail_queue = [item for item in self.detail_queue if item[0].number not in removed]
        if any(job.number in removed for job in self.point_active):
            self.point_active.difference_update(job for job in list(self.point_active) if job.number in removed)
            self.point_queue_timer.start()
        if self.detail_active is not None and self.detail_active[0].number in removed:
            self.detail_active = None
        self.detail_timer.start()
        self.animate_loading()
        self.fit_queries()

    def queue_point(self, job, x, y):
        self.point_queue.append((job, x, y))
        if not self.point_queue_timer.isActive():
            self.pump_points()

    def pump_points(self):
        # A slow location lookup need not hold every later click. Host queues
        # still bound HTTP concurrency and give parcel fills higher priority.
        while self.point_queue and len(self.point_active) < self.MAX_POINT_LOOKUPS:
            job, x, y = self.point_queue.pop(0)
            if not job.cancelled:
                self.point_active.add(job)
                try:
                    job.start_stage('point', lambda: job.point.start_point(x, y))
                except Exception as exc:
                    job.failed('point', str(exc))

    def queue_location(self, job, data):
        job.start_stage('locate', lambda: job.locator.start_parcel(data))

    def details_allowed(self):
        return not self.unloaded and not self.submitting_batch and not any(
            not job.cancelled and job.pending.intersection(('point', 'locate', 'fill'))
            for job in self.jobs.values())

    def pump_details(self):
        if not self.details_allowed():
            return
        for job in list(self.jobs.values()):
            job.start_attributes()
        # Unblock attributes queued before a new map query arrived. A live
        # reply can finish; queued requests wait until map work is complete.
        schedulers = {client.scheduler for job in self.jobs.values()
                      for client in job.clients().values() if client.scheduler is not None}
        for scheduler in schedulers:
            scheduler.pump()
        if self.detail_active is not None:
            return
        while self.detail_queue:
            job, key, start = self.detail_queue.pop(0)
            if not job.cancelled and key in job.pending:
                self.detail_active = (job, key)
                try:
                    job.start_stage(key, start)
                except Exception as exc:
                    job.failed(key, str(exc))
                return

    def query_number(self, data):
        jobs = self.query_numbers([data])
        return jobs[0] if jobs else None

    def query_numbers(self, values):
        if isinstance(values, dict):
            values = [values]
        submitted, seen = [], set()
        try:
            for value in values:
                data = dict(value)
                fields = request_fields(data)
                key = (str(data['office']).upper(), fields['city'], fields['sect'], fields['landno'])
                if key in seen:
                    continue
                seen.add(key)
                if self.locator_panel is not None:
                    for name, text in [('cityName', self.locator_panel.county_combo.currentText()),
                                       ('townName', self.locator_panel.town_combo.currentText()),
                                       ('sectName', self.locator_panel.section_combo.currentText())]:
                        data.setdefault(name, text)
                submitted.append(data)
        except (ValueError, KeyError, TypeError) as exc:
            self.failed(str(exc))
            return []
        jobs = []
        self.fit_jobs, self.fit_waiting = [], False
        self.submitting_batch = True
        try:
            for data in submitted:
                if self.reuse_parcel(data):
                    job = self.active_job
                else:
                    job = self.new_job()
                    self.set_status('已送出查詢，可繼續點選或輸入下一筆。')
                    job.start_number(data)
                jobs.append(job)
        finally:
            self.submitting_batch = False
            self.detail_timer.start()
        self.fit_jobs, self.fit_waiting = jobs, bool(jobs)
        if len(jobs) > 1:
            self.set_status('已送出 %d 筆地號；取得範圍後將一併定位，可繼續查詢下一筆。' % len(jobs))
        self.fit_queries()
        return jobs

    def load_selected_building(self, index):
        if index == 1:
            self.show_building_result()

    def job_positioned(self, job, x, y):
        if self.submitting_batch or job.cancelled:
            return
        if self.fit_jobs:
            self.fit_queries()
        elif job is self.active_job:
            self.fit_jobs, self.fit_waiting = [job], True
            self.fit_queries()

    def fit_queries(self):
        if not self.fit_waiting or self.submitting_batch:
            return
        jobs = [job for job in self.fit_jobs if not job.cancelled]
        if any('locate' in job.pending or (job.extent is None and 'fill' in job.pending) for job in jobs):
            return
        self.fit_waiting = False
        extents = [job.extent for job in jobs if job.extent is not None]
        centers = [job.center for job in jobs if job.extent is None and job.center is not None]
        try:
            fit_parcels(self.canvas, extents, centers)
        except Exception as exc:
            self.failed('地籍範圍定位失敗：' + str(exc))

    def job_changed(self, job):
        if job.cancelled and job.identity is None:
            return
        self.detail_timer.start()
        if job in self.point_active and 'point' not in job.pending:
            self.point_active.discard(job)
            self.point_queue_timer.start()
        if self.detail_active is not None and self.detail_active[0] is job and self.detail_active[1] not in job.pending:
            self.detail_active = None
        if job.identity is not None:
            version = id(job.images) if job.images else 0
            if self.history_versions.get(job.number) != version:
                self.history.record(job.identity, job.images,
                    self.fill_enabled.isChecked() or job.fill_requested)
                if job.images:
                    job.fill_requested = False
                self.history_versions[job.number] = version
            row = next((i for i, entry in enumerate(self.history.entries)
                if entry['data'].get('_query_id') == job.number), None)
            if row is not None:
                self.history.update_attributes(job.number, job.groups)
                self.history.update_status(job.number, job.status_text(), job.errors.values())
        self.update_copy_state()
        if job is self.active_job and not job.cancelled:
            self.render_job(job)
        self.fit_queries()

    def select_history_job(self, row, column):
        if column == 0 or row >= len(self.history.entries):
            return
        number = self.history.entries[row]['data'].get('_query_id')
        job = self.jobs.get(number)
        if job is not None and job.identity is not None:
            self.fit_jobs, self.fit_waiting = [], False
            self.active_job = job
            self.rendered_buildings = None
            self.locator_panel.sync_from_map(job.identity)
            self.render_job(job)
            self.tabs.setCurrentIndex(0)

    def render_job(self, job):
        if job.identity is not None and self.pending_identity != job.identity:
            self.pending_identity = dict(job.identity)
            self.locator_panel.sync_from_map(job.identity)
        self.nlsc.groups = dict(job.groups)
        self.nlsc.render_groups()
        for key, label in [('land', self.nlsc.status), ('section', self.nlsc.section_status), ('land', self.nlsc.public_status)]:
            label.setText(job.errors.get(key) or ('查詢已取消。' if job.cancelled and key in job.interrupted
                          else '查詢中…' if key in job.pending else '查詢完成。'))
        signature = (job.number, tuple(str(b) for b in job.buildings))
        if signature != self.rendered_buildings:
            self.rendered_buildings = signature
            self.buildings.blockSignals(True)
            self.buildings.clear()
            for building in job.buildings:
                self.buildings.addItem(building['buildNo'] + ' 建號', building)
            self.buildings.blockSignals(False)
            self.build_selection_changed()
        self.show_building_result()
        self.notice.setPlainText('')
        self.set_status(job.status_text() + '；可繼續查詢下一筆。' if job.pending else
            ('本筆查詢已取消；重新查詢此地號可繼續。' if job.cancelled else
             '本筆部分查詢失敗，請查看訊息。' if job.errors else '本筆查詢完成。雙擊記錄可查看其他結果。'))
        self.update_tab_visibility()

    def deactivated(self, *args):
        self.action.setChecked(False)

    def closed(self, *args):
        self.cancel_vector_task()
        self.fit_jobs, self.fit_waiting = [], False
        self.parcel_queue.clear()
        self.point_queue_timer.stop()
        self.point_queue.clear()
        self.point_active.clear()
        self.detail_queue.clear()
        self.detail_active = None
        for job in list(self.jobs.values()):
            if job.pending:
                job.cancel()
                if job.identity is not None:
                    self.job_changed(job)
                else:
                    self.jobs.pop(job.number, None)
                    job.deleteLater()
        self.point_lookup.cancel()
        self.nlsc.cancel()
        self.detail_timer.stop()
        self.history.clear_fills()
        self.loading_timer.stop()
        self.loading.clear()
        if self.locator_panel is not None:
            self.locator_panel.cancel_counties()
            self.locator_panel.cancel_parcel_location()
        self.fill_lookup.cancel()
        self.overlay.clear()
        self.deactivated()

    def clicked(self, point, button):
        if button != Qt.MouseButton.LeftButton or not self.action.isChecked():
            return
        if self.locator_panel is not None:
            self.locator_panel.cancel_parcel_location()
        self.begin_query()
        self.tabs.setCurrentIndex(0)
        try:
            transform = QgsCoordinateTransform(self.canvas.mapSettings().destinationCrs(), QgsCoordinateReferenceSystem('EPSG:4326'), QgsProject.instance())
            wgs = transform.transform(point)
            self.set_status('查詢中：經度 %.8f，緯度 %.8f…' % (wgs.x(), wgs.y()))
            job = self.new_job()
            job.start_point(wgs.x(), wgs.y())
        except Exception:
            self.failed('無法轉換點選座標或啟動查詢，請確認地圖座標系統。')

    def failed(self, message):
        self.set_status('查詢未完成，請查看提示訊息。')
        self.notice.setPlainText(message)

    def current_result_table(self):
        return {0: self.table, 1: self.build_table,
                2: self.nlsc.section_table, 3: self.nlsc.public_table,
                4: self.history.table}.get(self.tabs.currentIndex())

    def update_tab_visibility(self):
        visible = {1: self.buildings.count() > 0, 3: self.nlsc.has_public_data()}
        if self.tabs.currentIndex() in visible and not visible[self.tabs.currentIndex()]:
            self.tabs.setCurrentIndex(0)
        for index, show in visible.items():
            self.tabs.setTabVisible(index, show)
        self.update_copy_state()

    def update_copy_state(self, *args):
        table = self.current_result_table()
        self.copy.setEnabled(table is not None and table.rowCount() > 0)

    def copy_result(self):
        if self.tabs.currentIndex() == 4:
            headers, rows = self.history.copy_rows()
            text, html = grid_formats(headers, rows)
            mime = QMimeData()
            mime.setText(text)
            mime.setHtml(html)
            QApplication.clipboard().setMimeData(mime)
            return
        table = self.current_result_table()
        if table is None:
            return
        rows = []
        for i in range(table.rowCount()):
            values = [table.item(i, j).text() if table.item(i, j) else '' for j in range(table.columnCount())]
            rows.append((values[0], values[1]))
        text, html = table_formats(rows)
        mime = QMimeData()
        mime.setText(text)
        mime.setHtml(html)
        QApplication.clipboard().setMimeData(mime)

    def build_selection_changed(self, *args):
        self.build_table.setRowCount(0)
        self.build_notice.clear()
        self.build_notice.hide()
        available = self.buildings.currentData() is not None
        if available:
            self.build_status.setText('請選取建物以查詢明細。')
        else:
            self.build_status.setText('NLSC 未提供此宗地的建物清單。')
        self.show_building_result()
        self.update_copy_state()

    def show_building_result(self):
        data, job = self.buildings.currentData(), self.active_job
        if not data or job is None:
            return
        key = job.building_key(data)
        if key in job.build_results:
            self.build_received(*job.build_results[key])
        elif key in job.errors:
            self.build_failed(job.errors[key])
        elif key in job.pending:
            self.build_notice.clear()
            self.build_status.setText('建物背景查詢中…')
        else:
            self.build_notice.clear()
            self.build_status.setText('請選取建物以查詢明細。')

    def query_building(self, *args):
        data = self.buildings.currentData()
        job = self.active_job
        if data and job is not None:
            key = job.building_key(data)
            job.request_building(data)
            if key in job.build_results:
                self.build_received(*job.build_results[key])
            elif key in job.errors:
                self.build_failed(job.errors[key])
            else:
                self.build_status.setText('建物背景查詢中…')

    def build_received(self, rows, notice):
        NlscPanel.populate(self.build_table, rows)
        self.update_copy_state()
        self.build_notice.setPlainText(notice)
        self.build_notice.hide()
        self.build_status.setText('建物查詢完成。')

    def build_failed(self, message):
        self.build_status.setText('建物查詢未完成。')
        self.build_notice.setPlainText(message)
        self.build_notice.hide()

    def fill_parcel(self, images, label):
        self.pending_images = images
        if self.pending_identity is not None:
            self.history.record(self.pending_identity, images, self.fill_enabled.isChecked())

    def fill_error(self, message):
        self.fill_warning = message
        self.notice.appendPlainText(message)

    def set_fill_color(self, color):
        if color.isValid():
            self.fill_color = QColor(color)
            self.color_button.set_color(color)
            self.restyle()

    def restyle(self, *args):
        self.overlay.style(self.fill_color, self.opacity_spin.value()/100)
        self.history.restyle()

    def vectorize_fills(self):
        if self.vector_task is not None:
            return
        try:
            from .fill_vector import snapshot, convert, create_layer
            records = snapshot(self.history)
        except Exception as exc:
            self.map_message('生成範圍失敗：' + str(exc))
            return
        if not records:
            self.map_message('生成範圍未完成：請先勾選有填色範圍的記錄。')
            return
        project = QgsProject.instance()
        crs = QgsCoordinateReferenceSystem(self.canvas.mapSettings().destinationCrs())
        record_count = len(records)
        self.vector_button.setEnabled(False)
        self.map_message('正在由 %d 筆填色生成範圍…' % record_count)

        def finished(exception, results=None):
            task, self.vector_task = self.vector_task, None
            if self.unloaded:
                return
            self.vector_button.setEnabled(True)
            if task.isCanceled():
                self.map_message('生成範圍已取消。')
                return
            if exception is not None:
                self.map_message('生成範圍失敗：' + str(exception))
                return
            if results is None:
                self.map_message('生成範圍已取消。')
                return
            try:
                layer = create_layer(results, crs, project)
                project.addMapLayer(layer, False)
                root = project.layerTreeRoot()
                root.insertLayer(0, layer)
                if root.hasCustomLayerOrder():
                    root.setCustomLayerOrder([layer] + [item for item in root.customLayerOrder()
                                                       if item.id() != layer.id()])
                self.map_message('生成範圍完成：%d 筆宗地已融合為 1 個圖徵。' % record_count)
            except Exception as exc:
                self.map_message('生成範圍失敗：' + str(exc))

        self.vector_task = QgsTask.fromFunction('NLSC生成範圍', convert,
                                               on_finished=finished, records=records,
                                               style=(self.fill_color.name(), self.opacity_spin.value()/100))
        QgsApplication.taskManager().addTask(self.vector_task)

    def cancel_vector_task(self):
        if self.vector_task is not None:
            self.vector_task.cancel()

    def unload(self):
        self.unloaded = True
        # Also cancel when the dialog was never shown or is already hidden.
        self.closed()
        self.point_lookup.cancel()
        if self.locator_panel is not None:
            self.locator_panel.cancel_counties()
        self.action.setChecked(False)
        self.fill_lookup.cancel()
        self.overlay.clear()
        self.dialog.close()
        self.dialog.deleteLater()
        self.tool.deleteLater()
        self.deleteLater()
