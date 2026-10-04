"""Refresh editable choices after map selection without blocking the query."""
import xml.etree.ElementTree as ET
from qgis.PyQt.QtCore import QObject, QTimer
from qgis.PyQt.QtNetwork import QNetworkRequest, QNetworkReply
from qgis.core import QgsNetworkAccessManager
from . import list_cache


class PointListRefresh(QObject):
    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self.pending = []
        self.generation = 0
        self.identity = None
        self.snapshot = None

    def cancel(self):
        self.generation += 1
        pending, self.pending = self.pending, []
        for reply, timer, slot in pending:
            timer.stop()
            reply.finished.disconnect(slot)
            reply.abort()
            reply.deleteLater()
            timer.deleteLater()

    def start(self, identity):
        panel = self.panel
        snapshot = (panel.county_combo.currentText(), panel.town_combo.currentText(), panel.section_code_edit.text(), panel.section_combo.currentText())
        same_area = self.identity is not None and all(self.identity[k] == identity[k] for k in ('cityCode', 'townCode'))
        if not same_area:
            self.cancel()
        self.identity, self.snapshot = dict(identity), snapshot
        if same_area and self.pending:
            return
        generation = self.generation
        for kind, url in [('town', panel.NLSC_TOWN_URL.format(identity['cityCode'])),
                          ('section', panel.NLSC_SECTION_URL.format(identity['cityCode'], identity['townCode']))]:
            if (kind == 'town' and panel.towns_loaded_for == identity['cityCode'] or
                    kind == 'section' and panel.sections_loaded_for == (identity['cityCode'], identity['townCode'])):
                continue
            cached = list_cache.get(url)
            if cached is not None:
                self.apply(kind, cached, identity, snapshot)
                continue
            reply = QgsNetworkAccessManager.instance().get(list_cache.list_request(url))
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(30000)
            timer.timeout.connect(reply.abort)
            slot = lambda r=reply, t=timer, k=kind: self.finished(r, t, k, dict(identity), snapshot, generation)
            self.pending.append((reply, timer, slot))
            reply.finished.connect(slot)
            timer.start()

    def finished(self, reply, timer, kind, data, snapshot, generation):
        if generation != self.generation or not any(entry[0] is reply for entry in self.pending):
            return
        self.pending = [entry for entry in self.pending if entry[0] is not reply]
        timer.stop()
        ok = reply.error() == QNetworkReply.NetworkError.NoError and reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) == 200
        body = bytes(reply.readAll())
        reply.deleteLater()
        timer.deleteLater()
        panel = self.panel
        if not ok or generation != self.generation:
            return
        url = (panel.NLSC_TOWN_URL.format(data['cityCode']) if kind == 'town' else
               panel.NLSC_SECTION_URL.format(data['cityCode'], data['townCode']))
        if not list_cache.valid(url, body):
            return
        list_cache.put(url, body)
        self.apply(kind, body, self.identity, self.snapshot)

    def apply(self, kind, body, data, snapshot):
        panel = self.panel
        # Never replace choices while the user is editing or after a newer click.
        current = (panel.county_combo.currentText(), panel.town_combo.currentText(), panel.section_code_edit.text(), panel.section_combo.currentText())
        if current != snapshot:
            return
        try:
            root = ET.fromstring(body)
            if kind == 'town':
                items = [(panel.xml_text(node, 'townname'), panel.xml_text(node, 'towncode')) for node in root]
                items = [(name, code) for name, code in items if name and code]
                if not any(code == data['townCode'] for _, code in items):
                    return
                combo = panel.town_combo
                combo.blockSignals(True)
                try:
                    combo.clear()
                    for name, code in items:
                        combo.addItem(name, code)
                    combo.setCurrentIndex(combo.findData(data['townCode']))
                    combo.setEditText(snapshot[1])
                finally:
                    combo.blockSignals(False)
                panel.towns_loaded_for = data['cityCode']
            else:
                records = [{key: panel.xml_text(node, key) for key in ('office', 'officestr', 'sectcode', 'sectstr')} for node in root]
                records = [record for record in records if all(record[k] for k in ('office', 'sectcode', 'sectstr'))]
                selected = next((r for r in records if r['sectcode'] == data['sectNo'] and r['office'] == data['office']), None)
                if selected is None:
                    return
                combo = panel.section_combo
                combo.blockSignals(True)
                try:
                    combo.clear()
                    panel.section_records = records
                    panel.section_by_code = {r['sectcode']: r for r in records}
                    for record in records:
                        combo.addItem(record['sectcode']+'｜'+record['sectstr'], record)
                    panel.section_code_model.setStringList([r['sectcode']+'｜'+r['sectstr'] for r in records])
                    panel.apply_section_selection(selected)
                    combo.setEditText(snapshot[3])
                finally:
                    combo.blockSignals(False)
                panel.sections_loaded_for = (data['cityCode'], data['townCode'])
        except (ET.ParseError, ValueError):
            # Keep the valid identity returned by NLSC if the list is unavailable.
            return
