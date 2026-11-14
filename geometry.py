"""Local mosaic neighbours. Pixel fitting stays behind the generic Bridge SDK."""
import numpy as np
from scipy.spatial import cKDTree
from .engine import project


def matrix(value):
    if value is None:return None
    a=np.asarray(value,float)
    return a if a.shape==(3,3) and np.isfinite(a).all() and abs(np.linalg.det(a))>1e-10 else None


class FieldMap:
    def __init__(self,rows,ctx,report_id,cancel,progress):
        self.rows={r['id']:r for r in rows};self.positions={};self.order={r['id']:i for i,r in enumerate(rows)}
        self.recovered=0
        for row in rows:
            transform=matrix(row.get('transform'))
            if transform is not None:self.positions[row['id']]=(row.get('reference') or row['id'],transform)
        # Catalogue-only local recovery. A mosaic tile can establish its own
        # anchor; it need not overlap the single global reference frame.
        for index,row in enumerate(rows):
            if cancel.is_set():raise InterruptedError('Локалното сравнение е спряно.')
            if row['id'] not in self.positions and not row.get('error') and len(row.get('starList',[]))>=10:
                neighbours=[r for r in rows[max(0,index-6):index+7] if r['id']!=row['id'] and not r.get('error') and len(r.get('starList',[]))>=10]
                neighbours.sort(key=lambda r:(abs(self.order[r['id']]-index),r['id'] not in self.positions))
                for neighbour in neighbours[:3]:
                    if cancel.is_set():raise InterruptedError('Локалното сравнение е спряно.')
                    result=ctx.inspection('register',dict(reportId=report_id,sourceId=row['id'],targetId=neighbour['id']))
                    local=matrix(result.get('matrix'))
                    if local is None:continue
                    root,base=self.positions.setdefault(neighbour['id'],(neighbour['id'],np.eye(3)))
                    self.positions[row['id']]=(root,base@local);self.recovered+=1;break
            progress(index+1,len(rows),'Локални полета и мозайки')
        groups={}
        for ident,(root,transform) in self.positions.items():
            row=self.rows[ident];centre=project(transform,np.array([[(row['width']-1)/2,(row['height']-1)/2]]))[0]
            if np.isfinite(centre).all():groups.setdefault(root,[]).append((ident,centre))
        self.trees={root:([v[0] for v in values],cKDTree([v[1] for v in values])) for root,values in groups.items()}

    def candidates(self,row):
        position=self.positions.get(row['id'])
        if position is None:return []
        root,transform=position
        if root not in self.trees:return []
        identifiers,tree=self.trees[root];centre=project(transform,np.array([[(row['width']-1)/2,(row['height']-1)/2]]))[0]
        _,indices=tree.query(centre,k=min(48,len(identifiers)))
        result=[]
        for i in np.atleast_1d(indices):
            ident=identifiers[int(i)]
            if ident==row['id']:continue
            source=self.rows[ident]
            if source.get('error'):continue
            local=np.linalg.inv(transform)@self.positions[ident][1]
            # Cheap native-pixel footprint ranking; the published percentage is
            # subsequently measured from actual warped valid-pixel masks.
            xx,yy=np.meshgrid(np.linspace(0,row['width']-1,16),np.linspace(0,row['height']-1,16))
            mapped=project(np.linalg.inv(local),np.column_stack((xx.ravel(),yy.ravel())))
            valid=np.isfinite(mapped).all(axis=1)&(mapped[:,0]>=0)&(mapped[:,0]<source['width'])&(mapped[:,1]>=0)&(mapped[:,1]<source['height'])
            overlap=float(valid.mean())
            if overlap>=.35:result.append((source,local,overlap))
        return sorted(result,key=lambda item:item[2],reverse=True)
