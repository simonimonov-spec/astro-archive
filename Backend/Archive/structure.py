"""Archive schemas and bounded header expressions, shared by routing and display."""
import ast, copy, re
from pathlib import Path
from Backend.Archive.formulas import display
from Backend.Fits.images import component

FIELDS=dict(telescope='Телескоп',realTelescope='Истинско име на телескопа',object='Обект',night='Наблюдателна нощ',observed='Дата на заснемане',created='Дата на създаване',filter='Филтър',sensor='Сензор',resolution='Резолюция',frames='Кадри',files='Файлове',stacks='Стакове',sourceStackCount='Изходни стакове',totalExposure='Обща експозиция',exposure='Експозиция',size='Размер',telescopes='Участвали телескопи',kind='Тип',filename='Име на файла',basename='Име без разширение',extension='Разширение')
def preset(kind='frames'):
    kind=kind if kind in ('frames','stacks','photos') else 'frames'
    telescope=dict(field='telescope',formula='{telescope}',icon='telescope',columns=['frames','totalExposure','size','realTelescope'])
    obj=dict(field='object',formula='{object}',icon='object',columns=['frames','totalExposure','size'])
    night=dict(field='night',formula='Наблюдателна нощ {night}',icon='day',columns=['frames','totalExposure','size'])
    levels=[telescope,obj,night]
    file_columns=['exposure','observed','filter','size','sensor','resolution']
    if kind=='stacks':
        obj['columns']=['stacks','frames','totalExposure','size'];levels=[telescope,obj]
        file_columns=['frames','totalExposure','created','filter','sensor','telescope','size','resolution']
    if kind=='photos':
        obj['columns']=['files','created'];levels=[obj]
        file_columns=['created','sourceStackCount','telescopes','size','kind','resolution']
    return dict(version=1,preset=kind,levels=levels,fileFormula='{filename}',fileColumns=file_columns)

def expression(formula,fields):
    """Only constants, names, +, and parentheses. No attributes, calls or eval."""
    tree=ast.parse(formula,mode='eval')
    if sum(1 for _ in ast.walk(tree))>80:raise ValueError('Формулата е прекалено сложна.')
    def visit(node):
        if isinstance(node,ast.Expression):return visit(node.body)
        if isinstance(node,ast.Constant) and type(node.value) in (str,int,float):return node.value
        if isinstance(node,ast.Name):return fields.get(node.id,'Неизвестно')
        if isinstance(node,ast.BinOp) and isinstance(node.op,ast.Add):
            a,b=visit(node.left),visit(node.right)
            return a+b if isinstance(a,(int,float)) and isinstance(b,(int,float)) else str(a)+str(b)
        raise ValueError('Разрешени са имена на полета, + и текст в кавички.')
    return str(visit(tree))

def fields(meta,name,aliases=None):
    values=dict(meta.get('headers',{}),**{k:v for k,v in meta.items() if k!='headers'})
    values.update(filename=name,basename=Path(name).stem,extension=Path(name).suffix,realTelescope=meta.get('telescope',''),size=meta.get('size',0))
    values['telescope']=(aliases or {}).get(meta.get('telescope'),meta.get('telescope','Неизвестен телескоп'))
    return values

def render(formula,meta,name,aliases=None):
    values=fields(meta,name,aliases)
    if '{' in formula:return display(formula,name,dict(meta,headers=values),aliases)
    return expression(formula,values)

def validate(value,kind='frames'):
    value=copy.deepcopy(value or preset(kind))
    if not isinstance(value,dict) or not isinstance(value.get('levels'),list) or not 1<=len(value['levels'])<=8:raise ValueError('Избери от 1 до 8 нива на архива.')
    for item in [*value['levels'],dict(formula=value.get('fileFormula','{filename}'),columns=value.get('fileColumns',[]))]:
        formula=item.get('formula','')
        if not isinstance(formula,str) or not 1<=len(formula)<=512:raise ValueError('Името трябва да има формула до 512 знака.')
        render(formula,dict(object='M16',telescope='S50',night='2026-10-25',headers={}), 'example.fit')
        if not isinstance(item.get('columns'),list) or len(item['columns'])>24 or any(k not in FIELDS for k in item['columns']):raise ValueError('Непознато информационно поле.')
    for level in value['levels']:
        if not isinstance(level.get('field',''),str) or len(level.get('field',''))>80:raise ValueError('Невалидно поле за групиране.')
        if not isinstance(level.get('icon','folder'),str) or len(level.get('icon','folder'))>160:raise ValueError('Невалидна икона.')
    value['version']=1;return value

def schema(settings,archive):
    root=next(a for a in settings.value['archives'] if a['id']==archive)
    return root.get('structure') or preset(root.get('type',archive))

def destination(settings,archive,meta,name):
    layout=schema(settings,archive)
    # Display aliases must not split an existing telescope into two physical
    # groups when an unrelated object's metadata is edited.
    folders=[component(render(level['formula'],meta,name)) for level in layout['levels']]
    file=component(render(layout.get('fileFormula','{filename}'),meta,name))
    if Path(file).suffix.lower()!=Path(name).suffix.lower():file+=Path(name).suffix
    return Path(*folders)/file
