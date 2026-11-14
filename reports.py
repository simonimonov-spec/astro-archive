"""Recognise report containers and empty Astrometrica acknowledgements.

This is a structural check, not scientific validation or submission to MPC.
The original text is never reformatted.
"""
import math
import re
import xml.etree.ElementTree as ET

# Automatic and current-report reads use the original Astrometrica MPC file only.
# Other formats remain available through explicit file selection.
REPORT_NAMES = ('MPCReport.txt',)


def report_info(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    result = dict(kind='text', label='Текстов отчет', rows=None, usable=True,
                  reason='Прегледай съдържанието и потвърди принадлежността към комплекта.')
    if not lines:
        return dict(result, kind='empty', label='Няма отчет', rows=0, usable=False,
                    reason='Още няма измервания в отчета.')
    ack = re.compile(r'^ACK\s+MPCReport\s+file\s+updated\b', re.I)
    end = re.compile(r'^-+\s*end\s*-+$', re.I)
    payload = [line for line in lines if not ack.match(line) and not end.match(line)]
    if not payload:
        return dict(result, kind='ack', label='Служебно потвърждение', rows=0, usable=False,
                    reason='„ACK MPCReport file updated“ не съдържа измервания. Изчакай измерванията да бъдат записани в MPCReport.txt и прочети файла отново.')

    # ADES may contain multiple contexts/tables. Read identifiers, not row numbers.
    columns = None
    rows = 0
    has_psv = False
    for line in payload:
        if line.startswith(('#', '!')):
            continue
        cells = [cell.strip() for cell in line.split('|')]
        if {'obsTime', 'ra', 'dec'} <= set(cells):
            columns = {name: i for i, name in enumerate(cells)}
            has_psv = True
            continue
        if columns:
            try:
                ra, dec = float(cells[columns['ra']]), float(cells[columns['dec']])
                observed = cells[columns['obsTime']]
                if (observed and math.isfinite(ra) and math.isfinite(dec)
                        and 0 <= ra < 360 and -90 <= dec <= 90):
                    rows += 1
            except (ValueError, IndexError):
                pass
    if has_psv:
        return dict(result, kind='ades-psv', label='ADES PSV', rows=rows, usable=bool(rows),
                    reason=f'Разпознати редове с измервания: {rows}. Това не е научна проверка.' if rows else 'Има ADES заглавка, но още няма редове с измервания.')
    stripped = text.lstrip('\ufeff \t\r\n')
    if stripped.startswith(('<?xml', '<ades')):
        if re.search(r'<!\s*(?:DOCTYPE|ENTITY)\b', text, re.I):
            return dict(result, kind='ades-xml', label='XML отчет', rows=0, usable=False,
                        reason='Не се приемат XML DTD или външни обекти.')
        try:
            root = ET.fromstring(stripped)
            if root.tag.rsplit('}', 1)[-1] == 'ades':
                rows = sum(node.tag.rsplit('}', 1)[-1] in ('optical', 'radar', 'occultation') for node in root.iter())
                return dict(result, kind='ades-xml', label='ADES XML', rows=rows, usable=bool(rows),
                            reason=f'Разпознати записи: {rows}. Това не е научна проверка.' if rows else 'ADES XML още няма записи с измервания.')
        except ET.ParseError:
            return dict(result, kind='ades-xml', label='XML отчет', rows=0, usable=False,
                        reason='XML отчетът не е завършен или не може да бъде прочетен.')
    if all(line.startswith(('#', '!')) for line in payload) and any('version=' in line.lower() for line in payload):
        return dict(result, kind='ades-psv', label='ADES PSV', rows=0, usable=False,
                    reason='Има само ADES описание, без измервания.')
    return result


def report_suffix(text):
    kind = report_info(text)['kind']
    return '.psv' if kind == 'ades-psv' else '.xml' if kind == 'ades-xml' else '.txt'
