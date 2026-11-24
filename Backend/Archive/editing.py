"""Previewed, journaled metadata edits and routing. Originals survive failed batches."""
from Backend.Foundation.branding import companion as companion_path
import base64,contextlib,copy, datetime as dt, itertools, json, os, re, shutil, threading, time, uuid
from pathlib import Path
from Backend.Foundation.runtime import atomic_json,within,digest,read_json
from Backend.Fits.images import FITS,IMAGES,metadata,finite,component,image_hdu
from Backend.Archive.structure import destination,validate,preset,schema,FIELDS,render

HEADER_KEYS=dict(object='OBJECT',telescope='TELESCOP',night='PSNIGHT',observed='DATE-OBS',filter='FILTER',sensor='DETECTOR',frames='NCOMBINE',exposure='EXPTIME',totalExposure='TOTEXP',sourceStackCount='PSSTKCNT',telescopes='PSTSCOPS',created='DATE')
ROUTING_FIELDS=('telescope','object','night','filter','sensor')
PORTABLE_FIELDS=('sourceFiles','sourceNights','provenanceOverride','sourceStacks','combination','processing','kind','tag')
@contextlib.contextmanager
def acquire(lock,cancel,progress):
    if not lock.acquire(blocking=False):
        progress(0,1,'Изчакване на текущата архивна операция')
        while not lock.acquire(timeout=.2):
            if cancel.is_set():raise InterruptedError('Операцията е отменена.')
    try:
        if cancel.is_set():raise InterruptedError('Операцията е отменена.')
        yield
    finally:lock.release()
def changes(value):
    if not isinstance(value,dict) or len(value)>60:raise ValueError('Невалидни промени.')
    result={}
    for key,v in value.items():
        if key not in HEADER_KEYS:raise ValueError('Полето не може да се редактира: '+key)
        if key in ('frames','sourceStackCount','exposure','totalExposure'):
            number=finite(v)
            if number is None or number<0 or key=='frames' and number<1:raise ValueError('Невалидно число за '+FIELDS[key])
            if key in ('frames','sourceStackCount') and not number.is_integer():raise ValueError('Броят трябва да е цяло число.')
            v=int(number) if key in ('frames','sourceStackCount') else number
        elif key=='telescopes':v=[str(x).strip()[:160] for x in (v if isinstance(v,list) else str(v).split(';')) if str(x).strip()]
        else:
            v=str(v).strip()
            if not v or len(v)>240:raise ValueError('Попълни текст до 240 знака.')
            if key in ('night','created','observed'):
                try:dt.datetime.fromisoformat(v.replace('Z','+00:00'))
                except ValueError:raise ValueError('Използвай дата YYYY-MM-DD или ISO дата и час.')
            if key=='night':v=v[:10]
        result[key]=v
    return result

def amended(meta, updates):
    result=copy.deepcopy(meta)
    result.update(updates)
    result.setdefault('headers',{}).update({HEADER_KEYS[key]:value for key,value in updates.items()})
    return result

def inferred_folder_value(level, folder, meta, name, key):
    # Invert a simple formula like "Observation night {night}". A rerender
    # below still has to reproduce every selected folder component.
    marker='AstroArchiveValue'
    example=component(render(level['formula'],amended(meta,{key:marker}),name))
    if example.count(marker)!=1:return None
    prefix,suffix=example.split(marker)
    if not folder.casefold().startswith(prefix.casefold()) or not folder.casefold().endswith(suffix.casefold()):
        return None
    return folder[len(prefix):len(folder)-len(suffix)]

