"""Conservative cleanup of the old plugin's UUID-named PNG/world files."""
from pathlib import Path
import re


def clean_cache(folder, protected=()):
    folder = Path(folder)
    if not folder.is_dir() or folder.is_symlink():
        return 0, 0, 0
    root = folder.resolve()
    protected = {str(Path(p).resolve()).casefold() for p in protected if p}
    removed = kept = failed = 0
    for path in folder.iterdir():
        if not re.fullmatch(r'[0-9a-f]{32}\.(png|pgw)', path.name):
            continue
        if path.is_symlink() or not path.is_file() or path.resolve().parent != root:
            kept += 1
            continue
        if any(str(path.with_suffix(s).resolve()).casefold() in protected for s in ('.png', '.pgw')):
            kept += 1
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            failed += 1
    return removed, kept, failed


def clean_legacy_cache(iface):
    from qgis.PyQt.QtCore import QStandardPaths
    from qgis.PyQt.QtWidgets import QMessageBox
    from qgis.core import QgsProject
    folder = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)) / 'moi_parcel_locator' / 'overlays'
    # The current project can be checked; other saved projects cannot.
    answer = QMessageBox.question(iface.mainWindow(), '清理舊填色暫存檔',
        '刪除舊版產生、且未被目前專案使用的填色 PNG 與定位檔。\n'
        '若其他已儲存專案仍需這些舊填色，請先取消並保留檔案。\n\n' + str(folder),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
    if answer != QMessageBox.StandardButton.Yes:
        return
    protected = []
    for layer in QgsProject.instance().mapLayers().values():
        source = layer.source().split('|', 1)[0]
        if source.casefold().startswith(str(folder).casefold()):
            protected.append(source)
    removed, kept, failed = clean_cache(folder, protected)
    QMessageBox.information(iface.mainWindow(), '清理完成', '已刪除 %d 個檔案；保留 %d 個；失敗 %d 個。' % (removed, kept, failed))
