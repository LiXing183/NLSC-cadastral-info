"""Fit long raster edges with length-weighted orthogonal least squares."""
import numpy as np
from qgis.core import QgsGeometry, QgsPointXY


def fit_line(points):
    starts, ends = points[:-1], points[1:]
    deltas = ends - starts
    weights = np.linalg.norm(deltas, axis=1)
    total = weights.sum()
    if total <= 0:
        return None
    midpoints = (starts + ends) / 2
    center = (midpoints * weights[:, None]).sum(axis=0) / total
    offsets = midpoints - center
    # Integrate each segment instead of weighting densely sampled steps more.
    covariance = (np.einsum('n,ni,nj->ij', weights, offsets, offsets)
                  + np.einsum('n,ni,nj->ij', weights, deltas, deltas) / 12) / total
    direction = np.linalg.eigh(covariance)[1][:, -1]
    return center, direction


def fit_ring(original, simplified, tolerance):
    points = np.array([(p.x(), p.y()) for p in original[:-1]], dtype=float)
    lookup = {tuple(point): index for index, point in enumerate(points)}
    indices = [lookup.get((p.x(), p.y())) for p in simplified[:-1]]
    if len(indices) < 3 or any(index is None for index in indices):
        return simplified

    def section(start, end):
        stop = end if end > start else end + len(points)
        return points[np.arange(start, stop+1) % len(points)]

    # Join nearly collinear simplified sections only if their original raster
    # points also fit the combined line within the existing pixel tolerance.
    changed = True
    while changed and len(indices) > 3:
        changed = False
        for position, index in enumerate(indices):
            previous, following = indices[position-1], indices[(position+1) % len(indices)]
            before, after = points[index]-points[previous], points[following]-points[index]
            lengths = np.linalg.norm(before)*np.linalg.norm(after)
            if lengths == 0 or np.dot(before, after)/lengths < np.cos(np.deg2rad(5)):
                continue
            samples = section(previous, following)
            fitted = fit_line(samples)
            if fitted is None:
                continue
            center, direction = fitted
            normal = np.array([-direction[1], direction[0]])
            if np.max(np.abs((samples-center) @ normal)) <= tolerance:
                indices.pop(position)
                changed = True
                break

    lines = []
    for position, index in enumerate(indices):
        following = indices[(position+1) % len(indices)]
        samples = section(index, following)
        length = np.linalg.norm(np.diff(samples, axis=0), axis=1).sum()
        fitted = fit_line(samples) if len(samples) >= 4 and length >= 8*tolerance else None
        if fitted is None:
            direction = points[following]-points[index]
            fitted = points[index], direction/np.linalg.norm(direction)
        lines.append(fitted)
    vertices = []
    for position, index in enumerate(indices):
        first, second = lines[position-1], lines[position]
        matrix = np.column_stack((first[1], -second[1]))
        point = points[index]
        if abs(np.linalg.det(matrix)) > np.sin(np.deg2rad(5)):
            distance = np.linalg.solve(matrix, second[0]-first[0])[0]
            intersection = first[0] + distance*first[1]
            if np.linalg.norm(intersection-point) <= tolerance:
                point = intersection
        vertices.append(QgsPointXY(float(point[0]), float(point[1])))
    return vertices + [QgsPointXY(vertices[0])]


def fit_boundary(original, simplified, tolerance):
    original = QgsGeometry(original)
    simplified = QgsGeometry(simplified)
    original.convertToMultiType()
    simplified.convertToMultiType()
    raw_polygons, simple_polygons = original.asMultiPolygon(), simplified.asMultiPolygon()
    if len(raw_polygons) != len(simple_polygons):
        return simplified
    polygons = []
    for raw, simple in zip(raw_polygons, simple_polygons):
        if len(raw) != len(simple):
            return simplified
        polygons.append([fit_ring(raw_ring, simple_ring, tolerance)
                         for raw_ring, simple_ring in zip(raw, simple)])
    candidate = QgsGeometry.fromMultiPolygonXY(polygons)
    # Retain the proven topology-preserving result if fitting changes topology,
    # invalidates a ring, or exceeds the allowed boundary displacement.
    if (candidate.isEmpty() or not candidate.isGeosValid()
            or abs(candidate.area()-original.area()) > .02*original.area()
            or candidate.hausdorffDistance(original) > tolerance * 1.001):
        return simplified
    return candidate
