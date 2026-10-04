"""Merge georeferenced alpha masks at the finest source pixel spacing."""
import math
import numpy as np


def merge_masks(tiles, max_pixels=16000000):
    if not tiles:
        raise ValueError("沒有宗地影像")
    for ext, alpha in tiles:
        if alpha.ndim != 2 or not alpha.size:
            raise ValueError("宗地透明遮罩無效")
        if not (len(ext) == 4 and all(math.isfinite(v) for v in ext)
                and ext[0] < ext[2] and ext[1] < ext[3]):
            raise ValueError("宗地影像範圍無效")
    if len(tiles) == 1:
        (xmin, ymin, xmax, ymax), alpha = tiles[0]
        height, width = alpha.shape
        if width*height > max_pixels:
            raise ValueError("宗地影像超過像素上限，已停止填色以避免記憶體不足。")
        # Native single images need no coordinate grids or resampling. Return
        # independent pixels just as the multi-image path does.
        return alpha.copy(), (xmin, (xmax-xmin)/width, 0., ymax, 0., -(ymax-ymin)/height)
    xmin = min(ext[0] for ext, _ in tiles)
    ymin = min(ext[1] for ext, _ in tiles)
    xmax = max(ext[2] for ext, _ in tiles)
    ymax = max(ext[3] for ext, _ in tiles)
    dx = min((e[2]-e[0])/a.shape[1] for e, a in tiles)
    dy = min((e[3]-e[1])/a.shape[0] for e, a in tiles)
    width = max(1, math.ceil((xmax-xmin)/dx - 1e-7))
    height = max(1, math.ceil((ymax-ymin)/dy - 1e-7))
    if width*height > max_pixels:
        raise ValueError("宗地合併影像超過像素上限，已停止填色以避免記憶體不足。")
    result = np.zeros((height, width), dtype=np.uint8)
    xs = xmin + (np.arange(width)+0.5)*dx
    ys = ymax - (np.arange(height)+0.5)*dy
    for (left, bottom, right, top), alpha in tiles:
        cols = np.flatnonzero((xs >= left) & (xs < right))
        rows = np.flatnonzero((ys <= top) & (ys > bottom))
        if not len(cols) or not len(rows):
            continue
        sx = np.minimum(((xs[cols]-left)/(right-left)*alpha.shape[1]).astype(int), alpha.shape[1]-1)
        sy = np.minimum(((top-ys[rows])/(top-bottom)*alpha.shape[0]).astype(int), alpha.shape[0]-1)
        target = np.ix_(rows, cols)
        result[target] = np.maximum(result[target], alpha[np.ix_(sy, sx)])
    return result, (xmin, dx, 0.0, ymax, 0.0, -dy)
