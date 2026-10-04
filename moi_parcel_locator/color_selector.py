"""Native color dropdown with shared presets and an optional custom color."""
from qgis.PyQt.QtCore import QSize, pyqtSignal
from qgis.PyQt.QtGui import QColor, QIcon, QPalette, QPixmap, QPainter
from qgis.PyQt.QtWidgets import QComboBox, QColorDialog, QSizePolicy, QStyle, QStyleOptionComboBox, QStylePainter
from .color_contrast import contrasting_text


PRESETS = (
    ('Black', '#000000'), ('White', '#ffffff'), ('Magenta', '#ff00ff'),
    ('Red', '#ff0000'), ('Yellow', '#ffff00'), ('Green', '#00ff00'),
    ('Cyan', '#00ffff'), ('Blue', '#0000ff'),
)


class ColorSelector(QComboBox):
    color_selected = pyqtSignal(QColor)

    def __init__(self, color, title, parent=None):
        super().__init__(parent)
        self.title = title
        self.color = None
        self.foreground = QColor('#000000')
        self.setAccessibleName(title)
        self.setIconSize(QSize(16, 16))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        for name, value in PRESETS:
            self.addItem(self.swatch(value), name, value)
        self.addItem('Custom', None)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        initial = QColor(color)
        self.set_color(initial if initial.isValid() else QColor('#000000'))
        # Only user activation changes the map. Synchronizing an existing layer
        # or restoring a cancelled dialog must not emit another color change.
        self.activated.connect(self.select_color)

    def display_text(self):
        return self.color.name() if self.currentIndex() == len(PRESETS) and self.color else self.currentText()

    def sizeHint(self):
        # Retain the native icon, arrow and frame spacing, but only reserve
        # enough text width for the selected color, not the longest menu entry.
        size = super().sizeHint()
        metrics = self.fontMetrics()
        longest = max((metrics.boundingRect(self.itemText(i)).width() for i in range(self.count())), default=0)
        size.setWidth(size.width() - longest + metrics.boundingRect(self.display_text()).width())
        return size

    def minimumSizeHint(self):
        return self.sizeHint()

    def showPopup(self):
        # The expanded list still has room for every full preset/custom label.
        self.view().setMinimumWidth(super().sizeHint().width())
        super().showPopup()

    def set_color(self, color):
        color = QColor(color)
        if not color.isValid():
            return
        color = QColor(color.name())
        changed = self.color != color
        index = self.findData(color.name())
        index = index if index >= 0 else len(PRESETS)
        # Even an unchanged color must restore the index after Custom is cancelled.
        if self.currentIndex() != index:
            self.setCurrentIndex(index)
        if changed:
            self.color = color
            self.foreground = QColor(contrasting_text(color))
            self.setItemIcon(len(PRESETS), self.swatch(color))
            self.setToolTip(self.title + '：' + self.display_text())
            self.update()
        self.updateGeometry()

    @staticmethod
    def swatch(color):
        color = QColor(color)
        pixmap = QPixmap(16, 16)
        pixmap.fill(color)
        painter = QPainter(pixmap)
        # Keep Black and White swatches visible on the matching control background.
        painter.setPen(QColor(contrasting_text(color)))
        painter.drawRect(0, 0, 15, 15)
        painter.end()
        return QIcon(pixmap)

    def select_color(self, index):
        value = self.itemData(index)
        chosen = QColor(value) if value is not None else QColorDialog.getColor(self.color, self, self.title)
        if not chosen.isValid():
            self.set_color(self.color)
            return
        previous = self.color.name()
        self.set_color(chosen)
        if self.color.name() != previous:
            self.color_selected.emit(QColor(self.color))

    def paintEvent(self, event):
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
            for role in (QPalette.ColorRole.Button, QPalette.ColorRole.Base, QPalette.ColorRole.Window):
                option.palette.setColor(group, role, self.color)
            for role in (QPalette.ColorRole.ButtonText, QPalette.ColorRole.Text, QPalette.ColorRole.WindowText):
                option.palette.setColor(group, role, self.foreground)
        option.currentText = self.display_text()
        painter = QStylePainter(self)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option)
