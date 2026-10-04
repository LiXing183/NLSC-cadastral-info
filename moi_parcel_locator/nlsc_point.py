"""NLSC map's coordinate lookup; parse JSONP as data, never execute it."""
import base64
import json
import math
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlencode
from qgis.PyQt.QtCore import pyqtSignal
from .network_client import NetworkClient
from . import list_cache

LOCATION = 'https://api.nlsc.gov.tw/MapSearch/LocationQuery'
TOWNS = 'https://api.nlsc.gov.tw/other/ListTown/'
PARCEL = 'https://landmaps.nlsc.gov.tw/S_Maps/qryTileMapIndex'


def location(body):
    city, separator, text = body.partition('@')
    if not separator or not re.fullmatch('[A-Z]', city.strip()):
        raise ValueError('NLSC 未辨識此位置的縣市。')
    match = re.search(r'行政區[:：]\s*([^<\r\n]+)', text)
    if not match:
        raise ValueError('NLSC 未提供行政區名稱。')
    return city.strip(), match[1].strip()


def town_identity(body, city, address):
    matches = []
    for node in ET.fromstring(body):
        code, name = node.findtext('towncode', '').strip(), node.findtext('townname', '').strip()
        # Match the city-name prefix as well as town, not a village-name suffix.
        if re.fullmatch(city + r'\d{2}', code) and name:
            match = re.match(r'^(.+?[市縣])' + re.escape(name), address)
            if match:
                matches.append((code, name, match[1]))
    if len(matches) != 1:
        raise ValueError('NLSC 行政區無法唯一對應，請重新點選。')
    code, name, city_name = matches[0]
    return dict(cityCode=city, townCode=code, cityName=city_name, townName=name)


def parcel_identity(body, city):
    source = body.strip()
    if source.startswith('nlscParcel('):
        match = re.fullmatch(r'nlscParcel\((.*)\)\s*;?', source, re.S)
        if not match:
            raise ValueError('NLSC JSONP 格式無效。')
        source = match[1]
    payload = json.loads(source)
    if not isinstance(payload, list):
        raise ValueError('NLSC 地號回應格式無效。')
    rows = [row for group in payload for row in (group if isinstance(group, list) else [group])
            if isinstance(row, dict) and row.get('landno')]
    identities = {(str(r.get('office', '')), str(r.get('sect', '')), str(r['landno'])) for r in rows}
    if len(identities) != 1:
        raise ValueError('此位置未回傳唯一宗地，請點選宗地內部。')
    row = rows[0]
    office, section, number = next(iter(identities))
    if not (re.fullmatch(city + r'[A-Za-z0-9]{1,7}', office)
            and re.fullmatch(r'\d{4}', section) and re.fullmatch(r'\d{8}', number)):
        raise ValueError('NLSC 宗地代碼無效或縣市不一致。')
    if str(row.get('extend') or '') not in ('', '0'):
        raise ValueError('此地段含延伸碼，目前 NLSC 屬性串接不支援。')
    try:
        name = base64.b64decode(row.get('sectStr', ''), validate=True).decode('utf-8')
    except (ValueError, UnicodeError):
        name = section
    main, sub = int(number[:4]), int(number[4:])
    return dict(office=office, sectNo=section, landNo=str(main) + ('-' + str(sub) if sub else ''), sectName=name)


class NlscPointLookup(NetworkClient):
    identified = pyqtSignal(object)
    BASE = ''
    REFERER = 'https://maps.nlsc.gov.tw/'
    ORIGIN = 'https://maps.nlsc.gov.tw'

    def shared_request_key(self, endpoint, fields):
        # Serialize only coordinate-to-county HTTP requests. Each point must
        # still receive its own response; no response is shared or cached here.
        return LOCATION if endpoint == LOCATION else None

    def request_interval(self, endpoint, fields):
        # Rapid LocationQuery calls return HTTP 200 with an empty body, even
        # when serialized. Pace this endpoint without delaying other services.
        return 1.6 if endpoint == LOCATION else 0.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.retry_timer.timeout.disconnect(self._retry_token)
        self.retry_timer.timeout.connect(self._retry_location)
        self.retry_timer.setInterval(1500)

    def _retry_location(self):
        if self.context is not None and self.reply is None:
            self._request(LOCATION, {'center': self.context['x'] + ',' + self.context['y']})

    def start_point(self, x, y):
        self.cancel()
        if not (math.isfinite(x) and math.isfinite(y) and -180 <= x <= 180 and -90 <= y <= 90):
            raise ValueError('點選座標無效。')
        self.context = dict(x=format(x, '.12f'), y=format(y, '.12f'))
        self.location_retries = 0
        self._request(LOCATION, {'center': self.context['x'] + ',' + self.context['y']})

    def _success(self, body):
        if self.endpoint == LOCATION:
            try:
                city, address = location(body)
            except ValueError:
                # This service can return HTTP 200 with no location while busy.
                # Retry briefly; do not guess a county or parcel from old inputs.
                if self.location_retries >= 3:
                    raise
                self.location_retries += 1
                self.progress.emit('NLSC 點位資料暫未取得，正在重試 %d/3…' % self.location_retries)
                self.retry_timer.start()
                return
            self.context.update(city=city, address=address)
            url = TOWNS + city
            cached = list_cache.get(url)
            if cached is not None:
                self._town_ready(cached)
            else:
                self._request(url, None)
        elif self.endpoint.startswith(TOWNS):
            self._town_ready(body)
            list_cache.put(TOWNS + self.context['city'], body.encode('utf-8'))
        else:
            data = dict(self.context['identity'])
            data.update(parcel_identity(body, self.context['city']))
            self.context = None
            self.identified.emit(data)

    def _town_ready(self, body):
        ctx = self.context
        ctx['identity'] = town_identity(body, ctx['city'], ctx['address'])
        params = dict(type='2', flag='1', city=ctx['city'], x=ctx['x'], y=ctx['y'], alpah='0.5f', callback='nlscParcel')
        self._request(PARCEL + '?' + urlencode(params), None)

    def _fail(self, status, body, reason):
        self.context = None
        self.timer.stop()
        self.retry_timer.stop()
        self.failed.emit('NLSC 點選查詢失敗：HTTP %s｜%s' % (status if status is not None else '無回應', reason))
