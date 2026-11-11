"""Bounded presentation preferences, separate from source pixels and file paths."""
import copy,json,math,threading
from Backend.Foundation.runtime import atomic_json

OPTIONS={'mode','channel','palette','separateRgb','blackPercent','whitePercent','midtone','gamma','negative'}
TRANSFORM={'zoom','panX','panY','centerX','centerY','rotation','flipX','flipY','fitting'}
def numeric(value,low,high):
    if type(value) not in (int,float) or not math.isfinite(value) or not low<=value<=high:raise ValueError('Невалидна настройка на прегледа.')
def boolean(value):
    if type(value) is not bool:raise ValueError('Невалиден превключвател на прегледа.')

class ViewerState:
    def __init__(self,store):
        self.path=store.folder/'viewer-preferences.json';self.lock=threading.RLock()
        self.value=dict(version=1,display=dict(options={},edge=1600,debayer=None,pattern='auto'),controls=dict(smooth=True,cross=False,grid=False),transform=dict(zoom=1,centerX=.5,centerY=.5,rotation=0,flipX=False,flipY=False,fitting=True))
        try:
            if self.path.is_file() and not self.path.is_symlink() and self.path.stat().st_size<=16000:
                value=json.loads(self.path.read_text(encoding='utf-8'))
                if value.get('version')==1:
                    value.pop('version');self.update(value,persist=False)
        except (OSError,ValueError,TypeError,AttributeError):pass
    def display(self):
        with self.lock:return copy.deepcopy(self.value['display'])
    def presentation(self):
        with self.lock:return copy.deepcopy({k:v for k,v in self.value.items() if k in ('controls','transform','panel')})
    def update(self,patch,can_debayer=True,persist=True):
        if not isinstance(patch,dict) or set(patch)-{'display','controls','transform','panel'}:raise ValueError('Непознати настройки на прегледа.')
        with self.lock:
            value=copy.deepcopy(self.value)
            if 'display' in patch:
                display=patch['display']
                if not isinstance(display,dict) or set(display)-{'options','edge','debayer','pattern'}:raise ValueError('Невалидни екранни настройки.')
                options=display.get('options',{})
                if not isinstance(options,dict) or set(options)-OPTIONS:raise ValueError('Непознати екранни настройки.')
                options={**value['display']['options'],**options}
                for k,v in options.items():
                    if k in ('negative','separateRgb'):boolean(v)
                    elif k in ('mode','channel','palette'):
                        if type(v) is not int:raise ValueError('Невалидна екранна опция.')
                        numeric(v,0,{'mode':6,'channel':4,'palette':2}[k])
                    else:numeric(v,*{'blackPercent':(0,99.999),'whitePercent':(.001,100),'midtone':(.001,.999),'gamma':(.1,10)}[k])
                if options.get('blackPercent',0)>=options.get('whitePercent',100):raise ValueError('Черната граница трябва да е под бялата.')
                value['display']['options']=options
                if 'edge' in display:
                    edge=display['edge']
                    if type(edge) is not int:raise ValueError('Невалидна разделителна способност.')
                    numeric(edge,0,6400);value['display']['edge']=edge
                if 'debayer' in display:
                    if display['debayer'] is not None:boolean(display['debayer'])
                    if can_debayer:value['display']['debayer']=display['debayer']
                if 'pattern' in display:
                    if display['pattern'] not in ('auto','RGGB','BGGR','GRBG','GBRG'):raise ValueError('Невалидна Bayer матрица.')
                    if can_debayer:value['display']['pattern']=display['pattern']
            if 'controls' in patch:
                controls=patch['controls']
                if not isinstance(controls,dict) or set(controls)-{'smooth','cross','grid'}:raise ValueError('Непознати локални настройки на прегледа.')
                for v in controls.values():boolean(v)
                value['controls'].update(controls)
            if 'transform' in patch:
                transform=patch['transform']
                if not isinstance(transform,dict) or set(transform)-TRANSFORM:raise ValueError('Невалидна ориентация на прегледа.')
                for k,v in transform.items():
                    if k in ('flipX','flipY','fitting'):boolean(v)
                    else:numeric(v,1e-8 if k=='zoom' else -1e8,(1e8 if transform.get('fitting',value['transform'].get('fitting')) else 64) if k=='zoom' else 1e8)
                if ('centerX' in transform)!=('centerY' in transform):raise ValueError('Нужни са двете координати на центъра.')
                if 'panX' in transform or 'panY' in transform:
                    if 'centerX' not in transform:
                        value['transform'].pop('centerX',None);value['transform'].pop('centerY',None)
                value['transform'].update(transform)
            if 'panel' in patch:boolean(patch['panel']);value['panel']=patch['panel']
            if value!=self.value:
                if persist:atomic_json(self.path,value)
                self.value=value

    def accept(self,snapshot,document):
        if not isinstance(snapshot,dict) or set(snapshot)-{'frameId','display','controls','transform','panel'}:raise ValueError('Невалиден запазен преглед.')
        if not isinstance(snapshot.get('frameId'),str) or not document.get('id') or snapshot['frameId']!=document['id']:return False
        self.update({k:v for k,v in snapshot.items() if k!='frameId'},can_debayer=bool(document.get('canDebayer')))
        return True
