"""Polygonize in-memory fill masks without writing intermediate files."""
import numpy as np
from pathlib import Path
from osgeo import gdal, ogr
from qgis.core import (QgsGeometry, QgsVectorLayer, QgsField, QgsFeature,
                       QgsCoordinateReferenceSystem, QgsCoordinateTransform,
                       QgsCoordinateTransformContext, QgsPointXY, Qgis, QgsMapLayer, QgsSpatialIndex, QgsWkbTypes)
from qgis.PyQt.QtCore import QMetaType
from .history_fields import HEADERS, export_cell
from .boundary_fit import fit_boundary

MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024


def polygonal_geometry(geometry):
    """Keep every polygon after repair, discarding only zero-area line/point remnants."""
    result = QgsGeometry(geometry)
    if result.isNull() or result.isEmpty():
        raise ValueError('填色範圍沒有有效面幾何。')
    valid = result.isGeosValid()
    if not valid:
        result = result.makeValid()
    if QgsWkbTypes.flatType(result.wkbType()) == Qgis.WkbType.GeometryCollection:
        polygons, pending = [], [result]
        while pending:
            part = pending.pop()
            kind = QgsWkbTypes.flatType(part.wkbType())
            if kind == Qgis.WkbType.GeometryCollection:
                pending.extend(reversed(part.asGeometryCollection()))
            elif kind in (Qgis.WkbType.Polygon, Qgis.WkbType.MultiPolygon) and not part.isEmpty():
                polygons.append(part)
        if not polygons:
            raise ValueError('填色範圍修復後只剩線或點，沒有可輸出的面範圍。')
        result = QgsGeometry.unaryUnion(polygons)
        valid = False
    kind = QgsWkbTypes.flatType(result.wkbType())
    if (kind not in (Qgis.WkbType.Polygon, Qgis.WkbType.MultiPolygon)
            or result.isEmpty() or result.area() <= 0
            or not result.convertToMultiType() or (not valid and not result.isGeosValid())):
        raise ValueError('填色範圍無法建立有效多邊形：' + QgsWkbTypes.displayString(result.wkbType()))
    return result


def mask_parts(overlay):
    for part in overlay.parts:
        yield from mask_parts(part)
    if overlay.item is not None and overlay.alpha is not None:
        yield overlay.item.extent, overlay.alpha


def snapshot(history):
    """Capture widgets and masks on the GUI thread; workers receive plain data."""
    sources, total = [], 0
    for row, entry in enumerate(history.entries):
        parts = list(mask_parts(entry['overlay']))
        if not parts:
            continue
        total += sum(alpha.nbytes for _, alpha in parts)
        if total > MAX_SNAPSHOT_BYTES:
            raise ValueError('填色遮罩超過 256 MiB，請減少勾選範圍後再生成，避免記憶體不足。')
        sources.append((row, entry, parts))
    records = []
    for row, entry, parts in sources:
        parts = [(extent, alpha.copy()) for extent, alpha in parts]
        values = [export_cell(history.table.item(row, col).text())
                  if history.table.item(row, col) else '' for col in range(1, len(HEADERS))]
        color, opacity = history.entry_style(entry)
        records.append(dict(parts=parts, values=values, color=color.name(), opacity=opacity))
    return records


