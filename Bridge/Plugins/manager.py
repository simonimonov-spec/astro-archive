"""Folder-based Python additions, declarative UI and optional core capabilities."""
from __future__ import annotations
import base64, collections, contextvars, copy, hashlib, importlib.util, io, json, os, re, shutil, sys, threading, types, uuid
from pathlib import Path
from Backend.Foundation.runtime import ROOT,atomic_json,read_json,within
from Bridge.Core.protocol import Caller,data_only
from Bridge.Plugins.contracts import PluginGateway,Request,SLOTS,ui_document,ui_values

IDENTIFIER=re.compile(r'^[a-z][a-z0-9_-]{0,63}$')
PERMISSIONS={'analysis.inspect','archive.manage','archive.read','analysis.read','preparation.read','capabilities.invoke','processing.manage','remote.host'}
CALL_CHAIN=contextvars.ContextVar('astroarchive_plugin_chain',default=())
PERMISSION_LABELS={'analysis.inspect':'Четене на избрани кадри и измервания за инспекция и публикуване на предупреждения без промяна на подбора','archive.manage':'Добавяне и обновяване на допълнителни архивни папки чрез архивния SDK; мрежовото споделяне е в плъгина','remote.host':'Локален уеб сървър с удостоверени сесии и ограничени дистанционни команди','archive.read':'Четене на индексите и статистиката на архивите','analysis.read':'Четене на създадени отчети за анализ','preparation.read':'Четене на състоянието на опашката','capabilities.invoke':'Използване на разрешени възможности на други плъгини','processing.manage':'Работни копия, избрани външни програми и потвърдено прибиране на резултати'}

class PluginContext:
    """The documented SDK: commands, UI data, settings, tasks and host calls."""
    def __init__(self,manager,ident,generation):self.manager=manager;self.id=ident;self.generation=generation
    def _check(self):return self.manager.active(self.id,self.generation)
    def command(self,name,handler):
        self._check();name=self.manager.command_name(self.id,name)
        if not callable(handler):raise ValueError('Командата трябва да е функция.')
        def guarded(parameters):self._check();return handler(parameters)
        self.manager.gateway.register(self.id,name,guarded)
    def publish(self,contribution,elements):self._check();return self.manager.publish(self.id,contribution,elements)
    def settings(self):self._check();return read_json(self.manager.settings_path(self.id),{})
    def save_settings(self,values):
        self._check();data_only(values,100_000)
        if not isinstance(values,dict):raise ValueError('Настройките трябва да са обект.')
        atomic_json(self.manager.settings_path(self.id),values);return copy.deepcopy(values)
    def host(self,command,parameters=None):
        record=self._check()
        return copy.deepcopy(data_only(self.manager.service.bridge.invoke(command,parameters or {},Caller('plugin',self.id,frozenset(record['permissions']))),8_000_000))
    def capability(self,ident,parameters=None):
        record=self._check()
        if 'capabilities.invoke' not in record['permissions']:raise PermissionError('Липсва разрешение за други плъгин възможности.')
        return self.manager.invoke_capability(ident,parameters or {})
    def storage(self):
        self._check();return self.manager.settings_path(self.id).parent
    def processing(self,action,parameters=None,cancel=None,progress=None):
        record=self._check()
        if 'processing.manage' not in record['permissions']:raise PermissionError('Липсва разрешение за външна обработка.')
        return self.manager.service.exchange.invoke(self.id,action,parameters or {},cancel,progress)
    def remote(self,action,parameters=None):
        record=self._check()
        if 'remote.host' not in record['permissions']:raise PermissionError('Липсва разрешение за дистанционен хост.')
        return self.manager.service.host_services.invoke(self.id,action,parameters or {})
    def archives(self,action,parameters=None):
        record=self._check()
        if 'archive.manage' not in record['permissions']:raise PermissionError('Липсва разрешение за архивния SDK.')
        return self.manager.archive_services.invoke(self.id,action,parameters or {})
    def inspection(self,action,parameters=None,cancel=None,progress=None):
        record=self._check()
        if 'analysis.inspect' not in record['permissions']:raise PermissionError('Липсва разрешение за инспекционния SDK.')
        return self.manager.inspection_services.invoke(self.id,action,parameters or {},cancel,progress)
    def task(self,title,work,section='analysis',tier='important'):
        record=self._check()
        if section not in ('archive','analysis','preparation'):raise ValueError('Невалиден раздел за задача.')
        def guarded(cancel,progress):self._check();return work(cancel,progress)
        result=self.manager.service.task(section,str(title)[:160],guarded,tier);record['tasks'].add(result['taskId']);return result

