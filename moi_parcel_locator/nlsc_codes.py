"""Versioned official code tables. Decode presentation only; retain raw rows."""
import json
import re
import base64
import binascii
from pathlib import Path

with Path(__file__).with_name('nlsc_codes.json').open(encoding='utf-8') as stream:
    REFERENCE = json.load(stream)

TABLES = REFERENCE['tables']
FIELD_TABLE = {
    'AA06': 'registration', 'AA08': 'land_category',
    'AA11': 'zone', 'AA12': 'use_category',
    'svWay': 'survey_method', 'svType': 'survey_type', 'coor': 'coordinate',
    'svway': 'survey_method', 'ecoor': 'coordinate', 'wornst': 'damage',
}


def display_code(key, value):
    text = str(value).strip()
    if key in ('AA11', 'AA12') and text and text != '未提供':
        # Official qt_ajax.js decodes these two fields as UTF-8 Base64.
        # Also accept already-decoded names and legacy two-character codes.
        if len(text) > 2 and re.fullmatch(r'[A-Za-z0-9+/]+={0,2}', text):
            try:
                decoded = base64.b64decode(text + '=' * (-len(text) % 4), validate=True).decode('utf-8')
                if decoded and all(c.isprintable() for c in decoded):
                    text = decoded.strip()
            except (ValueError, UnicodeError, binascii.Error):
                pass
    table = TABLES.get(FIELD_TABLE.get(key), {})
    if not table or not text or text == '未提供':
        return text
    code = text.upper()
    if key == 'AA06' and re.fullmatch(r'\d{1,2}', code):
        code = code.zfill(2)
    if code in table:
        return table[code]
    # Already decoded Chinese names remain unchanged. Never guess base64 content.
    if re.fullmatch(r'[A-Za-z0-9]+', text):
        return '代碼 '+text+'（尚未解讀）'
    return text
