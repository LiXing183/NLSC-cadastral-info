"""Independent QGIS parcel locator using NLSC lists and MOI NLSC."""
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QComboBox, QPlainTextEdit, QMessageBox, QCompleter, QCheckBox, QSpinBox, QColorDialog, QWidget
)
try:
    from qgis.PyQt.QtGui import QAction
except ImportError:
    from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtCore import Qt, QTimer, QStringListModel
from qgis.PyQt.QtGui import QColor, QIcon
from qgis.PyQt.QtNetwork import QNetworkRequest, QNetworkReply
from qgis.core import QgsNetworkAccessManager
from . import safe_xml as ET
from pathlib import Path
from .parcel_input import parcel_numbers
from .parcel_view import fit_parcels
from .nlsc_fill import NlscFillLookup
from . import list_cache
from .canvas_overlay import ParcelOverlay, color_swatch
from .click_tool import ParcelClickTool
from .nlsc_number import NlscNumberLookup
from .parcel_combo import ParcelComboBox


class MOIParcelLocatorPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dialog = None
        self.click_action = None
        self.click_controller = None
        self.cleanup_action = None

    def initGui(self):
        if self.click_controller is not None:
            return
        self.action = QAction(QIcon(str(Path(__file__).with_name('entrance-ico.svg'))), "NLSC地籍資訊查詢", self.iface.mainWindow())
        self.action.setToolTip("開啟宗地查詢：輸入地號或點選圖面")
        self.action.triggered.connect(self.run)
        self.click_action = QAction('NLSC 點選宗地屬性', self.iface.mainWindow())
        self.click_action.setCheckable(True)
        self.click_action.setToolTip('點選地圖查詢地段、地號、面積與公告地價等資料')
        try:
            self.click_controller = ParcelClickTool(self.iface, self.click_action)
        except Exception:
            self.click_action.deleteLater()
            self.click_action = None
            self.action.deleteLater()
            self.action = None
            raise
        # Internal map-tool state; only the unified query action is exposed.
        from .overlay_cache import clean_legacy_cache
        self.cleanup_action = QAction('清理舊填色暫存檔', self.iface.mainWindow())
        self.cleanup_action.triggered.connect(lambda: clean_legacy_cache(self.iface))
        # Publish actions only after the controller has initialized successfully.
        self.action.setObjectName('moi_parcel_locator.query')
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu('NLSC地籍資訊查詢', self.action)
        self.iface.addPluginToMenu('NLSC地籍資訊查詢', self.cleanup_action)
        # Fetch lists when the plugin is enabled, before the first dropdown click.
        list_cache.prune()
        self.click_controller.ensure_inputs()

    def run(self):
        self.click_controller.show_input()

    def unload(self):
        if self.cleanup_action is not None:
            self.iface.removePluginMenu('NLSC地籍資訊查詢', self.cleanup_action)
            self.cleanup_action.deleteLater()
            self.cleanup_action = None
        if self.click_controller is not None:
            self.click_controller.unload()
            self.click_controller = None
        if self.click_action is not None:
            self.click_action.deleteLater()
            self.click_action = None
        if self.dialog is not None:
            self.dialog.cancel_counties()
            self.dialog.cancel_parcel_location()
            self.dialog.close()
            self.dialog.deleteLater()
            self.dialog = None
        if self.action is not None:
            self.iface.removeToolBarIcon(self.action)
            self.iface.removePluginMenu("NLSC地籍資訊查詢", self.action)
            self.action.deleteLater()
            self.action = None