class PluginManager:
    def __init__(self,service,folder=None):
        self.service=service;self.settings=service.settings;self.events=service.events
        self.folder=Path(folder or ROOT/'Plugins').resolve()
        from Bridge.Plugins.archive_services import ArchiveServices
        self.archive_services=ArchiveServices(service)
        from Bridge.Plugins.inspection_services import InspectionServices
        self.inspection_services=InspectionServices(service)
        self.gateway=PluginGateway(self.settings,self.events);self.records={};self.live={};self.problems=[];self.lock=threading.RLock()
    @staticmethod
    def command_name(plugin,name):
        if not isinstance(name,str):raise ValueError('Невалидна команда на плъгин.')
        value=name if name.startswith(plugin+'.') else plugin+'.'+name
        if not re.fullmatch(r'[a-z][a-z0-9_-]*\.[a-z][a-z0-9_.-]{0,119}',value):raise ValueError('Невалидна команда на плъгин.')
        return value
    def state(self,ident):return self.settings.value.setdefault('pluginState',{}).setdefault(ident,dict(enabled=False,uiAllowed=False))
    def settings_path(self,ident):
        if not IDENTIFIER.fullmatch(ident):raise ValueError('Невалиден плъгин.')
        folder=self.service.store.folder/'Plugins'/ident;folder.mkdir(parents=True,exist_ok=True);return folder/'settings.json'
    def manifest(self,folder):
        path=within(folder,'plugin.json')
        if path.stat().st_size>100_000:raise ValueError('Прекалено голям плъгин манифест.')
        m=read_json(path)
        if not isinstance(m,dict) or m.get('apiVersion')!=1 or not IDENTIFIER.fullmatch(m.get('id','')):raise ValueError('Невалиден плъгин манифест / API версия.')
        for field in ('name','description','version'):
            if not isinstance(m.get(field),str) or len(m[field])>2000:raise ValueError('Липсва име, описание или версия.')
        entry=within(folder,m.get('entry','main.py'))
        if entry.suffix!='.py' or not entry.is_file():raise ValueError('Липсва Python входният файл на плъгина.')
        permissions=m.get('permissions',[])
        if not isinstance(permissions,list) or any(p not in PERMISSIONS for p in permissions):raise ValueError('Неподдържано разрешение на плъгин.')
        contributions=m.get('contributions',[])
        if not isinstance(contributions,list) or len(contributions)>12:raise ValueError('Максимум 12 визуални допълнения.')
        ids=set()
        for c in contributions:
            if not isinstance(c,dict) or not IDENTIFIER.fullmatch(c.get('id','')) or c['id'] in ids or c.get('slot') not in SLOTS or not isinstance(c.get('title'),str):raise ValueError('Невалидно визуално допълнение.')
            if 'clients' in c and (not isinstance(c['clients'],list) or not c['clients'] or any(x not in ('desktop','remote') for x in c['clients'])):raise ValueError('Невалиден вид клиент за UI допълнението.')
            if 'archiveKey' in c and (c['slot']!='customization' or not isinstance(c['archiveKey'],str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,39}',c['archiveKey'])):raise ValueError('Невалидна връзка към настройки на плъгин архив.')
            if 'order' in c and (type(c['order']) is not int or not -1000<=c['order']<=1000):raise ValueError('Невалиден ред на визуалното допълнение.')
            ids.add(c['id'])
        capabilities=m.get('capabilities',[])
        if not isinstance(capabilities,list) or len(capabilities)>24:raise ValueError('Максимум 24 плъгин възможности.')
        for c in capabilities:
            if not isinstance(c,dict) or not re.fullmatch(r'[a-z][a-z0-9_.-]{0,119}',c.get('id','')) or not isinstance(c.get('command'),str):raise ValueError('Невалидна плъгин възможност.')
        # No source is imported while scanning. A changed source needs fresh consent.
        digest=hashlib.sha256();total=0
        for parent,dirs,names in os.walk(folder,followlinks=False):
            dirs[:]=sorted(d for d in dirs if not d.startswith('.') and d not in ('__pycache__','node_modules'))
            for name in sorted(names):
                relative=(Path(parent)/name).relative_to(folder).as_posix();file=within(folder,relative)
                if not file.is_file():continue
                size=file.stat().st_size;total+=size
                if size>16_000_000 or total>64_000_000:raise ValueError('Плъгинът надвишава допустимия размер за тази API версия.')
                digest.update(relative.encode());digest.update(file.read_bytes())
        m=copy.deepcopy(m);m.update(folder=str(folder),entry=str(entry),uiRevision=digest.hexdigest(),permissions=list(dict.fromkeys(permissions)),contributions=contributions,capabilities=capabilities)
        return m
    def scan(self):
        with self.lock:
            found={};self.problems=[]
            for folder in sorted(self.folder.iterdir()) if self.folder.exists() else []:
                if not folder.is_dir() or folder.name.startswith('.') or folder.name=='__pycache__':continue
                try:
                    folder=within(self.folder,folder.name)
                    m=self.manifest(folder)
                    if folder.name!=m['id']:raise ValueError('Името на папката трябва да съвпада с id на плъгина.')
                    found[m['id']]=m
                except Exception as error:self.problems.append(folder.name+': '+str(error))
            for ident in list(self.live):
                if ident not in found or found[ident]['uiRevision']!=self.live[ident]['revision']:self.unload(ident)
            self.records=found
            for ident,m in found.items():
                state=self.state(ident)
                if state.get('revision')!=m['uiRevision']:
                    if state.get('enabled'):m['problem']='Кодът е променен. Прегледай и включи плъгина отново.'
                    state.update(enabled=False,uiAllowed=False);self.settings.value['approvedPluginPermissions'][ident]=False
                elif state.get('enabled') and ident not in self.live:
                    try:self.load(ident)
                    except Exception as error:m['problem']=str(error);state['enabled']=False
            self.prune_folder()
            self.settings.save();self.events.emit('plugins.changed');return self.list()
    def list(self):
        result=[]
        with self.lock:
            for ident,m in self.records.items():
                state=self.state(ident);runtime=self.live.get(ident);contributions=[]
                for declared in m.get('contributions',[]):
                    contributions.append(dict(declared,elements=copy.deepcopy(runtime['documents'].get(declared['id'],[])) if runtime else []))
                result.append(dict(id=ident,name=m['name'],description=m['description'],version=m['version'],author=m.get('author',''),folder=m['folder'],
                    kind='declarative',available=True,enabled=bool(runtime),busy=False,uiAllowed=bool(runtime and state.get('uiAllowed')),
                    uiRevision=m['uiRevision'],contributions=contributions,permissions=[dict(id=p,label=PERMISSION_LABELS[p]) for p in m['permissions']],
                    capabilities=m.get('capabilities',[]),problem=m.get('problem'),logoDataUrl=self.logo(m)))
        return result
    def logo(self,m):
        if not m.get('logo'):return None
        try:
            from PIL import Image
            file=within(Path(m['folder']),m['logo'])
            if file.stat().st_size>5_000_000:return None
            with Image.open(file) as image:
                image=image.convert('RGBA');image.thumbnail((128,128));buffer=io.BytesIO();image.save(buffer,'PNG')
            return 'data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode()
        except (OSError,ValueError):return None
    def active(self,ident,generation=None):
        with self.lock:
            if ident not in self.records:
                raise ValueError(f'Плъгинът „{ident}“ не е инсталиран. Изтегли го отделно и го добави от Настройки → Плъгини → Инсталирай.')
            live=self.live.get(ident)
            if not live or not self.state(ident).get('enabled'):
                raise PermissionError(f'Плъгинът „{ident}“ е изключен. Включи го от Настройки → Плъгини.')
            if generation and live['generation']!=generation:raise PermissionError('Плъгинът е презареден. Отвори функцията отново.')
            return live
    def load(self,ident):
        m=self.records[ident];state=self.state(ident);generation=uuid.uuid4().hex;package='_astroarchive_plugin_'+generation
        self.settings.value['approvedPluginPermissions'][ident]=True
        runtime=dict(generation=generation,revision=m['uiRevision'],package=package,documents={},assets=collections.OrderedDict(),assetBytes=0,permissions=set(m['permissions']),tasks=set(),stop=None,command_lock=threading.RLock())
        self.live[ident]=runtime;state['enabled']=True
        try:
            namespace=types.ModuleType(package);namespace.__path__=[m['folder']];sys.modules[package]=namespace
            spec=importlib.util.spec_from_file_location(package+'.entry',m['entry']);module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
            if not callable(getattr(module,'setup',None)):raise ValueError('Плъгинът трябва да има setup(context).')
            runtime['stop']=module.setup(PluginContext(self,ident,generation))
            for capability in m.get('capabilities',[]):
                if self.command_name(ident,capability['command']) not in self.gateway.commands:raise ValueError('Възможността няма регистрирана команда: '+capability['id'])
            state['revision']=m['uiRevision'];m.pop('problem',None)
        except BaseException:self.unload(ident);state['enabled']=False;raise
    def unload(self,ident):
        live=self.live.pop(ident,None)
        self.gateway.commands={k:v for k,v in self.gateway.commands.items() if v[0]!=ident};self.gateway.contributions.pop(ident,None)
        self.settings.value['approvedPluginPermissions'][ident]=False
        if live:
            for task in live['tasks']:self.service.jobs.cancel(task)
            try:
                if callable(live['stop']):live['stop']()
            except Exception as error:self.problems.append(ident+': '+str(error))
            for name in list(sys.modules):
                if name==live['package'] or name.startswith(live['package']+'.'):sys.modules.pop(name,None)
        self.service.host_services.owners.discard(ident)
        self.events.emit('plugin.ui.removed',plugin=ident)
    def toggle(self,ident,enabled,confirmed=False):
        with self.lock:
            if ident not in self.records:raise ValueError('Плъгинът не е намерен. Обнови списъка.')
            if enabled and not confirmed:raise PermissionError('Потвърди изпълнението на плъгина и описаните разрешения.')
            if enabled:
                current=self.manifest(Path(self.records[ident]['folder']))
                if current['uiRevision']!=self.records[ident]['uiRevision']:raise ValueError('Кодът е променен след прегледа. Обнови списъка.')
                if ident not in self.live:self.load(ident)
            else:self.unload(ident);self.state(ident).update(enabled=False,uiAllowed=False)
            self.settings.save();self.events.emit('plugins.changed');return self.list()
    def allow_ui(self,ident,revision,allowed):
        with self.lock:
            if type(allowed) is not bool:raise ValueError('Невалидно разрешение за интерфейса.')
            self.active(ident)
            if self.records[ident]['uiRevision']!=revision:raise ValueError('Допълнението е променено. Прегледай го отново.')
            self.state(ident)['uiAllowed']=bool(allowed);self.settings.save();self.events.emit('plugins.changed');return self.list()
    def publish(self,ident,contribution,elements):
        live=self.active(ident)
        if not any(c['id']==contribution for c in self.records[ident]['contributions']):raise ValueError('Полето не е декларирано в манифеста.')
        elements=ui_document(elements)
        # Large image bytes stay on the host; the UI receives opaque references.
        # Images are fetched only when the corresponding image control is visible.
        pending={}
        def resources(items):
            for e in items:
                if e['type']=='image':
                    if 'value' in e:
                        data=base64.b64decode(e.pop('value').split(',',1)[1],validate=True);key=hashlib.sha256(data).hexdigest()
                        pending[key]=data;e['assetId']=key
                    elif e['assetId'] not in live['assets'] and e['assetId'] not in pending:raise ValueError('Изображението не принадлежи на този плъгин.')
                resources(e.get('children',[]))
        with self.lock:
            resources(elements)
            def refs(items):return {e['assetId'] for e in items if e['type']=='image'}|{key for e in items for key in refs(e.get('children',[]))}
            required=refs(elements)|{key for cid,items in live['documents'].items() if cid!=contribution for key in refs(items)}
            if len(required)>32 or sum(len(pending.get(k,live['assets'].get(k,b''))) for k in required)>32*1024**2:raise ValueError('Твърде много активни изображения на плъгина.')
            for key,data in pending.items():
                if key not in live['assets']:live['assets'][key]=data;live['assetBytes']+=len(data)
                live['assets'].move_to_end(key)
            while len(live['assets'])>32 or live['assetBytes']>32*1024**2:
                oldest=next(k for k in live['assets'] if k not in required);live['assetBytes']-=len(live['assets'].pop(oldest))
            live['documents'][contribution]=elements
        if self.state(ident).get('uiAllowed'):self.events.emit('plugin.ui.changed',plugin=ident,contribution=contribution,elements=elements,revision=live['revision'])
        return dict(accepted=True)
    def image_asset(self,ident,key):
        if not isinstance(ident,str) or not IDENTIFIER.fullmatch(ident) or not isinstance(key,str) or not re.fullmatch('[a-f0-9]{64}',key):raise ValueError('Невалиден ресурс на плъгин.')
        live=self.active(ident)
        if not self.state(ident).get('uiAllowed'):raise PermissionError('Няма разрешение за UI на плъгина.')
        with self.lock:data=live['assets'].get(key)
        if data is None:raise FileNotFoundError('Прегледът вече не е наличен.')
        return data
    def invoke(self,ident,command,parameters=None,wait=False):
        runtime=self.active(ident);chain=CALL_CHAIN.get()
        if ident in chain or len(chain)>=8:raise ValueError('Прекъснат цикъл от плъгин команди.')
        # Shared desktop/remote viewer activation and short capability polls can
        # arrive together. Wait briefly before entering, never replay a handler.
        if not runtime['command_lock'].acquire(timeout=.5 if wait else 0):raise ValueError('Плъгинът изпълнява друга команда. Опитай отново след приключването ѝ.')
        token=CALL_CHAIN.set((*chain,ident))
        try:
            self.active(ident,runtime['generation'])
            if parameters is not None and not isinstance(parameters,dict):raise ValueError('Параметрите на плъгин команда трябва да са обект.')
            data_only(parameters or {},200_000)
            result=self.gateway.call(Request(ident,self.command_name(ident,command),copy.deepcopy(parameters or {})));return copy.deepcopy(data_only(result,2_000_000))
        finally:CALL_CHAIN.reset(token);runtime['command_lock'].release()
    def input(self,ident,contribution,element,values=None,row=None,context=None,item_id=None):
        live=self.active(ident)
        if not self.state(ident).get('uiAllowed'):raise PermissionError('Няма разрешение за плъгин интерфейса.')
        elements=live['documents'].get(contribution,[]);target=None
        def find(items):
            for e in items:
                if e.get('disabled') or e.get('visible') is False:continue
                if e['id']==element:return e
                if e['type']=='group':
                    match=find(e.get('children',[]))
                    if match:return match
        target=find(elements)
        if not target or not target.get('command') or target.get('disabled') or target.get('visible') is False:raise ValueError('Елементът няма достъпно действие.')
        parameters=dict(element=element,values=ui_values(elements,values or {}),context=self.ui_context(ident,context))
        if target['type']=='list':
            if not isinstance(item_id,str) or len(item_id)>120:raise ValueError('Невалиден идентификатор на ред в списъка.')
            selected=next((item for item in target.get('items',[]) if item.get('id')==item_id),None)
            if selected is None or selected.get('disabled') or row is not None:raise ValueError('Редът на списъка вече не е наличен.')
            parameters.update(selected=copy.deepcopy(selected))
        elif item_id is not None:
            raise ValueError('Идентификаторът на списък не е допустим за този контрол.')
        if row is not None:
            if target['type']!='table' or type(row) is not int or not 0<=row<len(target['rows']):raise ValueError('Невалиден ред.')
            parameters.update(row=row,selected=target['rows'][row])
        return self.invoke(ident,target['command'],parameters)
    def ui_context(self,ident,value):
        value=value or {}
        if not isinstance(value,dict):raise ValueError('Невалиден подбор за добавката.')
        ids=value.get('ids',[])
        if 'viewerId' in value and (not isinstance(value['viewerId'],str) or not re.fullmatch('[a-f0-9]{32}',value['viewerId'])):raise ValueError('Невалиден идентификатор на преглед.')
        if not isinstance(value,dict) or set(value)-{'ids','archiveId','stage','viewerId'} or not isinstance(ids,list) or len(ids)>50000 or any(not isinstance(i,str) or len(i)>80 for i in ids):raise ValueError('Невалиден подбор за добавката.')
        if ids and 'archive.read' not in self.active(ident)['permissions']:raise PermissionError('Плъгинът няма разрешение за подбора от архива.')
        return copy.deepcopy(value)
    def activate(self,ident,contribution,context):
        self.active(ident)
        if not self.state(ident).get('uiAllowed'):raise PermissionError('Няма разрешение за интерфейса.')
        entry=next((c for c in self.records[ident]['contributions'] if c['id']==contribution),None)
        if not entry:raise ValueError('Непознато допълнение.')
        if not entry.get('activate'):return dict(accepted=True)
        return self.invoke(ident,entry['activate'],dict(context=self.ui_context(ident,context)),wait=True)
    def capabilities(self):
        with self.lock:return [dict(c,pluginId=ident,pluginName=self.records[ident]['name']) for ident in self.live for c in self.records[ident].get('capabilities',[])]
    def invoke_capability(self,capability,parameters=None,plugin=None):
        providers=[c for c in self.capabilities() if c['id']==capability and (not plugin or c['pluginId']==plugin)]
        if len(providers)!=1:raise ValueError('Избери наличен доставчик на възможността.' if providers else f'Няма включен плъгин за функцията „{capability}“. Инсталирай и включи подходящия плъгин от Настройки → Плъгини; виж docs/PLUGINS.md.')
        provider=providers[0];return self.invoke(provider['pluginId'],provider['command'],parameters or {},wait=True)
    def remove(self,ident,confirmed=False):
        if not confirmed:raise PermissionError('Потвърди премахването на добавката.')
        with self.lock:
            m=self.records[ident];self.unload(ident);source=within(self.folder,ident)
            destination=self.service.store.folder/'RemovedPlugins'/(ident+'-'+uuid.uuid4().hex);destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.move(str(source),str(destination));self.records.pop(ident);self.state(ident).update(enabled=False,uiAllowed=False);self.settings.save();self.events.emit('plugins.changed')
            self.prune_folder()
            return dict(preserved=str(destination))
    def prune_folder(self):
        # rmdir only removes an actually empty directory; never discard unknown files.
        try:self.folder.rmdir()
        except OSError:pass
    def install(self,source):
        with self.lock:return self._install(source)
    def _install(self,source):
        source=Path(source).resolve();m=self.manifest(source);destination=self.folder/m['id']
        if destination.exists():raise FileExistsError('Плъгин с този id вече съществува.')
        staging=self.folder/('.install-'+uuid.uuid4().hex)
        try:
            self.folder.mkdir(parents=True,exist_ok=True)
            staging.mkdir()
            for parent,dirs,names in os.walk(source,followlinks=False):
                dirs[:]=[d for d in dirs if not d.startswith('.') and d not in ('__pycache__','node_modules')]
                for name in names:
                    relative=(Path(parent)/name).relative_to(source).as_posix();file=within(source,relative);target=within(staging,relative,existing=False);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(file,target)
            staging.rename(destination)
        finally:
            if staging.exists() and staging.resolve().is_relative_to(self.folder) and staging.name.startswith('.install-'):shutil.rmtree(staging)
            self.prune_folder()
        self.scan();return dict(installed=m['id'])
