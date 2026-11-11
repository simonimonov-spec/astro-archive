"""Previewed, journaled metadata edits and routing. Originals survive failed batches."""
from Backend.Foundation.branding import companion as companion_path
import base64,contextlib,copy, datetime as dt, json, os, shutil, time, uuid
from pathlib import Path
from Backend.Foundation.runtime import atomic_json,within,digest
from Backend.Fits.images import FITS,metadata,finite,component,image_hdu
from Backend.Archive.structure import destination,validate,preset,schema,FIELDS,render

HEADER_KEYS=dict(object='OBJECT',telescope='TELESCOP',night='PSNIGHT',observed='DATE-OBS',filter='FILTER',sensor='DETECTOR',frames='NCOMBINE',exposure='EXPTIME',totalExposure='TOTEXP',sourceStackCount='PSSTKCNT',telescopes='PSTSCOPS',created='DATE')
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

class ArchiveEditor:
    def __init__(self,catalog):self.catalog=catalog;self.plans={}
    def describe(self,archive,relative=''):
        rows=self.catalog.query(dict(archive=archive,relative=relative));cache=self.catalog.browse.get(archive)
        options={k:sorted({str(r['meta'].get(k,'')) for r in cache['rows'] if r['meta'].get(k)}) for k in ('telescope','object','night','filter','sensor')}
        values={k:next(iter(v)) if len(v:={json.dumps(r['meta'].get(k),ensure_ascii=False) for r in rows})==1 else None for k in HEADER_KEYS}
        return dict(count=len(rows),values={k:json.loads(v) if v is not None else None for k,v in values.items()},suggestions=options,fields=[dict(key=k,title=FIELDS.get(k,k)) for k in HEADER_KEYS],ordinaryImages=sum(Path(r['relative']).suffix.lower() not in FITS for r in rows))
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
            meta=copy.deepcopy(row['meta']);meta.update(updates)
            if 'exposure' in updates and 'totalExposure' not in updates:meta['totalExposure']=meta['exposure']*meta.get('frames',1)
            if 'frames' in updates and 'totalExposure' not in updates:meta['totalExposure']=meta.get('exposure')*meta['frames'] if meta.get('exposure') is not None else None
            if 'observed' in updates and 'night' not in updates:meta['night']=(dt.datetime.fromisoformat(meta['observed'].replace('Z','+00:00'))-dt.timedelta(hours=12)).date().isoformat()
            rel=destination(self.catalog.settings,archive,meta,Path(row['relative']).name).as_posix();target=within(root,rel,existing=False)
            if target.parent.exists() and target.parent!=within(root,row['relative']).parent:merges.add(target.parent.relative_to(root).as_posix())
            if (target.exists() and rel.casefold() not in old_paths) or rel.casefold() in targets:
                target=target.with_name(target.stem+'-'+row['id'][:8]+target.suffix);rel=target.relative_to(root).as_posix()
            if rel.casefold() in targets or target.exists() and rel.casefold() not in old_paths:raise FileExistsError('Името се повтаря: '+rel)
            targets.add(rel.casefold());planned.append(dict(row=row,target=rel,meta=meta))
        token=uuid.uuid4().hex;self.plans={k:v for k,v in self.plans.items() if time.time()-v['created']<900}
        self.plans[token]=dict(archive=archive,updates=updates,files=planned,created=time.time(),structure=copy.deepcopy(schema(self.catalog.settings,archive)))
        return dict(token=token,count=len(planned),moves=sum(p['target']!=p['row']['relative'] for p in planned),merges=sorted(merges),examples=[dict(before=p['row']['relative'],after=p['target']) for p in planned[:100]],fields=updates,ordinaryImages=sum(Path(p['target']).suffix.lower() not in FITS for p in planned),backup=True)
    def commit(self,token,cancel,progress,confirmed=False):
        if not confirmed:raise ValueError('Потвърди предварителния преглед.')
        plan=self.plans.pop(token,None)
        if not plan or time.time()-plan['created']>900:raise ValueError('Прегледът е изтекъл; направи нов.')
        archive=plan['archive'];root=self.catalog.root(archive);batch=within(root,'.astroarchive-edits/'+token,existing=False)
        lock=self.catalog.scan_locks.setdefault(archive,__import__('threading').Lock())
        with acquire(self.catalog.operation_lock,cancel,progress),acquire(lock,cancel,progress):
            if schema(self.catalog.settings,archive)!=plan['structure']:raise ValueError('Структурата е променена; направи нов преглед.')
            for item in plan['files']:
                source=within(root,item['row']['relative']);stat=source.stat()
                if (stat.st_size,stat.st_mtime_ns)!=(item['row']['size'],item['row']['mtime']):raise ValueError('Файл е променен; обнови индекса и прегледа.')
            needed=sum(i['row']['size'] for i in plan['files'])*1.05
            if shutil.disk_usage(root).free<needed:raise OSError('Недостатъчно място за безопасни временни копия.')
            batch.mkdir(parents=True);atomic_json(batch/'journal.json',dict(state='staging',plan=plan));applied=[];removed=[]
            try:
                for index,item in enumerate(plan['files'],1):
                    if cancel.is_set():raise InterruptedError()
                    row=item['row'];source=within(root,row['relative']);stage=batch/(row['id']+Path(row['relative']).suffix);shutil.copy2(source,stage)
                    values={k:item['meta'][k] for k in HEADER_KEYS if k in item['meta']}
                    if plan['updates'] and source.suffix.lower() in FITS:
                        from astropy.io import fits
                        with fits.open(stage,mode='update',memmap=False,do_not_scale_image_data=True) as hdus:
                            header=image_hdu(hdus).header
                            unicode_values={}
                            def fits_value(key,value):
                                value=';'.join(value) if isinstance(value,list) else value
                                if isinstance(value,str) and not value.isascii():
                                    unicode_values[HEADER_KEYS[key]]=value;return value.encode('ascii','backslashreplace').decode('ascii')
                                return value
                            for key in set(plan['updates'])|({'totalExposure'} if {'frames','exposure'}&set(plan['updates']) else set())|({'night'} if 'observed' in plan['updates'] else set()):
                                value=values.get(key)
                                if value is not None:header[HEADER_KEYS[key]]=fits_value(key,value)
                            # Keep primary metadata consistent when the image is in an extension.
                            for key in plan['updates']:
                                value=values.get(key)
                                if value is not None:hdus[0].header[HEADER_KEYS[key]]=fits_value(key,value)
                            previous=item['row']['meta'].get('headers',{}).get('PSUTF8')
                            if previous:
                                try:
                                    old=json.loads(base64.urlsafe_b64decode(previous));old={k:v for k,v in old.items() if k not in {HEADER_KEYS[x] for x in plan['updates']}};old.update(unicode_values);unicode_values=old
                                except (ValueError,TypeError):pass
                            if unicode_values:
                                header['PSUTF8']=base64.urlsafe_b64encode(json.dumps(unicode_values,ensure_ascii=False).encode()).decode();hdus[0].header['PSUTF8']=header['PSUTF8']
                            else:
                                header.remove('PSUTF8',ignore_missing=True);hdus[0].header.remove('PSUTF8',ignore_missing=True)
                            for hdu in hdus:
                                if 'CHECKSUM' in hdu.header:hdu.add_checksum()
                    companion={k:item['meta'][k] for k in (*HEADER_KEYS,'sourceFiles','sourceNights','provenanceOverride','kind') if k in item['meta']}
                    # Overrides follow explicit edits rather than masking them on a rescan.
                    if 'provenanceOverride' in companion:companion['provenanceOverride'].update({k:v for k,v in values.items() if k in ('frames','totalExposure')})
                    atomic_json(companion_path(stage),companion)
                    item['stage']=str(stage);item['parsed']=metadata(stage);progress(index,len(plan['files'])*2,'Подготовка и проверка на копията')
                if cancel.is_set():raise InterruptedError()
                atomic_json(batch/'journal.json',dict(state='applying',plan=plan))
                # Evacuate originals first so renaming A->B and B->A is safe.
                for item in plan['files']:
                    source=within(root,item['row']['relative']);backup=batch/'originals'/item['row']['relative'];backup.parent.mkdir(parents=True,exist_ok=True)
                    os.replace(source,backup);removed.append((source,backup))
                    side=companion_path(source)
                    if side.exists():saved=companion_path(backup);os.replace(side,saved);removed.append((side,saved))
                for index,item in enumerate(plan['files'],1):
                    target=within(root,item['target'],existing=False);target.parent.mkdir(parents=True,exist_ok=True)
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
                    item['parsed'].update(size=target.stat().st_size,mtime=target.stat().st_mtime_ns)
                    progress(len(plan['files'])+index,len(plan['files'])*2,'Записване и подреждане')
                with self.catalog.store.db() as db:
                    for item in plan['files']:db.execute('UPDATE files SET relative=? WHERE id=?',('.astroarchive-staged/'+item['row']['id'],item['row']['id']))
                    for item in plan['files']:db.execute('UPDATE files SET relative=?,size=?,mtime=?,meta=?,fingerprint=NULL WHERE id=?',(item['target'],item['parsed']['size'],item['parsed']['mtime'],json.dumps(item['parsed'],ensure_ascii=False),item['row']['id']))
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
            self.catalog.browse.cache.pop(archive,None)
        self.catalog.events.emit('archive.changed');self.catalog.events.emit('archive.summary.changed',archiveId=archive)
        return dict(updated=len(plan['files']),backup=str(batch/'originals'))