def polygonize(extent, alpha):
    xmin, ymin, xmax, ymax = extent
    height, width = alpha.shape
    if not width or not height or xmax <= xmin or ymax <= ymin:
        raise ValueError('填色影像範圍無效。')
    peak = float(alpha.max())
    if peak <= 0:
        return []
    # Padding closes edge-touching contours. Use the alpha transition itself,
    # rather than expanding every faint pixel to a full pixel-sized polygon.
    mask = np.pad(alpha.astype(np.float32) / peak, 1)
    dx, dy = (xmax-xmin)/width, (ymax-ymin)/height
    raster = gdal.GetDriverByName('MEM').Create('', width+2, height+2, 1, gdal.GDT_Float32)
    raster.SetGeoTransform((xmin-dx, dx, 0, ymax+dy, 0, -dy))
    band = raster.GetRasterBand(1)
    band.WriteArray(mask)
    driver = ogr.GetDriverByName('MEM') or ogr.GetDriverByName('Memory')
    output = driver.CreateDataSource('')
    layer = output.CreateLayer('contours', geom_type=ogr.wkbLineString)
    if gdal.ContourGenerate(band, 0, 0, [0.5], 0, 0, layer, -1, -1) != 0:
        raise ValueError('填色次像素輪廓擷取失敗。')
    lines = []
    for feature in layer:
        geometry = QgsGeometry()
        geometry.fromWkb(bytes(feature.GetGeometryRef().ExportToWkb()))
        lines.append(geometry)
    if not lines:
        return []
    regions = QgsGeometry.polygonize(lines).asGeometryCollection()
    geometries = []
    for region in regions:
        point = region.pointOnSurface().asPoint()
        # Contour polygonization includes holes as faces; retain only the
        # high-alpha faces using bilinear sampling at an interior point.
        column, row = (point.x()-xmin)/dx+.5, (ymax-point.y())/dy+.5
        x0, y0 = int(np.floor(column)), int(np.floor(row))
        if not (0 <= x0 < width+1 and 0 <= y0 < height+1):
            continue
        tx, ty = column-x0, row-y0
        value = ((1-ty)*((1-tx)*mask[y0,x0]+tx*mask[y0,x0+1])
                 + ty*((1-tx)*mask[y0+1,x0]+tx*mask[y0+1,x0+1]))
        if value >= .5:
            geometries.append(region)
    if not geometries:
        raise ValueError('填色次像素輪廓沒有有效面範圍。')
    return geometries


def overlapping_pairs(bounds):
    """Return bounding-box neighbors in the original, deterministic order."""
    index = QgsSpatialIndex()
    for number, rect in enumerate(bounds):
        feature = QgsFeature(number)
        feature.setGeometry(QgsGeometry.fromRect(rect))
        index.addFeature(feature)
    for first, rect in enumerate(bounds):
        for second in sorted(index.intersects(rect)):
            if second > first:
                yield first, second


def join_seams(geometry, parcels, pixel):
    """Add closing patches only where two different parcels are nearby."""
    if len(parcels) < 2:
        return QgsGeometry(geometry)
    distance = 1.25 * pixel
    expanded = [part.buffer(distance,4,Qgis.EndCapStyle.Flat,Qgis.JoinStyle.Miter,2) for part in parcels]
    neighborhoods = []
    for first, second in overlapping_pairs([part.boundingBox() for part in expanded]):
        nearby = expanded[first].intersection(expanded[second])
        if not nearby.isEmpty():
            neighborhoods.append(nearby)
    if not neighborhoods:
        return QgsGeometry(geometry)
    closed = geometry.buffer(distance,4,Qgis.EndCapStyle.Flat,Qgis.JoinStyle.Miter,2)
    closed = closed.buffer(-distance,4,Qgis.EndCapStyle.Flat,Qgis.JoinStyle.Miter,2)
    patches = closed.difference(geometry).intersection(QgsGeometry.unaryUnion(neighborhoods))
    return geometry.combine(patches) if not patches.isEmpty() else QgsGeometry(geometry)


def repair_cracks(geometry, pixel):
    """Fill long, raster-width cracks bounded by the original on both sides.

    Unlike global closing, only accepted additive patches are retained; the
    exterior is neither eroded nor replaced. Real gaps wider than four source
    pixels and broad corner notches fail the width/elongation/support tests.
    """
    distance = 2 * pixel
    closed = geometry.buffer(distance,4,Qgis.EndCapStyle.Flat,Qgis.JoinStyle.Miter,2)
    closed = closed.buffer(-distance,4,Qgis.EndCapStyle.Flat,Qgis.JoinStyle.Miter,2)
    missing = closed.difference(geometry)
    if missing.isEmpty():
        return QgsGeometry(geometry)
    missing.convertToMultiType()
    original = QgsGeometry(geometry)
    original.convertToMultiType()
    boundary = QgsGeometry.fromMultiPolylineXY([ring for polygon in original.asMultiPolygon() for ring in polygon])
    support_band = boundary.buffer(.02*pixel,2)
    accepted = []
    for patch in missing.asGeometryCollection():
        perimeter, area = patch.length(), patch.area()
        if perimeter <= 0 or area <= 0:
            continue
        width = 2*area/perimeter
        if perimeter/2 < 8*width or perimeter/2 < 8*pixel:
            continue
        # Reject any patch with a local interior wider than the gap limit.
        if not patch.buffer(-distance,4).isEmpty():
            continue
        patch_lines = QgsGeometry.fromMultiPolylineXY(patch.asPolygon())
        if patch_lines.intersection(support_band).length()/perimeter < .85:
            continue
        accepted.append(patch)
    return geometry.combine(QgsGeometry.unaryUnion(accepted)) if accepted else QgsGeometry(geometry)


