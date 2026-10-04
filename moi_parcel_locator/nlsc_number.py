"""Locate a parcel using NLSC's flag=2 metadata, without a token."""
import json
import math
import re
from html import unescape
from urllib.parse import urlencode
from .network_client import NetworkClient
from .nlsc_point import PARCEL, parcel_identity
from .nlsc_data import request_fields
from .parcel_view import valid_extent, union_extents


def parcel_location(body, requested):
    source = body.strip()
    match = re.fullmatch(r'nlscParcel\((.*)\)\s*;?', source, re.S)
    payload = json.loads(match[1] if match else source)
    if not payload or (isinstance(payload, list) and all(not group for group in payload)):
        raise ValueError('NLSC 未提供此地號的定位資料。')
    if isinstance(payload, list):
        for group in payload:
            for row in group if isinstance(group, list) else [group]:
                if isinstance(row, dict) and row.get('msg'):
                    message = ' '.join(re.sub(r'<[^>]*>', '', unescape(str(row['msg']))).split())[:240]
                    raise ValueError('NLSC 回覆：' + message)
    fields = request_fields(requested)
    actual = parcel_identity(json.dumps(payload), fields['city'])
    if actual['office'] != str(requested['office']) or request_fields(dict(actual, cityCode=fields['city'])) != fields:
        raise ValueError('NLSC 定位回應與輸入宗地不一致。')
    rows = [row for group in payload for row in (group if isinstance(group, list) else [group])
            if isinstance(row, dict) and row.get('landno')]
    centers = set()
    for row in rows:
        if isinstance(row.get('cx'), bool) or isinstance(row.get('cy'), bool):
            raise ValueError('NLSC 宗地中心座標無效。')
        x, y = float(row['cx']), float(row['cy'])
        if not (math.isfinite(x) and math.isfinite(y) and -180 <= x <= 180 and -90 <= y <= 90) or (x == 0 and y == 0):
            raise ValueError('NLSC 宗地中心座標無效。')
        centers.add((x, y))
    if len(centers) != 1:
        raise ValueError('NLSC 未回傳唯一宗地中心座標。')
    extents = [tuple(row.get(key) for key in ('lx', 'ly', 'rx', 'ry')) for row in rows]
    # Use bounds only when every fragment supplies a valid extent.
    extent = union_extents(extents) if all(valid_extent(e) for e in extents) else None
    return next(iter(centers)), extent


def parcel_center(body, requested):
    return parcel_location(body, requested)[0]


class NlscNumberLookup(NetworkClient):
    BASE = ''
    REFERER = 'https://maps.nlsc.gov.tw/'
    ORIGIN = 'https://maps.nlsc.gov.tw'

    def __init__(self, parent=None):
        super().__init__(parent)
        self.extent = None

    def start_parcel(self, data):
        self.cancel()
        self.extent = None
        fields = request_fields(data)
        self.context = dict(data)
        url = PARCEL + '?' + urlencode(dict(type='2', flag='2', office=data['office'],
            sect=fields['sect'], landno=fields['landno'], alpah='0.5f', callback='nlscParcel'))
        self._request(url, None)

    def _success(self, body):
        (x, y), self.extent = parcel_location(body, self.context)
        self.context = None
        self.located.emit(x, y)

    def _fail(self, status, body, reason):
        data, self.context = self.context or {}, None
        self.timer.stop()
        http = '' if status == 200 else 'HTTP %s｜' % (status if status is not None else '無回應')
        self.failed.emit('NLSC 地號定位失敗(%s/%s)：%s%s' %
            (data.get('sectNo', '?'), data.get('landNo', '?'), http, reason))
