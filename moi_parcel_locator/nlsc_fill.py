"""NLSC parcel PNG loader with bounded image-generation retries."""
import json
import re
from html import unescape
from urllib.parse import urlencode
from qgis.PyQt.QtCore import pyqtSignal
from .network_client import NetworkClient, image_layers
from .nlsc_point import PARCEL, parcel_identity
from .nlsc_data import request_fields


def fill_images(body, identity):
    return fill_response(body, identity)[0]


def fill_response(body, identity):
    source = body.strip()
    match = re.fullmatch(r'nlscParcel\((.*)\)\s*;?', source, re.S)
    payload = json.loads(match[1] if match else source)
    if not isinstance(payload, list) or not payload:
        raise ValueError('NLSC 未回傳宗地填色資料。')
    images = []
    for group in payload:
        if not isinstance(group, list) or not group:
            raise ValueError('NLSC 填色群組格式無效。')
        for row in group:
            if not isinstance(row, dict):
                raise ValueError('NLSC 填色項目格式無效。')
            if row.get('msg'):
                message = ' '.join(re.sub(r'<[^>]*>', '', unescape(str(row['msg']))).split())[:240]
                if re.search(r'請稍候|請稍後|稍後再試|產置中|產製中|處理中|忙碌|繁忙|頻繁|頻率|超過.*(?:次數|上限)', message):
                    return None, message
                raise ValueError('NLSC 回覆：' + message)
        actual = parcel_identity(json.dumps([group]), identity['cityCode'])
        if any(str(actual[key]) != str(identity[key]) for key in ('office', 'sectNo', 'landNo')):
            raise ValueError('NLSC 填色回應與查詢宗地不一致。')
        for row in group:
            if row.get('image'):
                images.append(dict(IMG=row['image'], EXT=[row.get(k) for k in ('lx', 'ly', 'rx', 'ry')]))
    return image_layers(json.dumps({'IMG': images})), ''


class NlscFillLookup(NetworkClient):
    images_ready = pyqtSignal(object, str)
    BASE = ''
    REFERER = 'https://maps.nlsc.gov.tw/'
    ORIGIN = 'https://maps.nlsc.gov.tw'
    RETRY_DELAYS = (750, 1500, 3000, 6000)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.retry_timer.timeout.disconnect(self._retry_token)
        self.retry_timer.timeout.connect(self._retry_image)
        self.retry_timer.setInterval(750)

    def start_parcel(self, data):
        self.cancel()
        fields = request_fields(data)
        self.context = dict(data, landNo=str(int(fields['landno'][:4])) +
            ('-' + str(int(fields['landno'][4:])) if int(fields['landno'][4:]) else ''))
        self.image_retries = 0
        self.image_url = PARCEL + '?' + urlencode(dict(type='2', flag='2',
            office=data['office'], sect=fields['sect'], landno=fields['landno'],
            alpah='0.5f', imgflag='1', callback='nlscParcel'))
        self._request(self.image_url, None)

    def _retry_image(self):
        if self.context is not None and self.reply is None:
            self._request(self.image_url, None)

    def _success(self, body):
        images, message = fill_response(body, self.context)
        if images is None:
            if not self._schedule_retry(message):
                self._fail(200, '', message + '；已重試 %d 次，可重新查詢此地號補查填色。' % self.image_retries)
            return
        data, self.context = self.context, None
        self.images_ready.emit(images, data['sectNo'] + '／' + data['landNo'])

    def _schedule_retry(self, message):
        if self.context is None or self.image_retries >= len(self.RETRY_DELAYS):
            return False
        delay = self.RETRY_DELAYS[self.image_retries]
        self.image_retries += 1
        self.retry_timer.start(delay)
        self.progress.emit('NLSC 填色：%s；%.2g 秒後重試 %d/%d。' %
                           (message, delay/1000, self.image_retries, len(self.RETRY_DELAYS)))
        return True

    def _fail(self, status, body, reason):
        if status in (502, 503, 504) and self._schedule_retry('HTTP %s，服務暫時無法處理請求' % status):
            return
        data = self.context or {}
        self.context = None
        self.timer.stop()
        self.retry_timer.stop()
        parcel = '%s/%s' % (data.get('sectNo', '?'), data.get('landNo', '?'))
        http = '' if status == 200 else 'HTTP %s｜' % (status if status is not None else '無回應')
        self.failed.emit('NLSC 填色未完成(%s)：%s%s' % (parcel, http, reason))
