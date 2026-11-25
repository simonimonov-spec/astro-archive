"""Read the public StackingWizard grades export for an existing analysis."""
import csv
from collections import Counter
from io import StringIO
from math import isfinite
from unicodedata import normalize

MAX_CSV_BYTES = 2_000_000
NUMERIC_COLUMNS = (
    'grade', 'sky_glow_pct_over_best', 'background', 'noise', 'stars', 'sharpness', 'trailing',
)


def filename_key(value):
    return normalize('NFC', value).casefold()


def match_grades(content, frames):
    if not isinstance(content, str) or not content or len(content.encode('utf-8')) > MAX_CSV_BYTES:
        raise ValueError('Избери CSV файл от StackingWizard до 2 MB.')

    names = Counter(filename_key(row['name']) for row in frames)
    ids = {filename_key(row['name']): row['id'] for row in frames}
    seen = set()
    matched = {}
    try:
        reader = csv.DictReader(StringIO(content.lstrip('\ufeff'), newline=''), strict=True)
        fields = reader.fieldnames or []
        if not {'file', 'grade'} <= set(fields) or len(fields) != len(set(fields)):
            raise ValueError('CSV файлът трябва да е от „Save the grades“ в StackingWizard.')
        for line, row in enumerate(reader, 2):
            name = row.get('file')
            if not name or '/' in name or '\\' in name or None in row or any(value is None for value in row.values()):
                raise ValueError(f'Невалиден ред {line} в CSV файла.')
            key = filename_key(name)
            if key in seen or names[key] > 1:
                raise ValueError(f'Името „{name[:120]}“ се повтаря. Отвори анализ и CSV с уникални имена на кадрите.')
            seen.add(key)
            if key not in ids:
                continue
            metrics = {}
            for column in NUMERIC_COLUMNS:
                raw = row.get(column, '').strip()
                if not raw:
                    continue
                try:
                    number = float(raw)
                except ValueError:
                    raise ValueError(f'Невалидна стойност за {column} на ред {line}.') from None
                if not isfinite(number) or abs(number) > 1e12 or (column == 'grade' and not 0 <= number <= 100):
                    raise ValueError(f'Невалидна стойност за {column} на ред {line}.')
                metrics[column] = number
            if metrics:
                matched[ids[key]] = metrics
    except csv.Error as exc:
        raise ValueError('CSV файлът е повреден или непълен.') from exc
    if not matched:
        raise ValueError('CSV файлът няма измервания за кадри с имена от този анализ.')
    columns = [column for column in NUMERIC_COLUMNS if any(column in row for row in matched.values())]
    return dict(frames=matched, matched=len(matched), csvFrames=len(seen), columns=columns)
