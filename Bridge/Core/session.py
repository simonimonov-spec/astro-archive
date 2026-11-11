"""Shared presentation and task references, without replaying UI commands.

One host owns the work. Clients exchange bounded descriptions, never DOM, paths
to arbitrary resources, executable code, or copies of astronomical pixel arrays.
"""
import copy
import json
import math
import re
import threading
import time

CHANNELS = ('route', 'archive', 'analysis', 'preparation', 'extensions', 'viewer')
ANALYSIS = {'phase','mode','kind','view','picker','selected','starIds','x','metric','chartColor','plotWindow','zoom','center','preset','columns','controls','frameList','reportId','compareId','openedSaved','compareMode','classicRun'}
PREPARATION = {'phase','mode','picker','jobId','outputs','controls','exportRequest'}
CONTROLS = {
    'analysis': {'q-tool-view','q-quality-mode','q-advanced','q-zoom-night','q-coverage-view','q-cover-alpha','q-cover-low','q-scope-object','q-scope-night','q-show-excluded','q-force','q-source-filter','q-palette','q-map-mode','q-radius','q-series-metric','q-series-filter','q-detector-sigma','q-detector-radius','q-detector-roundness','q-detector-maxstars','q-sorted','q-plot-warnings','q-units'},
    'preparation': {'w-format','w-program','w-result-kind','w-destination','w-export-format','w-calibrations','w-darks','w-flats','w-biases'},
}

def text(value, size=256):
    if not isinstance(value,str) or len(value)>size or '\0' in value:raise ValueError('Невалиден текст в общата сесия.')
    return value

def ids(value):
    if not isinstance(value,list) or len(value)>40000:raise ValueError('Невалиден общ избор.')
    return list(dict.fromkeys(text(v,100) for v in value))

def choice(value, allowed):
    if value not in allowed:raise ValueError('Непознат изглед в общата сесия.')
    return value

def number(value, low, high):
    if type(value) not in (int,float) or not math.isfinite(value) or not low<=value<=high:raise ValueError('Невалидна стойност в общата сесия.')
    return value

