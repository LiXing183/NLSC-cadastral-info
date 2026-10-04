"""Spreadsheet text and rich table representations of displayed attributes."""
import csv
import io
from html import escape


def table_formats(rows):
    rows = [['欄位', '內容']] + [[str(key), str(value)] for key, value in rows]
    return grid_formats(rows[0], rows[1:])


def grid_formats(headers, rows):
    rows = [list(map(str, headers))] + [list(map(str, row)) for row in rows]
    stream = io.StringIO(newline='')
    csv.writer(stream, delimiter='\t', lineterminator='\r\n').writerows(rows)
    html = '<table>' + ''.join('<tr>' + ''.join(
        '<td style="white-space:pre-wrap">' + escape(cell).replace('\n', '<br>') + '</td>'
        for cell in row) + '</tr>' for row in rows) + '</table>'
    return stream.getvalue(), html
