"""Declarative AI inspection plugin. No imports from application internals."""
from __future__ import annotations
import csv,io,json,math,subprocess,sys,threading,time,uuid
from pathlib import Path
from . import engine,model,quality
from .pipeline import Pipeline,atomic,status_for

def field(kind,ident,**kwargs):return dict(type=kind,id=ident,**kwargs)
def option(value,label):return dict(value=value,label=label)
def annotation(row):
    warnings=sorted(row.get('warnings',[]),key=lambda w:w.get('severity')!='high')[:8]
    return dict(id=row['id'],warnings=warnings,quality=row.get('aiQuality'),comparison=dict(coverage=row.get('comparisonCoverage'),references=int(row.get('referenceFrames',0)),matchedStars=int(row.get('matchedStars',0)),reason=str(row.get('qualityReason') or row.get('uncertainty') or row.get('error') or '')[:400]))
LABELS=dict(cloud='Облачност',satellite='Следа',obstruction='Препятствие')

class Inspector:
    def __init__(self,ctx):
        self.ctx=ctx;self.folder=Path(ctx.storage());self.options={**dict(threshold=.72,qualityCutoff=35,edge=768,workers=2,archive='frames',telescope='',object='',night='',view='all'),**ctx.settings()}
        self.ctx.save_settings(self.options);self.ids=[];self.source_report=None;self.result=None;self.selected=None;self.busy=False;self.problem='';self.image=None;self.closed=False;self.generation=0
        self.starter=dict(version='heuristic-v1',digest='heuristic-v1',provenance='transparent analyzer rules');self.pipeline=Pipeline(ctx,self.folder)
        self.training_lock=threading.RLock();self.training_jobs={}
        from .ai_host import AIHost
        self.ai=AIHost(self)
        self.trainer=Path(__file__).resolve().parent/'Trainer'/'trainer.pyw'
        self.archives=ctx.inspection('archives')['archives'];self.facets={k:[] for k in ('telescope','object','night')}
        for name,callback in [('refresh',self.refresh),('save',self.save),('adopt',self.adopt),('choose',self.choose),('run',self.run),('chart_run',self.chart_run),('chart_cutoff',self.chart_cutoff),('row',self.row),('diagnostic',self.diagnostic),('original',self.original),('apply',self.apply),('label',self.label),('export',self.export),('training',self.training),('training_preview',self.training_preview),('training_api',self.training_api)]:ctx.command(name,callback)
        ctx.command('ai_api',self.ai.service.call)
        self.publish()

    def values(self,p):return p.get('values',p)
    def save(self,p):
        v=self.values(p);o=dict(self.options)
        for key in ('threshold','qualityCutoff','edge','workers','archive','telescope','object','night','view'):
            if key in v:o[key]=v[key]
        if type(o['threshold']) not in (int,float) or not .55<=o['threshold']<=.98:raise ValueError('Прагът е между 0.55 и 0.98.')
        if type(o['qualityCutoff']) not in (int,float) or not math.isfinite(o['qualityCutoff']) or not 0<=o['qualityCutoff']<=100:raise ValueError('Прагът за AI качество е от 0 до 100%.')
        if type(o['edge']) is not int or not 256<=o['edge']<=1536 or type(o['workers']) is not int or not 1<=o['workers']<=4:raise ValueError('Невалиден работен размер или брой процеси.')
        if o['view'] not in ('all','suspect','uncertain'):raise ValueError('Невалиден изглед.')
        for k in ('archive','telescope','object','night'):
            if not isinstance(o[k],str) or len(o[k])>256:raise ValueError('Невалиден избор на кадри.')
        self.options=o;self.ctx.save_settings(o)
        if 'view' in v:self.publish()
        return dict(saved=True)

    def refresh(self,p):
        self.save(p);self.archives=self.ctx.inspection('archives')['archives'];self.facets=self.ctx.host('python.archive.facets',dict(filters=dict(archive=self.options['archive'])));self.publish();return dict(message='Опциите са обновени от индекса; не са четени пиксели.')

    def adopt(self,p):
        if self.busy:raise ValueError('Изчакай текущата инспекция.')
        selection=self.ctx.inspection('selection')
        if selection.get('kind')!='frames':raise ValueError('Избери отделни астро кадри, а не стакове.')
        if not selection['ids']:raise ValueError('Първо избери обект и вечери в основния Анализ или използвай полетата тук.')
        self.ids=selection['ids'];self.source_report=selection.get('reportId');self.result=None;self.selected=None;self.image=None;self.problem='';self.publish();return dict(message=f'Взети са {len(self.ids)} кадъра от текущия анализ.')

    def choose(self,p):
        self.save(p)
        if self.busy:raise ValueError('Изчакай текущата инспекция.')
        if not all(self.options[k] for k in ('telescope','object','night')):raise ValueError('Избери телескоп, обект и вечер, или вземи подбора от основния Анализ.')
        filters={k:self.options[k] for k in ('archive','telescope','object','night')};filters['kind']='frame'
        data=self.ctx.host('python.archive.query',dict(filters=filters));self.ids=[r['id'] for r in data['rows']]
        if not self.ids:raise ValueError('Няма кадри за този избор.')
        self.source_report=None;self.result=None;self.image=None;self.selected=None;self.publish();return dict(message=f'Избрани са {len(self.ids)} кадъра.')

    def chart_run(self,p):
        if self.busy:raise ValueError('AI инспекцията вече работи.')
        selection=self.ctx.inspection('selection',dict(scope='report'));context=p.get('context',{})
        if not selection.get('reportId') or context.get('stage')!='result':raise ValueError('Първо отвори готовата диаграма от анализа.')
        if selection.get('kind')!='frames':raise ValueError('AI проверката е за отделни кадри, а не за стакове.')
        ids=context.get('ids',[]);allowed=set(selection['ids'])
        if not isinstance(ids,list) or not ids or len(ids)>5000 or any(i not in allowed for i in ids):raise ValueError('Избери показан обект и вечер от текущия анализ, до 5000 кадъра.')
        self.ids=list(dict.fromkeys(ids));self.source_report=selection['reportId'];self.result=None;self.image=None;self.selected=None
        return self.run({},chart=True)

    def chart_cutoff(self,p):
        if self.busy:raise ValueError('Изчакай текущата AI проверка.')
        self.save(p)
        if self.result:
            current=self.ctx.inspection('selection',dict(scope='report'))
            if current.get('reportId')!=self.result['reportId']:
                self.publish();return dict(saved=True,message='Прагът е запазен. Изпълни AI проверка за текущия анализ.')
            for row in self.result['rows']:
                if row.get('error'):continue
                row['warnings']=[w for w in row.get('warnings',[]) if not w.get('message','').startswith(('Индексът е под','Напълно проблемен според AI'))]
                value=row.get('aiQuality');row['qualityProblem']=value is not None and value<=self.options['qualityCutoff']
                if row['qualityProblem']:row['warnings'].append(dict(type='quality',origin='ai',score=round(1-value/100,4),severity='high',message=f'Напълно проблемен според AI: индекс {value:.1f}% е под зададения праг. Провери ръчно.'))
                row['status']=status_for(row)
            annotations=[annotation(r) for r in self.result['rows']]
            self.ctx.inspection('annotate',dict(reportId=self.result['reportId'],annotations=annotations,highlight=True))
        self.publish();return dict(saved=True)

    def run(self,p,chart=False):
        self.save(p)
        if self.busy:raise ValueError('AI инспекцията вече работи.')
        if not self.ids:raise ValueError('Избери кадри за инспекция.')
        self.busy=True;self.problem='';self.image=None;self.generation+=1;generation=self.generation
        user_model=None
        force=bool(self.values(p).get('force',False));options=dict(self.options,force=force,model=self.starter,qualityModel=user_model);ids=list(self.ids);report_id=self.source_report;self.publish()
        def work(cancel,progress):
            try:
                result=self.pipeline.run(ids,report_id,options,cancel,lambda done,total,label:progress(.75*done/max(1,total),1,label))
                self.ai.attach(result,cancel=cancel,progress=lambda done,total:progress(.75+.25*done/max(1,total),1,f'ResNet / евристична оценка · {done}/{total}'))
                if generation==self.generation and not self.closed:
                    self.result=result;self.source_report=result['reportId'];self.selected=None
                    if chart and not cancel.is_set():self.ctx.inspection('annotate',dict(reportId=result['reportId'],annotations=[annotation(r) for r in result['rows']],highlight=True))
                return dict(frames=len(result['rows']),suspect=sum(bool(r.get('warnings')) for r in result['rows']),cached=result['cached'])
            except Exception as error:self.problem=str(error);raise
            finally:
                self.busy=False
                if not self.closed:self.publish()
        try:return self.ctx.task('AI инспекция · облаци, следи и препятствия',work,'analysis')
        except BaseException:self.busy=False;self.publish();raise

    def visible(self):
        rows=self.result['rows'] if self.result else []
        if self.options['view']=='suspect':rows=[r for r in rows if r.get('warnings')]
        if self.options['view']=='uncertain':rows=[r for r in rows if not r.get('reliable')]
        return rows

    def row(self,p):
        rows=self.visible();i=p.get('row',-1)
        if type(i) is not int or not 0<=i<len(rows):raise ValueError('Избери кадър от таблицата.')
        self.selected=rows[i]['id'];self.image=None;self.publish();return dict(selected=self.selected)

    def current(self):
        if not self.result or not self.selected:raise ValueError('Избери кадър от таблицата.')
        return next(r for r in self.result['rows'] if r['id']==self.selected)

    def diagnostic(self,p):
        if self.busy:raise ValueError('Изчакай текущата инспекция.')
        row=self.current()
        if row.get('error'):raise ValueError(row['error'])
        if not row.get('matrix'):raise ValueError('Няма локално диагностично измерване. Отвори оригинала за ръчна проверка.')
        self.image=self.pipeline.preview(row);self.publish();return dict(message='Подравнен диагностичен преглед: жълто — линейни следи, червено — тъмна област. Очертанията са кандидати, не доказана класификация.')

    def original(self,p):return self.ctx.inspection('show',dict(id=self.current()['id']))

    def apply(self,p):
        if not self.result:raise ValueError('Първо изпълни инспекцията.')
        annotations=[annotation(r) for r in self.result['rows']]
        result=self.ctx.inspection('annotate',dict(reportId=self.result['reportId'],annotations=annotations,present=True))
        return dict(**result,message='AI предупрежденията са добавени към диаграмата. Подборът и оригиналите не са променени.')

    def label(self,p):
        row=self.current();value=self.values(p).get('human-label','clear')
        if value not in ('clear','cloud','satellite','obstruction','uncertain'):raise ValueError('Невалидна ръчна оценка.')
        from .trainer_bridge import append_label
        labels=dict(quality=None,cloud='clear' if value=='clear' else 'cloud' if value=='cloud' else None,trail='satellite' if value=='satellite' else None,light=None,elongation=None,defects='present' if value=='obstruction' else None,artifacts=None,objects=[],obstruction='present' if value=='obstruction' else None)
        if value=='uncertain':
            self.ai.service.audit.append('label.uncertain',actor='chart-label',details=dict(frameId=row['id']))
            return dict(saved=True,message='Несигурността е отбелязана; не се използва като сигурен учебен етикет.')
        return append_label(self,row['id'],labels,actor='chart-label',notes='Ръчна проверка: '+value)

    def export(self,p):
        if not self.result:raise ValueError('Няма готов резултат.')
        folder=self.folder/'Exports';folder.mkdir(exist_ok=True);path=folder/('inspection-'+self.result['id'][:12]+'.csv')
        with path.open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.writer(f);writer.writerow(['frame','name','night','status','quality_index_percent','cloud_score','satellite_score','obstruction_score','reference_frames','matched_stars','comparison_coverage_percent','comparison_note'])
            for r in self.result['rows']:writer.writerow([r.get('frameIndex'),r['name'],r.get('night'),r['status'],r.get('aiQuality'),*[r.get('scores',{}).get(c) for c in model.CLASSES],r.get('referenceFrames'),r.get('matchedStars'),r.get('comparisonCoverage'),r.get('qualityReason','')])
        return dict(message='CSV отчетът е записан: '+str(path),copyText=str(path))

    def training(self,p):
        from .trainer_bridge import open_trainer
        return open_trainer(self)

    def legacy_training_export(self,p):
        if self.busy or not self.result:raise ValueError('Първо завърши AI проверката на кадрите.')
        if not self.trainer.is_file() or self.trainer.is_symlink():raise ValueError('Отделната програма за обучение липсва в програмната папка.')
        rows=[];usable=0
        for r in self.result['rows']:
            if r.get('qualityFeatures'):quality.vector(r['qualityFeatures']);usable+=1
            rows.append(dict(id=r['id'],name=r['name'],night=r.get('night',''),object=r.get('object',''),sourceSize=r.get('sourceSize'),sourceMtime=r.get('sourceMtime'),qualityFeatures=r.get('qualityFeatures'),aiQuality=r.get('aiQuality'),group=r.get('telescope','')+'|'+r.get('object','')+'|'+r.get('night','')))
        if not usable:raise ValueError('Няма надеждно измерени кадри за обучение. Избери повече кадри с общо поле и ги анализирай отново.')
        folder=self.folder/'Training'/'Inbox';folder.mkdir(parents=True,exist_ok=True);path=folder/('batch-'+uuid.uuid4().hex+'.json')
        atomic(path,dict(format='astroarchive-ai-training-batch-v1',created=time.time(),features=list(quality.FEATURES),rows=rows))
        subprocess.Popen([sys.executable,str(self.trainer)],cwd=str(self.trainer.parent),close_fds=True)
        return dict(message=f'Подадени са измерванията на {len(rows)} кадъра. В програмата избери етикет за всеки. Оригиналите не се копират.',copyText=str(path))

    def training_api(self,p):
        from .trainer_bridge import invoke
        return invoke(self,p)

    def training_preview(self,p):
        """On-demand local preview, restricted to the current inspection result."""
        if self.busy or not self.result:raise ValueError('Отвори AI инспекцията с текущия отчет.')
        ident=p.get('id')
        row=next((r for r in self.result['rows'] if r['id']==ident),None)
        if not row or row.get('error') or not row.get('shape'):raise ValueError('Прегледът е достъпен само за измерен кадър от текущия AI отчет.')
        return dict(image=self.pipeline.preview(row))

    def publish(self):
        if self.closed:return
        o=self.options;rows=self.visible();current=next((r for r in (self.result or {}).get('rows',[]) if r['id']==self.selected),None)
        table=[]
        for r in rows[:5000]:
            table.append(dict(number=r.get('frameIndex'),name=r['name'],night=r.get('night',''),state=r['status'],quality=r.get('aiQuality'),cloud=round(r['scores']['cloud']*100,1) if r.get('scores',{}).get('cloud') is not None else None,satellite=round(r['scores']['satellite']*100,1) if r.get('scores',{}).get('satellite') is not None else None,obstruction=round(r['scores']['obstruction']*100,1) if r.get('scores',{}).get('obstruction') is not None else None,reason=' · '.join(w['message'] for w in r.get('warnings',[])) or r.get('qualityReason',''),coverage=r.get('comparisonCoverage'),references=r.get('referenceFrames',0),matched=r.get('matchedStars',0)))
        doc=[field('text','intro',span=2,value='Сравнение с научен фон от подравнени кадри: шумът се нормализира, постоянните мъглявини и Млечният път се отделят от временните промени. Решението остава твое.'),
             field('text','starter',span=2,tone='warning',value='AI оценител · '+self.ai.engine_status()+'. Оценките изискват ръчна проверка. Подборът и оригиналите се запазват.'),
             field('button','adopt',label='Вземи избраните кадри от Анализ',command='adopt',tone='primary',disabled=self.busy),
             field('metric','count',label='Избрани кадри',value=len(self.ids)),
             field('group','picker',label='Или избери една наблюдателна нощ',span=2,children=[
                field('select','archive',label='Архив на кадри',value=o['archive'] if any(a['id']==o['archive'] for a in self.archives) else (self.archives[0]['id'] if self.archives else ''),options=[option(a['id'],a['name']) for a in self.archives] or [option('','Няма архив')]),
                field('button','refresh',label='Обнови опциите от индекса',command='refresh',disabled=self.busy),
                *[field('select',key,label=title,value=o[key] if o[key] in self.facets[key] else '',options=[option('','Избери…')]+[option(v,v) for v in self.facets[key]]) for key,title in [('telescope','Телескоп'),('object','Обект'),('night','Наблюдателна нощ')]],
                field('button','choose',label='Избери тези кадри',command='choose',disabled=self.busy)]),
             field('checkbox','force',label='Преизчисли запазените измервания',value=False,disabled=self.busy),field('button','run',label='Изпълни AI инспекцията',command='run',tone='primary',disabled=self.busy or not self.ids),
             field('text','state',span=2,value='Обработват се данни — напредъкът е в индикатора горе вляво.' if self.busy else f"Готови: {len((self.result or {}).get('rows',[]))} кадъра" if self.result else 'За надеждно сравнение избери поне 5 кадъра от едно поле. При слабо подравняване резултатът ще е несигурен.'),
             field('select','view',label='Покажи',value=o['view'],options=[option('all','Всички'),option('suspect','Подозрителни — за ръчна проверка'),option('uncertain','Несигурни / недостатъчни данни')],command='save'),
             field('table','results',label='Резултати · избери ред за инспекция',span=2,command='row',columns=[dict(key=k,label=t,format='number' if k in ('number','quality','cloud','satellite','obstruction','references','coverage','matched') else 'text') for k,t in [('number','№'),('name','Кадър'),('night','Вечер'),('state','Състояние'),('quality','AI индекс %'),('coverage','Сравнена област %'),('matched','Общи звезди (мин.)'),('cloud','Облак · оценка'),('satellite','Следа · оценка'),('obstruction','Препятствие · оценка'),('reason','Обяснение'),('references','Референции')]],rows=table),
             field('button','original',label='Отвори оригинала за ръчна инспекция',command='original',disabled=not current or self.busy),field('button','diagnostic',label='Покажи очертанията на подозрителните области',command='diagnostic',disabled=(self.busy or bool(current.get('error')) or not current.get('matrix')) if current else True),
             field('button','apply',label='Покажи AI предупрежденията върху диаграмата',command='apply',tone='primary',disabled=not self.result or self.busy),field('button','export',label='Запази числата като CSV',command='export',disabled=not self.result or self.busy)]
        if self.trainer.is_file():doc.append(field('button','training',label='Отвори AI Trainer',command='training',tone='primary',disabled=self.busy))
        if current and current.get('aiResult'):
            outcome=current['aiResult']
            doc.append(field('text','engine',span=2,value='ResNet-18 · пикселни признаци + измервания; приблизителни AI оценки' if outcome['engine']['kind']=='neural' else 'Евристична оценка'))
            doc.append(field('table','factors',label='Фактори · риск 0–1 (по-високо = по-лошо)',span=2,columns=[dict(key=k,label=t) for k,t in [('id','Фактор'),('status','Измерено / оценено'),('risk','Риск'),('confidence','Доверие'),('contribution','Принос към quality')]],rows=[{k:f[k] for k in ('id','status','risk','confidence','contribution')} for f in outcome['factors']]))
            doc.append(field('text','limitations',span=2,value=' · '.join(outcome['limitations'])))
            doc.append(field('table','measurements',label='Числови измервания и производни оценки',span=2,columns=[dict(key=k,label=t) for k,t in [('factor','Фактор'),('name','Показател'),('value','Стойност'),('unit','Единица'),('measured','Измерен'),('method','Метод')]],rows=[dict(factor=f['id'],**{k:m.get(k) for k in ('name','value','unit','measured','method')}) for f in outcome['factors'] for m in f['metrics']]))
            doc.append(field('table','evidence',label='Локализация · области кандидати',span=2,columns=[dict(key=k,label=t) for k,t in [('factor','Фактор'),('bbox','x, y, ширина, височина'),('space','Координати'),('note','Метод / ограничения')]],rows=[dict(factor=f['id'],bbox=str(e.get('bbox')),space=e.get('coordinateSpace','analysis_sample'),note=e.get('note','')) for f in outcome['factors'] for e in f['evidence'] if e.get('bbox')]))
        if self.problem:doc.insert(2,field('text','problem',value=self.problem,tone='error',span=2))
        if current:
            doc.append(field('group','details',label=current['name'],span=2,children=[
                field('text','frame-status',span=2,value=current['status']+((' · '+current['error']) if current.get('error') else '')),
                field('metric','matched-stars',label='Общи звезди · минимум на референция',value=current.get('matchedStars',0)),
                field('metric','comparison-coverage',label='Сравнена област от този кадър',value=current.get('comparisonCoverage',0),unit='%'),
                field('metric','reference-count',label='Референтни кадри',value=current.get('referenceFrames',0)),
                field('metric','transparency',label='Относителна звездна прозрачност',value=round(current.get('transparency',0)*100,1),unit='%',help='Спрямо по-прозрачните кадри от същото поле и вечер; не е абсолютна оценка на небето.'),
                field('text','frame-findings',span=2,value=' · '.join(w['message'] for w in current.get('warnings',[])) or current.get('qualityReason') or current.get('uncertainty') or 'Не е намерен убедителен временен дефект. Това не е гаранция, че кадърът е годен за стакване.')]))
            doc.extend([field('select','human-label',label='Моята проверка на този кадър',value='uncertain',options=[option('clear','Няма такъв проблем'),option('cloud','Има облачност'),option('satellite','Има следа'),option('obstruction','Има препятствие'),option('uncertain','Не мога да определя')]),field('button','label',label='Запази моята оценка за бъдещо обучение',command='label')])
        if self.image:doc.append(field('image','diagnostic-image',label='Подравнен диагностичен преглед · зарежда се само при поискване',value=self.image,span=2))
        self.ctx.publish('inspect',doc)
        chart_state='Проверяват се показаните кадри. Напредъкът е горе вляво.' if self.busy else f"Проверени: {len(self.result['rows'])} · с AI индекс: {sum(r.get('aiQuality') is not None for r in self.result['rows'])} · под прага: {sum(bool(r.get('qualityProblem')) for r in self.result['rows'])} · облаци: {sum(any(w['type']=='cloud' for w in r.get('warnings',[])) for r in self.result['rows'])} · светлинни смущения/градиент: {sum(any(w['type']=='light' for w in r.get('warnings',[])) for r in self.result['rows'])} · без надежден индекс: {sum(r.get('aiQuality') is None for r in self.result['rows'])}. Прегледай преди изключване." if self.result else 'Проверява показания обект и вечер без предварително маркиране. Шумът и постоянният небесен фон се сравняват с референции.'
        self.ctx.publish('chart',[field('number','qualityCutoff',label='AI качество до [%] · предложи за изключване',value=o['qualityCutoff'],min=0,max=100,step=1,command='chart_cutoff',disabled=self.busy),field('button','chart-run',label='AI проверка на кадрите',command='chart_run',tone='primary',disabled=self.busy),field('text','chart-state',value=self.problem or chart_state,tone='error' if self.problem else 'normal'),field('text','chart-limit',value='AI индексът е качество, а сравнена област [%] показва колко от конкретния кадър има поне 3 референции. Това не е увереност на модела. Различни части на мозайка не се наказват за липса на съвпадение. Без достатъчно данни няма процент или автоматично изключване.',tone='warning')])
        self.ctx.publish('options',[field('text','model-note',span=2,value='Локален AI индекс: ориентировъчен, не калибрирана вероятност. Кадрите без надеждно измерване нямат процент. Обучен модел се прилага от следващата проверка.'),field('number','qualityCutoff',label='Напълно проблемен при AI индекс до [%]',value=o['qualityCutoff'],min=0,max=100,step=1,command='save'),field('number','threshold',label='Праг за сигнал за облак / следа',value=o['threshold'],min=.55,max=.98,step=.01,command='save'),field('number','edge',label='Максимална работна дълга страна [px]',value=o['edge'],min=256,max=1536,step=128,command='save'),field('number','workers',label='Паралелни работници',value=o['workers'],min=1,max=4,step=1,command='save')])

    def close(self):
        self.closed=True;self.generation+=1
        self.ai.service.close()
        with self.training_lock:
            for job in self.training_jobs.values():job['cancel'].set()

def setup(context):
    if not callable(getattr(context,'inspection',None)):raise RuntimeError('Обнови Astro Archive до версия с инспекционния Bridge SDK.')
    plugin=Inspector(context);return plugin.close

