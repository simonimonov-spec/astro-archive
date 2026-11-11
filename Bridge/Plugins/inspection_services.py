"""Generic read-only pixel/quality SDK and non-destructive report annotations."""
from __future__ import annotations
import collections,copy, math, re, threading
from pathlib import Path
from Backend.Foundation.runtime import atomic_json

class InspectionServices:
    def __init__(self,service):self.service=service;self.lock=threading.RLock();self.owned_reports={};self.alignments=collections.OrderedDict()

    def indexed_source(self,path,size=None,mtime=None):
        # Resolve metadata only within configured archives and the existing index.
        # This never scans folders or opens an arbitrary client-supplied file.
        if not isinstance(path,str) or not 1<=len(path)<=4096:raise ValueError('Невалидна запазена архивна връзка.')
        target=Path(path)
        if not target.is_absolute():raise ValueError('Нужен е абсолютен път на запазения кадър.')
        target=target.resolve();matches=[]
        for archive in self.service.settings.value['archives']:
            try:
                root=self.service.catalog.root(archive['id']).resolve()
                if not target.is_relative_to(root):continue
                relative=target.relative_to(root).as_posix()
                with self.service.store.db() as db:row=db.execute('SELECT id FROM files WHERE archive=? AND relative=? AND deleted=0',(archive['id'],relative)).fetchone()
                if row:matches.append((len(root.parts),row['id']))
            except (OSError,ValueError):continue
        if not matches:raise ValueError('Запазеният кадър не е намерен в индекса на настроените архиви.')
        depth=max(d for d,_ in matches);ids={i for d,i in matches if d==depth}
        if len(ids)!=1:raise ValueError('Архивната връзка е двусмислена; отвори кадъра от правилния архив.')
        ident=ids.pop();stat=self.service.catalog.path(ident).stat()
        if size is not None and (type(size) is not int or stat.st_size!=size):raise ValueError('Размерът на кадъра е променен след оценката.')
        if mtime is not None and (type(mtime) is not int or stat.st_mtime_ns!=mtime):raise ValueError('Кадърът е променен след оценката.')
        return ident

    def identifiers(self,value):
        if not isinstance(value,list) or not value or len(value)>5000 or any(not isinstance(i,str) or not 1<=len(i)<=80 for i in value):raise ValueError('Избери от 1 до 5000 архивни кадъра.')
        return list(dict.fromkeys(value))

    def invoke(self,owner,action,p,cancel=None,progress=None):
        if action=='source':
            # Only the trusted catalog or an already opened viewer can resolve paths.
            # Local paths go to the permitted plugin, never to declarative controls.
            ident=p.get('id');viewer_id=p.get('viewerId');saved=p.get('saved')
            if saved is not None:
                if ident or viewer_id or not isinstance(saved,dict) or set(saved)!={'path','size','mtime'}:raise ValueError('Невалидна запазена архивна връзка.')
                if type(saved['size']) is not int or saved['size']<0 or type(saved['mtime']) is not int or saved['mtime']<=0:raise ValueError('Невалидна сигнатура на запазения кадър.')
                ident=self.indexed_source(saved['path'],saved['size'],saved['mtime'])
            if viewer_id:
                with self.service.viewer.lock:
                    frame=self.service.viewer.frames.get(viewer_id)
                    if not frame:raise ValueError('Прегледът вече не е отворен.')
                    ident=frame.get('sourceId');path=Path(frame['path']).resolve();name=frame['name']
            elif ident:
                self.identifiers([ident]);path=self.service.catalog.path(ident);name=path.name
            else:raise ValueError('Избери отворен преглед или архивен идентификатор.')
            if not ident and viewer_id:
                try:ident=self.indexed_source(str(path))
                except (OSError,ValueError):pass
            row=self.service.catalog.record(ident) if ident else None
            stat=path.stat();meta=row['meta'] if row else {}
            return dict(id=ident,path=str(path),name=name,archive=row['archive'] if row else None,
                relative=row['relative'] if row else None,size=stat.st_size,mtime=stat.st_mtime_ns,
                fingerprint=row.get('fingerprint') if row else None,kind=meta.get('kind'),
                group='|'.join(str(meta.get(k,'')) for k in ('telescope','object','night')),
                telescope=meta.get('telescope',''),object=meta.get('object',''),night=meta.get('night',''),filter=meta.get('filter',''))
        if action=='archives':return dict(archives=[dict(id=a['id'],name=a['name'],kind=a.get('type') or a['id']) for a in self.service.settings.value['archives'] if (a.get('type') or a['id'])=='frames' and not a.get('hidden')])
        if action=='limits':return dict(memoryMb=self.service.settings.value['memoryMb'],workers=self.service.settings.value['workers'])
        if action=='show':
            self.identifiers([p.get('id')]);self.service.catalog.record(p['id'])
            self.service.shared_viewer('function.image',dict(id=p['id']))
            return dict(displayed=True)
        if action=='selection':
            value=self.service.session.analysis_state()
            report=self.service.quality.results.get(value.get('reportId'),{})
            ids=[r['id'] for r in report.get('frames',[])] if p.get('scope')=='report' else value.get('selected') or value.get('picker',{}).get('ids') or [r['id'] for r in report.get('frames',[])]
            return dict(ids=ids,reportId=report.get('id'),kind=value.get('kind','frames'))
        if action=='prepare':
            ids=self.identifiers(p.get('ids'));report=self.service.quality.results.get(p.get('reportId'))
            if report and report.get('algorithm','').startswith('quality-4') and not p.get('force'):
                by_id={r['id']:r for r in report['frames']}
                if all(i in by_id for i in ids):
                    reusable=True
                    for i in ids:
                        stat=self.service.catalog.path(i).stat();r=by_id[i]
                        if (stat.st_size,stat.st_mtime_ns)!=(r.get('sourceSize'),r.get('sourceMtime')):reusable=False;break
                    if reusable:
                        refs={by_id[i].get('reference') for i in ids}
                        return dict(copy.deepcopy(report),frames=[copy.deepcopy(by_id[i]) for i in ids],referenceFrames=[copy.deepcopy(r) for r in report['frames'] if r['id'] in refs],reused=True)
            if self.service.session.active:raise ValueError('Изчакай текущия основен анализ, за да използваме неговите измервания.')
            # Plugin measurements use the same algorithm/cache, but must not replace
            # the user's "last analysis" autosave or delete its active report.
            from Backend.Analysis.quality import Quality
            class PluginMeasurements(Quality):
                def autosave(self,result):return None
            engine=PluginMeasurements(self.service.catalog,self.service.jobs)
            result=engine.run(ids,cancel or threading.Event(),progress or (lambda *args:None),force=bool(p.get('force')),kind='frames')
            with self.lock:
                self.service.quality.results[result['id']]=result
                owned=self.owned_reports.setdefault(owner,[]);owned.append(result['id'])
                current=self.service.session.analysis_state().get('reportId')
                saved={e['id'] for e in self.service.quality.history()['entries']}
                # Keep three recent plugin reports; never remove visible/saved analyses.
                while len(owned)>3:
                    old=owned.pop(0)
                    if old==current or old in saved:continue
                    try:(self.service.quality.reports/(old+'.json')).unlink(missing_ok=True)
                    except OSError:continue
                    self.service.quality.results.pop(old,None)
            return result
        if action=='register':
            # Generic catalogue-only registration: reuse the existing geometry
            # service, never reopen FITS or change the user's report/decisions.
            source_id=p.get('sourceId');target_id=p.get('targetId');self.identifiers([source_id,target_id])
            report_id=p.get('reportId')
            if not isinstance(report_id,str) or not re.fullmatch('[a-f0-9]{32}',report_id):raise ValueError('Невалиден отчет за подравняване.')
            report=self.service.quality.results.get(report_id)
            if not report:raise ValueError('Отчетът за измерванията вече не е наличен.')
            rows={r['id']:r for r in report.get('frames',[])+report.get('referenceFrames',[])}
            if source_id not in rows or target_id not in rows:raise ValueError('Подравняване извън измерения отчет.')
            source,target=rows[source_id],rows[target_id]
            for row in (source,target):
                stat=self.service.catalog.path(row['id']).stat()
                if (stat.st_size,stat.st_mtime_ns)!=(row.get('sourceSize'),row.get('sourceMtime')):raise ValueError('Кадърът е променен след измерването; преизчисли анализа.')
            key=(report_id,source_id,target_id)
            with self.lock:
                if key in self.alignments:self.alignments.move_to_end(key);return copy.deepcopy(self.alignments[key])
            from Backend.Analysis.alignment import register
            result=register(source,target) if not source.get('error') and not target.get('error') else None
            value=dict(matrix=result[0].tolist(),rms=float(result[1]),matched=int(result[2])) if result else dict(matrix=None,rms=None,matched=0)
            with self.lock:
                self.alignments[key]=value
                while len(self.alignments)>4096:self.alignments.popitem(last=False)
            return copy.deepcopy(value)
        if action=='sample':
            import numpy as np
            from Backend.Fits.images import pixels
            from Backend.Analysis.psf import analysis_plane
            ident=p.get('id');self.identifiers([ident]);edge=p.get('edge',768)
            if type(edge) is not int or not 128<=edge<=1536:raise ValueError('Работният размер е между 128 и 1536 пиксела.')
            row=self.service.catalog.record(ident);meta=row['meta'];maximum=self.service.settings.value['memoryMb']*1024**2
            if meta.get('width',0)*meta.get('height',0)*32>maximum:raise ValueError('Кадърът надвишава зададения RAM бюджет.')
            path=self.service.catalog.path(ident);before=path.stat();data,header,shape=pixels(path);image,channel=analysis_plane(data,header)
            step=max(1,math.ceil(max(image.shape)/edge));h,w=image.shape;h1=h//step;w1=w//step
            if min(h1,w1)<32:raise ValueError('Недостатъчен работен размер.')
            with np.errstate(invalid='ignore'):
                small=np.nanmean(image[:h1*step,:w1*step].reshape(h1,step,w1,step),axis=(1,3)).astype(np.float32)
            after=path.stat()
            if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('Кадърът е променен по време на четенето.')
            scale=np.array([[1/step,0,-(step-1)/(2*step)],[0,1/step,-(step-1)/(2*step)],[0,0,1.]],float)
            return dict(image=small,pixelToSample=scale,nativeShape=(h,w),step=step,channel=channel,signature=f'{before.st_size}:{before.st_mtime_ns}')
        if action=='warp':
            import numpy as np
            from Backend.Analysis.alignment import warp
            shape=p.get('shape');matrix=np.asarray(p.get('matrix'),float)
            if not isinstance(shape,(tuple,list)) or len(shape)!=2 or any(type(v) is not int or not 1<=v<=1536 for v in shape) or p.get('order',1) not in (0,1):raise ValueError('Невалиден размер за подравняване.')
            if matrix.shape!=(3,3) or not np.isfinite(matrix).all() or abs(np.linalg.det(matrix))<1e-12:raise ValueError('Невалидна проекция.')
            return warp(p['image'],p['matrix'],p['shape'],order=p.get('order',1),cval=p.get('cval',0))
        if action=='annotate':return self.annotate(owner,p)
        raise ValueError('Непозната операция на инспекционния SDK.')

    def annotate(self,owner,p):
        if p.get('present') is True and self.service.session.active:raise ValueError('Изчакай текущия анализ, преди да покажеш инспекционния отчет.')
        report_id=p.get('reportId');annotations=p.get('annotations')
        if not isinstance(report_id,str) or not re.fullmatch('[a-f0-9]{32}',report_id):raise ValueError('Невалиден отчет.')
        if not isinstance(annotations,list) or len(annotations)>5000:raise ValueError('Невалидни анотации.')
        with self.lock:
            original=self.service.quality.results.get(report_id)
            if not original:raise ValueError('Отчетът вече не е в текущата сесия. Пусни инспекцията отново.')
            report=copy.deepcopy(original);rows={r['id']:r for r in report['frames']};source='plugin.'+owner
            for item in annotations:
                if not isinstance(item,dict) or set(item)-{'id','warnings','quality','comparison'} or item.get('id') not in rows:raise ValueError('Анотация извън анализираните кадри.')
                warnings=item.get('warnings',[])
                if not isinstance(warnings,list) or len(warnings)>8:raise ValueError('Прекалено много предупреждения.')
                row=rows[item['id']];stat=self.service.catalog.path(item['id']).stat()
                if (stat.st_size,stat.st_mtime_ns)!=(row.get('sourceSize'),row.get('sourceMtime')):raise ValueError('Кадърът е променен след анализа; преизчисли го.')
                clean=[]
                for warning in warnings:
                    if not isinstance(warning,dict) or set(warning)-{'type','score','message','severity','origin'}:raise ValueError('Невалидно предупреждение.')
                    if warning.get('origin','algorithm') not in ('algorithm','ai'):raise ValueError('Невалиден източник на предупреждение.')
                    if warning.get('type') not in ('cloud','satellite','obstruction','light','quality') or warning.get('severity','medium') not in ('low','medium','high'):raise ValueError('Непознат вид предупреждение.')
                    score=warning.get('score');message=warning.get('message')
                    if type(score) not in (int,float) or not math.isfinite(score) or not 0<=score<=1 or not isinstance(message,str) or not message or len(message)>400:raise ValueError('Невалидна оценка или обяснение.')
                    clean.append(dict(warning,source=source,confidence='candidate'))
                row['warnings']=[w for w in row.get('warnings',[]) if w.get('source')!=source]+clean
                if 'quality' in item:
                    value=item['quality']
                    if value is not None and (type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=100):raise ValueError('Невалиден индекс на качество.')
                    row.setdefault('inspectionMetrics',{})[source]=dict(quality=value)
                if 'comparison' in item:
                    comparison=item['comparison']
                    if not isinstance(comparison,dict) or set(comparison)-{'coverage','references','matchedStars','reason'}:raise ValueError('Невалидни данни за сравнение.')
                    coverage=comparison.get('coverage');references=comparison.get('references');matched=comparison.get('matchedStars');reason=comparison.get('reason','')
                    if coverage is not None and (type(coverage) not in (int,float) or not math.isfinite(coverage) or not 0<=coverage<=100):raise ValueError('Покритието е от 0 до 100%.')
                    if type(references) is not int or not 0<=references<=16 or type(matched) is not int or not 0<=matched<=2000 or not isinstance(reason,str) or len(reason)>400:raise ValueError('Невалиден брой референции, звезди или обяснение.')
                    row.setdefault('inspectionMetrics',{}).setdefault(source,{}).update(coverage=coverage,references=references,matchedStars=matched,reason=reason)
                row['recommended']='exclude' if any(w.get('severity')=='high' for w in row['warnings']) else 'review' if row['warnings'] else 'approved'
                row['statusText']='Предложен за изключване — провери' if row['recommended']=='exclude' else 'За преглед' if row['warnings'] else 'Без отклонения'
            atomic_json(self.service.store.folder/'Reports'/(report_id+'.json'),report)
            # Only annotation fields change in RAM. Never replace concurrently
            # updated manual decisions with the earlier report snapshot.
            current_rows={}
            for frame in original['frames']:current_rows.setdefault(frame['id'],frame)
            for item in annotations:
                current=current_rows[item['id']];updated=rows[item['id']]
                for key in ('warnings','recommended','statusText','inspectionMetrics'):
                    if key in updated:current[key]=updated[key]
        self.service.session.decisions()
        self.service.session.inspection_result(
            report_id,
            [item['id'] for item in annotations if item.get('warnings')],
            highlight=p.get('highlight') is True,
            present=p.get('present') is True,
        )
        return dict(annotated=len(annotations),decisionsChanged=False,originalsChanged=False)
