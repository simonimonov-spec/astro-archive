"""Bounded local-frame mosaic comparison; no direct access to archive paths."""
import collections,hashlib,json,math
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
import numpy as np
from . import engine,model,quality
from .geometry import FieldMap


class Samples:
    def __init__(self,pipeline,edge,budget):self.pipeline=pipeline;self.edge=edge;self.budget=budget;self.items=collections.OrderedDict();self.bytes=0
    def get(self,row):
        key=(row['id'],row.get('sourceSize'),row.get('sourceMtime'))
        with self.pipeline.io_lock:
            if key in self.items:
                current=self.pipeline.ctx.inspection('source',dict(id=row['id']))
                if (current['size'],current['mtime'])!=key[1:]:raise ValueError('Кадърът е променен; преизчисли анализа.')
                self.items.move_to_end(key);return self.items[key][0]
            sample=self.pipeline.ctx.inspection('sample',dict(id=row['id'],edge=self.edge))
            if sample['signature']!=f"{key[1]}:{key[2]}":raise ValueError('Кадърът е променен след измерването; преизчисли анализа.')
            image=np.asarray(sample['image'],np.float32);exposure=float(row['meta'].get('exposure') or 0)
            if exposure>0:image=image/exposure
            valid=np.isfinite(image);image.setflags(write=False);valid.setflags(write=False)
            value=dict(sample,image=image,valid=valid);size=image.nbytes+valid.nbytes
            if size<=self.budget:
                self.items[key]=(value,size);self.bytes+=size
                while self.bytes>self.budget:
                    _,(_,old)=self.items.popitem(last=False);self.bytes-=old
            return value


def base(row):
    return dict(id=row['id'],frameIndex=row.get('frameIndex'),name=row.get('name',''),night=row['meta'].get('night',''),object=row['meta'].get('object',''),telescope=row['meta'].get('telescope',''),
                decision=row.get('decision','approved'),sourceSize=row.get('sourceSize'),sourceMtime=row.get('sourceMtime'),comparisonCoverage=0.,referenceFrames=0,matchedStars=0,referenceIds=[],comparisonMethod='local-frame',qualityFeatures=None)


