"""Session history with independent, temporary parcel highlights."""
from datetime import datetime
from qgis.PyQt.QtCore import Qt, QSize, QItemSelectionModel, pyqtSignal
from qgis.PyQt.QtGui import QColor, QBrush
from qgis.PyQt.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidgetItem, QAbstractItemView, QMenu, QColorDialog
from .canvas_overlay import ParcelOverlay
from .history_fields import HEADERS, COPY_COLUMNS, parcel_sort_key, history_attributes, export_cell
from .color_selector import ColorSelector, PRESETS
from .history_table import HistoryTable, HistoryDelegate, ERROR_ROLE, RECORD_KEY_ROLE


class QueryHistory(QWidget):
    contents_changed = pyqtSignal()
    def __init__(self, canvas, style, parent=None):
        super().__init__(parent)
        self.canvas, self.style_values = canvas, style
        self.entries = []
        self.on_remove = None
        self.on_retry = None
        self.color_menu = QMenu(self)
        layout = QVBoxLayout(self)
        self.hint = QLabel('雙擊記錄查看結果；勾選方塊切換填色；點色塊可調整顏色，Ctrl／Shift 可多選。不透明度統一使用主視窗設定。')
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.table = HistoryTable(0, len(HEADERS))
        self.table.setItemDelegate(HistoryDelegate(self.table))
        self.table.setIconSize(QSize(16, 16))
        self.table.color_clicked.connect(self.configure_clicked_fill)
        self.table.setWordWrap(False)
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 65)
        self.table.cellChanged.connect(self.changed)
        layout.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        select = QPushButton('全選/取消全選')
        select.clicked.connect(self.toggle_selection)
        buttons.addWidget(select)
        fill = QPushButton('填色/取消填色')
        fill.clicked.connect(self.toggle_selected_fills)
        buttons.addWidget(fill)
        clear_history = QPushButton('清除選取記錄')
        clear_history.clicked.connect(self.remove_selected)
        buttons.addWidget(clear_history)
        layout.addLayout(buttons)

    def incomplete_count(self):
        return sum(entry.get('state') != '完成' for entry in self.entries)

    def summary(self):
        count = self.incomplete_count()
        return '部分記錄尚未完成查詢：%d / %d 筆未完成，已置頂顯示。' % (count, len(self.entries)) if count else ''

    def sort_key(self, entry):
        color = self.entry_style(entry)[0].name()
        colors = [value for _, value in PRESETS]
        group = colors.index(color) if color in colors else len(colors)
        return (entry.get('state') == '完成', group, color, parcel_sort_key(entry['data']))

    def sort_rows(self):
        order = sorted(range(len(self.entries)), key=lambda row: self.sort_key(self.entries[row]))
        if order == list(range(len(self.entries))):
            return
        selected = {self.entries[row]['key'] for row in self.selected_rows()}
        current_row, current_column = self.table.currentRow(), self.table.currentColumn()
        current_key = self.entries[current_row]['key'] if 0 <= current_row < len(self.entries) else None
        scroll = self.table.verticalScrollBar().value()
        blocked = self.table.blockSignals(True)
        self.table.setUpdatesEnabled(False)
        try:
            items = [[self.table.takeItem(row, column) for column in range(len(HEADERS))]
                     for row in range(len(self.entries))]
            self.entries = [self.entries[row] for row in order]
            self.table.clearSelection()
            for row, old_row in enumerate(order):
                for column, item in enumerate(items[old_row]):
                    if item is not None:
                        self.table.setItem(row, column, item)
                if self.entries[row]['key'] in selected:
                    self.table.selectionModel().select(self.table.model().index(row, 0),
                        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
                if self.entries[row]['key'] == current_key:
                    self.table.setCurrentCell(row, current_column, QItemSelectionModel.SelectionFlag.NoUpdate)
            self.table.verticalScrollBar().setValue(scroll)
        finally:
            self.table.setUpdatesEnabled(True)
            self.table.blockSignals(blocked)

    def update_status(self, number, state, errors):
        row = next((i for i, entry in enumerate(self.entries) if entry['data'].get('_query_id') == number), None)
        if row is None:
            return
        entry = self.entries[row]
        errors = tuple(errors)
        if entry.get('status_signature') == (state, errors):
            return
        was_complete = entry.get('state') == '完成'
        entry['state'] = state
        entry['failed'] = bool(errors)
        entry['status_signature'] = (state, errors)
        blocked = self.table.blockSignals(True)
        try:
            self.set_text(row, len(HEADERS)-1, state)
            self.table.item(row, len(HEADERS)-1).setToolTip('雙擊此列查看結果。\n' + '\n'.join(errors))
            for column in range(len(HEADERS)):
                item = self.table.item(row, column)
                if item is not None:
                    self.apply_text_color(item, entry['failed'])
        finally:
            self.table.blockSignals(blocked)
        # Progress text is not a sort key. Only crossing the completed boundary
        # can change this record's position (color changes sort separately).
        if was_complete != (state == '完成'):
            self.sort_rows()
        self.contents_changed.emit()

    @staticmethod
    def apply_text_color(item, failed):
        if item.data(ERROR_ROLE) == failed:
            return
        item.setData(ERROR_ROLE, failed)
        item.setForeground(QBrush(QColor('#ff0000')) if failed else QBrush())

    def retry_failed(self):
        if self.on_retry is not None:
            rows = self.selected_rows() or range(len(self.entries))
            self.on_retry([self.entries[row]['data'].get('_query_id') for row in rows])

    def entry_style(self, entry):
        color, opacity = self.style_values()
        override = entry.get('style_override')
        return QColor(override['color'] if override is not None else color), opacity

    def configure_clicked_fill(self, key):
        row = next((i for i, entry in enumerate(self.entries) if entry['key'] == key), None)
        if row is None:
            return
        if row not in self.selected_rows():
            self.table.clearSelection()
            self.table.selectRow(row)
        self.configure_selected_fill(row)

    def configure_selected_fill(self, anchor_row=None):
        rows = self.selected_rows()
        if not rows:
            return
        # Capture identities: a background response can reorder rows while open.
        entries = [self.entries[row] for row in rows]
        keys = tuple(entry['key'] for entry in entries)
        entry = entries[0]
        color, _ = self.entry_style(entry)
        menu = self.color_menu
        menu.clear()
        follows = all(entry.get('style_override') is None for entry in entries)
        colors = {self.entry_style(entry)[0].name() for entry in entries}
        for name, value in PRESETS:
            action = menu.addAction(ColorSelector.swatch(value), name)
            action.setData(value)
            action.setCheckable(True)
            action.setChecked(not follows and colors == {value})
            action.triggered.connect(lambda checked=False, v=value: self.set_entry_style(keys, dict(color=v)))
        custom = menu.addAction('Custom')
        custom.triggered.connect(lambda: self.choose_custom_color(keys, color))
        menu.addSeparator()
        common = menu.addAction('預設填色')
        common.setCheckable(True)
        common.setChecked(follows)
        common.triggered.connect(lambda: self.set_entry_style(keys, None))
        row = rows[0] if anchor_row is None else anchor_row
        rect = self.table.itemDelegate().swatch_rect(self.table.model().index(row, 0))
        menu.popup(self.table.viewport().mapToGlobal(rect.bottomLeft()))

    def choose_custom_color(self, keys, initial):
        color = QColorDialog.getColor(initial, self, '自訂填色顏色')
        if color.isValid():
            self.set_entry_style(keys, dict(color=color.name()))

    def set_entry_style(self, keys, style):
        keys = set(keys)
        for row, entry in enumerate(self.entries):
            if entry['key'] in keys:
                entry['style_override'] = dict(color=QColor(style['color']).name()) if style is not None else None
                entry['overlay'].style(*self.entry_style(entry))
                self.update_style_badge(row)
        self.sort_rows()

    def update_style_badge(self, row, status=None):
        entry = self.entries[row]
        if status is not None:
            entry['fill_status'] = status
        color, opacity = self.entry_style(entry)
        blocked = self.table.blockSignals(True)
        try:
            item = self.table.item(row, 0)
            if entry.get('badge_color') != color.name():
                item.setIcon(ColorSelector.swatch(color))
                entry['badge_color'] = color.name()
            item.setToolTip('%s\n%s；不透明度 %d%%\n%s' % (
                entry.get('fill_status', ''), color.name(), round(opacity*100),
                '獨立顏色；點色塊調整' if entry.get('style_override') is not None else '共用顏色；點色塊調整'))
        finally:
            self.table.blockSignals(blocked)

    def set_text(self, row, column, text):
        text = str(text)
        item = self.table.item(row, column)
        if item is None:
            item = QTableWidgetItem(text)
            self.table.setItem(row, column, item)
        elif item.text() != text:
            item.setText(text)
        if item.toolTip() != text:
            item.setToolTip(text)
        self.apply_text_color(item, self.entries[row].get('failed', False))

    def record(self, data, images, checked):
        key = tuple(str(data[k]) for k in ('office', 'sectNo', 'landNo'))
        if '_query_id' in data:
            key += (str(data['_query_id']),)
        row = next((i for i, entry in enumerate(self.entries) if entry['key'] == key), len(self.entries))
        if row == len(self.entries):
            self.entries.insert(row, dict(key=key, data=dict(data), overlay=ParcelOverlay(self.canvas),
                                         state='查詢中…', time=datetime.now().strftime('%H:%M:%S')))
            self.table.insertRow(row)
        entry = self.entries[row]
        entry['overlay'].clear()
        entry.pop('badge_color', None)
        entry.pop('status_signature', None)
        entry.update(data=dict(data), images=images)
        self.table.blockSignals(True)
        try:
            check = QTableWidgetItem()
            check.setData(RECORD_KEY_ROLE, key)
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
            if not images:
                check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            check.setCheckState(Qt.CheckState.Checked if checked and images else Qt.CheckState.Unchecked)
            self.table.setItem(row, 0, check)
            self.apply_text_color(check, entry.get('failed', False))
            values = [data.get('cityName') or data.get('cityCode', ''),
                      data.get('townName') or data.get('townCode', ''),
                      data['sectNo'], data.get('sectName', ''), data['landNo']]
            for column, text in enumerate(values, 1):
                self.set_text(row, column, text)
            self.set_text(row, 12, entry['time'])
            self.set_text(row, 13, entry['state'])
        finally:
            self.table.blockSignals(False)
        self.changed(row, 0)
        self.sort_rows()
        self.contents_changed.emit()

    def update_attributes(self, number, groups):
        row = next((i for i, entry in enumerate(self.entries)
                    if entry['data'].get('_query_id') == number), None)
        if row is None:
            return
        source = (tuple(groups.get('土地資訊', ())), tuple(groups.get('權利類別原始資料', ())))
        entry = self.entries[row]
        if entry.get('attribute_source') == source:
            return
        self.table.blockSignals(True)
        try:
            for column, value in enumerate(history_attributes(groups), 6):
                self.set_text(row, column, value)
            entry['attribute_source'] = source
        finally:
            self.table.blockSignals(False)

    def copy_rows(self):
        rows = self.selected_rows() or list(range(len(self.entries)))
        return ([HEADERS[column] for column in COPY_COLUMNS],
                [[export_cell(self.table.item(row, column).text()) if self.table.item(row, column) else ''
                  for column in COPY_COLUMNS] for row in rows])

    def enable_fill(self, number):
        for row, entry in enumerate(self.entries):
            if entry['data'].get('_query_id') == number and entry['images']:
                self.table.blockSignals(True)
                try:
                    self.table.item(row, 0).setCheckState(Qt.CheckState.Checked)
                finally:
                    self.table.blockSignals(False)
                self.changed(row, 0)
                return

    def changed(self, row, column):
        if column != 0 or row >= len(self.entries):
            return
        entry = self.entries[row]
        checked = self.table.item(row, 0).checkState() == Qt.CheckState.Checked
        entry['overlay'].clear()
        status = '未勾選' if entry['images'] else '未取得填色影像'
        if checked and entry['images']:
            try:
                color, opacity = self.entry_style(entry)
                entry['overlay'].show(entry['images'], '/'.join(entry['key']), color, opacity)
                status = '顯示中'
            except Exception as exc:
                entry['overlay'].clear()
                self.table.blockSignals(True)
                self.table.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
                self.table.blockSignals(False)
                status = '填色失敗：' + str(exc)
        self.update_style_badge(row, status)

    def clear_fills(self):
        self.table.blockSignals(True)
        try:
            for row, entry in enumerate(self.entries):
                entry['overlay'].clear()
                self.table.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
                self.update_style_badge(row, '未勾選' if entry['images'] else '未取得填色影像')
        finally:
            self.table.blockSignals(False)

    def restyle(self):
        for row, entry in enumerate(self.entries):
            entry['overlay'].style(*self.entry_style(entry))
            self.update_style_badge(row)
        self.sort_rows()

    def clear_history(self):
        if self.on_remove is not None:
            self.on_remove([entry['data'].get('_query_id') for entry in self.entries])
        self.clear_fills()
        self.entries.clear()
        self.table.setRowCount(0)
        self.contents_changed.emit()

    def selected_rows(self):
        return sorted(index.row() for index in self.table.selectionModel().selectedRows())

    def toggle_selection(self):
        if self.entries and len(self.selected_rows()) == len(self.entries):
            self.table.clearSelection()
        else:
            self.table.selectAll()

    def toggle_selected_fills(self):
        rows = [row for row in self.selected_rows() if self.entries[row]['images']]
        if not rows:
            return
        all_checked = all(self.table.item(row, 0).checkState() == Qt.CheckState.Checked for row in rows)
        state = Qt.CheckState.Unchecked if all_checked else Qt.CheckState.Checked
        for row in rows:
            self.table.item(row, 0).setCheckState(state)

    def remove_selected(self):
        rows = self.selected_rows()
        if self.on_remove is not None:
            self.on_remove([self.entries[row]['data'].get('_query_id') for row in rows])
        self.table.blockSignals(True)
        try:
            for row in reversed(rows):
                self.entries[row]['overlay'].clear()
                self.entries.pop(row)
                self.table.removeRow(row)
        finally:
            self.table.blockSignals(False)
        self.contents_changed.emit()
