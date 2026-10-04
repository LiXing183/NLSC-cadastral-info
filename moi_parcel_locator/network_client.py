"""NLSC parcel lookup. All QObject activity stays on the GUI thread."""
import json
import math
import re
import base64
import struct
from urllib.parse import urlencode, urlsplit

from qgis.PyQt.QtCore import QObject, QTimer, QUrl, QByteArray, pyqtSignal
from qgis.PyQt.QtNetwork import QNetworkRequest, QNetworkReply
from qgis.core import QgsNetworkAccessManager
from .request_queue import request_queue


def parcel_number(value):
    value = value.strip()
    if not re.fullmatch(r"[0-9]+(?:-[0-9]+)?", value):
        raise ValueError("請輸入一般地號，例如 1、896 或 123-4。")
    return value


def response_summary(body):
    # Keep diagnostics useful without exposing session tokens in HTML errors.
    body = re.sub(r"(<input\b[^>]*\bvalue\s*=\s*)([\"']).*?\2",
                  r"\1\2[redacted]\2", body, flags=re.I | re.S)
    return " ".join(body.split())[:600] or "（空白回應）"


class NetworkClient(QObject):
    located = pyqtSignal(float, float)
    failed = pyqtSignal(str)
    progress = pyqtSignal(str)
    queue_state = pyqtSignal(bool, str)
    BASE = ""
    request_priority = 10
    dispatch_allowed = None


    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = QgsNetworkAccessManager.instance()
        self.reply = None
        self.context = None
        self.scheduler = None
        self.bound_queues = set()
        self.rate_retries = 0
        self.last_status = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(30000)
        self.timer.timeout.connect(self._timeout)
        self.retry_timer = QTimer(self)
        self.retry_timer.setSingleShot(True)
        self.retry_timer.setInterval(2000)
        self.retry_timer.timeout.connect(self._retry_token)

    def _request(self, endpoint, fields=None, retry=False):
        self.endpoint = endpoint
        self.request_fields = None if fields is None else dict(fields)
        if not retry:
            self.rate_retries = 0
            self.last_status = None
        host = (urlsplit(self.BASE + endpoint).hostname or '').lower()
        if host == 'nlsc.gov.tw' or host.endswith('.nlsc.gov.tw'):
            self.scheduler = request_queue(host)
            if self.scheduler not in self.bound_queues:
                self.bound_queues.add(self.scheduler)
                queue, token = self.scheduler, id(self)
                self.destroyed.connect(lambda: queue.discard(token))
            self.scheduler.submit(self, endpoint, self.request_fields)
        else:
            self._send_request(endpoint, self.request_fields)

    def cached_response(self, endpoint, fields):
        return None

    def can_dispatch(self):
        return self.dispatch_allowed is None or self.dispatch_allowed()

    def shared_request_key(self, endpoint, fields):
        return None

    def request_interval(self, endpoint, fields):
        return 0.0

    def _send_request(self, endpoint, fields):
        if self.context is None:
            if self.scheduler is not None:
                self.scheduler.completed(self)
            return
        self.timed_out = False
        request = QNetworkRequest(QUrl(self.BASE + endpoint))
        request.setAttribute(QNetworkRequest.Attribute.CacheLoadControlAttribute,
                             QNetworkRequest.CacheLoadControl.AlwaysNetwork)
        request.setAttribute(QNetworkRequest.Attribute.CacheSaveControlAttribute, False)
        request.setRawHeader(b"Referer", getattr(self, 'REFERER', self.BASE + 'Normal').encode('ascii'))
        request.setRawHeader(b"X-Requested-With", b"XMLHttpRequest")
        request.setRawHeader(b"Accept", b"application/json, text/html, */*")
        try:
            if fields is None:
                self.reply = self.manager.get(request)
            else:
                request.setRawHeader(b"Content-Type", b"application/x-www-form-urlencoded; charset=UTF-8")
                parts = urlsplit(self.BASE)
                request.setRawHeader(b"Origin", getattr(self, 'ORIGIN', parts.scheme + '://' + parts.netloc).encode('ascii'))
                self.reply = self.manager.post(request, QByteArray(urlencode(fields).encode("utf-8")))
            reply = self.reply
            self.finished_slot = lambda r=reply: self._finished(r)
            reply.finished.connect(self.finished_slot)
            self.timer.start()
        except Exception as exc:
            if self.scheduler is not None:
                self.scheduler.completed(self)
            self._fail(None, "", str(exc))

    def _timeout(self):
        if self.reply is not None:
            self.timed_out = True
            self.reply.abort()

    def cancel(self):
        self.timer.stop()
        self.retry_timer.stop()
        reply, self.reply = self.reply, None
        self.context = None
        if self.scheduler is not None:
            self.scheduler.discard(id(self))
        if reply is not None:
            reply.finished.disconnect(self.finished_slot)
            reply.abort()
            reply.deleteLater()

    def _retry_token(self):
        pass

    def _fail(self, status, body, reason):
        context = self.context or {}
        self.context = None
        self.failed.emit(
            "地號定位失敗：office={office} / sectNo={sectNo} / landNo={landNo}\n"
            "{endpoint}｜HTTP {status}｜{reason}\n回應摘要：{summary}".format(
                office=context.get("office", ""), sectNo=context.get("sectNo", ""),
                landNo=context.get("landNo", ""), endpoint=self.endpoint,
                status=status if status is not None else "無回應", reason=reason,
                summary=response_summary(body)))

    def _finished(self, reply):
        # A queued completion from a cancelled request must never consume the
        # new reply, stop its timeout, or parse it using the new endpoint.
        if reply is not self.reply:
            return
        self.reply = None
        self.timer.stop()
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        self.last_status = status
        body = bytes(reply.readAll()).decode("utf-8-sig", errors="replace")
        error, reason = reply.error(), reply.errorString()
        retry_after = bytes(reply.rawHeader(b'Retry-After')).decode('ascii', errors='ignore') if hasattr(reply, 'rawHeader') else ''
        reply.deleteLater()
        if self.scheduler is not None:
            if status == 429:
                self.scheduler.rate_limited(retry_after)
            self.scheduler.completed(self, success=status == 200 and error == QNetworkReply.NetworkError.NoError)
        if self.context is None:
            return
        if status == 429 and self.scheduler is not None:
            if self.rate_retries < 4:
                self.rate_retries += 1
                self._request(self.endpoint, self.request_fields, retry=True)
            else:
                self._fail(429, '', 'NLSC 持續限制查詢頻率，已重試 4 次；請稍後重新查詢未完成地號。')
            return
        if self.timed_out or status != 200 or error != QNetworkReply.NetworkError.NoError:
            reason = "請求逾時（30 秒）" if self.timed_out else reason
            self._fail(status, body, reason)
            return
        try:
            self._success(body)
        except Exception as exc:
            self._fail(status, body, str(exc))

    def _success(self, body):
        raise NotImplementedError()


