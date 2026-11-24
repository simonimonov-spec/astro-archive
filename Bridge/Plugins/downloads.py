"""Official GitHub catalogue and bounded, verified, non-executing downloads."""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import threading
import urllib.request
import urllib.parse
import uuid
import zipfile

from Backend.Foundation.runtime import ROOT, read_json, atomic_json, within

REPOSITORY = 'simonimonov-spec/astro-archive'
CATALOG_URL = f'https://raw.githubusercontent.com/{REPOSITORY}/refs/heads/main/docs/plugin-catalog.json'
MAX_ARCHIVE = 32_000_000


def version_key(value):
    match = re.fullmatch(r'(?:(alpha|beta|rc|v)[ -]?)?(\d+(?:\.\d+)*)', value.strip(), re.IGNORECASE)
    if not match:
        return None
    channel, number = match.groups()
    parts = [int(part) for part in number.split('.')]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return {'alpha': 0, 'beta': 1, 'rc': 2, 'v': 3, '': 3}[(channel or '').lower()], tuple(parts)


def newer_version(current, offered):
    installed, latest = version_key(current), version_key(offered)
    return installed is not None and latest is not None and latest > installed


def fetch(url, limit):
    request = urllib.request.Request(url, headers={'User-Agent': 'AstroArchive', 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=12) as response:
        final = urllib.parse.urlsplit(response.url)
        if final.scheme != 'https' or final.hostname not in {'raw.githubusercontent.com', 'api.github.com', 'codeload.github.com'}:
            raise ValueError('Невалиден адрес за изтегляне.')
        data = response.read(limit + 1)
        if len(data) > limit:raise ValueError('Изтеглянето надвишава допустимия размер.')
        return data


def validate_package(item):
    if item.get('repository') != REPOSITORY:
        raise ValueError('Каталогът трябва да сочи проверено официално изтегляне.')
    for field, length in (('commit', 40), ('sha256', 64)):
        value = item.get(field)
        if not isinstance(value, str) or len(value) != length or not re.fullmatch(r'[0-9a-f]+', value):
            raise ValueError('Каталогът трябва да сочи проверено официално изтегляне.')
    for field in ('name', 'description', 'version'):
        if not isinstance(item.get(field), str) or not item[field] or len(item[field]) > 2000:
            raise ValueError('Непълно описание на плъгин.')


def validate(document):
    if not isinstance(document, dict) or document.get('format') != 1 or not isinstance(document.get('plugins'), list) or len(document['plugins']) > 100:
        raise ValueError('Невалиден каталог на плъгините.')
    entries, seen = [], set()
    for item in document['plugins']:
        if not isinstance(item, dict):raise ValueError('Невалиден запис в каталога.')
        ident = item.get('id', '')
        if not isinstance(ident, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', ident) or ident in seen or ident == 'ai-training':
            raise ValueError('Невалиден публичен плъгин.')
        seen.add(ident)
        validate_package(item)
        # Older clients use the newest package field they recognise.
        for field in ('archiveSourcePackage', 'lanPackage'):
            if field not in item:
                continue
            package = item[field]
            if not isinstance(package, dict) or set(package) != {'description', 'version', 'commit', 'sha256'}:
                raise ValueError('Невалиден пакет за споделен архив.')
            item = dict(item, **package)
            del item[field]
            validate_package(item)
        entries.append(item)
    return entries


def unpack(data, target):
    """Accept one GitHub archive root, regular files only, no Windows aliases."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) > 2000 or sum(e.file_size for e in entries) > 64_000_000:raise ValueError('Прекалено голям плъгин.')
        roots = set(); seen = set()
        for entry in entries:
            if entry.orig_filename != entry.filename:raise ValueError('Ненормализиран ZIP път.')
            parts = entry.filename.rstrip('/').split('/')
            if len(parts) < 1 or any(not p or p in {'.', '..'} or p.endswith((' ', '.')) or re.search(r'[\\:\x00-\x1f<>"|?*]', p) or p.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(10)),*(f'LPT{i}' for i in range(10))} for p in parts):
                raise ValueError('Небезопасен път в ZIP.')
            roots.add(parts[0])
            if len(roots) != 1 or (entry.external_attr >> 16) & 0o170000 == 0o120000:raise ValueError('Невалидна структура или връзка в ZIP.')
            if len(parts) == 1:
                if not entry.is_dir():raise ValueError('Липсва основна папка в ZIP.')
                continue
            relative = PurePosixPath(*parts[1:])
            key = str(relative).casefold()
            if key in seen:raise ValueError('Повтарящ се ZIP път.')
            seen.add(key)
            if entry.is_dir():continue
            if entry.file_size > 16_000_000:raise ValueError('Прекалено голям файл на плъгин.')
            destination = target.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as source, destination.open('xb') as output:
                remaining = entry.file_size
                while chunk := source.read(min(65536, remaining + 1)):
                    remaining -= len(chunk)
                    if remaining < 0:raise ValueError('Невалиден ZIP размер.')
                    output.write(chunk)


class Downloads:
    def __init__(self, manager):
        self.manager = manager
        self.lock = threading.Lock()
        self.cache = manager.service.store.folder/'plugin-catalog.json'
        self.problem = None
        try:self.entries = validate(read_json(self.cache, read_json(ROOT/'docs/plugin-catalog.json', {})))
        except ValueError:self.entries = []

    def refresh(self):
        with self.lock:
            try:
                document = json.loads(fetch(CATALOG_URL, 300_000))
                entries = validate(document)
                atomic_json(self.cache, document)
                self.entries = entries; self.problem = None
            except Exception:
                self.problem = 'Каталогът в GitHub е недостъпен. Показан е последният запазен списък; опитай проверката отново.'
        return self.list()

    def list(self):
        installed = self.manager.records
        return [dict(item, installed=item['id'] in installed,
                     updateAvailable=item['id'] in installed and newer_version(installed[item['id']]['version'], item['version'])) for item in self.entries]

    def entry(self, ident):
        item = next((entry for entry in self.entries if entry['id'] == ident), None)
        if item is None:
            raise ValueError('Плъгинът не е в официалния каталог. Обнови списъка.')
        return item

    def download(self, item, target):
        data = fetch(f'https://codeload.github.com/{REPOSITORY}/zip/{item["commit"]}', MAX_ARCHIVE)
        if hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ValueError('Изтеглянето не съвпада с проверения ZIP. Обнови каталога.')
        unpack(data, target)
        manifest = self.manager.manifest(target)
        if manifest['id'] != item['id'] or manifest['version'] != item['version']:
            raise ValueError('Версията или името на плъгина не съвпада с каталога.')
        return manifest

    def install(self, ident):
        with self.lock:
            item = self.entry(ident)
            if ident in self.manager.records:raise FileExistsError('Първо изтрий инсталираната версия. Настройките се запазват.')
            with tempfile.TemporaryDirectory(prefix='plugin-download-', dir=self.cache.parent) as folder:
                target = Path(folder)
                self.download(item, target)
                with self.manager.lock:return self.manager.install(target)

    @contextmanager
    def stage_update(self, ident, commit):
        with self.lock:
            item = self.entry(ident)
            current = self.manager.records.get(ident)
            if item['commit'] != commit or current is None or not newer_version(current['version'], item['version']):
                raise ValueError('Версията е сменена или вече е инсталирана. Обнови списъка.')
        with tempfile.TemporaryDirectory(prefix='.download-', dir=self.manager.folder) as folder:
            staged = Path(folder)/'update'
            package = staged/'package'
            package.mkdir(parents=True)
            manifest = self.download(item, package)
            atomic_json(staged/'revisions.json', dict(old=current['uiRevision'], new=manifest['uiRevision']))
            yield staged

    def queue_update(self, ident, staged):
        with self.manager.lock:
            current = self.manager.manifest(within(self.manager.folder, ident))
            if current['uiRevision'] != read_json(staged/'revisions.json')['old']:
                raise ValueError('Инсталираният плъгин е променен. Провери папката и опитай отново.')
            staged.rename(self.manager.folder/('.update-'+ident))

    def apply_pending(self):
        """Replace packages before loading any plug-in in the restarted desktop process."""
        problems = []
        for pending in self.manager.folder.glob('.update-*'):
            ident = pending.name.removeprefix('.update-')
            if not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', ident) or not pending.is_dir() or pending.is_symlink() or pending.is_junction():
                continue
            try:
                self.apply_update(ident, pending)
            except (OSError, ValueError, TypeError) as error:
                problems.append(f'{ident}: {error}')
        return problems

    def apply_update(self, ident, pending):
        package, previous = pending/'package', pending/'previous'
        destination = self.manager.folder/ident
        revisions = pending/'revisions.json'
        if package.exists():
            try:
                expected = read_json(revisions, {})
                new = self.manager.manifest(within(pending, 'package'))
                old_path = within(pending, 'previous') if previous.exists() else within(self.manager.folder, ident)
                old = self.manager.manifest(old_path)
                if not isinstance(expected, dict) or new['id'] != ident or old['id'] != ident or new['uiRevision'] != expected.get('new') or old['uiRevision'] != expected.get('old'):
                    raise ValueError('Файловете на плъгина са променени. Обновяването е прекратено.')
                if not previous.exists():
                    destination.rename(previous)
                package.rename(destination)
            except (OSError, ValueError, TypeError):
                if previous.exists() and not destination.exists():
                    previous.rename(destination)
                pending.rename(self.manager.folder/('.failed-update-'+uuid.uuid4().hex))
                raise
        if previous.exists():
            if not destination.exists():
                previous.rename(destination)
                raise ValueError('Обновяването е прекъснато. Възстановена е предишната версия.')
            backup = self.cache.parent/'RemovedPlugins'/(ident+'-'+uuid.uuid4().hex)
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(previous), str(backup))
        revisions.unlink(missing_ok=True)
        pending.rmdir()