def remove_pixel_fragments(geometry, pixel, protected=()):
    """Remove only tiny nearby islands relative to a much larger component.

    Source parcel anchors protect independent small parcels in the final
    fused result. A single-component parcel is always retained.
    """
    multi = QgsGeometry(geometry)
    multi.convertToMultiType()
    parts = sorted(multi.asGeometryCollection(),key=lambda part:part.area(),reverse=True)
    if len(parts) < 2:
        return multi
    largest = parts[0].area()
    kept = []
    for part in parts:
        tiny = part.area() <= 4*pixel*pixel and part.area() < .001*largest
        anchored = any(part.distance(anchor) <= 1.1*pixel for anchor in protected) if tiny else False
        if tiny and not anchored and any(part.distance(main) <= 3*pixel for main in kept):
            continue
        kept.append(part)
    return QgsGeometry.unaryUnion(kept)


def clean_geometry(geometry, pixel, parcels=None, seam_pixel=None):
    """Repair neighboring parcel seams and fit the subpixel boundary."""
    if parcels is None:
        parts = QgsGeometry(geometry)
        parts.convertToMultiType()
        parcels = parts.asGeometryCollection()
    cleaned = join_seams(geometry, parcels, pixel)
    cleaned = repair_cracks(cleaned, seam_pixel or pixel)
    cleaned = cleaned.removeInteriorRings(4 * pixel * pixel)
    source = ogr.CreateGeometryFromWkb(bytes(cleaned.asWkb()))
    simplified = source.SimplifyPreserveTopology(1.1 * pixel)
    if simplified is None or simplified.IsEmpty():
        raise ValueError('填色邊界平滑失敗。')
    result = QgsGeometry()
    result.fromWkb(bytes(simplified.ExportToWkb()))
    if not result.isGeosValid():
        raise ValueError('填色邊界修整後無效。')
    # A pixel-based tolerance can be large relative to tiny parcels. Do not
    # accept a valid but heavily shrunken triangle in place of a small polygon.
    if abs(result.area()-cleaned.area()) > .02*cleaned.area():
        result = QgsGeometry(cleaned)
    fitted = fit_boundary(cleaned, result, 1.1 * pixel)
    # Check again after fitting so newly formed thin corner slivers do not
    # survive merely because the geometry remains topologically valid.
    repaired = repair_cracks(fitted, seam_pixel or pixel)
    anchors = []
    for parcel in parcels:
        multi = QgsGeometry(parcel)
        multi.convertToMultiType()
        anchors.extend(part.pointOnSurface() for part in multi.asGeometryCollection())
    return remove_pixel_fragments(repaired, seam_pixel or pixel, anchors)


