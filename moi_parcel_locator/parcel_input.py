"""Validate a whole comma-separated submission before starting any requests."""
import re


def parcel_numbers(text):
    result, seen = [], set()
    for token in str(text).replace('，', ',').split(','):
        token = token.strip()
        if re.fullmatch(r'[0-9]{8}', token):
            main, sub = int(token[:4]), int(token[4:])
        elif re.fullmatch(r'[0-9]{1,4}(?:-[0-9]{1,4})?', token):
            parts = token.split('-')
            main, sub = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
        else:
            raise ValueError('地號「%s」格式無效，請以逗號分隔，例如 1, 43, 123-4；主號及子號各限四碼。'
                             % (token or '空白'))
        number = str(main) + ('-' + str(sub) if sub else '')
        if number not in seen:
            seen.add(number)
            result.append(number)
    return result
