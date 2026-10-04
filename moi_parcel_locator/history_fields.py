from .nlsc_display import friendly_rows, ownership_rows, compact_field_label

HEADERS = ['填色', '縣市', '行政區', '段碼', '地段', '地號', '面積', '使用分區',
           '使用地類別', '權利人類別', '公告地價', '公告現值', '查詢時間', '查詢狀態']
STATUS_COLUMN = len(HEADERS) - 1
COPY_COLUMNS = list(range(1, 12))


def export_cell(text):
    return '；'.join(part.strip() for part in str(text).splitlines() if part.strip()).replace('\t', ' ')


def parcel_sort_key(data):
    text = str(data['landNo'])
    if len(text) == 8 and text.isdigit():
        main, sub = int(text[:4]), int(text[4:])
    else:
        parts = text.split('-')
        main, sub = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    return str(data.get('cityCode', '')), str(data.get('office', '')), str(data['sectNo']), main, sub


def history_attributes(groups):
    values = dict((compact_field_label(k), v) for k, v in friendly_rows('土地資訊', groups.get('土地資訊', [])))
    owner = export_cell(dict(ownership_rows(groups.get('權利類別原始資料', [])))['權利人類別'])
    return [values.get('面積(㎡)', '未提供'), values.get('使用分區', '未提供'),
            values.get('使用地類別', '未提供'), owner,
            values.get('公告地價(元/㎡)', '未提供'), values.get('公告土地現值(元/㎡)', '未提供')]
