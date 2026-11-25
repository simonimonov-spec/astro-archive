"""Application identity and compatibility with the original on-disk records."""
import os
import re
from pathlib import Path

APP_NAME = 'Astro Archive'
_RELEASE_TAG = os.environ.get('ASTRO_ARCHIVE_RELEASE_TAG', 'beta1.2.4').strip()
_VERSION_MATCH = re.fullmatch(r'beta(\d+(?:\.\d+){0,2})(-preview)?', _RELEASE_TAG, re.IGNORECASE)
APP_VERSION = (f"Beta {_VERSION_MATCH.group(1)}{' Preview' if _VERSION_MATCH.group(2) else ''}"
               if _VERSION_MATCH else _RELEASE_TAG or 'Beta 1.2.4')
DATA_PREFIX = 'astroarchive'
# These identifiers belong to saved data / the previous local bridge protocol.
# Keeping them here lets an existing archive and unfinished session survive a rename.
LEGACY_DATA_PREFIX = 'photosorter'
LEGACY_APP_NAME = 'PhotoSorter Python'
LEGACY_KEY_HEADER = 'X-PhotoSorter-Key'
KEY_HEADER = 'X-AstroArchive-Key'


def companion(path):
    path = Path(path)
    current = path.with_name(path.name + '.' + DATA_PREFIX + '.json')
    previous = path.with_name(path.name + '.' + LEGACY_DATA_PREFIX + '.json')
    return previous if not current.exists() and previous.exists() else current


def marker(folder, kind):
    folder = Path(folder)
    current = folder / ('.' + DATA_PREFIX + '-' + kind + '.json')
    previous = folder / ('.' + LEGACY_DATA_PREFIX + '-' + kind + '.json')
    return previous if not current.exists() and previous.exists() else current


def session_marker_names():
    return {'.' + prefix + '-session.json' for prefix in (DATA_PREFIX, LEGACY_DATA_PREFIX)}
