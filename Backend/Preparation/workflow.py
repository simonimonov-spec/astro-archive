"""Copy-only export and tracked external workspaces with explicit result return."""
from __future__ import annotations
from Backend.Foundation.branding import marker as archive_marker, session_marker_names
import csv, io, json, os, shutil, subprocess, threading, time, uuid, zipfile
from pathlib import Path
from Backend.Foundation.runtime import ROOT,within, digest, atomic_json, read_json
from Backend.Fits.images import IMAGES, FITS, metadata, component
from Backend.Preparation.artifacts import Artifacts,DATA_FORMATS
from Backend.Preparation.provenance import context as result_context

class Workflow:
    def __init__(self,catalog,jobs):self.catalog=catalog;self.store=catalog.store;self.jobs=jobs;self.gate=threading.Lock();self.artifacts=Artifacts(self.store,catalog.events)
    def record(self,ident):return self.artifacts.record(ident) if ident.startswith('data-') else self.catalog.record(ident)
    def path(self,ident):return self.artifacts.path(ident) if ident.startswith('data-') else self.catalog.path(ident)
    def list(self):
        with self.store.db() as db:rows=[json.loads(r[0]) for r in db.execute('SELECT document FROM workflows')]
        return sorted(rows,key=lambda j:j.get('queueOrder',j.get('started',0)),reverse=True)
    def reorder(self,ident,delta):
        queue=[j for j in reversed(self.list()) if j['state']=='queued'];at=next((i for i,j in enumerate(queue) if j['id']==ident),None)
        if at is None:raise ValueError('Само чакащите задачи могат да се подреждат.')
        other=at+(-1 if delta<0 else 1)
        if 0<=other<len(queue):
            a,b=queue[at],queue[other];a['queueOrder'],b['queueOrder']=b.get('queueOrder',b['started']),a.get('queueOrder',a['started']);self.save(a);self.save(b)
        return dict(updated=True)
    def save(self,document):
        with self.store.db() as db:db.execute('INSERT OR REPLACE INTO workflows VALUES(?,?)',(document['id'],json.dumps(document,ensure_ascii=False)))
        self.catalog.events.emit('preparation.changed')
    def get(self,ident):
        job=next((j for j in self.list() if j['id']==ident),None)
        if not job:raise ValueError('Непозната задача.')
        return job
    def validate_folder(self,folder,archives=None,create=True):
        folder=Path(folder).resolve()
        if folder==Path(folder.anchor) or folder.is_relative_to(ROOT) or ROOT.is_relative_to(folder):raise ValueError('Избери отделна работна папка извън програмата.')
        for a in archives if archives is not None else self.catalog.settings.value['archives']:
            if not a['path']:continue
            root=Path(a['path']).resolve()
            if folder.is_relative_to(root) or root.is_relative_to(folder):raise ValueError('Работната папка не може да съвпада с архив или негов родител.')
        if create:folder.mkdir(parents=True,exist_ok=True)
        return folder
    def export(self,ids,folder,cancel,progress,format='original'):
        import numpy as np
        from astropy.io import fits
        from PIL import Image
        from Backend.Fits.images import pixels
        folder=self.validate_folder(folder);copied=[]
        for n,ident in enumerate(dict.fromkeys(ids),1):
            if cancel.is_set():raise InterruptedError()
            source=self.path(ident);stem=component(self.record(ident).get('name',source.stem));stem=Path(stem).stem
            if ident.startswith('data-') and format!='original':raise ValueError('Отчетите се изнасят в собствения им формат. Избери „Оригинален формат“.')
            extension=source.suffix if format=='original' else '.'+format
            target=folder/(stem+extension)
            if target.exists():target=folder/(stem+'-'+ident[:8]+extension)
            if target.exists():raise FileExistsError('Изходният файл вече съществува.')
            owned=False
            try:
                descriptor=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY|getattr(os,'O_BINARY',0));owned=True
                with os.fdopen(descriptor,'wb') as output:
                    if format=='original':
                        with source.open('rb') as src:shutil.copyfileobj(src,output,4*1024*1024)
                    elif format=='fits':
                        data,header,_=pixels(source,dtype=None)
                        if data.ndim==3:data=np.moveaxis(data,-1,0)
                        fits.PrimaryHDU(data,header=fits.Header(header)).writeto(output)
                    elif format in ('tiff','png'):
                        data,_,_=pixels(source,dtype=None)
                        integral=bool(np.all(np.isfinite(data)) and data.min()>=0 and np.all(data==np.floor(data)))
                        if data.ndim==3:
                            if not integral or data.max()>255:raise ValueError('Цветните PNG/TIFF се изнасят без загуба само при стойности 0…255. За научни цветни данни избери FITS/NPZ или оригинал.')
                            image=Image.fromarray(data.astype(np.uint8))
                        elif format=='tiff':
                            converted=data.astype(np.float32)
                            if not np.all((data==converted)|(~np.isfinite(data)&~np.isfinite(converted))):raise ValueError('Този TIFF float32 би загубил точност. Избери оригинал, FITS или NPZ.')
                            image=Image.fromarray(converted)
                        else:
                            if not integral or data.max()>65535:raise ValueError('PNG без загуба изисква целочислени монохромни стойности 0…65535. Използвай FITS за други данни.')
                            image=Image.fromarray(data.astype(np.uint16))
                        image.save(output,format='TIFF' if format=='tiff' else 'PNG')
                    elif format=='npz':
                        data,header,_=pixels(source,dtype=None);np.savez_compressed(output,image=data,header=json.dumps(header,default=str))
                    else:raise ValueError('Неподдържан формат за изображения.')
                if format=='original' and digest(target)!=digest(source):raise OSError('Копието не съвпада.')
                copied.append(str(target))
            except BaseException:
                # Only this export's newly created target can be removed.
                if owned:target.unlink(missing_ok=True)
                raise
            progress(n,len(ids),'Изнасяне на работни копия')
        self.store.history('export',ids=ids,files=copied,format=format);return dict(files=copied)
    def enqueue(self,ids,options):
        groups={}
        for ident in dict.fromkeys(ids):
            row=self.record(ident);m=row['meta'];key=(m['telescope'],m['object'],m['night'] if options.get('stage',0)==0 else '')
            groups.setdefault(key,[]).append(ident)
        queued=[]
        for key,values in groups.items():
            job=dict(id=uuid.uuid4().hex,state='queued',ids=values,telescope=key[0],object=key[1],nights=[key[2]] if key[2] else [],options=options,started=time.time(),queueOrder=time.time())
            self.save(job);queued.append(job)
        return dict(jobs=queued)
    def start_queued(self,ident,cancel,progress):
        job=self.get(ident);o=job['options']
        if job['state']!='queued':raise ValueError('Задачата не чака стартиране.')
        return self.start(job['ids'],o['programId'],cancel,progress,o.get('asStack',True),o.get('resultArchive','stacks'),o.get('calibrations',True),ident,o.get('mode','return')!='application')
    def start(self,ids,program_id,cancel,progress,as_stack=True,result_archive='stacks',calibrations=True,queue_id=None,expects_return=True):
        if not self.gate.acquire(blocking=False):raise ValueError('Друга задача подготвя работната папка.')
        try:
            if any(j['state'] in ('copying','waiting','returning','cleanup-required') for j in self.list()):raise ValueError('Приключи или откажи активната задача първо.')
            program=next((p for p in self.catalog.settings.value['programs'] if p['id']==program_id),None)
            if not program:raise ValueError('Избери запазена програма от настройките.')
            executable=Path(program['executable'])
            if not executable.is_file() or executable.suffix.lower()!='.exe':raise ValueError('Не е намерен изпълнимият файл на програмата.')
            folder=self.validate_folder(program['workingFolder'])
            if (archive_marker(folder,'session')).exists():raise ValueError('В тази папка има неприключена сесия. Възстанови я от списъка със задачи.')
            rows=[self.record(i) for i in dict.fromkeys(ids)]
            if not rows:raise ValueError('Няма избрани кадри.')
            objects={r['meta']['object'] for r in rows};telescopes={r['meta']['telescope'] for r in rows}
            if len(objects)!=1 or len(telescopes)!=1:raise ValueError('Една задача трябва да съдържа един обект от един телескоп.')
            baseline={};baseline_dirs=[]
            for parent,dirs,names in os.walk(folder,followlinks=False):
                if Path(parent)!=folder:baseline_dirs.append(Path(parent).relative_to(folder).as_posix())
                dirs[:]=[d for d in dirs if not (Path(parent)/d).is_symlink() and not getattr(Path(parent)/d,'is_junction',lambda:False)()]
                for name in names:
                    candidate=Path(parent)/name
                    if candidate.is_symlink():continue
                    p=within(folder,candidate.relative_to(folder).as_posix());s=p.stat();baseline[p.relative_to(folder).as_posix()]=dict(size=s.st_size,mtime=s.st_mtime_ns)
            job=dict(id=queue_id or uuid.uuid4().hex,state='copying',ids=[r['id'] for r in rows],program=program,workspace=str(folder),
                object=next(iter(objects)),telescope=next(iter(telescopes)),nights=sorted({night for r in rows for night in (r['meta'].get('sourceNights') or [r['meta']['night']])}),
                baseline=baseline,baselineDirectories=baseline_dirs,created=[],createdDirectories=[],dataOnly=all(r['meta']['kind']=='data' for r in rows),expectsReturn=expects_return,asStack=bool(as_stack),resultArchive='stacks' if as_stack else result_archive,started=time.time())
            if queue_id:job['queueOrder']=self.get(queue_id).get('queueOrder',time.time())
            self.save(job);atomic_json(archive_marker(folder,'session'),dict(id=job['id'],profile=str(self.store.path)))
            sources=[(r,'Data' if job['dataOnly'] else 'Lights') for r in rows]
            if calibrations and not job['dataOnly']:
                calibration_kinds={'dark','flat','bias'} if calibrations is True else set(calibrations)&{'dark','flat','bias'}
                for r in self.catalog.query(dict(telescope=job['telescope'])):
                    kind=r['meta']['kind']
                    if kind in calibration_kinds and r['meta']['night'] in job['nights']:
                        sources.append((r,{'dark':'Darks','flat':'Flats','bias':'Biases'}[kind]))
            try:
                for n,(row,group) in enumerate(sources,1):
                    if cancel.is_set():raise InterruptedError()
                    destination=folder/group
                    if not destination.exists():destination.mkdir();job['createdDirectories'].append(group);self.save(job)
                    source=self.path(row['id']);target=destination/row.get('name',source.name)
                    if target.exists():target=destination/(source.stem+'-'+row['id'][:8]+source.suffix)
                    target=within(folder,target.relative_to(folder).as_posix(),existing=False)
                    rel=target.relative_to(folder).as_posix()
                    # Persist ownership before opening; a failed partial copy is recoverable.
                    if target.exists():raise FileExistsError(str(target))
                    with target.open('xb') as dst:
                        job['created'].append(rel);self.save(job)
                        with source.open('rb') as src:shutil.copyfileobj(src,dst,4*1024*1024)
                    source_digest=digest(source)
                    if digest(target)!=source_digest:raise OSError('Работното копие не съвпада.')
                    job.setdefault('createdFingerprints',{})[rel]=source_digest;self.save(job)
                    progress(n,len(sources),'Копиране в Lights и калибрационните папки')
                # Keep external software open across jobs. Do not kill it on cancel.
                active=any(j.get('program',{}).get('id')==program_id and self.process_alive(j.get('pid')) for j in self.list() if j['id']!=job['id'])
                if not active:
                    process=subprocess.Popen([str(executable)],cwd=str(folder));job['launched']=True;job['pid']=process.pid if isinstance(process.pid,int) else None
                job.update(state='waiting',message='Работи във външната програма, после натисни „Готово“.');self.save(job)
                return job
            except BaseException as error:
                job.update(state='cleanup-required',problem=str(error));self.save(job);raise
        finally:self.gate.release()
    def outputs(self,ident):
        job=self.get(ident);folder=Path(job['workspace']);result=[]
        for parent,dirs,names in os.walk(folder,followlinks=False):
            dirs[:]=[d for d in dirs if not (Path(parent)/d).is_symlink() and not getattr(Path(parent)/d,'is_junction',lambda:False)()]
            for name in names:
                p=Path(parent)/name;relative=p.relative_to(folder).as_posix()
                if relative in session_marker_names():continue
                if p.is_symlink():continue
                p=within(folder,relative);s=p.stat()
                result.append(dict(id=relative,name=relative,size=s.st_size,input=relative in job['created'],preexisting=relative in job['baseline'],supported=p.suffix.lower() in IMAGES|DATA_FORMATS,mtime=s.st_mtime_ns))
        return dict(files=result,job=job)
    def finish(self,ident,outputs,cancel,progress,confirmed=False):
        if not confirmed:raise ValueError('Избери резултатите и потвърди прибирането.')
        if not self.gate.acquire(blocking=False):raise ValueError('Папката се използва от друга операция.')
        try:
            job=self.get(ident);folder=Path(job['workspace'])
            if job['state'] not in ('waiting','returning','cleanup-required'):raise ValueError('Тази задача не чака резултат.')
            if not job.get('expectsReturn',True):raise ValueError('Тази задача е без връщане на резултати.')
            available={f['id']:f for f in self.outputs(ident)['files']};paths=[]
            for output in outputs:
                f=available.get(output)
                if not f or f['input'] or f['preexisting'] or not f['supported']:raise ValueError('Избери готово изображение, а не входно копие.')
                paths.append(within(folder,output))
            if not paths and not job.get('imported'):raise ValueError('Избери поне един резултат.')
            job['state']='returning';self.save(job)
            if not job.get('imported'):
                context=result_context([self.record(i) for i in job['ids']],job['asStack'])
                if not job['asStack']:
                    destination=self.catalog.settings.archive(job['resultArchive']);kind=destination.get('type',destination['id'])
                    context['kind']='photo' if kind=='photos' else 'frame' if kind=='frames' else None
                result=dict(imported=[],duplicates=[],errors=[])
                # Journal every returned file so a retry cannot re-import earlier files.
                returned=job.setdefault('returned',{})
                for index,path in enumerate(paths,1):
                    relative=path.relative_to(folder).as_posix()
                    if relative in returned:continue
                    if cancel.is_set():raise InterruptedError()
                    if path.suffix.lower() in DATA_FORMATS:
                        added=self.artifacts.add(path,context);part=dict(imported=[added['id']],duplicates=[],errors=[])
                    else:part=self.catalog.import_files([str(path)],cancel,progress,job['resultArchive'],context)
                    for key in result:result[key].extend(part[key])
                    if not part['errors']:
                        returned[relative]=part['imported'] or ['duplicate']
                        # Journal before reading the archived copy: a retry must not
                        # import twice if it becomes unavailable during hashing.
                        self.save(job)
                        if part['imported']:
                            job.setdefault('returnedFingerprints',{})[relative]=digest(self.path(part['imported'][0]));self.save(job)
                        else:
                            # import_files only reports a duplicate after matching the
                            # complete source fingerprint, so use that verified hash
                            # to authorize removal of this returned workspace copy.
                            job.setdefault('returnedFingerprints',{})[relative]=digest(path);self.save(job)
                    progress(index,len(paths),'Прибиране на избраните резултати')
                if result['errors']:raise OSError('Прибирането не е завършено. Работните файлове са запазени: '+json.dumps(result['errors'],ensure_ascii=False))
                job['imported']=[i for values in returned.values() for i in values];job['outputs']=outputs;self.save(job)
                if not returned:raise ValueError('Няма успешно прибран резултат.')
            self.cleanup(job,include_new_outputs=True)
            job.update(state='completed',finished=time.time());self.save(job)
            return job
        finally:self.gate.release()
    def cleanup(self,job,include_new_outputs=False):
        folder=Path(job['workspace']);marker=read_json(archive_marker(folder,'session'),{})
        if marker.get('id')!=job['id']:raise ValueError('Работната папка няма валиден маркер за тази задача.')
        listing=self.outputs(job['id'])['files']
        existing_dirs=set(job.get('baselineDirectories',[]))
        for rel in job['baseline']:
            existing_dirs.update(p.as_posix() for p in Path(rel).parents if p.as_posix()!='.')
        returned=set(job.get('returned',{})) if include_new_outputs else set()
        if include_new_outputs:returned.update(job.get('abortFiles',[]))
        targets=[f for f in listing if f['id'] not in job['baseline'] and (f['id'] in returned or f['id'] in job['created'])]
        errors=[]
        for f in targets:
            try:
                p=within(folder,f['id']);s=p.stat()
                if s.st_mtime_ns!=f['mtime'] or s.st_size!=f['size']:raise OSError('Файлът още се променя.')
                fingerprints=job.get('returnedFingerprints',{}) if f['id'] in returned else job.get('createdFingerprints',{})
                expected=fingerprints.get(f['id'])
                if not expected:raise OSError('Липсва потвърдена контролна сума. Файлът е запазен за ръчна проверка.')
                if digest(p)!=expected:raise OSError('Файлът е променен. Запазен е за ръчна проверка.')
                after=p.stat()
                if after.st_mtime_ns!=s.st_mtime_ns or after.st_size!=s.st_size:raise OSError('Файлът още се променя.')
                p.unlink()
            except OSError as error:errors.append(dict(file=f['id'],problem=str(error)))
        if errors:
            job.update(state='cleanup-required',cleanupErrors=errors);self.save(job);raise OSError('Има заключени или променящи се файлове. Затвори ги във външната програма и повтори почистването.')
        # Only empty directories created during this session are removed.
        for parent,dirs,names in os.walk(folder,topdown=False,followlinks=False):
            p=Path(parent)
            if p!=folder and p.relative_to(folder).as_posix() not in existing_dirs:
                try:p.rmdir()
                except OSError:pass
        (archive_marker(folder,'session')).unlink()
    def complete(self,ident,confirmed=False):
        if not confirmed:raise ValueError('Потвърди, че външната работа е приключила.')
        if not self.gate.acquire(blocking=False):raise ValueError('Изчакай текущата операция.')
        try:
            job=self.get(ident)
            if job.get('expectsReturn',True) or job['state'] not in ('waiting','cleanup-required'):raise ValueError('Тази задача изисква прибиране на резултати.')
            self.cleanup(job);job.update(state='completed',finished=time.time());self.save(job);return job
        finally:self.gate.release()
    def cancel(self,ident,clean=False,confirmed=False):
        if not confirmed:raise ValueError('Необходимо е потвърждение.')
        if not self.gate.acquire(blocking=False):raise ValueError('Първо спри текущото копиране от индикатора за задачи и изчакай да приключи.')
        try:
            job=self.get(ident)
            if job.get('workspace'):
                if clean:
                    # On abort, remove every file created since this task began,
                    # including unselected outputs and intermediate files.
                    folder=Path(job['workspace'])
                    listing=self.outputs(ident)['files']
                    job['abortFiles']=[f['id'] for f in listing if f['id'] not in job['baseline'] and f['id'] not in job['created']]
                    fingerprints=job.setdefault('returnedFingerprints',{})
                    for relative in job['abortFiles']:
                        path=within(folder,relative)
                        try:fingerprints[relative]=digest(path)
                        except OSError:pass
                    self.save(job)
                    self.cleanup(job,include_new_outputs=True)
                else:
                    marker=archive_marker(Path(job['workspace']),'session')
                    if read_json(marker,{}).get('id')!=ident:raise ValueError('Маркерът не принадлежи на тази задача.')
                    marker.unlink()
            # With clean=True all files created by this task were removed above;
            # callers that explicitly request marker-only cancellation retain them.
            job.update(state='cancelled',finished=time.time());self.save(job);return job
        finally:self.gate.release()
    @staticmethod
    def process_alive(pid):
        if not isinstance(pid,int):return False
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
        handle=kernel.OpenProcess(0x1000,False,pid)
        if not handle:return False
        try:
            code=wintypes.DWORD();kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
            return bool(kernel.GetExitCodeProcess(handle,ctypes.byref(code)) and code.value==259)
        finally:
            kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle(handle)
