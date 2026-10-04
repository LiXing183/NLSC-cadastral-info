from qgis.PyQt.QtCore import pyqtSignal
import time
from collections import OrderedDict
from .network_client import NetworkClient, response_summary
from .nlsc_data import request_fields, sections, section_info

SECTION_CACHE = OrderedDict()
SECTION_CACHE_TTL = 300


class NlscLookup(NetworkClient):
    ready = pyqtSignal(object)
    BASE = 'https://api.nlsc.gov.tw/S09_Ralid/'
    RESULT_ENDPOINT = 'getLandInfoSect'
    REFERER = 'https://maps.nlsc.gov.tw/'
    ORIGIN = 'https://maps.nlsc.gov.tw'

    def start_parcel(self, identity):
        self.cancel()
        self.context = request_fields(identity)
        self._request(self.RESULT_ENDPOINT, dict(self.context))

    def _success(self, body):
        result = sections(body, self.context)
        self.context = None
        self.ready.emit(result)

    def _fail(self, status, body, reason):
        context, self.context = self.context or {}, None
        self.timer.stop()
        if status == 200 and not body.strip():
            self.failed.emit('NLSC 本次未回傳查詢資料(地段 %s%s)。' %
                (context.get('sect',''), ' / 地號 ' + context['landno'] if context.get('landno') else ''))
            return
        self.failed.emit('NLSC 查詢失敗：city=%s / sect=%s / landno=%s\nHTTP %s｜%s\n%s' %
            (context.get('city',''), context.get('sect',''), context.get('landno',''),
             status if status is not None else '無回應', reason, response_summary(body)))


class NlscSectionLookup(NlscLookup):
    BASE = 'https://api.nlsc.gov.tw/other/GetLandSecInfoNlsc/'

    def shared_request_key(self, endpoint, fields):
        return self.BASE + endpoint

    def cached_response(self, endpoint, fields):
        key = self.shared_request_key(endpoint, fields)
        item = SECTION_CACHE.get(key)
        if item is not None:
            if 0 <= time.monotonic()-item[0] < SECTION_CACHE_TTL:
                return item[1]
            SECTION_CACHE.pop(key, None)
        return None

    def start_parcel(self, identity):
        self.cancel()
        fields = request_fields(identity)
        self.context = dict(city=fields['city'], sect=fields['sect'])
        self._request(fields['city']+'/'+fields['sect'], None)

    def _success(self, body):
        result = section_info(body, self.context)
        key = self.BASE + self.context['city'] + '/' + self.context['sect']
        # Cache only a validated response, without refreshing its age on reads.
        item = SECTION_CACHE.get(key)
        if item is None or not 0 <= time.monotonic()-item[0] < SECTION_CACHE_TTL:
            SECTION_CACHE[key] = (time.monotonic(), body)
            SECTION_CACHE.move_to_end(key)
            while len(SECTION_CACHE) > 128:
                SECTION_CACHE.popitem(last=False)
        self.context = None
        self.ready.emit(result)
