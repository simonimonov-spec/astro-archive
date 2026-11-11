"""Versioned plugin input/output contract. No legacy plugins are loaded."""
from dataclasses import dataclass, field
from typing import Any, Callable
import copy, re
from Bridge.Core.protocol import data_only

ELEMENT_ID=re.compile(r'^[a-zA-Z][a-zA-Z0-9_-]{0,63}$')
UI_TYPES={'button','table','list','text','select','checkbox','number','input','textarea','chart','group','metric','console','image'}
SLOTS={'archive','analysis','analysis-tools','viewer-tools','preparation','preparation-activity','settings','customization','header'}

def ui_document(elements):
    """Declarative data only: fixed controls, no markup, scripts or arbitrary URLs."""
    data_only(elements,2_000_000)
    if not isinstance(elements,list):raise ValueError('UI документът трябва да е списък.')
    seen=set()
    def validate(items,depth=0):
        if depth>6:raise ValueError('Прекалено вложен интерфейс.')
        if not isinstance(items,list):raise ValueError('UI елементите трябва да са списък.')
        for e in items:
            if not isinstance(e,dict) or not isinstance(e.get('type'),str) or e['type'] not in UI_TYPES or not isinstance(e.get('id'),str) or not ELEMENT_ID.fullmatch(e['id']):raise ValueError('Невалиден UI елемент.')
            if e['id'] in seen:raise ValueError('Повторен UI идентификатор.')
            seen.add(e['id'])
            if len(seen)>200:raise ValueError('Максимум 200 UI елемента на поле.')
            common={'type','id','label','help','visible','disabled','span'}
            allowed={
                'text':{'value','tone'},'metric':{'value','unit','tone'},
                'input':{'value','placeholder','maxLength','command'},'textarea':{'value','placeholder','maxLength','command'},'number':{'value','min','max','step','command'},
                'checkbox':{'value','command'},'select':{'value','options','command'},
                'button':{'command','tone'},'group':{'children'},'console':{'value'},'image':{'value','assetId'},
                'table':{'columns','rows','command'},'chart':{'chartType','series','xLabel','yLabel'},
                'list':{'items','command','actionLabel'},
            }[e['type']]
            if set(e)-common-allowed:raise ValueError('Непозволени свойства на UI елемент: '+', '.join(set(e)-common-allowed))
            for key in ('label','help','placeholder','unit'):
                if key in e and (not isinstance(e[key],str) or len(e[key])>4000):raise ValueError('Невалиден текст в UI.')
            if 'command' in e and (not isinstance(e['command'],str) or not re.fullmatch(r'[a-z][a-z0-9_.-]{0,119}',e['command'])):raise ValueError('Невалидна UI команда.')
            for key in ('visible','disabled'):
                if key in e and type(e[key]) is not bool:raise ValueError('Очаква се булева стойност за '+key)
            if 'span' in e and (type(e['span']) is not int or e['span'] not in (1,2)):raise ValueError('Невалидна ширина на UI поле.')
            if 'tone' in e and e['tone'] not in ('normal','primary','warning','success','error'):raise ValueError('Невалиден стил на поле.')
            if e['type'] in ('text','metric') and type(e.get('value')) not in (str,int,float,bool,type(None)):raise ValueError('Очаква се текст или число за показване.')
            if e['type']=='console' and (not isinstance(e.get('value',''),str) or len(e.get('value',''))>100000):raise ValueError('Невалидна конзола.')
            if e['type']=='image':
                if 'assetId' in e:
                    if 'value' in e or not isinstance(e['assetId'],str) or not re.fullmatch('[a-f0-9]{64}',e['assetId']):raise ValueError('Невалиден ресурс за изображение.')
                    continue
                import base64,io
                from PIL import Image
                value=e.get('value','')
                if not isinstance(value,str) or not value.startswith('data:image/png;base64,') or len(value)>2000000:raise ValueError('Изображението трябва да е PNG данни.')
                with Image.open(io.BytesIO(base64.b64decode(value.split(',',1)[1],validate=True))) as picture:
                    if picture.format!='PNG' or picture.width*picture.height>4000000:raise ValueError('Прекалено голямо изображение.')
            if e['type'] in ('input','textarea'):
                if type(e.get('maxLength',4000)) is not int or not 1<=e.get('maxLength',4000)<=(64000 if e['type']=='textarea' else 4000):raise ValueError('Невалидна дължина на текстов вход.')
                if 'value' in e and (not isinstance(e['value'],str) or len(e['value'])>e.get('maxLength',4000)):raise ValueError('Невалиден начален текст.')
            if e['type']=='checkbox' and 'value' in e and type(e['value']) is not bool:raise ValueError('Невалидна начална отметка.')
            if e['type']=='number':
                for key in ('min','max','step','value'):
                    if key in e and type(e[key]) not in (int,float):raise ValueError('Невалидно числово свойство.')
                if e.get('min',float('-inf'))>e.get('max',float('inf')) or e.get('step',1)<=0:raise ValueError('Невалиден числов интервал.')
                if 'value' in e and not e.get('min',float('-inf'))<=e['value']<=e.get('max',float('inf')):raise ValueError('Началната стойност е извън интервала.')
            if e['type']=='group':validate(e.get('children',[]),depth+1)
            if e['type']=='select':
                options=e.get('options',[])
                if not isinstance(options,list) or len(options)>1000 or any(not isinstance(o,dict) or set(o)-{'value','label'} or not isinstance(o.get('value'),str) or not isinstance(o.get('label'),str) for o in options):raise ValueError('Невалидни опции за избор.')
                if len({o['value'] for o in options})!=len(options) or ('value' in e and e['value'] not in [o['value'] for o in options]):raise ValueError('Повторена опция или невалиден начален избор.')
            if e['type']=='table':
                columns=e.get('columns',[]);rows=e.get('rows',[])
                if not isinstance(columns,list) or not 1<=len(columns)<=40 or any(not isinstance(c,dict) or not isinstance(c.get('key'),str) or not ELEMENT_ID.fullmatch(c['key']) or not isinstance(c.get('label'),str) or c.get('format','text') not in ('text','number','exposure') or set(c)-{'key','label','format'} for c in columns):raise ValueError('Невалидни колони на таблица.')
                if len({c['key'] for c in columns})!=len(columns):raise ValueError('Повторена колона.')
                if not isinstance(rows,list) or len(rows)>10000 or any(not isinstance(r,dict) for r in rows):raise ValueError('Невалидни редове на таблица.')
                if any(type(r.get(c['key'])) not in (str,int,float,bool,type(None)) for r in rows for c in columns):raise ValueError('Клетките на таблицата трябва да съдържат текст или числа.')
            if e['type']=='list':
                items=e.get('items',[])
                if not isinstance(items,list) or len(items)>1000:raise ValueError('Невалиден списък на плъгин.')
                ids=set()
                for item in items:
                    if not isinstance(item,dict) or set(item)-{'id','label','description','status','tone','disabled'}:raise ValueError('Невалиден ред на списък.')
                    ident=item.get('id')
                    if not isinstance(ident,str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9:_-]{0,119}',ident) or ident in ids:raise ValueError('Невалиден или повторен идентификатор в списъка.')
                    ids.add(ident)
                    for key in ('label','description','status'):
                        if key in item and (not isinstance(item[key],str) or len(item[key])>4000):raise ValueError('Невалиден текст в списъка.')
                    if item.get('tone','normal') not in ('normal','primary','success','warning','error'):raise ValueError('Невалиден стил в списъка.')
                    if 'disabled' in item and type(item['disabled']) is not bool:raise ValueError('Невалидно състояние на списък.')
                if 'actionLabel' in e and (not isinstance(e['actionLabel'],str) or len(e['actionLabel'])>120):raise ValueError('Невалидно действие на списък.')
            if e['type']=='chart':
                if e.get('chartType','line') not in ('line','scatter','bar'):raise ValueError('Неподдържан вид диаграма.')
                series=e.get('series',[])
                if not isinstance(series,list) or len(series)>12:raise ValueError('Прекалено много серии.')
                for series_item in series:
                    if not isinstance(series_item,dict) or set(series_item)-{'name','color','points'} or not isinstance(series_item.get('name',''),str):raise ValueError('Невалидна серия.')
                    if 'color' in series_item and (not isinstance(series_item['color'],str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',series_item['color'])):raise ValueError('Невалиден цвят.')
                    points=series_item.get('points',[])
                    if not isinstance(points,list) or len(points)>5000:raise ValueError('Максимум 5000 точки на серия.')
                    if any(not isinstance(p,dict) or set(p)-{'x','y','label'} or type(p.get('x')) not in (int,float) or type(p.get('y')) not in (int,float) for p in points):raise ValueError('Невалидни координати.')
    validate(elements)
    return copy.deepcopy(elements)

def ui_values(elements,values):
    if not isinstance(values,dict):raise ValueError('Невалидни стойности от UI.')
    fields={}
    def visit(items):
        for e in items:
            if e['type']=='group':visit(e.get('children',[]))
            elif e['type'] in ('input','textarea','number','select','checkbox'):fields[e['id']]=e
    visit(elements)
    if set(values)-set(fields):raise ValueError('Непознато поле във входа на плъгин.')
    for key,value in values.items():
        field=fields[key];kind=field['type']
        if kind=='checkbox' and type(value) is not bool:raise ValueError('Очаква се отметка.')
        if kind in ('input','textarea') and (not isinstance(value,str) or len(value)>min(64000 if kind=='textarea' else 4000,field.get('maxLength',4000))):raise ValueError('Невалиден текстов вход.')
        if kind=='number' and (type(value) not in (int,float) or value<field.get('min',float('-inf')) or value>field.get('max',float('inf'))):raise ValueError('Числото е извън разрешените граници.')
        if kind=='select' and value not in [o['value'] for o in field.get('options',[])]:raise ValueError('Непозната избрана стойност.')
    data_only(values,300_000);return values

@dataclass(frozen=True)
class Request:
    plugin:str
    command:str
    parameters:dict[str,Any]=field(default_factory=dict)
    version:int=1

class PluginGateway:
    VERSION=1
    UI_TYPES=UI_TYPES
    def __init__(self,settings,events):self.settings=settings;self.events=events;self.commands={};self.contributions={}
    def register(self,plugin:str,command:str,handler:Callable):
        if not plugin or not command.startswith(plugin+'.') or command in self.commands:raise ValueError('Невалидна или повторена команда на плъгин.')
        self.commands[command]=(plugin,handler)
    def call(self,request:Request):
        if request.version!=self.VERSION:raise ValueError('Неподдържана версия на плъгин интерфейса.')
        if not self.settings.value['approvedPluginPermissions'].get(request.plugin):raise PermissionError('Плъгинът няма разрешение от потребителя.')
        record=self.commands.get(request.command)
        if not record or record[0]!=request.plugin:raise ValueError('Непозната команда на плъгин.')
        return record[1](request.parameters)
    def contribute(self,plugin,section,elements):
        if not self.settings.value['approvedPluginPermissions'].get(plugin):raise PermissionError('Необходимо е разрешение за добавяне на UI.')
        if section not in ('archive','analysis','preparation'):raise ValueError('Непознат раздел.')
        if len(elements)>100:raise ValueError('Твърде много UI елементи.')
        for e in elements:
            if e.get('type') not in self.UI_TYPES or not isinstance(e.get('id'),str):raise ValueError('Невалиден UI елемент.')
            if any(k in e for k in ('html','script','onclick','src')):raise ValueError('Договорът приема данни, а не изпълним HTML/JavaScript.')
        self.contributions[plugin]=dict(section=section,elements=elements)
        self.events.emit('plugin.ui.changed',plugin=plugin,contribution=self.contributions[plugin])
        return dict(accepted=True)
    def input(self,plugin,element,value):
        contribution=self.contributions.get(plugin)
        if not contribution or not any(e['id']==element for e in contribution['elements']):raise ValueError('Непознат UI вход.')
        if not self.settings.value['approvedPluginPermissions'].get(plugin):raise PermissionError('Отнето разрешение.')
        self.events.emit('plugin.input',plugin=plugin,element=element,value=value)
        return dict(accepted=True)
