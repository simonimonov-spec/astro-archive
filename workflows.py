"""IASC-owned work copies and reviewed reports. No host implementation imports."""
from pathlib import Path
import copy,hashlib,json,os,shutil,subprocess,threading,time,uuid
from .downloads import FITS,MARKER,slug,digest_file,check_cancel
from .servers import ServerChecks
from .reports import REPORT_NAMES,report_info,report_suffix
from . import mpc_export

MAX_TEXT=64000

def atomic(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('x',encoding='utf-8') as stream:json.dump(value,stream,ensure_ascii=False);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path)
    finally:temp.unlink(missing_ok=True)

def read_report(path):
    path=Path(path)
    if path.suffix.lower() not in ('.txt','.psv','.xml','.log') or path.is_symlink() or not path.is_file() or path.stat().st_size>256000:raise ValueError('Избери текстов отчет до 256 KB.')
    with path.open('rb') as stream:data=stream.read(256001)
    if len(data)>256000 or b'\0' in data:raise ValueError('Файлът не е поддържан текстов отчет.')
    try:text=data.decode('utf-8-sig')
    except UnicodeDecodeError:text=data.decode('cp1251')
    if len(text)>MAX_TEXT:raise ValueError('Отчетът е над лимита от 64 000 знака. Избери отчет само за текущия комплект.')
    return text,hashlib.sha256(data).hexdigest()

