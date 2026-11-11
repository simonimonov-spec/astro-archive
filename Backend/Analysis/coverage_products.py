"""Persistent coverage and an arithmetic-mean diagnostic preview, originals read-only."""
from __future__ import annotations
import hashlib,json,math,re,threading,uuid
from pathlib import Path
import numpy as np
from astropy.io import fits
from PIL import Image
from Backend.Foundation.runtime import atomic_json
from Backend.Fits.images import pixels,gray
from Backend.Analysis.alignment import project,warp
from Backend.Analysis.psf import analysis_plane

VERSION='coverage-mean-2'

class CoverageProducts:
    def __init__(self,quality):self.quality=quality;self.lock=threading.RLock()
    @property
    def folder(self):return self.quality.store.folder/'Coverage'

    def image(self,key):
        if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{64}',key):raise ValueError('Невалиден аналитичен ресурс.')
        path=self.folder/(key+'.png')
        if not (self.folder/(key+'.json')).is_file() or not path.is_file():raise FileNotFoundError('Прегледът не е наличен. Създай покритието отново.')
        return path.read_bytes()

    def build(self,result_id,cancel,progress,ids=None):
        with self.lock:return self._build(result_id,cancel,progress,ids)

    def _build(self,result_id,cancel,progress,ids):
        if cancel.is_set():raise InterruptedError('Създаването на покритието е отменено.')
        q=self.quality;report=q.results[result_id];selected=set(ids) if ids is not None else None
        chosen=[r for r in report['frames'] if r.get('decision')!='excluded' and (selected is None or r['id'] in selected) and q.catalog.record(r['id']).get('decision')!='excluded']
        requested=[r for r in chosen if not r.get('error')];selection_ids=sorted(r['id'] for r in chosen)
        rows=[r for r in requested if r.get('transform')];skipped=len(chosen)-len(rows)
        if not rows:raise ValueError('Няма кадри с известно общо разположение. Качеството им остава независимо от това; за мозайка е нужно общо звездно или WCS решение.')
        if len({r['reference'] for r in rows})!=1:raise ValueError('Избери една координатна група. Различните телескопи/филтри още нямат общо решение за покритие.')
        signatures=[]
        for r in rows:
            stat=q.catalog.path(r['id']).stat()
            if (stat.st_size,stat.st_mtime_ns)!=(r.get('sourceSize'),r.get('sourceMtime')):raise ValueError('Кадър е променен след анализа. Преизчисли анализа преди покритието.')
            signatures.append([r['id'],stat.st_size,stat.st_mtime_ns,r['transform'],r['meta'].get('exposure'),r['width'],r['height']])
        key=hashlib.sha256(json.dumps([VERSION,sorted(signatures,key=lambda s:s[0])],sort_keys=True).encode()).hexdigest()
        self.folder.mkdir(parents=True,exist_ok=True);manifest=self.folder/(key+'.json');path=self.folder/(key+'.fits');png=self.folder/(key+'.png')
        if manifest.is_file() and path.is_file() and png.is_file():
            result=json.loads(manifest.read_text(encoding='utf-8'));result.update(cached=True,skipped=skipped,selectionIds=selection_ids)
            report.update(coveragePath=str(path),coverageKey=key);progress(1,1,'Използвано е запазеното покритие и усредненият преглед; FITS пикселите не са прочетени отново.');return result
        corners=[]
        for r in rows:corners.extend(project(np.asarray(r['transform']),np.array([[0,0],[r['width'],0],[r['width'],r['height']],[0,r['height']]])))
        low=np.floor(np.min(corners,axis=0));high=np.ceil(np.max(corners,axis=0));width,height=(high-low).astype(int)
        budget=q.catalog.settings.value['memoryMb']*1024**2;source=max(r['width']*r['height'] for r in rows)
        if min(width,height)<=0 or width*height*20+source*24>budget:raise ValueError('Общото покритие надвишава RAM бюджета. Намали избора или увеличи лимита в настройките.')
        counts=np.zeros((height,width),np.uint32);seconds=np.zeros((height,width),np.float64)
        ys=max(1,math.ceil(height/600));xs=max(1,math.ceil(width/800));shape=(math.ceil(height/ys),math.ceil(width/xs));scale=np.diag([1/xs,1/ys,1.])
        total=np.zeros(shape,np.float64);contributions=np.zeros(shape,np.uint32)
        for index,r in enumerate(rows,1):
            if cancel.is_set():raise InterruptedError('Създаването на покритието е отменено.')
            file=q.catalog.path(r['id']);before=file.stat();data,header,_=pixels(file);valid=np.isfinite(gray(data)).astype(np.float32)
            if (before.st_size,before.st_mtime_ns)!=(r['sourceSize'],r['sourceMtime']):raise ValueError('Кадър е променен след анализа; покритието не е запазено.')
            projected=warp(valid,r['transform'],(height,width),origin=low,order=0)>.5
            exposure=float(r['meta'].get('exposure') or 0);exposure=exposure if math.isfinite(exposure) and exposure>0 else 0
            counts+=projected;seconds+=projected*exposure
            plane,_=analysis_plane(data,header);matrix=scale@np.asarray(r['transform']);origin=low/np.array([xs,ys])
            weight=warp(np.isfinite(plane).astype(np.float32),matrix,shape,origin=origin,order=1)
            sample=warp(np.nan_to_num(plane),matrix,shape,origin=origin,order=1);covered=weight>.999
            total[covered]+=sample[covered]/weight[covered];contributions+=covered
            after=file.stat()
            if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('Кадър е променен по време на четенето; покритието не е запазено.')
            del data,plane,valid,projected,sample,weight
            progress(index,len(rows),'Покритие, експозиция и усреднен диагностичен преглед')
        average=np.divide(total,contributions,out=np.zeros_like(total),where=contributions>0);valid=contributions>0
        values=average[valid]
        lo,hi=np.percentile(values,[.5,99.8]) if len(values) else (0,1)
        stretched=np.arcsinh(15*np.clip((average-lo)/max(float(hi-lo),1e-8),0,1))/np.arcsinh(15)
        rgba=np.zeros((*shape,4),np.uint8);rgba[...,:3]=np.rint(stretched[...,None]*255).astype(np.uint8);rgba[...,3]=valid*255
        header=fits.Header();header['MAPTYPE']='FRAMECOUNT';header['NCOMBINE']=len(rows);header['XOFFSET']=float(low[0]);header['YOFFSET']=float(low[1]);header['COVVERS']=VERSION
        temporary=path.with_name(key+'.'+uuid.uuid4().hex+'.tmp')
        try:
            fits.HDUList([fits.PrimaryHDU(counts,header),fits.ImageHDU(seconds,name='EXPOSURE'),fits.ImageHDU(average.astype(np.float32),name='MEAN_PREVIEW')]).writeto(temporary)
            if cancel.is_set():raise InterruptedError()
            temporary.replace(path);Image.fromarray(rgba).save(temporary,format='PNG');temporary.replace(png)
        finally:temporary.unlink(missing_ok=True)
        unknown=sum(not r['meta'].get('exposure') or not math.isfinite(float(r['meta']['exposure'])) or float(r['meta']['exposure'])<=0 for r in rows)
        result=dict(path=str(path),key=key,averageAsset=key,width=int(width),height=int(height),previewWidth=shape[1],previewHeight=shape[0],maximum=int(counts.max()),frames=len(rows),skipped=skipped,selectionIds=selection_ids,exposureKnown=not unknown,unknownExposures=unknown,counts=counts[::ys,::xs].tolist(),seconds=seconds[::ys,::xs].tolist(),cached=False,
            note='Покритието и експозицията са точни в FITS. Усредненият монохромен преглед е sum / брой валидни приноси, без отхвърляне и без фотометрична калибрация. Червените слабо покрити зони са предупреждение за по-малко експозиционно време, не измерена карта на шума.')
        atomic_json(manifest,result);report.update(coveragePath=str(path),coverageKey=key);return result
