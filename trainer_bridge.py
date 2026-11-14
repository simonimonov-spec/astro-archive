"""Compatibility bridge: labels, registry status and external trainer launch."""
from __future__ import annotations
import json
import subprocess
import sys
import uuid
from .ai_service import SCOPES
from .analyzers.storage import utc_now


def open_trainer(inspector):
    if not inspector.trainer.is_file() or inspector.trainer.is_symlink():
        raise ValueError('Липсва отделният AI Trainer.')
    token=inspector.ai.service.issue_token('trainer-'+uuid.uuid4().hex[:12],SCOPES)
    try:
        # Credential is sent once through an anonymous pipe, never argv or disk.
        from pathlib import Path
        executable=Path(sys.executable)
        if executable.name.lower()=='pythonw.exe':executable=executable.with_name('python.exe')
        process=subprocess.Popen([str(executable),str(inspector.trainer),'--pipe'],cwd=str(inspector.trainer.parent),stdin=subprocess.PIPE,close_fds=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        process.stdin.write((json.dumps(dict(token=token))+'\n').encode());process.stdin.close()
    except Exception:
        inspector.ai.service.revoke_token(token);raise
    def cleanup():
        process.wait()
        inspector.ai.service.revoke_token(token)
    import threading
    threading.Thread(target=cleanup,daemon=True,name='AI-trainer-session').start()
    return dict(opened=True,message='AI Trainer е отворен. Обучението се изпълнява в отделния процес.')


def invoke(inspector,p):
    op=str(p.get('operation') or '')
    if op in ('open','trainer.open'): return open_trainer(inspector)
    service=inspector.ai.service
    if op.startswith('labeling.') or op=='dataset.create':
        parameters=dict(p)
        if parameters.get('viewerId'):
            source=inspector.ctx.inspection('source',dict(viewerId=parameters.pop('viewerId')))
            if not source.get('id'):raise ValueError('Кадърът трябва да е в архив.')
            if parameters.get('frameId') not in (None,source['id']):raise ValueError('Прегледът е сменен; обнови оценката.')
            parameters['frameId']=source['id']
        token=service.issue_token('viewer-labeler',{'ai:read','ai:dataset:read','ai:dataset:write'})
        try:
            response=service.call(dict(parameters,token=token))
            if not response['ok']:raise ValueError(response['error']['code']+': '+response['error']['message'])
            return response['result']
        finally:service.revoke_token(token)
    if op in ('status','registry.status'):
        return dict(active=service.registry.active(), models=service.registry.models(), jobs=[dict(ticket=j['ticket'],state=j['state']) for j in service.jobs.values() if j['kind']=='training'],trainingExecutor='external')
    if op=='label':
        source=inspector.ctx.inspection('source',dict(viewerId=p.get('viewerId')))
        if not source.get('id'): raise ValueError('Добави кадъра в архив преди оценяване.')
        return append_label(inspector,source['id'],p.get('labels'),actor='quick-label',notes=p.get('notes',''),dataset_id=p.get('datasetId','quick-labels'))
    raise ValueError('Обучението и активирането са преместени в отделния AI Trainer. Отвори го от AI обучение.')


def append_label(inspector,frame_id,labels,*,actor,notes='',dataset_id='quick-labels'):
    snapshot=inspector.ai.frame_features(dict(frameId=frame_id));service=inspector.ai.service
    token=service.issue_token(actor,['ai:dataset:write'])
    try:
        store=service._store(dict(datasetId=dataset_id))
        if not store.exists():
            result=service.call(dict(token=token,operation='dataset.create',datasetId=dataset_id))
            if not result['ok']:raise ValueError(result['error']['message'])
        example=dict(schema='astro-ai-example-v1',exampleId=uuid.uuid4().hex,frame=snapshot['frame'],context=snapshot['context'],labels=labels,labeler=dict(id=actor),createdAt=utc_now(),split='unassigned',notes=notes,origin='manual')
        result=service.call(dict(token=token,operation='dataset.append',datasetId=dataset_id,example=example))
        if not result['ok']:raise ValueError(result['error']['message'])
        return dict(saved=True,message='Оценката е записана чрез общия AI API.')
    finally:service.revoke_token(token)