class Workflows:
    def __init__(self,parent):
        self.p=parent;self.ctx=parent.ctx;self.path=parent.folder/'work-sessions.json';self.pending=[];self.choice='';self.current=''
        try:self.sessions=json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError,ValueError):self.sessions=[]
        for session in self.sessions:
            if session['state'] in ('copying','archiving'):session.update(state='interrupted',problem='Предишната дейност е прекъсната. Копията и архивът са запазени; провери сесията.')
            # Correct the old auto-reader's ACK-only draft. Keep an audit copy and
            # never replace manually pasted or explicitly chosen content.
            if session['state']!='completed' and session.get('draftOrigin')=='auto' and report_info(session.get('draft',''))['kind']=='ack':
                session['ignoredAutoDraft']=dict(text=session['draft'],source=session.get('reportSource',''),sha256=session.get('reportHash',''))
                session.update(draft='',draftOrigin='',state='waiting')
                for key in ('reportSource','reportHash','candidate'):session.pop(key,None)
        self.save()
        defaults=dict(workOperator='',workRoot='',workProgram='',workReports='',workAutoReport=True,workAutoMpc=False,workReportView='source')
        for key,value in defaults.items():parent.options.setdefault(key,value)
        # One-time migration: use the source report and stop automatic conversion.
        # Drafts and previously generated exports remain untouched.
        if parent.options.get('workReportReaderVersion',0)<1:
            parent.options.update(workAutoMpc=False,workReportView='source',workReportReaderVersion=1)
        if not parent.options['workProgram'] and Path(r'C:\Astrometrica\Astrometrica.exe').is_file():parent.options['workProgram']=r'C:\Astrometrica\Astrometrica.exe'
        if not parent.options['workReports'] and Path(r'C:\Astrometrica\Data').is_dir():parent.options['workReports']=r'C:\Astrometrica\Data'
        self.servers=ServerChecks(parent)
        for name,fn in {'prepare-work':self.prepare,'work-options':self.configure,'work-root':self.choose_root,'work-reports':self.choose_reports,
            'work-program':self.choose_program,'work-choice':self.choose,'work-start':self.start,'work-resume':self.resume,
            'work-draft':self.draft,'work-report-file':self.import_report,'work-report-latest':self.import_latest,'work-log-file':self.import_log,'work-copy':self.copy_report,'work-open':self.open_program,
            'work-path':self.copy_path,'work-finish':self.finish,'work-abort':self.abort,'work-cleanup-retry':self.retry_cleanup,'work-mpc-build':self.build_mpc,'work-report-view':self.report_view,'work-mpc-path':self.copy_mpc_path}.items():
            def guarded(parameters,handler=fn):
                with self.p.lock:return handler(parameters)
            self.ctx.command(name,guarded)

    def save(self):atomic(self.path,self.sessions)
    def selected(self):return next((s for s in self.sessions if s['id']==self.current),None)
    def unfinished(self):return [s for s in self.sessions if s['state'] not in ('completed','cancelled')]
    def configure(self,p):
        values=p.get('values',{})
        for key in ('workOperator','workAutoReport','workAutoMpc'):
            if key in values:self.p.options[key]=values[key]
        if not isinstance(self.p.options['workOperator'],str) or len(self.p.options['workOperator'])>120 or type(self.p.options['workAutoReport']) is not bool or type(self.p.options['workAutoMpc']) is not bool:raise ValueError('Невалидни настройки за работната сесия.')
        self.p.persist();self.p.publish();return dict(saved=True)
    def choose_root(self,p):
        chosen=self.ctx.archives('choose',dict(title='Отделна папка за работните IASC копия')).get('path')
        if not chosen:return dict(cancelled=True)
        root=Path(chosen).resolve(strict=True)
        if not root.is_dir() or root==Path(root.anchor):raise ValueError('Избери отделна работна папка, а не целия диск.')
        for a in self.ctx.archives('list')['archives']:
            if a.get('path'):
                archive=Path(a['path']).resolve()
                if root.is_relative_to(archive) or archive.is_relative_to(root):raise ValueError('Работната папка трябва да е извън архивите.')
        self.p.options['workRoot']=str(root);self.p.persist();self.p.publish();return dict(saved=True)
    def choose_reports(self,p):
        path=self.ctx.archives('choose',dict(title='Папка за отчетите на Astrometrica (OutDir)')).get('path')
        if path:self.p.options['workReports']=str(Path(path).resolve(strict=True));self.p.persist();self.p.publish()
        return dict(saved=bool(path))
    def choose_program(self,p):
        path=self.ctx.archives('choose-file',dict(kind='program',title='Astrometrica.exe')).get('path')
        if path:self.p.options['workProgram']=path;self.p.persist();self.p.publish()
        return dict(saved=bool(path))

    def prepare(self,p):
        ids=p.get('context',{}).get('ids',[])
        if not ids:self.p.publish();return dict(message='Избери IASC кадри от Подготовка. За незавършена сесия използвай списъка отдолу.')
        if self.p.busy:raise ValueError('Изчакай текущата IASC дейност.')
        self.p.sync_archive();rows=self.ctx.processing('describe',dict(ids=ids))['rows']
        if {r['id'] for r in rows}!=set(ids):raise ValueError('Подборът е променен. Обнови кадрите.')
        if not self.p.options['root']:raise ValueError('Първо задай папка за IASC архива.')
        root=Path(self.p.options['root']).resolve(strict=True);sets=self.p.store.rows();groups={}
        for row in rows:
            if row['archiveId']!=self.p.options['archiveId'] or row.get('decision')=='excluded':raise ValueError('Избери само включени кадри от IASC архива.')
            source=(root/row['relativePath']).resolve(strict=True)
            if not source.is_relative_to(root) or source.suffix.lower() not in FITS:raise ValueError('Невалиден IASC кадър.')
            matches=[s for s in sets if s['status']=='downloaded' and s['target'] and source.is_relative_to(Path(s['target']).resolve())]
            if len(matches)!=1:raise ValueError('Кадърът няма еднозначна връзка с изтеглен IASC комплект.')
            selected=matches[0];group=groups.setdefault(selected['id'],dict(setId=selected['id'],setName=selected['name'],campaign=selected['campaign_name'],team=selected['team_name'],setTarget=selected['target'],sources=[]))
            group['sources'].append(dict(id=row['id'],path=str(source),relative=source.relative_to(Path(selected['target']).resolve()).as_posix(),size=source.stat().st_size))
        self.pending=list(groups.values());self.choice=self.pending[0]['setId'];self.current='';self.p.publish()
        return dict(message=f'Избрани са {len(rows)} кадъра в {len(groups)} комплекта. Всеки комплект има отделна сесия и отчет.')
    def choose(self,p):
        value=p['values']['work-choice']
        if value not in {g['setId'] for g in self.pending}:raise ValueError('Избери подготвен комплект.')
        self.choice=value;self.current='';self.p.publish();return dict(selected=True)
    def report_baseline(self,folder):
        result={}
        for name in REPORT_NAMES:
            try:result[name]=read_report(Path(folder)/name)[1]
            except (OSError,ValueError):pass
        return result
    def remove_workspace(self,session):
        folder=Path(session['workspace']);ident=session['id']
        if not folder.exists():return True
        if folder.is_symlink() or folder.is_junction() or not folder.is_dir():raise ValueError('Работната папка не е обикновена сесийна папка.')
        base=Path(session.get('workBase') or folder.parent.parent)
        if folder.name!=slug(session['setName'])+'-'+ident[:8] or folder.parent.name!=slug(session['operator']) or folder.parent.resolve().parent!=base.resolve() or folder.resolve().parent!=folder.parent.resolve():
            raise ValueError('Работната папка не съвпада със сесията.')
        marker=folder/'.iasc-work.json'
        try:document=json.loads(marker.read_text(encoding='utf-8'))
        except (OSError,ValueError):raise ValueError('Липсва валиден маркер за работната папка.')
        if document.get('owner')!='iasc-campaigns' or document.get('id')!=ident:raise ValueError('Маркерът не принадлежи на тази сесия.')
        for parent,dirs,names in os.walk(folder,followlinks=False):
            for name in dirs+names:
                path=Path(parent)/name
                if path.is_symlink() or path.is_junction():raise ValueError('Работната папка съдържа връзка и е запазена за проверка.')
        shutil.rmtree(folder)
        return True
    def start(self,p):
        group=next((g for g in self.pending if g['setId']==self.choice),None)
        if not group:raise ValueError('Избери IASC комплект за работната сесия.')
        operator=self.p.options['workOperator'].strip()
        if not operator:raise ValueError('Въведи име на изпълнителя за работната папка.')
        root=Path(self.p.options['workRoot'])
        if not self.p.options['workRoot'] or not root.is_dir():raise ValueError('Избери работна папка.')
        if not p.get('values',{}).get('work-confirm'):raise ValueError('Потвърди създаването на работни копия.')
        def work(cancel,progress):
            session=dict(copy.deepcopy(group),id=uuid.uuid4().hex,operator=operator,state='copying',started=time.time(),reportFolder=self.p.options['workReports'],draft='',draftOrigin='',noReport=False,note='',problem='')
            base=root.resolve(strict=True);folder=base/slug(operator)/(slug(session['setName'])+'-'+session['id'][:8]);session['workBase']=str(base)
            if not folder.resolve().is_relative_to(base):raise ValueError('Невалидна работна папка.')
            folder.mkdir(parents=True,exist_ok=False);session['workspace']=str(folder);atomic(folder/'.iasc-work.json',dict(owner='iasc-campaigns',id=session['id'],setId=session['setId']));session['baseline']=self.report_baseline(session['reportFolder']) if session['reportFolder'] else {}
            session['copies']=[];self.sessions.append(session);self.current=session['id'];self.save()
            try:
                for i,source in enumerate(session['sources']):
                    check_cancel(cancel);original=Path(source['path']);target=folder/'images'/source['relative']
                    if not target.resolve().is_relative_to(folder) or original.is_symlink() or not original.resolve().is_relative_to(Path(session['setTarget']).resolve()):raise ValueError('Небезопасен път за работно копие.')
                    target.parent.mkdir(parents=True,exist_ok=True);before=original.stat();h=hashlib.sha256()
                    with original.open('rb') as input_,target.open('xb') as output:
                        while data:=input_.read(4*1024**2):check_cancel(cancel);output.write(data);h.update(data)
                        output.flush();os.fsync(output.fileno())
                    if original.stat().st_mtime_ns!=before.st_mtime_ns or original.stat().st_size!=before.st_size or digest_file(target,cancel)!=h.hexdigest():raise OSError('Кадърът е променен или копието не съвпада.')
                    session['copies'].append(dict(relative=target.relative_to(folder).as_posix(),sha256=h.hexdigest(),size=before.st_size));self.save();progress(i+1,len(session['sources']),'Проверени IASC работни копия')
                session['state']='waiting';self.save();return dict(session=session['id'],copies=len(session['copies']))
            except BaseException as error:
                if isinstance(error,InterruptedError):
                    try:self.remove_workspace(session);session.update(state='cancelled',problem='Отменено. Работната папка е почистена.')
                    except (OSError,ValueError) as cleanup_error:session.update(state='interrupted',problem='Отменено, но работната папка остана за проверка: '+str(cleanup_error)[:300])
                else:session.update(state='interrupted',problem=str(error)[:400])
                self.save();raise
        return self.p.task('IASC · работни копия за Astrometrica',work)
    def resume(self,p):
        ident=p.get('selected',{}).get('id')
        session=next((s for s in self.unfinished() if s['id']==ident),None)
        if session is None:raise ValueError('Сесията вече е приключена.')
        self.current=ident;self.p.publish();return dict(openContribution='astrometrica')
    def draft(self,p):
        session=self.selected()
        if not session or session['state']=='completed':raise ValueError('Няма отворена сесия.')
        values=p.get('values',{});text=values.get('work-report',session['draft'])
        if not isinstance(text,str) or len(text)>MAX_TEXT:raise ValueError('Отчетът е до 64 000 знака.')
        if text!=session['draft']:session['draftOrigin']='manual'
        session.update(draft=text,noReport=values.get('work-no-report',session.get('noReport',False)),note=values.get('work-note',session.get('note','')))
        self.save();return dict(saved=True)
    def import_report(self,p):
        session=self.selected()
        if not session or session['state']=='completed':raise ValueError('Избери сесия.')
        path=self.ctx.archives('choose-file',dict(kind='report',title='Отчет само за текущия IASC комплект')).get('path')
        if not path:return dict(cancelled=True)
        text,fingerprint=read_report(path)
        info=report_info(text)
        if not info['usable']:raise ValueError(info['reason'])
        self.p.options['workReportView']='source';self.p.persist()
        session.update(draft=text,draftOrigin='file',reportSource=str(path),reportHash=fingerprint,state='ready-report');self.save();self.p.publish();return dict(message='Отчетът е прочетен. Провери, че е за показания комплект.',**self.auto_mpc(session))
    def copy_report(self,p):
        session=self.selected()
        if not session:raise ValueError('Избери сесия.')
        if self.p.options['workReportView']=='iasc':
            if not self.fresh_mpc(session):raise ValueError('Създай актуален MPC отчет за IASC или избери оригиналния ADES изглед.')
            return dict(copyText=session['mpcExport']['text'],message='MPC отчетът за IASC е копиран. Прегледай правилния комплект и екип преди подаване.')
        return dict(copyText=session['draft'],message='Текстът на отчета е копиран.')
    def import_latest(self,p):
        session=self.selected()
        if not session or session['state']=='completed' or self.p.busy:raise ValueError('Избери незавършена сесия без активна задача.')
        if not session.get('reportFolder'):raise ValueError('Първо избери папката за отчетите на Astrometrica.')
        path=Path(session['reportFolder'])/'MPCReport.txt'
        if not path.is_file():raise ValueError('Не е намерен MPCReport.txt в '+str(path.parent)+'. Провери папката за отчетите на Astrometrica или избери файла ръчно.')
        text,fingerprint=read_report(path)
        info=report_info(text)
        if not info['usable']:raise ValueError('MPCReport.txt: '+info['reason'])
        session.update(draft=text,draftOrigin='file',reportSource=str(path),reportHash=fingerprint,state='ready-report')
        self.p.options['workReportView']='source';self.p.persist();self.save();self.p.publish()
        return dict(message='MPCReport.txt е прочетен директно, без преобразуване. Провери, че измерванията са за показания комплект; файлът може да съдържа предишни измервания.')
    def fresh_mpc(self,session):
        result=session.get('mpcExport',{})
        return bool(result.get('text') and result.get('exportVersion')==1 and result.get('sourceHash')==mpc_export.fingerprint(session['draft']))
    def report_view(self,p):
        value=p.get('values',{}).get('workReportView')
        if value not in ('source','iasc'):raise ValueError('Невалиден изглед на отчета.')
        self.p.options['workReportView']=value;self.p.persist();self.p.publish();return dict(saved=True)
    def auto_mpc(self,session):
        if self.p.options['workAutoMpc'] and report_info(session['draft'])['kind'] in ('ades-psv','ades-xml') and not self.fresh_mpc(session):return self._build_mpc(session)
        return {}
    def build_mpc(self,p):
        self.draft(p);session=self.selected()
        if not session:raise ValueError('Избери работна сесия.')
        return self._build_mpc(session)
    def _build_mpc(self,session):
        if self.p.busy or session['state']=='completed':raise ValueError('Изчакай текущата задача или избери незавършена сесия.')
        info=report_info(session['draft'])
        if not info['usable'] or info['kind'] not in ('ades-psv','ades-xml'):raise ValueError('Прочети реален ADES отчет с измервания.')
        source=session['draft'];signature=mpc_export.fingerprint(source)
        session['mpcAttemptHash']=signature;session['mpcProblem']='';self.save()
        def work(cancel,progress):
            try:
                progress(0,3,'IAU · преобразуване на ADES в MPC за IASC')
                result=mpc_export.convert(source,self.p.folder/'Conversion',cancel)
                progress(2,3,'Проверени брой, идентификатори, координати и време чрез обратно преобразуване.')
                with self.p.lock:
                    check_cancel(cancel)
                    if mpc_export.fingerprint(session['draft'])!=signature:raise ValueError('ADAS отчетът е променен по време на конвертирането. Създай MPC отчета отново.')
                    base=self.p.folder.resolve();folder=base/'MPC Exports'/session['id'];folder.mkdir(parents=True,exist_ok=True)
                    if not folder.resolve().is_relative_to(base) or folder.is_symlink() or folder.is_junction():raise ValueError('Небезопасна папка за отчета.')
                    target=folder/'MPCReport.txt';temporary=folder/(uuid.uuid4().hex+'.tmp')
                    if target.exists() and target.read_bytes()!=session.get('mpcExport',{}).get('text','').encode('utf-8'):
                        target=folder/('MPCReport-'+uuid.uuid4().hex[:10]+'.txt')
                    try:
                        with temporary.open('xb') as stream:stream.write(result['text'].encode('utf-8'));stream.flush();os.fsync(stream.fileno())
                        os.replace(temporary,target)
                    finally:temporary.unlink(missing_ok=True)
                    result['path']=str(target);session['mpcExport']=result;session['mpcProblem']='';self.save()
                    self.p.options['workReportView']='iasc';self.p.persist()
                progress(3,3,'MPC отчетът за IASC е създаден за преглед.');return dict(rows=result['verification']['rows'])
            except Exception as error:
                with self.p.lock:session['mpcProblem']=str(error)[:500];self.save()
                raise
        return self.p.task('IASC · създаване на MPC отчет',work,tier='normal')
    def copy_mpc_path(self,p):
        session=self.selected()
        if not session or not self.fresh_mpc(session):raise ValueError('Създай актуален MPC отчет.')
        return dict(copyText=session['mpcExport']['path'],message='Пътят до създадения MPCReport.txt е копиран.')
    def import_log(self,p):
        session=self.selected()
        if not session or session['state']=='completed':raise ValueError('Избери сесия.')
        path=self.ctx.archives('choose-file',dict(kind='report',title='Журнал само за текущата Astrometrica сесия')).get('path')
        if not path:return dict(cancelled=True)
        text,fingerprint=read_report(path);session.update(logText=text,logSource=str(path),logHash=fingerprint);self.save();self.p.publish()
        return dict(message='Журналът е приложен заедно с текущата сесия. Провери принадлежността му.')
    def copy_path(self,p):
        session=self.selected()
        if not session:raise ValueError('Избери сесия.')
        return dict(copyText=str(Path(session['workspace'])/'images'),message='Работната папка е копирана. В Astrometrica използвай Load Images (Ctrl+L).')
    def open_program(self,p):
        exe=Path(self.p.options['workProgram'])
        if not exe.is_file() or exe.suffix.lower()!='.exe':raise ValueError('Избери Astrometrica.exe от настройките.')
        subprocess.Popen([str(exe)],cwd=exe.parent);return dict(message='Astrometrica е стартирана. Зареждането и измерванията са ръчни.')
    def poll_reports(self):
        if not self.p.options['workAutoReport'] or self.p.busy:return False
        changed=False;touched=False;groups={}
        for session in self.unfinished():
            if session['state'] in ('waiting','ready-report') and session.get('reportFolder'):groups.setdefault(os.path.normcase(str(Path(session['reportFolder']).resolve())),[]).append(session)
        for sessions in groups.values():
            if len(sessions)!=1:continue
            session=sessions[0]
            if session['draftOrigin'] in ('manual','file'):continue
            for name in REPORT_NAMES:
                path=Path(session['reportFolder'])/name
                try:
                    text,fingerprint=read_report(path)
                    if not report_info(text)['usable'] or session['baseline'].get(name)==fingerprint or path.stat().st_mtime<session['started']:continue
                    candidate=name+':'+fingerprint
                    if session.get('candidate')!=candidate:session['candidate']=candidate;touched=True;break
                    if session.get('reportHash')==fingerprint:break
                    session.update(draft=text,draftOrigin='auto',reportSource=str(path),reportHash=fingerprint,state='ready-report');changed=True;break
                except (OSError,ValueError):continue
        if changed:
            self.p.options['workReportView']='source';self.p.persist()
        if changed or touched:self.save()
        if changed and self.p.options['workAutoMpc'] and not self.p.busy:
            candidate=next((s for sessions in groups.values() for s in sessions if s.get('draftOrigin')=='auto' and report_info(s.get('draft',''))['kind'] in ('ades-psv','ades-xml') and not self.fresh_mpc(s) and s.get('mpcAttemptHash')!=mpc_export.fingerprint(s['draft'])),None)
            if candidate:self.auto_mpc(candidate)
        return changed
    def finish(self,p):
        self.draft(p);session=self.selected();values=p.get('values',{})
        if not values.get('work-reviewed'):raise ValueError('Потвърди, че отчетът/резултатът е за показания комплект.')
        if not session['draft'].strip() and (not session.get('noReport') or not session.get('note','').strip()):raise ValueError('Постави отчет или отбележи „Без отчет“ и опиши причината.')
        if session['draft'].strip() and not report_info(session['draft'])['usable']:raise ValueError(report_info(session['draft'])['reason'])
        if self.p.options['workAutoMpc'] and report_info(session['draft'])['kind'] in ('ades-psv','ades-xml') and not self.fresh_mpc(session):raise ValueError('Първо създай актуалния MPC отчет за IASC или изключи автоматичния MPC износ, ако прибираш само ADES.')
        def work(cancel,progress):
            root=Path(self.p.options['root']).resolve(strict=True);target=Path(session['setTarget']).resolve(strict=True)
            if not target.is_relative_to(root) or json.loads((target/MARKER).read_text(encoding='utf-8')).get('id')!=session['setId']:raise ValueError('Архивният комплект е преместен или променен. Работната сесия е запазена.')
            result=target/'IASC Reports'/slug(session['operator'])/session['id']
            if not result.resolve().is_relative_to(target):raise ValueError('Папката за отчети сочи извън комплекта. Сесията е запазена.')
            for ancestor in result.parents:
                if ancestor==target:break
                if ancestor.is_symlink() or ancestor.is_junction():raise ValueError('Папката за отчети не може да използва външна връзка.')
            result.parent.mkdir(parents=True,exist_ok=True)
            if result.exists():
                saved=json.loads((result/'session.json').read_text(encoding='utf-8'))
                reports=list(result.glob('MPCReport.*'))
                if saved.get('id')!=session['id'] or (session['draft'].strip() and (len(reports)!=1 or reports[0].read_bytes()!=session['draft'].encode('utf-8'))):raise ValueError('Прибраният отчет е различен. Запазените файлове не са променени.')
                if self.fresh_mpc(session) and (not (result/'IASC-MPCReport.txt').is_file() or (result/'IASC-MPCReport.txt').read_bytes()!=session['mpcExport']['text'].encode('utf-8')):raise ValueError('Прибраният MPC износ е различен или липсва. Файловете са запазени за проверка.')
                session['archived']=str(result)
            session['state']='archiving';self.save()
            staging=result.parent/('.pending-'+session['id'])
            if staging.exists():
                try:owned=json.loads((staging/'.owner.json').read_text(encoding='utf-8')).get('id')==session['id']
                except (OSError,ValueError):owned=False
                if not owned:raise ValueError('Има непотвърдена временна папка от предишно прибиране. Запазена е за проверка: '+str(staging))
                staging.rename(staging.with_name('.preserved-'+session['id']+'-'+uuid.uuid4().hex[:8]))
            staging.mkdir(exist_ok=False);atomic(staging/'.owner.json',dict(id=session['id']))
            try:
                if session['draft'].strip():
                    suffix=report_suffix(session['draft'])
                    (staging/('MPCReport'+suffix)).write_bytes(session['draft'].encode('utf-8'))
                if self.fresh_mpc(session):(staging/'IASC-MPCReport.txt').write_bytes(session['mpcExport']['text'].encode('utf-8'))
                if session.get('logText'):(staging/'Astrometrica.log').write_bytes(session['logText'].encode('utf-8'))
                summary={k:session[k] for k in ('id','setId','setName','campaign','team','operator','started','sources','draftOrigin','noReport','note')}
                summary.update(completed=time.time(),submittedToIASC=False,sourceReport=session.get('reportSource',''),sourceLog=session.get('logSource',''))
                if self.fresh_mpc(session):summary['mpcExport']={k:v for k,v in session['mpcExport'].items() if k not in ('text','path')}
                atomic(staging/'session.json',summary);check_cancel(cancel)
                if result.exists():
                    shutil.rmtree(staging)
                else:staging.rename(result)
            except BaseException:
                if staging.is_dir() and staging.resolve().parent==result.parent.resolve():shutil.rmtree(staging)
                raise
            session.update(state='archiving',archived=str(result));self.save();progress(1,2,'Отчетът е запазен към точния IASC комплект.')
            preserved=[];folder=Path(session['workspace'])
            try:self.remove_workspace(session)
            except (OSError,ValueError) as cleanup_error:preserved.append('Работната папка остана за проверка: '+str(cleanup_error))
            session.update(state='completed',preserved=preserved,completed=time.time());self.save()
            if session['draft'].strip():self.p.store.update(session['setId'],local_report=1)
            progress(2,2,'Отчетът е прибран; работната папка е почистена.' if not preserved else 'Отчетът е прибран; работната папка остана за проверка.');return dict(archived=str(result),preserved=preserved)
        return dict(self.p.task('IASC · прибиране на проверения отчет',work),returnToPreparation=True)

    def retry_cleanup(self,p):
        session=self.selected()
        if not session or session['state']!='completed' or not session.get('preserved'):raise ValueError('Няма приключила сесия с работна папка за повторно почистване.')
        self.remove_workspace(session);session['preserved']=[];self.save();self.p.publish()
        return dict(message='Работната папка е почистена. Архивният отчет е запазен.')

    def abort(self,p):
        if self.p.busy:raise ValueError('Изчакай текущата IASC дейност.')
        session=self.selected()
        if not session or session['state'] in ('completed','cancelled'):raise ValueError('Няма активна IASC сесия за отказване.')
        self.remove_workspace(session);session.update(state='cancelled',problem='Отказано. Работната папка е почистена.',completed=time.time());self.save();self.p.publish()
        return dict(message='IASC сесията е отказана. Работната папка е изтрита; архивните оригинали са запазени.')

    def publish(self):
        f=lambda kind,ident,**kw:dict(type=kind,id=ident,**kw)
        option=lambda value,label:dict(value=value,label=label)
        settings=[f('input','workOperator',label='Име на изпълнителя',value=self.p.options['workOperator'],maxLength=120,command='work-options'),
            f('text','work-root-path',value='Работна папка: '+(self.p.options['workRoot'] or 'избери отделна папка.')),
            f('button','work-root',label='Избери работна папка',command='work-root'),f('button','work-program',label='Избери Astrometrica.exe',command='work-program'),
            f('text','work-program-path',value='Програма: '+(self.p.options['workProgram'] or 'не е избрана.')),
            f('text','work-reports-path',value='Отчети на Astrometrica: '+(self.p.options['workReports'] or 'избери OutDir от настройките на Astrometrica.')),
            f('button','work-reports',label='Папка за отчетите',command='work-reports'),f('checkbox','workAutoReport',label='Следи MPCReport.txt за нови измервания (без автоматично приключване)',value=self.p.options['workAutoReport'],command='work-options'),
            f('checkbox','workAutoMpc',label='Допълнително: преобразувай ръчно избран ADES в MPC',value=self.p.options['workAutoMpc'],command='work-options')]
        for item in settings:
            if item.get('command'):item['disabled']=self.p.busy
            if item['type']=='text' or item['id'] in ('workOperator','workAutoReport','workAutoMpc'):item['span']=2
        document=[f('text','work-intro',span=2,value='IASC → работни копия → ръчна Astrometrica → преглед на MPC/ADES → прибиране към точния комплект.'),f('group','work-settings',label='Работна папка и изпълнител',children=settings)]
        session=self.selected()
        if not session:
            if self.pending:
                document.extend([f('select','work-choice',label='Комплект за тази сесия',value=self.choice,options=[option(g['setId'],g['setName']+' · '+g['team']) for g in self.pending],command='work-choice'),
                    f('checkbox','work-confirm',label='Потвърждавам създаването на проверени работни копия',value=False),
                    f('button','work-start',label='Подготви избрания комплект',command='work-start',tone='primary',disabled=self.p.busy)])
            else:document.append(f('text','work-empty',span=2,value='Избери IASC кадри от началния екран или продължи запазена сесия отдолу.'))
        else:
            info=report_info(session['draft'])
            mpc_ready=self.fresh_mpc(session);view=self.p.options['workReportView'];export=session.get('mpcExport',{})
            report_status=((f"MPC за IASC · Редове: {export['verification']['rows']} · проверено обратно преобразуване. Оригиналният ADES е запазен." if mpc_ready else session.get('mpcProblem') or 'Още няма актуален MPC отчет за IASC. Прочети ADES и натисни „Създай MPC за IASC“.') if view=='iasc' else (info['label']+' · '+Path(session['reportSource']).name+' · ' if session.get('reportSource') else '')+info['reason'])
            same_folder=[s for s in self.unfinished() if s['state'] in ('waiting','ready-report') and s.get('reportFolder') and session.get('reportFolder') and os.path.normcase(str(Path(s['reportFolder']).resolve()))==os.path.normcase(str(Path(session['reportFolder']).resolve()))]
            watch_info=(f'{len(same_folder)} незавършени сесии използват тази папка. Автоматичното прочитане изчаква. Избери точната сесия и натисни „Прочети MPCReport.txt“.' if len(same_folder)>1 else 'Автоматично се следи само MPCReport.txt, без преобразуване. Ръчно прочетен или редактиран текст се запазва; обнови го с бутона при нови измервания.')
            state_text={'copying':'Копиране…','waiting':'Чака ръчна обработка в Astrometrica','ready-report':'Има отчет за твоя преглед' if info['usable'] else 'Още няма отчет с измервания — прочети MPCReport.txt','interrupted':'Незавършена сесия — провери работните копия','completed':'Приключена сесия'}.get(session['state'],session['state'])
            document.extend([f('text','work-session',span=2,value=session['setName']+' · '+session['team']+' · '+session['operator']),f('text','work-state',span=2,value=state_text),
                f('text','work-workspace',span=2,value=('Работна папка: почистена' if session['state'] in ('completed','cancelled') and not session.get('preserved') else 'Работна папка: '+session['workspace'])),f('button','work-path',label='Копирай папката за Load Images',command='work-path',disabled=session['state'] in ('completed','cancelled')),f('button','work-open',label='Отвори Astrometrica',command='work-open',disabled=session['state'] in ('completed','cancelled')),
                f('text','work-manual',span=2,value='В Astrometrica: Load Images (Ctrl+L), избери работните кадри, извърши измерванията и прегледай отчета. Reset Files се прави ръчно едва след запазването му. Общият отчет може да съдържа предишни измервания — провери комплекта.'),
                f('select','workReportView',span=2,label='Изглед на отчета',value=view,options=[option('source','Оригинален отчет · от файл'),option('iasc','Предишен износ · ADES → MPC')],command='work-report-view'),
                f('text','work-report-status',span=2,tone='success' if view=='iasc' and mpc_ready else 'normal' if view=='source' and info['usable'] else 'warning',value=report_status),
                f('text','work-report-watch',span=2,tone='warning' if len(same_folder)>1 else 'normal',value=watch_info),
                f('textarea','work-report',label='Оригинален отчет',span=2,value=session['draft'],maxLength=MAX_TEXT,command='work-draft',disabled=self.p.busy or session['state']=='completed',visible=view=='source'),
                f('console','work-mpc-preview',label='MPC отчет за преглед',span=2,value=export['text'] if mpc_ready else 'Създай актуален MPC отчет от ADES измерванията. При промяна на източника старият износ не се използва.',visible=view=='iasc'),
                f('text','work-mpc-file',span=2,value='Създаден файл: '+export.get('path',''),visible=view=='iasc' and mpc_ready),
                f('button','work-mpc-build',label='Създай MPC за IASC',command='work-mpc-build',tone='primary',disabled=self.p.busy or session['state']=='completed',visible=info['kind'] in ('ades-psv','ades-xml') and info['usable']),
                f('button','work-mpc-path',label='Копирай пътя до файла',command='work-mpc-path',visible=view=='iasc' and mpc_ready),
                f('button','work-report-latest',label='Прочети MPCReport.txt',command='work-report-latest',disabled=self.p.busy or session['state']=='completed'),
                f('button','work-report-file',label='Прочети отчет от файл…',command='work-report-file',disabled=self.p.busy or session['state']=='completed'),f('button','work-copy',label='Копирай MPC за IASC' if view=='iasc' else 'Копирай оригиналния отчет',command='work-copy',disabled=self.p.busy or (view=='iasc' and not mpc_ready)),
                f('button','work-log-file',label='Прикачи журнал от Astrometrica…',command='work-log-file',disabled=self.p.busy or session['state']=='completed'),f('text','work-log-info',span=2,value='Приложен журнал: '+session.get('logSource','няма')),
                f('checkbox','work-no-report',label='Приключване без отчет (опиши причината)',value=session.get('noReport',False),command='work-draft'),f('input','work-note',label='Бележка / причина',value=session.get('note',''),maxLength=2000,command='work-draft'),
                f('checkbox','work-reviewed',label='Проверих принадлежността на отчета / резултата към този комплект',value=False),
                f('button','work-finish',label='Прибери отчета и приключи сесията',command='work-finish',tone='primary',disabled=self.p.busy or session['state'] not in ('waiting','ready-report','interrupted','archiving') or (bool(session['draft'].strip()) and not info['usable'])),
                f('button','work-abort',label='Откажи сесията и почисти работната папка',command='work-abort',disabled=self.p.busy or session['state'] in ('completed','cancelled')),
                f('button','work-cleanup-retry',label='Повтори почистването на работната папка',command='work-cleanup-retry',disabled=self.p.busy or session['state']!='completed' or not session.get('preserved')),
                f('text','work-cleanup',span=2,value=('Папката остана за проверка: '+'; '.join(session.get('preserved',[]))) if session.get('preserved') else 'Приключването първо записва отчета и журнала към точния IASC комплект, после премахва цялата отделна работна папка на сесията. Архивните оригинали и файловете извън тази папка остават. Не изпраща отчет към IASC/MPC.')])
        report_ids={'workReportView','work-mpc-preview','work-mpc-file','work-mpc-build','work-mpc-path','work-report-status','work-report-watch','work-report','work-report-latest','work-report-file','work-copy','work-log-file','work-log-info','work-no-report','work-note','work-reviewed','work-finish','work-abort','work-cleanup-retry','work-cleanup'}
        report=[e for e in document if e['id'] in report_ids]
        center=[e for e in document if e['id'] not in report_ids and e['id']!='work-settings']
        center=([f('text','work-servers-title',span=2,value='Достъпност на сървърите за Astrometrica'),*self.servers.document()] if self.servers.visible else
            [f('button','work-servers-show',label='Сървъри · VizieR / MPCOrb',command='work-servers-show',span=2),*center])
        self.ctx.publish('astrometrica',[f('group','work-settings',label='Папки и изпълнител',children=settings),f('group','work-files',label='Комплект и ръчна работа',children=center),f('group','work-result',label='MPC отчет',children=report or [f('text','work-result-help',value='След подготвянето тук ще се появят директно прочитане на MPCReport.txt, преглед и потвърждение.')])])
        active=self.unfinished()
        self.ctx.publish('work-sessions',[] if not active else [f('list','work-sessions-list',label='Незавършени IASC сесии',items=[dict(id=s['id'],label=s['setName']+' · '+s['operator'],description=s['team'],status='Копиране' if s['state']=='copying' and self.p.busy else 'Отчет за преглед' if s['state']=='ready-report' else 'Незавършена / чака ръчна работа',tone='warning' if s['state']=='interrupted' else 'normal') for s in active],command='work-resume',actionLabel='Продължи')])
