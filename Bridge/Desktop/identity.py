"""Identity belongs to an installation path, never a shared application title."""
import hashlib
import os
from pathlib import Path
from Backend.Foundation.branding import APP_VERSION


def installation_id(root):
    value = str(Path(root).resolve()).casefold()
    return hashlib.sha256(value.encode('utf-8')).hexdigest()[:32]


def window_title(root):
    return f'Astro Archive · {APP_VERSION} · {installation_id(root)[:8]}'


def matches(hello, root):
    return isinstance(hello, dict) and hello.get('installation') == installation_id(root)


def signal_ready(root):
    """Only acknowledge the unpredictable ready-file in this installation's cache."""
    value = os.environ.pop('ASTRO_ARCHIVE_READY', '')
    if not value:
        return
    path = Path(value).resolve()
    folder = (Path(root) / 'Backend' / 'Cache' / 'data').resolve()
    if path.parent != folder or not path.name.startswith('launcher-ready-') or path.suffix != '.json':
        raise ValueError('Invalid launcher readiness path')
    path.write_text('{"ready":true}', encoding='utf-8')
