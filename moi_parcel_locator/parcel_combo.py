"""Efficient administrative dropdown sizing with native Qt popup effects."""
from qgis.PyQt.QtWidgets import QComboBox


class ParcelComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.view().setUniformItemSizes(True)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(12)
