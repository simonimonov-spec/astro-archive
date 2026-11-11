"""Aligned selection image and aperture light curves. Originals are read-only.

The selection stack is for locating sources only; all fluxes are measured on
native input pixels. No absolute magnitude/catalogue identity is inferred.
"""
from __future__ import annotations
import base64, io, json, math, time, uuid
from Backend.Foundation.runtime import atomic_json, parallel
from Backend.Fits.images import pixels, gray
from Backend.Analysis.alignment import project,warp


def aperture(argument):
    ident,path,meta,transform,stars,radius=argument
    import numpy as np
    from astropy.stats import sigma_clipped_stats
    from astropy.time import Time,TimeDelta
    from photutils.aperture import CircularAperture, aperture_photometry
    rows=[]
    try:
        image=gray(pixels(path)[0]);inverse=np.linalg.inv(np.array(transform,float))
        exposure=meta.get('exposure');stamp=meta.get('observed','');scale=str(meta.get('headers',{}).get('TIMESYS','UTC')).lower()
        if scale not in Time.SCALES:scale='utc'
        try:
            t=Time(stamp.rstrip('Z'),format='isot',scale=scale)
            if exposure and exposure>0:t=t+TimeDelta(exposure/2,format='sec')
            jd=float(t.utc.jd);utc=t.utc.isot
        except (ValueError,TypeError):jd=None;utc=stamp
        for star in stars:
            x,y=project(inverse,[[star['x'],star['y']]])[0]
            row=dict(id=ident+':'+star['id'],fileId=ident,starId=star['id'],starName=star['name'],name=meta.get('filename',''),
                     night=meta.get('night',''),filter=meta.get('filter',''),utc=utc,jd=jd,exposure=exposure,x=float(x),y=float(y),
                     flux=None,fluxRate=None,instrumentalMagnitude=None,background=None,backgroundError=None,warnings=[])
            outer=radius*3
            if x-outer<0 or y-outer<0 or x+outer>=image.shape[1] or y+outer>=image.shape[0]:
                row['warnings'].append('Звездата или фоновият пръстен е извън кадъра.');rows.append(row);continue
            # Recenter conservatively around the registered position, never jump to a distant source.
            ix,iy=round(x),round(y);yy,xx=np.mgrid[iy-3:iy+4,ix-3:ix+4];cut=image[iy-3:iy+4,ix-3:ix+4]
            finite=np.isfinite(cut)
            if not finite.all():row['warnings'].append('Невалидни пиксели около звездата.');rows.append(row);continue
            weights=np.maximum(cut-np.median(cut),0);total=weights.sum()
            if total>0:
                cx=float((xx*weights).sum()/total);cy=float((yy*weights).sum()/total)
                if math.hypot(cx-x,cy-y)<=2:x,y=cx,cy
            y0,y1=int(y-outer)-1,int(y+outer)+2;x0,x1=int(x-outer)-1,int(x+outer)+2
            cut=image[y0:y1,x0:x1];yy,xx=np.mgrid[y0:y1,x0:x1];distance=np.hypot(xx-x,yy-y)
            sky=cut[(distance>=radius*2)&(distance<=outer)&np.isfinite(cut)]
            if sky.size<20 or not np.all(np.isfinite(cut[distance<=radius+1])):
                row['warnings'].append('Недостатъчно валидни пиксели за измерване.');rows.append(row);continue
            _,background,sigma=sigma_clipped_stats(sky,sigma=3,maxiters=5)
            ap=CircularAperture([(x-x0,y-y0)],r=radius)
            flux=float(aperture_photometry(np.nan_to_num(cut),ap,method='exact')['aperture_sum'][0]-background*ap.area)
            error=float(sigma*math.sqrt(ap.area+ap.area**2/sky.size))
            row.update(x=x,y=y,flux=flux,background=float(background),backgroundError=error,
                       fluxRate=flux/exposure if exposure and exposure>0 else None)
            if not exposure or exposure<=0:row['warnings'].append('Липсва положителна експозиция; потокът не е нормализиран.')
            if jd is None:row['warnings'].append('Липсва валиден момент на наблюдение.')
            if flux<=max(0,5*error):row['warnings'].append('Нисък сигнал; идентификацията и потокът са несигурни.')
            elif row['fluxRate']:row['instrumentalMagnitude']=-2.5*math.log10(row['fluxRate'])
            saturation=meta.get('headers',{}).get('SATURATE')
            if saturation is not None and np.max(cut[distance<=radius])>=float(saturation):row['warnings'].append('Възможно насищане според SATURATE.')
            rows.append(row)
    except Exception as error:
        rows=[dict(id=ident+':'+s['id'],fileId=ident,starId=s['id'],starName=s['name'],name=meta.get('filename',''),night=meta.get('night',''),filter=meta.get('filter',''),jd=None,utc=meta.get('observed',''),flux=None,fluxRate=None,instrumentalMagnitude=None,warnings=[str(error)]) for s in stars]
    return rows


