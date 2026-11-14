"""Canonical labeling controls shared by the viewer and external Trainer."""
import math

QUALITY_CHOICES=['']+[str(round(i/20,2)) for i in range(20,-1,-1)]
RISK_CHOICES=['']+[str(round(i/10,1)) for i in range(11)]
FIELDS=[
    ('quality','Качество 0–1',QUALITY_CHOICES),
    ('cloud','Облаци',['','clear','cloud','uncertain']),
    ('trail','Следа',['','none','satellite','aircraft','uncertain']),
    ('light','Светлина · риск',RISK_CHOICES),
    ('elongation','Елипсовидност на звездите',RISK_CHOICES),
    ('defocus','Разфокусирани звезди · риск',RISK_CHOICES),
    ('defects','Дефекти',['','none','present','uncertain']),
    ('artifacts','Отблясъци',['','none','present','uncertain']),
    ('noise','Шум · риск',RISK_CHOICES),
    ('obstruction','Препятствие',['','none','present','uncertain']),
]
NUMERIC={'quality','light','elongation','defocus','noise'}

# Explicit labeling policy, not learned probabilities or scientific calibration.
WEIGHTS=dict(cloud=.9,trail=.8,light=.55,elongation=.85,defocus=.85,defects=.75,artifacts=.65,noise=.65,obstruction=1.)
CATEGORIES=dict(cloud=dict(clear=0.,cloud=1.),trail=dict(none=0.,satellite=.7,aircraft=1.),
                defects=dict(none=0.,present=1.),artifacts=dict(none=0.,present=1.),obstruction=dict(none=0.,present=1.))


def validate_weights(weights):
    if not isinstance(weights,dict) or set(weights)!=set(WEIGHTS):raise ValueError('Задай тежест за всеки фактор, без непознати полета.')
    result={}
    for key,value in weights.items():
        if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=1:raise ValueError('Тежестта трябва да е 0–100%: '+key)
        result[key]=float(value)
    return result


def quality_summary(labels,weights=None):
    """Multiplicative penalties; unknowns do not count as observed clean factors."""
    quality=1.;known=0;reasons=[]
    for key,weight in validate_weights(WEIGHTS if weights is None else weights).items():
        value=labels.get(key)
        if value in (None,'','uncertain'):continue
        if key in CATEGORIES:
            if value not in CATEGORIES[key]:raise ValueError('Невалиден фактор: '+key)
            risk=CATEGORIES[key][value]
        else:
            if isinstance(value,bool):raise ValueError('Невалиден риск: '+key)
            risk=float(value)
            if not math.isfinite(risk) or not 0<=risk<=1:raise ValueError('Рискът трябва да е 0–1: '+key)
        known+=1;quality*=1-weight*risk
        if risk:reasons.append(dict(id=key,risk=risk,weight=weight,penalty=round(weight*risk,4)))
    return dict(quality=round(quality,4) if known else None,known=known,total=len(WEIGHTS),reasons=reasons,method='manual-label-policy-v1')


def constrain_quality(labels,weights=None):
    if not isinstance(labels,dict):raise ValueError('Липсват етикети.')
    result=dict(labels);summary=quality_summary(result,weights);limit=summary['quality'];quality=result.get('quality')
    if quality is not None:
        if isinstance(quality,bool) or not math.isfinite(float(quality)) or not 0<=float(quality)<=1:raise ValueError('Качеството трябва да е 0–1.')
        quality=float(quality)
    result['quality']=min(quality,limit) if quality is not None and limit is not None else limit if quality is None else quality
    return result


def explanation(labels,weights=None):
    summary=quality_summary(labels,weights);q=summary['quality']
    titles={k:t for k,t,_ in FIELDS}
    parts=[titles[r['id']]+' −'+str(round(100*r['penalty'],1))+'%' for r in summary['reasons']]
    return ('Правило за етикети, не AI измерване. '+('Качество до '+str(q)+'. ' if q is not None else 'Няма оценени фактори. ')+
            'Оценени фактори: '+str(summary['known'])+'/'+str(summary['total'])+'. '+
            (' × '.join(parts)+'. Намаленията се умножават.' if parts else '')+' Неизвестните остават неизвестни; обектите не намаляват качеството.')


def empty():return dict(quality=None,cloud=None,trail=None,light=None,elongation=None,defocus=None,defects=None,artifacts=None,noise=None,obstruction=None,objects=[])


def text(value):return '' if value is None else str(float(value)) if type(value) in (int,float) else str(value)


def parse(values,base=None):
    result=empty();result.update(base or {})
    for key,_,_ in FIELDS:
        value=values.get(key,'')
        result[key]=None if value=='' else float(value) if key in NUMERIC else value
    return result
