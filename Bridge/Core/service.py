"""Named UI commands -> Python services. No dependency on a C# host."""
from __future__ import annotations
from Backend.Foundation.branding import marker as archive_marker
import copy, csv, io, json, os, shutil, threading, time, uuid
from pathlib import Path
from Backend.Foundation.runtime import ROOT,CACHE,Store,Settings,Events,Jobs,read_json,atomic_json,within
from Backend.Archive.catalog import Catalog
from Backend.Archive.editing import ArchiveEditor
from Backend.Archive.structure import schema,validate,FIELDS
from Backend.Analysis.quality import Quality
from Backend.Analysis.timeseries import TimeSeries
from Backend.Imaging.viewer import Viewer
from Backend.Preparation.workflow import Workflow
from Backend.Preparation.exchange import Exchange
from Bridge.Core.protocol import Router
from Bridge.Core.commands import CORE_COMMANDS, CORE_ROUTES
from Bridge.Core.appearance import themes as appearance_themes, validate as validate_appearance, save as save_appearance, FIELDS as APPEARANCE_FIELDS
from Bridge.Core.session import SharedSession
from Bridge.Core.viewer_state import ViewerState
from Bridge.Plugins.manager import PluginManager
from Bridge.Plugins.host_services import HostServices

class Service:
    def __init__(self,store=None,plugin_folder=None,desktop_actions=None):
        self.store=store or Store();self.settings=Settings(self.store);self.events=Events();self.jobs=Jobs(self.events)
        self.catalog=Catalog(self.store,self.settings,self.events);self.quality=Quality(self.catalog,self.jobs);self.timeseries=TimeSeries(self.quality)
        self.archive_editor=ArchiveEditor(self.catalog)
        self.viewer_state=ViewerState(self.store);self.viewer_epoch=0;self.viewer_loading_epoch=None;self.viewer_guard=threading.RLock()
        self.viewer=Viewer(self.catalog,self.events,self.viewer_state.display);self.workflow=Workflow(self.catalog,self.jobs);self.exchange=Exchange(self)
        self.bridge=Router();self.plugins=PluginManager(self,plugin_folder);self.host_services=HostServices(self);self.desktop_actions=desktop_actions or {};self._dialogs=None
        self.index_started=set();self.index_tasks={};self.index_guard=threading.RLock();self.audit=read_json(self.store.folder/'archive-check.json',None)
        from Backend.Foundation.resources import Sampler
        self.sampler=Sampler()
        self.session=SharedSession(self)
        self._register_bridge();self.plugins.scan()
    def _register_bridge(self):
        self._core_handlers = {
            command: getattr(self, handler)
            for handler, commands in CORE_ROUTES.items()
            for command in commands
        }
        external={**{name:'archive.read' for name in ('python.archive.query','python.archive.facets','python.archive.info','archive.summary')},
                  'python.analysis.result':'analysis.read','python.preparation.state':'preparation.read',
                  'bridge.capabilities':'capabilities.invoke','bridge.capability.call':'capabilities.invoke'}
        overrides={
            'ui.session.get':lambda p:self.session.snapshot(p),
            'ui.session.publish':lambda p:self.session.publish(p),
            'ui.session.resource':lambda p:self.session.resource(p),
            'ui.session.export':lambda p:self.session.export(p),
            'ui.session.sources':lambda p:self.session.sources(p),
            'ui.appearance.get':lambda p:self.configuration(),
            'ui.appearance.save':lambda p:save_appearance(self,p),
            'archive.filtered':lambda p:self.catalog.filtered(p.get('filters',{}),p.get('offset',0),p.get('search','')),
            'archive.structure.sample':lambda p:self.archive_editor.sample(p['archiveId']),
            'archive.structure.preview':lambda p:self.structure_preview(p),
            'archive.metadata.describe':lambda p:self.archive_editor.describe(p['archiveId'],p.get('relativePath','')),
            'archive.metadata.preview':lambda p:self.archive_editor.preview(p['archiveId'],p.get('relativePath',''),p.get('values'),p.get('restructure',False)),
            'archive.metadata.commit':lambda p:self.commit_archive_edit(p),
            'archive.source.nights':lambda p:self.source_nights(p['id']),
            'archive.provenance':lambda p:self.provenance(p['id']),
            'bridge.describe':lambda p:self.bridge.describe(),
            'bridge.invoke':lambda p:self.bridge.invoke(p['command'],p.get('parameters',{})),
            'bridge.capabilities':lambda p:dict(capabilities=self.plugins.capabilities()),
            'bridge.capability.call':lambda p:self.plugins.invoke_capability(p['id'],p.get('parameters',{}),p.get('pluginId')),
            'ui.plugins.scan':lambda p:self.scan_plugins(),
            'ui.plugins.toggle':lambda p:self.toggle_plugin(p),
            'ui.plugins.permission':lambda p:self.permission_plugin(p),
            'ui.plugins.install':lambda p:self.install_plugin(),
            'ui.plugins.remove':lambda p:self.remove_plugin(p),
            'ui.plugins.archive':lambda p:self.plugin_archive(p),
            'plugin.call':lambda p:self.plugins.invoke(p['pluginId'],p.get('command',p.get('method')),p.get('parameters',p.get('params',p.get('payload',{})))),
            'plugin.input':lambda p:self.plugins.input(p['pluginId'],p['contributionId'],p['element'],p.get('values',{}),p.get('row'),p.get('context')),
            'plugin.ui.input':lambda p:self.plugins.input(p['pluginId'],p['contributionId'],p['element'],p.get('values',{}),p.get('row'),p.get('context'),p.get('itemId')),
            'plugin.ui.activate':lambda p:self.plugins.activate(p['pluginId'],p['contributionId'],p.get('context',{})),
            'plugin.ui.state':lambda p:dict(plugins=self.plugins.list()),
            'python.preparation.program.open':lambda p:self.open_program(p['id']),
        }
        for name in ('python.analysis.run','python.analysis.coverage','python.analysis.reference','python.analysis.timeseries'):
            overrides[name]=lambda p,name=name:self.session.track(name,p,lambda:self._core_call(name,p))
        for name in ('function.image','image.open','viewer.render','viewer.neighbor','viewer.close'):
            overrides[name]=lambda p,name=name:self.shared_viewer(name,p)
        for name in ('python.preparation.start','python.preparation.export','python.preparation.finish'):
            overrides[name]=lambda p,name=name:self.session.preparation_task(name,p,lambda:self._core_call(name,p))
        for name in ('python.preparation.complete','python.preparation.cancel'):
            overrides[name]=lambda p,name=name:self.shared_preparation_done(name,p)
        overrides['python.archive.decide']=self.shared_decision
        for name in sorted(set(CORE_COMMANDS)|set(overrides)):
            callback=overrides.get(name) or (lambda p,name=name:self._core_call(name,p))
            self.bridge.register(name,callback,section=name.split('.')[1] if name.startswith('python.') else name.split('.')[0],permission=external.get(name))
    def call(self,method,p=None):
        self.jobs.enter_call()
        try:return self.bridge.invoke(method,p or {})
        finally:self.jobs.leave_call()
    def shared_decision(self,p):
        result=self._core_call('python.archive.decide',p);self.session.decisions(p.get('ids'));return result
    def shared_preparation_done(self,method,p):
        result=self._core_call(method,p);self.session.preparation_done(p['id']);return result
    def shared_viewer(self,method,p):
        p=dict(p);snapshot=p.pop('viewState',None)
        with self.viewer_guard:
            current=self.session.channels.get('viewer',{}).get('value',{}).get('document',{})
            if snapshot is not None:
                if not isinstance(snapshot,dict):raise ValueError('Невалиден запазен преглед.')
                self.viewer_state.accept(snapshot,current)
            if method in ('function.image','image.open','viewer.neighbor') or method=='viewer.close' and (not current or current.get('id')==p.get('id') or self.viewer_loading_epoch is not None):
                self.viewer_epoch+=1
            epoch=self.viewer_epoch
            if method in ('function.image','image.open','viewer.neighbor'):self.viewer_loading_epoch=epoch
            elif method=='viewer.close':self.viewer_loading_epoch=None
            if method=='viewer.render':
                if self.viewer_loading_epoch==epoch:raise ValueError('Зарежда се следващият кадър.')
                if current and current.get('id')!=p.get('id'):raise ValueError('Прегледът вече е сменен.')
                patch={k:p[k] for k in ('options','edge','debayer','pattern') if k in p}
                self.viewer_state.update(dict(display=patch),can_debayer=bool(current.get('canDebayer')))
                display=self.viewer_state.display();p.update(display)
                if not current.get('canDebayer'):p.update(debayer=False,pattern='auto')
        try:result=self._core_call(method,p)
        except BaseException:
            with self.viewer_guard:
                if self.viewer_loading_epoch==epoch:self.viewer_loading_epoch=None
            raise
        with self.viewer_guard:
            if epoch!=self.viewer_epoch:return result
            if self.viewer_loading_epoch==epoch:self.viewer_loading_epoch=None
            if method=='viewer.render' and self.session.channels.get('viewer',{}).get('value',{}).get('document',{}).get('id') not in (None,p.get('id')):return result
            if method=='viewer.close':
                with self.session.lock:
                    if self.session.channels.get('viewer',{}).get('value',{}).get('document',{}).get('id')==p['id']:self.session.viewer(closed=True)
            else:
                document=result.get('viewerDocument') or (result if method=='viewer.render' else None)
                if document:self.session.viewer(document)
        return result
    def dispatch(self,request):
        self.jobs.enter_call()
        try:return self.bridge.dispatch(request)
        finally:self.jobs.leave_call()
    def invoke_capability(self,ident,parameters=None,plugin=None):return self.plugins.invoke_capability(ident,parameters or {},plugin)
    def open_program(self,ident):
        program=next((p for p in self.settings.value['programs'] if p['id']==ident),None)
        if not program:raise ValueError('Избери запазена програма от настройките.')
        executable=Path(program['executable'])
        if not executable.is_file() or executable.suffix.lower()!='.exe':raise ValueError('Не е намерен изпълнимият файл на програмата.')
        import subprocess
        subprocess.Popen([str(executable)],cwd=executable.parent)
        return dict(opened=True)
    def scan_plugins(self):self.plugins.scan();return self.configuration()
    def toggle_plugin(self,p):
        if type(p.get('enabled')) is not bool:raise ValueError('Невалидно състояние на плъгин.')
        if p['enabled'] and p.get('revision')!=self.plugins.records[p['id']]['uiRevision']:raise ValueError('Прегледай текущата версия на плъгина.')
        self.plugins.toggle(p['id'],p['enabled'],p.get('confirmed',False));return self.configuration()
    def permission_plugin(self,p):self.plugins.allow_ui(p['id'],p['revision'],p['allowed']);return self.configuration()
    def remove_plugin(self,p):self.plugins.remove(p['id'],p.get('confirmed',False));return self.configuration()
    def install_plugin(self):
        folder=self.choose_folder('Избери папка на плъгин с plugin.json');self.plugins.install(folder);return self.configuration()
    def plugin_archive(self,p):
        self.plugins.active(p['id'])
        if not self.plugins.state(p['id']).get('uiAllowed'):raise PermissionError('Първо разреши интерфейса на добавката.')
        contribution=next((c for c in self.plugins.records[p['id']]['contributions'] if c['id']==p['contributionId'] and c['slot']=='archive' and c.get('archiveRoot')),None)
        if not contribution:raise ValueError('Добавката не е заявила отделен архив.')
        ident='plugin-'+p['id']+'-'+contribution['id'];folder=str(self.choose_folder('Избери папка за '+contribution['title']))
        archives=copy.deepcopy(self.settings.value['archives']);entry=next((a for a in archives if a['id']==ident),None)
        if entry:entry['path']=folder
        else:archives.append(dict(id=ident,name=contribution['title'],path=folder,ownerPlugin=p['id']))
        self.save_settings(dict(archives=archives));self.events.emit('plugins.changed');return self.configuration()
    @property
    def dialogs(self):
        if self._dialogs is None:
            from Bridge.Desktop.dialogs import Dialogs
            self._dialogs=Dialogs()
        return self._dialogs
    def configuration(self):
        preferences=copy.deepcopy(self.settings.value['preferences'])
        return dict(preferences=preferences,themes=appearance_themes(),
            languages=[dict(id='bg',locale='bg',name='Български',messages={}),dict(id='en',locale='en',name='English',messages={})],
            plugins=self.plugins.list(),problems=list(self.plugins.problems),backgroundDataUrl=self.settings.value.get('backgroundDataUrl'),pluginsFolder=str(self.plugins.folder),icons=self.settings.value.get('icons',[]))
    def bootstrap(self):
        return dict(protocol=1,archives=[dict(a,structure=schema(self.settings,a['id'])) for a in self.settings.value['archives'] if not a.get('hidden') and (not a.get('ownerPlugin') or a['ownerPlugin'] in self.plugins.live)],ui=self.configuration(),activity=self.jobs.snapshot(),problem=None)
    def commit_archive_edit(self,p):
        def work(cancel,progress):
            plan=self.archive_editor.plans.get(p['token'])
            if not plan:raise ValueError('Направи нов предварителен преглед.')
            archive=plan['archive'];result=self.archive_editor.commit(p['token'],cancel,progress,p.get('confirmed',False))
            result['index']=self.catalog.scan(archive,cancel,progress,wait=True);return result
        return self.task('archive','Записване на информацията и подреждане на архива',work,'important')
    def structure_preview(self,p):
        from Backend.Archive.structure import render
        sample=self.archive_editor.sample(p['archiveId']);layout=validate(p['structure'])
        return dict(names=[render(x['formula'],sample['sample'],sample['sampleName']) for x in layout['levels']]+[render(layout['fileFormula'],sample['sample'],sample['sampleName'])])
    def provenance(self,ident):
        seen=set()
        def visit(ident,depth=0):
            if ident in seen or depth>6:return dict(id=ident,reference=True)
            seen.add(ident)
            if len(seen)>500:return dict(id=ident,truncated=True)
            try:row=self.catalog.record(ident)
            except FileNotFoundError:return dict(id=ident,missing=True,name='Вече не е в активния архив')
            m=row['meta'];parents=m.get('sourceStacks') or m.get('sourceFiles',[])
            return dict(id=ident,name=Path(row['relative']).name,archiveId=row['archive'],relativePath=row['relative'],kind=m.get('kind'),combination=m.get('combination'),nights=m.get('sourceNights') or [m.get('night')],frames=m.get('frames'),exposure=m.get('totalExposure'),sources=[visit(i,depth+1) for i in parents])
        return visit(ident)
    def source_nights(self,ident):
        source=self.catalog.record(ident)['meta'];nights=set(source.get('sourceNights') or [source.get('night')]);ids=set(source.get('sourceFiles',[]));result={}
        for row in self.catalog.browse.get('frames')['rows']:
            m=row['meta']
            if row['id'] in ids or m.get('night') in nights and m.get('object')==source.get('object') and m.get('telescope') in (source.get('telescopes') or [source.get('telescope')]):
                relative=str(Path(row['relative']).parent).replace('\\','/');result[relative]=dict(archiveId='frames',relativePath=relative,name=relative)
        return list(result.values())
    def task(self,section,title,work,tier='normal'):return self.jobs.submit(section,title,work,tier)
    def wait_task(self,started):
        while True:
            with self.jobs.lock:task=dict(self.jobs.tasks[started['taskId']])
            if task['state']=='completed':return task['result']
            if task['state']=='blocked':raise ValueError(task['problem'])
            if task['state']=='cancelled':raise InterruptedError('Задачата е спряна.')
            time.sleep(.1)
    def ensure_index(self,archive):
        key=(archive,self.settings.archive(archive)['path'])
        if key in self.index_started:return
        self.index_started.add(key)
        if not self.catalog.index_status(archive)['building']:self.start_index(archive)
    def start_index(self,archive,verify=False):
        with self.index_guard:
            existing=self.index_tasks.get(archive)
            if existing and not existing.get('done'):
                if verify:existing['verify']=True
                return dict(taskId=existing['taskId'])
            control=dict(verify=bool(verify),done=False);self.index_tasks[archive]=control
            def work(cancel,progress):
                try:
                    while True:
                        full=control['verify'];result=self.catalog.scan(archive,cancel,progress,verify=full,wait=True)
                        with self.index_guard:
                            if not full and control['verify'] and result['read']<result['files']:continue
                            control['done']=True;return result
                finally:
                    with self.index_guard:control['done']=True
            started=self.task('archive','Проверка и допълване на архивната информация',work,'ambient');control['taskId']=started['taskId'];return started
    def choose_folder(self,title):
        path=self.dialogs.choose('folder',title=title)
        if not path:raise InterruptedError('Изборът е отменен.')
        return Path(path)
    def save_islands(self,value):
        """Patch only layout preferences; never change archive paths or indices."""
        import math,re
        if not isinstance(value,dict) or value.get('version')!=1 or not isinstance(value.get('sections'),dict):raise ValueError('Невалидна подредба.')
        if set(value['sections'])-{'archive','analysis','preparation'}:raise ValueError('Непознат основен изглед.')
        layout=dict(version=1,sections={})
        for key,section in value['sections'].items():
            if not isinstance(section,dict) or not isinstance(section.get('order'),list) or not isinstance(section.get('panels'),dict) or len(section['order'])>32:raise ValueError('Невалидни панели.')
            order=section['order']
            if any(not isinstance(i,str) or not re.fullmatch(r'[a-z0-9_-]{1,64}',i) for i in order) or len(order)!=len(set(order)):raise ValueError('Невалидни имена на панели.')
            panels={}
            for ident in order:
                p=section['panels'].get(ident,{})
                if not isinstance(p,dict):raise ValueError('Невалиден размер.')
                span=p.get('span',24);height=p.get('height')
                if isinstance(span,bool) or not isinstance(span,(int,float)) or not math.isfinite(span):raise ValueError('Невалидна ширина.')
                panels[ident]=dict(span=max(3,min(24,round(span))))
                if height is not None:
                    if isinstance(height,bool) or not isinstance(height,(int,float)) or not math.isfinite(height):raise ValueError('Невалидна височина.')
                    panels[ident]['height']=max(140,min(2400,round(height)))
            if order:layout['sections'][key]=dict(order=order,panels=panels)
        with self.settings.lock:
            self.settings.value['preferences']['islands']=layout;self.settings.save()
        return dict(layout=layout)
    def set_ui_preferences(self,preferences):
        with self.settings.lock:
            next_value=copy.deepcopy(preferences)
            next_value.update(validate_appearance({k:v for k,v in next_value.items() if k in APPEARANCE_FIELDS}))
            next_value.setdefault('islands',copy.deepcopy(self.settings.value['preferences'].get('islands',dict(version=1,sections={}))))
            self.settings.value['preferences']=next_value
    def _core_call(self, method, p):
        handler = self._core_handlers.get(method)
        if handler is not None:
            return handler(method, p)
        if method.startswith(('ui.plugins.','plugin.open')):raise ValueError('Старите плъгини не се зареждат в Python версията. Подготвен е само новият вход/изход.')
        raise ValueError('Неподдържана команда в новата версия: '+method)

    def _application_command(self, method, p):
        if method=='app.bootstrap':
            for a in self.settings.value['archives']:
                if a.get('path') and not a.get('hidden'):self.ensure_index(a['id'])
            return self.bootstrap()
        if method in ('plugin.archives',):return dict(archives=self.bootstrap()['archives'])
        if method=='ui.plugins.folder':os.startfile(self.plugins.folder);return dict(opened=True)
        if method=='resources.read':return self.resources()
        if method=='debug.logs.folder':os.startfile(self.store.folder);return dict(opened=True)
        raise ValueError('Неподдържана команда: '+method)

    def _settings_command(self, method, p):
        if method=='ui.layout.save':return self.save_islands(p.get('layout'))
        if method in ('ui.load','ui.cancelPreview','ui.plugins.scan'):return self.configuration()
        if method=='ui.save':
            if 'archives' in p:self.save_settings(p)
            else:self.set_ui_preferences(p['preferences']);self.settings.save()
            self.events.emit('ui.appearance.changed')
            return dict(self.configuration(),archives=copy.deepcopy(self.settings.value['archives']))
        if method=='ui.background.choose':
            from PIL import Image
            import base64
            path=self.dialogs.choose('file',title='Избери фон',filetypes=[('Изображения','*.png *.jpg *.jpeg')])
            if not path:return None
            with Image.open(path) as image:
                image=image.convert('RGB');image.thumbnail((3840,2160));output=io.BytesIO();image.save(output,'JPEG',quality=88)
            url='data:image/jpeg;base64,'+base64.b64encode(output.getvalue()).decode();self.settings.value['backgroundDataUrl']=url;self.settings.save()
            return dict(fileName=Path(path).name,dataUrl=url)
        if method=='python.settings.get':return self.settings.value
        if method=='python.settings.choose':
            kind=p['kind'];value=str(self.choose_folder('Избери папка')) if kind!='executable' else self.dialogs.choose('file',title='Избери програма',filetypes=[('Програма','*.exe')])
            return dict(path=value)
        if method=='python.settings.save':return self.save_settings(p)
        if method=='python.settings.import':return self.import_settings()
        if method in ('ui.storage','debug.open'):return dict(openSettings=True,archives=self.bootstrap()['archives'])
        if method=='customization.catalog':return dict(archives=self.bootstrap()['archives'],telescopes=sorted({r['meta']['telescope'] for a in self.settings.value['archives'] for r in self.catalog.browse.get(a['id'])['rows']}),problem=None)
        if method=='customization.folder.choose':return dict(path=str(self.choose_folder('Избери архив')))
        if method=='customization.locations.save':self.save_settings(dict(archives=p['archives']));return dict(archives=self.bootstrap()['archives'])
        if method=='customization.formula.preview':
            from Backend.Archive.formulas import display
            return dict(text=display(p['formula'],'M16_0001.fit',dict(headers={'OBJECT':'M16','EXPTIME':10,'NCOMBINE':30,'DATE-OBS':'2026-10-22T20:15:00'},telescope='S50',frames=30,totalExposure=300)))
        if method=='ui.icon.choose':return self.choose_icon()
        raise ValueError('Неподдържана команда: '+method)

    def _archive_command(self, method, p):
        if method=='archive.list':
            self.catalog.root(p['archiveId']);self.ensure_index(p['archiveId'])
            return self.catalog.listing(p['archiveId'],p.get('relativePath',''),int(p.get('offset',0)),p.get('search',''))
        if method=='archive.navigation':
            entries=self.catalog.listing_menu(p['archiveId'],p.get('relativePath',''))
            return dict(relativePath=p.get('relativePath',''),totalFolders=len(entries),offset=0,entries=entries,problem=None)
        if method=='archive.rows':
            result=[]
            for item in p.get('requests',p.get('rows',[])):
                try:
                    path=self.catalog.resolve(item['archiveId'],item['relativePath'])
                    entry=self.catalog.entry(item['archiveId'],item['relativePath'].replace('\\','/'),path)
                    result.append(dict(**item,**{k:entry[k] for k in ('bytes','modified','info','details')}))
                except (OSError,ValueError) as error:result.append(dict(**item,bytes=None,modified=None,info={},details=dict(kind='folder',frames=None,totalExposure=None,problem=str(error),building=False,stale=True)))
            return result
        if method=='archive.summary':return self.catalog.summary(p['archiveId'],p.get('relativePath',''))
        if method in ('archive.summary.refresh','python.archive.scan'):
            archive=p.get('archiveId','frames')
            started=self.start_index(archive,verify=True)
            if method=='archive.summary.refresh':self.wait_task(started);return self.catalog.summary(archive,p.get('relativePath',''))
            return started
        if method=='archive.audit':
            def audit(c,r):
                errors=[];files=0
                for a in self.settings.value['archives']:
                    if not a.get('path') or a.get('hidden'):continue
                    checked=self.catalog.scan(a['id'],c,r,verify=True);files+=checked['files'];errors.extend(checked['errors'])
                import datetime as dt
                rows=self.catalog.query();result=dict(fits=sum(Path(row['relative']).suffix.lower() in ('.fit','.fits','.fts') for row in rows),photos=sum(Path(row['relative']).suffix.lower() not in ('.fit','.fits','.fts') for row in rows),files=files,issues=len(errors),examples=[dict(path=e['path'],reason=e['problem']) for e in errors],problems=[],savedUtc=dt.datetime.now(dt.timezone.utc).isoformat(),reportPath=str(self.store.folder/'archive-check.json'))
                atomic_json(self.store.folder/'archive-check.json',result);self.audit=result;return result
            return self.wait_task(self.task('archive','Внимателна проверка на целия архив',audit,'important'))
        if method=='archive.audit.status':return self.audit
        if method=='archive.audit.open':os.startfile(self.store.folder/'archive-check.json');return dict(opened=True)
        if method in ('archive.summary.stop','archive.read.stop'):return dict(requested=True)
        if method=='archive.import':
            if p.get('folder'):
                from Backend.Fits.images import IMAGES
                folder=self.choose_folder('Внеси папка с изображения (включително подпапките)')
                def import_folder(cancel,progress):
                    paths=[]
                    for parent,dirs,files in os.walk(folder,followlinks=False):
                        if cancel.is_set():raise InterruptedError()
                        dirs[:]=[d for d in dirs if not d.startswith('.') and not (Path(parent)/d).is_symlink() and not getattr(Path(parent)/d,'is_junction',lambda:False)()]
                        paths.extend(str(within(folder,(Path(parent)/n).relative_to(folder).as_posix())) for n in files if Path(n).suffix.lower() in IMAGES and not (Path(parent)/n).is_symlink() and not n.endswith('.coverage.fits'))
                        progress(len(paths),max(1,len(paths)),'Откриване на файловете за внасяне')
                    return self.catalog.import_files(paths,cancel,progress,forced_archive=p.get('archiveId'))
                return self.task('archive','Внасяне на папка в архива',import_folder,'important')
            paths=self.dialogs.choose('files',title='Внеси кадри',filetypes=[('Астрономически изображения','*.fit *.fits *.fts *.png *.jpg *.jpeg *.tif *.tiff')])
            if not paths:return dict(cancelled=True)
            return self.task('archive','Внасяне в архива',lambda c,r:self.catalog.import_files(list(paths),c,r,forced_archive=p.get('archiveId')),'important')
        if method=='python.archive.query':
            rows=self.catalog.query(p.get('filters',{}),slim=True);return dict(rows=[self.row(r) for r in rows],statistics=self.catalog.totals(rows))
        if method=='python.archive.facets':
            rows=self.catalog.browse.get(p.get('filters',{}).get('archive','frames'))['rows']
            return {key:sorted({r['meta'].get(key,'') for r in rows if r['meta'].get(key)}) for key in ('telescope','object','night')}
        if method=='python.archive.rename':return self.catalog.rename(p['archiveId'],p['relativePath'],p['name'])
        if method=='python.archive.info':return self.catalog.record(p['id'])
        if method=='python.archive.decide':
            result=self.catalog.decide(p['ids'],p['decision'])
            selected_ids=set(p['ids'])
            for report in self.quality.results.values():
                for frame in report['frames']:
                    if frame['id'] in selected_ids:frame['decision']=p['decision']
            return result
        if method=='python.archive.trash':return self.task('archive','Преместване в кошчето',lambda c,r:self.catalog.trash(p['ids'],p.get('confirmed',False)),'important')
        if method in ('analysis.trash.list','python.trash.list'):return self.catalog.trash_list()
        if method in ('analysis.trash.restore','python.trash.restore'):return self.catalog.restore(p['id'])
        if method=='python.trash.empty':return self.catalog.empty_trash(p.get('confirmed',False))
        if method=='python.trash.preview':
            import zipfile
            entry=next((t for t in self.catalog.trash_list() if t['id']==p['id']),None)
            if not entry:raise ValueError('Непознат кадър в кошчето.')
            from Backend.Foundation.runtime import digest
            folder=self.store.folder/'TrashPreview';folder.mkdir(exist_ok=True);target=folder/(entry['id']+Path(entry['originalName']).suffix)
            if not target.exists():
                with target.open('xb') as output,zipfile.ZipFile(within(self.store.folder/'Trash',entry['zip'])) as archive,archive.open(archive.namelist()[0]) as source:shutil.copyfileobj(source,output,4*1024*1024)
            if digest(target)!=entry['hash']:target.unlink();raise ValueError('Проблем при разархивирането на прегледа.')
            try:return self.viewer.open(target,can_navigate=False,display_name=entry['originalName'])
            finally:target.unlink(missing_ok=True)
        if method=='archive.pending':
            folder=self.store.folder/'Recovery';items=[p for p in folder.iterdir() if p.suffix!='.json'] if folder.exists() else []
            return dict(count=len(items),bytes=sum(p.stat().st_size for p in items),folder=str(folder),message='Записът не е завършен. Запазени са възстановителни копия в папката на приложението.' if items else None)
        if method=='archive.pending.open':
            folder=self.store.folder/'Recovery';folder.mkdir(exist_ok=True);os.startfile(folder);return dict(opened=True)
        if method=='archive.disk':return self.disk(p.get('archiveId','frames'))
        raise ValueError('Неподдържана команда: '+method)

    def _analysis_command(self, method, p):
        if method=='python.analysis.thumbnail':return self.viewer.thumbnail(p['id'])
        if method=='python.analysis.reference':return self.task('analysis','Изображение за избор на звезди',lambda c,r:self.timeseries.reference(p['resultId'],p['ids'],c,r),'normal')
        if method=='python.analysis.timeseries':return self.task('analysis','Звезди във времето',lambda c,r:self.timeseries.run(p['referenceId'],p['starIds'],c,r,p.get('radius',5)),'normal')
        if method=='python.analysis.qualityConfig':
            import math
            defaults=dict(threshold=20,weights=dict(fwhm=1,fwhmY=1,roundness=1,stars=1,background=1))
            if set(p)-{'config'}:raise ValueError('Непозната настройка за качество.')
            if 'config' in p:
                config=p['config']
                if not isinstance(config,dict) or set(config)!={'threshold','weights'}:raise ValueError('Невалидна настройка за качество.')
                threshold=config['threshold'];weights=config['weights']
                if type(threshold) not in (int,float) or not math.isfinite(threshold) or not 0<=threshold<=100:raise ValueError('Прагът трябва да е от 0 до 100%.')
                if not isinstance(weights,dict) or set(weights)!=set(defaults['weights']) or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=10 for v in weights.values()) or not any(weights.values()):raise ValueError('Задай тежести от 0 до 10, като поне една е различна от нула.')
                with self.settings.lock:self.settings.value['analysisQuality']=copy.deepcopy(config);self.settings.save()
                self.events.emit('analysis.quality.changed')
            return dict(config=copy.deepcopy(self.settings.value.get('analysisQuality',defaults)))
        if method=='python.analysis.presets':
            if 'presets' in p:
                presets=p['presets']
                if not isinstance(presets,list) or len(presets)>30 or any(not isinstance(v,dict) or set(v)-{'id','name','x','y','columns'} or not all(isinstance(v.get(k),str) and 0<len(v[k])<=100 for k in ('id','name','x','y')) or not isinstance(v.get('columns'),list) or len(v['columns'])>40 or any(not isinstance(k,str) or len(k)>60 for k in v['columns']) for v in presets):raise ValueError('Невалидни пресети на диаграмата.')
                with self.settings.lock:self.settings.value['analysisPresets']=copy.deepcopy(presets);self.settings.save()
            return dict(presets=self.settings.value.get('analysisPresets',[]))
        if method=='python.analysis.history':return self.quality.history()
        if method=='python.analysis.save':return self.quality.save(p['id'],p['name'])
        if method=='python.analysis.delete':return self.quality.delete(p['id'])
        if method=='python.analysis.run':return self.task('analysis','Отчет за качество',lambda c,r:self.quality.run(p['ids'],c,r,p.get('force',False),p.get('kind','frames'),p.get('detector')),'normal')
        if method=='python.analysis.coverage':return self.task('analysis','Маска на покритието',lambda c,r:self.quality.coverage(p['resultId'],c,r,p.get('ids')),'normal')
        if method=='python.analysis.coverage.attach':return self.quality.attach_coverage(p['resultId'],p['stackId'])
        if method=='python.analysis.export':return self.export_report(p)
        if method=='python.analysis.result':return self.quality.load(p['id'],bool(p.get('compact')))
        raise ValueError('Неподдържана команда: '+method)

    def _viewer_command(self, method, p):
        if method in ('function.image','image.open'):
            path=self.catalog.path(p['id']) if p.get('id') else self.catalog.resolve(p['archiveId'],p['relativePath'])
            return self.viewer.open(path,p.get('id'))
        if method=='viewer.render':return self.viewer.render(p['id'],p.get('options',{}),p.get('edge',1600),p.get('debayer'),p.get('pattern'))
        if method=='viewer.pixel':return self.viewer.pixel(p['id'],p['x'],p['y'])
        if method=='viewer.header':return self.viewer.header(p['id'])
        if method=='viewer.close':return self.viewer.close(p['id'])
        if method=='viewer.neighbor':return self.viewer.neighbor(p['id'],p['delta'])
        if method=='viewer.presented':return dict(accepted=True)
        raise ValueError('Неподдържана команда: '+method)

    def _preparation_command(self, method, p):
        if method in ('python.artifact.create','python.artifact.preview'):
            report=(self.timeseries.results if p.get('source')=='timeseries' else self.quality.results)[p['resultId']]
            action=self.workflow.artifacts.preview if method.endswith('preview') else self.workflow.artifacts.report
            return action(report,p.get('format','csv'),p.get('ids'),p.get('png'))
        if method=='python.artifact.list':return self.workflow.artifacts.list()
        if method=='python.preparation.state':return dict(jobs=self.workflow.list(),programs=self.settings.value['programs'])
        if method=='python.preparation.enqueue':return self.workflow.enqueue(p['ids'],p['options'])
        if method=='python.preparation.reorder':return self.workflow.reorder(p['id'],p['delta'])
        if method=='python.preparation.start':return self.task('preparation','Подготовка на работната папка',lambda c,r:self.workflow.start_queued(p['id'],c,r),'important')
        if method=='python.preparation.export':
            folder=p.get('folder')
            if not folder:
                folder=self.dialogs.choose('folder',title='Избери папка за копията')
                if not folder:return dict(cancelled=True)
            return self.task('preparation','Изнасяне на копия',lambda c,r:self.workflow.export(p['ids'],folder,c,r,p.get('format','original')),'important')
        if method=='python.preparation.outputs':return self.workflow.outputs(p['id'])
        if method=='python.preparation.finish':return self.task('preparation','Прибиране на резултати',lambda c,r:self.workflow.finish(p['id'],p['outputs'],c,r,p.get('confirmed',False)),'important')
        if method=='python.preparation.complete':return self.workflow.complete(p['id'],p.get('confirmed',False))
        if method=='python.preparation.cancel':return self.workflow.cancel(p['id'],p.get('clean',False),p.get('confirmed',False))
        if method=='python.preparation.folder':
            job=self.workflow.get(p['id']);os.startfile(job['workspace']);return dict(opened=True)
        raise ValueError('Неподдържана команда: '+method)

    def _activity_command(self, method, p):
        if method=='python.task':
            with self.jobs.lock:
                task=self.jobs.tasks.get(p['id'])
                if not task:raise ValueError('Непозната задача.')
                return {k:v for k,v in task.items() if k!='cancel'}
        if method in ('activity.stop','python.task.cancel'):return self.jobs.cancel(p.get('taskId',p.get('id')))
        if method=='activity.state':return self.jobs.snapshot()
        if method=='activity.dismiss':return self.jobs.dismiss()
        raise ValueError('Неподдържана команда: '+method)

    def row(self,r):
        return {**{k:v for k,v in r['meta'].items() if k not in ('headers','sourceFiles')},'id':r['id'],'archiveId':r['archive'],'relativePath':r['relative'],'name':Path(r['relative']).name,'added':r['added'],'size':r['size'],'decision':r.get('decision')}
    def save_settings(self,p):
        archives=copy.deepcopy(p.get('archives',self.settings.value['archives']));programs=copy.deepcopy(p.get('programs',self.settings.value['programs']))
        for archive in archives:
            old=next((a for a in self.settings.value['archives'] if a.get('id')==archive.get('id')),None)
            if old and old.get('ownerPlugin') and old.get('path')!=archive.get('path'):
                runtime=self.plugins.live.get(old['ownerPlugin'],{})
                if any(self.jobs.tasks.get(ident,{}).get('state')=='running' for ident in runtime.get('tasks',())):
                    raise ValueError('Изчакай активната задача на плъгина преди смяна на архивната папка.')
        ids=set();roots=[];markers=[]
        for a in archives:
            if not isinstance(a.get('id'),str) or a['id'] in ids:raise ValueError('Повторен или невалиден архив.')
            ids.add(a['id'])
            a['type']=a.get('type',a['id'] if a['id'] in ('frames','stacks','photos') else 'frames')
            if a['type'] not in ('frames','stacks','photos'):raise ValueError('Избери готов тип на архива.')
            a['structure']=validate(a.get('structure'),a['type'])
            if a.get('path'):
                root=Path(a['path']).resolve()
                if not root.is_dir():raise ValueError('Папката на архива не съществува: '+str(root))
                if root.is_relative_to(ROOT) or ROOT.is_relative_to(root) or root==Path(root.anchor):raise ValueError('Избери архивна папка извън програмата.')
                if any(root.is_relative_to(r) or r.is_relative_to(root) for r in roots):raise ValueError('Архивите трябва да са в отделни папки, които не са вложени една в друга.')
                roots.append(root)
                marker=read_json(archive_marker(root,'archive'),{})
                a['identity']=marker.get('id') or a.get('identity') or uuid.uuid4().hex
                markers.append((archive_marker(root,'archive'),dict(id=a['identity'],version=1)))
                a['path']=str(root)
        if not {'frames','stacks','photos'}.issubset(ids):raise ValueError('Трите основни архива трябва да останат в настройките.')
        ids=set()
        for program in programs:
            if not program.get('id'):program['id']=uuid.uuid4().hex
            if program['id'] in ids:raise ValueError('Повторен идентификатор на програма.')
            ids.add(program['id'])
            exe=Path(program.get('executable',''))
            if not exe.is_file() or exe.suffix.lower()!='.exe':raise ValueError('Липсва изпълним .exe файл за '+program.get('name','програмата'))
            self.workflow.validate_folder(program['workingFolder'],archives=archives,create=False)
        aliases=p.get('aliases',self.settings.value['aliases'])
        if not isinstance(aliases,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in aliases.items()):raise ValueError('Имената на телескопите трябва да са текстови двойки.')
        workers=max(1,min(os.cpu_count() or 1,int(p.get('workers',self.settings.value['workers']))));memory=max(128,min(65536,int(p.get('memoryMb',self.settings.value['memoryMb']))))
        for path,value in markers:atomic_json(path,value)
        self.settings.value.update(archives=archives,programs=programs,aliases=p.get('aliases',self.settings.value['aliases']),
            workers=workers,memoryMb=memory)
        if 'preferences' in p:self.set_ui_preferences(p['preferences'])
        self.settings.save();self.catalog.browse.cache.clear();self.events.emit('archive.changed')
        for a in archives:
            if a.get('path') and not a.get('hidden'):self.start_index(a['id'])
        return self.settings.value
    def import_settings(self):
        path=self.dialogs.choose('file',title='Избери settings.json от старата C# програма · Library',filetypes=[('Настройки','*.json')])
        if not path:return dict(cancelled=True)
        old=read_json(Path(path),{})
        for a,key in zip(self.settings.value['archives'][:3],['FramesArchiveFolder','StacksArchiveFolder','PhotosArchiveFolder']):a['path']=old.get(key,'')
        self.settings.value['aliases']=old.get('TelescopeAliases',{})
        for p in old.get('ProcessingPrograms',[]):
            self.settings.value['programs'].append(dict(id=uuid.uuid4().hex,name=p.get('Name','Програма'),executable=p.get('ExecutablePath',p.get('Executable','')),workingFolder=p.get('WorkingFolder','')))
        ui=read_json(Path(path).parent/'Ui'/'settings.json',{})
        if ui.get('preferences'):self.set_ui_preferences(ui['preferences'])
        self.settings.save();return self.settings.value
    def export_report(self,p):
        result=self.quality.results[p['resultId']];fmt=p.get('format','csv')
        target=self.dialogs.choose('save',title='Запази отчет',defaultextension='.'+fmt,filetypes=[('Отчет','*.'+fmt)])
        if not target:return dict(cancelled=True)
        if fmt=='json':atomic_json(Path(target),result)
        elif fmt=='csv':
            keys=['id','name','decision','fwhm','fwhmY','hfr','roundness','eccentricity','stars','background','noise','peakSnr','shiftX','shiftY','rotation','scale','registrationRms','recommended','statusText']
            with Path(target).open('w',newline='',encoding='utf-8-sig') as stream:
                writer=csv.DictWriter(stream,fieldnames=keys,extrasaction='ignore');writer.writeheader();writer.writerows(result['frames'])
        else:raise ValueError('Поддържани отчети: CSV и JSON.')
        return dict(path=target)
    def choose_icon(self):
        import base64
        from PIL import Image
        path=self.dialogs.choose('file',title='Добави икона',filetypes=[('Икони','*.png *.jpg *.jpeg *.ico *.bmp')])
        if not path:return None
        if Path(path).stat().st_size>5*1024**2:raise ValueError('Иконата трябва да е до 5 MB.')
        with Image.open(path) as image:
            image=image.convert('RGBA');image.thumbnail((128,128));stream=io.BytesIO();image.save(stream,'PNG')
        result=dict(fileName=uuid.uuid4().hex+'.png',dataUrl='data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode(),name=Path(path).name)
        self.settings.value.setdefault('icons',[]).append(result);self.settings.save();return result
    def disk(self,archive):
        try:
            root=self.catalog.root(archive);usage=shutil.disk_usage(root)
            return dict(name=root.anchor,volume=root.anchor,totalBytes=usage.total,freeBytes=usage.free,archiveBytes=self.catalog.browse.group(archive)['bytes'],reading=False,skipped=False,problem=None)
        except (ValueError,OSError):return None
    def resources(self):
        return self.sampler.read()
