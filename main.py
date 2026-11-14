"""Independent processing addition. Communicates with the host via its SDK only."""
from __future__ import annotations
import copy,json,os,threading,time,uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from .engine import Engine,DEFAULTS,MAX_PARALLEL,allocation,settings,plan,included

def setup(context):
    controller=Controller(context);controller.register();controller.publish();return controller.stop

class Controller:
    def __init__(self,context):
        self.ctx=context;self.folder=Path(context.storage());self.lock=threading.RLock();self.publish_lock=threading.RLock();self.runtimes={};self.busy=False;self.batch_options=None;self.batch_allocations=[];self.selection_generation=0;self.batch_selection=0;self.auto_selected=False;self.last_publish=0;self.closed=False;self.local_cancel=threading.Event()
        initial=dict(DEFAULTS,**context.settings());self.options=settings(initial)
        if not self.options['executable']:
            candidate=Path(os.environ.get('ProgramFiles',r'C:\Program Files'))/'Siril/bin/siril-cli.exe'
            if candidate.is_file():self.options['executable']=str(candidate)
        self.state=dict(groups=[],selected='',status='red',message='Siril е изключен',log=[],graph=[],combine=False)
        saved=self.folder/'session.json'
        if saved.is_file():
            try:self.state.update(json.loads(saved.read_text(encoding='utf-8')))
            except (ValueError,OSError):pass
        for row in self.state['groups']:
            if row['state']=='running':row['state']='paused';row['problem']='Предишната сесия е прекъсната. Работните копия са запазени.'
        self.state.update(status='red',message='Siril е изключен');self.image=None
    def save(self,force=False):
        with self.lock:
            if self.closed and not force:return
            target=self.folder/'session.json';temp=target.with_suffix('.tmp');temp.write_text(json.dumps(self.state,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(temp,target)
    def selected(self):return next((g for g in self.state['groups'] if g['id']==self.state['selected']),None)
    def register(self):
        for name,action in [('resume',self.resume),('prepare',self.prepare),('configure',self.configure),('start',self.start),('select',self.select),('accept',self.accept),('retry',self.retry),('skip',self.skip),('cleanup',self.cleanup_session),('merge',self.merge),('restart',self.restart),('stop',self.cancel),('preview',self.preview),('graph',self.graph),('navigate',self.navigate),('status',self.snapshot_status),('choose-executable',lambda p:self.choose('executable')),('choose-dark',lambda p:self.choose('dark')),('choose-flat',lambda p:self.choose('flat')),('choose-bias',lambda p:self.choose('bias'))]:self.ctx.command(name,action)
    def log(self,line):
        with self.lock:self.state['log'].append(str(line)[:16000]);self.state['log']=self.state['log'][-300:]
        self.publish(throttle=True)
    def status(self,color,message):
        with self.lock:self.state.update(status=color,message=message)
        self.publish()
    def prepare(self,p):
        ids=p.get('context',{}).get('ids',[])
        if self.busy:return dict(message='Текущата обработка продължава.')
        if not ids:self.publish();return dict(message='Избери кадри от началния екран на Подготовка.')
        previous={i for g in self.state['groups'] if not g.get('combined') for i in g['ids']}
        if set(ids)==previous:self.publish();return dict(accepted=True)
        active=[g for g in self.state['groups'] if g['state'] not in ('approved','skipped')]
        if active and previous:return dict(message='Завърши или пропусни текущите дни, преди да замениш подбора.')
        query=self.ctx.processing('describe',dict(ids=ids));rows={r['id']:r for r in query['rows']}
        missing=set(ids)-set(rows)
        if missing:raise ValueError('Подборът е променен. Обнови архива и избери кадрите отново.')
        groups={}
        for ident in ids:
            r=rows[ident]
            if r.get('decision')=='excluded':continue
            if r['kind'] not in ('frame','stack'):raise ValueError('Избери FITS кадри или приети стакове.')
            key=(r['telescope'],r['object'],r['night'],r.get('filter',''),r.get('width'),r.get('height'),r['kind'])
            groups.setdefault(key,[]).append(ident)
        self.state['groups']=[dict(id=uuid.uuid4().hex,ids=values,telescope=k[0],object=k[1],night=k[2],filter=k[3],kind=k[6],state='pending',settings={},attempt=0) for k,values in sorted(groups.items(),key=lambda kv:str(kv[0]))]
        if not self.state['groups']:raise ValueError('Няма одобрени кадри.')
        self.state['selected']=self.state['groups'][0]['id'];self.image=None;self.state['graph']=[];self.save();self.publish();return dict(message='Избрани са '+str(len(self.state['groups']))+' групи. Настрой и потвърди стартирането.')
    def resume(self,p):
        self.select(p)
        return dict(openContribution='automatic-stacking')

    def configure(self,p):
        values=p.get('values',{});row=self.selected();override=bool(values.get('override',False));current=dict(self.options,**(row.get('settings',{}) if row and override else {}))
        next_=settings(dict(current,**{k:v for k,v in values.items() if k in DEFAULTS}))
        if self.busy:raise ValueError('Изчакай приключването на партидата преди промяна на настройките.')
        if override and row:row['settings']={k:v for k,v in next_.items() if k not in ('override','combine','executable','parallel')}
        else:self.options=next_;self.ctx.save_settings(self.options)
        self.options['override']=override;self.options['parallel']=next_['parallel'];self.options['combine']=next_['combine'];self.options['executable']=next_['executable'];self.ctx.save_settings(self.options);self.state['combine']=next_['combine'];self.save();self.publish();return dict(saved=True)
    def choose(self,key):
        if self.busy:raise ValueError('Изчакай текущото изпълнение.')
        result=self.ctx.processing('choose',dict(kind='executable' if key=='executable' else 'master'))
        if result['path']:
            if self.options.get('override') and self.selected() and key!='executable':self.selected()['settings'][key]=result['path']
            else:self.options[key]=result['path'];self.ctx.save_settings(self.options)
            self.save();self.publish()
        return dict(saved=True)
    def select(self,p):
        ident=p.get('selected',{}).get('id')
        with self.lock:
            if not any(g['id']==ident for g in self.state['groups']):raise ValueError('Непознат ден.')
            self.state['selected']=ident;self.selection_generation+=1;self.image=None;chosen=copy.deepcopy(self.selected())
        if chosen.get('session') and chosen.get('relative'):
            try:
                image=self.ctx.processing('preview',dict(session=chosen['session'],relative=chosen['relative']))['image']
                with self.lock:
                    if self.state['selected']==ident and self.selected().get('session')==chosen['session']:self.image=image
            except (OSError,ValueError):pass
        self.save();self.publish();return dict(selected=True)

    def run_group(self,row,cancel,progress,runtime,budget):
        controller=self
        class Signal:
            def is_set(self):return cancel.is_set() or controller.local_cancel.is_set() or runtime['cancel'].is_set() or controller.closed
        signal=Signal()
        def line(value):self.log('['+row['night']+' · '+row['object']+'] '+str(value))
        while True:
            try:
                if signal.is_set():raise InterruptedError('Задачата е спряна.')
                with self.lock:
                    old_session=row.get('session')
                    row.update(state='running',progress=0,runtimeMessage='Подготвяне на копия',attempt=row.get('attempt',0)+1,problem='')
                    for key in ('relative','usedIds','rejectedIds','sequence'):row.pop(key,None)
                    if self.state['selected']==row['id']:self.image=None
                    o=settings(dict(self.batch_options,**row.get('settings',{})))
                    o['cpu']=min(o['cpu'],budget['cpu']);o['memory']=min(o['memory'],budget['memory'])
                    if row.get('combined'):o.update(debayer=False,dark='',flat='',bias='',background=False,drizzle=False)
                    row['effectiveSettings']=o
                if old_session:
                    self.ctx.processing('discard',dict(session=old_session))
                    with self.lock:
                        for key in ('session','folder'):row.pop(key,None)
                self.save();self.publish()
                stage=self.ctx.processing('stage',dict(ids=row['ids']),signal,lambda d,t,p:progress(.1*d/max(1,t),1,p))
                with self.lock:row.update(session=stage['session'],folder=stage['folder'])
                self.save()
                if signal.is_set():raise InterruptedError('Задачата е спряна.')
                cfa=[bool(source['meta'].get('headers',{}).get('BAYERPAT')) for source in stage['sources']]
                if any(cfa) and not all(cfa):raise ValueError('Групата съдържа едновременно CFA и моно/RGB кадри. Раздели подбора.')
                for key in ('dark','flat','bias'):
                    if o[key]:self.ctx.processing('master',dict(session=row['session'],kind=key,path=o[key]))
                commands,sequence=plan(o,all(cfa),bool(row.get('combined')))
                with self.lock:row['effectiveSettings']=o;row['sequence']=sequence
                self.save();line('Опит '+str(row['attempt'])+' · '+str(o['cpu'])+' CPU нишки · RAM '+str(round(o['memory']*100,1))+'% от свободната')
                runtime['engine'].run(o['executable'],row['folder'],commands,signal,line,lambda color,message:self.worker_status(row,runtime,color,message),lambda d,t,p:progress(.1+.85*d/max(1,t),1,p))
                if signal.is_set():raise InterruptedError('Задачата е спряна.')
                if not (Path(row['folder'])/'result.fit').is_file():raise ValueError('Siril не създаде очаквания result.fit. Входните копия са запазени.')
                numbers=included(row['folder'],sequence,len(stage['sources']))
                used=[source['id'] for source in stage['sources'] if source['number'] in numbers]
                if not used:raise ValueError('Нито един кадър не е включен в резултата.')
                with self.lock:
                    row.update(usedIds=used,rejectedIds=[source['id'] for source in stage['sources'] if source['number'] not in numbers],state='review',relative='result.fit',accepted=None,progress=100)
                    selected=self.selected()
                    if not self.auto_selected and self.selection_generation==self.batch_selection and (not selected or selected['state'] not in ('review','approved')):
                        self.state['selected']=row['id'];self.auto_selected=True
                    preview=self.state['selected']==row['id']
                self.save()
                if preview:
                    image=self.ctx.processing('preview',dict(session=row['session'],relative='result.fit'))['image']
                    with self.lock:
                        if self.state['selected']==row['id']:self.image=image
                progress(1,1,'Готов за оценка');self.publish();return
            except (InterruptedError,RuntimeError):
                with self.lock:
                    restart=runtime['restart'] and not cancel.is_set() and not self.local_cancel.is_set() and not self.closed
                    if restart:runtime['restart']=False;runtime['cancel'].clear();runtime['status']='yellow'
                if restart:line('Повторно стартиране само на избрания ден.');continue
                raise

    def launch(self,rows):
        with self.lock:
            if self.busy:raise ValueError('Siril вече работи.')
            if not rows:raise ValueError('Няма чакащи групи.')
            self.local_cancel.clear();self.busy=True;self.batch_options=copy.deepcopy(self.options)
            self.batch_allocations=allocation(self.batch_options,len(rows));self.batch_selection=self.selection_generation;self.auto_selected=False
            self.state['batchErrors']=0;self.state['batchTotal']=len(rows);self.state['batchCompleted']=0;self.refresh_status()
        self.publish()
        def work(cancel,progress):
            fractions={row['id']:0. for row in rows};failures=[];queue=iter(rows)
            def report(row,done,total,phase):
                with self.lock:
                    fraction=max(0.,min(1.,done/max(1,total)));fractions[row['id']]=fraction;row['progress']=round(fraction*100)
                    progress(sum(fractions.values()),len(rows),row['night']+' · '+str(phase))
                self.publish(throttle=True)
            def execute(row,slot):
                runtime=dict(engine=Engine(),cancel=threading.Event(),restart=False,status='yellow',message='Подготвяне на копия',slot=slot)
                with self.lock:self.runtimes[row['id']]=runtime;self.refresh_status()
                try:
                    self.run_group(row,cancel,lambda d,t,p:report(row,d,t,p),runtime,self.batch_allocations[slot]);return None
                except Exception as error:
                    with self.lock:row.update(state='paused' if isinstance(error,InterruptedError) else 'error',problem=str(error))
                    self.log('['+row['night']+'] '+str(error));return error
                finally:
                    with self.lock:
                        self.runtimes.pop(row['id'],None);fractions[row['id']]=1.;self.state['batchCompleted']+=1
                        self.refresh_status();progress(sum(fractions.values()),len(rows),'Приключили групи: '+str(self.state['batchCompleted'])+'/'+str(len(rows)))
                    self.save();self.publish()
            def stopped():return cancel.is_set() or self.local_cancel.is_set() or self.closed
            try:
                with ThreadPoolExecutor(max_workers=len(self.batch_allocations),thread_name_prefix='Siril-day') as pool:
                    running={}
                    def submit(slot):
                        if stopped():return
                        row=next(queue,None)
                        if row is not None:running[pool.submit(execute,row,slot)]=(row,slot)
                    for slot in range(len(self.batch_allocations)):submit(slot)
                    while running:
                        if stopped():self.request_stop()
                        done,_=wait(running,timeout=.15,return_when=FIRST_COMPLETED)
                        for future in done:
                            row,slot=running.pop(future);error=future.result()
                            if error and not isinstance(error,InterruptedError):failures.append(row['night']+': '+str(error))
                            submit(slot)
                if stopped():
                    cleanup_errors=[]
                    for row in rows:
                        if not row.get('session'):continue
                        try:
                            self.ctx.processing('discard',dict(session=row['session']))
                            for key in ('session','folder','relative'):row.pop(key,None)
                            if row['state']!='approved':row.update(state='paused',problem='Отменено. Работните копия и временните резултати са почистени.')
                        except Exception as cleanup_error:
                            cleanup_errors.append(str(cleanup_error));row['problem']='Отменено, но работната папка не можа да се почисти: '+str(cleanup_error)
                    detail=' Работните папки са почистени.' if not cleanup_errors else ' Някои папки останаха за ръчна проверка.'
                    raise InterruptedError('Партидата е спряна.'+detail)
                if failures:raise RuntimeError(str(len(failures))+' групи завършиха с грешка. Останалите резултати са запазени за оценка. '+failures[0])
                return dict(review=sum(row['state']=='review' for row in rows),parallel=len(self.batch_allocations))
            finally:
                with self.lock:self.busy=False;self.state['batchErrors']=len(failures);self.refresh_status()
                self.save();self.publish()
        try:return self.ctx.task('Siril · автоматично стакване',work,'preparation')
        except BaseException:
            with self.lock:self.busy=False;self.refresh_status()
            self.publish();raise

    def start(self,p):
        if p.get('values'):self.configure(p)
        return self.launch([g for g in self.state['groups'] if g['state'] in ('pending','paused','error') and not g.get('combined')])
    def retry(self,p):
        if p.get('values'):self.configure(p)
        row=self.selected()
        if not row:raise ValueError('Избери ден.')
        return self.launch([row])
    def skip(self,p):
        if self.busy:raise ValueError('Изчакай текущата група.')
        row=self.selected()
        if not row:raise ValueError('Избери ден.')
        if row.get('accepted'):raise ValueError('Този стак вече е прибран. Използвай почистването на работната папка.')
        if row.get('session'):
            self.ctx.processing('discard',dict(session=row['session']))
            for key in ('session','folder','relative'):row.pop(key,None)
        row['state']='skipped';self.save();self.publish();return dict(message='Групата е пропусната. Архивните оригинали са запазени.')
    def accept(self,p):
        row=self.selected()
        if self.busy or not row or row['state']!='review':raise ValueError('Първо завърши и оцени избрания резултат.')
        result=self.ctx.processing('import',dict(session=row['session'],relative=row['relative'],usedIds=row['usedIds'],settings=row['effectiveSettings'],confirmed=True))
        row.update(state='approved',accepted=result['id'])
        # Persist the accepted archive ID before removing its source workspace,
        # so an app restart between import and cleanup can safely retry.
        self.save()
        cleanup_pending=False
        try:
            self.ctx.processing('discard',dict(session=row['session']))
            for key in ('session','folder','relative'):row.pop(key,None)
        except Exception as cleanup_error:
            cleanup_pending=True
            row['problem']='Стакът е прибран, но работната папка не можа да се изтрие: '+str(cleanup_error)
        if row.get('combined'):
            for source in self.state['groups']:
                if source.get('accepted') in row['ids']:source['combinedInto']=result['id']
        self.save();self.publish()
        message='Стакът е прибран и свързан с източниците.'
        if cleanup_pending:message+=' Работната папка остана за повторно почистване.'
        return dict(message=message)
    def cleanup_session(self,p):
        row=self.selected()
        if not row or not row.get('session'):raise ValueError('Няма запазена работна папка за почистване.')
        self.ctx.processing('discard',dict(session=row['session']))
        for key in ('session','folder','relative'):row.pop(key,None)
        row.pop('problem',None);self.save();self.publish();return dict(message='Работната папка е почистена. Архивните оригинали и прибраният стак остават.')
    def merge(self,p):
        if p.get('values'):self.configure(p)
        if not self.options.get('combine'):raise ValueError('Включи обединяването след оценката на дневните стакове.')
        if any(g['state'] not in ('approved','skipped') for g in self.state['groups'] if not g.get('combined')):raise ValueError('Оцени или пропусни всички дневни резултати първо.')
        groups={}
        for row in self.state['groups']:
            if not row.get('combined') and row.get('accepted') and row['state']=='approved':groups.setdefault((row['telescope'],row['object'],row['filter']),[]).append(row)
        created=[]
        for key,rows in groups.items():
            if len(rows)<2:continue
            ids=[r['accepted'] for r in rows]
            if any(g.get('combined') and g['ids']==ids and g['state']!='skipped' for g in self.state['groups']):continue
            row=dict(id=uuid.uuid4().hex,ids=ids,telescope=key[0],object=key[1],filter=key[2],night='Обединение на '+str(len(rows))+' стакове',kind='stack',state='pending',settings={},attempt=0,combined=True)
            self.state['groups'].append(row);created.append(row)
        if not created:raise ValueError('Нужни са поне два приети съпоставими стака от един обект, телескоп и филтър.')
        self.save();return self.launch(created)
    def restart(self,p):
        with self.lock:
            row=self.selected();runtime=self.runtimes.get(row['id']) if row else None
            if self.busy:
                if not runtime or row['state']!='running':raise ValueError('Избери работещ ден от таблицата за рестартиране.')
                runtime['restart']=True;runtime['cancel'].set();engine=runtime['engine']
            else:engine=None
        if engine:
            threading.Thread(target=engine.stop,daemon=True).start();return dict(message='Рестартира се само избраният ден. Другите Siril процеси продължават.')
        if p.get('values'):self.configure(p)
        if row:return self.launch([row])
        raise ValueError('Първо избери кадри и ден за обработка.')

    def cancel(self,p):
        self.local_cancel.set()
        with self.lock:
            for runtime in self.runtimes.values():runtime['restart']=False
        self.request_stop();return dict(message='Спират се Siril процесите; след приключването работните копия и временните резултати ще бъдат почистени.')
    def request_stop(self):
        with self.lock:
            engines=[r['engine'] for r in self.runtimes.values() if not r.get('stopRequested')]
            for runtime in self.runtimes.values():runtime['stopRequested']=True
        for engine in engines:threading.Thread(target=engine.stop,daemon=True).start()
    def worker_status(self,row,runtime,color,message):
        with self.lock:runtime['status']=color;runtime['message']=message;row['runtimeMessage']=message;self.refresh_status()
        self.publish()
    def refresh_status(self):
        if self.busy:
            active=sum(r['status'] in ('green','yellow') and getattr(r['engine'],'process',None) is not None for r in self.runtimes.values())
            warning=any(r['status']=='yellow' for r in self.runtimes.values())
            self.state.update(status='yellow' if warning or not active else 'green',message=f"Siril процеси: {active}/{len(self.batch_allocations)} · групи в работа: {len(self.runtimes)} · приключили: {self.state.get('batchCompleted',0)}/{self.state.get('batchTotal',0)}")
        else:
            review=sum(g['state']=='review' for g in self.state['groups']);errors=sum(g['state']=='error' for g in self.state['groups'])
            self.state.update(status='red',message=f'Siril е изключен · {review} резултата за оценка'+(f' · {errors} с грешка' if errors else ''))
    def snapshot_status(self,p):
        with self.lock:return dict(busy=self.busy,status=self.state['status'],message=self.state['message'],parallel=self.options['parallel'],limit=len(self.batch_allocations) if self.busy else len(allocation(self.options,max(1,len(self.state['groups'])))),running=len(self.runtimes))

    def preview(self,p):
        row=self.selected()
        if not row or not row.get('session') or not row.get('relative'):raise ValueError('Няма готов резултат.')
        return self.ctx.processing('open',dict(session=row['session'],relative=row['relative']))
    def graph(self,p):
        row=self.selected()
        if not row or not row.get('accepted'):raise ValueError('Първо приеми стака.')
        document=self.ctx.processing('graph',dict(id=row['accepted']));rows=[]
        def visit(node,depth=0):
            rows.append(dict(id=node['id'],name='  '*depth+node.get('name',node['id']),archive=node.get('archiveId',''),location=node.get('relativePath','Недостъпен източник')))
            for child in node.get('sources',[]):visit(child,depth+1)
        visit(document);self.state['graph']=rows;self.publish();return dict(accepted=True)
    def navigate(self,p):return self.ctx.processing('navigate',dict(id=p['selected']['id']))
    def stop(self):
        with self.lock:
            self.closed=True;self.local_cancel.set()
            for row in self.state['groups']:
                if row['state']=='running':row.update(state='paused',problem='Плъгинът е спрян. Работните копия са запазени.')
            self.save(force=True)
        self.request_stop()

    def publish(self,throttle=False):
        if self.closed:return
        with self.publish_lock:
            if not self.closed:self._publish(throttle)
    def _publish(self,throttle=False):
        if self.closed:return
        now=time.monotonic()
        with self.lock:
            if throttle and now-self.last_publish<.5:return
            self.last_publish=now
            selected=self.selected();o=settings(dict(self.options,**(selected.get('settings',{}) if selected and self.options.get('override') else {})))
            def field(kind,ident,label,**extra):return dict(type=kind,id=ident,label=label,**extra)
            def control(kind,ident,label,**extra):return field(kind,ident,label,value=o[ident],command='configure',disabled=self.busy,**extra)
            def action(ident,label,command,disabled=False,tone='normal'):return field('button',ident,label,command=command,disabled=disabled,tone=tone)
            choices=lambda pairs:[dict(value=k,label=v) for k,v in pairs]
            options=[field('text','autosave','',span=2,value='Настройките се запазват автоматично. При „Само за избрания ден“ промените важат само за него.'),control('checkbox','override','Само за избрания ден',span=2),
                control('input','executable','Siril CLI · отделна инсталация',maxLength=1024),action('browse-exe','Избери siril-cli.exe…','choose-executable',self.busy),
                control('number','parallel','Максимум паралелни Siril процеси',min=1,max=MAX_PARALLEL,step=1,span=2),control('checkbox','debayer','Debayer при CFA кадри'),control('checkbox','registration','Подравняване'),control('checkbox','mosaic','Пълно поле / мозайка'),control('checkbox','drizzle','Drizzle'),control('number','scale','Мащаб',min=.5,max=3,step=.1),control('number','pixfrac','Drizzle · дял на пиксела',min=.1,max=1,step=.05),control('select','normalize','Нормализация',options=choices([('addscale','Additive + scale'),('add','Additive'),('mulscale','Multiplicative + scale'),('mul','Multiplicative'),('none','Без нормализация')])),control('checkbox','rejection','Отхвърляне на пикселни отклонения'),control('number','sigmaLow','Долна sigma',min=.1,max=10,step=.1),control('number','sigmaHigh','Горна sigma',min=.1,max=10,step=.1),control('checkbox','background','Премахване на градиент · степен 1'),control('number','cpu','CPU нишки · общ бюджет' if not o['override'] else 'CPU нишки · горна граница за деня',min=1,max=os.cpu_count() or 1,step=1),control('number','memory','Общ дял от свободната RAM' if not o['override'] else 'RAM · горна граница за деня',min=.05,max=.95,step=.05),control('checkbox','combine','След оценка: обедини приетите дни')]
            for key,label in [('dark','Master Dark'),('flat','Master Flat'),('bias','Master Bias')]:options.extend([control('input',key,label,maxLength=1024),action('choose-'+key,'Избери '+label+'…','choose-'+key,self.busy)])
            limits=self.batch_allocations if self.busy else allocation(self.options,max(1,sum(g['state'] in ('pending','paused','error') for g in self.state['groups'])))
            cpu_limits=sorted({a['cpu'] for a in limits});cpu_text='/'.join(str(v) for v in cpu_limits)
            options.append(field('text','parallel-budget','',span=2,value=f"Сега: до {len(limits)} групи едновременно (зададен максимум {self.options['parallel']}). На процес: до {cpu_text} CPU нишки и {limits[0]['memory']*100:.1f}% от свободната RAM. Броят се ограничава от групите и общия CPU/RAM бюджет. Настройките на деня са допълнителни горни граници. Променя се преди стартиране на партидата."))
            labels=dict(pending='Чака',running='Обработва се',review='За оценка',approved='Прибран',paused='Спрян',error='Грешка',skipped='Пропуснат')
            rows=[dict(id=g['id'],night=g['night'],object=g['object'],frames=len(g['ids']),state=labels.get(g['state'],g['state'])+(' · обединен' if g.get('combinedInto') else ''),used=len(g.get('usedIds',[])),attempt=g.get('attempt',0),progress=str(g.get('progress',100 if g['state'] in ('review','approved') else 0))+'%') for g in self.state['groups']]
            center=[field('text','selection','',value=('Избран: '+selected['object']+' · '+selected['night']) if selected else 'Избери кадри от Подготовка.'),field('table','days','Дни и резултати',command='select',columns=[dict(key='night',label='Вечер / обединение'),dict(key='object',label='Обект'),dict(key='frames',label='Входни',format='number'),dict(key='used',label='Приети входни',format='number'),dict(key='state',label='Статус'),dict(key='progress',label='Напредък')],rows=rows),action('start','Потвърди и стаквай дните','start',self.busy or not any(g['state'] in ('pending','paused','error') and not g.get('combined') for g in self.state['groups']),'primary'),action('accept','Приеми и прибери избрания стак','accept',self.busy or not selected or selected['state']!='review','primary'),action('retry','Повтори само избрания ден','retry',self.busy or not selected),action('skip','Пропусни избрания ден','skip',self.busy or not selected),action('merge','Обедини приетите дневни стакове','merge',self.busy or not o['combine']),action('graph','От кои кадри / стакове е направен','graph',not selected or not selected.get('accepted'))]
            if selected and selected.get('session') and selected['state']=='approved':center.append(action('cleanup','Почисти работната папка','cleanup',self.busy))
            if selected and selected.get('problem'):center.append(field('text','problem','',value=selected['problem'],tone='error'))
            if selected and selected.get('rejectedIds'):center.append(field('text','rejected','',value=f"{len(selected['rejectedIds'])} входни кадъра не участват според Siril. Оригиналите остават в архива; няма автоматично изтриване.",tone='warning'))
            tone={'green':'success','yellow':'warning','red':'error'}[self.state['status']]
            right=[field('text','status','Siril',value=('● '+self.state['message']),tone=tone),action('restart','Рестартирай избрания ден','restart',self.busy and (not selected or selected['id'] not in self.runtimes or selected['state']!='running')),action('stop','Отмени и почисти текущата партида','stop',not self.busy),field('console','console','Конзола на Siril',value='\n'.join(self.state['log'])[-80000:])]
            live=[]
            for g in self.state['groups']:
                runtime=self.runtimes.get(g['id'])
                if not runtime:continue
                effective=g.get('effectiveSettings',{});process=getattr(runtime['engine'],'process',None)
                live.append(dict(id=g['id'],night=g['night'],pid=str(getattr(process,'pid','—')),cpu=effective.get('cpu','—'),memory=str(round(effective.get('memory',0)*100,1))+'%',phase=runtime['message']))
            if live:right.append(field('table','active-processes','Паралелна работа · отделни процеси и папки',columns=[dict(key='night',label='Вечер'),dict(key='pid',label='PID'),dict(key='cpu',label='CPU'),dict(key='memory',label='RAM'),dict(key='phase',label='Дейност')],rows=live))
            if self.image:right.append(field('image','result','Преглед на резултата',value=self.image))
            if selected and selected.get('relative'):right.append(action('preview','Отвори резултата за оценка','preview'))
            if self.state.get('graph'):right.append(field('table','sources','Съставни стакове и кадри · натисни за папката',command='navigate',columns=[dict(key='name',label='Източник'),dict(key='archive',label='Архив'),dict(key='location',label='Местоположение')],rows=self.state['graph'][:500]))
        try:
            self.ctx.publish('automatic-stacking',[field('group','settings','Настройки',children=options),field('group','jobs','Избрани дни и оценка',children=center),field('group','runtime','Siril · конзола и резултат',children=right)])
            self.ctx.publish('options',options)
            unfinished=[g for g in self.state['groups'] if g['state'] not in ('approved','skipped') and (g.get('session') or g.get('folder') or g.get('combined') or g.get('attempt',0) or g['state'] in ('running','paused','review'))]
            if self.options.get('combine'):
                accepted={}
                for g in self.state['groups']:
                    if not g.get('combined') and g['state']=='approved' and g.get('accepted'):accepted.setdefault((g['telescope'],g['object'],g['filter']),[]).append(g)
                for rows in accepted.values():
                    ids=[g['accepted'] for g in rows]
                    if len(rows)>1 and not any(g.get('combined') and g['ids']==ids and g['state']!='skipped' for g in self.state['groups']):unfinished.append(dict(rows[0],object=rows[0]['object']+' · чака обединяване'))
            self.ctx.publish('sessions',[] if not unfinished else [dict(type='list',id='unfinished-siril',label='Незавършени Siril сесии',items=[dict(id=g['id'],label=g['object']+' · '+g['night'],status='Изпълнява се' if self.busy and g['state']=='running' else 'Чака оценка' if g['state']=='review' else 'Незавършена / прекъсната сесия',tone='primary' if self.busy and g['state']=='running' else 'warning') for g in unfinished],command='resume',actionLabel='Продължи')])
        except PermissionError:pass