def route_to_prefix(settings, archive, meta, name, prefix, neighbors):
    layout=schema(settings,archive)
    if len(prefix)>len(layout['levels']):
        raise ValueError('Целевата папка е извън нивата на архивната структура.')

    def matches(candidate, count):
        path=destination(settings,archive,candidate,name,layout)
        return tuple(part.casefold() for part in path.parts[:count])==tuple(part.casefold() for part in prefix[:count])

    result=meta
    for index, folder in enumerate(prefix):
        if matches(result,index+1):continue
        level=layout['levels'][index]
        words=set(re.findall(r'[A-Za-z][A-Za-z0-9_-]*',level['formula']))
        keys=[key for key in ROUTING_FIELDS if key in words or HEADER_KEYS[key] in words or key=='telescope' and 'realTelescope' in words]
        # Use complete combinations observed in the target. This avoids
        # assembling a telescope/object pair never present in that folder.
        candidates={}
        def offer(values):
            try:values=changes({key:value for key,value in values.items() if isinstance(value,str) and value and value!=result.get(key)})
            except ValueError:return
            for count in range(1,len(values)+1):
                found=False
                for subset in itertools.combinations(values,count):
                    patch={key:values[key] for key in subset}
                    candidate=amended(result,patch)
                    if matches(candidate,index+1):
                        candidates[tuple(sorted(patch.items()))]=candidate
                        found=True
                if found:break

        profiles={tuple(neighbor.get(key) if isinstance(neighbor.get(key),str) else None for key in keys) for neighbor in neighbors}
        for profile in profiles:
            offer(dict(zip(keys,profile)))
        if not candidates:
            for key in keys:
                offer({key:inferred_folder_value(level,folder,result,name,key)})
        if candidates:
            fewest=min(map(len,candidates))
            best=[value for patch,value in candidates.items() if len(patch)==fewest]
            if len(best)==1:
                result=best[0];continue
        raise ValueError(f'Папка „{folder}“ не определя еднозначно нужните метаданни за „{name}“.')
    return result

