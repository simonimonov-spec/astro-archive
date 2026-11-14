"""Trainable, local quality index. Scores are not calibrated probabilities."""
from __future__ import annotations
import hashlib, json, math
from pathlib import Path
import numpy as np
from scipy.optimize import minimize
from scipy.special import expit

LEGACY_FEATURES=('transparency_loss', 'flux_scatter', 'star_loss', 'positive_area', 'negative_area', 'diffuse_peak', 'dark_area', 'dark_edge', 'edge_contact', 'line_length', 'line_snr', 'line_fill', 'reference_count', 'overlap', 'noise_ratio', 'fwhm_ratio', 'stars_ratio', 'elongation')
EXTRA_FEATURES=('analysis_fwhm_y_ratio', 'analysis_weighted_fwhm_ratio', 'analysis_hfr_ratio', 'analysis_background_ratio', 'analysis_noise_ratio', 'analysis_snr_ratio', 'analysis_roundness', 'analysis_eccentricity', 'analysis_shift_x', 'analysis_shift_y', 'analysis_gradient_ratio', 'analysis_dark_fraction', 'analysis_saturated_fraction', 'analysis_measured_fraction', 'analysis_invalid_fraction')
FEATURES=LEGACY_FEATURES+EXTRA_FEATURES+tuple(k+"_missing" for k in EXTRA_FEATURES)
FORMAT='frame-inspector-user-quality-v2'

def vector(value,keys=None):
    if not isinstance(value,dict) or set(value) not in (set(LEGACY_FEATURES),set(FEATURES)):raise ValueError('Несъвместими признаци на кадъра.')
    keys=FEATURES if keys is None else keys
    x=np.asarray([value.get(k,1. if k.endswith('_missing') else 0.) for k in keys],float)
    if x.shape!=(len(keys),) or not np.isfinite(x).all() or (np.abs(x)>100).any():raise ValueError('Невалидни числови признаци.')
    return x

def measurements(outputs,source_rows):
    """Compare FWHM and star counts only within the same telescope, field and night."""
    from collections import defaultdict
    groups=defaultdict(list)
    def key(r):
        m=r.get('meta',{});return tuple(str(m.get(x,'')) for x in ('telescope','object','filter','night','exposure'))+(r.get('width'),r.get('height'),str(m.get('headers',{}).get('INSTRUME','')),str(m.get('headers',{}).get('GAIN','')))
    for r in source_rows:groups[key(r)].append(r)
    source={r['id']:r for r in source_rows}
    for result in outputs:
        row=source.get(result.get('id'),{});feature=result.get('features');group=groups[key(row)]
        local=[source[i] for i in result.get('referenceIds',[]) if i in source and key(source[i])==key(row)]
        if len(local)>=3:group=local
        width=row.get('fwhm');stars=row.get('stars');roundness=row.get('roundness')
        if not result.get('reliable') or not isinstance(feature,dict) or not isinstance(width,(int,float)) or not math.isfinite(width) or width<=0 or not isinstance(stars,(int,float)) or stars<=0:
            result['qualityFeatures']=None
            if not result.get('qualityReason'):result['qualityReason']='Няма надеждно сравнение или липсват измерени FWHM/звезди; не означава лош кадър.'
            continue
        widths=[r['fwhm'] for r in group if isinstance(r.get('fwhm'),(int,float)) and math.isfinite(r['fwhm']) and r['fwhm']>0]
        counts=[r['stars'] for r in group if isinstance(r.get('stars'),(int,float)) and math.isfinite(r['stars']) and r['stars']>0]
        if not widths or not counts:
            result['qualityFeatures']=None;result['qualityReason']='Липсват сравними измервания за FWHM или брой звезди.';continue
        values={k:feature[k] for k in FEATURES[:15]}
        values.update(fwhm_ratio=min(10.,width/float(np.median(widths))),stars_ratio=min(10.,stars/float(np.median(counts))),elongation=min(5.,abs(1-float(roundness))) if isinstance(roundness,(int,float)) and math.isfinite(roundness) else 0.)
        # Reuse every chart metric and the existing spatial diagnostics. Missing
        # measurements get an explicit flag, never a fake perfect measurement.
        from .analysis_features import extend
        values.update(extend(row,group))
        try:vector(values);result['qualityFeatures']=values
        except (ValueError,KeyError,TypeError):result['qualityFeatures']=None;result['qualityReason']='Невалидни или несъвместими числови признаци; индексът не е изчислен.'

def _head(head,keys=None):
    if not isinstance(head,dict) or set(head)!={'mean','scale','weights'}:raise ValueError('Невалиден модел.')
    mean,scale,weights=(np.asarray(head[k],float) for k in ('mean','scale','weights'))
    keys=keys or (LEGACY_FEATURES if len(mean)==len(LEGACY_FEATURES) else FEATURES)
    if mean.shape!=(len(keys),) or scale.shape!=mean.shape or weights.shape!=(len(keys)+1,) or not all(np.isfinite(a).all() for a in (mean,scale,weights)) or (scale<.001).any() or (np.abs(weights)>1000).any():raise ValueError('Невалидни параметри на модела.')
    return mean,scale,weights

