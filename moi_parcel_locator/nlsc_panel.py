from qgis.PyQt.QtWidgets import QWidget, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem, QAbstractItemView
from .nlsc_lookup import NlscLookup, NlscSectionLookup
from .nlsc_display import friendly_rows, ownership_rows, compact_field_label, public_rows, survey_rows


class NlscPanel(QWidget):
    """Merged land page with two sibling pages, without nested tabs."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.groups = {}
        self.on_changed = None
        layout = QVBoxLayout(self)
        self.status = QLabel('查詢宗地後，自動取得 NLSC 資料。')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.table = self.make_table()
        layout.addWidget(self.table, 1)
        self.section_page = QWidget()
        section_layout = QVBoxLayout(self.section_page)
        self.section_status = QLabel('地段測繪：等待查詢。')
        self.section_status.setWordWrap(True)
        section_layout.addWidget(self.section_status)
        self.section_table = self.make_table()
        section_layout.addWidget(self.section_table, 1)
        self.public_page = QWidget()
        public_layout = QVBoxLayout(self.public_page)
        self.public_status = QLabel('公有土地：等待查詢。')
        self.public_status.setWordWrap(True)
        public_layout.addWidget(self.public_status)
        self.public_table = self.make_table()
        public_layout.addWidget(self.public_table, 1)
        self.client = NlscLookup(self)
        self.client.ready.connect(self.received)
        self.client.failed.connect(self.land_failed)
        self.section_client = NlscSectionLookup(self)
        self.section_client.ready.connect(self.section_received)
        self.section_client.failed.connect(self.section_status.setText)

    @staticmethod
    def make_table():
        table = QTableWidget(0, 2)
        table.setHorizontalHeaderLabels(['欄位', '內容'])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setStretchLastSection(True)
        table.setColumnWidth(0, 180)
        table.setColumnWidth(1, 290)
        return table

    @staticmethod
    def populate(table, rows):
        rows = [(compact_field_label(key), str(value)) for key, value in rows]
        if table.rowCount() == len(rows) and all(
                table.item(i, j) is not None and table.item(i, j).text() == text
                for i, row in enumerate(rows) for j, text in enumerate(row)):
            return
        table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, text in enumerate(row):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                if len(row) == 2 and row[1] == '' and row[0].startswith(('公有土地資料', '公有土地所有權人資料', '地段數化整合資訊', '地段原始資訊')):
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                table.setItem(i, j, item)
        table.resizeRowsToContents()

    def cancel(self):
        self.client.cancel()
        self.section_client.cancel()

    def is_busy(self):
        return self.client.context is not None or self.section_client.context is not None

    def clear(self):
        self.cancel()
        self.groups.clear()
        self.render_groups()
        self.status.setText('等待宗地查詢。')
        self.section_status.setText('地段測繪：等待查詢。')
        self.public_status.setText('公有土地：等待查詢。')

    def start(self, identity):
        self.clear()
        self.status.setText('正在取得 NLSC 土地資訊…')
        self.public_status.setText('正在取得公有土地資訊…')
        try:
            self.client.start_parcel(identity)
        except ValueError as exc:
            self.land_failed(str(exc))
        self.section_status.setText('正在取得地段測繪資訊…')
        try:
            self.section_client.start_parcel(identity)
        except ValueError as exc:
            self.section_status.setText(str(exc))

    def land_failed(self, message):
        self.status.setText(message)
        self.public_status.setText(message)

    def received(self, groups):
        self.groups.update(groups)
        self.render_groups()
        self.status.setText('NLSC 土地資訊查詢完成。')
        self.public_status.setText('公有土地資訊查詢完成。' if self.groups.get('公有土地')
                                   else '本次回應未提供公有土地資料。')

    def section_received(self, groups):
        self.groups.update(groups)
        self.render_groups()
        self.section_status.setText('地段測繪查詢完成。')

    def rows_for(self, title):
        raw = self.groups.get(title, [])
        if title == '公有土地':
            return public_rows(raw)
        if title == '地段測繪資訊':
            return survey_rows(raw)
        rows = friendly_rows(title, raw)
        if title == '土地資訊':
            if '權利類別原始資料' in self.groups:
                rows += ownership_rows(self.groups['權利類別原始資料'])
            # Display formatted fields while retaining the source rows in groups.
            rows = [(k, v) for k, v in rows
                    if k not in ('地目', '等則') and not any('['+code+']' in k for code in ('AA08', 'AA09'))]
            dates = [(k, v) for k, v in rows if k.startswith('登記日期') or k.startswith('登記原因')]
            rows = [(k, v) for k, v in rows if not (k.startswith('登記日期') or k.startswith('登記原因'))]
            index = next((i+1 for i, (k, _) in enumerate(rows) if k == '權利人類別'), len(rows))
            rows[index:index] = sorted(dates, key=lambda row: 0 if row[0].startswith('登記日期') else 1)
        return rows

    def has_public_data(self):
        # Empty objects, missing values and identity-only responses are not data.
        for key, value in self.groups.get('公有土地', []):
            if any('['+code+']' in key for code in ('AA45','AA46','AA48','AA49')):
                continue
            if str(value).strip() not in ('', '（空白）', '（空物件）', '（無回傳項目）', '未提供'):
                return True
        return False

    def render_groups(self, *args):
        land = self.rows_for('土地資訊')
        self.populate(self.table, land)
        for title, table in [('地段測繪資訊', self.section_table), ('公有土地', self.public_table)]:
            self.populate(table, self.rows_for(title))
        if self.on_changed:
            self.on_changed()
