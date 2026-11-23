"""Optional AI filter: asks the existing AI evaluator for its quality index and drops the
worst X % of one night. Frames without a reliable index are kept and not counted."""
from __future__ import annotations
import math,time

CAPABILITY='inspector.quality.v1'

def removed(scores:dict,percent:float)->list[str]:
    rated=sorted((value,ident) for ident,value in scores.items() if value is not None)
    return [ident for _,ident in rated[:math.floor(len(rated)*percent/100)]]

def available(ctx)->bool:
    try:return bool(ctx.capability(CAPABILITY,dict(operation='available')).get('ok'))
    except (ValueError,PermissionError,KeyError,TypeError,AttributeError):return False

def scores(ctx,ids:list[str],stopped,progress,interval:float=.5,retries:int=5)->dict:
    """Poll the evaluator; a busy host lock is retried. Any unfinished exit cancels the job."""
    ticket=ctx.capability(CAPABILITY,dict(operation='score-start',ids=list(ids)))['ticket'];finished=False;busy=0
    try:
        while True:
            if stopped():raise InterruptedError('AI оценката е спряна.')
            try:status=ctx.capability(CAPABILITY,dict(operation='score-status',ticket=ticket));busy=0
            except ValueError:
                busy+=1
                if busy>retries:raise
                time.sleep(interval);continue
            progress(status.get('fraction',0),status.get('message',''))
            if status['state']=='completed':
                result=ctx.capability(CAPABILITY,dict(operation='score-result',ticket=ticket))['scores'];finished=True;return result
            if status['state'] in ('failed','cancelled','delivered'):finished=True;raise RuntimeError('AI оценителят не завърши: '+str(status.get('message','')))
            time.sleep(interval)
    finally:
        if not finished:
            try:ctx.capability(CAPABILITY,dict(operation='score-cancel',ticket=ticket))
            except Exception:pass
