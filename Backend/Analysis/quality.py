"""Quality metrics and conservative artifact indications; no photometry workflow."""
from __future__ import annotations
import json, math, re, threading, time, uuid
from pathlib import Path
from Backend.Foundation.runtime import parallel, atomic_json, read_json
from Backend.Fits.images import pixels, gray,metadata
from Backend.Analysis.grading import VERSION as STANDARD_VERSION, standard_review

VERSION='quality-5-gaussian-green-1'

def noise_grid(image):
    import numpy as np
    height,width=image.shape;tile=max(32,math.ceil(max(height,width)/20));rows=[];background=[]
    for y in range(0,height,tile):
        line=[];base=[]
        for x in range(0,width,tile):
            cut=image[y:y+tile,x:x+tile];valid=cut[np.isfinite(cut)]
            if valid.size<32:line.append(None);base.append(None);continue
            center=float(np.median(valid));sigma=float(1.4826*np.median(np.abs(valid-center)))
            trimmed=valid[np.abs(valid-center)<3*max(sigma,1e-12)]
            line.append(float(np.std(trimmed)) if trimmed.size>16 else sigma);base.append(center)
        rows.append(line);background.append(base)
    values=image[np.isfinite(image)];limit=max(1,image.size//500000);sample=values[::limit]
    center=float(np.median(sample));sigma=float(1.4826*np.median(np.abs(sample-center)))
    dx=image[:,1:]-image[:,:-1];dy=image[1:]-image[:-1]
    diff=np.concatenate([dx.ravel()[::max(1,dx.size//200000)],dy.ravel()[::max(1,dy.size//200000)]])
    diff=diff[np.isfinite(diff)];difference_noise=float(1.4826*np.median(np.abs(diff-np.median(diff)))/math.sqrt(2)) if diff.size else None
    return dict(noise=rows,background=background,tile=tile,width=width,height=height,backgroundMedian=center,robustSigma=sigma,differenceNoise=difference_noise,
        note='Оценки от наличния стак. Мъглявини, градиенти, разтягане и шумопотискане влияят на резултата; броят входни кадри не се възстановява от пикселите.')

def measure(argument):
    ident,path,kind,*extra=argument
    detector=extra[0] if extra else None
    import numpy as np
    from scipy.ndimage import label, find_objects, gaussian_filter
    from Backend.Analysis.psf import analysis_plane,background_statistics,detect_stars,detection_settings,angular_scale,saturation_level
    data,header,shape=pixels(path);image,channel=analysis_plane(data,header);valid=np.isfinite(image)
    if valid.sum()<100:raise ValueError('Недостатъчно валидни пиксели.')
    background,noise=background_statistics(image)
    settings=detection_settings(detector);stars,candidates=detect_stars(image,background,noise,settings,saturation=saturation_level(header))
    clean=np.where(valid,image,background)-background
    values=image[valid]
    unsaturated=[star for star in stars if not star.get('saturated')]
    metric=lambda key:float(np.mean([star[key] for star in unsaturated])) if unsaturated else None
    fwhm,fwhmy=metric('fwhm'),metric('fwhmY')
    # The detector orders stars by the Gaussian integral, up to a constant.
    bright_stars = unsaturated[:12]
    bright_signal = float(np.median([star['amplitude'] * star['fwhm'] * star['fwhmY']
                                    for star in bright_stars])) if len(bright_stars) == 12 else None
    result=dict(id=ident,width=shape[1],height=shape[0],stars=len(stars),fittedStars=len(stars),
        measuredStars=len(unsaturated),saturatedStars=len(stars)-len(unsaturated),candidates=candidates,starList=stars[:256],
        fwhm=fwhm,fwhmY=fwhmy,hfr=metric('hfr'),roundness=fwhmy/fwhm if fwhm and fwhmy else None,
        eccentricity=metric('eccentricity'),weightedFwhm=None,peakSnr=metric('peakSnr'),
        background=metric('background') if unsaturated else background,skyMedian=background,noise=noise,
        brightStarSignal=bright_signal,
        mean=float(np.mean(values)),stdDev=float(np.std(values)),minimum=float(values.min()),maximum=float(values.max()),
        validPixels=int(valid.sum()),invalidPixels=int(image.size-valid.sum()),measurementChannel=channel,
        measurementModel='Gaussian',detectionSettings=settings,pixelScale=angular_scale(header),
        warnings=[],transform=None,registrationRms=None,matchedStars=0,shiftX=None,shiftY=None,rotation=None,scale=None)
    if not unsaturated:result['warnings'].append(dict(type='no-psf',severity='high',confidence='measured',message='Няма надеждно измерени ненаситени звезди. FWHM не се заменя с нула. Провери кадъра.'))
    # Spatial diagnostics operate on a reduced image and only propose review.
    small=clean[::max(1,image.shape[0]//512),::max(1,image.shape[1]//512)]
    blurred=gaussian_filter(small,3);spread=float(np.percentile(blurred,95)-np.percentile(blurred,5))
    result['backgroundVariation']=spread;result['darkFraction']=float(np.mean(small < -4*noise))
    # Subtract the local background so bright stars and broad sky structure do
    # not set the cutoff for a narrow trail. Measure elongation in its own axes.
    residual = small - gaussian_filter(small, 8)
    labels, _ = label(residual > 15 * noise, structure=np.ones((3, 3)))
    for ident, region in enumerate(find_objects(labels), 1):
        if region is None or max(part.stop - part.start for part in region) < .14 * max(small.shape):
            continue
        coordinates = np.column_stack(np.nonzero(labels[region] == ident))
        eigenvalues, axes = np.linalg.eigh(np.cov(coordinates, rowvar=False))
        if eigenvalues[1] <= 100 * max(eigenvalues[0], 1) or np.ptp(coordinates @ axes[:, 1]) < .2 * max(small.shape):
            continue
        result['warnings'].append(dict(type='bright-track', severity='high', confidence='candidate',
                                       message='Възможна ярка линейна следа. Провери кадъра.'))
        break
    if result['darkFraction']>.15:result['warnings'].append(dict(type='obstruction',severity='high',confidence='candidate',message='Голяма тъмна област: възможно препятствие или реална структура.'))
    if kind=='stacks':result['noiseMap']=noise_grid(image)
    return result

def measure_safe(argument):
    try:return measure(argument)
    except Exception as error:return dict(id=argument[0],error=str(error),warnings=[dict(type='unreadable',severity='high',confidence='confirmed',message=str(error))],starList=[],stars=0,transform=None)

from Backend.Analysis.alignment import similarity,register,displayed_transform

def wcs_alignment(source,target):
    """Use existing celestial WCS; reject fields that need a nonlinear warp."""
    if not all(m.get('headers',{}).get('CTYPE1') and m.get('headers',{}).get('CTYPE2') for m in (source,target)):return None
    import numpy as np
    from astropy.wcs import WCS
    from astropy.io import fits
    a=WCS(fits.Header(source.get('headers',{}))).celestial;b=WCS(fits.Header(target.get('headers',{}))).celestial
    if not a.has_celestial or not b.has_celestial:return None
    w,h=source.get('width',1),source.get('height',1)
    positions=np.array([[x,y] for y in (0,h/2,h-1) for x in (0,w/2,w-1)],float)
    mapped=b.all_world2pix(a.all_pix2world(positions,0),0)
    if not np.all(np.isfinite(mapped)):return None
    matrix=similarity(positions,mapped);fitted=positions@matrix[:2,:2].T+matrix[:2,2]
    rms=float(np.sqrt(np.mean(np.sum((fitted-mapped)**2,axis=1))))
    return (matrix,rms,0) if rms<=.5 else None

def align_safe(argument):
    ident,source,target=argument
    try:
        aligned=register(source,target)
    except (ValueError,ArithmeticError):aligned=None
    if aligned is not None:return (*aligned,'stars')
    try:
        aligned=wcs_alignment(source,target)
        return (*aligned,'WCS') if aligned is not None else None
    except (ValueError,ArithmeticError):return None

class Quality:
    def __init__(self,catalog,jobs):
        self.catalog=catalog;self.store=catalog.store;self.jobs=jobs;self.results={}
        self.reports=self.store.folder/'Reports';self.history_path=self.reports/'history.json'
        self.history_guard=threading.RLock();self.history_entries=None
    @staticmethod
    def _ident(value):
        ident=str(value or '')
        if not re.fullmatch('[a-f0-9]{32}',ident):raise ValueError('Невалиден анализ.')
        return ident
    def _entries(self):
        with self.history_guard:
            if self.history_entries is None:
                data=read_json(self.history_path,None)
                rows=data.get('entries',[]) if isinstance(data,dict) else []
                self.history_entries=[e for e in rows if isinstance(e,dict) and isinstance(e.get('id'),str) and re.fullmatch('[a-f0-9]{32}',e.get('id',''))]
            return list(self.history_entries)
    def _store_entries(self,entries):
        with self.history_guard:
            self.history_entries=list(entries);atomic_json(self.history_path,dict(version=1,entries=self.history_entries))
    def _entry(self,result,name,auto=False):
        frames=result.get('frames') or []
        def values(key):return sorted({(row.get('meta') or {}).get(key) for row in frames if (row.get('meta') or {}).get(key)})
        return dict(id=result['id'],name=str(name).strip()[:160],auto=bool(auto),created=float(result.get('created') or time.time()),
            kind=result.get('kind','frames'),mode='quality',objects=values('object'),nights=values('night'),frames=len(frames),algorithm=result.get('algorithm',''))
    def _standard_review(self, result):
        settings = self.catalog.settings.value.get('preferences', {}).get('customization', {}).get('analysis', {})
        standard_review(result['frames'], settings)
        result['standardAlgorithm'] = STANDARD_VERSION

    def load(self,ident,compact=False):
        ident=self._ident(ident);result=self.results.get(ident)
        if result is None:
            path=self.reports/(ident+'.json')
            if not path.is_file():raise ValueError('Анализът не е намерен. Възможно е записът да е изтрит.')
            result=read_json(path,None)
            if not isinstance(result,dict) or not isinstance(result.get('frames'),list):raise ValueError('Файлът не съдържа анализ на качеството.')
            with self.store.db() as db:decisions={row['file']:row['decision'] for row in db.execute('SELECT file,decision FROM decisions')}
            for index,row in enumerate(result['frames'],1):
                row.setdefault('frameIndex',index)
                row['decision']=decisions.get(row.get('id'),row.get('decision') or 'approved')
            if result.get('standardAlgorithm') != STANDARD_VERSION:
                self._standard_review(result)
            self.results[ident]=result
        if compact:return dict(result,frames=[{k:v for k,v in row.items() if k not in ('starList','noiseMap')} for row in result['frames']])
        return result
    def _stackingwizard_path(self, ident):
        return self.reports / (ident + '.stackingwizard.json')

    def display_result(self, ident, compact=False):
        result = self.load(ident, compact)
        imported = read_json(self._stackingwizard_path(result['id']), None)
        return dict(result, stackingWizard=imported) if imported else result

    def import_stackingwizard(self, ident, content):
        from Backend.Analysis.stackingwizard_csv import match_grades
        with self.history_guard:
            report = self.load(ident)
            imported = match_grades(content, report['frames'])
            atomic_json(self._stackingwizard_path(report['id']), imported)
        return imported

    def _remove_report(self, ident):
        with self.history_guard:
            for path in (self.reports / (ident + '.json'), self._stackingwizard_path(ident)):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            self.results.pop(ident, None)

    def history(self):
        entries=self._entries();valid=[e for e in entries if (self.reports/(e['id']+'.json')).is_file()]
        if len(valid)!=len(entries):self._store_entries(valid)
        return dict(entries=sorted(valid,key=lambda e:float(e.get('created') or 0),reverse=True))
    def save(self,ident,name):
        ident=self._ident(ident);name=str(name or '').strip()
        if not name:raise ValueError('Въведи име на записа.')
        entry=self._entry(self.load(ident),name,False)
        entries=[e for e in self._entries() if e.get('id')!=ident]
        entries.append(entry);self._store_entries(entries);return entry
    def autosave(self,result):
        ident=result['id'];frames=result.get('frames') or []
        objects=sorted({(row.get('meta') or {}).get('object') for row in frames if (row.get('meta') or {}).get('object')})
        nights=sorted({(row.get('meta') or {}).get('night') for row in frames if (row.get('meta') or {}).get('night')})
        name='Последен анализ · '+(' · '.join(objects[:2]) or 'Качество')+((' · '+nights[-1]) if nights else '')
        entries=[]
        for entry in self._entries():
            if entry.get('id')==ident:continue
            if entry.get('auto'):
                self._remove_report(entry['id'])
                continue
            entries.append(entry)
        entries.append(self._entry(result,name,True));self._store_entries(entries);return entries[-1]
    def delete(self,ident):
        ident=self._ident(ident);entries=[e for e in self._entries() if e.get('id')!=ident]
        self._store_entries(entries)
        self._remove_report(ident)
        return dict(deleted=True,id=ident)
    def run(self,ids,cancel,progress,force=False,kind='frames',detector=None):
        import numpy as np
        from Backend.Analysis.psf import detection_settings
        detector=detection_settings(detector);detector_key=json.dumps(detector,sort_keys=True,separators=(',',':'))
        records=sorted([self.catalog.record(i) for i in dict.fromkeys(ids)],key=lambda row:(row['meta'].get('observed',''),row['relative'],row['id']))
        needed=[];results={};signatures={}
        for row in records:
            try:
                path=self.catalog.path(row['id']);stat=path.stat()
                if not row.get('shared') and (stat.st_size,stat.st_mtime_ns)!=(row['size'],row['mtime']):
                    self.catalog.put(row['archive'],path,metadata(path));row.update(self.catalog.record(row['id']))
            except (OSError,ValueError) as error:
                results[row['id']]=dict(id=row['id'],error=str(error),warnings=[dict(type='unavailable',severity='high',confidence='confirmed',message=str(error))],starList=[],stars=0,transform=None);continue
            signature=f"{VERSION}:{kind}:{detector_key}:{row['size']}:{row['mtime']}";signatures[row['id']]=signature
            with self.store.db() as db:saved=db.execute('SELECT result FROM measurements WHERE file=? AND signature=?',(row['id'],signature)).fetchone()
            if saved and not force:results[row['id']]=json.loads(saved[0])
            else:needed.append((row['id'],str(path),kind,{k:v for k,v in detector.items() if k!='profile'}))
        maximum=max((r['meta'].get('width',1)*r['meta'].get('height',1)*32 for r in records),default=1)
        workers=max(1,min(self.catalog.settings.value['workers'],self.catalog.settings.value['memoryMb']*1024**2//max(1,maximum)))
        for argument,result in parallel(measure_safe,needed,workers,cancel,processes=True):
            ident=argument[0];results[ident]=result
            with self.store.db() as db:db.execute('INSERT OR REPLACE INTO measurements VALUES(?,?,?)',(ident,signatures[ident],json.dumps(result,ensure_ascii=False,allow_nan=False)))
            progress(len(results),2*len(records),'Измерване на кадри с отделни процеси')
        ordered=[dict(results[r['id']],name=Path(r['relative']).name,meta=r['meta'],sourceSize=r['size'],sourceMtime=r['mtime'],decision=r.get('decision') or 'approved') for r in records]
        for index,row in enumerate(ordered,1):row['frameIndex']=index
        good=[r for r in ordered if not r.get('error')]
        groups={}
        for r in good:groups.setdefault((r['meta']['object'],r['meta']['telescope'],r['meta']['filter'],r['width'],r['height']),[]).append(r)
        aligned_count = len(ordered) - len(good)
        for group in groups.values():
            maximum_stars=max(r['stars'] for r in group)
            eligible=[r for r in group if r.get('fwhm') and r['stars']>=maximum_stars/2 and len(r['starList'])>=6]
            reference=min(eligible,key=lambda r:(r['fwhm']*(3-2*r['stars']/max(1,maximum_stars)),r['frameIndex'])) if eligible else max(group,key=lambda r:r['stars'])
            try:own_wcs=wcs_alignment(reference['meta'],reference['meta'])
            except (ValueError,ArithmeticError):own_wcs=None
            alignment_by_id={reference['id']:(np.eye(3),0,len(reference['starList']),'stars' if len(reference['starList'])>=10 else 'WCS')} if len(reference['starList'])>=10 or own_wcs else {}
            arguments=[(r['id'],dict(r['meta'],starList=r['starList']),dict(reference['meta'],starList=reference['starList'])) for r in group if r is not reference]
            for (ident,_,_),alignment in parallel(align_safe,arguments,workers,cancel,processes=True):alignment_by_id[ident]=alignment
            for r in group:
                r['weightedFwhm']=r['fwhm']*(3-2*r['stars']/reference['stars']) if r.get('fwhm') and reference['stars'] else None
                r['reference']=reference['id'];r['referenceFrame']=reference['frameIndex']
                if cancel.is_set():raise InterruptedError()
                alignment=alignment_by_id.get(r['id'])
                if alignment:
                    matrix,rms,matched,method=alignment;r.update(transform=matrix.tolist(),registrationRms=rms,matchedStars=matched,alignmentMethod=method,
                        **displayed_transform(matrix,r['width'],r['height']))
                else:r['alignmentNote']='Няма общо координатно решение. Възможен отделен панел на мозайка; качеството се оценява независимо. Не участва в общото покритие.'
            aligned_count += len(group)
            progress(len(records)+aligned_count,2*len(records),'Подравняване и обобщаване на качеството')
        token=uuid.uuid4().hex;result=dict(id=token,kind=kind,frames=ordered,created=time.time(),algorithm=VERSION,detector=detector,
            limitations=['Облаци, следи, препятствия и отражения са евристични предложения, а не сигурна физична класификация.','Независим Gaussian PSF: съпоставяй еднакви входни пиксели, канал, прагове и единици. Отделни фитове и открити звезди могат да се различават.'])
        self._standard_review(result)
        self.results[token]=result;atomic_json(self.store.folder/'Reports'/(token+'.json'),result);self.autosave(result)
        return result
    def coverage(self,result_id,cancel,progress,ids=None):
        from Backend.Analysis.coverage_products import CoverageProducts
        if not hasattr(self,'coverage_products'):self.coverage_products=CoverageProducts(self)
        return self.coverage_products.build(result_id,cancel,progress,ids)

    def coverage_image(self,key):
        from Backend.Analysis.coverage_products import CoverageProducts
        return CoverageProducts(self).image(key)

    def attach_coverage(self,result_id,stack_id):
        import shutil
        from Backend.Foundation.runtime import within,digest
        result=self.results[result_id];saved=Path(result.get('coveragePath',''))
        if not saved.is_file() or saved.parent!=self.store.folder/'Coverage':raise ValueError('Първо създай маска на покритието.')
        row=self.catalog.record(stack_id)
        if row['archive']!='stacks' or row['meta']['kind']!='stack':raise ValueError('Избери стак от архива на стакове.')
        stack=self.catalog.path(stack_id);root=self.catalog.root('stacks')
        target=within(root,(stack.parent/(stack.stem+'.'+result_id[:8]+'.coverage.fits')).relative_to(root).as_posix(),existing=False)
        owned=False
        try:
            with target.open('xb') as output,saved.open('rb') as source:owned=True;shutil.copyfileobj(source,output,4*1024*1024)
            if digest(target)!=digest(saved):raise OSError('Маската не съвпада.')
            atomic_json(target.with_suffix('.json'),dict(stackId=stack_id,resultId=result_id,sourceIds=[r['id'] for r in result['frames']],map='frame-count-and-exposure'))
        except BaseException:
            if owned:target.unlink(missing_ok=True)
            raise
        return dict(path=str(target))