class ParcelLocatorDialog(QDialog):
    NLSC_COUNTY_URL = "https://api.nlsc.gov.tw/other/ListCounty"
    NLSC_TOWN_URL = "https://api.nlsc.gov.tw/other/ListTown/{}"
    NLSC_SECTION_URL = "https://api.nlsc.gov.tw/other/ListLandSection/{}/{}"

    def __init__(self, iface, query_callback=None, before_query=None):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.query_callback = query_callback
        self.before_query = before_query
        self.point_list_refresh = None
        self.list_loading = False
        self.list_requests = {}
        self.county_request = None
        self.counties_loaded = False
        self.towns_loaded_for = None
        self.sections_loaded_for = None
        self.setWindowTitle("NLSC地籍資訊查詢")
        self.resize(600, 640)
        self.section_records = []
        self.section_by_code = {}
        self._section_syncing = False
        self.overlay = ParcelOverlay(iface.mapCanvas())
        self.fill_color = QColor("#00ffff")
        self.last_parcel = None
        self.build_ui()
        self.parcel_locator = NlscNumberLookup(self)
        self.parcel_locator.MAX_TOKEN_RETRIES = 1
        self.parcel_locator.located.connect(self.on_parcel_located)
        self.parcel_locator.failed.connect(self.on_parcel_failed)
        self.parcel_locator.progress.connect(self.output.appendPlainText)
        self.image_loader = NlscFillLookup(self)
        self.image_loader.images_ready.connect(lambda images, label: self.on_parcel_images(images))
        self.image_loader.failed.connect(self.on_image_failed)
        self.finished.connect(self.cancel_parcel_location)
        if query_callback is None:
            self.load_counties()

    def build_ui(self):
        main = QVBoxLayout(self)
        title = QLabel("NLSC地籍資訊查詢")
        title.setStyleSheet("font-size:18px;font-weight:bold;")
        main.addWidget(title)
        info = QLabel("選擇地段並輸入地號，可用逗號分隔多筆；依宗地範圍自動定位。")
        info.setWordWrap(True)
        main.addWidget(info)
        location_row = QHBoxLayout()
        for label, name in (("縣市", "county_combo"), ("行政區", "town_combo")):
            column = QVBoxLayout()
            column.addWidget(QLabel(label + "："))
            combo = ParcelComboBox()
            self.setup_combo(combo)
            setattr(self, name, combo)
            column.addWidget(combo)
            location_row.addLayout(column, 1)
        main.addLayout(location_row)

        main.addWidget(QLabel("地段代碼／地段名稱："))
        row = QHBoxLayout()
        self.section_code_edit = QLineEdit()
        self.section_code_edit.setPlaceholderText("例如 0828")
        self.section_code_edit.setClearButtonEnabled(True)
        self.section_combo = ParcelComboBox()
        self.setup_combo(self.section_combo)
        row.addWidget(self.section_code_edit, 1)
        row.addWidget(self.section_combo, 2)
        main.addLayout(row)
        self.section_code_model = QStringListModel(self)
        self.section_code_completer = QCompleter(self.section_code_model, self)
        self.section_code_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.section_code_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.section_code_completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.section_code_edit.setCompleter(self.section_code_completer)
        self.code_label = QLabel("地政事務所：-\nUNIT：-\nSEC：-")
        main.addWidget(self.code_label)

        row = QHBoxLayout()
        row.addWidget(QLabel("地號："))
        self.parcel_edit = QLineEdit()
        self.parcel_edit.setPlaceholderText("單筆或多筆，例如 1, 43, 123-4")
        self.parcel_edit.setToolTip('查詢目前地段的多筆地號，以逗號分隔；重複地號只查一次。')
        self.locate_button = QPushButton("定位")
        self.locate_button.setAutoDefault(False)
        row.addWidget(self.parcel_edit, 1)
        row.addWidget(self.locate_button)
        main.addLayout(row)
        fill_controls = QWidget()
        fill_layout = QVBoxLayout(fill_controls)
        self.fill_check = QCheckBox("定位後顯示宗地填色")
        self.fill_check.setChecked(True)
        fill_layout.addWidget(self.fill_check)
        fill_row = QHBoxLayout()
        self.custom_color_check = QCheckBox("自訂顏色（取消恢復洋紅色）")
        self.custom_color_check.setChecked(True)
        self.fill_color_button = QPushButton("顏色：洋紅色")
        color_swatch(self.fill_color_button, self.fill_color)
        self.fill_color_button.setAutoDefault(False)
        self.opacity_spin = QSpinBox()
        self.opacity_spin.setRange(0, 100)
        self.opacity_spin.setSuffix(" %")
        self.opacity_spin.setValue(30)
        self.opacity_spin.setToolTip("0% 為完全透明，100% 為不透明。")
        self.clear_fill_button = QPushButton("清除填色")
        self.clear_fill_button.setAutoDefault(False)
        fill_row.addWidget(self.custom_color_check)
        fill_row.addWidget(self.fill_color_button)
        fill_row.addWidget(QLabel("不透明度："))
        fill_row.addWidget(self.opacity_spin)
        fill_row.addWidget(self.clear_fill_button)
        fill_layout.addLayout(fill_row)
        main.addWidget(fill_controls)
        fill_controls.setVisible(self.query_callback is None)
        self.fill_color_button.clicked.connect(self.choose_fill_color)
        self.custom_color_check.toggled.connect(self.restyle_fill)
        self.opacity_spin.valueChanged.connect(self.update_fill_opacity)
        self.clear_fill_button.clicked.connect(self.clear_parcel_fill)
        main.addWidget(QLabel("執行訊息："))
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(200)
        main.addWidget(self.output, 1)
        self.clear_button = QPushButton("清除訊息")
        self.clear_button.setAutoDefault(False)
        main.addWidget(self.clear_button)
        self.clear_button.clicked.connect(self.output.clear)
        self.county_combo.currentIndexChanged.connect(self.on_county_changed)
        self.town_combo.currentIndexChanged.connect(self.on_town_changed)
        self.county_combo.activated.connect(lambda: self.ensure_selected_lists())
        self.town_combo.activated.connect(lambda: self.ensure_selected_lists())
        self.section_combo.currentIndexChanged.connect(self.on_section_changed)
        self.section_code_edit.textEdited.connect(self.on_section_code_edited)
        self.section_code_edit.returnPressed.connect(self.on_section_code_enter)
        self.section_code_edit.editingFinished.connect(self.on_section_code_finished)
        self.section_code_completer.activated.connect(self.on_section_code_completion)
        self.locate_button.clicked.connect(self.locate_parcel)
        self.parcel_edit.returnPressed.connect(self.locate_parcel)
        if self.query_callback is not None:
            title.hide()
            info.hide()
            self.code_label.hide()
            self.output.hide()
            self.clear_button.hide()
            # Keep only the selection fields in the pinned header.
            for i in range(main.count()):
                widget = main.itemAt(i).widget()
                if isinstance(widget, QLabel) and widget.text() == '執行訊息：':
                    widget.hide()
            main.setContentsMargins(0, 0, 0, 0)

    def sync_from_map(self, data):
        self.cancel_lists()
        from .point_lists import PointListRefresh
        if self.point_list_refresh is None:
            self.point_list_refresh = PointListRefresh(self)
        self._section_syncing = True
        combos = (self.county_combo, self.town_combo, self.section_combo)
        for combo in combos:
            combo.blockSignals(True)
        try:
            city_changed = self.county_combo.currentData() != data['cityCode']
            town_changed = city_changed or self.town_combo.currentData() != data['townCode']
            if city_changed:
                self.town_combo.clear()
                self.towns_loaded_for = None
            if town_changed:
                self.section_combo.clear()
                self.sections_loaded_for = None
                self.section_records = []
                self.section_by_code = {}
            for combo, key, name in ((self.county_combo, 'cityCode', 'cityName'), (self.town_combo, 'townCode', 'townName')):
                index = combo.findData(data[key])
                if index < 0:
                    combo.addItem(data.get(name) or data[key], data[key])
                    index = combo.count()-1
                combo.setCurrentIndex(index)
                combo.setEditText(combo.itemText(index))
            record = dict(office=data['office'], officestr='', sectcode=data['sectNo'], sectstr=data.get('sectName') or data['sectNo'])
            if data['sectNo'] not in self.section_by_code:
                self.section_records.append(record)
                self.section_combo.addItem(record['sectcode'] + '｜' + record['sectstr'], record)
            self.section_by_code[data['sectNo']] = record
            self.section_code_model.setStringList([r['sectcode']+'｜'+r['sectstr'] for r in self.section_records])
            self.apply_section_selection(record)
            self.parcel_edit.setText(data['landNo'])
        finally:
            self._section_syncing = False
            for combo in combos:
                combo.blockSignals(False)
        self.point_list_refresh.start(data)

    def setup_combo(self, combo):
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.lineEdit().editingFinished.connect(lambda: self.resolve_combo_text(combo))
        combo.lineEdit().textEdited.connect(lambda: self.resolve_combo_text(combo, clear_invalid=False))
        completer = combo.completer()
        if completer:
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)

    def resolve_combo_text(self, combo, clear_invalid=True):
        raw = combo.currentText().strip()
        if not raw:
            return
        index = combo.findText(raw)
        if index >= 0:
            combo.setCurrentIndex(index)
            data = combo.itemData(index)
            if isinstance(data, dict):
                self.apply_section_selection(data)
            return
        if combo is self.section_combo:
            data = self.section_by_code.get(raw) or next(
                (record for record in self.section_records if record['sectstr'] == raw), None)
            if data is not None:
                self.apply_section_selection(data)
                return
        else:
            index = combo.findData(raw)
            if index >= 0:
                combo.setCurrentIndex(index)
                return
        if raw and clear_invalid:
            combo.setCurrentIndex(-1)
            combo.setEditText(raw)
            if combo is getattr(self, 'section_combo', None):
                self.section_code_edit.clear()

    def cancel_lists(self):
        pending, self.list_requests = self.list_requests, {}
        self.list_loading = False
        for reply, timer, slot in pending.values():
            timer.stop()
            reply.finished.disconnect(slot)
            reply.abort()
            reply.deleteLater()
            timer.deleteLater()

    def request_list(self, kind, url, snapshot, apply):
        if kind in self.list_requests:
            return
        cached = list_cache.get(url)
        if cached is not None:
            apply(cached)
            return
        reply = QgsNetworkAccessManager.instance().get(list_cache.list_request(url))
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(reply.abort)
        def finished():
            current = self.list_requests.get(kind)
            if current is None or current[0] is not reply:
                return
            self.list_requests.pop(kind)
            self.list_loading = bool(self.list_requests)
            timer.stop()
            ok = (reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) == 200
                  and reply.error() == QNetworkReply.NetworkError.NoError)
            body = bytes(reply.readAll())
            reply.deleteLater()
            timer.deleteLater()
            selection = (self.county_combo.currentData(), self.town_combo.currentData())
            if selection[:len(snapshot)] != snapshot:
                return
            if not ok or not list_cache.valid(url, body):
                self.output.appendPlainText(('行政區' if kind == 'town' else '地段') + '資料取得失敗，請重新選擇。')
                return
            list_cache.put(url, body)
            apply(body)
        self.list_requests[kind] = (reply, timer, finished)
        reply.finished.connect(finished)
        self.list_loading = True
        timer.start(30000)

    def xml_text(self, element, name):
        child = element.find(".//" + name)
        if child is not None and child.text:
            return child.text.strip()
        return None

    def load_counties(self):
        if self.county_request is not None:
            return
        cached = list_cache.get(self.NLSC_COUNTY_URL)
        if cached is not None:
            self.apply_counties(cached)
            return
        self.output.appendPlainText("載入縣市資料...")
        reply = QgsNetworkAccessManager.instance().get(list_cache.list_request(self.NLSC_COUNTY_URL))
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(30000)
        timer.timeout.connect(reply.abort)
        def slot():
            return self.counties_finished(reply, timer)

        self.county_request = (reply, timer, slot)
        reply.finished.connect(slot)
        timer.start()

    def cancel_counties(self):
        pending, self.county_request = self.county_request, None
        if pending is not None:
            reply, timer, slot = pending
            timer.stop()
            reply.finished.disconnect(slot)
            reply.abort()
            reply.deleteLater()
            timer.deleteLater()

    def counties_finished(self, reply, timer):
        if self.county_request is None or self.county_request[0] is not reply:
            return
        self.county_request = None
        timer.stop()
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        body = bytes(reply.readAll())
        ok = reply.error() == QNetworkReply.NetworkError.NoError
        reply.deleteLater()
        timer.deleteLater()
        if not ok or status != 200 or not list_cache.valid(self.NLSC_COUNTY_URL, body):
            self.output.appendPlainText("縣市資料取得失敗；重新開啟查詢視窗可重試，亦可點選圖面查詢。")
            return
        list_cache.put(self.NLSC_COUNTY_URL, body)
        self.apply_counties(body)

    def apply_counties(self, body):
        root = ET.fromstring(body)
        combo = self.county_combo
        # A map click or typed input may have arrived while downloading.
        code, text = combo.currentData(), combo.currentText()
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItem("請選擇縣市", None)
            for item in root:
                item_code = self.xml_text(item, "countycode")
                name = self.xml_text(item, "countyname")
                if item_code and name:
                    combo.addItem(name, item_code)
            index = combo.findData(code) if code else 0
            if code and index < 0:
                combo.addItem(text or code, code)
                index = combo.count() - 1
            combo.setCurrentIndex(index)
            if text:
                combo.setEditText(text)
        finally:
            combo.blockSignals(False)
        self.counties_loaded = True
        self.output.appendPlainText("縣市資料載入完成。")
        self.ensure_selected_lists()

    def ensure_selected_lists(self):
        if self.point_list_refresh is not None and self.point_list_refresh.pending:
            return
        county, town = self.county_combo.currentData(), self.town_combo.currentData()
        if county and self.towns_loaded_for != county and 'town' not in self.list_requests:
            self.load_towns(county)
        if county and town and self.sections_loaded_for != (county, town) and 'section' not in self.list_requests:
            self.load_sections(county, town)

    def on_county_changed(self):
        self.cancel_lists()
        if self.point_list_refresh is not None:
            self.point_list_refresh.cancel()
        countycode = self.county_combo.currentData()
        self.towns_loaded_for = None
        self.sections_loaded_for = None

        self.town_combo.blockSignals(True)
        self.town_combo.clear()
        self.town_combo.addItem("請選擇行政區", None)
        self.town_combo.blockSignals(False)

        self.section_combo.blockSignals(True)
        self.section_combo.clear()
        self.section_combo.addItem("請選擇地段", None)
        self.section_combo.blockSignals(False)

        self.section_code_edit.clear()
        self.section_records = []
        self.section_by_code = {}
        self.section_code_model.setStringList([])
        self.code_label.setText("地政事務所：-\nUNIT：-\nSEC：-")

        if countycode:
            self.load_towns(countycode)

    def load_towns(self, countycode):
        if self.towns_loaded_for == countycode or 'town' in self.list_requests:
            return
        self.output.appendPlainText("載入行政區...")
        self.request_list('town', self.NLSC_TOWN_URL.format(countycode), (countycode,), self.apply_towns)

    def apply_towns(self, body):
        try:
            root = ET.fromstring(body)
        except Exception as e:
            self.output.appendPlainText("行政區 XML 解析失敗：" + str(e))
            return

        selected_code = self.town_combo.currentData()
        self.town_combo.blockSignals(True)
        self.town_combo.clear()
        self.town_combo.addItem("請選擇行政區", None)

        for item in root:
            code = self.xml_text(item, "towncode")
            name = self.xml_text(item, "townname")
            if code and name:
                self.town_combo.addItem(name, code)

        if selected_code:
            index = self.town_combo.findData(selected_code)
            self.town_combo.setCurrentIndex(index if index >= 0 else 0)
        self.town_combo.blockSignals(False)
        self.towns_loaded_for = self.county_combo.currentData()
        self.output.appendPlainText("行政區載入完成。")
        self.ensure_selected_lists()

    def on_town_changed(self):
        self.cancel_lists()
        if self.point_list_refresh is not None:
            self.point_list_refresh.cancel()
        countycode = self.county_combo.currentData()
        towncode = self.town_combo.currentData()
        self.sections_loaded_for = None

        self.section_combo.blockSignals(True)
        self.section_combo.clear()
        self.section_combo.addItem("請選擇地段", None)
        self.section_combo.blockSignals(False)

        self.section_code_edit.clear()
        self.section_records = []
        self.section_by_code = {}
        self.section_code_model.setStringList([])
        self.code_label.setText("地政事務所：-\nUNIT：-\nSEC：-")

        if countycode and towncode:
            self.load_sections(countycode, towncode)

    def load_sections(self, countycode, towncode):
        if self.sections_loaded_for == (countycode, towncode) or 'section' in self.list_requests:
            return
        self.output.appendPlainText("載入地段...")
        self.request_list('section', self.NLSC_SECTION_URL.format(countycode, towncode),
                          (countycode, towncode), self.apply_sections)

    def apply_sections(self, body):
        try:
            root = ET.fromstring(body)
        except Exception as e:
            self.output.appendPlainText("地段 XML 解析失敗：" + str(e))
            return

        code = self.section_code_edit.text()
        self.section_combo.blockSignals(True)
        self.section_combo.clear()
        self.section_combo.addItem("請選擇地段", None)

        self.section_records = []
        self.section_by_code = {}

        for item in root:
            office = self.xml_text(item, "office")
            officestr = self.xml_text(item, "officestr")
            sectcode = self.xml_text(item, "sectcode")
            sectstr = self.xml_text(item, "sectstr")

            if office and sectcode and sectstr:
                data = {
                    "office": office,
                    "officestr": officestr,
                    "sectcode": sectcode,
                    "sectstr": sectstr,
                }
                self.section_records.append(data)
                self.section_by_code[sectcode] = data
                self.section_combo.addItem(
                    "{}｜{}".format(sectcode, sectstr), data
                )

        self.section_combo.blockSignals(False)
        self.section_code_model.setStringList([
            "{}｜{}".format(r["sectcode"], r["sectstr"])
            for r in self.section_records
        ])
        self.sections_loaded_for = (self.county_combo.currentData(), self.town_combo.currentData())
        if code in self.section_by_code:
            self.apply_section_selection(self.section_by_code[code])
        self.output.appendPlainText(
            "地段載入完成，共 {} 筆；可輸入代碼或名稱搜尋。".format(
                len(self.section_records)
            )
        )

    def on_section_changed(self):
        if self._section_syncing:
            return
        data = self.section_combo.currentData()
        if not isinstance(data, dict):
            self.code_label.setText("地政事務所：-\nUNIT：-\nSEC：-")
            return
        self.apply_section_selection(data)

    def apply_section_selection(self, data):
        if not isinstance(data, dict):
            return

        self._section_syncing = True
        try:
            sectcode = data.get("sectcode") or ""
            sectstr = data.get("sectstr") or ""
            self.section_code_edit.setText(sectcode)

            index = self.section_combo.findText(sectcode + '｜', Qt.MatchFlag.MatchStartsWith)
            if index >= 0:
                self.section_combo.setCurrentIndex(index)
                self.section_combo.setEditText(sectstr)

            self.code_label.setText(
                "地政事務所：{}\nUNIT：{}\nSEC：{}".format(
                    data.get("officestr") or "-",
                    data.get("office") or "-",
                    sectcode or "-",
                )
            )
        finally:
            self._section_syncing = False

    def on_section_code_edited(self, text):
        if self._section_syncing:
            return
        raw = text.strip()
        data = self.section_by_code.get(raw)
        if data:
            self.apply_section_selection(data)

    def normalize_section_code(self, text):
        raw = str(text).strip().split("｜", 1)[0].strip()
        if not raw:
            return ""
        # NLSC 段代碼為 4 碼；純數字不足 4 碼時自動左補 0。
        if raw.isdigit() and len(raw) < 4:
            return raw.zfill(4)
        return raw

    def on_section_code_enter(self):
        if self._section_syncing:
            return

        raw = self.section_code_edit.text().strip()
        if not raw:
            return

        code_value = self.normalize_section_code(raw)
        data = self.section_by_code.get(code_value)

        if data:
            # Enter 直接帶入地段，不需要再點候選清單。
            self.apply_section_selection(data)

            # 收起可能仍開著的 completer 候選清單。
            popup = self.section_code_completer.popup()
            if popup is not None:
                popup.hide()

            # 將焦點移到地段名稱，讓使用者明確看到已完成帶入。
            self.parcel_edit.setFocus()
        else:
            self.code_label.setText("地政事務所：-\nUNIT：-\nSEC：-")
            QMessageBox.warning(
                self,
                "找不到地段代碼",
                "目前行政區中找不到地段代碼：{}".format(code_value),
            )

    def on_section_code_finished(self):
        if self._section_syncing:
            return
        raw = self.section_code_edit.text().strip()
        if not raw:
            return
        code_value = self.normalize_section_code(raw)
        data = self.section_by_code.get(code_value)
        if data:
            self.apply_section_selection(data)
        else:
            self.code_label.setText("地政事務所：-\nUNIT：-\nSEC：-")

    def on_section_code_completion(self, text):
        code_value = self.normalize_section_code(text)
        data = self.section_by_code.get(code_value)
        if data:
            self.apply_section_selection(data)

    def locate_parcel(self):
        if self.parcel_locator.context is not None or self.image_loader.context is not None:
            return
        try:
            if self.county_combo.currentData() is None or self.town_combo.currentData() is None:
                raise ValueError('請輸入或選擇有效的縣市及行政區。')
            lands = parcel_numbers(self.parcel_edit.text())
            if self.query_callback is None and len(lands) > 1:
                raise ValueError('請由宗地查詢主視窗執行多筆查詢。')
            code = self.normalize_section_code(self.section_code_edit.text())
            data = self.section_by_code.get(code) if code else self.section_combo.currentData()
            if not isinstance(data, dict):
                raise ValueError("請先選擇有效地段；目前輸入的地段代碼無法對應。")
            if not data.get("office") or not data.get("sectcode"):
                raise ValueError("目前地段缺少 office 或 SEC。")
            self.apply_section_selection(data)
        except ValueError as exc:
            self.output.appendPlainText("地號定位失敗：" + str(exc))
            return
        land = lands[-1]
        identities = [dict(cityCode=self.county_combo.currentData(),
            townCode=self.town_combo.currentData(), cityName=self.county_combo.currentText(),
            townName=self.town_combo.currentText(), office=data['office'],
            sectNo=data['sectcode'], sectName=data['sectstr'], landNo=number) for number in lands]
        self.output.appendPlainText(
            "開始地號定位：office={} / sectNo={} / landNo={}".format(
                data["office"], data["sectcode"], ', '.join(lands)))
        self.set_parcel_busy(True)
        if self.before_query is not None:
            self.before_query()
        self.last_parcel = (data["office"], data["sectcode"], land)
        self.last_identity = identities[-1]
        self.overlay.clear()
        if self.query_callback is not None:
            self.set_parcel_busy(False)
            self.query_callback(identities)
            self.parcel_edit.setText(', '.join(lands))
            return
        self.parcel_locator.start_parcel(self.last_identity)

    def set_parcel_busy(self, busy):
        self.locate_button.setEnabled(not busy)
        self.locate_button.setText("定位中…" if busy else "定位")
        for widget in (self.fill_check, self.clear_fill_button):
            widget.setEnabled(not busy)
        for widget in (self.county_combo, self.town_combo, self.section_combo,
                       self.section_code_edit, self.parcel_edit):
            widget.setEnabled(True)

    def cancel_parcel_location(self, *args):
        self.cancel_lists()
        self.set_parcel_busy(False)
        if self.point_list_refresh is not None:
            self.point_list_refresh.cancel()
        self.overlay.clear()
        if self.parcel_locator.context is not None or self.image_loader.context is not None:
            self.parcel_locator.cancel()
            self.image_loader.cancel()
            self.set_parcel_busy(False)
            self.output.appendPlainText("地號定位已取消。")

    def on_parcel_failed(self, message):
        self.set_parcel_busy(False)
        self.output.appendPlainText(message)

    def on_parcel_located(self, x, y):
        try:
            canvas = self.iface.mapCanvas()
            extent = self.parcel_locator.extent
            scale = fit_parcels(canvas, [extent] if extent else [], [] if extent else [(x, y)])
            self.output.appendPlainText(
                "地號定位成功：X={:.6f}, Y={:.6f}｜{}｜比例尺 1:{:.0f}".format(
                    x, y, canvas.mapSettings().destinationCrs().authid(), scale))
        except Exception as exc:
            self.output.appendPlainText(
                "NLSC 已回傳 X={}, Y={}，但 QGIS 定位失敗：{}".format(x, y, exc))
        if self.query_callback is not None:
            self.set_parcel_busy(False)
            self.query_callback(dict(self.last_identity))
        elif self.fill_check.isChecked() and self.last_parcel:
            self.locate_button.setText("載入填色…")
            self.output.appendPlainText("取得宗地填色影像…")
            self.image_loader.start_parcel(self.last_identity)
        else:
            self.set_parcel_busy(False)

    def choose_fill_color(self):
        color = QColorDialog.getColor(self.fill_color, self, "宗地填色顏色")
        if color.isValid():
            self.fill_color = color
            color_swatch(self.fill_color_button, color)
            self.custom_color_check.setChecked(True)
            self.restyle_fill()

    def fill_opacity(self):
        return self.opacity_spin.value()/100.0

    def on_parcel_images(self, images):
        self.set_parcel_busy(False)
        try:
            label = "{}／{}／{}".format(*self.last_parcel)
            self.overlay.show(images, label,
                self.fill_color if self.custom_color_check.isChecked() else None,
                self.fill_opacity())
            self.output.appendPlainText("宗地填色完成：{} 張影像合併為暫時填色，關閉視窗即移除。".format(len(images)))
        except Exception as exc:
            self.output.appendPlainText("宗地填色失敗（定位結果保留）：" + str(exc))

    def on_image_failed(self, message):
        self.set_parcel_busy(False)
        self.output.appendPlainText("宗地填色未取得，已完成的定位結果保留。\n" + message)

    def restyle_fill(self, *args):
        color_swatch(self.fill_color_button, self.fill_color if self.custom_color_check.isChecked() else None)
        if self.overlay.images:
            try:
                self.overlay.style(
                    self.fill_color if self.custom_color_check.isChecked() else None,
                    self.fill_opacity())
            except Exception as exc:
                self.output.appendPlainText("更新填色失敗：" + str(exc))

    def update_fill_opacity(self, *args):
        self.overlay.opacity(self.fill_opacity())

    def clear_parcel_fill(self):
        self.overlay.clear()
        self.output.appendPlainText("已清除本次宗地填色。")
