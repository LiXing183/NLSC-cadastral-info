"""Readable presentation; raw response rows remain available separately."""
import re
import math
from datetime import date
from .nlsc_codes import display_code, TABLES


def ownership_rows(rows):
    """Mappings verified against the supplied NLSC responses and screenshots."""
    values = {}
    invalid = []
    for key, raw in rows:
        if not key.startswith('lcde_'):
            continue
        try:
            value = float(raw)
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError()
            values[key] = value
        except (ValueError, TypeError):
            invalid.append(key)
    labels = TABLES['owner_api']
    persons = tuple(key for key in set(values) | set(invalid)
                    if key == 'lcde_1' or key.startswith('lcde_1_'))
    lines = []
    if any(key in invalid for key in persons):
        lines.append('本國人：比例資料不完整')
    else:
        total = sum(values.get(key, 0) for key in persons)
        if total > 0:
            lines.append('本國人：%.2f%%' % (total*100))
    for key, value in values.items():
        if key not in persons and value > 0:
            label = labels.get(key, '未解讀類別（'+key+'）')
            lines.append('%s：%.2f%%' % (label, value*100))
    lines.extend('比例資料無效（'+key+'）' for key in invalid if key not in persons)
    if not lines:
        lines = ['本次回應未提供非零比例' if values else '未提供']
    return [('權利人類別', '\n'.join(lines))]


def public_rows(rows):
    land, owners = [], {}
    labels = {'date': '登記日期', 'cause': '登記原因', 'name': '所有權人',
              'id': '統一編號', 'type': '所有權人類別', 'scope': '權利範圍類別',
              'price': '申報地價(元/㎡)', 'seq': '登記次序',
              'denominator': '權利範圍分母', 'numerator': '權利範圍分子',
              'manage': '管理者名稱'}
    for label, value in rows:
        if not label.startswith('userList／'):
            land.extend(friendly_rows('公有土地基本資料', [(label, value)]))
            continue
        match = re.match(r'userList／第(\d+)筆／(.+)', label)
        if not match:
            continue
        number, field = match.groups()
        key = re.search(r'\[([^\]]+)\]$', field)
        key = key.group(1) if key else field
        name = labels.get(key, re.sub(r' \[[^\]]+\]', '', field))
        text = '未提供' if value in ('（空白）', '（空物件）', '（無回傳項目）') else value
        if key in ('date', 'AA05'):
            text = public_date(text)
        owners.setdefault(number, []).append((name, text))
    land = [(name, public_date(value) if name == '登記日期' else value) for name, value in land]
    result = [('公有土地資料', '')] + land if land else []
    for number, values in owners.items():
        result.append(('公有土地所有權人資料(第%s筆)' % number, ''))
        result.extend(values)
    return result


def public_date(text):
    if re.fullmatch(r'\d{7}', str(text)):
        return text[:3] + '/' + text[3:5] + '/' + text[5:]
    match = re.fullmatch(r'民國 (\d+) 年 (\d+) 月 (\d+) 日', str(text))
    if match:
        return '%03d/%02d/%02d' % tuple(map(int, match.groups()))
    return text


def survey_rows(rows):
    values = {}
    for label, value in rows:
        match = re.search(r' \[([^\]]+)\]$', label)
        if match:
            values[match.group(1)] = (label, value)
    method = values.get('svway', values.get('svWay', ('', '')))[1]
    integration = method == '4' or method.split('（')[0] == TABLES['survey_method'].get('4')
    common, integrated, original = [], [], []
    for key, pair in values.items():
        if key in ('mYear', 'mMonth'):
            continue
        rendered = friendly_rows('測繪分組', [pair])
        if key == 'scale':
            rendered = [('比例尺', pair[1])]
        target = common if key in ('city', 'ldcode', 'scNo', 'scNoExt', 'town') else original
        if key in ('ecoor', 'ecoorsrc') or (integration and key in ('svway', 'svWay')):
            target = integrated
        target.extend(rendered)
    if 'mYear' in values or 'mMonth' in values:
        original.append(('成圖年月', values.get('mYear', ('', '未提供'))[1] + '-' +
                         values.get('mMonth', ('', '未提供'))[1]))
    result = common
    if integration and integrated:
        result += [('地段數化整合資訊', '')] + integrated
    elif integrated:
        original.extend(integrated)
    if original:
        result += [('地段原始資訊', '')] + original
    return result


def friendly_rows(title, rows):
    if title == '權利類別原始資料':
        return []
    result = []
    year = month = None
    for label, value in rows:
        if title == '建號清單':
            parts = value.split(',')
            if len(parts) == 2 and re.fullmatch(r'\d{4}', parts[0]) and re.fullmatch(r'\d{8}', parts[1]):
                main, sub = int(parts[1][:5]), int(parts[1][5:])
                number = str(main) + ('-'+str(sub) if sub else '')
                result.append(('建號', '地段 '+parts[0]+'／'+number+' 建號'))
            continue
        match = re.search(r' \[([^\]]+)\]$', label)
        if not match:
            continue
        key = match.group(1)
        # Keep owner grouping, but remove JSON paths and field codes.
        name = re.sub(r' \[[^\]]+\]', '', label)
        name = name.replace('userList／', '').replace('（原始值）', '')
        text = re.sub(r'（(?:代碼 |原始值 )[^）]*）', '', value).strip()
        if text in ('（空白）', '（空物件）', '（無回傳項目）'):
            text = '未提供'
        if key in ('rYear','dDate','ecoorsrc') and text == '0':
            text = '未提供'
        if key == 'scNoExt' and text in ('Y','Z'):
            text = '無'
        if key == 'ecoorsrc' and re.fullmatch(r'\d{6}',text) and 1 <= int(text[4:]) <= 12:
            text = text[:4]+'-'+text[4:]
        if key == 'AA27' and re.fullmatch(r'\d{5}',text) and 1 <= int(text[3:]) <= 12:
            text = '民國 %d 年 %d 月' % (int(text[:3]),int(text[3:]))
        if name == '成圖年':
            year = text
            continue
        if name == '成圖月':
            month = text
            continue
        if name == '登記日期（民國年月日）' and re.fullmatch(r'\d{7}', text):
            try:
                y, m, d = int(text[:3]), int(text[3:5]), int(text[5:])
                date(y+1911, m, d)
                text = '民國 %d 年 %d 月 %d 日' % (y, m, d)
            except ValueError:
                pass
            name = '登記日期'
        if name == '地號' and re.fullmatch(r'\d{8}', text):
            main, sub = int(text[:4]), int(text[4:])
            text = str(main) + ('-'+str(sub) if sub else '')
        if name == '比例尺分母':
            name = '比例尺'
            if text.isdigit() and int(text) > 0:
                text = '1:'+text
        text = display_code(key, text)
        result.append((name, text))
    if year is not None or month is not None:
        result.append(('成圖年月', (year or '未提供')+' 年 '+(month or '未提供')+' 月'))
    return result
def compact_field_label(text):
    text = ''.join(str(text).split()).replace('平方公尺', '㎡')
    return ''.join(chr(ord(char) - 0xFEE0) if 0xFF01 <= ord(char) <= 0xFF5E else char
                   for char in text)
