"""inspector.quality.v1 — the same AI index as the visual check, for other plugins.

Runs the existing pipeline and AI scoring on given frame IDs. No chart, no annotations,
and the visible inspection result is not replaced. One job at a time, ticket based."""
import threading,time,uuid

MAX_IDS=5000

def invoke(inspector,p):
    op=p.get('operation')
    jobs=inspector.__dict__.setdefault('quality_jobs',{})
    if op=='available':return dict(ok=True)
    if op in ('score-status','score-result','score-cancel'):
        with inspector.training_lock:
            job=jobs.get(p.get('ticket'))
            if job is None:raise ValueError('Непозната или изтекла AI задача.')
            if op=='score-cancel':job['cancel'].set();return dict(stopping=job['state']=='running')
            if op=='score-result':
                if job['state']!='completed':raise ValueError(job.get('message') or 'AI индексът още не е готов.')
                result=job.pop('result');job['state']='delivered';return result
            return dict(state=job['state'],fraction=job['fraction'],message=job['message'])
    if op!='score-start':raise ValueError('Непозната операция за AI качество.')
    ids=p.get('ids')
    if not isinstance(ids,list) or not 1<=len(ids)<=MAX_IDS or any(not isinstance(i,str) or not i or len(i)>80 for i in ids):raise ValueError(f'Подай от 1 до {MAX_IDS} ID на кадри.')
    ids=list(dict.fromkeys(ids))
    with inspector.training_lock:
        if inspector.busy:raise ValueError('AI оценителят е зает с друга проверка.')
        inspector.busy=True
        ticket=uuid.uuid4().hex;event=threading.Event()
        job=dict(state='running',message='AI оценка на кадрите…',fraction=0.,updated=time.time(),cancel=event)
        jobs.clear();jobs[ticket]=job
    inspector.publish()
    def work(cancel,progress):
        class Stop:
            def is_set(_):return event.is_set() or cancel.is_set() or inspector.closed
        def update(done,total,message):
            if Stop().is_set():raise InterruptedError('AI оценката е спряна.')
            with inspector.training_lock:job.update(message=str(message),fraction=max(0.,min(1.,done/max(1,total))),updated=time.time())
            progress(done,total,message)
        try:
            options=dict(inspector.options,force=False,model=inspector.starter,qualityModel=None)
            result=inspector.pipeline.run(ids,None,options,Stop(),lambda d,t,m:update(.75*d/max(1,t),1,m))
            inspector.ai.attach(result,cancel=Stop(),progress=lambda d,t:update(.75+.25*d/max(1,t),1,f'AI индекс · {d}/{t}'))
            wanted=set(ids)
            scores={r['id']:(float(r['aiQuality']) if r.get('aiQuality') is not None else None) for r in result['rows'] if r['id'] in wanted}
            with inspector.training_lock:job.update(state='completed',fraction=1.,result=dict(scores=scores),message='AI индексът е готов.',updated=time.time())
            return dict(scoredFrames=sum(v is not None for v in scores.values()))
        except Exception as error:
            with inspector.training_lock:job.update(state='cancelled' if Stop().is_set() else 'failed',message=str(error) or 'AI оценката е прекъсната.',updated=time.time())
            raise
        finally:
            inspector.busy=False;inspector.publish()
    try:
        task=inspector.ctx.task('AI качество · за автоматично стакване',work,section='preparation',tier='ambient')
        return dict(ticket=ticket,**task)
    except Exception:
        with inspector.training_lock:jobs.pop(ticket,None);inspector.busy=False
        inspector.publish();raise
