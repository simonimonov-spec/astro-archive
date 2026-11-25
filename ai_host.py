"""Read-only SDK adapter. Never accepts a client path or returns one."""
from __future__ import annotations
import hashlib
import threading
import uuid
import collections
from pathlib import Path
from .pipeline import grouping
from . import evaluator,frame_checks,problems,score_formula,series
from .analyzers.storage import utc_now, atomic_write_json, read_json
from .ai_service import AIService, APIError, require


REASON_TYPE={'light':'gradient','trails':'satellite','artifacts':'flare'}


def reason_problems(outcome):
    """Problems from evaluator reasons: only factors that map to a problem type, by the score thresholds of
    frame_checks (high >= .85, medium >= .6). 'overall', noise, defects and stars never become problems."""
    out=[];stack=outcome['recommendation']['stack']
    for reason in outcome['recommendation']['reasons']:
        kind=REASON_TYPE.get(reason.get('factor'))
        if kind is None or reason.get('risk') is None:continue
        score=_clamp(reason['risk'])
        severity='high' if score>=.85 else 'medium' if score>=.6 else None
        if severity is None:continue
        if stack=='do_not_stack':severity='high'
        out.append(problems.problem(kind,score,severity,('AI оценител · '+reason['message'])[:400],dict(factor=reason['factor'])))
    return out


def row_view(row):
    """Row with its stored measurements flattened in, as the frame and series checks expect."""
    view={k:v for k,v in (row.get('measurements') or {}).items() if v is not None}
    view.update(row)
    return view


def _clamp(value):
    return min(1.,max(0.,float(value)))


def _add(found,problem):
    old=found.get(problem['type'])
    if old is None or problem['score']>old['score']:found[problem['type']]=problem


def _is_cutoff(problem):
    return problem.get('type')=='quality' and (problem.get('measures') or {}).get('cutoff') is True


def rebuild_row(row,options):
    """Recompute index, problems and warnings from the stored frame/series problems and factor risks.

    A frame without a reliable evaluator quality (or with an error) never gets an index; its problems
    are still listed and warned about. 'low' problems (frames of a series event that are under the threshold
    by themselves) stay in row['problems'] for details but are no risk and no warning. A frame-check 'quality' problem (severe degradation) is the
    'severe' risk; the cutoff 'quality' problem made here (measures.cutoff) is never fed back."""
    base=[p for p in list(row.get('frameProblems') or [])+list(row.get('seriesProblems') or []) if not _is_cutoff(p)]
    outcome=row.get('aiResult')
    quality=((outcome or {}).get('overall') or {}).get('quality') if outcome else None
    value=None
    if quality is not None and not row.get('error'):
        risks={}
        for p in base:
            if p.get('severity')=='low':continue
            key='severe' if p['type']=='quality' else p['type']
            risks[key]=max(risks.get(key,0.),p['score'])
        for factor in outcome.get('factors',[]):
            key=score_formula.FACTOR_TO_RISK.get(factor.get('id'))
            if key and factor.get('risk') is not None:risks[key]=max(risks.get(key,0.),_clamp(factor['risk']))
        value=score_formula.index(risks)
    row['aiQuality']=value
    row['qualityProblem']=value is not None and value<=options.get('qualityCutoff',35)
    found=[p for p in base if not (row['qualityProblem'] and p['type']=='quality')]
    if row['qualityProblem']:
        found.append(problems.problem('quality',round(1-value/100,4),'high',f'Напълно проблемен според AI: индекс {value:.1f}% е под зададения праг. Провери ръчно.',dict(aiQuality=round(value,1),cutoff=True)))
    row['problems']=found
    row['warnings']=[dict(message=p['message'][:400],type=p['type'],score=p['score'],severity=p['severity'],origin='ai',scope=p.get('scope','frame')) for p in found if p.get('severity')!='low']  # low: details only
    if outcome and not row.get('error'):
        from .pipeline import status_for
        row['status']=status_for(row)
    return row


