"""Network policy, owned and unloaded with the Remote Operation plugin."""
import copy

READ={'archive.list','archive.navigation','archive.rows','archive.filtered','archive.summary','archive.source.nights','archive.provenance',
      'archive.disk','resources.read','archive.pending','archive.audit.status','python.archive.query','python.archive.facets','python.archive.info',
      'python.analysis.result','python.analysis.thumbnail','python.analysis.presets','python.analysis.qualityConfig','python.preparation.state','python.preparation.outputs',
      'python.artifact.list','python.task','activity.state','function.image','image.open','viewer.close','viewer.header','viewer.neighbor','viewer.pixel',
      'viewer.presented','viewer.render','analysis.trash.list','python.trash.list','python.trash.preview'}
ACTION={'python.analysis.run','python.analysis.coverage','python.analysis.reference','python.analysis.timeseries','python.archive.decide',
        'python.archive.trash','python.trash.restore','analysis.trash.restore','archive.summary.refresh','archive.summary.stop',
        'archive.read.stop','python.task.cancel','activity.stop','activity.dismiss','python.artifact.create','python.artifact.preview'}
SIRIL={'prepare','start','select','configure','accept','retry','skip','merge','restart','stop','preview','graph','navigate','status'}
CHART_INSPECTOR=('frame-inspector-ai','chart')
TRAINER=('ai-training','rating')

