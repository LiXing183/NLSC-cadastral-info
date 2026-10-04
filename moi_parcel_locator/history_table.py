"""History cells keep error text readable and expose the color swatch as a target."""
from qgis.PyQt.QtCore import Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QPalette
from qgis.PyQt.QtWidgets import QApplication, QTableWidget, QStyledItemDelegate, QStyleOptionViewItem, QStyle


ERROR_ROLE = int(Qt.ItemDataRole.UserRole) + 1
RECORD_KEY_ROLE = int(Qt.ItemDataRole.UserRole) + 2


class HistoryDelegate(QStyledItemDelegate):
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        if index.data(ERROR_ROLE):
            for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
                option.palette.setColor(group, QPalette.ColorRole.HighlightedText, QColor('#ff0000'))

    def swatch_rect(self, index):
        table = self.parent()
        option = QStyleOptionViewItem()
        option.decorationSize = table.iconSize()
        self.initStyleOption(option, index)
        option.rect = table.visualRect(index)
        return table.style().subElementRect(QStyle.SubElement.SE_ItemViewItemDecoration, option, table)


class HistoryTable(QTableWidget):
    color_clicked = pyqtSignal(object)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._color_press = None
        self._color_pending = None
        self._color_timer = QTimer(self)
        self._color_timer.setSingleShot(True)
        self._color_timer.timeout.connect(self._open_color)

    def _cancel_color(self):
        self._color_timer.stop()
        self._color_press = self._color_pending = None

    def _open_color(self):
        key, self._color_pending = self._color_pending, None
        if key is not None and self.isVisible() and self.isEnabled():
            self.color_clicked.emit(key)

    def color_hit(self, event):
        index = self.indexAt(event.pos())
        return (event.button() == Qt.MouseButton.LeftButton and index.isValid() and index.column() == 0
                and self.itemDelegate().swatch_rect(index).contains(event.pos()))

    def mousePressEvent(self, event):
        self._cancel_color()
        if self.color_hit(event):
            # Preserve a multi-selection before the standard table press clears it.
            index = self.indexAt(event.pos())
            self._color_press = (index.data(RECORD_KEY_ROLE), event.pos(),
                                 self.itemDelegate().swatch_rect(index))
            self._color_dragged = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._color_press is not None:
            if (event.pos() - self._color_press[1]).manhattanLength() >= QApplication.startDragDistance():
                self._color_dragged = True
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._color_press is not None:
            key, _, rect = self._color_press
            self._color_press = None
            if (event.button() == Qt.MouseButton.LeftButton and not self._color_dragged
                    and rect.contains(event.pos())):
                # Finish the native release before the popup takes the mouse grab.
                # Keep the record key even if a response sorts/replaces its cells.
                self._color_pending = key
                self._color_timer.start(0)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self.color_hit(event):
            # Qt can classify a quick repeat click as a double-click instead of
            # sending another press. It must still arm the following release.
            self.mousePressEvent(event)
            return
        super().mouseDoubleClickEvent(event)

    def hideEvent(self, event):
        self._cancel_color()
        super().hideEvent(event)
