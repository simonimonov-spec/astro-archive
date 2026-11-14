"""Scoped AI facade. Host supplies read-only frame resolution and evaluation.

No model training occurs here. Training tickets are leased to an external
trainer; the completed candidate is verified and stored, never activated.
"""
from __future__ import annotations
import collections
import copy
import hashlib
import hmac
import json
import secrets
import threading
import time
import uuid
import base64
from pathlib import Path
from .analyzers.audit import AuditLog
from .analyzers.dataset import DatasetStore, DatasetError, validate_example
from .analyzers.registry import ModelRegistry, RegistryError
from .analyzers.storage import StorageError, atomic_write_json, canonical, component, identifier, read_json, utc_now
from . import evaluator

CAP = 2_000_000
SCOPES = {'ai:evaluate', 'ai:read', 'ai:pixel:read', 'ai:dataset:read', 'ai:dataset:write', 'ai:model:read', 'ai:model:activate', 'ai:train:submit'}
OPERATIONS = {
    'capabilities':'ai:read', 'evaluate':'ai:evaluate', 'result':'ai:evaluate', 'cancel':'ai:evaluate',
    'dataset.list':'ai:dataset:read', 'dataset.get':'ai:dataset:read', 'labels.validate':'ai:dataset:read',
    'dataset.create':'ai:dataset:write', 'dataset.append':'ai:dataset:write', 'dataset.freeze':'ai:dataset:write',
    'model.list':'ai:model:read', 'model.active':'ai:model:read', 'model.activate':'ai:model:activate', 'model.rollback':'ai:model:activate',
    'training.submit':'ai:train:submit', 'training.status':'ai:train:submit', 'training.cancel':'ai:train:submit',
    'training.claim':'ai:train:submit', 'training.complete':'ai:train:submit', 'training.fail':'ai:train:submit',
    'audit.list':'ai:read', 'frames.list':'ai:read', 'frame.get':'ai:read', 'frame.features':'ai:read',
    'frame.visual':'ai:pixel:read', 'training.weights.put':'ai:train:submit',
    'labeling.session':'ai:dataset:read','labeling.get':'ai:dataset:read','labeling.list':'ai:dataset:read','labeling.quality':'ai:dataset:read',
    'labeling.select':'ai:dataset:write','labeling.save':'ai:dataset:write','labeling.fork':'ai:dataset:write','labeling.open':'ai:read',
    'labeling.policy':'ai:dataset:read','labeling.policy.set':'ai:dataset:write','labeling.policy.reset':'ai:dataset:write',
}


class APIError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def require(condition, code, message):
    if not condition:
        raise APIError(code, message)


def page(items, p):
    limit = p.get('limit', 100)
    require(type(limit) is int and 1 <= limit <= 100, 'invalid_request', 'limit: 1..100')
    cursor = p.get('cursor')
    require(cursor is None or (isinstance(cursor, str) and cursor.isascii() and cursor.isdigit()), 'invalid_request', 'Невалиден cursor.')
    start = int(cursor or 0)
    require(start <= len(items), 'invalid_request', 'Невалиден cursor.')
    result = items[start:start+limit]
    return dict(items=result, next=str(start+len(result)) if start+len(result)<len(items) else None, total=len(items))


