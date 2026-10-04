"""NLSC DMAPS XYZ layer and alpha-preserving line/text coloring."""
from urllib.parse import urlsplit, unquote
from qgis.PyQt.QtGui import QColor
from qgis.core import QgsProject, QgsRasterLayer, QgsDataSourceUri

URL = 'https://landmaps.nlsc.gov.tw/S_Maps/wmts/DMAPS/default/EPSG:3857/{z}/{y}/{x}'
PARAMS = {'type':'xyz', 'url':URL, 'zmin':'0', 'zmax':'20', 'tilePixelRatio':'1',
          'http-header:referer':'https://maps.nlsc.gov.tw/',
          'http-header:Origin':'https://maps.nlsc.gov.tw'}


def matches(layer):
    if not isinstance(layer, QgsRasterLayer) or layer.providerType() != 'wms':
        return False
    uri = QgsDataSourceUri()
    uri.setEncodedUri(layer.source())
    actual, target = urlsplit(uri.param('url')), urlsplit(URL)
    return (uri.param('type').lower() == 'xyz' and actual.scheme.lower() == target.scheme
            and actual.netloc.lower() == target.netloc and unquote(actual.path).rstrip('/') == target.path)


class CadastralLayer:
    def __init__(self, iface, report):
        self.iface, self.report = iface, report
        self.color_changed = None

    def find(self):
        active = self.iface.activeLayer()
        if matches(active):
            return active
        return next((layer for layer in QgsProject.instance().mapLayers().values() if matches(layer)), None)

    def add(self):
        layer = self.find()
        if layer is None:
            uri = QgsDataSourceUri()
            for key, value in PARAMS.items():
                uri.setParam(key, value)
            layer = QgsRasterLayer(bytes(uri.encodedUri()).decode('utf-8'), 'NLSC地籍圖', 'wms')
            if not layer.isValid():
                self.report('NLSC地籍圖建立失敗。')
                return None
            self.apply_color(layer, QColor('#000000'))
            QgsProject.instance().addMapLayer(layer)
        else:
            uri = QgsDataSourceUri()
            uri.setEncodedUri(layer.source())
            if uri.param('tilePixelRatio') != '1':
                original_source = layer.source()
                color = QColor(layer.customProperty('moi/dmapsColor', '#000000'))
                opacity = layer.opacity()
                uri.setParam('tilePixelRatio', '1')
                layer.setDataSource(bytes(uri.encodedUri()).decode('utf-8'), layer.name(), 'wms')
                if not layer.isValid():
                    layer.setDataSource(original_source, layer.name(), 'wms')
                    self.apply_color(layer, color)
                    layer.setOpacity(opacity)
                    self.report('地籍圖 96 DPI 設定失敗，已恢復原來源。')
                    return None
                self.apply_color(layer, color)
                layer.setOpacity(opacity)
        node = QgsProject.instance().layerTreeRoot().findLayer(layer.id())
        if node is not None:
            node.setItemVisibilityChecked(True)
        self.iface.setActiveLayer(layer)
        self.configure_resampling(layer)
        layer.triggerRepaint()
        if self.color_changed is not None:
            self.color_changed(QColor(layer.customProperty('moi/dmapsColor', '#000000')))
        self.report('NLSC地籍圖已顯示。')
        return layer

    def set_color(self, color):
        layer = self.find()
        if layer is None:
            self.report('請先按「加入地籍圖」。')
            if self.color_changed is not None:
                self.color_changed(QColor('#000000'))
            return
        if not color.isValid():
            return
        self.apply_color(layer, color)

    def apply_color(self, layer, color):
        try:
            from qgis.core import QgsHueSaturationFilter
            brightness = layer.brightnessFilter()
            hue = layer.hueSaturationFilter()
            if brightness is None or hue is None:
                raise ValueError('此圖層缺少 QGIS 色彩濾鏡，保留原樣式。')
            # These native filters work on rendered ARGB, including single-band XYZ.
            # Contrast -100 makes RGB uniform while preserving every pixel's alpha.
            # Set its lightness, then colorize in the following hue/saturation filter.
            brightness.setContrast(-100)
            brightness.setGamma(1.0)
            brightness.setBrightness(round(color.lightnessF()*255-127.5))
            hue.setInvertColors(False)
            hue.setSaturation(0)
            modes = getattr(QgsHueSaturationFilter, 'GrayscaleMode', QgsHueSaturationFilter)
            hue.setGrayscaleMode(modes.GrayscaleOff)
            # QGIS reads HSV saturation from the chosen tint, but applies it as HSL.
            tint = QColor.fromHsv(max(0, color.hslHue()), color.hslSaturation(), 255)
            hue.setColorizeColor(tint)
            hue.setColorizeStrength(100)
            hue.setColorizeOn(True)
            layer.setCustomProperty('moi/dmapsColor', color.name())
            self.configure_resampling(layer)
            layer.triggerRepaint()
            if self.color_changed is not None:
                self.color_changed(color)
            self.report('地籍線與地號文字顏色已改為 ' + color.name())
        except Exception as exc:
            if self.color_changed is not None:
                self.color_changed(QColor(layer.customProperty('moi/dmapsColor', '#000000')))
            self.report('地籍圖改色未完成：' + str(exc))

    def configure_resampling(self, layer):
        from qgis.core import Qgis, QgsBilinearRasterResampler
        # Interpolate fine transparent linework instead of skipping pixels when
        # the map canvas does not align with the native tile pixel grid.
        layer.setResamplingStage(Qgis.RasterResamplingStage.ResampleFilter)
        resample = layer.resampleFilter()
        if resample is not None:
            # Avoid requesting a higher tile zoom and shrinking its labels.
            # This affects display sampling only, not source PNG dimensions.
            resample.setMaxOversampling(1.0)
            resample.setZoomedInResampler(QgsBilinearRasterResampler())
            resample.setZoomedOutResampler(QgsBilinearRasterResampler())
