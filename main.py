"""Siril Auto: Настройки → Работа → оценка на всеки ден. Talks to the host via its SDK only.

The controller only wires the SDK to the tested modules: engine (Siril scripts and process),
parts (part folders), scheduler (shared queue of part/merge jobs), ai_filter (optional AI
evaluator), slots (Siril workers and visual Siril) and view (screens)."""
from __future__ import annotations
import copy,json,math,os,threading,time,uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from .engine import Engine,DEFAULTS,allocation,settings,plan,merge_options,included
from . import ai_filter,parts as partlib,slots as slotlib,view
from .scheduler import Queue

GLOBAL_KEYS=('executable','parallel','cpu','memory','combine','override')
RESET_KEYS=('session','folder','relative','parts','merge','usedIds','rejectedIds','aiRemovedIds','cfa','progress','effectiveSettings','problem','accepted')

def setup(context):
    controller=Controller(context);controller.register();controller.publish();return controller.stop

class Controller:
    def __init__(self,context,engine_factory=Engine):
        self.ctx=context;self.engine_factory=engine_factory;self.folder=Path(context.storage())
        self.lock=threading.RLock();self.publish_lock=threading.RLock();self.ai_lock=threading.Lock()
        self.busy=False;self.closed=False;self.local_cancel=threading.Event();self.engines={};self.batch_allocations=[]
        self.last_publish=0.;self.image=None;self.confirm_until=0.;self.abandon_after=False;self.ai_available=False
        initial=dict(DEFAULTS,**{k:v for k,v in context.settings().items() if k in DEFAULTS})
        try:self.options=settings(initial)
        except ValueError:self.options=settings(dict(DEFAULTS,executable=initial.get('executable','') if isinstance(initial.get('executable'),str) else ''))
        if not self.options['executable']:
            candidate=Path(os.environ.get('ProgramFiles',r'C:\Program Files'))/'Siril/bin/siril-cli.exe'
            if candidate.is_file():self.options['executable']=str(candidate)
        self.state=dict(groups=[],selected='',phase='settings',scope=[],slots=[],selectedSlot=1,graph=[])
        saved=self.folder/'session.json';saved_keys=set()
        if saved.is_file():
            try:document=json.loads(saved.read_text(encoding='utf-8'));saved_keys=set(document);self.state.update(document)
            except (ValueError,OSError):pass
        for row in self.state['groups']:
            if row['state'] in ('running','merging'):row.update(state='paused',problem='Работата е прекъсната. Готовите части са запазени.')
        Queue([g for g in self.state['groups'] if g.get('parts')]).resume()
        for slot in self.state['slots']:slot.update(status='red',pid=None,task='')
        if not self.state['groups']:self.state['phase']='settings'
        elif 'phase' not in saved_keys and any(g['state']!='pending' for g in self.state['groups']):self.state['phase']='work'

    # ---------- persistence and helpers ----------
    def save(self,force=False):
        with self.lock:
            if self.closed and not force:return
            target=self.folder/'session.json';temp=target.with_suffix('.tmp')
            temp.write_text(json.dumps(self.state,ensure_ascii=False,indent=1),encoding='utf-8');os.replace(temp,target)
    def selected(self):return next((g for g in self.state['groups'] if g['id']==self.state['selected']),None)
    def day(self,ident):
        row=next((g for g in self.state['groups'] if g['id']==ident),None)
        if row is None:raise ValueError('Непознат ден.')
        return row
    def scope_days(self):return [g for g in self.state['groups'] if g['id'] in self.state.get('scope',[])]
    def confirming(self):return time.monotonic()<self.confirm_until
    def register(self):
        for name,action in [('prepare',self.prepare),('resume',self.resume),('finish',self.abandon),('abandon',self.abandon),('configure',self.configure),
                            ('start',self.start),('back',self.back),('select',self.select),('review',self.review),('accept',self.accept),('repeat',self.repeat),
                            ('reconfigure',self.reconfigure),('retry',self.retry),('merge',self.merge),('slot',self.slot),('visual',self.visual),
                            ('preview',self.preview),('graph',self.graph),('navigate',self.navigate),('status',self.snapshot_status),
                            ('choose-executable',lambda p:self.choose('executable')),('choose-dark',lambda p:self.choose('dark')),
                            ('choose-flat',lambda p:self.choose('flat')),('choose-bias',lambda p:self.choose('bias'))]:self.ctx.command(name,action)

    # ---------- selection and settings ----------
    def prepare(self,p):
        ids=p.get('context',{}).get('ids',[]);self.ai_available=ai_filter.available(self.ctx)
        if self.busy or not ids:self.publish();return dict(accepted=True) if self.busy else dict(message='Избери кадри от началния екран на Подготовка.')
        previous={i for g in self.state['groups'] if not g.get('combined') for i in g['ids']}
        if set(ids)==previous:self.publish();return dict(accepted=True)
        if any(g['state'] not in ('approved','skipped') for g in self.state['groups']):
            self.publish();return dict(message='Има незавършена Siril работа. Продължи я или я откажи, преди да започнеш нова.')
        self.retry_orphans()
        rows={r['id']:r for r in self.ctx.processing('describe',dict(ids=ids))['rows']}
        if set(ids)-set(rows):raise ValueError('Подборът е променен. Обнови архива и избери кадрите отново.')
        groups={}
        for ident in ids:
            r=rows[ident]
            if r.get('decision')=='excluded':continue
            if r['kind'] not in ('frame','stack'):raise ValueError('Избери FITS кадри или приети стакове.')
            groups.setdefault((r['telescope'],r['object'],r['night'],r.get('filter',''),r.get('width'),r.get('height'),r['kind']),[]).append(ident)
        if not groups:raise ValueError('Няма одобрени кадри.')
        self.state.update(groups=[dict(id=uuid.uuid4().hex,ids=v,telescope=k[0],object=k[1],night=k[2],filter=k[3],kind=k[6],state='pending',settings={},attempt=0) for k,v in sorted(groups.items(),key=lambda kv:str(kv[0]))],
                          selected='',phase='settings',scope=[],graph=[])
        self.image=None;self.save();self.publish()
        return dict(message=f"Избрани са {len(self.state['groups'])} дни. Настрой и натисни „Стартирай“.")
    def form_options(self):
        days=self.scope_days()
        if not days:return self.options
        used={k:v for k,v in days[0].get('effectiveSettings',{}).items() if k in DEFAULTS and k not in GLOBAL_KEYS}
        return settings({**self.options,**used,**days[0].get('settings',{})})
    def configure(self,p):
        values={k:v for k,v in p.get('values',{}).items() if k in DEFAULTS and k!='override'}
        days=self.scope_days()
        if self.busy and not days:raise ValueError('Изчакай Siril да приключи, преди да променяш общите настройки.')
        merged=settings(dict(self.form_options(),**values))
        with self.lock:
            for key in GLOBAL_KEYS:
                if key in values:self.options[key]=merged[key]
            if days:
                for row in days:row['settings']={k:merged[k] for k in DEFAULTS if k not in GLOBAL_KEYS}
            else:self.options=settings(dict(self.options,**{k:merged[k] for k in values}))
        self.ctx.save_settings(self.options);self.save();self.publish();return dict(saved=True)
    def choose(self,key):
        if self.busy and not self.scope_days():raise ValueError('Изчакай текущото изпълнение.')
        result=self.ctx.processing('choose',dict(kind='executable' if key=='executable' else 'master'))
        if result['path']:return self.configure(dict(values={key:result['path']}))
        return dict(saved=True)
    def back(self,p):
        self.state.update(phase='work',scope=[]);self.save();self.publish();return dict(accepted=True)
    def select(self,p):
        ident=(p.get('selected') or {}).get('id');self.day(ident)
        with self.lock:self.state['selected']=ident;self.state['graph']=[];self.image=None
        self.load_preview(ident);self.save();self.publish();return dict(selected=True)
    def load_preview(self,ident):
        row=copy.deepcopy(self.day(ident))
        if row.get('session') and row.get('relative'):
            try:
                image=self.ctx.processing('preview',dict(session=row['session'],relative=row['relative']))['image']
                with self.lock:
                    if self.state['selected']==ident:self.image=image
            except (OSError,ValueError):pass

    # ---------- evaluation ----------
    def review(self,p):
        self.select(p);return dict(message='Прегледай резултата: „Приеми“ го прибира, „Повтори“ го връща за пренастройване.')
    def accept(self,p):
        row=self.selected()
        if not row or row['state']!='review' or row.get('repeat'):raise ValueError('Първо избери ден, готов за оценка.')
        info=dict(row['effectiveSettings'],parts=len(row.get('parts') or []),aiRemovedIds=row.get('aiRemovedIds',[]))
        result=self.ctx.processing('import',dict(session=row['session'],relative=row['relative'],usedIds=row['usedIds'],settings=info,confirmed=True))
        with self.lock:row.update(state='approved',accepted=result['id'],problem='')
        self.save();problem=self.discard(row['session'])
        for key in ('session','folder','relative'):row.pop(key,None)
        cleanup=' Работната папка не можа да се изтрие и ще бъде опитана отново: '+problem if problem else ''
        if row.get('combined'):
            for source in self.state['groups']:
                if source.get('accepted') in row['ids']:source['combinedInto']=result['id']
        self.save();self.publish();return dict(message='Стакът е прибран и свързан с кадрите.'+cleanup)
    def repeat(self,p):
        row=self.selected()
        if not row or row['state']=='approved':raise ValueError('Избери неприет ден.')
        row['repeat']=True;self.save();self.publish()
        return dict(message='Денят е за повторение. Натисни „Пренастрой“, когато си готов.')
    def reconfigure(self,p):
        marked=[g['id'] for g in self.state['groups'] if g.get('repeat') and g['state']!='approved']
        if not marked:raise ValueError('Няма дни за повторение.')
        self.state.update(phase='settings',scope=marked);self.save();self.publish();return dict(accepted=True)
    def retry(self,p):
        row=self.selected()
        if not row or row['state']!='error':raise ValueError('Избери ден с грешка.')
        self.requeue(row);return self.launch([row])
    def requeue(self,row):
        """Re-queue failed jobs; if every job had finished (the check after Siril failed), rerun the last step."""
        if not row.get('parts'):return
        Queue([row]).reset_failed(row['id'])
        if all(p['state']=='done' for p in row['parts']) and row.get('merge') in (None,'done'):
            if row.get('merge'):row['merge']='pending'
            else:row['parts'][0]['state']='pending'

    # ---------- starting and stopping ----------
    def start(self,p):
        if p.get('values'):self.configure(p)
        if self.busy:raise ValueError('Siril още работи. Изчакай да приключи.')
        days=self.scope_days()
        if days:
            for row in days:self.reset_day(row)
            rows=days
        else:
            rows=[g for g in self.state['groups'] if g['state'] in ('pending','paused','error') and not g.get('repeat')]
            for row in rows:
                if row['state']=='error':self.requeue(row)
        if not rows:raise ValueError('Няма дни за обработка.')
        self.state.update(phase='work',scope=[]);return self.launch(rows)
    def session_folder(self,session):
        """The workspace folder of one of our sessions, or None when it cannot be identified."""
        for row in self.state['groups']:
            if row.get('session')==session and row.get('folder'):return Path(row['folder'])
        root=Path(self.state.get('workRoot') or self.folder.parent.parent/'PluginWork'/self.folder.name)
        folder=root/session
        return folder if len(session)==32 and folder.is_dir() and (folder/'session.json').is_file() else None
    def unlink_siril_links(self,session):
        folder=self.session_folder(session)
        if folder is not None:partlib.remove_links(folder)
    def discard(self,session)->str:
        """Delete a host workspace; on failure keep its reference for a later retry."""
        try:self.unlink_siril_links(session);self.ctx.processing('discard',dict(session=session));return ''
        except Exception as error:
            with self.lock:
                orphans=self.state.setdefault('orphanSessions',[])
                if session not in orphans:orphans.append(session)
            return str(error)
    def retry_orphans(self)->list[str]:
        problems=[]
        for session in list(self.state.get('orphanSessions',[])):
            try:self.unlink_siril_links(session);self.ctx.processing('discard',dict(session=session))
            except ValueError as error:
                if 'не принадлежи' not in str(error) and 'Невалидна' not in str(error):problems.append(str(error));continue
            except Exception as error:problems.append(str(error));continue
            with self.lock:self.state['orphanSessions'].remove(session)
        return problems
    def reset_day(self,row):
        if row.get('session'):self.discard(row['session'])
        for key in RESET_KEYS:row.pop(key,None)
        row.update(state='pending',repeat=False)
    def abandon(self,p):
        if not self.confirming():
            self.confirm_until=time.monotonic()+10;self.publish()
            return dict(message='Натисни отново до 10 секунди, за да откажеш работата.')
        self.confirm_until=0.
        def work(cancel,progress):
            # Stop Siril first; the cleanup runs once the batch has really ended.
            if self.busy:
                self.local_cancel.set();self.request_stop();deadline=time.monotonic()+120
                while self.busy and time.monotonic()<deadline:time.sleep(.2)
                if self.busy:raise RuntimeError('Siril не спря навреме. Опитай отново.')
            progress(0,1,'Почистване на работните папки');self.cleanup_all();progress(1,1,'Работата е отказана')
            return dict(abandoned=True)
        task=self.ctx.task('Siril · отказ на работата',work,'preparation')
        # On success the host returns to the day selection in Подготовка.
        return dict(task,returnToPreparation=True,message='Работата се отказва. Прибраните стакове остават в архива.')
    def cleanup_all(self):
        problems=[]
        for row in self.state['groups']:
            if row.get('session'):
                problem=self.discard(row['session'])
                if problem:problems.append(problem)
        problems+=[p for p in self.retry_orphans() if p not in problems]
        with self.lock:self.state.update(groups=[],selected='',phase='settings',scope=[],graph=[]);self.image=None
        self.save();self.publish()
        if problems:raise OSError('Някои работни папки останаха за ръчна проверка: '+problems[0])
    def resume(self,p):
        self.ai_available=ai_filter.available(self.ctx)
        if self.state['groups']:self.state['phase']='work'
        self.save();self.publish();return dict(openContribution='automatic-stacking')
    def request_stop(self):
        with self.lock:engines=list(self.engines.values())
        for engine in engines:threading.Thread(target=engine.stop,daemon=True).start()
    def stop(self):
        with self.lock:
            self.closed=True;self.local_cancel.set()
            for row in self.state['groups']:
                if row['state'] in ('running','merging'):row.update(state='paused',problem='Плъгинът е спрян. Готовите части са запазени.')
            Queue([g for g in self.state['groups'] if g.get('parts')]).resume()
            self.save(force=True)
        self.request_stop()

    # ---------- batch execution ----------
    def estimate_jobs(self,rows):
        total=0
        for row in rows:
            try:o=settings(dict(self.options,**row.get('settings',{})))
            except ValueError:total+=1;continue  # reported for that day when it runs
            total+=len(row['parts']) if row.get('parts') else (1 if row.get('combined') or not o['chunkSize'] else len(partlib.split(len(row['ids']),o['chunkSize'])))
        return max(1,total)
    def launch(self,rows):
        with self.lock:
            if self.busy:raise ValueError('Siril вече работи.')
            self.local_cancel.clear();self.abandon_after=False;self.busy=True;self.batch_options=copy.deepcopy(self.options)
            self.batch_allocations=allocation(self.batch_options,self.estimate_jobs(rows))
            old={s['number']:s for s in self.state['slots']}
            self.state['slots']=[old.get(n) or slotlib.new_slots(n)[-1] for n in range(1,len(self.batch_allocations)+1)]
            for row in rows:
                row['state']='pending' if not row.get('parts') else 'running';row['problem']='';row['attempt']=row.get('attempt',0)+1
        self.save();self.publish()
        try:return self.ctx.task('Siril · автоматично стакване',lambda cancel,progress:self.work(rows,cancel,progress),'preparation')
        except BaseException:
            with self.lock:self.busy=False
            self.publish();raise
    def stopped(self,cancel):return cancel.is_set() or self.local_cancel.is_set() or self.closed
    def work(self,rows,cancel,progress):
        queue=Queue(rows);preps=[r for r in rows if not r.get('parts')];fractions={}
        free=list(range(len(self.batch_allocations)));running={};failures=[]
        def report():
            with self.lock:
                for row in rows:
                    total=len(row.get('parts') or [1])+(1 if row.get('merge') else 0)
                    done=sum(p['state']=='done' for p in row.get('parts') or [])+(row.get('merge')=='done')
                    row['progress']=round(100*min(1,(done+sum(v for (d,_),v in fractions.items() if d==row['id']))/total))
                progress(sum(r['progress'] for r in rows)/100,len(rows),'Siril · '+str(sum(r['state'] in ('review','approved') for r in rows))+'/'+str(len(rows))+' дни готови')
            self.publish(throttle=True)
        def take():
            with self.lock:
                job=queue.next_job()
                if job:return job
                if preps:return dict(day=preps.pop(0)['id'],kind='prepare',name='prepare')
            return None
        try:
            with ThreadPoolExecutor(max_workers=len(self.batch_allocations),thread_name_prefix='Siril') as pool:
                while True:
                    if self.stopped(cancel):self.request_stop()
                    while free and not self.stopped(cancel):
                        job=take()
                        if not job:break
                        slot=free.pop(0);running[pool.submit(self.execute,job,slot,cancel,fractions,report)]=(slot,job)
                    if not running:break
                    done,_=wait(running,timeout=.2,return_when=FIRST_COMPLETED)
                    for future in done:
                        slot,job=running.pop(future);free.append(slot);outcome=future.result();row=self.day(job['day'])
                        with self.lock:
                            fractions.pop((job['day'],job['name']),None)
                            if job['kind']!='prepare' and outcome!='stopped':queue.finish(job,outcome=='ok')
                            if outcome not in ('ok','stopped'):failures.append(row['night']+': '+outcome)
                            if job['kind']=='prepare' and outcome=='ok':row['state']='running'
                            elif outcome!='stopped' and row.get('parts'):self.update_day(row,queue)
                            elif job['kind']=='prepare' and outcome not in ('ok','stopped'):row.update(state='error',problem=outcome)
                        self.save();report()
            if self.stopped(cancel):
                with self.lock:
                    queue.resume()
                    for row in rows:
                        if row['state'] not in ('review','approved','error'):row.update(state='paused',problem='Спрян. Готовите части са запазени.')
                raise InterruptedError('Siril работата е спряна.')
            if failures:raise RuntimeError(str(len(failures))+' дни с грешка. Останалите са готови за оценка. '+failures[0])
            return dict(review=sum(r['state']=='review' for r in rows),parallel=len(self.batch_allocations))
        except (InterruptedError,RuntimeError):raise
        except Exception as error:
            # Unexpected failure: keep finished parts, nothing stays "running".
            with self.lock:
                queue.resume()
                for row in rows:
                    if row['state'] not in ('review','approved','error'):row.update(state='paused',problem='Неочаквана грешка: '+str(error))
            raise
        finally:
            with self.lock:
                self.busy=False
                for slot in self.state['slots']:slot.update(status='red',pid=None,task='')
            self.save()
            if self.abandon_after and not self.closed:
                try:self.cleanup_all()
                except OSError:pass
            self.publish()
    def update_day(self,row,queue):
        state=queue.day_state(row['id'])
        if state=='review':
            try:self.finalize(row)
            except Exception as error:row.update(state='error',problem=str(error));return
            row['state']='review'
            if not self.selected() or self.selected()['state'] not in ('review','approved'):
                self.state['selected']=row['id'];threading.Thread(target=self.load_preview,args=(row['id'],),daemon=True).start()
        elif state=='error':row.update(state='error',problem=row.get('problem') or 'Част от деня завърши с грешка.')
        else:row['state']=state
    def finalize(self,row):
        used_by_part=[p.get('used',[]) for p in row['parts']]
        if len(row['parts'])>1:
            numbers=included(Path(row['folder'])/'merge',row['mergeSequence'],len(row['parts']))
            used=[i for n in numbers for i in used_by_part[n-1]]
        else:used=used_by_part[0]
        if not used:raise ValueError('Нито един кадър не е включен в резултата.')
        staged=[s['id'] for p in row['parts'] for s in p['sources']]
        row.update(usedIds=used,rejectedIds=[i for i in staged if i not in set(used)],relative=partlib.result_relative(row['parts']),progress=100)
    def options_for(self,row,slot):
        budget=self.batch_allocations[slot];o=settings(dict(self.batch_options,**row.get('settings',{})))
        o['cpu']=min(o['cpu'],budget['cpu']);o['memory']=min(o['memory'],budget['memory'])
        if row.get('combined'):o.update(debayer=False,dark='',flat='',bias='',background=False,drizzle=False,chunkSize=0,aiFilter=False)
        if row['kind']!='frame':o['aiFilter']=False
        return o
    def execute(self,job,slot_index,cancel,fractions,report):
        """Runs in a pool thread. Returns 'ok', 'stopped' or an error text."""
        row=self.day(job['day']);slot=self.state['slots'][slot_index]
        controller=self
        class Signal:
            def is_set(_):return controller.stopped(cancel)
        signal=Signal();label=row['object']+' · '+row['night']
        def log(line):
            with self.lock:slotlib.append_log(slot,'['+label+'] '+str(line))
            self.publish(throttle=True)
        def set_task(text,status=None):
            with self.lock:
                slot['task']=label+' · '+text
                if status:slot['status']=status
            self.publish(throttle=True)
        def fraction(value):
            with self.lock:fractions[(job['day'],job['name'])]=max(0.,min(.99,value))
            report()
        try:
            o=self.options_for(row,slot_index)
            if job['kind']=='prepare':
                set_task('подготвя кадрите');self.prepare_day(row,o,signal,log,fraction);return 'ok'
            folder=Path(row['folder']);total=len(row['parts'])
            if job['kind']=='merge':
                set_task('слива частите');partlib.prepare_merge(folder,[p['name'] for p in row['parts']])
                mo=merge_options(o,total);commands,sequence=plan(mo,False,True);target=folder/'merge'
                with self.lock:row['mergeSequence']=sequence
            else:
                part=next(p for p in row['parts'] if p['name']==job['name']);index=row['parts'].index(part)+1
                set_task(f'част {index}/{total}' if total>1 else 'стаква');target=folder/part['name']
                partlib.reset_part(target);partlib.copy_masters(folder,target)
                commands,sequence=plan(o,bool(row.get('cfa')),bool(row.get('combined')))
            with self.lock:row['effectiveSettings']=o;slot.update(folder=str(target),cpu=o['cpu'],memory=o['memory'])
            engine=self.engine_factory()
            with self.lock:self.engines[(job['day'],job['name'])]=engine
            def status(color,message):
                with self.lock:
                    slot['status']=color;process=getattr(engine,'process',None);slot['pid']=getattr(process,'pid',None)
                self.publish(throttle=True)
            try:engine.run(o['executable'],str(target),commands,signal,log,status,lambda d,t,p:fraction(d/max(1,t)))
            finally:
                with self.lock:self.engines.pop((job['day'],job['name']),None)
            if signal.is_set():return 'stopped'
            if not (target/'result.fit').is_file():raise ValueError('Siril не създаде result.fit.')
            if job['kind']=='part':
                numbers=set(included(target,sequence,len(part['sources'])))
                with self.lock:part['used']=[s['id'] for s in part['sources'] if s['number'] in numbers]
            log('Готово.');return 'ok'
        except InterruptedError:return 'stopped'
        except Exception as error:
            if signal.is_set():return 'stopped'
            with self.lock:row['problem']=str(error)
            log('Грешка: '+str(error));return str(error)
        finally:
            with self.lock:slot.update(task='',pid=None,status='red')
            self.publish(throttle=True)
    def prepare_day(self,row,o,signal,log,fraction):
        if row.get('session'):
            self.discard(row['session'])
            with self.lock:row.pop('session',None);row.pop('folder',None)
        ids=list(row['ids']);removed=[]
        if o['aiFilter'] and o['aiPercent']>0 and self.ai_available:
            log('AI оценка на '+str(len(ids))+' кадъра…')
            try:
                with self.ai_lock:scores=ai_filter.scores(self.ctx,ids,signal.is_set,lambda f,m:fraction(.2*f))
                removed=ai_filter.removed(scores,o['aiPercent']);log(f"AI маха {len(removed)} от {len(ids)} кадъра ({o['aiPercent']:g} %).")
            except InterruptedError:raise
            except Exception as error:
                with self.lock:row['problem']='AI филтърът не е приложен: '+str(error)
                log(row['problem'])
        keep=[i for i in ids if i not in set(removed)]
        if not keep:raise ValueError('AI филтърът не остави кадри.')
        stage=self.ctx.processing('stage',dict(ids=keep),signal,lambda d,t,p:fraction(.2+.8*d/max(1,t)))
        with self.lock:row.update(session=stage['session'],folder=stage['folder'],aiRemovedIds=removed);self.state['workRoot']=str(Path(stage['folder']).parent)
        self.save()
        cfa=[bool(s['meta'].get('headers',{}).get('BAYERPAT')) for s in stage['sources']]
        if any(cfa) and not all(cfa):raise ValueError('Денят съдържа едновременно CFA и моно/RGB кадри. Раздели подбора.')
        for key in ('dark','flat','bias'):
            if o[key]:self.ctx.processing('master',dict(session=stage['session'],kind=key,path=o[key]))
        sizes=partlib.split(len(stage['sources']),o['chunkSize'])
        split=partlib.distribute(stage['folder'],stage['sources'],sizes)
        with self.lock:
            row.update(cfa=all(cfa),parts=[dict(name=p['name'],state='pending',sources=p['sources']) for p in split],merge='pending' if len(split)>1 else None)
        log(f'{len(stage["sources"])} кадъра в {len(split)} част(и).')

    # ---------- other commands ----------
    def merge(self,p):
        if not self.options.get('combine'):raise ValueError('Включи „Обедини приетите дни“ в настройките.')
        if any(g['state'] not in ('approved','skipped') for g in self.state['groups'] if not g.get('combined')):raise ValueError('Оцени всички дни първо.')
        groups={}
        for row in self.state['groups']:
            if not row.get('combined') and row.get('accepted') and row['state']=='approved':groups.setdefault((row['telescope'],row['object'],row['filter']),[]).append(row)
        created=[]
        for key,rows in groups.items():
            ids=[r['accepted'] for r in rows]
            if len(rows)<2 or any(g.get('combined') and g['ids']==ids and g['state']!='skipped' for g in self.state['groups']):continue
            row=dict(id=uuid.uuid4().hex,ids=ids,telescope=key[0],object=key[1],filter=key[2],night='Обединение на '+str(len(rows))+' дни',kind='stack',state='pending',settings={},attempt=0,combined=True)
            self.state['groups'].append(row);created.append(row)
        if not created:raise ValueError('Нужни са поне два приети дни от един обект, телескоп и филтър.')
        self.save();return self.launch(created)
    def slot(self,p):
        number=int((p.get('selected') or {}).get('id',1))
        if not any(s['number']==number for s in self.state['slots']):raise ValueError('Непознат Siril.')
        self.state['selectedSlot']=number;self.publish();return dict(selected=True)
    def visual(self,p):
        slot=next((s for s in self.state['slots'] if s['number']==self.state.get('selectedSlot')),None)
        pid=slotlib.open_visual(self.options['executable'],(slot or {}).get('folder',''),self.folder)
        return dict(message=f'Siril е отворен визуално (PID {pid}). Работи в същата папка; фоновият процес не се прекъсва.')
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
    def snapshot_status(self,p):
        with self.lock:
            working=sum(s['status']!='red' for s in self.state['slots'])
            return dict(busy=self.busy,status='green' if working else 'red',message=f'Siril процеси: {working}/{len(self.state["slots"])}',parallel=self.options['parallel'],running=working)

    # ---------- publishing ----------
    def budget_text(self):
        limits=allocation(self.options,max(1,self.options['parallel']))
        if len(limits)<2:return ''
        cpu='/'.join(str(v) for v in sorted({a['cpu'] for a in limits}))
        return f"Всеки Siril получава до {cpu} CPU нишки и {limits[0]['memory']*100:.1f} % от свободната RAM."
    def publish(self,throttle=False):
        if self.closed:return
        with self.publish_lock:
            if not self.closed:self._publish(throttle)
    def _publish(self,throttle):
        now=time.monotonic()
        with self.lock:
            if throttle and now-self.last_publish<.5:return
            self.last_publish=now;confirm=self.confirming();scope=self.scope_days()
            if self.state['phase']=='settings':
                screen=view.settings_screen(self.form_options(),scope,self.ai_available,self.busy and not scope,confirm,self.budget_text(),has_work=any(g['state']!='pending' for g in self.state['groups']),running=self.busy)
            else:
                screen=view.work_screen(copy.deepcopy(self.state),copy.deepcopy(self.state['slots']) or slotlib.new_slots(self.options['parallel']),self.state.get('selectedSlot',1),self.busy,confirm,self.image,bool(self.options.get('combine')))
            options=[c for c in view.settings_screen(self.options,[],self.ai_available,self.busy,False,self.budget_text())[0]['children'] if c.get('command') not in ('start','back','abandon')]
            activity=view.activity(self.state['groups'],self.busy,confirm)
        try:
            self.ctx.publish('automatic-stacking',screen);self.ctx.publish('options',options);self.ctx.publish('sessions',activity)
        except PermissionError:pass
