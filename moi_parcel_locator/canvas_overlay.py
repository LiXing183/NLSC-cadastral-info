"""Temporary in-memory parcel mask, above map layers; never writes image files."""
import numpy as np
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QPointF, QRectF, Qt
from qgis.PyQt.QtGui import QImage, QColor, QPolygonF, QTransform
from qgis.core import QgsCoordinateTransform, QgsCoordinateReferenceSystem, QgsProject, QgsPointXY
from qgis.gui import QgsMapCanvasItem
from .parcel_mosaic import merge_masks
from .color_contrast import contrasting_text


def color_swatch(button, color):
    from qgis.PyQt.QtGui import QPalette
    color = color or QColor('#00ffff')
    foreground = contrasting_text(color)
    button.setText(color.name())
    button.setStyleSheet('')
    palette = button.palette()
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive,
                  QPalette.ColorGroup.Disabled):
        palette.setColor(group, QPalette.ColorRole.Button, color)
        palette.setColor(group, QPalette.ColorRole.ButtonText, QColor(foreground))
    button.setPalette(palette)
    button.update()


class MaskItem(QgsMapCanvasItem):
    def __init__(self, canvas, image, extent):
        self.canvas, self.image, self.extent = canvas, image, extent
        self.bounds = QRectF()
        self.warp = None
        super().__init__(canvas)
        self.setZValue(10000)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.updatePosition()

    def boundingRect(self):
        return self.bounds

    def updatePosition(self, *args):
        self.prepareGeometryChange()
        self.warp = None
        try:
            xmin, ymin, xmax, ymax = self.extent
            transform = QgsCoordinateTransform(QgsCoordinateReferenceSystem('EPSG:4326'), self.canvas.mapSettings().destinationCrs(), QgsProject.instance())
            points = [self.toCanvasCoordinates(transform.transform(QgsPointXY(x, y)))
                      for x, y in [(xmin, ymax), (xmax, ymax), (xmax, ymin), (xmin, ymin)]]
            left, top = min(p.x() for p in points), min(p.y() for p in points)
            self.setPos(left, top)
            target = QPolygonF([QPointF(p.x()-left, p.y()-top) for p in points])
            self.bounds = target.boundingRect().adjusted(-1, -1, 1, 1)
            width, height = self.image.width(), self.image.height()
            source = QPolygonF([QPointF(0, 0), QPointF(width, 0), QPointF(width, height), QPointF(0, height)])
            warp = QTransform()
            if QTransform.quadToQuad(source, target, warp):
                self.warp = warp
        except Exception:
            self.bounds = QRectF()
        self.update()

    def paint(self, painter, *args):
        if self.warp is not None:
            painter.save()
            painter.setTransform(self.warp, True)
            painter.drawImage(QPointF(0, 0), self.image)
            painter.restore()


class ParcelOverlay:
    def __init__(self, canvas):
        self.canvas = canvas
        self.item = None
        self.images, self.label = [], ''
        self.alpha = None
        self.color = QColor('#ff00ff')
        self.connections = []
        self.parts = []

    def clear(self):
        for part in self.parts:
            part.clear()
        self.parts = []
        for signal in self.connections:
            signal.disconnect(self.item.updatePosition)
        self.connections = []
        if self.item is not None:
            self.canvas.scene().removeItem(self.item)
            sip.delete(self.item)
            self.item = None
        self.images, self.label, self.alpha = [], '', None

    def show(self, images, label, color, opacity):
        # Preserve each source raster's dimensions and geographic extent.
        # Do not allocate a large mosaic spanning gaps between fragments.
        if len(images) > 1:
            self.clear()
            try:
                for tile in images:
                    part = ParcelOverlay(self.canvas)
                    self.parts.append(part)
                    part.show([tile], label, color, opacity)
                self.images, self.label = images, label
            except Exception:
                self.clear()
                raise
            return
        tiles = []
        for extent, png in images:
            image = QImage.fromData(png, 'PNG')
            if image.isNull() or not image.hasAlphaChannel():
                raise ValueError('宗地 PNG 無效或缺少透明遮罩。')
            image = image.convertToFormat(QImage.Format.Format_RGBA8888)
            buffer = image.constBits()
            buffer.setsize(image.sizeInBytes())
            pixels = np.frombuffer(buffer, dtype=np.uint8)
            alpha = pixels.reshape(image.height(), image.bytesPerLine())[:, :image.width()*4].reshape(image.height(), image.width(), 4)[:, :, 3].copy()
            tiles.append((extent, alpha))
        alpha, transform = merge_masks(tiles)
        # NLSC masks may have a built-in 50% opacity. Normalize the mask so
        # the UI opacity sets the parcel interior's actual opacity, retaining edges.
        peak = int(alpha.max())
        if peak and peak != 255:
            alpha = np.rint(alpha.astype(np.float32) * (255.0 / peak)).astype(np.uint8)
        self.clear()
        self.alpha, self.images, self.label = alpha, images, label
        height, width = alpha.shape
        x, dx, _, y, _, dy = transform
        self.item = MaskItem(self.canvas, self.colored_image(color), (x, y+height*dy, x+width*dx, y))
        for name in ('destinationCrsChanged', 'rotationChanged'):
            signal = getattr(self.canvas, name, None)
            if signal is not None:
                signal.connect(self.item.updatePosition)
                self.connections.append(signal)
        self.style(color, opacity)

    def colored_image(self, color):
        self.color = QColor(color) if color is not None else QColor('#ff00ff')
        height, width = self.alpha.shape
        rgba = np.empty((height, width, 4), dtype=np.uint8)
        rgba[:, :, :3] = [self.color.red(), self.color.green(), self.color.blue()]
        rgba[:, :, 3] = self.alpha
        return QImage(rgba.data, width, height, width*4, QImage.Format.Format_RGBA8888).copy()

    def style(self, color, opacity):
        for part in self.parts:
            part.style(color, opacity)
        if self.item is not None:
            target = QColor(color) if color is not None else QColor('#ff00ff')
            # Opacity is applied by the canvas item. Only an RGB change needs a
            # new image; retain native pixels when moving the opacity control.
            if self.color.rgb() != target.rgb():
                self.item.image = self.colored_image(target)
                self.item.update()
            self.opacity(opacity)

    def opacity(self, value):
        for part in self.parts:
            part.opacity(value)
        if self.item is not None:
            value = max(0., min(1., value))
            if self.item.opacity() != value:
                self.item.setOpacity(value)
                self.item.update()