def probability(head,features):
    mean,scale,w=_head(head);keys=LEGACY_FEATURES if len(mean)==len(LEGACY_FEATURES) else FEATURES;z=np.clip((vector(features,keys)-mean)/scale,-15,15)
    return float(expit(z@w[:-1]+w[-1]))

def load(path):
    path=Path(path)
    if not path.exists():return None
    if path.is_symlink() or path.stat().st_size>200000:raise ValueError('Невалиден файл на обучения модел.')
    data=json.loads(path.read_text(encoding='utf-8-sig'))
    keys=LEGACY_FEATURES if data.get('features')==list(LEGACY_FEATURES) else FEATURES
    if data.get('format') not in (FORMAT,'frame-inspector-user-quality-v1') or data.get('features')!=list(keys):raise ValueError('Несъвместим обучен модел.')
    _head(data.get('quality'),keys);cloud=data.get('cloud')
    if cloud is not None:_head(cloud,keys)
    defects=data.get('defects',{})
    if not isinstance(defects,dict) or set(defects)-{'cloud','satellite','light','elongation'}:raise ValueError('Невалидни класове на модела.')
    for head in defects.values():_head(head,keys)
    data['digest']=hashlib.sha256(path.read_bytes()).hexdigest();return data

def estimate(row,user_model=None):
    f=row.get('qualityFeatures')
    if not f:return None
    if user_model:return round(100*probability(user_model['quality'],f),1)
    # A transparent heuristic until the user supplies both good and bad examples.
    scores=row.get('scores',{});defect=max([float(scores.get(c,0)) for c in ('cloud','satellite','obstruction')]+[0.])
    width=max(0.,f['fwhm_ratio']-1.)*.45;star_loss=max(0.,1.-f['stars_ratio'])*.6;elongation=f['elongation']*.5
    return round(100*(1-min(1.,max(defect,width,star_loss,elongation))),1)

def fit(features,labels):
    x=np.asarray([vector(f) for f in features]);y=np.asarray(labels,float)
    if len(y)<8 or int(y.sum())<4 or int((1-y).sum())<4:raise ValueError('За обучение са нужни поне 4 добри и 4 проблемни надеждно измерени кадъра.')
    mean=x.mean(axis=0);scale=np.maximum(x.std(axis=0),.025);z=np.column_stack((np.clip((x-mean)/scale,-15,15),np.ones(len(x))))
    weights=np.where(y>.5,len(y)/(2*y.sum()),len(y)/(2*(len(y)-y.sum())))
    def objective(w):
        logits=z@w;loss=np.mean(weights*(np.logaddexp(0,logits)-y*logits))+.03*np.sum(w[:-1]**2)
        grad=z.T@(weights*(expit(logits)-y))/len(y);grad[:-1]+=.06*w[:-1]
        return loss,grad
    result=minimize(objective,np.zeros(z.shape[1]),jac=True,method='L-BFGS-B',options={'maxiter':400})
    if not result.success:raise ValueError('Обучението не се сближи: '+str(result.message))
    return dict(mean=mean.tolist(),scale=scale.tolist(),weights=result.x.tolist())

def train(examples):
    if not isinstance(examples,list) or len(examples)>10000:raise ValueError('Невалидна обучаваща извадка.')
    rows=[r for r in examples if r.get('label') in ('perfect','bad','cloud') and r.get('qualityFeatures')]
    labels=[1 if r['label']=='perfect' else 0 for r in rows]
    quality=fit([r['qualityFeatures'] for r in rows],labels)
    clouds=[r for r in rows if r['label'] in ('perfect','cloud')]
    cloud=None
    if sum(r['label']=='cloud' for r in clouds)>=4 and sum(r['label']=='perfect' for r in clouds)>=4:
        cloud=fit([r['qualityFeatures'] for r in clouds],[1 if r['label']=='cloud' else 0 for r in clouds])
    # Test a full observation group only if both classes remain in train and test.
    groups=sorted({str(r.get('group','')) for r in rows});evaluation=None
    for group in groups:
        test=[r for r in rows if str(r.get('group',''))==group];training=[r for r in rows if str(r.get('group',''))!=group]
        if len(groups)<2 or {r['label']=='perfect' for r in test}!={True,False}:continue
        try:head=fit([r['qualityFeatures'] for r in training],[1 if r['label']=='perfect' else 0 for r in training])
        except ValueError:continue
        hits=sum((probability(head,r['qualityFeatures'])>=.5)==(r['label']=='perfect') for r in test)
        evaluation=dict(method='held-out-observation-group',testFrames=len(test),correct=hits,accuracy=round(hits/len(test),4),groups=len(groups));break
    return dict(format=FORMAT,features=list(FEATURES),quality=quality,cloud=cloud,counts=dict(perfect=labels.count(1),bad=labels.count(0),cloud=sum(r['label']=='cloud' for r in rows),uncertain=sum(r.get('label')=='uncertain' for r in examples)),evaluation=evaluation,
      limitations='Резултатите са ориентировъчни; малки и еднотипни извадки не доказват надеждност за други нощи или телескопи.')

