"""NLSC map building details using its public page token and session."""
import json
import re
from html.parser import HTMLParser
from qgis.PyQt.QtCore import pyqtSignal
from .network_client import NetworkClient

HOME = 'https://maps.nlsc.gov.tw/T09/mapshow.action?In_type=web'
DETAIL = 'https://maps.nlsc.gov.tw/T09/pro/Build_detail.action'


class PageToken(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag.lower() == 'input' and attrs.get('id') == 'magicToken' and attrs.get('value'):
            self.values.add(attrs['value'])


def building_fields(data):
    city = str(data.get('cityCode') or data['office'][0]).upper()
    section = str(data['sectNo'])
    number = str(data.get('nlscNumber') or data['buildNo'])
    if not re.fullmatch(r'\d{8}', number):
        if not re.fullmatch(r'\d{1,5}(?:-\d{1,3})?', number):
            raise ValueError('建號格式無效。')
        parts = number.split('-')
        number = parts[0].zfill(5) + (parts[1] if len(parts) == 2 else '0').zfill(3)
    if not re.fullmatch('[A-Z]', city) or not re.fullmatch(r'\d{4}', section):
        raise ValueError('建物縣市或地段代碼無效。')
    return dict(city=city, sect=section, buildno=number)


def building_rows(body, requested):
    response = json.loads(body)
    if not isinstance(response, dict) or response.get('errorMessage'):
        raise ValueError('NLSC 建物服務忙碌或拒絕查詢，請稍後重試。')
    value = response.get('value')
    if not isinstance(value, dict) or not value:
        raise ValueError('NLSC 未提供此建物明細。')
    if str(value.get('buildno', '')) != requested['buildno']:
        raise ValueError('NLSC 回傳建號不一致，未顯示資料。')
    section = str(value.get('sectstr', ''))
    if not re.match(re.escape(requested['sect']) + r'(?:\s|$)', section):
        raise ValueError('NLSC 回傳地段不一致，未顯示資料。')
    labels = [('town','行政區'),('officestr','地政事務所'),('sectstr','地段'),
              ('buildno','建號'),('door','門牌'),('area','建物面積（平方公尺）'),
              ('story','樓層數（原始值）'),('floor','所在樓層'),
              ('date','建物完成日期（原始值）'),('mainuse','主要用途')]
    return [(label, str(value[key]) if value.get(key) not in (None, '') else '未提供') for key, label in labels]


class NlscBuildingLookup(NetworkClient):
    building_ready = pyqtSignal(object, str)
    BASE = ''
    REFERER = 'https://maps.nlsc.gov.tw/T09/mapshow.action?In_type=web'
    ORIGIN = 'https://maps.nlsc.gov.tw'

    def start_building(self, data):
        self.cancel()
        self.context = building_fields(data)
        self._request(HOME, None)

    def _success(self, body):
        if self.endpoint == HOME:
            parser = PageToken()
            parser.feed(body)
            if len(parser.values) != 1:
                raise ValueError('NLSC 圖台未提供有效的建物查詢 token。')
            self._request(DETAIL, dict(self.context, buildToken=next(iter(parser.values))))
        else:
            rows = building_rows(body, self.context)
            self.context = None
            self.building_ready.emit(rows, '')

    def _fail(self, status, body, reason):
        self.context = None
        self.timer.stop()
        self.failed.emit('NLSC 建物查詢失敗：HTTP %s｜%s' % (status if status is not None else '無回應', reason))
