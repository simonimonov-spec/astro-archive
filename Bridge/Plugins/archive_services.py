"""Generic archive-folder SDK. Transport and credentials stay in each plugin."""
from __future__ import annotations
import copy, json, os, re, threading, uuid
from pathlib import Path
from Backend.Foundation.runtime import ROOT, atomic_json, read_json
from Backend.Foundation.branding import marker
from Backend.Archive.structure import preset, validate


class ArchiveServices:
    def __init__(self, service):
        self.service = service
        self.lock = threading.RLock()

    def folder(self, value):
        if not isinstance(value, str) or not value or len(value) > 2000 or '\x00' in value:
            raise ValueError('Невалидна архивна папка.')
        path = Path(value)
        if not path.is_absolute():
            raise ValueError('Избери пълен път до архивната папка.')
        path = path.resolve(strict=True)
        if not path.is_dir() or path == Path(path.anchor) or path.is_relative_to(ROOT) or ROOT.is_relative_to(path):
            raise ValueError('Избери отделна архивна папка извън програмата, а не целия диск.')
        return path

    def register(self, owner, parameters):
        """Declare an owned archive before a folder is chosen; never overwrite edits."""
        key, name, kind = parameters.get('key'), parameters.get('name','').strip(), parameters.get('kind','frames')
        if not isinstance(key,str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,39}',key):
            raise ValueError('Невалиден ключ на плъгин архива.')
        if not name or len(name)>120 or kind not in ('frames','stacks','photos'):
            raise ValueError('Въведи име и вид на плъгин архива.')
        if type(parameters.get('managedStructure',False)) is not bool:
            raise ValueError('Невалиден режим на структурата.')
        layout = validate(parameters.get('structure'),kind)
        changed = False
        with self.lock, self.service.settings.lock:
            previous = self.service.settings.value['archives']
            archives = copy.deepcopy(previous)
            row = next((a for a in archives if a.get('ownerPlugin')==owner and a.get('pluginArchiveKey')==key),None)
            if row is None and parameters.get('previousId'):
                row = next((a for a in archives if a['id']==parameters['previousId'] and a.get('ownerPlugin')==owner
                            and a.get('originPlugin')==owner and not a.get('pluginArchiveKey')),None)
                if row is not None:
                    row['pluginArchiveKey'] = key
                    row.setdefault('managedStructure',parameters.get('managedStructure',False))
                    if parameters.get('structure') and row.get('structure')==preset(row.get('type',kind)):
                        row['structure']=layout
                    changed = True
            if row is None:
                if len(archives)>=27:
                    raise ValueError('Достигнат е лимитът за допълнителни архиви.')
                ident = 'plugin-'+owner+'-'+key
                if any(a['id']==ident for a in archives):
                    raise ValueError('Идентификаторът на плъгин архива вече е зает.')
                row = dict(id=ident,name=name,type=kind,path='',identity=None,structure=layout,
                           ownerPlugin=owner,originPlugin=owner,pluginArchiveKey=key,
                           managedStructure=parameters.get('managedStructure',False))
                archives.append(row)
                changed = True
            if changed:
                try:
                    self.service.settings.value['archives'] = archives
                    self.service.settings.save()
                except BaseException:
                    self.service.settings.value['archives'] = previous
                    raise
            result = copy.deepcopy(row)
        if changed:
            self.service.catalog.browse.cache.clear()
            self.service.events.emit('archive.changed')
        return result

    def attach(self, owner, parameters):
        path = self.folder(parameters.get('path'))
        kind = parameters.get('kind', 'frames')
        name = parameters.get('name', '').strip()
        if kind not in ('frames', 'stacks', 'photos') or not name or len(name) > 120:
            raise ValueError('Въведи име и вид на архива.')
        with self.lock, self.service.settings.lock:
            archives = copy.deepcopy(self.service.settings.value['archives'])
            target = None
            if parameters.get('id'):
                target = next((a for a in archives if a['id']==parameters['id'] and a.get('ownerPlugin')==owner
                               and a.get('originPlugin')==owner),None)
                if target is None:
                    raise PermissionError('Плъгинът може да настройва само собствен архив.')
            for archive in archives:
                if archive is target:
                    continue
                if not archive.get('path'):
                    continue
                other = Path(archive['path']).resolve()
                if path == other:
                    if target is not None:
                        raise ValueError('Тази папка вече е зададена на друг архив.')
                    return dict(id=archive['id'], name=archive['name'], path=str(path), existing=True)
                if path.is_relative_to(other) or other.is_relative_to(path):
                    raise ValueError('Архивите не могат да бъдат вложени един в друг.')
            existing_marker=marker(path, 'archive')
            identity = read_json(existing_marker, {}).get('id') or parameters.get('identity') or uuid.uuid4().hex
            try:identity=uuid.UUID(identity).hex
            except (ValueError,TypeError,AttributeError):raise ValueError('Невалиден идентификатор на архива.')
            if parameters.get('initialize') is True and not existing_marker.exists():
                with existing_marker.open('x',encoding='utf-8') as stream:
                    json.dump(dict(id=identity,version=1),stream);stream.flush();os.fsync(stream.fileno())
            if target is None:
                archive = dict(id='shared-' + uuid.uuid4().hex, name=name, type=kind,
                               path=str(path), identity=identity, structure=preset(kind),
                               ownerPlugin=owner, originPlugin=owner)
                archives.append(archive)
            else:
                archive = target
                archive.update(path=str(path),identity=identity,name=name,type=kind)
            previous = self.service.settings.value['archives']
            try:
                self.service.settings.value['archives'] = archives
                self.service.settings.save()
            except BaseException:
                self.service.settings.value['archives'] = previous
                raise
        self.service.catalog.browse.cache.clear()
        self.service.events.emit('archive.changed')
        return dict(id=archive['id'], name=name, path=str(path), existing=False, archive=copy.deepcopy(archive))

    def invoke(self, owner, action, p):
        if action == 'register':
            return self.register(owner,p)
        if action == 'hold':
            if type(p.get('active')) is not bool:
                raise ValueError('Очаква се отметка за жизнения цикъл на плъгина.')
            with self.service.host_services.lock:
                if p['active']:self.service.host_services.owners.add(owner)
                else:self.service.host_services.owners.discard(owner)
            return dict(active=p['active'])
        if action == 'list':
            return dict(archives=[{k: copy.deepcopy(a.get(k)) for k in
                        ('id', 'name', 'path', 'type', 'identity', 'ownerPlugin', 'pluginArchiveKey', 'managedStructure')}
                        for a in self.service.settings.value['archives']])
        if action == 'choose':
            if not self.service.desktop_actions:
                raise ValueError('Изборът на папка е наличен само на компютъра.')
            return dict(path=str(self.service.choose_folder(str(p.get('title', 'Избери архивна папка'))[:120])))
        if action == 'choose-file':
            if not self.service.desktop_actions:raise ValueError('Изборът на файл е наличен само на компютъра.')
            kind=p.get('kind','report')
            if kind not in ('report','program'):raise ValueError('Неподдържан избор на файл.')
            types=[('Отчет / журнал','*.txt *.psv *.xml *.log')] if kind=='report' else [('Програма','*.exe')]
            path=self.service.dialogs.choose('file',title=str(p.get('title','Избери файл'))[:120],filetypes=types)
            return dict(path=str(path) if path else '')
        if action == 'attach':
            return self.attach(owner, p)
        if action == 'refresh':
            self.service.settings.archive(p['id'])
            return self.service.start_index(p['id'])
        if action == 'detach':
            with self.lock, self.service.settings.lock:
                archives = self.service.settings.value['archives']
                row = next((a for a in archives if a['id'] == p.get('id')), None)
                if not row or row.get('originPlugin') != owner or row['id'] in ('frames', 'stacks', 'photos'):
                    raise ValueError('Добавката може да отделя само собствените си допълнителни архиви.')
                self.service.settings.value['archives'] = [a for a in archives if a is not row]
                try:
                    self.service.settings.save()
                except BaseException:
                    self.service.settings.value['archives'] = archives
                    raise
            self.service.catalog.browse.cache.clear()
            self.service.events.emit('archive.changed')
            return dict(detached=True, filesDeleted=False)
        raise ValueError('Непозната операция на архивния SDK.')
