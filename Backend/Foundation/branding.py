"""Application identity and compatibility with the original on-disk records."""
from pathlib import Path

APP_NAME = 'Astro Archive'
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
