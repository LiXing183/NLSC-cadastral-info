"""NLSC section and eight-digit building-number pairs."""
import re

def nlsc_buildings(rows, identity):
    result, seen = [], set()
    for label, value in rows:
        match = re.fullmatch(r'\s*(\d{4})\s*,\s*(\d{8})\s*', str(value))
        if not match or match.groups() in seen:
            continue
        section, number = match.groups()
        seen.add(match.groups())
        main, sub = int(number[:5]), int(number[5:])
        display = str(main) + ('-' + str(sub) if sub else '')
        result.append(dict(office=identity['office'], sectNo=section,
                           buildNo=display, nlscNumber=number))
    return result
