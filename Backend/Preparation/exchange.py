"""Restricted host I/O for external processing plugins, with owned workspaces."""
from __future__ import annotations
import json,os,re,shutil,threading,uuid
from pathlib import Path
from Backend.Foundation.runtime import within,atomic_json,read_json,digest
from Backend.Fits.images import FITS,metadata
from Backend.Preparation.provenance import context

class Exchange:
    def __init__(self,service):self.service=service;self.lock=threading.RLock()
    def root(self,owner):
        if not re.fullmatch('[a-z][a-z0-9_-]{0,63}',owner):raise ValueError('Невалиден плъгин.')
        root=self.service.store.folder/'PluginWork'/owner;root.mkdir(parents=True,exist_ok=True);return root
    def session(self,owner,ident):
        if not re.fullmatch('[0-9a-f]{32}',ident):raise ValueError('Невалидна работна сесия.')
        folder=within(self.root(owner),ident);document=read_json(within(folder,'session.json'))
        if not document or document.get('owner')!=owner or document.get('id')!=ident:raise ValueError('Сесията не принадлежи на добавката.')
        return folder,document
    def invoke(self,owner,action,p,cancel=None,progress=None):
        if action=='stage':return self.stage(owner,p['ids'],cancel,progress)
        if action=='describe':
            ids=p.get('ids',[])
            if not isinstance(ids,list) or len(ids)>50000 or any(not isinstance(i,str) or len(i)>80 for i in ids):raise ValueError('Невалиден подбор.')
            ids=list(dict.fromkeys(ids));rows=[]
            # Read only the selected metadata, never serialize the whole archive.
            with self.service.store.db() as db:
                for at in range(0,len(ids),500):
                    batch=ids[at:at+500];marks=','.join('?' for _ in batch)
                    for record in db.execute("SELECT f.*,d.decision,json_remove(f.meta,'$.headers','$.sourceFiles') AS summary FROM files f LEFT JOIN decisions d ON d.file=f.id WHERE f.deleted=0 AND f.id IN ("+marks+")",batch):
                        row=dict(record);row['meta']=json.loads(row.pop('summary'));rows.append(self.service.row(row))
            return dict(rows=rows)
        if action=='choose':
            kind=p.get('kind');exe=kind=='executable'
            if kind not in ('executable','master'):raise ValueError('Невалиден избор.')
            path=self.service.dialogs.choose('file',title='Siril CLI' if exe else 'Калибрационен master FITS',filetypes=[('Програма' if exe else 'FITS','*.exe' if exe else '*.fit *.fits *.fts')])
            return dict(path=path or '')
        if action=='graph':return self.service.provenance(p['id'])
        if action=='navigate':
            row=self.service.catalog.record(p['id']);return dict(navigation=dict(archiveId=row['archive'],relativePath=Path(row['relative']).parent.as_posix()))
        if action=='preview':
            folder,_=self.session(owner,p['session']);path=within(folder,p['relative'])
            if path.suffix.lower() not in FITS:raise ValueError('Избери FITS резултат.')
            return self.service.viewer.preview_file(path,1000)
        if action=='open':
            folder,_=self.session(owner,p['session']);return self.service.viewer.open(within(folder,p['relative']),can_navigate=False)
        if action=='import':return self.import_result(owner,p)
        if action=='discard':return self.discard(owner,p['session'])
        if action=='master':
            folder,_=self.session(owner,p['session']);source=Path(p['path'])
            if source.suffix.lower() not in FITS or not source.is_file() or source.is_symlink():raise ValueError('Невалиден master FITS.')
            name=p['kind']
            if name not in ('dark','flat','bias'):raise ValueError('Невалидна калибрация.')
            target=within(folder,'master-'+name+'.fit',existing=False)
            with target.open('xb') as output,source.open('rb') as input_:shutil.copyfileobj(input_,output,4*1024*1024)
            if digest(target)!=digest(source):raise OSError('Master копието не съвпада.')
            return dict(relative=target.name)
        raise ValueError('Непозната операция за външна обработка.')
    def stage(self,owner,ids,cancel=None,progress=None):
        ids=list(dict.fromkeys(ids));catalog=self.service.catalog
        if not ids:raise ValueError('Няма избрани кадри.')
        records=[catalog.record(i) for i in ids]
        if any(r.get('decision')=='excluded' for r in records):raise ValueError('Изключен кадър не може да бъде подаден автоматично.')
        if len({r['meta']['object'] for r in records})!=1:raise ValueError('Една сесия трябва да съдържа един обект.')
        ident=uuid.uuid4().hex;folder=self.root(owner)/ident;folder.mkdir();source=folder/'source';source.mkdir()
        document=dict(owner=owner,id=ident,sources=[],state='copying')
        atomic_json(folder/'session.json',document)
        try:
            for index,row in enumerate(records,1):
                if cancel and cancel.is_set():raise InterruptedError('Копирането е спряно.')
                path=catalog.path(row['id'])
                if path.suffix.lower() not in FITS:raise ValueError('Автоматичното стакване приема FITS кадри.')
                name=f'input_{index:06d}.fit';target=source/name
                with target.open('xb') as output,path.open('rb') as input_:shutil.copyfileobj(input_,output,4*1024*1024)
                if digest(target)!=digest(path):raise OSError('Работното копие не съвпада.')
                document['sources'].append(dict(id=row['id'],number=index,name=name,meta=row['meta'],size=row['size'],mtime=row['mtime'],fingerprint=digest(path)));atomic_json(folder/'session.json',document)
                if progress:progress(index,len(records),'Проверени работни копия за външния процес')
            document['state']='ready';atomic_json(folder/'session.json',document)
            return dict(session=ident,folder=str(folder),sources=document['sources'])
        except BaseException:
            try:self.discard(owner,ident)
            except (OSError,ValueError):pass
            raise
    def discard(self,owner,ident):
        folder,document=self.session(owner,ident)
        root=self.root(owner).resolve()
        if folder.is_symlink() or getattr(folder,'is_junction',lambda:False)() or folder.resolve().parent!=root:
            raise ValueError('Работната папка не е собствена сесия на плъгина.')
        for parent,dirs,names in os.walk(folder,followlinks=False):
            for name in dirs+names:
                path=Path(parent)/name
                if path.is_symlink() or getattr(path,'is_junction',lambda:False)():raise ValueError('Работната папка съдържа връзка и е запазена за проверка.')
        shutil.rmtree(folder)
        return dict(removed=True)
    def import_result(self,owner,p):
        if not p.get('confirmed'):raise ValueError('Оцени и потвърди резултата преди прибиране.')
        with self.lock:
            folder,document=self.session(owner,p['session']);path=within(folder,p['relative']);catalog=self.service.catalog
            if path.suffix.lower() not in FITS or path.is_relative_to(folder/'source'):raise ValueError('Избери готов FITS резултат.')
            used=p.get('usedIds')
            if not isinstance(used,list) or not used or set(used)-{s['id'] for s in document['sources']}:raise ValueError('Неизвестна връзка с използваните кадри.')
            sources=[catalog.record(i) for i in used];ctx=context(sources,True)
            # A new automation result must explicitly record its accepted contribution.
            if not ctx.get('provenanceOverride'):
                metas=[r['meta'] for r in sources];ctx['provenanceOverride']=dict(frames=sum(m.get('frames',1) for m in metas),totalExposure=sum(m['totalExposure'] for m in metas) if all(m.get('totalExposure') is not None for m in metas) else None)
            ctx['processing']=dict(plugin=owner,settings=p.get('settings',{}),excludedSources=[s['id'] for s in document['sources'] if s['id'] not in used])
            fingerprint=digest(path);result=catalog.import_files([path],threading.Event(),lambda *a:None,'stacks',ctx)
            if result['errors']:raise OSError(str(result['errors']))
            ids=result['imported']
            if not ids:
                with catalog.store.db() as db:ids=[r[0] for r in db.execute('SELECT id FROM files WHERE fingerprint=? AND archive=? AND deleted=0',(fingerprint,'stacks'))]
            if len(ids)!=1:raise ValueError('Не може еднозначно да се свърже прибраният стак.')
            if not result['imported'] and set(catalog.record(ids[0])['meta'].get('sourceFiles',[]))!=set(used):raise ValueError('Идентичен файл вече съществува с други източници. Запазеният произход не е променен; работният резултат остава за преглед.')
            document.update(state='imported',result=ids[0],usedIds=used);atomic_json(folder/'session.json',document)
            return dict(id=ids[0],archiveId='stacks',relativePath=catalog.record(ids[0])['relative'])
