"""Fit geographic parcel bounds with margin and a whole-hundred scale."""
import math
from qgis.core import QgsCoordinateTransform, QgsCoordinateReferenceSystem, QgsProject, QgsRectangle, QgsPointXY


def valid_extent(values):
    try:
        if any(isinstance(v, bool) for v in values):
            return False
        x1, y1, x2, y2 = map(float, values)
        return (all(math.isfinite(v) for v in (x1, y1, x2, y2))
                and -180 <= x1 < x2 <= 180 and -90 <= y1 < y2 <= 90)
    except (ValueError, TypeError):
        return False


def union_extents(extents):
    extents = [tuple(map(float, extent)) for extent in extents if valid_extent(extent)]
    if not extents:
        return None
    return (min(e[0] for e in extents), min(e[1] for e in extents),
            max(e[2] for e in extents), max(e[3] for e in extents))


def fit_parcels(canvas, extents, centers=()):
    transform = QgsCoordinateTransform(QgsCoordinateReferenceSystem('EPSG:4326'),
        canvas.mapSettings().destinationCrs(), QgsProject.instance())
    projected = None
    for extent in extents:
        if valid_extent(extent):
            rect = transform.transformBoundingBox(QgsRectangle(*extent))
            if projected is None:
                projected = QgsRectangle(rect)
            else:
                projected.combineExtentWith(rect)
    for x, y in centers:
        point = transform.transform(QgsPointXY(x, y))
        if projected is None:
            projected = QgsRectangle(point, point)
        else:
            projected.combineExtentWith(point.x(), point.y())
    if projected is None:
        return None
    center = projected.center()
    if projected.width() > 0 or projected.height() > 0:
        # Account for canvas rotation before fitting the rectangular viewport.
        angle = math.radians(canvas.rotation())
        c, s = abs(math.cos(angle)), abs(math.sin(angle))
        width = (projected.width()*c + projected.height()*s)*1.15
        height = (projected.width()*s + projected.height()*c)*1.15
        canvas.setExtent(QgsRectangle(center.x()-width/2, center.y()-height/2,
                                      center.x()+width/2, center.y()+height/2))
    else:
        # A center-only response cannot determine parcel size. Keep current zoom.
        canvas.setCenter(center)
    scale = canvas.scale()
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError('無法計算地籍範圍的顯示比例尺。')
    scale = max(100, math.ceil(scale/100)*100)
    canvas.zoomScale(scale)
    canvas.refresh()
    return scale
