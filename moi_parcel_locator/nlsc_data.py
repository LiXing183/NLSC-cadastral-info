"""NLSC getLandInfoSect response; retain unknown keys instead of guessing."""
import json
import re
from . import safe_xml as ET
from .nlsc_codes import TABLES


def section_info(body, requested):
    root = ET.fromstring(body)
    def local(tag):
        return tag.rsplit('}', 1)[-1]

    if local(root.tag).lower() != 'sysdatsecbean':
        raise ValueError('NLSC 未回傳預期的地段測繪 XML。')
    records = [(local(node.tag), (node.text or '').strip()) for node in root]
    if not records or not any(key == 'scNo' and value for key, value in records):
        raise ValueError('NLSC 未回傳有效地段代碼。')
    if any(key == 'scNo' and value != requested['sect'] for key, value in records):
        raise ValueError('地段測繪回應與本次地段不一致。')
    labels = {'city':'縣市', 'ldcode':'地政事務所', 'scNo':'地段代碼', 'scNoExt':'段延伸碼',
              'town':'鄉鎮市區代碼', 'svWay':'測量方法', 'svType':'測量類別',
              'mYear':'成圖年', 'mMonth':'成圖月', 'coor':'坐標系統', 'scale':'比例尺分母',
              'dDate':'數化年月', 'svway':'測量方法', 'ecoor':'數化整合坐標系統',
              'ecoorsrc':'數化整合年月', 'wornst':'破損情形',
              'slprt':'山坡地佔比例', 'urbnrt':'都市計畫區比例',
              'rplrt':'重劃保留地比例', 'rYear':'預計重測年度'}
    known = {'svWay': TABLES['survey_method'], 'svType': TABLES['survey_type'],
             'coor': TABLES['coordinate'], 'svway': TABLES['survey_method'],
             'ecoor': TABLES['coordinate'], 'wornst': TABLES['damage']}
    rows = []
    for key, value in records:
        label = labels.get(key, key)
        if key in labels:
            label += ' ['+key+']'
        text = value or '（空白）'
        if value in known.get(key, {}):
            text = known[key][value] + '（代碼 '+value+'）'
        elif key == 'dDate' and re.fullmatch(r'\d{6}', value) and 1 <= int(value[4:]) <= 12:
            text = value[:4]+'-'+value[4:]+'（原始值 '+value+'）'
        rows.append((label, text))
    return [('地段測繪資訊', rows)]


def request_fields(data):
    city, sect, land = (str(data[k]).strip() for k in ('cityCode', 'sectNo', 'landNo'))
    if not re.fullmatch('[A-Za-z]', city) or not re.fullmatch(r'\d{4}', sect):
        raise ValueError('NLSC 查詢缺少有效縣市或地段代碼。')
    if re.fullmatch(r'\d{8}', land):
        number = land
    elif re.fullmatch(r'\d{1,4}(?:-\d{1,4})?', land):
        parts = land.split('-')
        number = parts[0].zfill(4) + (parts[1] if len(parts) == 2 else '0').zfill(4)
    else:
        raise ValueError('NLSC 地號須為主號／子號各四碼以內，或八碼地號。')
    return dict(city=city.upper(), sect=sect, landno=number)


LABELS = {
    'AA45':'縣市代碼', 'AA46':'行政區代碼', 'AA48':'地段代碼', 'AA49':'地號',
    'AA05':'登記日期（民國年月日）', 'AA06':'登記原因', 'AA08':'地目', 'AA09':'等則',
    'AA10':'面積（平方公尺）',
    'AA11':'使用分區（原始值）', 'AA12':'使用地類別（原始值）',
    'AA16':'公告土地現值（元／平方公尺）', 'AA17':'公告地價（元／平方公尺）',
    'AA27':'公告現值年月',
    'seq':'序號', 'name':'所有權人', 'id':'統一編號', 'type':'所有權人類別',
    'scope':'權利範圍類別', 'denominator':'權利範圍持分分母', 'numerator':'權利範圍持分分子',
    'price':'申報地價（元／平方公尺）', 'manage':'管理者名稱',
}


def flatten(value, path='', depth=0):
    if depth > 12:
        return [(path, '資料層級過深，未展開')]
    if isinstance(value, dict):
        if not value:
            return [(path, '（空物件）')]
        rows = []
        for key, item in value.items():
            label = LABELS.get(key, key)
            if key in LABELS:
                label += ' ['+key+']'
            rows.extend(flatten(item, path+'／'+label if path else label, depth+1))
        return rows
    if isinstance(value, list):
        if not value:
            return [(path, '（無回傳項目）')]
        return [row for index, item in enumerate(value, 1) for row in flatten(item, path+'／第%d筆' % index, depth+1)]
    text = '（空白）' if value is None or value == '' else str(value)
    return [(path, text)]


def sections(body, requested):
    data = json.loads(body)
    if not isinstance(data, dict) or not any(k in data for k in ('ralid','lcdetype','land','buildList')):
        raise ValueError('NLSC 未回傳預期的土地資料物件。')
    for section in ('ralid', 'land'):
        record = data.get(section)
        if isinstance(record, dict):
            for key, param in [('AA45','city'), ('AA48','sect'), ('AA49','landno')]:
                if record.get(key) not in (None, '') and str(record[key]) != requested[param]:
                    raise ValueError('NLSC 回傳宗地與本次查詢不一致，未顯示資料。')
    names = {'ralid':'土地資訊', 'lcdetype':'權利類別原始資料', 'land':'公有土地', 'buildList':'建號清單'}
    return [(names.get(key, key), flatten(value)) for key, value in data.items()]
