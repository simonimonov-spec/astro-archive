"""Data-only measurement capability with cancellable, bounded progress tickets."""
import copy,json,threading,time,uuid
from . import quality
from .pipeline import atomic


def measure(inspector,p,cancel,progress):
    ids=p.get('ids')
    if not isinstance(ids,list) or not 1<=len(ids)<=1000 or any(not isinstance(i,str) for i in ids):raise ValueError('Избери до 1000 учебни кадъра на едно обучение.')
    wanted=set(ids);groups={};all_ids=[]
    for number,ident in enumerate(ids,1):
        if cancel.is_set():raise InterruptedError('Измерването е спряно безопасно.')
        source=inspector.ctx.inspection('source',dict(id=ident))
        if source['kind']!='frame':continue
        key=(source['archive'],source['telescope'],source['object'],source['night'],source['filter'])
        if key not in groups:
            filters=dict(zip(('archive','telescope','object','night','filter'),key));filters['kind']='frame'
            rows=inspector.ctx.host('python.archive.query',dict(filters=filters))['rows']
            groups[key]=[r['id'] for r in rows if r.get('filter','')==source['filter']]
            if len(groups[key])>1500:raise ValueError('Учебната група е над 1500 кадъра; използвай по-малка наблюдателна група.')
            all_ids.extend(groups[key])
        progress(0,1,f'Контекст · {number}/{len(ids)} оценени кадъра · {len(groups)} групи')
    all_ids=list(dict.fromkeys(all_ids))
    if not all_ids:raise ValueError('Обучението използва архивирани отделни FITS кадри. Другите оценки са запазени.')
    if len(all_ids)>5000:raise ValueError('Контекстът за обучение надвишава 5000 кадъра; намали учебните примери.')
    progress(0,1,f'Измерване на {len(all_ids)} кадъра с референциите; оценени: {len(wanted)}')
    selection=inspector.ctx.inspection('selection',dict(scope='report'))
    result=inspector.pipeline.run(all_ids,selection.get('reportId'),dict(inspector.options,force=False,model=inspector.starter,qualityModel=None),cancel,progress)
    if cancel.is_set():raise InterruptedError('Измерването е спряно безопасно.')
    rows=[dict(id=r['id'],qualityFeatures=r.get('qualityFeatures'),sourceSize=r.get('sourceSize'),sourceMtime=r.get('sourceMtime')) for r in result['rows'] if r['id'] in wanted]
    if p.get('compact') is True:
        for row in rows:
            values=row.pop('qualityFeatures');row['qualityValues']=quality.vector(values).tolist() if values is not None else None
    return dict(featureSchema=2,features=list(quality.FEATURES),rows=rows)


def invoke(inspector,p):
    op=p.get('operation')
    if op in ('measure-status','measure-result','measure-cancel'):
        with inspector.training_lock:
            job=inspector.training_jobs.get(p.get('ticket'))
            if job is None:raise ValueError('Непозната или изтекла задача за измерване.')
            if op=='measure-cancel':job['cancel'].set();return dict(stopping=job['state']=='running')
            if op=='measure-result':
                if job['state']!='completed':raise ValueError(job.get('message') or 'Измерването още не е готово.')
                result=job.pop('result');job['state']='delivered';return result
            return {k:copy.deepcopy(v) for k,v in job.items() if k not in ('result','cancel')}
    if op in ('measure','measure-start'):
        with inspector.training_lock:
            if inspector.busy:raise ValueError('Изчакай текущата AI проверка.')
            inspector.busy=True
        inspector.publish()
        if op=='measure':
            try:return measure(inspector,p,threading.Event(),lambda *args:None)
            finally:inspector.busy=False;inspector.publish()
        ticket=uuid.uuid4().hex;event=threading.Event()
        job=dict(state='running',message='Подготовка на измерването…',fraction=0.,updated=time.time(),cancel=event)
        with inspector.training_lock:inspector.training_jobs={ticket:job}
        def work(cancel,progress):
            class Stop:
                def is_set(_):return event.is_set() or cancel.is_set() or inspector.closed
            last_publish=0.
            def update(done,total,message):
                nonlocal last_publish
                if Stop().is_set():raise InterruptedError('Измерването е спряно безопасно.')
                now=time.monotonic()
                if now-last_publish<.2 and done<total:return
                last_publish=now
                with inspector.training_lock:job.update(message=str(message),fraction=max(0.,min(1.,done/max(1,total))),updated=time.time())
                progress(done,total,message)
            try:
                result=measure(inspector,p,Stop(),update)
                with inspector.training_lock:job.update(state='completed',fraction=1.,result=result,message='Измерванията са готови.',updated=time.time())
                return dict(measuredFrames=len(result['rows']))
            except Exception as error:
                with inspector.training_lock:job.update(state='cancelled' if Stop().is_set() else 'failed',message=str(error) or 'Измерването е прекъснато.',updated=time.time())
                raise
            finally:inspector.busy=False;inspector.publish()
        try:
            task=inspector.ctx.task('AI обучение · измерване на кадрите',work,section='analysis',tier='ambient')
            return dict(ticket=ticket,**task)
        except Exception:
            with inspector.training_lock:inspector.training_jobs.pop(ticket,None);inspector.busy=False
            inspector.publish();raise
    if inspector.busy:raise ValueError('Изчакай текущата AI проверка.')
    if op=='activate':
        folder=inspector.folder/'Training';folder.mkdir(parents=True,exist_ok=True)
        path=folder/('candidate-'+uuid.uuid4().hex+'.json')
        try:
            atomic(path,p.get('model'));value=quality.load(path);value.pop('digest',None)
            active=folder/'active-quality.json'
            if active.is_file():atomic(folder/'Previous'/('model-'+uuid.uuid4().hex+'.json'),json.loads(active.read_text(encoding='utf-8')))
            atomic(active,value)
        finally:path.unlink(missing_ok=True)
        return dict(activated=True,version=value.get('version'))
    raise ValueError('Непозната операция за обучение.')