def convert(task, records, style=None):
    geometries = []
    parcels = []
    # Use an independent transform context in the worker, not the GUI project.
    source_crs = QgsCoordinateReferenceSystem('EPSG:4326')
    metric_crs = QgsCoordinateReferenceSystem('EPSG:3857')
    context = QgsCoordinateTransformContext()
    forward = QgsCoordinateTransform(source_crs, metric_crs, context)
    backward = QgsCoordinateTransform(metric_crs, source_crs, context)
    pixel_sizes = []
    for index, record in enumerate(records):
        parcel_parts = []
        parcel_pixels = []
        if task.isCanceled():
            return None
        for extent, alpha in record['parts']:
            if task.isCanceled():
                return None
            xmin, ymin, xmax, ymax = extent
            height, width = alpha.shape
            # NLSC reports geographic corner coordinates for a raster whose
            # pixels are uniform in Web Mercator. Do not distribute its rows
            # uniformly in latitude: independently-sized tiles then disagree
            # along the same parcel boundary and create numerical cracks.
            lower = forward.transform(QgsPointXY(xmin,ymin))
            upper = forward.transform(QgsPointXY(xmax,ymax))
            native_extent = (lower.x(),lower.y(),upper.x(),upper.y())
            parts = polygonize(native_extent,alpha)
            pixel = max((upper.x()-lower.x())/width,(upper.y()-lower.y())/height)
            pixel_sizes.append(pixel)
            parcel_pixels.append(pixel)
            parcel_parts.extend(parts)
        if parcel_parts:
            parcel = remove_pixel_fragments(QgsGeometry.unaryUnion(parcel_parts),max(parcel_pixels))
            parcels.append(parcel)
            geometries.append(parcel)
        task.setProgress(80*(index+1)/len(records))
    if task.isCanceled():
        return None
    if not geometries:
        raise ValueError('填色影像沒有可轉換的範圍。')
    geometry = QgsGeometry.unaryUnion(geometries)
    if geometry.isNull() or geometry.isEmpty():
        raise ValueError('填色範圍合併失敗。')
    geometry = polygonal_geometry(geometry)
    if task.isCanceled():
        return None
    # Mixed-resolution masks use the finest source pixel to protect detail.
    geometry = clean_geometry(geometry, min(pixel_sizes), parcels, max(pixel_sizes))
    geometry = polygonal_geometry(geometry)
    geometry.transform(backward)
    geometry = polygonal_geometry(geometry)
    if task.isCanceled():
        return None
    values = ['；'.join(dict.fromkeys(record['values'][column] for record in records
                                    if record['values'][column]))
              for column in range(len(HEADERS)-1)]
    color, opacity = style or (records[0]['color'], records[0]['opacity'])
    task.setProgress(100)
    return [dict(wkb=bytes(geometry.asWkb()), values=values, color=color,
                 opacity=opacity, count=len(records))]


def create_layer(results, crs, project):
    layer = QgsVectorLayer('MultiPolygon', '填色範圍', 'memory')
    layer.setCrs(crs)
    provider = layer.dataProvider()
    fields = [QgsField(name, QMetaType.Type.QString) for name in HEADERS[1:]]
    fields += [QgsField('宗地筆數', QMetaType.Type.Int),
               QgsField('填色顏色', QMetaType.Type.QString),
               QgsField('不透明度', QMetaType.Type.Double)]
    if not provider.addAttributes(fields):
        raise ValueError('無法建立向量圖層欄位。')
    layer.updateFields()
    transform = QgsCoordinateTransform(QgsCoordinateReferenceSystem('EPSG:4326'), crs, project)
    features = []
    for result in results:
        geometry = QgsGeometry()
        geometry.fromWkb(result['wkb'])
        geometry = polygonal_geometry(geometry)
        geometry.transform(transform)
        geometry = polygonal_geometry(geometry)
        feature = QgsFeature(layer.fields())
        feature.setGeometry(geometry)
        feature.setAttributes(result['values'] + [result['count'], result['color'], result['opacity']*100])
        features.append(feature)
    if not provider.addFeatures(features)[0]:
        details = '；'.join(provider.errors())
        raise ValueError('無法寫入向量圖徵。' + (' QGIS：' + details if details else ''))
    message, success = layer.loadNamedStyle(str(Path(__file__).with_name('scope.qml')),
                                            categories=QgsMapLayer.StyleCategory.Symbology)
    if not success:
        raise ValueError('無法套用預設向量樣式：' + message)
    layer.setAbstract('由 NLSC 填色影像透明度的 50% 等值線擷取次像素輪廓。融合後補合相鄰宗地細縫，並在擬合前後依窄長形狀及兩側邊界支撐修補最多約 4 像素寬的裂縫。小孔洞移除與 1.1 像素長直邊擬合保留。混合解析度時擬合採最細像素，裂縫辨識採最粗像素；可能填補同樣窄長的真實空隙。邊界精度受原始影像解析度限制。資料來源：NLSC國土測繪圖資服務雲。')
    layer.updateExtents()
    return layer

