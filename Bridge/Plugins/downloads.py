"""Official GitHub catalogue and bounded, verified, non-executing downloads."""
from __future__ import annotations
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import tempfile
import threading
import urllib.request
import urllib.parse
import zipfile

from Backend.Foundation.runtime import ROOT, read_json, atomic_json

REPOSITORY = 'simonimonov-spec/astro-archive'
CATALOG_URL = f'https://raw.githubusercontent.com/{REPOSITORY}/main/docs/plugin-catalog.json'
MAX_ARCHIVE = 32_000_000


def fetch(url, limit):
    request = urllib.request.Request(url, headers={'User-Agent': 'AstroArchive', 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=12) as response:
        final = urllib.parse.urlsplit(response.url)
        if final.scheme != 'https' or final.hostname not in {'raw.githubusercontent.com', 'api.github.com', 'codeload.github.com'}:
            raise ValueError('Невалиден адрес за изтегляне.')
        data = response.read(limit + 1)
        if len(data) > limit:raise ValueError('Изтеглянето надвишава допустимия размер.')
        return data


def validate(document):
    if not isinstance(document, dict) or document.get('format') != 1 or not isinstance(document.get('plugins'), list) or len(document['plugins']) > 100:
        raise ValueError('Невалиден каталог на плъгините.')
    seen = set()
    for item in document['plugins']:
        if not isinstance(item, dict):raise ValueError('Невалиден запис в каталога.')
        ident = item.get('id', '')
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', ident) or ident in seen or ident == 'ai-training':
            raise ValueError('Невалиден публичен плъгин.')
        seen.add(ident)
        if item.get('repository') != REPOSITORY or not re.fullmatch(r'[0-9a-f]{40}', item.get('commit', '')) or not re.fullmatch(r'[0-9a-f]{64}', item.get('sha256', '')):
            raise ValueError('Каталогът трябва да сочи проверено официално изтегляне.')
        for field in ('name', 'description', 'version'):
            if not isinstance(item.get(field), str) or not item[field] or len(item[field]) > 2000:raise ValueError('Непълно описание на плъгин.')
    return document['plugins']


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
                     updateAvailable=item['id'] in installed and installed[item['id']]['version'] != item['version']) for item in self.entries]

    def install(self, ident):
        with self.lock:
            item = next((e for e in self.entries if e['id'] == ident), None)
            if item is None:raise ValueError('Плъгинът не е в официалния каталог. Обнови списъка.')
            if ident in self.manager.records:raise FileExistsError('Първо изтрий инсталираната версия. Настройките се запазват.')
            data = fetch(f'https://codeload.github.com/{REPOSITORY}/zip/{item["commit"]}', MAX_ARCHIVE)
            if hashlib.sha256(data).hexdigest() != item['sha256']:raise ValueError('Изтеглянето не съвпада с проверения ZIP. Обнови каталога.')
            with tempfile.TemporaryDirectory(prefix='plugin-download-', dir=self.cache.parent) as folder:
                target = Path(folder)
                unpack(data, target)
                manifest = self.manager.manifest(target)
                if manifest['id'] != ident or manifest['version'] != item['version']:raise ValueError('Версията или името на плъгина не съвпада с каталога.')
                with self.manager.lock:return self.manager.install(target)
