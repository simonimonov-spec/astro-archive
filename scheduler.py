"""One shared queue of part and merge jobs across all nights.

No threads and no Siril here: state lives in each day's plain dicts (``parts`` and
``merge``) so it is saved in session.json and survives an application restart."""
from __future__ import annotations

class Queue:
    def __init__(self,days:list[dict]):self.days=list(days)
    def _day(self,ident):
        for day in self.days:
            if day['id']==ident:return day
        raise KeyError(ident)
    def next_job(self)->dict|None:
        # A night whose parts are all done is merged first, so results appear early.
        for day in self.days:
            if day.get('merge')=='pending' and all(p['state']=='done' for p in (day.get('parts') or [])):
                day['merge']='running';return dict(day=day['id'],kind='merge',name='merge')
        for day in self.days:
            for part in (day.get('parts') or []):
                if part['state']=='pending':part['state']='running';return dict(day=day['id'],kind='part',name=part['name'])
        return None
    def finish(self,job:dict,ok:bool)->None:
        day=self._day(job['day']);state='done' if ok else 'error'
        if job['kind']=='merge':day['merge']=state;return
        for part in (day.get('parts') or []):
            if part['name']==job['name']:part['state']=state;return
        raise KeyError(job['name'])
    def day_state(self,ident)->str:
        day=self._day(ident);parts=[p['state'] for p in (day.get('parts') or [])];merge=day.get('merge')
        parts_done=all(s=='done' for s in parts)
        if any(s in ('pending','running') for s in parts):return 'running'
        if parts_done and merge in ('pending','running'):return 'merging'
        if any(s=='error' for s in parts) or merge=='error':return 'error'
        return 'review'
    def progress(self,ident)->tuple[int,int]:
        day=self._day(ident);return sum(p['state']=='done' for p in (day.get('parts') or [])),len((day.get('parts') or []))
    def reset_failed(self,ident)->None:
        day=self._day(ident)
        for part in (day.get('parts') or []):
            if part['state']=='error':part['state']='pending'
        if day.get('merge') in ('error','done') and any(p['state']=='pending' for p in (day.get('parts') or [])):day['merge']='pending'
        elif day.get('merge')=='error':day['merge']='pending'
    def resume(self)->None:
        for day in self.days:
            for part in (day.get('parts') or []):
                if part['state']=='running':part['state']='pending'
            if day.get('merge')=='running':day['merge']='pending'