class ArchiveEditor:
    def __init__(self,catalog):self.catalog=catalog;self.plans={}
    def local_path(self,archive,relative):
        if self.catalog.shared_sources and self.catalog.shared_sources.get(archive):
            raise ValueError('Промяната на споделени файлове се прави от хоста на архива.')
        root=self.catalog.root(archive)
        path=within(root,relative.replace('\\','/'))
        if any(part.startswith('.') for part in path.relative_to(root).parts):
            raise ValueError('Избери видима папка или изображение от архива.')
        return root,path

    def selection(self,archive,relative):
        root,path=self.local_path(archive,relative)
        if path.is_file():
            images=[path] if path.suffix.lower() in IMAGES else []
        else:
            images=[]
            def fail(error):raise error
            for folder,dirs,names in os.walk(path,onerror=fail):
                parent=Path(folder)
                dirs[:]=[name for name in dirs if not name.startswith('.') and not (parent/name).is_symlink() and not (parent/name).is_junction()]
                images.extend(parent/name for name in names if not name.startswith('.') and (parent/name).suffix.lower() in IMAGES and not (parent/name).is_symlink())
        actual={image.relative_to(root).as_posix() for image in images}
        rows=self.catalog.query(dict(archive=archive,relative=path.relative_to(root).as_posix() if path!=root else ''))
        indexed={row['relative']:row for row in rows}
        if actual-indexed.keys():raise ValueError('Има изображения извън индекса. Обнови архива и опитай отново.')
        if not actual:raise ValueError('В избраното място няма архивни изображения.')
        return root,path,[indexed[name] for name in sorted(actual)]

    def rename_preview(self,archive,relative,formulas,file_formula):
        root,path,rows=self.selection(archive,relative)
        if not path.is_dir():raise ValueError('Избери папка за пакетно именуване.')
        parents=path.relative_to(root).parts
        saved=schema(self.catalog.settings,archive)
        layout=copy.deepcopy(saved)
        levels=layout['levels'][len(parents):]
        if not isinstance(formulas,list) or len(formulas)!=len(levels):
            raise ValueError('Нивата в архива са променени. Отвори инструмента отново.')
        for level,formula in zip(levels,formulas):
            level['formula']=formula
        layout['fileFormula']=file_formula
        validate(layout)
        items=[]
        for row in rows:
            original=Path(row['relative'])
            routed=destination(self.catalog.settings,archive,row['meta'],original.name,layout)
            # Keep the selected directory itself and any deeper physical levels
            # outside the configured formulas.
            tail=original.parent.parts[len(parents):]
            used=min(len(tail),len(levels))
            folders=(*parents,*routed.parts[len(parents):len(parents)+used],*tail[used:])
            items.append(dict(row=row,target=Path(*folders,routed.name).as_posix(),meta=row['meta'],updates={}))
        return self.plan_selection(archive,archive,root,root,items)

    def move_preview(self,archive,relative,target_archive,target_relative):
        root,source=self.local_path(archive,relative)
        target_root,target=self.local_path(target_archive,target_relative)
        if source==root or not target.is_dir():
            raise ValueError('Избери архивен файл или подпапка и целева папка.')
        if target.is_relative_to(source):
            raise ValueError('Папка не може да се постави в самата себе си.')
        left=self.catalog.settings.archive(archive).get('type',archive)
        right=self.catalog.settings.archive(target_archive).get('type',target_archive)
        if left!=right or archive!=target_archive and (root.is_relative_to(target_root) or target_root.is_relative_to(root)):
            raise ValueError('Избери отделна папка в архив от същия вид.')
        if source.is_dir() and not any(source.iterdir()):
            before=source.relative_to(root).as_posix()
            after=(target/source.name).relative_to(target_root).as_posix()
            if within(target_root,after,existing=False).exists():
                raise FileExistsError('В целевата папка вече има папка с това име.')
            preview=self.register_plan(archive,target_archive,root,target_root,[],{},set())
            self.plans[preview['token']]['emptyMove']=dict(before=before,after=after)
            return dict(preview,moves=1,examples=[dict(before=before,after=after,fields=[])],backup=False)
        _,_,rows=self.selection(archive,relative)
        prefix=target.relative_to(target_root).parts
        neighbors=[row['meta'] for row in self.catalog.query(dict(archive=target_archive,relative=target.relative_to(target_root).as_posix()))] if prefix else []
        items=[]
        for row in rows:
            name=Path(row['relative']).name
            result=route_to_prefix(self.catalog.settings,target_archive,row['meta'],name,prefix,neighbors)
            updates={key:result[key] for key in ROUTING_FIELDS if result.get(key)!=row['meta'].get(key)}
            routed=destination(self.catalog.settings,target_archive,result,name)
            target_path=Path(*prefix,*routed.parts[len(prefix):])
            items.append(dict(row=row,target=target_path.as_posix(),meta=result,updates=updates))
        return self.plan_selection(archive,target_archive,root,target_root,items)

    def delete_selection(self,archive,relative,cancel,progress,confirmed=False):
        if not confirmed:raise ValueError('Потвърди преместването в кошчето.')
        with acquire(self.catalog.operation_lock,cancel,progress):
            root,path=self.local_path(archive,relative)
            if path==root:raise ValueError('Основната папка на архива не се изтрива.')
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()
                self.catalog.browse.cache.pop(archive,None)
                self.catalog.events.emit('archive.changed')
                return dict(trashed=[],removedFolder=True)
            root,path,rows=self.selection(archive,relative)
            result=self.catalog.trash([row['id'] for row in rows],confirmed=True,cancel=cancel,progress=progress)
            if not path.is_dir():return result
            folders={parent for row in rows for parent in (root/row['relative']).parents if parent.is_relative_to(path)}
            for parent in sorted(folders,key=lambda p:len(p.parts),reverse=True):
                with contextlib.suppress(OSError):parent.rmdir()
            result['removedFolder']=not path.exists()
            return result

    def plan_selection(self,archive,target_archive,root,target_root,items):
        sources={item['row']['relative'].casefold() for item in items} if archive==target_archive else set()
        with self.catalog.store.db() as db:
            occupied={row['relative'].casefold() for row in db.execute('SELECT relative FROM files WHERE archive=?',(target_archive,))}
        targets=set()
        for item in items:
            target=within(target_root,item['target'],existing=False)
            def conflict(path):
                rel=path.relative_to(target_root).as_posix().casefold()
                return rel in targets or rel not in sources and (rel in occupied or path.exists() or companion_path(path).exists())
            if conflict(target):
                target=target.with_name(target.stem+'-'+item['row']['id'][:8]+target.suffix)
            if conflict(target):raise FileExistsError('В целевата папка вече има: '+target.name)
            item['target']=target.relative_to(target_root).as_posix()
            targets.add(item['target'].casefold())
        changed=[item for item in items if archive!=target_archive or item['target']!=item['row']['relative'] or item['updates']]
        if not changed:raise ValueError('Изображенията вече са на това място с тези имена.')
        return self.register_plan(archive,target_archive,root,target_root,items,{},set())

    def register_plan(self,archive,target_archive,root,target_root,items,updates,merges):
        token=uuid.uuid4().hex
        self.plans={key:plan for key,plan in self.plans.items() if time.time()-plan['created']<900}
        structures={ident:copy.deepcopy(schema(self.catalog.settings,ident)) for ident in {archive,target_archive}}
        self.plans[token]=dict(archive=archive,targetArchive=target_archive,roots={archive:str(root),target_archive:str(target_root)},
                               structures=structures,updates=updates,files=items,created=time.time())
        examples=[]
        for item in items[:100]:
            fields=item.get('updates',updates)
            examples.append(dict(before=item['row']['relative'],after=item['target'],
                                 fields=[dict(name=FIELDS[key],before=item['row']['meta'].get(key),after=value) for key,value in fields.items()]))
        return dict(token=token,count=len(items),moves=sum(archive!=target_archive or item['target']!=item['row']['relative'] for item in items),
                    merges=sorted(merges),examples=examples,fields=updates,ordinaryImages=sum(Path(item['target']).suffix.lower() not in FITS for item in items),backup=True)
    def describe(self,archive,relative=''):
        rows=self.catalog.query(dict(archive=archive,relative=relative));cache=self.catalog.browse.get(archive)
        options={k:sorted({str(r['meta'].get(k,'')) for r in cache['rows'] if r['meta'].get(k)}) for k in ('telescope','object','night','filter','sensor')}
        values={k:next(iter(v)) if len(v:={json.dumps(r['meta'].get(k),ensure_ascii=False) for r in rows})==1 else None for k in HEADER_KEYS}
        return dict(count=len(rows),totals=self.catalog.totals(rows),values={k:json.loads(v) if v is not None else None for k,v in values.items()},
                    suggestions=options,fields=[dict(key=k,title=FIELDS.get(k,k)) for k in HEADER_KEYS],ordinaryImages=sum(Path(r['relative']).suffix.lower() not in FITS for r in rows))
    def sample(self,archive):
        layout=schema(self.catalog.settings,archive);rows=self.catalog.browse.get(archive)['rows'];meta={}
        if rows:meta=self.catalog.record(rows[0]['id'])['meta']
        fields=[dict(key=k,title=v,value=meta.get(k)) for k,v in FIELDS.items()]
        fields.extend(dict(key=k,title='FITS · '+k,value=v) for k,v in meta.get('headers',{}).items() if k not in FIELDS)
        return dict(structure=layout,presets={k:preset(k) for k in ('frames','stacks','photos')},fields=fields,sampleName=Path(rows[0]['relative']).name if rows else 'example.fit',sample=meta,hasSample=bool(rows))
    def preview(self,archive,relative='',values=None,restructure=False):
        updates=changes(values or {});rows=self.catalog.query(dict(archive=archive,relative=relative));root=self.catalog.root(archive)
        if not rows:raise ValueError('В избраната група няма индексирани изображения.')
        if not updates and not restructure:raise ValueError('Избери поне едно поле за промяна.')
        planned=[];targets=set();old_paths={r['relative'].casefold() for r in rows};merges=set()
        for row in rows:
            meta=amended(row['meta'],updates)
            if 'exposure' in updates and 'totalExposure' not in updates:meta['totalExposure']=meta['exposure']*meta.get('frames',1)
            if 'frames' in updates and 'totalExposure' not in updates:meta['totalExposure']=meta.get('exposure')*meta['frames'] if meta.get('exposure') is not None else None
            if 'observed' in updates and 'night' not in updates:meta['night']=(dt.datetime.fromisoformat(meta['observed'].replace('Z','+00:00'))-dt.timedelta(hours=12)).date().isoformat()
            rel=destination(self.catalog.settings,archive,meta,Path(row['relative']).name).as_posix();target=within(root,rel,existing=False)
            if target.parent.exists() and target.parent!=within(root,row['relative']).parent:merges.add(target.parent.relative_to(root).as_posix())
            if (target.exists() and rel.casefold() not in old_paths) or rel.casefold() in targets:
                target=target.with_name(target.stem+'-'+row['id'][:8]+target.suffix);rel=target.relative_to(root).as_posix()
            if rel.casefold() in targets or target.exists() and rel.casefold() not in old_paths:raise FileExistsError('Името се повтаря: '+rel)
            targets.add(rel.casefold());planned.append(dict(row=row,target=rel,meta=meta))
        return self.register_plan(archive,archive,root,root,planned,updates,merges)
    def commit(self,token,cancel,progress,confirmed=False):
        if not confirmed:raise ValueError('Потвърди предварителния преглед.')
        plan=self.plans.pop(token,None)
        if not plan or time.time()-plan['created']>900:raise ValueError('Прегледът е изтекъл; направи нов.')
        archive,target_archive=plan['archive'],plan['targetArchive']
        root,target_root=self.catalog.root(archive),self.catalog.root(target_archive)
        batch=within(root,'.astroarchive-edits/'+token,existing=False)
        with acquire(self.catalog.operation_lock,cancel,progress),contextlib.ExitStack() as locks:
            for ident in sorted({archive,target_archive}):
                locks.enter_context(acquire(self.catalog.scan_locks.setdefault(ident,threading.Lock()),cancel,progress))
                if str(self.catalog.root(ident))!=plan['roots'][ident] or schema(self.catalog.settings,ident)!=plan['structures'][ident]:
                    raise ValueError('Архивът или структурата са променени; направи нов преглед.')
            if move:=plan.get('emptyMove'):
                source=within(root,move['before']);target=within(target_root,move['after'],existing=False)
                if not source.is_dir() or any(source.iterdir()):
                    raise ValueError('Изходната папка вече не е празна.')
                target.mkdir()
                try:source.rmdir()
                except OSError:
                    target.rmdir()
                    raise
                for ident in {archive,target_archive}:self.catalog.browse.cache.pop(ident,None)
                self.catalog.events.emit('archive.changed')
                return dict(updated=0,movedFolder=True)
            for item in plan['files']:
                source=within(root,item['row']['relative']);stat=source.stat()
                if (stat.st_size,stat.st_mtime_ns)!=(item['row']['size'],item['row']['mtime']):raise ValueError('Файл е променен; обнови индекса и прегледа.')
                side=companion_path(source)
                if side.is_symlink() or side.is_junction():
                    raise ValueError('Придружаващите метаданни не могат да са връзка.')
                if (side.stat().st_mtime_ns if side.exists() else 0)!=item['row']['meta'].get('companionMtime',0):
                    raise ValueError('Метаданните на файл са променени; обнови прегледа.')
            needed=sum(i['row']['size'] for i in plan['files'])*1.05
            if shutil.disk_usage(root).free<needed or root.stat().st_dev!=target_root.stat().st_dev and shutil.disk_usage(target_root).free<needed:
                raise OSError('Недостатъчно място за безопасни временни копия.')
            batch.mkdir(parents=True);atomic_json(batch/'journal.json',dict(state='staging',plan=plan));applied=[];removed=[]
            try:
                for index,item in enumerate(plan['files'],1):
                    if cancel.is_set():raise InterruptedError()
                    row=item['row'];source=within(root,row['relative']);stage=batch/(row['id']+Path(row['relative']).suffix)
                    item['stage']=str(stage)
                    shutil.copy2(source,stage)
                    values={k:item['meta'][k] for k in HEADER_KEYS if k in item['meta']}
                    updates=item.get('updates',plan['updates'])
                    changed=set(updates)|({'totalExposure'} if {'frames','exposure'}&set(updates) else set())|({'night'} if 'observed' in updates else set())
                    if changed and source.suffix.lower() in FITS:
                        from astropy.io import fits
                        with fits.open(stage,mode='update',memmap=False,do_not_scale_image_data=True) as hdus:
                            header=image_hdu(hdus).header
                            unicode_values={}
                            def fits_value(key,value):
                                value=';'.join(value) if isinstance(value,list) else value
                                if isinstance(value,str) and not value.isascii():
                                    unicode_values[HEADER_KEYS[key]]=value;return value.encode('ascii','backslashreplace').decode('ascii')
                                return value
                            for key in changed:
                                value=values.get(key)
                                if value is not None:header[HEADER_KEYS[key]]=fits_value(key,value)
                            # Keep primary metadata consistent when the image is in an extension.
                            for key in changed:
                                value=values.get(key)
                                if value is not None:hdus[0].header[HEADER_KEYS[key]]=fits_value(key,value)
                            previous=item['row']['meta'].get('headers',{}).get('PSUTF8')
                            if previous:
                                try:
                                    old=json.loads(base64.urlsafe_b64decode(previous));old={k:v for k,v in old.items() if k not in {HEADER_KEYS[x] for x in changed}};old.update(unicode_values);unicode_values=old
                                except (ValueError,TypeError):pass
                            if unicode_values:
                                header['PSUTF8']=base64.urlsafe_b64encode(json.dumps(unicode_values,ensure_ascii=False).encode()).decode();hdus[0].header['PSUTF8']=header['PSUTF8']
                            else:
                                header.remove('PSUTF8',ignore_missing=True);hdus[0].header.remove('PSUTF8',ignore_missing=True)
                            for hdu in hdus:
                                if 'CHECKSUM' in hdu.header:hdu.add_checksum()
                    companion=read_json(companion_path(source),{})
                    companion=companion if isinstance(companion,dict) else {}
                    companion.update({k:item['meta'][k] for k in (*HEADER_KEYS,*PORTABLE_FIELDS) if k in item['meta']})
                    # Overrides follow explicit edits rather than masking them on a rescan.
                    if isinstance(companion.get('provenanceOverride'),dict):
                        companion['provenanceOverride'].update({k:values[k] for k in changed & {'frames','totalExposure'} if k in values})
                    atomic_json(companion_path(stage),companion)
                    item['parsed']=metadata(stage);progress(index,len(plan['files'])*2,'Подготовка и проверка на копията')
                if cancel.is_set():raise InterruptedError()
                atomic_json(batch/'journal.json',dict(state='applying',plan=plan))
                # Evacuate originals first so renaming A->B and B->A is safe.
                for item in plan['files']:
                    source=within(root,item['row']['relative']);backup=batch/'originals'/item['row']['relative'];backup.parent.mkdir(parents=True,exist_ok=True)
                    os.replace(source,backup);removed.append((source,backup))
                    side=companion_path(source)
                    if side.exists():saved=companion_path(backup);os.replace(side,saved);removed.append((side,saved))
                for index,item in enumerate(plan['files'],1):
                    target=within(target_root,item['target'],existing=False);target.parent.mkdir(parents=True,exist_ok=True)
                    def install(stage,target):
                        try:os.link(stage,target);applied.append(target)
                        except FileExistsError:raise
                        except OSError:
                            with target.open('xb') as output:
                                applied.append(target)
                                with stage.open('rb') as source:shutil.copyfileobj(source,output,4*1024*1024)
                            if digest(target)!=digest(stage):raise OSError('Копието не съвпада.')
                    stage=Path(item['stage']);install(stage,target)
                    side=companion_path(target);install(companion_path(stage),side)
                    item['parsed'].update(size=target.stat().st_size,mtime=target.stat().st_mtime_ns,companionMtime=side.stat().st_mtime_ns)
                    progress(len(plan['files'])+index,len(plan['files'])*2,'Записване и подреждане')
                with self.catalog.store.db() as db:
                    for item in plan['files']:db.execute('UPDATE files SET relative=? WHERE id=?',('.astroarchive-staged/'+item['row']['id'],item['row']['id']))
                    for item in plan['files']:db.execute('UPDATE files SET archive=?,relative=?,size=?,mtime=?,meta=?,fingerprint=NULL WHERE id=?',(target_archive,item['target'],item['parsed']['size'],item['parsed']['mtime'],json.dumps(item['parsed'],ensure_ascii=False),item['row']['id']))
                # A journal write failure after the committed DB transaction must
                # never roll the files back while leaving the new index in place.
                try:atomic_json(batch/'journal.json',dict(state='completed',plan=plan))
                except OSError:pass
            except BaseException:
                for path in reversed(applied):path.unlink(missing_ok=True)
                for source,backup in reversed(removed):source.parent.mkdir(parents=True,exist_ok=True);os.replace(backup,source)
                atomic_json(batch/'journal.json',dict(state='rolled-back',plan=plan));raise
            finally:
                for item in plan['files']:
                    if item.get('stage'):
                        stage=Path(item['stage']);stage.unlink(missing_ok=True);companion_path(stage).unlink(missing_ok=True)
            # Only empty old folders are removed; hidden backups remain outside browsing.
            for source,_ in removed:
                parent=source.parent
                while parent!=root:
                    try:parent.rmdir()
                    except OSError:break
                    parent=parent.parent
            for ident in {archive,target_archive}:self.catalog.browse.cache.pop(ident,None)
        self.catalog.events.emit('archive.changed')
        for ident in {archive,target_archive}:self.catalog.events.emit('archive.summary.changed',archiveId=ident)
        return dict(updated=len(plan['files']),backup=str(batch/'originals'))
