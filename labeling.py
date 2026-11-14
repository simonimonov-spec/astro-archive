"""One ID/hash/revision-based labeling workflow for viewer and Trainer."""
from __future__ import annotations
import copy
import uuid
from .analyzers.dataset import DatasetStore,DatasetError,validate_example
from .analyzers.storage import atomic_write_json,component,digest_data,read_json,utc_now


class Labeling:
    def __init__(self,service,snapshot=None,show=None,describe=None):
        self.service=service;self.root=service.root;self.snapshot=snapshot;self.show=show;self.describe=describe
        self.path=self.root/'labeling-session.json'
        self.policy_path=self.root/'labeling-policy.json'

    def policy(self):
        from .label_fields import WEIGHTS,validate_weights
        value=read_json(self.policy_path,dict(weights=dict(WEIGHTS)))
        weights=validate_weights(value['weights'])
        return dict(weights=weights,defaults=dict(WEIGHTS),revision=digest_data(weights),method='manual-label-policy-v1')

    def set_policy(self,p,actor,rid,reset=False):
        from .label_fields import validate_weights
        current=self.policy()
        if p.get('expectedRevision')!=current['revision']:raise DatasetError('Тежестите са променени от друг прозорец. Обнови настройките.')
        weights=validate_weights(current['defaults'] if reset else p.get('weights'))
        atomic_write_json(self.policy_path,dict(schema='astro-manual-quality-policy-v1',weights=weights,updatedAt=utc_now()))
        result=self.policy()
        self.service.audit.append('labeling.policy.reset' if reset else 'labeling.policy.set',actor=actor,request_id=rid,details=dict(previous=current['weights'],weights=weights,revision=result['revision']))
        return result

    def quality(self,p):
        from .label_fields import constrain_quality,explanation
        policy=self.policy();adjusted=constrain_quality(p['labels'],policy['weights'])
        return dict(labels=adjusted,explanation=explanation(adjusted,policy['weights']),qualityPolicy=policy)

    def datasets(self):
        return [DatasetStore(p.parent).manifest() for p in sorted((self.root/'datasets').glob('*/manifest.json'))]

    def session(self):
        from .label_fields import FIELDS
        value=read_json(self.path,dict(datasetId=None,split='unassigned',serial=0))
        datasets=self.datasets()
        if value.get('datasetId') not in {d['datasetId'] for d in datasets}:
            draft=next((d for d in datasets if d['status']=='draft'),datasets[0] if datasets else None)
            value.update(datasetId=draft['datasetId'] if draft else None,exampleId=None,frameId=None)
        brief=[{k:d[k] for k in ('datasetId','version','status','exampleCount','hash')} for d in datasets]
        policy=self.policy()
        return dict(value,datasets=brief,fields=[list(f) for f in FIELDS],qualityPolicy=policy,etag=digest_data(dict(session=value,datasets=brief,policy=policy['revision'])))

    def select(self,dataset_id,split=None,*,frame_id=None,example_id=None):
        store=self.store(dataset_id)
        if split is not None and split not in ('train','validation','test','unassigned'):raise ValueError('Невалиден split.')
        current=self.session()
        value=dict(datasetId=dataset_id,split=split if split is not None else current['split'],serial=current.get('serial',0)+1,frameId=frame_id,exampleId=example_id)
        atomic_write_json(self.path,value)
        return self.session()

    def store(self,dataset_id):
        component(dataset_id,'datasetId');store=DatasetStore(self.root/'datasets'/dataset_id,self.service.audit)
        if not store.exists():raise ValueError('Наборът не е намерен. Създай го в Trainer или от прегледа.')
        return store

    def get(self,p):
        if not callable(self.snapshot):raise ValueError('Няма връзка към архивните кадри.')
        snapshot=self.snapshot(p);frame=snapshot['frame'];session=self.session()
        dataset_id=p.get('datasetId') or session['datasetId'];record=None;manifest=None
        if dataset_id:
            store=self.store(dataset_id);manifest=store.manifest()
            items=[e for e in store.effective_examples() if e['frame']['id']==frame['id'] and e['frame']['hash']==frame['hash']]
            selected=p.get('exampleId') or (session.get('exampleId') if session.get('datasetId')==dataset_id and session.get('frameId')==frame['id'] else None)
            if selected:
                record=next((e for e in items if e['exampleId']==selected),None)
                if record is None:raise ValueError('Записът не съвпада с този кадър/hash.')
            elif len(items)>1:raise ValueError('Има няколко оценки на този кадър. Избери точния запис от таблицата в Trainer.')
            elif items:record=items[0]
        return dict(frame=frame,context=snapshot['context'],name=snapshot.get('name',frame['id']),datasetId=dataset_id,
                    manifest=manifest,example=record,revision=digest_data(record) if record else None,split=record['split'] if record else session['split'],session=session)

    def save(self,p,actor,rid):
        current=self.get(p);store=self.store(current['datasetId'])
        if store.manifest()['status']!='draft':raise DatasetError('Замразен набор: първо създай редактируемо копие.')
        if p.get('expectedRevision')!=current['revision']:raise DatasetError('Оценката е променена от друг прозорец. Обнови я преди запис.')
        from .label_fields import constrain_quality
        policy=self.policy()
        if 'expectedPolicyRevision' in p and p['expectedPolicyRevision']!=policy['revision']:raise DatasetError('Тежестите са променени. Преизчисли оценката преди запис.')
        example=current['example'];labels=constrain_quality(copy.deepcopy(p.get('labels')),policy['weights'])
        split=p.get('split',current['split']);notes=p.get('notes','')
        if example:
            probe=copy.deepcopy(example);probe.pop('overridden',None);probe.update(labels=labels,split=split,notes=notes)
        else:
            probe=dict(schema='astro-ai-example-v1',exampleId=uuid.uuid4().hex,frame=current['frame'],context=current['context'],labels=labels,labeler=dict(id=actor),createdAt=utc_now(),split=split,origin='manual',notes=notes)
        validate_example(probe);self.service._validate_labels(probe)
        if example:store.revise(example['exampleId'],labels=labels,split=split,notes=notes,actor=actor,expected_revision=current['revision'],request_id=rid)
        else:store.append(probe,actor=actor,request_id=rid)
        self.service.audit.append('labeling.quality.applied',actor=actor,request_id=rid,details=dict(datasetId=current['datasetId'],exampleId=probe['exampleId'],policyRevision=policy['revision'],weights=policy['weights'],quality=labels.get('quality')))
        self.select(current['datasetId'],split,frame_id=current['frame']['id'],example_id=probe['exampleId'])
        return dict(saved=True,**self.get(dict(frameId=current['frame']['id'],hash=current['frame']['hash'],datasetId=current['datasetId'],exampleId=probe['exampleId'])))

    def entries(self,p):
        dataset_id=p.get('datasetId') or self.session()['datasetId'];datasets=self.datasets() if p.get('allDatasets') is True else [self.store(dataset_id).manifest()] if dataset_id else []
        items=[]
        for manifest in datasets:
            for e in self.store(manifest['datasetId']).effective_examples():
                frame=e['frame'];brief={}
                if callable(self.describe):
                    try:brief=self.describe(frame['id'])
                    except (ValueError,OSError):brief=dict(name='Липсващ кадър · '+frame['id'])
                items.append(dict(datasetId=manifest['datasetId'],datasetStatus=manifest['status'],exampleId=e['exampleId'],frameId=frame['id'],hash=frame['hash'],name=brief.get('name',frame['id']),context=e['context'],labels=e['labels'],notes=e.get('notes',''),split=e['split'],revision=digest_data(e),labeler=e['labeler'],createdAt=e['createdAt']))
        return items

    def open(self,p):
        if not callable(self.show):raise ValueError('Няма връзка с прегледа в Astro Archive.')
        # Resolve the saved entry again; client never chooses a path or substitutes a hash.
        if p.get('exampleId'):
            e=next((e for e in self.store(p['datasetId']).effective_examples() if e['exampleId']==p['exampleId']),None)
            if e is None:raise ValueError('Оценката не е намерена.')
            if p.get('frameId')!=e['frame']['id']:raise ValueError('Оценката е за друг кадър.')
            p=dict(p,hash=e['frame']['hash'])
        current=self.get(p)
        if current['datasetId']:self.select(current['datasetId'],current['split'],frame_id=current['frame']['id'],example_id=current['example']['exampleId'] if current['example'] else None)
        result=self.show(dict(frameId=current['frame']['id'],hash=current['frame']['hash']))
        return dict(opened=True,frameId=current['frame']['id'],hash=current['frame']['hash'],datasetId=current['datasetId'],record=current,**result)

    def fork(self,p,actor,rid):
        source=self.store(p['datasetId']);name=component(p.get('newDatasetId') or (p['datasetId'][:90]+'-edit-'+uuid.uuid4().hex[:8]),'newDatasetId')
        version=p.get('version') or 'revision-'+uuid.uuid4().hex[:8]
        result=source.copy_to(self.root/'datasets'/name,name,version,actor=actor,request_id=rid)
        old=self.session()
        self.select(name,frame_id=old.get('frameId') if old.get('datasetId')==p['datasetId'] else None,example_id=old.get('exampleId') if old.get('datasetId')==p['datasetId'] else None)
        return dict(manifest=result,session=self.session())
