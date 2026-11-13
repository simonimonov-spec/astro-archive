"""Probe the private runtime without importing the application or changing data."""
import importlib
import importlib.metadata
from pathlib import Path
import sys


def problems(root):
    result = []
    if sys.version_info[:2] != (3, 13):
        result.append('Python 3.13 x64')
    if sys.maxsize <= 2**32:
        result.append('64-bit Python')
    for line in (root / 'requirements-lock.txt').read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        name, expected = line.split('==', 1)
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            actual = None
        if actual != expected:
            result.append(f'{name}=={expected} (installed: {actual or "missing"})')
    for name in ('numpy', 'scipy', 'astropy.io.fits', 'photutils', 'PIL.Image'):
        try:
            importlib.import_module(name)
        except Exception as error:
            result.append(f'{name}: {error}')
    return result


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    found = problems(Path(__file__).resolve().parents[1])
    print('\n'.join(found) if found else 'READY')