def image_layers(body):
    data = json.loads(body)
    items = data.get("IMG") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items or len(items) > 32:
        raise ValueError("宗地影像 IMG 清單為空或格式無效")
    result = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("宗地影像項目格式無效")
        ext = item.get("EXT", data.get("EXT"))
        if not isinstance(ext, list) or len(ext) != 4:
            raise ValueError("宗地影像缺少 EXT 範圍")
        ext = tuple(float(v) for v in ext)
        if not (all(math.isfinite(v) for v in ext) and
                -180 <= ext[0] < ext[2] <= 180 and -90 <= ext[1] < ext[3] <= 90):
            raise ValueError("宗地影像 EXT 經緯度無效")
        encoded = item.get("IMG", "")
        if not isinstance(encoded, str) or len(encoded) > 24000000:
            raise ValueError("宗地影像資料無效或過大")
        if encoded.startswith("data:image/png;base64,"):
            encoded = encoded.split(",", 1)[1]
        png = base64.b64decode("".join(encoded.split()), validate=True)
        if len(png) < 33 or png[:8] != b"\x89PNG\r\n\x1a\n" or png[12:16] != b"IHDR":
            raise ValueError("IMG 不是 PNG")
        w, h = struct.unpack(">II", png[16:24])
        if w < 1 or h < 1 or w*h > 16000000:
            raise ValueError("宗地影像尺寸無效或過大")
        result.append((ext, png))
    return result

