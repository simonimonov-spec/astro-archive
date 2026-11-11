"""Persistent incremental catalog and archive actions, independent of the UI."""
from __future__ import annotations
from Backend.Foundation.branding import companion as companion_path, marker as archive_marker
import contextlib, datetime as dt, hashlib, json, os, shutil, threading, time, uuid, zipfile
from pathlib import Path
from Backend.Foundation.runtime import atomic_json, read_json, within, digest, parallel
from Backend.Fits.images import metadata, IMAGES, FITS, component, finite, missing_fields, METADATA_VERSION, METADATA_FIELDS
from Backend.Archive.structure import schema,destination,render,FIELDS,preset
from Backend.Archive.browse import BrowseIndex, blank, accumulate

def inspect_file(path):
    try:return dict(meta=metadata(path))
    except Exception as error:return dict(error=str(error))

class Catalog:
    def __init__(self,store,settings,events):
        self.store,self.settings,self.events=store,settings,events
        self.scan_locks={};self.scan_states={};self.operation_lock=threading.RLock()
        self.browse=BrowseIndex(self)
    def index_status(self,archive):
        state=self.scan_states.get(archive)
        if state is None:
            with self.store.db() as db:row=db.execute('SELECT document FROM archive_index WHERE archive=?',(archive,)).fetchone()
            state=json.loads(row[0]) if row else {}
            self.scan_states[archive]=dict(state)
        state=dict(state);lock=self.scan_locks.get(archive)
        state['building']=bool(lock and lock.locked())
        state['stale']=not state.get('complete',False) or state.get('metadataVersion')!=METADATA_VERSION
        return state
    def save_index_status(self,archive,state):
        self.scan_states[archive]=dict(state)
        with self.store.db() as db:db.execute('INSERT OR REPLACE INTO archive_index VALUES(?,?)',(archive,json.dumps(state,ensure_ascii=False)))
    def root(self,ident):
        a=self.settings.archive(ident);root=Path(a['path'])
        if not root.is_dir():
            # First try the same path on changed drive letters, using identity.
            identity=a.get('identity')
            if identity:
                suffix=root.parts[1:]
                for letter in 'DEFGHIJKLMNOPQRSTUVWXYZ':
                    candidate=Path(letter+':\\').joinpath(*suffix)
                    if read_json(archive_marker(candidate,'archive'),{}).get('id')==identity:
                        a['path']=str(candidate);self.settings.save();root=candidate;break
        if not root.is_dir():raise FileNotFoundError('Архивът не е достъпен. Посочи новото му местоположение от Настройки.')
        return root.resolve()
    def resolve(self,archive,relative):return within(self.root(archive),relative)
    def record(self,ident):
        with self.store.db() as db:row=db.execute('SELECT f.*,d.decision FROM files f LEFT JOIN decisions d ON d.file=f.id WHERE f.id=? AND f.deleted=0',(ident,)).fetchone()
        if not row:raise FileNotFoundError('Кадърът вече не е в активния архив.')
        row=dict(row);row['meta']=json.loads(row['meta']);return row
    def path(self,ident):
        row=self.record(ident);return self.resolve(row['archive'],row['relative'])
    def put(self,archive,path,meta,added=None,fingerprint=None,*,_scan_db=None):
        meta=dict(meta)
        if self.settings.archive(archive).get('type',archive)=='stacks' and meta.get('kind')=='frame':
            meta.update(kind='stack',framesKnown=bool(meta.get('frameCountRecorded')))
            if not meta['framesKnown'] and not any(finite(meta.get('headers',{}).get(k)) is not None for k in ('TOTEXP','TOTALEXP','EXPTOT','TEXPTIME')):meta['totalExposure']=None
        root=self.root(archive);rel=Path(path).resolve().relative_to(root).as_posix()
        with self.store.db() if _scan_db is None else contextlib.nullcontext(_scan_db) as db:
            previous=db.execute('SELECT id,added,fingerprint,size,mtime,meta FROM files WHERE archive=? AND relative=?',(archive,rel)).fetchone()
            if previous and (previous['size'],previous['mtime'])==(meta['size'],meta['mtime']):
                old_meta=json.loads(previous['meta'])
                for key in ('sourceFiles','sourceNights','provenanceOverride','sourceStackCount','telescopes','sourceStacks','combination','processing'):
                    if key in old_meta and key not in meta:meta[key]=old_meta[key]
            meta.update(meta.get('provenanceOverride',{}))
            if 'frames' in meta.get('provenanceOverride',{}):meta['framesKnown']=True
            meta['missingFields']=missing_fields(meta)
            ident=previous['id'] if previous else uuid.uuid4().hex
            db.execute('''INSERT INTO files VALUES(?,?,?,?,?,?,?,?,0) ON CONFLICT(archive,relative) DO UPDATE SET
                 size=excluded.size,mtime=excluded.mtime,meta=excluded.meta,deleted=0,
                 fingerprint=CASE WHEN files.size!=excluded.size OR files.mtime!=excluded.mtime THEN excluded.fingerprint ELSE COALESCE(excluded.fingerprint,files.fingerprint) END''',
                (ident,archive,rel,meta['size'],meta['mtime'],previous['added'] if previous else added or time.time(),fingerprint,json.dumps(meta,ensure_ascii=False)))
            # Each scan item remains individually durable as before, but reuses
            # one connection instead of opening and configuring one per file.
            if _scan_db is not None:db.commit()
        return ident
    def scan(self,archive,cancel,progress,verify=False,wait=False):
        lock=self.scan_locks.setdefault(archive,threading.Lock())
        if not lock.acquire(blocking=False):
            if not wait:raise ValueError('Този архив вече се обновява.')
            progress(0,1,'Изчакване на текущата проверка на архива')
            while not lock.acquire(timeout=.2):
                if cancel.is_set():raise InterruptedError('Проверката е отменена.')
        state=dict(state='running',complete=False,metadataVersion=METADATA_VERSION,totalFiles=0,scannedFiles=0,errors=[],verify=bool(verify));last_notice=0
        def publish(force=False):
            nonlocal last_notice
            if force or time.monotonic()-last_notice>=1:
                self.save_index_status(archive,state);self.events.emit('archive.summary.changed',archiveId=archive);last_notice=time.monotonic()
        try:
            root=self.root(archive);found={};errors=[];walk_errors=[];state['root']=str(root);publish(True)
            progress(0,1,'Откриване на кадрите в архива')
            def walk_error(error):walk_errors.append(dict(path=str(error.filename or root),problem=str(error)))
            for parent,dirs,files in os.walk(root,followlinks=False,onerror=walk_error):
                if cancel.is_set():raise InterruptedError()
                dirs[:]=[d for d in dirs if not d.startswith('.') and not (Path(parent)/d).is_symlink() and not getattr(Path(parent)/d,'is_junction',lambda:False)()]
                for name in files:
                    p=Path(parent)/name
                    if p.suffix.lower() in IMAGES and not p.name.endswith('.coverage.fits'):
                        try:
                            p=within(root,p.relative_to(root).as_posix());s=p.stat();found[p.relative_to(root).as_posix()]=(p,s.st_size,s.st_mtime_ns)
                        except (OSError,ValueError) as e:walk_errors.append(dict(path=str(p),problem=str(e)))
            errors.extend(walk_errors)
            fields=' AND '.join("json_type(meta,'$."+key+"') IS NOT NULL" for key in METADATA_FIELDS)
            with self.store.db() as db:known={r['relative']:r for r in db.execute("SELECT relative,size,mtime,deleted,COALESCE(json_extract(meta,'$.companionMtime'),0) AS companion,CASE WHEN json_valid(meta) THEN (json_extract(meta,'$.metadataVersion')=? AND "+fields+" AND json_type(meta,'$.indexProblem') IS NULL) ELSE 0 END AS complete FROM files WHERE archive=?",(METADATA_VERSION,archive))}
            def companion_changed(path,row):
                side=companion_path(path)
                return (side.stat().st_mtime_ns if side.exists() else 0)!=row['companion']
            changed=[str(p) for rel,(p,size,mtime) in found.items() if verify or rel not in known or known[rel]['deleted'] or not known[rel]['complete'] or (size,mtime)!=(known[rel]['size'],known[rel]['mtime']) or companion_changed(p,known[rel])]
            state.update(totalFiles=len(found),scannedFiles=len(found)-len(changed));publish(True)
            count=0
            with self.store.db() if changed else contextlib.nullcontext(None) as scan_db:
                for path,result in parallel(inspect_file,changed,self.settings.value['workers'],cancel):
                    count+=1
                    try:
                        if 'error' in result:raise ValueError(result['error'])
                        self.put(archive,Path(path),result['meta'],added=getattr(Path(path).stat(),'st_birthtime',time.time()),_scan_db=scan_db)
                    except (OSError,ValueError) as error:
                        scan_db.rollback()
                        errors.append(dict(path=path,problem=str(error)))
                        relative=Path(path).relative_to(root).as_posix()
                        scan_db.execute("UPDATE files SET meta=json_set(meta,'$.indexProblem',?) WHERE archive=? AND relative=? AND json_valid(meta)",(str(error),archive,relative));scan_db.commit()
                    state.update(scannedFiles=len(found)-len(changed)+count,errors=errors[:50]);publish()
                    if count%20==0 or count==len(changed):progress(count,len(changed),'Проверка и допълване на метаданните' if verify else 'Обновяване на записаните метаданни')
            if cancel.is_set():raise InterruptedError()
            with self.store.db() as db:
                # An inaccessible subtree is not evidence that its files were deleted.
                if not walk_errors:db.executemany('UPDATE files SET deleted=1 WHERE archive=? AND relative=?',[(archive,rel) for rel in known if rel not in found])
                missing=db.execute("SELECT relative,json_extract(meta,'$.missingFields') FROM files WHERE archive=? AND deleted=0 AND json_array_length(meta,'$.missingFields')>0",(archive,)).fetchall()
            state.update(state='completed',complete=not errors,totalFiles=len(found),scannedFiles=len(found),errorCount=len(errors),missingMetadataFiles=len(missing),missingExamples=[dict(path=r[0],fields=json.loads(r[1])) for r in missing[:50]],savedUtc=dt.datetime.now(dt.timezone.utc).isoformat(),errors=errors[:50])
            publish(True)
            self.store.history('scan',archive=archive,changed=len(changed),files=len(found),errors=errors)
            return dict(files=len(found),read=len(changed),cached=len(found)-len(changed),errors=errors,missingMetadataFiles=len(missing),missingExamples=state['missingExamples'],cacheFile=str(self.store.path))
        except BaseException as error:
            state.update(state='cancelled' if isinstance(error,InterruptedError) else 'failed',complete=False,problem=str(error));publish(True);raise
        finally:
            lock.release()
            with self.browse.lock:
                if archive in self.browse.cache:self.browse.cache[archive]['validated']=0
            self.events.emit('archive.changed');self.events.emit('archive.summary.changed',archiveId=archive)
    def query(self,filters=None,slim=False):
        filters=filters or {};where=['f.deleted=0'];args=[]
        for key in ['archive','telescope','object','night','kind']:
            value=filters.get(key)
            if value not in (None,''):
                where.append('f.archive=?' if key=='archive' else f"json_extract(f.meta,'$.{key}')=?");args.append(value)
        if filters.get('since'):where.append('f.added>=?');args.append(float(filters['since']))
        if filters.get('relative'):
            prefix=filters['relative'].replace('\\','/').rstrip('/')
            where.append('(f.relative=? OR substr(f.relative,1,?)=?)');args.extend([prefix,len(prefix)+1,prefix+'/'])
        fields="f.id,f.archive,f.relative,f.size,f.mtime,f.added,f.fingerprint,json_remove(f.meta,'$.headers','$.sourceFiles') AS meta,f.deleted" if slim else 'f.*'
        with self.store.db() as db:
            rows=db.execute('SELECT '+fields+',d.decision FROM files f LEFT JOIN decisions d ON d.file=f.id WHERE '+' AND '.join(where)+' ORDER BY f.relative',args).fetchall()
        result=[]
        for row in rows:
            r=dict(row);r['meta']=json.loads(r['meta']);result.append(r)
        return result
    @staticmethod
    def totals(rows):
        values=[r['meta'] for r in rows]
        return dict(files=len(rows),frames=sum(max(1,int(finite(m.get('frames'),1))) for m in values),
            exposure=sum(max(0,finite(m.get('totalExposure'),0)) for m in values),bytes=sum(r['size'] for r in rows),
            exposureUnknown=sum(finite(m.get('totalExposure')) is None or finite(m.get('totalExposure'),0)<0 for m in values),
            framesUnknown=sum(m.get('framesKnown') is False or finite(m.get('frames')) is None for m in values),
            metadataIncomplete=sum(bool(m.get('missingFields')) or bool(m.get('indexProblem')) for m in values))
    def details(self,rows,kind='folder',index=None):
        t=self.totals(rows);observed=sorted(r['meta']['observed'] for r in rows if r['meta'].get('observed'))
        nights=sorted({n for r in rows for n in (r['meta'].get('sourceNights') or [r['meta'].get('night','')]) if n})
        return dict(kind=kind,frames=t['frames'] if kind!='frame' else None,
            exposure=rows[0]['meta'].get('exposure') if len(rows)==1 else None,totalExposure=t['exposure'] if not rows or t['exposureUnknown']<len(rows) else None,
            dateKind='observation' if kind in ('frame','stack','night','day') else 'modified',
            firstObservation=nights[0]+'T00:00:00' if nights else observed[0] if observed else None,lastObservation=nights[-1]+'T00:00:00' if nights else observed[-1] if observed else None,
            minimumExposure=bool(t['exposureUnknown']),minimumFrames=bool(t['framesUnknown']),unknownExposure=t['exposureUnknown'],building=bool(index and index.get('building')),stale=bool(index and index.get('stale')),metadataIncomplete=t['metadataIncomplete'])
    def duplicate(self,archive,source,fingerprint):
        """Size -> cached 64 KiB prefix -> full hash; an exact match is mandatory."""
        def valid(row):
            try:
                path=self.resolve(archive,row['relative']);stat=path.stat()
                if (stat.st_size,stat.st_mtime_ns)!=(row['size'],row['mtime']):self.put(archive,path,metadata(path));return None
                return path
            except (OSError,ValueError):return None
        with self.store.db() as db:known=db.execute('SELECT * FROM files WHERE archive=? AND deleted=0 AND fingerprint=?',(archive,fingerprint)).fetchall()
        for row in known:
            if valid(row):return True
        size=source.stat().st_size
        with self.store.db() as db:
            candidates=db.execute('''SELECT f.id,f.relative,f.size,f.mtime,p.size AS ps,p.mtime AS pm,p.value AS prefix FROM files f LEFT JOIN prefixes p ON p.file=f.id
                WHERE f.archive=? AND f.deleted=0 AND f.size=? AND f.fingerprint IS NULL''',(archive,size)).fetchall()
        if not candidates:return False
        def first(path):
            with path.open('rb') as stream:return hashlib.sha256(stream.read(65536)).hexdigest()
        expected=first(source);prefixes=[];hashes=[];duplicate=False
        for row in candidates:
            path=valid(row)
            if path is None:continue
            short=row['prefix'] if (row['ps'],row['pm'])==(row['size'],row['mtime']) else first(path)
            if (row['ps'],row['pm'],row['prefix'])!=(row['size'],row['mtime'],short):prefixes.append((row['id'],row['size'],row['mtime'],short))
            if short!=expected:continue
            full=digest(path);hashes.append((full,row['id']))
            if full==fingerprint:duplicate=True;break
        with self.store.db() as db:
            db.executemany('INSERT OR REPLACE INTO prefixes VALUES(?,?,?,?)',prefixes)
            db.executemany('UPDATE files SET fingerprint=? WHERE id=?',hashes)
        return duplicate
    def import_files(self,paths,cancel,progress,forced_archive=None,context=None):
        imported=[];duplicates=[];errors=[];context=context or {};touched=set()
        for index,source in enumerate(map(Path,paths),1):
            if cancel.is_set():raise InterruptedError()
            try:
                if source.suffix.lower() not in IMAGES or source.is_symlink():raise ValueError('Неподдържан файл.')
                m=metadata(source);fingerprint=digest(source)
                archive=forced_archive or ('stacks' if m['kind']=='stack' else 'photos' if m['kind']=='photo' else 'frames')
                root=self.root(archive)
                with self.operation_lock:
                    if self.duplicate(archive,source,fingerprint):duplicates.append(str(source));continue
                    for key in ('object','telescope','night'):
                        if context.get(key):m[key]=context[key]
                    for key in ('sourceNights','sourceFiles','kind','provenanceOverride','sourceStackCount','telescopes','sourceStacks','combination','processing'):
                        if context.get(key) is not None:m[key]=context[key]
                    m.update(m.get('provenanceOverride',{}))
                    target=root/destination(self.settings,archive,m,source.name)
                    target.parent.mkdir(parents=True,exist_ok=True)
                    if target.exists():target=target.with_name(target.stem+'-'+fingerprint[:10]+target.suffix)
                    target=within(root,target.relative_to(root).as_posix(),existing=False)
                    temporary=target.with_name('.astroarchive-'+uuid.uuid4().hex+'.partial')
                    owned=False
                    try:
                        shutil.copy2(source,temporary)
                        if digest(temporary)!=fingerprint:raise OSError('Неуспешна проверка на копието.')
                        # Link the verified same-volume copy atomically where supported.
                        # Existing names are never replaced; other filesystems use an exclusive copy.
                        try:os.link(temporary,target);owned=True
                        except FileExistsError:raise
                        except OSError:
                            with target.open('xb') as output:
                                owned=True
                                with temporary.open('rb') as input_:shutil.copyfileobj(input_,output,4*1024*1024)
                        m.update(size=target.stat().st_size,mtime=target.stat().st_mtime_ns)
                        source_side=companion_path(source)
                        extra=read_json(source_side,{}) if source_side.exists() else {}
                        if source.suffix.lower() not in FITS:extra.setdefault('created',m.get('created'))
                        if context:extra.update({k:m[k] for k in ('object','telescope','night','sourceFiles','sourceNights','kind','provenanceOverride','sourceStackCount','telescopes','sourceStacks','combination','processing') if k in m})
                        if extra:
                            side=companion_path(target);atomic_json(side,extra);m['companionMtime']=side.stat().st_mtime_ns
                        imported.append(self.put(archive,target,m,fingerprint=fingerprint))
                        touched.add(archive)
                    except BaseException:
                        if owned:target.unlink(missing_ok=True);companion_path(target).unlink(missing_ok=True)
                        # Keep a complete temporary copy for disk-full recovery.
                        if temporary.exists() and temporary.stat().st_size==source.stat().st_size:
                            recovery=self.store.folder/'Recovery';recovery.mkdir(exist_ok=True)
                            saved=recovery/(uuid.uuid4().hex+'-'+source.name);shutil.copy2(temporary,saved)
                            atomic_json(saved.with_suffix(saved.suffix+'.json'),dict(source=str(source),destination=str(target)))
                        raise
                    finally:temporary.unlink(missing_ok=True)
            except Exception as error:errors.append(dict(path=str(source),problem=str(error)))
            finally:progress(index,len(paths),'Внасяне с проверка за дубликати')
        for archive in touched:self.browse.cache.pop(archive,None)
        self.events.emit('archive.changed');self.events.emit('archive.summary.changed')
        self.store.history('import',imported=imported,duplicates=duplicates,errors=errors)
        return dict(imported=imported,duplicates=duplicates,errors=errors)
    def decide(self,ids,decision):
        if decision not in ('approved','excluded','review'):raise ValueError('Невалидно решение.')
        with self.store.db() as db:
            for ident in ids:
                self.record(ident);db.execute('INSERT OR REPLACE INTO decisions VALUES(?,?,?)',(ident,decision,time.time()))
        return dict(updated=len(ids))
    def trash(self,ids,confirmed=False):
        if not confirmed:raise ValueError('Необходимо е потвърждение за кошчето.')
        result=[];folder=self.store.folder/'Trash';folder.mkdir(exist_ok=True)
        for ident in ids:
            with self.operation_lock:
                row=self.record(ident);source=self.path(ident);original_hash=digest(source);entry=uuid.uuid4().hex
                target=folder/(entry+'.zip');temporary=target.with_suffix('.partial')
                manifest=dict(id=entry,file=row,hash=original_hash,originalName=source.name,created=time.time(),zip=target.name)
                side=companion_path(source)
                with zipfile.ZipFile(temporary,'x',zipfile.ZIP_DEFLATED,compresslevel=3,allowZip64=True) as archive:
                    archive.write(source,'image'+source.suffix)
                    if side.exists():archive.write(side,'metadata.json')
                with zipfile.ZipFile(temporary) as archive:
                    if archive.testzip() is not None:raise OSError('Повредено копие в кошчето.')
                    import hashlib
                    with archive.open(archive.namelist()[0]) as stream:
                        h=hashlib.sha256()
                        for block in iter(lambda:stream.read(4*1024*1024),b''):h.update(block)
                    if h.hexdigest()!=original_hash:raise OSError('Кошчето не съвпада с оригинала.')
                os.replace(temporary,target)
                with self.store.db() as db:db.execute('INSERT INTO trash VALUES(?,?,?)',(entry,json.dumps(manifest,ensure_ascii=False),'staged'))
                if digest(source)!=original_hash:raise OSError('Кадърът е променен; запазен е оригиналът.')
                source.unlink()
                side.unlink(missing_ok=True)
                with self.store.db() as db:
                    db.execute('UPDATE files SET deleted=1 WHERE id=?',(ident,));db.execute("UPDATE trash SET state='trashed' WHERE id=?",(entry,))
                result.append(entry)
                self.browse.cache.pop(row['archive'],None)
        self.events.emit('archive.changed');return dict(trashed=result)
    def trash_list(self):
        with self.store.db() as db:return [dict(**json.loads(r['manifest']),state=r['state']) for r in db.execute('SELECT * FROM trash')]
    def restore(self,entry):
        with self.operation_lock:
            manifest=next((m for m in self.trash_list() if m['id']==entry),None)
            if not manifest:raise ValueError('Непознат запис в кошчето.')
            row=manifest['file'];root=self.root(row['archive']);target=within(root,row['relative'],existing=False)
            target.parent.mkdir(parents=True,exist_ok=True)
            archive_path=within(self.store.folder/'Trash',manifest['zip'])
            if target.exists():raise FileExistsError('На старото място вече има файл; няма да го заменя.')
            owned=False
            try:
                with target.open('xb') as output:
                    owned=True
                    with zipfile.ZipFile(archive_path) as archive,archive.open(archive.namelist()[0]) as source:shutil.copyfileobj(source,output,4*1024*1024)
                if digest(target)!=manifest['hash']:raise OSError('Възстановеният файл не съвпада.')
                with zipfile.ZipFile(archive_path) as archive:
                    if 'metadata.json' in archive.namelist():
                        side=companion_path(target)
                        if side.exists():raise FileExistsError('Вече има метаданни на старото място.')
                        atomic_json(side,json.loads(archive.read('metadata.json')))
            except BaseException:
                if owned:target.unlink(missing_ok=True)
                raise
            meta=dict(row['meta'],size=target.stat().st_size,mtime=target.stat().st_mtime_ns)
            side=companion_path(target);meta['companionMtime']=side.stat().st_mtime_ns if side.exists() else 0
            self.put(row['archive'],target,meta,fingerprint=manifest['hash'])
            with self.store.db() as db:db.execute('DELETE FROM trash WHERE id=?',(entry,))
            archive_path.unlink();self.browse.cache.pop(row['archive'],None);self.events.emit('archive.changed');return dict(restored=True)
    def empty_trash(self,confirmed=False):
        if not confirmed:raise ValueError('Необходимо е отделно потвърждение за окончателно изтриване.')
        count=0
        with self.operation_lock:
            for m in self.trash_list():
                if m['state']!='trashed':continue
                within(self.store.folder/'Trash',m['zip']).unlink(missing_ok=True)
                with self.store.db() as db:db.execute('DELETE FROM trash WHERE id=?',(m['id'],))
                count+=1
        return dict(deleted=count)
    def rename(self,archive,relative,new_name):
        if component(new_name)!=new_name or not new_name:raise ValueError('Невалидно име.')
        root=self.root(archive);source=within(root,relative)
        if source==root:raise ValueError('Името на корена се сменя от настройките.')
        target=within(root,(source.parent/new_name).relative_to(root).as_posix(),existing=False)
        if target.exists():raise FileExistsError('Вече има файл или папка с това име.')
        source_side=companion_path(source) if source.is_file() else None
        target_side=companion_path(target)
        if source_side and source_side.exists() and target_side.exists():raise FileExistsError('Вече има метаданни с това име.')
        old=source.relative_to(root).as_posix();new=target.relative_to(root).as_posix();rows=self.query(dict(archive=archive,relative=old))
        with self.operation_lock:
            source.rename(target)
            try:
                if source_side and source_side.exists():source_side.rename(target_side)
                with self.store.db() as db:
                    for row in rows:db.execute('UPDATE files SET relative=? WHERE id=?',(new+row['relative'][len(old):],row['id']))
            except BaseException:
                target.rename(source)
                if source_side and target_side.exists():target_side.rename(source_side)
                raise
        self.browse.cache.pop(archive,None)
        self.store.history('rename',archive=archive,old=old,new=new);self.events.emit('archive.changed');return dict(renamed=True)

    def columns(self,archive,relative,files=False):
        layout=schema(self.settings,archive);depth=len(Path(relative).parts)
        return ['name']+list(layout['fileColumns'] if files or depth>=len(layout['levels']) else layout['levels'][depth]['columns'])
    def entry(self,archive,rel,path=None):
        cache=self.browse.get(archive);row=cache['files'].get(rel);folder=row is None and (path is None or path.is_dir())
        layout=schema(self.settings,archive);depth=len(Path(rel).parts);level=layout['levels'][depth-1] if folder and 0<depth<=len(layout['levels']) else {}
        meta=row['meta'] if row else {};group=cache['groups'].get(rel,blank());kind='day' if level.get('field')=='night' else 'folder'
        if row:kind=meta.get('kind','frame')
        details=self.browse.details(archive,rel,kind);s=path.stat() if path else None
        info=dict(group['info']) if folder else {k:(', '.join(map(str,v)) if isinstance(v,list) else str(v)) for k,v in meta.items() if k not in ('headers','sourceFiles','provenanceOverride','missingFields') and v is not None}
        info['realTelescope']=info.get('telescope','');info['sourceStackCount']=meta.get('sourceStackCount',0) if row else ''
        aliases=self.settings.value.get('preferences',{}).get('customization',{}).get('archive',{}).get('telescopeLabels',{})
        name=Path(rel).name;caption=name
        if row:
            formula=layout.get('fileFormula','{filename}')
            if formula=='{filename}':
                from Backend.Archive.formulas import effective
                profile=self.settings.value.get('preferences',{}).get('customization',{}).get('archive',{})
                formula=effective(profile,dict(archive=archive,kind=meta.get('kind'),name=name,telescope=meta.get('telescope'),level='day')).get('formula','{filename}')
            if formula!='{filename}':
                # Custom header formulas use the saved index, never reread FITS.
                full=cache.setdefault('headerDetails',{}).get(row['id'])
                if full is None:full=self.record(row['id'])['meta'];cache['headerDetails'][row['id']]=full
                meta=full
            caption=render(formula,meta,name,aliases)
        elif level:
            values=dict(info,night=(details.get('lastObservation') or '')[:10]);caption=render(level['formula'],values,name,aliases)
            if not group['files'] or '—' in caption or 'Неизвестно' in caption:caption=name
        return dict(id=row['id'] if row else None,archiveId=archive,relativePath=rel,name=name,displayName=caption,kind='folder' if folder else 'fits' if Path(rel).suffix.lower() in FITS else 'image',bytes=group['bytes'] if folder else row['size'] if row else s.st_size if s else None,
            modified=dt.datetime.fromtimestamp(s.st_mtime if s else row['mtime']/1e9 if row else time.time(),dt.timezone.utc).isoformat(),details=details,info=info,isLink=False,icon=level.get('icon') if folder else None)
    def listing(self,archive,relative='',offset=0,search=''):
        relative=relative.replace('\\','/').strip('/');folder=self.resolve(archive,relative);root=self.root(archive)
        # scandir does not stat every image. Only the visible page is enriched.
        names=[]
        with os.scandir(folder) as entries:
            for p in entries:
                if p.name.startswith('.') or p.is_symlink():continue
                directory=p.is_dir(follow_symlinks=False)
                if directory and getattr(Path(p.path),'is_junction',lambda:False)():continue
                if directory or Path(p.name).suffix.lower() in IMAGES and not p.name.endswith('.coverage.fits'):names.append((not directory,p.name))
        names.sort(key=lambda e:(e[0],e[1].casefold()));filtered=[e for e in names if search.casefold() in e[1].casefold()];offset=max(0,int(offset))
        entries=[self.entry(archive,(relative+'/' if relative else '')+name,folder/name) for _,name in filtered[offset:offset+100]]
        layout=schema(self.settings,archive);anchor='/'.join(relative.split('/')[:max(0,len(layout['levels'])-1)]) if relative else ''
        menu=self.listing_menu(archive,anchor)
        return dict(archiveId=archive,relativePath=relative,fullPath=str(folder),total=len(filtered),offset=offset,pageSize=100,directFiles=sum(d for d,_ in names),directFolders=sum(not d for d,_ in names),entries=entries,problem=None,
            columns=self.columns(archive,relative,files=bool(entries) and all(e['kind']!='folder' for e in entries)),structure=layout,navigation=dict(relativePath=anchor,totalFolders=len(menu),offset=0,entries=menu[:100],problem=None))
    def listing_menu(self,archive,relative):
        folder=self.resolve(archive,relative);root=self.root(archive);result=[]
        with os.scandir(folder) as entries:
            for p in entries:
                if not p.name.startswith('.') and not p.is_symlink() and p.is_dir(follow_symlinks=False) and not getattr(Path(p.path),'is_junction',lambda:False)():
                    rel=Path(p.path).relative_to(root).as_posix();depth=len(Path(rel).parts);levels=schema(self.settings,archive)['levels'];level=levels[depth-1] if depth<=len(levels) else {}
                    result.append(dict(relativePath=rel,name=p.name,kind='folder',isLink=False,icon=level.get('icon','folder')))
        return sorted(result,key=lambda e:e['name'].casefold())
    def summary(self,archive,relative=''):
        t=self.browse.group(archive,relative);state=self.index_status(archive)
        return dict(archiveId=archive,relativePath=relative,frames=t['frames'],files=t['files'],bytes=t['bytes'],totalExposure=t['exposure'] if not t['files'] or t['exposureUnknown']<t['files'] else None,
            exposureSeconds=t['exposure'] if not t['files'] or t['exposureUnknown']<t['files'] else None,unknownExposure=t['exposureUnknown'],minimumFrames=bool(t['framesUnknown']),minimumExposure=bool(t['exposureUnknown']),knownExposureFrames=t['knownExposureFrames'],partial=state['building'] or not state.get('complete',False),building=state['building'],stale=state['stale'],kind=self.settings.archive(archive).get('type',archive),created=t['created'],fromIndex=True,savedUtc=state.get('savedUtc'),problem=state.get('problem'),totalFiles=state.get('totalFiles',t['files']),scannedFiles=state.get('scannedFiles',0),metadataIncomplete=t['metadataIncomplete'],cacheFile=str(self.store.path))
    def filtered(self,filters,offset=0,search=''):
        archive=filters.get('archive','frames');rows=self.browse.get(archive)['rows'];matches=[];total=blank()
        for row in rows:
            if any(filters.get(k) and row['meta'].get(k)!=filters[k] for k in ('telescope','object','night','kind')):continue
            if filters.get('since') and row['added']<float(filters['since']):continue
            if search.casefold() not in row['relative'].casefold():continue
            matches.append(row);accumulate(total,row)
        offset=max(0,int(offset));entries=[self.entry(archive,r['relative']) for r in matches[offset:offset+100]]
        return dict(archiveId=archive,relativePath='',fullPath='Филтриран архив',filtered=True,total=len(matches),offset=offset,pageSize=100,directFiles=len(matches),directFolders=0,entries=entries,columns=self.columns(archive,'',files=True),statistics={k:v for k,v in total.items() if k!='info'},structure=schema(self.settings,archive))
