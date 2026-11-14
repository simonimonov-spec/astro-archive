"""Small regularized multilabel classifier; model files contain data, never code."""
from pathlib import Path
import json,hashlib
import numpy as np
from scipy.optimize import minimize
from scipy.special import expit
from .engine import FEATURES

CLASSES=('cloud','satellite','obstruction')

def load(path):
    path=Path(path)
    if path.stat().st_size>200000:raise ValueError('Прекалено голям AI модел.')
    data=json.loads(path.read_text(encoding='utf-8'))
    if data.get('format')!='frame-inspector-linear-v1' or data.get('features')!=list(FEATURES) or set(data.get('weights',{}))!=set(CLASSES):raise ValueError('Несъвместим AI модел.')
    for values in [data['mean'],data['scale'],*[data['weights'][c] for c in CLASSES]]:
        a=np.asarray(values,float)
        if not np.isfinite(a).all() or a.ndim!=1:raise ValueError('Невалидни тегла на AI модела.')
    if len(data['mean'])!=len(FEATURES) or len(data['scale'])!=len(FEATURES) or min(data['scale'])<=0 or any(len(data['weights'][c])!=len(FEATURES)+1 for c in CLASSES):raise ValueError('Невалидни размери на AI модела.')
    data['digest']=hashlib.sha256(path.read_bytes()).hexdigest();return data

def score(data,features):
    x=(np.asarray([features[k] for k in FEATURES])-np.asarray(data['mean']))/np.asarray(data['scale']);x=np.clip(x,-15,15)
    return {c:float(expit(np.dot(data['weights'][c][:-1],x)+data['weights'][c][-1])) for c in CLASSES}

def train(rows,labels,*,anchor=None,strength=.06):
    x=np.asarray([[r[k] for k in FEATURES] for r in rows],float);y=np.asarray(labels,float)
    mean=x.mean(axis=0);scale=np.maximum(x.std(axis=0),.025);z=np.column_stack(((x-mean)/scale,np.ones(len(x))))
    weights={}
    for i,c in enumerate(CLASSES):
        target=y[:,i];balance=np.where(target>.5,len(target)/(2*max(1,target.sum())),len(target)/(2*max(1,len(target)-target.sum())))
        def objective(w):
            logits=z@w;loss=np.mean(balance*(np.logaddexp(0,logits)-target*logits))+strength*np.sum(w[:-1]**2)/2
            gradient=z.T@(balance*(expit(logits)-target))/len(target);gradient[:-1]+=strength*w[:-1]
            return loss,gradient
        result=minimize(objective,np.zeros(z.shape[1]),jac=True,method='L-BFGS-B',options={'maxiter':300})
        if not result.success:raise ValueError('Обучението не се сближи: '+result.message)
        weights[c]=result.x.tolist()
    return dict(format='frame-inspector-linear-v1',features=list(FEATURES),mean=mean.tolist(),scale=scale.tolist(),weights=weights)

def findings(analysis,scores,threshold=.72):
    f=analysis['features'];reliable=analysis['reliable'];result=[]
    # Statistical evidence is mandatory even if an ML extrapolation is strong.
    gates=dict(cloud=reliable and (f['transparency_loss']>.16 or (f['positive_area']>.018 and f['flux_scatter']>.10)),
               satellite=reliable and f['line_length']>.14 and f['line_fill']>.32 and f['line_snr']>.35,
               obstruction=reliable and f['dark_area']>.025 and f['dark_edge']>.12 and (f['star_loss']>.12 or f['edge_contact']>.02))
    names=dict(cloud='Възможна облачност',satellite='Възможна сателитна/самолетна следа',obstruction='Възможно препятствие — дърво, сграда или подобен обект')
    for kind in CLASSES:
        if gates[kind] and scores[kind]>=threshold:result.append(dict(type=kind,origin='ai',score=scores[kind],message=names[kind]+'; провери ръчно.',severity='high' if scores[kind]>.90 and kind!='cloud' else 'medium'))
    return result