class Gateway:
    def __init__(self,context):self.ctx=context
    def config(self):
        value=copy.deepcopy(self.ctx.remote('configuration'));value['programs']=[]
        value['plugins']=[p for p in value.get('plugins',[]) if p['id'] in ('siril-auto','remote-operation',CHART_INSPECTOR[0],TRAINER[0])]
        for plugin in value['plugins']:
            slot={'siril-auto':'preparation','remote-operation':'header',CHART_INSPECTOR[0]:'analysis-tools',TRAINER[0]:'viewer-tools'}[plugin['id']]
            plugin['contributions']=[c for c in plugin.get('contributions',[]) if c['slot']==slot and (plugin['id']!=CHART_INSPECTOR[0] or c['id']==CHART_INSPECTOR[1])]
        return value
    def call(self,method,p):
        if not isinstance(method,str) or not isinstance(p,dict):raise ValueError('Невалидна дистанционна команда.')
        if method=='app.bootstrap':
            value=copy.deepcopy(self.ctx.remote('bootstrap'));value['ui']=self.config();return value
        if method=='plugin.archives':return dict(archives=self.ctx.remote('bootstrap')['archives'])
        if method in ('ui.load','ui.cancelPreview'):return self.config()
        if method in ('ui.session.get','ui.session.publish','ui.session.resource','ui.session.export','ui.session.sources'):
            if method=='ui.session.publish' and p.get('channel')=='preparation' and isinstance(p.get('value'),dict):
                p=copy.deepcopy(p)
                if isinstance(p['value'].get('controls'),dict):p['value']['controls'].pop('w-program',None)
            if method=='ui.session.publish' and p.get('channel')=='extensions':
                value=p.get('value',{})
                if value.get('analysis') is not None or value.get('preparation') not in (None,'siril-auto:automatic-stacking'):raise PermissionError('Допълнението не е достъпно дистанционно.')
            return self.ctx.remote('core',dict(method=method,params=p))
        if method=='ui.appearance.get':return self.config()
        if method=='ui.appearance.save':
            self.ctx.remote('core',dict(method=method,params=p))
            return self.config()
        if method=='python.settings.get':
            value=copy.deepcopy(self.ctx.remote('settings'));value['programs']=[];value.pop('pluginState',None);value.pop('approvedPluginPermissions',None);return value
        if method=='plugin.ui.state':return dict(plugins=self.config().get('plugins',[]))
        if method in ('plugin.ui.input','plugin.input','plugin.ui.activate'):
            if (p.get('pluginId'),p.get('contributionId'))==TRAINER:
                if method!='plugin.ui.activate' and p.get('element') not in ('perfect','excellent','very_good','good','fair','poor','bad','cloud','satellite','light','elongation_mild','elongation_moderate','elongation_severe','clear'):raise PermissionError('Позволени са само ръчни оценки от отворения преглед.')
                return self.ctx.remote('core',dict(method=method,params=p))
            if (p.get('pluginId'),p.get('contributionId'))==CHART_INSPECTOR:
                if method=='plugin.ui.activate':return dict(accepted=True)
                elements=self.ctx.remote('document',dict(pluginId=CHART_INSPECTOR[0],contributionId=CHART_INSPECTOR[1]))
                target=next((e for e in elements if e['id']==p.get('element')),None)
                if not target or target.get('command') not in ('chart_run','chart_cutoff'):raise PermissionError('Дистанционно е разрешена само AI проверката и прагът на текущата диаграма.')
                return self.ctx.remote('core',dict(method=method,params=p))
            if p.get('pluginId')!='siril-auto' or p.get('contributionId')!='automatic-stacking':raise PermissionError('Дистанционно се управлява само разрешеното автоматично стакване.')
            if method!='plugin.ui.activate':
                elements=self.ctx.remote('document',dict(pluginId='siril-auto',contributionId='automatic-stacking'))
                def find(items):
                    for e in items:
                        if e['id']==p.get('element'):return e
                        match=find(e.get('children',[]))
                        if match:return match
                target=find(elements)
                if not target or target.get('command') not in SIRIL:raise PermissionError('Това действие изисква локалния компютър.')
                def fields(items):
                    return {e['id']:e.get('value') for e in items if e['type']!='group'}|{k:v for e in items for k,v in fields(e.get('children',[])).items()}
                current=fields(elements)
                for name in ('executable','dark','flat','bias'):
                    if name in p.get('values',{}) and p['values'][name]!=current.get(name):raise PermissionError('Пътищата към Siril и master файловете се настройват на хоста.')
            return self.ctx.remote('core',dict(method=method,params=p))
        if method not in READ|ACTION:raise PermissionError('Тази функция изисква локалното приложение.')
        if method=='python.analysis.presets' and 'presets' in p:raise PermissionError('Общите настройки се променят на хоста.')
        if method in ('python.artifact.create','python.artifact.preview') and set(p)-{'source','resultId','format','options','ids','png','label'}:raise PermissionError('Невалидни параметри за отчет.')
        result=self.ctx.remote('core',dict(method=method,params=p))
        if method=='python.archive.query' and p.get('view')=='picker':
            fields=['id','kind','telescope','object','night','totalExposure','filter','decision']
            return dict(rowFields=fields,rowValues=[[r.get(k) for k in fields] for r in result['rows']],statistics=result['statistics'])
        return result
    def events(self,after):
        return self.event_batch(after)['events']
    def event_batch(self,after):
        through=self.ctx.remote('sequence')
        raw=[e for e in self.ctx.remote('events',dict(after=after)) if e['sequence']<=through][:128]
        cursor=raw[-1]['sequence'] if raw else max(after,through)
        def visible(e):
            if not e['name'].startswith('plugin.ui.'):return True
            return e.get('plugin')=='siril-auto' and e.get('contribution') in (None,'automatic-stacking') or e.get('plugin')=='remote-operation' and e.get('contribution')=='link-status' or e.get('plugin')==CHART_INSPECTOR[0] and e.get('contribution') in (None,CHART_INSPECTOR[1])
        events=[e for e in raw if visible(e)]
        latest={}
        for e in events:
            if e['name'] in ('activity.changed','archive.summary.changed','plugin.ui.changed'):latest[(e['name'],e.get('archiveId'),e.get('plugin'),e.get('contribution'))]=e['sequence']
        events=[e for e in events if e['name'] not in ('activity.changed','archive.summary.changed','plugin.ui.changed') or latest[(e['name'],e.get('archiveId'),e.get('plugin'),e.get('contribution'))]==e['sequence']]
        return dict(events=events,sequence=cursor,more=bool(raw and cursor<through))
    def remote(self,action,parameters=None):
        p=parameters or {}
        if action=='rpc':return self.call(p['method'],p.get('params',{}))
        if action=='events':return self.events(int(p.get('after',0)))
        if action=='event_batch':return self.event_batch(int(p.get('after',0)))
        if action=='asset' and p['route'].startswith('plugin-image/'):
            parts=p['route'].split('/')
            if len(parts)!=3 or parts[1]!='siril-auto':raise PermissionError('Този ресурс не е достъпен дистанционно.')
            def contains(items):return any(e.get('assetId')==parts[2] or contains(e.get('children',[])) for e in items)
            if not contains(self.ctx.remote('document',dict(pluginId='siril-auto',contributionId='automatic-stacking'))):raise PermissionError('Прегледът не е в разрешения панел.')
        return self.ctx.remote(action,p)