def run(pipeline,ids,report_id,options,cancel,progress):
    from .pipeline import grouping,atomic,assess
    def prepare(done,total,label):progress(done*.28/max(1,total),1,'Звездни измервания и подравняване · '+label)
    report=pipeline.ctx.inspection('prepare',dict(ids=ids,reportId=report_id,force=options['force']),cancel,prepare)
    rows=report['frames'];limits=pipeline.ctx.inspection('limits');memory=limits['memoryMb']*1024**2
    workers=max(1,min(options['workers'],limits['workers'],limits['memoryMb']//256))
    edge=min(options['edge'],max(256,int(math.sqrt(memory*.6/(workers*160)))))
    signature=hashlib.sha256(json.dumps(dict(featureSchema=4,algorithm=engine.VERSION,quality=report.get('algorithm'),detector=report.get('detector'),model=options['model']['digest'],qualityModel=options.get('qualityModel',{}).get('digest') if options.get('qualityModel') else 'heuristic-v1',edge=edge,frames=[(r['id'],r.get('sourceSize'),r.get('sourceMtime'),r.get('transform'),r.get('reference'),r['meta'].get('exposure'),grouping(r)) for r in sorted(rows,key=lambda r:r['id'])]),sort_keys=True).encode()).hexdigest()
    cache=pipeline.folder/'Reports'/(signature+'.json')
    if cache.exists() and not options['force']:
        result=json.loads(cache.read_text(encoding='utf-8'));result.update(reportId=report['id'],cached=True)
        for row in result['rows']:
            if row.get('features'):assess(row,options)
        progress(1,1,'Използвани са запазените локални AI измервания; FITS пикселите не са прочетени отново');return result
    groups={};outputs=[];completed=0;samples=Samples(pipeline,edge,max(1,memory//5));recovered=0
    for row in rows:groups.setdefault(grouping(row),[]).append(row)
    for group in groups.values():
        if cancel.is_set():raise InterruptedError('AI инспекцията е отменена.')
        group=sorted(group,key=lambda r:r.get('frameIndex',0))
        fields=FieldMap(group,pipeline.ctx,report['id'],cancel,lambda done,total,label:progress(.28,1,f'{label} · {done}/{total}'))
        recovered+=fields.recovered
        def inspect(row):
            if cancel.is_set():raise InterruptedError('AI инспекцията е отменена.')
            result=base(row)
            if row.get('error'):return dict(result,error=row['error'],reliable=False,warnings=[],status='Не може да се измери',qualityReason=row['error'])
            eligible=[]
            for source,local,overlap in fields.candidates(row):
                if cancel.is_set():raise InterruptedError('AI инспекцията е отменена.')
                photo=engine.photometry(dict(row,transform=np.linalg.inv(local).tolist()),source)
                if photo['matched']>=8 and photo['ratio'] is not None and math.isfinite(photo['ratio']) and photo['ratio']>0:
                    eligible.append((source,local,overlap,photo,1/photo['ratio']))
            baseline=float(np.percentile([r[4] for r in eligible],85)) if eligible else 1.
            eligible=[r for r in eligible if r[4]>=baseline*.78]
            eligible.sort(key=lambda r:r[4]*r[2]/(1+max(0,r[0].get('fwhm') or 5)/20),reverse=True)
            if len(eligible)<3:
                reason=f'Надеждни локални референции: {len(eligible)}; нужни са поне 3. Липсата на съвпадение не е лошо качество.'
                return dict(result,reliable=False,uncertainty=reason,qualityReason=reason,warnings=[],status='Несигурно — недостатъчно локално сравнение')
            target=samples.get(row);image=target['image'];valid=target['valid'];scale=np.asarray(target['pixelToSample'])
            reference_images=[];reference_ids=[];photos=[];star_mask=engine.star_mask(image.shape,row.get('starList',[]),scale)
            for source,local,_,photo,brightness in eligible[:9]:
                if cancel.is_set():raise InterruptedError('AI инспекцията е отменена.')
                try:
                    sample=samples.get(source);mapping=scale@local@np.linalg.inv(sample['pixelToSample'])
                    warped=pipeline.ctx.inspection('warp',dict(image=np.nan_to_num(sample['image']),matrix=mapping,shape=image.shape))
                    mask=pipeline.ctx.inspection('warp',dict(image=sample['valid'].astype(np.float32),matrix=mapping,shape=image.shape))>.98
                    warped[~mask]=np.nan;warped/=max(.08,brightness/baseline)
                    reference_images.append(warped);reference_ids.append(source['id']);photos.append(photo)
                    star_mask|=engine.star_mask(image.shape,source.get('starList',[]),scale@local)
                except (OSError,ValueError):continue
            reference=None;reference_noise=None
            if len(reference_images)>=3:
                stack=np.stack(reference_images);counts=np.isfinite(stack).sum(axis=0)
                # Avoid global warnings filters in concurrent workers. Empty
                # pixels get temporary zeros and are masked out afterwards.
                stack[:,counts==0]=0
                reference=np.nanmedian(stack,axis=0);reference_noise=1.4826*np.nanmedian(abs(stack-reference),axis=0)/np.sqrt(np.maximum(1,counts))
                reference[counts<3]=np.nan
            transparency=1/max(1e-8,baseline)
            comparison=100*float((valid&np.isfinite(reference)).mean()) if reference is not None else 0.
            result.update(comparisonCoverage=round(comparison,2),referenceFrames=len(reference_images),referenceIds=reference_ids,matchedStars=min((p['matched'] for p in photos),default=0),matrix=np.eye(3).tolist(),shape=list(image.shape),edge=edge)
            try:
                analysis=engine.extract(image,reference,valid=valid,reference_noise=reference_noise,stars=star_mask,transparency=transparency,
                                        flux_scatter=float(np.median([p['scatter'] for p in photos])) if photos else 0.,star_loss=float(np.median([p['loss'] for p in photos])) if photos else 0.,reference_count=len(reference_images))
            except ValueError as error:return dict(result,error=str(error),reliable=False,warnings=[],status='Несигурно — недостатъчно сравнение',qualityReason=str(error))
            reasons=[analysis.get('uncertainty','')]
            if not row['meta'].get('exposure'):reasons.append('Липсва валидна експозиция за сравнение')
            if row['id'] not in fields.positions:reasons.append('Няма надеждно локално подравняване; това не е оценка за лошо качество')
            analysis['reliable'] &= bool(row['meta'].get('exposure')) and result['matchedStars']>=8
            result.update(features=analysis['features'],regions=analysis['regions'],reliable=bool(analysis['reliable']),uncertainty=' · '.join(r for r in reasons if r),qualityReason=' · '.join(r for r in reasons if r),
                          transparency=float(transparency),noise=float(analysis['noise']),comparisonCoverage=round(100*analysis['features']['overlap'],2) if reference is not None else 0.)
            from .analyzers import run_all
            result['analyzerData']=run_all(row,group=group,analysis=analysis,image=image*float(row['meta'].get('exposure') or 1),valid=valid,star_mask=star_mask,reference=reference)
            result['scores']={};return result
        with ThreadPoolExecutor(max_workers=workers,thread_name_prefix='FrameInspector') as pool:
            pending={};iterator=iter(group)
            def submit():
                try:row=next(iterator)
                except StopIteration:return
                pending[pool.submit(inspect,row)]=row
            for _ in range(workers):submit()
            while pending:
                if cancel.is_set():
                    for future in pending:future.cancel()
                    raise InterruptedError('AI инспекцията е отменена.')
                ready,_=wait(pending,timeout=.2,return_when=FIRST_COMPLETED)
                for future in ready:
                    row=pending.pop(future)
                    try:outputs.append(future.result())
                    except InterruptedError:raise
                    except Exception as error:outputs.append(dict(base(row),error=str(error),reliable=False,warnings=[],status='Несигурно — грешка при измерване',qualityReason=str(error)))
                    completed+=1;progress(.28+.72*completed/max(1,len(rows)),1,f'AI локална инспекция · {completed}/{len(rows)} кадъра');submit()
    outputs.sort(key=lambda row:(row.get('frameIndex') or 0,row['name']));quality.measurements(outputs,rows)
    from .analyzers import run_all
    source_by_id={r['id']:r for r in rows}
    for item in outputs:
        original=source_by_id[item['id']]
        meta=dict(original.get('meta',{}));headers=meta.get('headers',{})
        from .analyzers.base import number
        meta.update(pixelScale=number(original.get('pixelScale')),gain=number(headers.get('GAIN')),bayer=int(bool(headers.get('BAYERPAT'))))
        item.update(width=original.get('width'),height=original.get('height'),meta=meta)
        from .resnet_model import MEASUREMENTS
        item['measurements']={k:original.get(k) for k in MEASUREMENTS}
        if not item.get('error') and 'analyzerData' not in item:
            item['analyzerData']=run_all(original,group=groups[grouping(original)])
        assess(item,options)
    result=dict(id=signature,reportId=report['id'],rows=outputs,algorithm=engine.VERSION,modelVersion=options['model']['version'],modelProvenance=options['model']['provenance'],cached=False,decisionsChanged=False,originalsChanged=False,localAlignmentsRecovered=recovered)
    atomic(cache,result)
    for old in sorted(cache.parent.glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[30:]:old.unlink(missing_ok=True)
    progress(1,1,'AI инспекцията приключи. Покритието е спрямо всеки отделен кадър. Подборът не е променян.');return result