class TimeSeries:
    def __init__(self,quality):self.quality=quality;self.catalog=quality.catalog;self.references={};self.results={}
    def validate_source(self,row):
        path=self.catalog.path(row['id']);stat=path.stat()
        if (stat.st_size,stat.st_mtime_ns)!=(row['sourceSize'],row['sourceMtime']):raise ValueError('Кадърът е променен след анализа. Преизчисли измерванията: '+row['name'])
        return path
    def reference(self,result_id,ids,cancel,progress):
        import numpy as np
        from PIL import Image
        report=self.quality.results[result_id];chosen=set(ids)
        if report.get('kind')!='frames':raise ValueError('Времевите измервания изискват отделни кадри, а не стакове.')
        rows=[r for r in report['frames'] if r['id'] in chosen and r.get('decision')!='excluded' and r.get('transform') and not r.get('error') and self.catalog.record(r['id']).get('decision')!='excluded']
        if not rows:raise ValueError('Няма надеждно подравнени одобрени кадри.')
        if len({r['reference'] for r in rows})!=1:raise ValueError('За избор на звезди избери един обект, телескоп, филтър и размер.')
        ref=next(r for r in report['frames'] if r['id']==rows[0]['reference']);h,w=ref['height'],ref['width']
        if h*w*48>self.catalog.settings.value['memoryMb']*1024**2:raise ValueError('Изображението за избор надвишава RAM бюджета.')
        total=np.zeros((h,w),np.float64);weight=np.zeros((h,w),np.float32);swap=np.array([[0,1],[1,0]])
        for index,row in enumerate(rows,1):
            if cancel.is_set():raise InterruptedError()
            image=gray(pixels(self.validate_source(row))[0]);valid=np.isfinite(image)
            projected=warp(np.where(valid,image,0),row['transform'],(h,w),order=1)
            mask=warp(valid.astype(np.float32),row['transform'],(h,w),order=1)
            total+=projected;weight+=mask;progress(index,len(rows),'Подравняване и събиране на изображението за избор на звезди')
        image=np.divide(total,weight,out=np.zeros_like(total),where=weight>0)
        step=max(1,math.ceil(max(h,w)/1200));small=image[::step,::step];valid=small[np.isfinite(small)]
        lo,hi=np.percentile(valid,[1,99.8]);display=np.arcsinh(20*np.clip((small-lo)/max(hi-lo,1e-12),0,1))/np.arcsinh(20)
        stream=io.BytesIO();Image.fromarray((display*255).astype(np.uint8)).save(stream,format='PNG')
        stars=[];wcs=None
        if ref['meta'].get('headers',{}).get('CTYPE1') and ref['meta'].get('headers',{}).get('CTYPE2'):
            try:
                from astropy.wcs import WCS
                from astropy.io.fits import Header
                candidate=WCS(Header(ref['meta']['headers'])).celestial
                if candidate.has_celestial:wcs=candidate
            except (ValueError,TypeError):pass
        for i,s in enumerate(ref['starList']):
            star=dict(id='star-'+str(i+1),name='Звезда '+str(i+1),x=s['x'],y=s['y'],fwhm=s['fwhm'],ra=None,dec=None)
            if wcs is not None:
                try:star['ra'],star['dec']=map(float,wcs.all_pix2world([[s['x'],s['y']]],0)[0])
                except (ValueError,TypeError):pass
            stars.append(star)
        if not stars:raise ValueError('Не са намерени надеждни звезди за избор.')
        ident=uuid.uuid4().hex;document=dict(id=ident,resultId=result_id,ids=[r['id'] for r in rows],reference=ref['id'],width=w,height=h,stars=stars,
            image='data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode(),
            note='Средно подравнено изображение само за избор. Потокът се измерва върху оригиналните кадри; имената са локални, а не каталожна идентификация.')
        self.references[ident]=document;return document
    def run(self,reference_id,star_ids,cancel,progress,radius=5):
        if type(radius) not in (int,float) or not math.isfinite(radius) or not 2<=radius<=30:raise ValueError('Апертурата трябва да е между 2 и 30 пиксела.')
        reference=self.references[reference_id];stars=[s for s in reference['stars'] if s['id'] in set(star_ids)]
        if not stars or len(stars)>20:raise ValueError('Избери от 1 до 20 звезди.')
        report=self.quality.results[reference['resultId']];wanted=set(reference['ids']);frames=[r for r in report['frames'] if r['id'] in wanted and r.get('decision')!='excluded' and self.catalog.record(r['id']).get('decision')!='excluded']
        if not frames:raise ValueError('Няма одобрени кадри за измерване.')
        arguments=[(r['id'],str(self.validate_source(r)),dict(r['meta'],filename=r['name']),r['transform'],stars,radius) for r in frames]
        maximum=max(r['width']*r['height']*32 for r in frames);workers=max(1,min(self.catalog.settings.value['workers'],self.catalog.settings.value['memoryMb']*1024**2//max(1,maximum)))
        rows=[];done=0
        for _,values in parallel(aperture,arguments,workers,cancel,processes=True):
            rows.extend(values);done+=1;progress(done,len(frames),'Измерване на избраните звезди върху всеки оригинален кадър')
        rows.sort(key=lambda r:(r['jd'] is None,r['jd'] or 0,r['starId']))
        ident=uuid.uuid4().hex;result=dict(id=ident,type='timeseries',created=time.time(),stars=stars,radius=radius,rows=rows,
            sourceIds=[r['id'] for r in frames],sourceMeta=frames[0]['meta'],limitations=['Поток в ADU и ADU/s; величините са инструментални, без калибрирана нулева точка.','Нужни са калибрирани, линейни кадри; цветният сензор се измерва като сив канал.','backgroundError отчита фоновия шум; не включва пълна фотонна/детекторна грешка.','Без Gaia идентификация и барицентрична времева корекция. Времето е средата на експозицията в UTC.'])
        self.results[ident]=result;atomic_json(self.catalog.store.folder/'Reports'/(ident+'.json'),result);return result
