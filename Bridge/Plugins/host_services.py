"""Generic plugin SDK for UI assets, named commands and ID-based file transfers.

This module does not listen on a network or authenticate browser clients.
"""
from __future__ import annotations
import copy,mimetypes,re,threading
from pathlib import Path
from Backend.Foundation.runtime import ROOT,within
from Backend.Fits.images import IMAGES

class HostServices:
    def __init__(self,service):self.service=service;self.owners=set();self.lock=threading.RLock()
    def asset(self,route):
        if route.startswith('analysis-image/'):
            return self.service.quality.coverage_image(route.partition('/')[2]),'image/png'
        if route.startswith('plugin-image/'):
            parts=route.split('/')
            if len(parts)!=3:raise ValueError('Невалиден ресурс на плъгин.')
            return self.service.plugins.image_asset(parts[1],parts[2]),'image/png'
        if route.startswith('viewer-image/'):
            ident=route.partition('/')[2]
            if not re.fullmatch('[a-f0-9]{32}',ident):raise ValueError('Невалиден преглед.')
            data=self.service.viewer.assets.get(ident)
            if not data:raise FileNotFoundError('Прегледът е затворен.')
            return data,'image/png'
        route=route or 'index.html'
        if route.split('/')[0] in ('Archive','Analysis','Processing'):
            first,_,tail=route.partition('/');relative=f'{first}/Web/{tail}'
        else:relative='Shared/Web/'+route
        path=within(ROOT/'UI',relative)
        if not path.is_file() or path.suffix.lower() not in ('.html','.js','.css','.svg','.png','.jpg','.woff2','.ico'):raise FileNotFoundError('Липсва UI ресурс.')
        mime='text/javascript' if path.suffix=='.js' else mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        return path.read_bytes(),mime
    def download(self,ident):
        if not isinstance(ident,str) or len(ident)>80:raise ValueError('Невалиден файл.')
        path=self.service.workflow.artifacts.path(ident) if ident.startswith('data-') else self.service.catalog.path(ident)
        if not path.is_file():raise FileNotFoundError('Файлът вече не е наличен.')
        return path
    def upload(self,owner,ident,name,archive):
        if not re.fullmatch('[a-f0-9]{32}',ident):raise ValueError('Невалидно качване.')
        if not isinstance(name,str) or Path(name).name!=name or any(c in name for c in '/\\:\x00\r\n') or len(name)>200:raise ValueError('Невалидно име.')
        self.service.settings.archive(archive)
        path=within(self.service.plugins.settings_path(owner).parent/'Uploads',ident+'/'+name)
        if path.suffix.lower() not in IMAGES:raise ValueError('Неподдържано изображение.')
        def work(cancel,progress):
            result=self.service.catalog.import_files([path],cancel,progress,forced_archive=archive)
            # Keep the uploaded copy available for retry if import failed or was
            # cancelled. Remove it only once the archive has an imported/duplicate
            # record and no per-file errors were reported.
            if not result['errors'] and (result['imported'] or result['duplicates']):
                path.unlink(missing_ok=True)
                try:path.parent.rmdir()
                except OSError:pass
            return result
        return self.service.task('archive','Дистанционно внасяне · '+name,work,'important')
    def invoke(self,owner,action,p):
        if action=='configuration':return self.service.configuration()
        if action=='bootstrap':return self.service.bootstrap()
        if action=='settings':return copy.deepcopy(self.service.settings.value)
        if action=='document':return copy.deepcopy(self.service.plugins.active(p['pluginId'])['documents'].get(p['contributionId'],[]))
        if action=='core':
            method=p['method'];parameters=p.get('params',{})
            if method=='image.open' and not parameters.get('id'):
                self.service.catalog.resolve(parameters['archiveId'],parameters['relativePath'])
                with self.service.store.db() as db:record=db.execute('SELECT id FROM files WHERE archive=? AND relative=? AND deleted=0',(parameters['archiveId'],parameters['relativePath'])).fetchone()
                if record:parameters=dict(id=record[0])
            return self.service.call(method,parameters)
        if action=='events':return self.service.events.since(int(p.get('after',0)))
        if action=='asset':return self.asset(p['route'])
        if action=='download':return self.download(p['id'])
        if action=='upload':return self.upload(owner,p['id'],p['name'],p['archiveId'])
        if action=='sequence':return self.service.events.sequence
        if action=='tasks':return self.service.jobs.snapshot()
        if action=='desktop.available':return bool(self.service.desktop_actions)
        if action=='desktop':
            callback=self.service.desktop_actions.get(p.get('action'))
            if not callback:raise ValueError('Действието е налично само в приложението.')
            callback();return dict(accepted=True)
        if action=='hold':
            with self.lock:
                if p.get('active'):self.owners.add(owner)
                else:self.owners.discard(owner)
            return dict(active=bool(self.owners))
        raise ValueError('Непозната операция на хост SDK.')