class SharedSession:
    def __init__(self,service):
        self.service=service;self.lock=threading.RLock();self.revision=0;self.channels={};self.writers={};self.active=None;self.preparing=None;self.export_document=None
    def snapshot(self,p=None):
        known=(p or {}).get('versions',{})
        if not isinstance(known,dict) or set(known)-set(CHANNELS):raise ValueError('Невалидни версии на сесията.')
        with self.lock:
            return dict(revision=self.revision,channels=copy.deepcopy({k:v for k,v in self.channels.items() if v['revision']!=known.get(k)}))
    def _set(self,channel,value,author='server'):
        self.revision+=1
        record=dict(revision=self.revision,author=author,value=copy.deepcopy(value))
        self.channels[channel]=record
        self.service.events.emit('ui.session.changed',channel=channel,revision=self.revision,author=author)
        return copy.deepcopy(record)
    def validate(self,channel,value):
        if not isinstance(value,dict):raise ValueError('Очаква се описание на изгледа.')
        result=copy.deepcopy(value)
        allowed={'route':{'section'},'archive':{'entered','archiveId','path','offset','search','filters'},'analysis':ANALYSIS,'preparation':PREPARATION,'extensions':{'analysis','preparation'},'viewer':{'frameId','transform','panel','controls'}}[channel]
        if set(value)-allowed:raise ValueError('Сесията не приема команди, файлове или непознати полета.')
        if channel=='route':choice(value.get('section'),('archive','analysis','preparation'))
        if channel=='archive':
            relative=text(value.get('path',''),2000).replace('\\','/')
            if relative.startswith('/') or ':' in relative or '..' in relative.split('/'):raise ValueError('Разрешен е само относителен архивен път.')
            result['path']=relative
            if value.get('archiveId') not in {a['id'] for a in self.service.settings.value['archives']}:raise ValueError('Непознат архив.')
            if type(value.get('entered')) is not bool:raise ValueError('Невалиден архивен изглед.')
            number(value.get('offset',0),0,100_000_000);text(value.get('search',''))
            filters=value.get('filters')
            if filters is not None:
                if not isinstance(filters,dict) or set(filters)-{'archive','telescope','object','night','since'}:raise ValueError('Невалиден филтър.')
                for k,v in filters.items():number(v,0,4e10) if k=='since' else text(v)
        if channel in ('analysis','preparation'):
            if 'picker' in value:
                picker=value['picker']
                if not isinstance(picker,dict) or set(picker)-{'archive','telescope','object','ids'}:raise ValueError('Невалиден избор на данни.')
                for k in ('archive','telescope','object'):text(picker.get(k,''))
                if picker.get('archive') and picker['archive'] not in {a['id'] for a in self.service.settings.value['archives']}:raise ValueError('Непознат източник.')
                ids(picker.get('ids',[]))
            controls=value.get('controls',{})
            if not isinstance(controls,dict) or set(controls)-CONTROLS[channel]:raise ValueError('Непознати контроли на изгледа.')
            for v in controls.values():
                if isinstance(v,str):text(v)
                elif type(v) is not bool:number(v,-1e8,1e8)
            if channel=='analysis':
                for key in ('reportId','compareId'):
                    ident=value.get(key)
                    if ident is not None:
                        text(ident,100)
                        # Only existing analysis reports can be presented in the
                        # shared view; IDs are never treated as file paths.
                        self.service.quality.load(ident)
                for key in ('openedSaved','compareMode','classicRun'):
                    if key in value and type(value[key]) is not bool:raise ValueError('Невалидно състояние на анализа.')
                if 'phase' in value:choice(value['phase'],('data','action','running','result'))
                if 'mode' in value:choice(value['mode'],('astro','stars'))
                if 'kind' in value:choice(value['kind'],('frames','stacks'))
                if 'view' in value:choice(value['view'],('chart','table','gallery','map','stars','series'))
                for k in ('selected','starIds','columns'):
                    if k in value:ids(value[k])
                for k in ('x','metric','preset'):
                    if k in value:text(value[k],100)
                if 'chartColor' in value:choice(value['chartColor'],('plot','quality','night','ai','classic'))
                if 'frameList' in value:
                    listing=value['frameList']
                    if not isinstance(listing,dict) or set(listing)!={'open','view','filter','sort','direction','query','page'} or type(listing['open']) is not bool:raise ValueError('Невалиден списък с кадри.')
                    choice(listing['view'],('list','thumbs'));choice(listing['filter'],('all','included','excluded','ai'))
                    choice(listing['sort'],('name','night','fwhm','fwhmY','roundness','weightedFwhm','shiftX','shiftY','stars','background','classic','ai'));choice(listing['direction'],('asc','desc'))
                    text(listing['query'],200);number(listing['page'],0,20000)
                    if type(listing['page']) is not int:raise ValueError('Невалидна страница с кадри.')
                if 'plotWindow' in value:
                    window=value['plotWindow']
                    if not isinstance(window,list) or len(window)!=4:raise ValueError('Невалиден прозорец на диаграмата.')
                    for bound in window:number(bound,0,1)
                    if any(not 1/64-1e-9<=window[i+1]-window[i]<=1 for i in (0,2)):raise ValueError('Мащабът на диаграмата е между 100% и 6400%.')
                if 'zoom' in value:number(value['zoom'],1,64)
                if value.get('center') is not None:
                    if set(value['center'])!={'x','y'}:raise ValueError('Невалиден център.')
                    for v in value['center'].values():number(v,-1e15,1e15)
            else:
                if 'phase' in value:choice(value['phase'],('data','action','running','work','export','done'))
                if 'mode' in value:choice(value['mode'],('copy','application','return'))
                if value.get('jobId') is not None:text(value['jobId'],100)
                if 'outputs' in value:ids(value['outputs'])
                export=value.get('exportRequest')
                if export is not None:
                    if not isinstance(export,dict) or set(export)-{'source','resultId','ids','label'}:raise ValueError('Невалиден изглед на изнасянето.')
                    choice(export.get('source'),('quality','timeseries'));text(export.get('resultId'),100);text(export.get('label',''));ids(export.get('ids',[]))
        if channel=='extensions':
            for slot,key in value.items():
                if key is None:continue
                text(key,150);plugin,sep,contribution=key.partition(':')
                if not sep:raise ValueError('Невалидно допълнение.')
                manifest=self.service.plugins.records.get(plugin,{})
                if not self.service.plugins.state(plugin).get('uiAllowed') or not any(c['id']==contribution and c['slot']==slot for c in manifest.get('contributions',[])):raise ValueError('Допълнението не е разрешено.')
        if channel=='viewer':
            if 'frameId' in value:text(value['frameId'],100)
            controls=value.get('controls',{})
            if not isinstance(controls,dict) or set(controls)-{'smooth','cross','grid'} or any(type(v) is not bool for v in controls.values()):raise ValueError('Невалидни контроли на прегледа.')
            transform=value.get('transform',{})
            if not isinstance(transform,dict) or set(transform)-{'zoom','panX','panY','centerX','centerY','rotation','flipX','flipY','fitting'}:raise ValueError('Невалидна ориентация.')
            for k,v in transform.items():
                if k in ('flipX','flipY','fitting'):
                    if type(v) is not bool:raise ValueError('Невалиден превключвател.')
                else:number(v,-1e8,1e8)
            if 'panel' in value and type(value['panel']) is not bool:raise ValueError('Невалиден панел.')
        return result
    def publish(self,p):
        if set(p)-{'clientId','channel','value','baseRevision','sequence'}:raise ValueError('Невалидна публикация.')
        client=text(p.get('clientId'),100)
        if not re.fullmatch(r'[a-zA-Z0-9_-]{12,100}',client):raise ValueError('Невалиден участник.')
        channel=choice(p.get('channel'),CHANNELS);value=self.validate(channel,p.get('value'))
        number(p.get('sequence'),1,1e15);number(p.get('baseRevision',0),0,1e15)
        if len(json.dumps(value).encode())>1_000_000:raise ValueError('Описанието на сесията е твърде голямо.')
        with self.lock:
            current=self.channels.get(channel)
            previous=self.writers.get((client,channel),0)
            if p['sequence']<=previous or current and p.get('baseRevision',0)!=current['revision']:
                return dict(accepted=False,record=copy.deepcopy(current))
            if channel=='viewer' and (not current or not current['value'].get('open') or value.get('frameId')!=current['value'].get('document',{}).get('id')):
                return dict(accepted=False,record=copy.deepcopy(current))
            if len(self.writers)>512:self.writers.clear()
            self.writers[(client,channel)]=p['sequence']
            saved={**(current['value'] if current else {}),**value}
            if 'controls' in value:saved['controls']={**((current or {}).get('value',{}).get('controls',{})),**value['controls']}
            if channel=='analysis' and self.active:saved['phase']='running'
            elif channel=='analysis' and saved.get('phase')=='running':saved['phase']=(current or {}).get('value',{}).get('phase','action')
            if channel=='preparation' and self.preparing:saved['phase']='running'
            elif channel=='preparation' and saved.get('phase')=='running':saved['phase']=(current or {}).get('value',{}).get('phase','action')
            if channel=='viewer':
                self.service.viewer_state.update({k:v for k,v in saved.items() if k in ('transform','controls','panel')})
                saved.update(self.service.viewer_state.presentation())
            return dict(accepted=True,record=self._set(channel,saved,client))
    def preparation_task(self,method,p,start):
        fingerprint=json.dumps([method,p],sort_keys=True)
        with self.lock:
            if self.preparing:
                if self.preparing['fingerprint']==fingerprint:return dict(taskId=self.preparing['taskId'])
                raise ValueError('Подготовката вече работи в общата сесия.')
            self.preparing=dict(taskId=None,fingerprint=fingerprint)
        try:result=start()
        except Exception:
            with self.lock:self.preparing=None
            raise
        with self.lock:
            if not result.get('taskId'):self.preparing=None;return result
            task_id=result['taskId'];self.preparing=dict(taskId=task_id,fingerprint=fingerprint)
            value=copy.deepcopy(self.channels.get('preparation',{}).get('value',{}));value.update(phase='running',taskId=task_id,problem=None);self._set('preparation',value)
        threading.Thread(target=self._watch_preparation,args=(task_id,method,p),daemon=True).start()
        return result
    def sources(self,p):
        from pathlib import Path
        job=self.service.workflow.get(text(p.get('id'),100));rows=[]
        for ident in job['ids']:
            r=self.service.workflow.record(ident);meta=r.get('meta',{})
            rows.append(dict(id=ident,name=Path(r.get('relative',ident)).name,**{k:meta.get(k) for k in ('object','night','frames','totalExposure')}))
        return dict(rows=rows)
    def _watch_preparation(self,task_id,method,p):
        while True:
            with self.service.jobs.lock:
                task=self.service.jobs.tasks.get(task_id)
                if task and task['state']!='running':task=copy.copy(task);break
            time.sleep(.15)
        with self.lock:
            if not self.preparing or self.preparing['taskId']!=task_id:return
            self.preparing=None;value=copy.deepcopy(self.channels['preparation']['value']);value['taskId']=None
            if task['state']=='completed':
                if method.endswith('.start'):value.update(phase='work',jobId=task['result']['id'])
                elif method.endswith('.finish'):value.update(phase='data',jobId=None,outputs=[],picker={**value.get('picker',{}),'ids':[]})
                else:value.update(phase='done',doneCount=len(task['result'].get('files',[])))
            else:value.update(phase='work' if value.get('jobId') else 'action',problem=task.get('problem','Подготовката е спряна.'))
            self._set('preparation',value)
    def preparation_done(self,ident):
        with self.lock:
            value=copy.deepcopy(self.channels.get('preparation',{}).get('value',{}))
            if value.get('jobId') not in (None,ident):return
            value.update(phase='data',jobId=None,outputs=[],picker={**value.get('picker',{}),'ids':[]});self._set('preparation',value)
    def export(self,p):
        if set(p)-{'source','resultId','ids','label','png'}:raise ValueError('Невалиден документ за изнасяне.')
        self.validate('preparation',dict(exportRequest={k:v for k,v in p.items() if k!='png'}))
        reports=self.service.timeseries.results if p.get('source')=='timeseries' else self.service.quality.results
        if p.get('resultId') not in reports:raise ValueError('Непознат отчет.')
        if p.get('png'):
            import base64
            png=p['png']
            if not isinstance(png,str) or len(png)>4_000_000 or not png.startswith('data:image/png;base64,'):raise ValueError('Невалидно превю на диаграмата.')
            data=base64.b64decode(png.split(',',1)[1],validate=True)
            if not data.startswith(b'\x89PNG\r\n\x1a\n'):raise ValueError('Очаква се PNG диаграма.')
        with self.lock:self.export_document=copy.deepcopy(p)
        return dict(accepted=True)
    def track(self,method,p,start):
        operation=method.rsplit('.',1)[-1]
        if operation=='run':
            choice(p.get('mode','astro'),('astro','stars'));choice(p.get('kind','frames'),('frames','stacks'))
        fingerprint=json.dumps([method,p],sort_keys=True)
        with self.lock:
            if self.active:
                if self.active['fingerprint']==fingerprint:return dict(taskId=self.active['taskId'])
                raise ValueError('В общата сесия вече работи анализ. Изчакай го или го спри от индикатора.')
            result=start();task_id=result['taskId'];self.active=dict(taskId=task_id,fingerprint=fingerprint)
            value=copy.deepcopy(self.channels.get('analysis',{}).get('value',{}))
            if operation=='run':
                for key in ('reportId','coverageTask','referenceTask','seriesTask','decisionRevision'):value.pop(key,None)
                value.update(mode=choice(p.get('mode','astro'),('astro','stars')),kind=p.get('kind','frames'),selected=[],view='chart',classicRun=False)
                self._set('route',dict(section='analysis'))
            value.update(phase='running',taskId=task_id,operation=operation,problem=None)
            self._set('analysis',value)
        threading.Thread(target=self._watch,args=(task_id,operation),daemon=True,name='AstroArchive-shared-analysis').start()
        return result
    def _watch(self,task_id,operation):
        while True:
            with self.service.jobs.lock:
                task=self.service.jobs.tasks.get(task_id)
                if task and task['state']!='running':task=copy.copy(task);break
            time.sleep(.15)
        with self.lock:
            if not self.active or self.active['taskId']!=task_id:return
            self.active=None;value=copy.deepcopy(self.channels['analysis']['value']);value['taskId']=None
            if task['state']=='completed':
                result=task['result'];value.update(phase='result',problem=None)
                if operation=='run':value['reportId']=result['id'];value['view']='chart'
                elif operation in ('coverage','reference','timeseries'):
                    key,view={'coverage':('coverageTask','map'),'reference':('referenceTask','stars'),'timeseries':('seriesTask','series')}[operation]
                    value[key]=task_id;value['view']=view
            else:value.update(phase='result' if value.get('reportId') else 'action',problem=task.get('problem','Анализът е спрян.'))
            self._set('analysis',value)
    def analysis_state(self):
        """Return an isolated view of the current analysis presentation."""
        with self.lock:
            return copy.deepcopy(self.channels.get('analysis', {}).get('value', {}))

    def inspection_result(self, report_id, warning_ids, *, highlight=False, present=False):
        """Apply a plugin result without taking over a running or newer analysis.

        Reports and manual decisions belong to the analysis service. This method
        only updates presentation and owns all channel revisions and events.
        """
        with self.lock:
            if self.active:
                if present:
                    raise ValueError('Изчакай текущия анализ, преди да покажеш инспекционния отчет.')
                return False
            value = self.analysis_state()
            changed = False
            if highlight and value.get('reportId') == report_id:
                value.update(selected=list(warning_ids), view='chart',
                             controls={**value.get('controls', {}), 'q-plot-warnings': True})
                self._set('analysis', value)
                changed = True
            if present:
                value.update(phase='result', mode='astro', kind='frames', view='chart',
                             reportId=report_id, taskId=None, selected=[], problem=None)
                self._set('analysis', value)
                extensions = copy.deepcopy(self.channels.get('extensions', {}).get('value', {}))
                extensions['analysis'] = None
                self._set('extensions', extensions)
                self._set('route', dict(section='analysis'))
                changed = True
            return changed

    def decisions(self,changed_ids=None):
        with self.lock:
            record=self.channels.get('analysis')
            if record:
                value=copy.deepcopy(record['value']);value['decisionRevision']=value.get('decisionRevision',0)+1
                if changed_ids is not None:
                    changed=set(changed_ids);value['selected']=[i for i in value.get('selected',[]) if i not in changed]
                self._set('analysis',value)
    def resource(self,p):
        name=choice(p.get('name'),('coverage','reference','series','export'))
        if name=='export':
            with self.lock:return copy.deepcopy(self.export_document)
        with self.lock:task_id=self.channels.get('analysis',{}).get('value',{}).get(name+'Task')
        if not task_id:raise ValueError('В общата сесия няма такъв резултат.')
        with self.service.jobs.lock:
            task=self.service.jobs.tasks.get(task_id)
            if not task or task['state']!='completed':raise ValueError('Резултатът още не е готов.')
            return task['result']
    def viewer(self,document=None,closed=False):
        with self.lock:
            previous=self.channels.get('viewer',{}).get('value',{})
            if closed:
                self._set('viewer',dict(open=False));self.service.events.emit('viewer.closed');return
            changed=previous.get('document',{}).get('id')!=document['id']
            self._set('viewer',{**(self.service.viewer_state.presentation() if changed else previous),'open':True,'document':document})
