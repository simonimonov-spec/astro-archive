"""Read-only release checks; never replace a running installation."""
import datetime as dt
import json
import os
import threading
from Bridge.Plugins.downloads import fetch, REPOSITORY


class Updates:
    def __init__(self):
        self.lock = threading.Lock()
        self.value = dict(current=os.environ.get('ASTRO_ARCHIVE_RELEASE_TAG') or 'Изходно копие', checked=False, available=False)

    def check(self):
        with self.lock:
            try:
                releases = json.loads(fetch(f'https://api.github.com/repos/{REPOSITORY}/releases?per_page=100', 2_000_000))
                published = sorted((r for r in releases if not r.get('draft') and r.get('published_at')), key=lambda r:r['published_at'], reverse=True)
                release = published[0] if published else None
                cutoff = os.environ.get('ASTRO_ARCHIVE_RELEASE_CUTOFF')
                available = bool(release and cutoff and release['tag_name'] != self.value['current'] and dt.datetime.fromisoformat(release['published_at'].replace('Z','+00:00')) > dt.datetime.fromisoformat(cutoff.replace('Z','+00:00')))
                self.value = dict(current=self.value['current'], checked=True, available=available,
                                  latest=release['tag_name'] if release else None,
                                  url=f'https://github.com/{REPOSITORY}/releases',
                                  problem=None, currentKnown=bool(cutoff), checkedUtc=dt.datetime.now(dt.timezone.utc).isoformat())
            except Exception:
                self.value = dict(self.value, checked=True, available=False, problem='Проверката в GitHub е недостъпна. Можеш да продължиш с текущата програма и да опиташ отново.')
            return dict(self.value)
