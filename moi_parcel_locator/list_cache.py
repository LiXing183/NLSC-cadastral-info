"""One-day cache limited to public administrative dropdown lists."""
import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET

TTL = 86400
PREFIX = 'moi_parcel_locator/public_lists_v1'
MAX_ENTRIES = 128
MAX_BYTES = 8 * 1024 * 1024


def valid(url, body):
    match = re.fullmatch(r'https://api\.nlsc\.gov\.tw/other/(ListCounty|ListTown/[A-Z]|ListLandSection/([A-Z])/\2?\d{2})', url)
    if not match:
        return False
    fields = ('countycode', 'countyname') if match[1] == 'ListCounty' else (
        ('towncode', 'townname') if match[1].startswith('ListTown/') else ('office', 'sectcode', 'sectstr'))
    try:
        root = ET.fromstring(body)
        return any(all((node.findtext('.//' + field) or '').strip() for field in fields) for node in root)
    except (ET.ParseError, TypeError, ValueError):
        return False


def key(url):
    return PREFIX + '/' + hashlib.sha256(url.encode()).hexdigest()


def cached_item(raw, now):
    try:
        item = json.loads(raw)
        if not 0 <= now - item['time'] < TTL or not isinstance(item['body'], str):
            return None
        return item
    except (ValueError, TypeError, KeyError):
        return None


def prune(settings=None):
    """Remove only this plugin's expired/invalid hashed public-list records."""
    from qgis.PyQt.QtCore import QSettings
    settings = QSettings() if settings is None else settings
    now, live, removed = time.time(), [], 0
    settings.beginGroup(PREFIX)
    try:
        for name in settings.childKeys():
            if not re.fullmatch(r'[0-9a-f]{64}', name):
                continue
            raw = settings.value(name)
            item = cached_item(raw, now)
            if item is None:
                settings.remove(name)
                removed += 1
            else:
                live.append((item['time'], name, len(raw.encode('utf-8'))))
        total = 0
        for index, (_, name, size) in enumerate(sorted(live, reverse=True)):
            total += size
            if index >= MAX_ENTRIES or total > MAX_BYTES:
                settings.remove(name)
                removed += 1
    finally:
        settings.endGroup()
    return removed


def get(url):
    from qgis.PyQt.QtCore import QSettings
    settings = QSettings()
    name = key(url)
    item = cached_item(settings.value(name), time.time())
    if item is not None:
        body = item['body'].encode('utf-8')
        if valid(url, body):
            return body
    settings.remove(name)
    return None


def put(url, body):
    from qgis.PyQt.QtCore import QSettings
    if valid(url, body):
        value = json.dumps({'time': time.time(), 'body': body.decode('utf-8-sig')})
        if len(value.encode('utf-8')) <= MAX_BYTES:
            settings = QSettings()
            settings.setValue(key(url), value)
            prune(settings)


def list_request(url):
    """The bounded settings cache owns these lists, not Qt's general disk cache."""
    from qgis.PyQt.QtCore import QUrl
    from qgis.PyQt.QtNetwork import QNetworkRequest
    request = QNetworkRequest(QUrl(url))
    request.setAttribute(QNetworkRequest.Attribute.CacheLoadControlAttribute, QNetworkRequest.CacheLoadControl.AlwaysNetwork)
    request.setAttribute(QNetworkRequest.Attribute.CacheSaveControlAttribute, False)
    return request
