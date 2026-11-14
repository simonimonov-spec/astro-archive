"""Release checks and verified ZIP downloads; preserve the running installation."""
import datetime as dt
import json
import os
import threading
from Bridge.Plugins.downloads import fetch, REPOSITORY
from Backend.Foundation.runtime import CACHE
from Backend.Foundation.update_download import package_for, download


class Updates:
    def __init__(self,folder=None):
        self.lock = threading.RLock()
        self.folder=folder or CACHE/'Updates';self.release=None;self.task_id=None
        self.transfer=dict(phase='idle',received=0,total=0,percent=0)
        self.value = dict(current=os.environ.get('ASTRO_ARCHIVE_RELEASE_TAG') or 'Изходно копие', checked=False, available=False)

    def check(self):
        with self.lock:
            try:
                releases = json.loads(fetch(f'https://api.github.com/repos/{REPOSITORY}/releases?per_page=100', 2_000_000))
                published = sorted((r for r in releases if not r.get('draft') and r.get('published_at')), key=lambda r:r['published_at'], reverse=True)
                release = published[0] if published else None
                self.release=release
                cutoff = os.environ.get('ASTRO_ARCHIVE_RELEASE_CUTOFF')
                available = bool(release and cutoff and release['tag_name'] != self.value['current'] and dt.datetime.fromisoformat(release['published_at'].replace('Z','+00:00')) > dt.datetime.fromisoformat(cutoff.replace('Z','+00:00')))
                self.value = dict(current=self.value['current'], checked=True, available=available,
                                  latest=release['tag_name'] if release else None,
                                  downloadable=bool(package_for(release)),
                                  url=f'https://github.com/{REPOSITORY}/releases',
                                  problem=None, currentKnown=bool(cutoff), checkedUtc=dt.datetime.now(dt.timezone.utc).isoformat())
            except Exception:
                self.value = dict(self.value, checked=True, available=False, problem='Проверката в GitHub е недостъпна. Можеш да продължиш с текущата програма и да опиташ отново.')
            return dict(self.value)
    def status(self):
        with self.lock:return dict(self.transfer)
    def start(self,submit):
        with self.lock:
            if self.transfer['phase']=='downloading':return self.status()
            package=package_for(self.release)
            if not package:raise ValueError('Няма проверим Windows x64 ZIP за това издание. Провери за нова версия отново.')
            self.transfer=dict(phase='downloading',fileName=package['name'],received=0,total=package['size'],percent=0)
            def work(cancel,report):
                def progress(done,total):
                    with self.lock:self.transfer.update(received=done,total=total,percent=round(done/total*100))
                    report(done,total,'Изтегляне на '+package['name'])
                try:
                    path=download(package,self.folder,cancel,progress)
                    with self.lock:self.transfer.update(phase='complete',path=path,percent=100)
                    return dict(path=path)
                except InterruptedError:
                    with self.lock:self.transfer.update(phase='cancelled')
                    raise
                except Exception as error:
                    with self.lock:self.transfer.update(phase='error',problem=str(error))
                    raise
            try:self.task_id=submit('archive','Изтегляне на нова версия',work,'important')['taskId']
            except Exception:
                self.transfer.update(phase='error',problem='Изтеглянето не може да започне. Опитай отново.');raise
            return self.status()
    def open_folder(self):
        from pathlib import Path
        with self.lock:
            if self.transfer['phase']!='complete' or not Path(self.transfer['path']).is_file():raise ValueError('Няма завършено изтегляне.')
            os.startfile(str(Path(self.transfer['path']).parent))
            return dict(opened=True)
