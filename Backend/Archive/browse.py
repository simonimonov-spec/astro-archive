"""Revision-aware RAM index. One pass per change, O(1) folder statistics thereafter."""
import datetime as dt, json, threading, time
from pathlib import Path
from Backend.Fits.images import finite

def blank():return dict(files=0,frames=0,exposure=0,bytes=0,exposureUnknown=0,framesUnknown=0,metadataIncomplete=0,stacks=0,knownExposureFrames=0,firstObservation=None,lastObservation=None,created=None,info={})
def accumulate(out,row):
    m=row['meta'];frames=max(1,int(finite(m.get('frames'),1)));exp=finite(m.get('totalExposure'))
    out['files']+=1;out['frames']+=frames;out['bytes']+=row['size'];out['stacks']+=m.get('kind')=='stack'
    out['exposure']+=max(0,exp or 0);out['exposureUnknown']+=exp is None;out['framesUnknown']+=m.get('framesKnown') is False
    out['metadataIncomplete']+=bool(m.get('missingFields') or m.get('indexProblem'));out['knownExposureFrames']+=frames if exp is not None else 0
    dates=m.get('sourceNights') or [m.get('night') or m.get('observed','')[:10]]
    for stamp in dates:
        if stamp:out['firstObservation']=min(out['firstObservation'] or stamp,stamp);out['lastObservation']=max(out['lastObservation'] or stamp,stamp)
    stamp=m.get('created') or dt.datetime.fromtimestamp(row['mtime']/1e9,dt.timezone.utc).isoformat()
    out['created']=max(out['created'] or stamp,stamp)
    for key in ('object','telescope','filter','sensor','resolution','telescopes'):
        values=m.get(key,[]);values=values if isinstance(values,list) else [values]
        out['info'].setdefault(key,set()).update(str(v) for v in values if v)

class BrowseIndex:
    def __init__(self,catalog):self.catalog=catalog;self.lock=threading.RLock();self.cache={}
    def get(self,archive):
        with self.lock:
            old=self.cache.get(archive)
            if old and time.monotonic()-old.get('validated',0)<.2:return old
            with self.catalog.store.db() as db:row=db.execute('SELECT revision FROM archive_revision WHERE archive=?',(archive,)).fetchone()
            revision=row[0] if row else 0
            if old:old['validated']=time.monotonic()
            if old and (old['revision']==revision or self.catalog.index_status(archive)['building'] and time.monotonic()-old['time']<1):return old
            rows=self.catalog.query(dict(archive=archive),slim=True);by_rel={};groups={'':blank()};children={}
            for row in rows:
                rel=row['relative'];by_rel[rel]=row;parents=['']+['/'.join(rel.split('/')[:i]) for i in range(1,len(rel.split('/'))) ]
                for parent in parents:accumulate(groups.setdefault(parent,blank()),row)
                for parent in parents:
                    tail=rel[len(parent)+1:] if parent else rel;child=(parent+'/' if parent else '')+tail.split('/')[0]
                    children.setdefault(parent,set()).add(child)
            for group in groups.values():group['info']={k:', '.join(sorted(v)) for k,v in group['info'].items()}
            value=dict(revision=revision,time=time.monotonic(),validated=time.monotonic(),rows=rows,files=by_rel,groups=groups,children=children);self.cache[archive]=value;return value
    def group(self,archive,relative=''):return self.get(archive)['groups'].get(relative.replace('\\','/').strip('/'),blank())
    def details(self,archive,relative,kind='folder'):
        rel=relative.replace('\\','/').strip('/');cache=self.get(archive);row=cache['files'].get(rel);group=blank()
        if row:accumulate(group,row)
        else:group=cache['groups'].get(rel,group)
        state=self.catalog.index_status(archive)
        return dict(kind=kind,files=group['files'],stacks=group['stacks'],frames=group['frames'] if kind!='frame' else None,exposure=row['meta'].get('exposure') if row else None,
            totalExposure=group['exposure'] if not group['files'] or group['exposureUnknown']<group['files'] else None,
            dateKind='observation' if kind in ('frame','dark','flat','bias','stack','day','night') else 'modified',firstObservation=group['firstObservation'],lastObservation=group['lastObservation'],created=group['created'],minimumExposure=bool(group['exposureUnknown']),minimumFrames=bool(group['framesUnknown']),unknownExposure=group['exposureUnknown'],metadataIncomplete=group['metadataIncomplete'],building=state['building'],stale=state['stale'])
