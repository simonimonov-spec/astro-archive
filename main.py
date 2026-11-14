"""Independent encrypted Windows archive sharing. UI is declarative only."""
from __future__ import annotations
import copy, hashlib, json, os, re, threading, time, uuid
from pathlib import Path
from . import windows
from .validation import SHARE_MARKER, ARCHIVE_MARKER, KINDS, label, child_name, share_name, lan, unc, metadata, atomic

def field(kind, ident, **properties):return dict(type=kind,id=ident,**properties)
def option(value,label):return dict(value=value,label=label)

class SharedArchives:
    def __init__(self,context):
        self.ctx=context;self.folder=Path(context.storage());self.closed=threading.Event();self.lock=threading.RLock();self.controller=windows.Controller(self.folder,Path(__file__).with_name('host.ps1'))
        self.environment={};self.problem='';self.remote=[];self.selected=0;self.plan_text='';self.last_status=None
        self.reviewed=None;self.consent_id='host-approved'
        self.archives=self.ctx.archives('list')['archives']
        try:self.environment=windows.environment()
        except Exception as error:self.problem=str(error)
        local=[a for a in self.archives if a.get('path') and re.match(r'^[A-Za-z]:\\',a['path'])]
        first=next((a for a in local if a['id']=='frames'),local[0] if local else None)
        network=(self.environment.get('networks') or [{}])[0]
        defaults=dict(version=1,hostId=uuid.uuid4().hex,address=network.get('address',''),account=self.environment.get('account',''),maxClients=8,
                      exports=[dict(archiveId=first['id'],share='AstroArchive-frames',access='change')] if first else [],
                      workspaceEnabled=True,workspace=str(Path(first['path']).parent/'Astro Archive Shared') if first else '',
                      clientRoot='',clientName='',clientKind='frames',newName='',newKind='frames',connections=[])
        self.options={**defaults,**context.settings()};self.ctx.save_settings(self.options)
        for name,handler in [('refresh',self.refresh),('save',self.save),('add-export',self.add_export),('remove-export',self.remove_export),('select-export',self.select_export),('plan',self.review),('start',self.start),('stop',self.stop),('copy',self.copy),('connect',self.connect),('select-remote',self.select_remote),('attach',self.attach),('create',self.create),('refresh-index',self.refresh_index),('status',self.status)]:context.command(name,handler)
        self.publish();self.thread=threading.Thread(target=self.watch,daemon=True,name='SharedArchives-status');self.thread.start()

    def values(self,p):return p.get('values',p)
    def save(self,p):
        v=self.values(p);o=copy.deepcopy(self.options)
        mapping={'host-address':'address','host-account':'account','workspace':'workspace','workspace-enabled':'workspaceEnabled','max-clients':'maxClients','client-root':'clientRoot','client-name':'clientName','client-kind':'clientKind','new-name':'newName','new-kind':'newKind'}
        for key,target in mapping.items():
            if key in v:o[target]=v[key]
        if type(o['workspaceEnabled']) is not bool or type(o['maxClients']) is not int or not 1<=o['maxClients']<=16:raise ValueError('Невалидни настройки за споделяне.')
        if o['clientKind'] not in KINDS or o['newKind'] not in KINDS:raise ValueError('Непознат вид архив.')
        for key in ('workspace','clientRoot'):
            if not isinstance(o[key],str) or len(o[key])>1000 or '\x00' in o[key]:raise ValueError('Невалиден път.')
        for key in ('account','clientName','newName'):
            if not isinstance(o[key],str) or len(o[key])>120 or any(ord(c)<32 for c in o[key]):raise ValueError('Невалиден текст.')
        if self.controller.status()['state'] in ('running','starting','stopping'):
            for key in ('address','account','workspace','workspaceEnabled','maxClients'):
                if o[key]!=self.options[key]:raise ValueError('Спри хоста преди промяна на мрежовите му настройки.')
        self.options=o;self.ctx.save_settings(o)
        return dict(saved=True)

    def refresh(self,p):
        self.archives=self.ctx.archives('list')['archives']
        try:self.environment=windows.environment();self.problem=''
        except Exception as error:self.problem=str(error)
        self.publish();return self.status({})

    def add_export(self,p):
        self.save(p)
        if self.controller.status()['state'] not in ('off','stopped','error'):raise ValueError('Спри хоста преди добавяне на дял.')
        v=self.values(p);ident=v.get('host-archive');row=next((a for a in self.archives if a['id']==ident and a.get('path')),None)
        if not row or not re.match(r'^[A-Za-z]:\\',row['path']):raise ValueError('Избери локален архив на този компютър.')
        share=share_name(v.get('share-name',''))
        if any(e['share'].casefold()==share.casefold() or e['archiveId']==ident for e in self.options['exports']):raise ValueError('Архивът или името вече е добавено.')
        access=v.get('share-access','change')
        if access not in ('read','change'):raise ValueError('Невалидни права.')
        if len(self.options['exports'])>=15:raise ValueError('Допускат се до 15 архивни дяла.')
        self.options['exports'].append(dict(archiveId=ident,share=share,access=access));self.ctx.save_settings(self.options);self.publish();return dict(message='Архивът е добавен към плана. Хостът още не е включен.')

    def select_export(self,p):self.selected=p.get('row',0);return dict(selected=self.selected)
    def remove_export(self,p):
        if self.controller.status()['state'] not in ('off','stopped','error'):raise ValueError('Първо спри хоста.')
        if not 0<=self.selected<len(self.options['exports']):raise ValueError('Избери ред от дяловете.')
        self.options['exports'].pop(self.selected);self.selected=0;self.ctx.save_settings(self.options);self.publish();return dict(message='Дялът е премахнат от плана. Файловете са запазени.')

    def plan(self):
        self.archives=self.ctx.archives('list')['archives'];environment=windows.environment();self.environment=environment
        network=next((n for n in environment['networks'] if n['address']==self.options['address']),None)
        if not network:raise ValueError('Избери активен LAN интерфейс с домашен IPv4 адрес.')
        address,subnet=lan(network['address'],network['prefix'])
        account=label(self.options['account'])
        if not re.fullmatch(r'[^\\/:*?"<>|\r\n]+\\[^\\/:*?"<>|\r\n]+',account):raise ValueError('Въведи потребител като КОМПЮТЪР\\потребител.')
        if environment.get('smb1') or not environment.get('smb2') or not environment.get('rejectPlaintext') or environment.get('service')!='Running':raise ValueError('SMB 3 не е готов за задължително шифроване. Глобалните настройки не се променят автоматично.')
        exports=[]
        for item in self.options['exports']:
            row=next((a for a in self.archives if a['id']==item['archiveId']),None)
            if not row or not row.get('path'):raise ValueError('Папката на избран архив не е зададена.')
            path=Path(row['path']).resolve(strict=True)
            if not re.match(r'^[A-Za-z]:\\',str(path)) or not path.is_dir() or path==Path(path.anchor):raise ValueError('Споделяй локална архивна папка, а не мрежов дял или целия диск.')
            exports.append(dict(path=str(path),share=share_name(item['share']),access=item['access'],kind='archive',type=row.get('type') or (row['id'] if row['id'] in KINDS else 'frames'),name=row['name'],id=row.get('identity') or uuid.uuid5(uuid.NAMESPACE_URL,str(path)).hex))
        if self.options['workspaceEnabled']:
            path=Path(self.options['workspace']).resolve()
            if not re.match(r'^[A-Za-z]:\\',str(path)) or path==Path(path.anchor):raise ValueError('Избери отделна локална папка за новите архиви.')
            for row in self.archives:
                if row.get('path'):
                    other=Path(row['path']).resolve()
                    if path.is_relative_to(other) or other.is_relative_to(path):raise ValueError('Папката за нови архиви трябва да е извън съществуващите архиви.')
            if path.exists():
                if not path.is_dir():raise ValueError('Мястото за нови архиви не е папка.')
                existing=path/SHARE_MARKER
                if existing.exists():
                    document=metadata(existing,'workspace')
                    if document.get('hostId')!=self.options['hostId']:raise ValueError('Папката за нови архиви е заявена от друг хост.')
                elif any(path.iterdir()):raise ValueError('Избери нова или празна папка за новите архиви.')
            exports.append(dict(path=str(path),share='AstroArchive-Workspace',access='change',kind='workspace',name='Нови споделени архиви',id=self.options['hostId']))
        if not exports:raise ValueError('Добави поне един архив или папка за нови архиви.')
        if len({e['share'].casefold() for e in exports})!=len(exports):raise ValueError('Повторено име на дял.')
        root=Path(__file__).resolve().parents[2]
        for item in exports:
            path=Path(item['path'])
            if path.is_relative_to(root) or root.is_relative_to(path):raise ValueError('Програмната папка не се споделя като архив.')
            for parent in [path,*path.parents]:
                if parent.exists() and (parent.is_symlink() or getattr(parent,'is_junction',lambda:False)()):raise ValueError('Връзки към други папки не се споделят.')
        return dict(version=1,hostId=self.options['hostId'],account=account,address=address,network=subnet,prefix=network['prefix'],interface=network['interface'],maxClients=self.options['maxClients'],exports=exports)

    def review(self,p):
        self.save(p)
        if self.controller.status()['state'] not in ('off','stopped','error'):raise ValueError('Спри хоста преди промяна на плана.')
        plan=self.plan();self.reviewed=hashlib.sha256(json.dumps(plan,sort_keys=True).encode()).hexdigest();self.consent_id='host-approved-'+uuid.uuid4().hex[:12]
        self.plan_text=json.dumps(plan,ensure_ascii=False,indent=2);atomic(self.folder/'last-plan.json',plan);self.publish()
        return dict(message='Планът е готов за преглед. Не са създавани дялове или правила във Firewall.')

    def start(self,p):
        self.save(p)
        if self.values(p).get(self.consent_id) is not True:raise ValueError('Прегледай плана и потвърди точно описаните Windows дялове и LAN правило.')
        plan=self.plan();self.plan_text=json.dumps(plan,ensure_ascii=False,indent=2);atomic(self.folder/'last-plan.json',plan)
        if self.reviewed!=hashlib.sha256(json.dumps(plan,sort_keys=True).encode()).hexdigest():raise ValueError('Планът е променен. Прегледай и потвърди актуалните папки, права и мрежа.')
        self.reviewed=None;self.consent_id='host-approved-'+uuid.uuid4().hex[:12]
        def work(cancel,progress):
            try:
                for item in plan['exports']:
                    path=Path(item['path'])
                    if item['kind']=='workspace':path.mkdir(parents=False,exist_ok=True)
                    target=path/SHARE_MARKER
                    if target.exists():
                        previous=metadata(target)
                        if previous.get('hostId')!=plan['hostId']:raise ValueError('Папката има описание от друг хост; няма да бъде заменено.')
                    atomic(target,{k:item[k] for k in ('kind','name','id','type') if k in item}|dict(version=1,hostId=plan['hostId']))
                result=self.controller.start(plan,cancel,progress);self.ctx.archives('hold',dict(active=True));self.publish();return result
            except Exception as error:self.problem=str(error);self.publish();raise
        return self.ctx.task('Включване на шифровани LAN архиви',work,'archive')

    def stop(self,p):
        self.controller.stop()
        def work(cancel,progress):
            deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                result=self.controller.status()
                if result['state'] in ('off','stopped','error'):self.ctx.archives('hold',dict(active=False));self.publish();return result
                progress(0,1,'Windows затваря собствените мрежови дялове и връзките към тях');cancel.wait(.3)
            raise ValueError('Windows още не потвърждава спирането. Провери състоянието.')
        return self.ctx.task('Спиране на споделените архиви',work,'archive')

    def copy(self,p):
        exports=self.plan()['exports'];host=self.environment.get('computer') or self.options['address']
        return dict(copyText='\n'.join('\\\\'+host+'\\'+e['share'] for e in exports),message='Адресите са готови за другата инстанция на Astro Archive.')

    def browse(self,root,interactive=False):
        secure=windows.connection(root,interactive=interactive);document=metadata(Path(secure['path'])/SHARE_MARKER);rows=[]
        if document['kind']=='archive':rows.append(dict(path=secure['path'],**document))
        else:
            for path in sorted(Path(secure['path']).iterdir()):
                if len(rows)>=1000:raise ValueError('Твърде много архиви в споделената папка.')
                if not path.is_dir() or path.is_symlink() or getattr(path,'is_junction',lambda:False)():continue
                target=path/ARCHIVE_MARKER
                if target.exists():
                    data=metadata(target,'archive');rows.append(dict(path=str(path),**data))
        self.remote=rows;self.remote_kind=document['kind'];return secure

    def connect(self,p):
        self.save(p)
        def work(cancel,progress):
            progress(0,1,'Windows проверява LAN връзката, потребителя и шифроването')
            result=self.browse(self.options['clientRoot'],interactive=True)
            if self.options['clientRoot'] not in self.options['connections']:self.options['connections'].append(self.options['clientRoot']);self.ctx.save_settings(self.options)
            self.problem='';self.publish();return dict(**result,archives=len(self.remote))
        return self.ctx.task('Свързване към споделен архив',work,'archive')

    def select_remote(self,p):self.remote_selected=p.get('row',0);return dict(selected=getattr(self,'remote_selected',0))
    def attach(self,p):
        index=getattr(self,'remote_selected',0)
        if not 0<=index<len(self.remote):raise ValueError('Свържи папката и избери архив от таблицата.')
        row=self.remote[index];windows.connection(row['path']);result=self.ctx.archives('attach',dict(path=row['path'],name=row['name'],kind=row['type'],identity=row['id']))
        self.ctx.archives('refresh',dict(id=result['id']));self.archives=self.ctx.archives('list')['archives'];self.publish();return dict(message='Споделеният архив е добавен до локалните. Индексът се обновява.',navigation=dict(archiveId=result['id'],relativePath=''))

    def create(self,p):
        self.save(p);name=child_name(self.options['newName']);kind=self.options['newKind'];root=self.options['clientRoot']
        def work(cancel,progress):
            secure=windows.connection(root);parent=Path(secure['path']);metadata(parent/SHARE_MARKER,'workspace');path=parent/name
            path.mkdir(exist_ok=False);document=dict(version=1,kind='archive',id=uuid.uuid4().hex,name=name,type=kind)
            try:
                atomic(path/ARCHIVE_MARKER,document)
                # A new archive can also be exported independently in the future.
                atomic(path/SHARE_MARKER,document)
                result=self.ctx.archives('attach',dict(path=str(path),name=name,kind=kind,identity=document['id'],initialize=True))
            except BaseException:
                for filename in (ARCHIVE_MARKER,SHARE_MARKER):(path/filename).unlink(missing_ok=True)
                try:path.rmdir()
                except OSError:pass
                raise
            self.browse(root);self.archives=self.ctx.archives('list')['archives'];self.publish();progress(1,1,'Новият архив е създаден в споделената папка')
            return dict(**result,message='Новият архив е създаден на хоста; локалните архиви са запазени.')
        return self.ctx.task('Нов архив в споделената папка',work,'archive')

    def refresh_index(self,p):
        self.archives=self.ctx.archives('list')['archives'];started=[]
        for row in self.archives:
            if row.get('ownerPlugin')=='shared-archives' and row.get('path'):
                if row['path'].startswith('\\\\'):windows.connection(row['path'])
                started.append(self.ctx.archives('refresh',dict(id=row['id'])))
        return dict(message=f'Обновяване на {len(started)} споделени архива.',tasks=started)

    def status(self,p):return dict(**self.controller.status(),configuredExports=len(self.options['exports']),transport='SMB 3',account=self.options['account'])

    def publish_header(self):
        status=self.controller.status();running=status['state']=='running';text='Включен · SMB 3 · шифрован' if running else 'Изключен' if status['state'] in ('off','stopped') else status.get('problem') or status['state']
        self.ctx.publish('state',[field('metric','sharing-state',label='Споделени LAN архиви',value=text,tone='success' if running else 'normal',help=f"Отворени мрежови файлове: {status.get('openFiles',0)}")])

    def publish(self):
        archives=[a for a in self.archives if a.get('path') and re.match(r'^[A-Za-z]:\\',a['path'])];networks=self.environment.get('networks',[]);o=self.options;running=self.controller.status()['state'] not in ('off','stopped','error')
        exports=[]
        for e in o['exports']:
            row=next((a for a in self.archives if a['id']==e['archiveId']),{})
            exports.append(dict(name=row.get('name',e['archiveId']),path=row.get('path',''),share=e['share'],rights='Четене и запис' if e['access']=='change' else 'Само четене'))
        fields=[field('text','intro',span=2,value='Shared Archives свързва отделни Windows инстанции на Astro Archive през домашната LAN мрежа. Всеки компютър запазва собствения си профил и индекс; SQLite базата на програмата не се споделя.'),
            field('group','host',label='1 · Хоствай архив от този компютър',span=2,children=[
                field('select','host-address',label='LAN интерфейс',value=o['address'] if any(n['address']==o['address'] for n in networks) else (networks[0]['address'] if networks else ''),options=[option(n['address'],f"{n['interface']} · {n['address']}/{n['prefix']}") for n in networks] or [option('','Няма активна домашна LAN мрежа')],command='save'),
                field('input','host-account',label='Разрешен Windows потребител',value=o['account'],maxLength=120,command='save',help='Пример: MONI\\simon. Използва се Windows паролата, а не PIN. Не се дава достъп на Everyone или Guests.'),
                field('table','exports',label='Архиви, включени в плана',span=2,columns=[dict(key='name',label='Архив'),dict(key='path',label='Папка'),dict(key='share',label='Име на мрежов дял'),dict(key='rights',label='Права')],rows=exports,command='select-export'),
                field('select','host-archive',label='Добави локален архив',value=archives[0]['id'] if archives else '',options=[option(a['id'],a['name']) for a in archives] or [option('','Няма зададен локален архив')],disabled=running),
                field('input','share-name',label='Име на новия дял',value='AstroArchive-archive',maxLength=61,disabled=running),
                field('select','share-access',label='Права',value='change',options=[option('change','Четене и запис'),option('read','Само четене')],disabled=running),
                field('button','add-export',label='Добави към плана',command='add-export',disabled=running),field('button','remove-export',label='Премахни избрания дял от плана',command='remove-export',disabled=running or not exports),
                field('checkbox','workspace-enabled',label='Позволи създаване на нови архиви в отделна споделена папка',value=o['workspaceEnabled'],command='save',disabled=running),
                field('input','workspace',label='Отделна папка за новите архиви',value=o['workspace'],maxLength=1000,command='save',disabled=running,span=2),
                field('number','max-clients',label='Максимум едновременни потребители',value=o['maxClients'],min=1,max=16,step=1,command='save',disabled=running),
                field('text','host-security',span=2,value='Задължително SMB 3 шифроване; само избрани папки и конкретният Windows потребител. Ще се създаде едно правило TCP 445 само за избрания LAN адрес, интерфейс и подмрежа. Рутерът, VPN и глобалните SMB настройки не се променят.'),
                field('button','review',label='Преглед на точните промени в Windows',command='plan',disabled=running),
                field('console','plan',label='План преди включване',value=self.plan_text or 'Прегледай плана преди включване. Хостването е изключено по подразбиране.',span=2),
                field('checkbox',self.consent_id,label='Разрешавам точно показаните мрежови дялове и ограниченото LAN правило',value=False,disabled=running),
                field('button','start',label='Включи шифрования LAN хост',command='start',tone='primary',disabled=running),
                field('button','stop',label='Спри хоста и връзките към него',command='stop',disabled=not running,help='Запази промените на свързаните устройства преди спиране. При затваряне или срив на хоста собствените дялове също се премахват.'),
                field('button','copy',label='Копирай адресите за другата инстанция',command='copy'),
            ]),
            field('group','client',label='2 · Свържи архив от друг компютър',span=2,children=[
                field('input','client-root',label='Адрес, копиран от хоста',value=o['clientRoot'],maxLength=1000,placeholder='\\\\MONI\\AstroArchive-frames',command='save',span=2),
                field('button','connect',label='Свържи и открий архивите',command='connect',tone='primary'),
                field('text','client-security',span=2,value='Windows ще поиска потребител и парола в собствен защитен прозорец. Добавянето се отказва, ако връзката не е шифрована SMB 3. Големите FITS файлове се четат от обичайните функции при нужда; не се копира целият архив.'),
                field('table','remote-archives',label='Архиви в свързаната папка',span=2,columns=[dict(key='name',label='Архив'),dict(key='type',label='Вид'),dict(key='path',label='Мрежово място')],rows=self.remote,command='select-remote'),
                field('button','attach',label='Добави избрания архив до локалните',command='attach',disabled=not self.remote),
                field('input','new-name',label='Име на новия архив',value=o['newName'],maxLength=100,command='save'),
                field('select','new-kind',label='Вид на новия архив',value=o['newKind'],options=[option('frames','Астро кадри'),option('stacks','Астро стакове'),option('photos','Астро снимки')],command='save'),
                field('button','create',label='Създай нов архив в споделената папка',command='create',help='Използвай адреса AstroArchive-Workspace. Съществуващи папки не се заменят.'),
                field('button','refresh-index',label='Обнови споделените архиви',command='refresh-index'),
            ]),field('button','refresh',label='Обнови състоянието и LAN адресите',command='refresh')]
        if self.problem:fields.insert(1,field('text','problem',span=2,value=self.problem,tone='warning'))
        self.ctx.publish('network',fields)
        self.ctx.publish('archives',[field('text','archive-help',span=2,value='Споделените архиви се добавят от Настройки → Плъгини → Shared Archives. След свързване са в обичайния списък до собствените локални архиви.'),field('button','archive-refresh',label='Обнови споделените архиви',command='refresh-index')])
        self.publish_header()

    def watch(self):
        while not self.closed.wait(4):
            try:
                state=self.status({});fingerprint=json.dumps(state,sort_keys=True)
                if state['state'] in ('off','stopped','error'):self.ctx.archives('hold',dict(active=False))
                if fingerprint!=self.last_status:self.last_status=fingerprint;self.publish_header()
                if state['state']!='running' or not self.options['workspaceEnabled']:continue
                root=Path(self.options['workspace']);descriptor=metadata(root/SHARE_MARKER,'workspace')
                if descriptor.get('hostId')!=self.options['hostId']:continue
                known={a.get('path') for a in self.ctx.archives('list')['archives']}
                for path in root.iterdir():
                    if not path.is_dir() or path.is_symlink() or getattr(path,'is_junction',lambda:False)() or str(path) in known:continue
                    target=path/ARCHIVE_MARKER
                    if target.exists():
                        data=metadata(target,'archive');self.ctx.archives('attach',dict(path=str(path),name=data['name'],kind=data['type'],identity=data['id']));self.archives=self.ctx.archives('list')['archives']
            except Exception:pass

    def close(self):
        self.closed.set();self.controller.stop()
        if self.thread.is_alive():self.thread.join(2)

def setup(context):
    if not callable(getattr(context,'archives',None)):
        raise RuntimeError('Shared Archives изисква обновен Astro Archive с архивния Bridge SDK context.archives. Обнови програмата и на двата компютъра.')
    plugin=SharedArchives(context)
    return plugin.close