def merge_series(result,options):
    """Whole-series check over the rows; its problems join each frame's own and the index is recomputed."""
    analysis=series.analyze([row_view(r) for r in result['rows']],options)
    result['series']=analysis
    for row in result['rows']:
        row['seriesProblems']=analysis['perFrame'].get(row['id'],[])
        rebuild_row(row,options)
    return result


class AIHost:
    def __init__(self, inspector):
        self.inspector=inspector
        self.visual_cache=collections.OrderedDict();self.network_cache={};self.visual_lock=threading.RLock()
        self.service=AIService(inspector.folder/'AI',resolve=self.resolve,evaluate=self.evaluate,frames=self.frames,frame_features=self.frame_features,frame_visual=self.frame_visual,label_snapshot=self.label_snapshot,show_frame=self.show_frame,describe_frame=self.describe_frame)

    def describe_frame(self,ident):
        source=self.inspector.ctx.inspection('source',dict(id=ident))
        return {k:source.get(k) for k in ('name','object','telescope','night','filter')}

    def label_snapshot(self,p):
        frame=self.resolve(p);source=self.inspector.ctx.inspection('source',dict(id=frame['id']))
        context={k:source.get(k,'') for k in ('object','telescope','night','filter')}
        return dict(frame=frame,context=context,name=source.get('name',frame['id']))

    def show_frame(self,p):
        frame=self.resolve(p)
        return self.inspector.ctx.inspection('show',dict(id=frame['id']))

    def frame_visual(self,p):
        from . import visual_data
        from .analyzers.storage import canonical
        ident=p.get('frameId');checksum=p.get('hash');config=dict(p.get('config') or visual_data.DEFAULT)
        require(isinstance(ident,str) and isinstance(checksum,str),'invalid_request','Нужни са frameId и hash.')
        visual_data.configuration(config);key=(ident,checksum,canonical(config))
        with self.visual_lock:
            if key not in self.visual_cache:
                frame=self.resolve(dict(frameId=ident,hash=checksum))
                source=self.inspector.ctx.inspection('source',dict(id=ident));path=Path(source['path'])
                description=visual_data.describe(path,config)
                require((description['sourceSize'],description['sourceMtime'])==(frame['sourceSize'],frame['sourceMtime']),'conflict','FITS е променен.')
                self.visual_cache[key]=(path,description,frame)
                while len(self.visual_cache)>4:self.visual_cache.popitem(last=False)
            path,description,frame=self.visual_cache[key];self.visual_cache.move_to_end(key)
            source=self.inspector.ctx.inspection('source',dict(id=ident))
            require((source['size'],source['mtime'])==(frame['sourceSize'],frame['sourceMtime']),'conflict','FITS е променен.')
            if 'index' not in p:return dict(frame=frame,description=description)
            index=p['index'];array=visual_data.tile(path,description,index)
            if index==len(description['tiles'])-1:self.resolve(dict(frameId=ident,hash=checksum))
            return dict(frame=frame,index=index,pixels=visual_data.encode(array))

    def frame_features(self,p):
        frame=self.resolve(p)
        row=self._measurement(frame)
        require(row is not None and row.get('analyzerData') is not None,'unavailable','Изпълни AI проверка за кадъра.')
        require((frame['sourceSize'],frame['sourceMtime'])==(row.get('sourceSize'),row.get('sourceMtime')),'conflict','Измерването е остаряло.')
        meta=row.get('meta',{})
        context={k:meta[k] for k in ('object','telescope','filter','night','exposure','gain','pixelScale','zeropoint') if k in meta}
        context['group']='|'.join(str(meta.get(k,'')) for k in ('object','telescope','night','filter'))
        from . import resnet_model
        return dict(frame=frame, context=context, features=resnet_model.measurement_features(dict(row,**(row.get('measurements') or {})),row['analyzerData']['factors']))

    def evaluate_row(self,row,frame,rid,model,cancel=None):
        prediction=None
        if model is not None:
            try:
                from . import resnet_model,visual_data
                resnet_model.validate(model)
                key=model['weights']['hash']
                with self.visual_lock:
                    if key not in self.network_cache:
                        weights=self.service.registry.weights_path(model['modelId'],model['version']).read_bytes()
                        # CPU inference does not take over the trainer's GPU.
                        self.network_cache={key:resnet_model.load_weights(model,weights)}
                    network=self.network_cache[key]
                parameters=dict(frameId=frame['id'],hash=frame['hash'],config=model['preprocessing'])
                description=self.frame_visual(parameters)['description']
                source=dict(row,**(row.get('measurements') or {}))
                measured=resnet_model.measurement_features(source,row['analyzerData']['factors'])
                prediction=resnet_model.predict(network,model,description,lambda i:visual_data.decode(self.frame_visual(dict(parameters,index=i))['pixels'],model['preprocessing']['tileSize']),measured,cancel=cancel)
            except InterruptedError:raise
            except (ValueError,OSError,ImportError,RuntimeError,KeyError):prediction=None
        return evaluator.evaluate(row,frame,request_id=rid,model=model,analyzed=row['analyzerData'],prediction=prediction)

    def _measurement(self,frame):
        row=next((r for r in (self.inspector.result or {}).get('rows',[]) if r['id']==frame['id'] and (r.get('sourceSize'),r.get('sourceMtime'))==(frame['sourceSize'],frame['sourceMtime'])),None)
        if row is not None and row.get('analyzerData') is not None:return row
        path=self.service.root/'measurements'/(frame['hash'][7:]+'.json')
        saved=read_json(path,{})
        if saved.get('id')==frame['id'] and (saved.get('sourceSize'),saved.get('sourceMtime'))==(frame['sourceSize'],frame['sourceMtime']):return saved
        return None

    def frames(self):
        return [dict(frameId=r['id'], name=r['name'], night=r.get('night',''), quality=r.get('aiQuality')) for r in (self.inspector.result or {}).get('rows',[])]

    def resolve(self,p):
        # Remote and local clients use catalog IDs. Raw upload/path is deliberately
        # unsupported; the host's configured archive roots remain authoritative.
        require('path' not in p,'invalid_request','Използвай архивен frameId.')
        ident=p.get('frameId')
        require(isinstance(ident,str) and 1<=len(ident)<=80,'invalid_request','Невалиден frameId.')
        try: source=self.inspector.ctx.inspection('source',dict(id=ident))
        except (ValueError,FileNotFoundError): raise APIError('not_found','Кадърът не е намерен.')
        require(source.get('kind')=='frame','invalid_request','Нужен е отделен архивен кадър.')
        path=Path(source['path']); before=path.stat(); digest=hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''): digest.update(block)
        after=path.stat()
        require((before.st_size,before.st_mtime_ns)==(after.st_size,after.st_mtime_ns)==(source['size'],source['mtime']), 'conflict','Кадърът е променен при четене.')
        checksum='sha256:'+digest.hexdigest()
        require(p.get('hash',checksum)==checksum,'conflict','Hash на кадъра не съвпада.')
        return dict(id=ident, hash=checksum, sourceSize=after.st_size, sourceMtime=after.st_mtime_ns)

    def active_model(self):
        try:
            active=self.service.registry.load_active()
            return active['model'] if active else None
        except ValueError:
            # Evaluator records the fallback limitation instead of mislabelling it.
            return {}

    def engine_status(self):
        try:
            from . import resnet_model
            import importlib.util
            model=self.active_model()
            resnet_model.validate(model)
            if importlib.util.find_spec('torch') is None:raise ValueError('PyTorch липсва.')
            return 'избран обучен ResNet-18; точността върху реален архив не е потвърдена'
        except (ValueError,TypeError,ImportError):
            return 'евристична оценка; няма използваем обучен и активен ResNet'

    def attach(self,result, request_id=None,cancel=None,progress=None):
        model=self.active_model();options=self.inspector.options
        views={r['id']:row_view(r) for r in result['rows']};groups={}
        for row in result['rows']:
            try:groups.setdefault(grouping(row),[]).append(views[row['id']])
            except (KeyError,AttributeError,TypeError):pass
        for index,row in enumerate(result['rows']):
            if cancel is not None and cancel.is_set():raise InterruptedError('ResNet оценката е отменена.')
            if progress:progress(index,len(result['rows']))
            row.update(objectClass=None,frameProblems=[],seriesProblems=[],problems=[])
            if row.get('error'): continue
            try:
                frame=self.resolve(dict(frameId=row['id']))
                require((frame['sourceSize'],frame['sourceMtime'])==(row.get('sourceSize'),row.get('sourceMtime')),'conflict','Измерването е остаряло.')
                data=row.get('analyzerData')
                require(data is not None,'unavailable','Нужни са нови измервания.')
                frame.update(width=row['width'],height=row['height'])
                outcome=self.evaluate_row(row,frame,request_id or uuid.uuid4().hex,model,cancel)
                saved={k:row.get(k) for k in ('id','sourceSize','sourceMtime','width','height','meta','analyzerData','referenceFrames','measurements')}
                atomic_write_json(self.service.root/'measurements'/(frame['hash'][7:]+'.json'),saved)
                row['aiResult']=outcome
                value=outcome['overall']['quality']
                row['scores']=outcome['events']
                row['engineKind']=outcome['engine']['kind']
                row['qualityReason']='Невронна мрежа' if row['engineKind']=='neural' else 'Евристична оценка'
                try:key=grouping(row)
                except (KeyError,AttributeError,TypeError):key=None
                found={p['type']:p for p in frame_checks.check(views[row['id']],groups.get(key,[views[row['id']]]),options)}
                for reason_problem in reason_problems(outcome):_add(found,reason_problem)
                for event,probability in outcome['events'].items():
                    if probability is not None and probability>=options.get('threshold',.72) and event in problems.TYPES:
                        _add(found,problems.problem(event,_clamp(probability),'medium','ResNet кандидат за '+event+'; провери ръчно.',dict(probability=round(float(probability),4))))
                row['frameProblems']=list(found.values())
                rebuild_row(row,options)
            except (APIError, OSError, ValueError, KeyError):
                row.pop('aiResult',None)
                row.update(aiQuality=None,qualityProblem=False,warnings=[],problems=[],frameProblems=[],scores={},status='Няма валидна AI оценка; преизчисли кадъра.')
        if progress:progress(len(result['rows']),len(result['rows']))
        return result

    def evaluate(self,frame,cancel,rid):
        require(not cancel.is_set(),'unavailable','Оценката е отменена.')
        row=self._measurement(frame)
        if row is None:
            inspector=self.inspector
            with inspector.training_lock:
                require(not inspector.busy,'unavailable','Изчакай текущата AI проверка.')
                inspector.busy=True
            try:
                source=inspector.ctx.inspection('source',dict(id=frame['id']))
                filters={k:source[k] for k in ('archive','telescope','object','night','filter')};filters['kind']='frame'
                rows=inspector.ctx.host('python.archive.query',dict(filters=filters))['rows']
                require(0<len(rows)<=1500,'too_large','Групата трябва да съдържа до 1500 кадъра.')
                options=dict(inspector.options,force=False,model=inspector.starter,qualityModel=None)
                result=inspector.pipeline.run([r['id'] for r in rows],None,options,cancel,lambda *args:None)
                self.attach(result,request_id=rid,cancel=cancel)
                row=next((r for r in result['rows'] if r['id']==frame['id']),None)
            finally:inspector.busy=False
        require(row is not None and row.get('analyzerData') is not None,'unavailable','Първо измери кадъра с AI проверката в Анализ.')
        require((frame['sourceSize'],frame['sourceMtime'])==(row.get('sourceSize'),row.get('sourceMtime')),'conflict','Измерването е остаряло.')
        self.resolve(dict(frameId=frame['id'],hash=frame['hash']))
        frame=dict(frame,width=row['width'],height=row['height'])
        return self.evaluate_row(row,frame,rid,self.active_model(),cancel)
