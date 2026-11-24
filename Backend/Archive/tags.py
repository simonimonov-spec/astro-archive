# Beta 1.3 UI (в разработка, не е издание) · виж docs/BETA-1.3-UI.md
"""Data tag of an archived file: astrophotography or photometry (with type).

The tag lives in each file's companion JSON (key ``tag``) and is mirrored in
the catalog metadata. A missing tag means "not decided yet" (Без етикет).
"""
from __future__ import annotations

KINDS = ('astro', 'photometry')
TYPES = ('variable', 'exoplanet', 'other')
FILTERS = ('astro', 'photometry', 'variable', 'exoplanet', 'other', 'none')
LABEL_LIMIT = 40


def normalize(value, stack=False):
    """Validated tag or None. Stacks keep only the kind; a label only for "other"."""
    if value is None:
        return None
    if not isinstance(value, dict) or value.get('kind') not in KINDS:
        raise ValueError('Невалиден етикет.')
    kind = value['kind']
    kind_type = value.get('type') if kind == 'photometry' and not stack else None
    if kind_type is not None and kind_type not in TYPES:
        raise ValueError('Невалиден тип фотометрия.')
    label = value.get('label') if kind_type == 'other' else None
    if label is not None:
        label = str(label).strip()[:LABEL_LIMIT] or None
    return {'kind': kind, 'type': kind_type, 'label': label}


def matches(tag, wanted):
    """Whether a stored tag passes the archive filter value ('' = everything)."""
    if not wanted:
        return True
    if wanted not in FILTERS:
        raise ValueError('Невалиден филтър за етикет.')
    if wanted == 'none':
        return not tag
    if not tag:
        return False
    if wanted in KINDS:
        return tag.get('kind') == wanted
    return tag.get('kind') == 'photometry' and tag.get('type') == wanted


PREVIEW_SECONDS = 30 * 60


def preview_groups(paths):
    """Files grouped by night, object and telescope for the "what are you importing" dialog."""
    from pathlib import Path
    from Backend.Fits.images import metadata
    groups = {}
    for index, path in enumerate(paths):
        try:
            meta = metadata(Path(path))
        except Exception:  # unreadable files still get a row; the import reports them later
            meta = {}
        night = str(meta.get('night') or 'Неизвестна нощ')
        obj = str(meta.get('object') or 'Неизвестен обект')
        telescope = str(meta.get('telescope') or '')
        key = (night, obj, telescope)
        group = groups.setdefault(key, dict(key='|'.join(key), night=night, object=obj, telescope=telescope, stack=True, files=[]))
        group['stack'] = group['stack'] and meta.get('kind') == 'stack'
        group['files'].append(dict(index=index, name=Path(path).name, exposure=meta.get('exposure'), kind=meta.get('kind')))
    return sorted(groups.values(), key=lambda g: (g['night'], g['object']), reverse=True)


class ImportPreviews:
    """Paths chosen in the native dialog, kept server-side; the UI only sends indices."""
    def __init__(self):
        import threading
        self.lock = threading.Lock()
        self.items = {}

    def add(self, paths, archive_id):
        import secrets, time
        token = secrets.token_urlsafe(18)
        with self.lock:
            now = time.time()
            self.items = {k: v for k, v in self.items.items() if now - v['created'] < PREVIEW_SECONDS}
            self.items[token] = dict(paths=list(paths), archiveId=archive_id, created=now)
        return token

    def take(self, token):
        import time
        with self.lock:
            entry = self.items.pop(str(token), None)
        if not entry or time.time() - entry['created'] >= PREVIEW_SECONDS:
            raise ValueError('Прегледът на внасянето е изтекъл. Избери файловете отново.')
        return entry

    def put_back(self, token, entry):
        """Return a taken preview when its import could not start (e.g. another task runs)."""
        with self.lock:
            self.items[str(token)] = entry