class AIService:
    def __init__(self, root, *, resolve=None, evaluate=None, frames=None, frame_features=None,frame_visual=None,label_snapshot=None,show_frame=None,describe_frame=None):
        self.root = Path(root)
        self.audit = AuditLog(self.root)
        self.registry = ModelRegistry(self.root, self.audit)
        self.resolve = resolve
        self.evaluate_frame = evaluate
        self.frames = frames or (lambda: [])
        self.frame_features = frame_features
        self.frame_visual=frame_visual
        self.lock = threading.RLock()
        self.jobs = {}
        self.workers = set()
        self.rates = {}
        self.closed = False
        self.tokens_path = self.root/'tokens.json'
        self.tokens = read_json(self.tokens_path, {'tokens': []})['tokens']
        from .labeling import Labeling
        self.labeling=Labeling(self,label_snapshot,show_frame,describe_frame)

    def issue_token(self, actor, scopes):
        """Trusted local provisioning only; intentionally not an API operation."""
        identifier(actor, 'actor')
        require(set(scopes) <= SCOPES, 'invalid_request', 'Невалиден scope.')
        token = secrets.token_urlsafe(32)
        with self.lock:
            require(len(self.tokens) < 100, 'conflict', 'Достигнат лимит за токени.')
            self.tokens.append(dict(actor=actor, digest=hashlib.sha256(token.encode()).hexdigest(), scopes=sorted(scopes)))
            atomic_write_json(self.tokens_path, dict(tokens=self.tokens))
        return token

    def revoke_token(self, token):
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.lock:
            for job in self.jobs.values():
                if job['owner']==digest and job['state'] in ('queued','running'):
                    job['cancel'].set();job['state']='cancelled'
            self.tokens = [t for t in self.tokens if not hmac.compare_digest(t['digest'], digest)]
            atomic_write_json(self.tokens_path, dict(tokens=self.tokens))

    def authenticate(self, token):
        require(isinstance(token, str) and 16 <= len(token) <= 256, 'unauthorized', 'Нужен е AI bearer token.')
        digest = hashlib.sha256(token.encode()).hexdigest()
        match = None
        for record in self.tokens:
            if hmac.compare_digest(record['digest'], digest):
                match = record
        require(match is not None, 'unauthorized', 'Невалиден AI bearer token.')
        return match

    def call(self, p):
        request_id = uuid.uuid4().hex
        try:
            require(isinstance(p, dict), 'invalid_request', 'Нужен е JSON обект.')
            if 'requestId' in p:
                identifier(p['requestId'], 'requestId'); request_id = p['requestId']
            require(len(canonical(p).encode()) <= CAP, 'too_large', 'Заявката е над 2 MB.')
            require(p.get('schema', 'inspector.ai.v1') == 'inspector.ai.v1', 'invalid_request', 'Неподдържана API схема.')
            with self.lock:
                require(not self.closed, 'unavailable', 'Оценителят е спрян.')
                actor = self.authenticate(p.get('token'))
                op = p.get('operation')
                require(isinstance(op, str) and op in OPERATIONS, 'invalid_request', 'Непозната операция.')
                require(OPERATIONS[op] in actor['scopes'], 'forbidden_scope', 'Токенът няма нужното право.')
                self._rate(actor, op)
                self._expire()
                data = self._invoke(op, p, actor, request_id)
            result = dict(ok=True, requestId=request_id, result=data)
            require(len(canonical(result).encode()) <= CAP, 'too_large', 'Резултатът е над 2 MB; намали страницата.')
            return result
        except APIError as error:
            return dict(ok=False, requestId=request_id, error=dict(code=error.code, message=str(error)))
        except (DatasetError, RegistryError, StorageError, KeyError, TypeError, ValueError):
            return dict(ok=False, requestId=request_id, error=dict(code='invalid_request', message='Невалидни данни или несъвместимо състояние.'))
        except Exception:
            return dict(ok=False, requestId=request_id, error=dict(code='internal', message='Вътрешна грешка в AI услугата.'))

    def _rate(self, actor, op):
        category, count, window = ('read', 120, 60)
        if op == 'evaluate': category, count = 'evaluate', 12
        if op in ('dataset.append', 'dataset.create', 'dataset.freeze','labeling.save','labeling.select','labeling.fork','labeling.policy.set','labeling.policy.reset'): category, count = 'write', 60
        if op == 'training.submit': category, count, window = 'train', 2, 3600
        if op=='frame.visual':category,count='pixels',3600
        if op=='training.weights.put':category,count='weights',3600
        key = (actor['digest'], category); now = time.monotonic()
        history = self.rates.setdefault(key, collections.deque())
        while history and history[0] <= now-window: history.popleft()
        require(len(history)<count, 'rate_limited', 'Превишен лимит на заявки.')
        history.append(now)

    def _expire(self):
        for key, job in list(self.jobs.items()):
            if time.monotonic()-job['created'] > 86400:
                job['cancel'].set(); self.jobs.pop(key)

    def _store(self, p):
        name = component(p.get('datasetId'), 'datasetId')
        store = DatasetStore(self.root/'datasets'/name, self.audit)
        return store

    def _job(self, p, actor, kind):
        ticket = p.get('ticket', p.get('jobId'))
        require(isinstance(ticket, str), 'invalid_request', 'Липсва ticket.')
        job = self.jobs.get(ticket)
        require(job is not None and job['kind']==kind and job['owner']==actor['digest'], 'not_found', 'Задачата не е намерена.')
        return job

    def _public(self, job):
        return {k:copy.deepcopy(v) for k,v in job.items() if k not in ('owner', 'cancel', 'created', 'lease', 'requestId','_weights')}

    def _new_job(self, kind, actor, request_id):
        require(len(self.jobs)<256, 'rate_limited', 'Достигнат лимит на задачи; изчакай изтичането им.')
        active = sum(j['kind']==kind and j['state'] in ('queued','running') for j in self.jobs.values())
        require(active < (2 if kind=='evaluate' else 1), 'rate_limited', 'Има активна задача.')
        ticket = uuid.uuid4().hex
        job = dict(ticket=ticket, kind=kind, owner=actor['digest'], state='queued', created=time.monotonic(), cancel=threading.Event(), requestId=request_id)
        self.jobs[ticket] = job
        return job

    def _invoke(self, op, p, actor, rid):
        who = actor['actor']
        if op=='capabilities':
            return dict(capability='inspector.ai.v1', actor=who, operations=sorted(OPERATIONS), scopes=actor['scopes'], resultSchema='astro-ai-result-v1',modelFormats=['astro-ai-resnet-v1'],featureSchema='astro-ai-visual-measurements-v1',pixelSchema='astro-fits-native-asinh-v1', maxPageSize=100, maxBytes=CAP, transportMaxRequestBytes=195000, uploads=False, paths=False, trainingExecutor='external')
        if op=='frames.list': return page(self.frames(), p)
        if op.startswith('labeling.'):
            labels=self.labeling
            try:
                if op=='labeling.session':return labels.session()
                if op=='labeling.quality':return labels.quality(p)
                if op=='labeling.policy':return labels.policy()
                if op in ('labeling.policy.set','labeling.policy.reset'):return labels.set_policy(p,who,rid,reset=op.endswith('.reset'))
                if op=='labeling.select':return labels.select(p['datasetId'],p.get('split'))
                if op=='labeling.get':return labels.get(p)
                if op=='labeling.list':return page(labels.entries(p),p)
                if op=='labeling.save':return labels.save(p,who,rid)
                if op=='labeling.open':return labels.open(p)
                if op=='labeling.fork':return labels.fork(p,who,rid)
            except APIError:raise
            except DatasetError as error:raise APIError('conflict',str(error)) from error
            except (ValueError,StorageError) as error:raise APIError('invalid_request',str(error)) from error
        if op=='frame.visual':
            require(callable(self.frame_visual),'unavailable','Няма FITS pixel provider.')
            return self.frame_visual(p)
        if op=='frame.features':
            require(callable(self.frame_features),'unavailable','Няма измервания.')
            return self.frame_features(p)
        if op=='frame.get':
            require(callable(self.resolve), 'unavailable', 'Няма достъп до кадри.')
            return self.resolve(p)
        if op=='evaluate':
            require(callable(self.evaluate_frame), 'unavailable', 'Няма оценител.')
            frame = self.resolve(p)
            job = self._new_job('evaluate', actor, rid)
            def work():
                try:
                    with self.lock: job['state']='running'
                    result = self.evaluate_frame(frame, job['cancel'], rid)
                    require(len(canonical(result).encode()) < CAP-1000, 'too_large', 'Резултатът е над 2 MB.')
                    with self.lock:
                        if self.closed:return
                        if job['cancel'].is_set(): job['state']='cancelled'
                        else: job.update(state='completed', result=result)
                        self.audit.append('evaluate', actor=who, request_id=rid, details=dict(frameId=frame['id'], state=job['state']))
                except Exception:
                    with self.lock: job.update(state='cancelled' if job['cancel'].is_set() else 'failed', error='unavailable')
                finally:
                    with self.lock:self.workers.discard(threading.current_thread())
            thread=threading.Thread(target=work, daemon=True, name='AI-evaluate')
            self.workers.add(thread);thread.start()
            return dict(ticket=job['ticket'], state='queued')
        if op in ('result','cancel'):
            job = self._job(p, actor, 'evaluate')
            if op=='cancel':
                job['cancel'].set(); job['state']='cancelled'
                self.audit.append('evaluate.cancel',actor=who,request_id=rid,details=dict(ticket=job['ticket']))
            return self._public(job)
        if op=='dataset.list':
            items = [DatasetStore(f.parent).manifest() for f in sorted((self.root/'datasets').glob('*/manifest.json'))]
            return page(items, p)
        if op=='labels.validate':
            validate_example(p.get('example')); self._validate_labels(p['example'])
            return dict(valid=True)
        if op.startswith('dataset.'):
            store = self._store(p)
            if op=='dataset.create':
                require(not store.exists(), 'conflict', 'Наборът вече съществува.')
                return store.create(p['datasetId'], p.get('version','1'), actor=who)
            require(store.exists(), 'not_found', 'Наборът не е намерен.')
            if op=='dataset.get': return dict(manifest=store.manifest(), **page(store.effective_examples(),p))
            if op=='dataset.freeze': return store.freeze(actor=who, request_id=rid)
            if op=='dataset.append':
                example = copy.deepcopy(p.get('example')); validate_example(example); self._validate_labels(example)
                require(example['labeler']['id']==who, 'invalid_request', 'labeler не съвпада с actor.')
                require(callable(self.resolve), 'unavailable', 'Няма достъп до кадри.')
                self.resolve(dict(frameId=example['frame']['id'], hash=example['frame']['hash']))
                try: return store.append(example, actor=who, request_id=rid)
                except DatasetError as error: raise APIError('conflict', 'Дублиран, противоречив или замразен пример.') from error
        if op=='model.list':
            items=[]
            for descriptor in self.registry.models():
                meta=read_json(self.registry._meta_path(descriptor['modelId'],descriptor['version']))
                items.append(dict(descriptor,metrics=meta.get('metrics',{}),provenance=meta.get('provenance',{})))
            return page(items, p)
        if op=='model.active': return self.registry.active()
        if op in ('model.activate','model.rollback'):
            require(p.get('confirmed') is True, 'invalid_request', 'Нужно е изрично потвърждение.')
            if op=='model.activate':
                try: candidate=self.registry.get(p.get('modelId'),p.get('version'))
                except RegistryError as error: raise APIError('not_found','Моделът не е намерен.') from error
                model=read_json(self.registry._model_path(candidate['modelId'],candidate['version']))
                evaluator.validate_model(model)
                return self.registry.activate(candidate['modelId'],candidate['version'],actor=who, request_id=rid)
            require(self.registry.state().get('previous') is not None, 'not_found', 'Няма предишен модел.')
            return self.registry.rollback(actor=who, request_id=rid)
        if op=='audit.list':
            page([],dict(limit=p.get('limit',100)))
            return self.audit.list(cursor=p.get('cursor'), limit=p.get('limit',100))
        if op=='training.submit':
            store=self._store(p); require(store.exists(),'not_found','Наборът не е намерен.')
            manifest=store.manifest()
            require(manifest['status']=='frozen', 'conflict','Замрази набора преди обучение.')
            require(store.verify()['ok'], 'conflict','Наборът не премина проверката.')
            job=self._new_job('training',actor,rid)
            job.update(datasetId=manifest['datasetId'], datasetHash=manifest['hash'])
            self.audit.append(op,actor=who,request_id=rid,details=dict(jobId=job['ticket'],datasetHash=manifest['hash']))
            return self._public(job)
        if op.startswith('training.'):
            if op=='training.status' and p.get('ticket',p.get('jobId')) is None:
                return page([self._public(j) for j in self.jobs.values() if j['kind']=='training' and j['owner']==actor['digest']],p)
            job=self._job(p,actor,'training')
            if op=='training.status': return self._public(job)
            if op=='training.cancel':
                job['cancel'].set(); job['state']='cancelled'
                job.pop('_weights',None)
                self.audit.append(op,actor=who,request_id=rid,details=dict(jobId=job['ticket']))
                return self._public(job)
            if op=='training.claim':
                require(job['state']=='queued','conflict','Задачата вече е взета.')
                lease=secrets.token_urlsafe(32); job.update(state='running',lease=lease)
                self.audit.append(op,actor=who,request_id=rid,details=dict(jobId=job['ticket']))
                return dict(**self._public(job),lease=lease)
            require(job['state']=='running' and isinstance(p.get('lease'),str) and hmac.compare_digest(job.get('lease',''),p['lease']),'conflict','Невалиден training lease.')
            if op=='training.fail':
                job.update(state='failed',error='training_failed')
                job.pop('_weights',None)
                self.audit.append(op,actor=who,request_id=rid,details=dict(jobId=job['ticket']))
                return self._public(job)
            if op=='training.weights.put':
                encoded=p.get('data')
                require(isinstance(encoded,str) and len(encoded)<=90_000,'too_large','Weights chunk: до 64 KB.')
                block=base64.b64decode(encoded,validate=True)
                require(0<len(block)<=65_536,'invalid_request','Невалиден weights chunk.')
                pending=job.setdefault('_weights',bytearray())
                require(type(p.get('offset')) is int and p['offset']==len(pending),'conflict','Невалиден weights offset.')
                require(len(pending)+len(block)<=16_000_000,'too_large','Weights: до 16 MB.')
                pending.extend(block);return dict(received=len(pending))
            model=p.get('model'); evaluator.validate_model(model)
            require(model['training']['datasetHash']==job['datasetHash'] and model['training']['datasetId']==job['datasetId'],'conflict','Моделът сочи друг набор.')
            require(model.get('metrics') is not None,'invalid_request','Липсват validation метрики.')
            require(model['training']['datasetVersion']==self._store(dict(datasetId=job['datasetId'])).manifest()['version'],'conflict','Друга версия на набора.')
            weights=bytes(job.get('_weights',b''))
            descriptor=self.registry.install(model,actor=who,request_id=rid,weights=weights)
            job.pop('_weights',None)
            job.update(state='completed',candidate=descriptor)
            self.audit.append(op,actor=who,request_id=rid,details=dict(jobId=job['ticket'],candidate=descriptor))
            return self._public(job)
        raise APIError('invalid_request','Непозната операция.')

    @staticmethod
    def _validate_labels(example):
        labels=example['labels']; explanation=example.get('explanations') or {}; note=example.get('notes','')
        quality=labels.get('quality')
        if quality is not None and quality>=.7 and (labels.get('cloud')=='cloud' or labels.get('trail')=='satellite'):
            require(bool(note or explanation),'conflict','Високо качество с дефект изисква обяснение.')
        if quality is not None and quality<=.2 and labels.get('cloud')=='clear':
            require(bool(note or explanation),'conflict','Ниско качество при ясно небе изисква обяснение.')

    def close(self):
        with self.lock:
            self.closed=True
            for job in self.jobs.values(): job['cancel'].set()
            threads=list(self.workers)
        for thread in threads:
            if thread is not threading.current_thread():thread.join(timeout=1.)
