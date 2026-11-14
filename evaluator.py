"""Shared analyzer-to-result adapter used by chart inspection and API."""
from __future__ import annotations
import copy
import time
from . import analyzers, heuristic
from . import resnet_model
from .analyzers.base import clamp01, finite
from .analyzers.storage import utc_now
from .contracts import validate as validate_contract

FEATURE_SCHEMA = 'astro-ai-features-v1'
FILTERS=('L','R','G','B','HA','OIII','SII','OTHER')
FEATURE_NAMES = tuple(name for factor in analyzers.FACTOR_ORDER for name in (factor+'.risk', factor+'.confidence')) + ('context.exposure', 'context.gain', 'context.pixelScale', 'context.bayer', 'context.referenceCount') + tuple('context.filter.'+name for name in FILTERS)


def features(factors, context=None):
    context = context or {}
    result = {}
    for factor in factors:
        result[factor['id']+'.risk'] = factor['risk']
        result[factor['id']+'.confidence'] = factor['confidence']
    for name in ('exposure', 'gain', 'pixelScale', 'bayer', 'referenceCount'):
        result['context.'+name] = context.get(name)
    filter_name=str(context.get('filter','')).strip().upper()
    for name in FILTERS:result['context.filter.'+name]=float(filter_name==name if name!='OTHER' else filter_name not in FILTERS[:-1])
    return result


def validate_model(model):
    return resnet_model.validate(model)


def reason_for(factor,code='high_risk'):
    """Cite analyzer values; a derived risk is never called a measurement."""
    metrics=[copy.deepcopy(m) for m in factor['metrics'] if finite(m.get('value'))]
    measured=[m for m in metrics if m.get('measured') is True]
    citations=(measured or metrics)[:8]
    primary=citations[0] if citations else None
    details='; '.join(f"{m['name']}={m['value']:.6g} {m['unit']}" for m in citations[:3])
    message=f"{factor['id']}: риск {factor['risk']:.3f}. "
    message+=('Измерено: ' if measured else 'Оценени стойности: ')+details if citations else 'Липсват числови измервания; нужна е ръчна проверка.'
    return dict(factor=factor['id'],code=code,message=message[:400],
                value=primary['value'] if primary else None,unit=primary['unit'] if primary else None,
                metric=primary['name'] if primary else None,measured=bool(measured),
                risk=factor['risk'],status=factor['status'],measurements=citations)


def evaluate(row, frame, *, request_id, group=None, analysis=None, model=None, analyzed=None, prediction=None, **pixels):
    started = utc_now(); clock = time.monotonic()
    analyzed = analyzed or analyzers.run_all(row, group=group, analysis=analysis, **pixels)
    factors = copy.deepcopy(analyzed['factors'])
    value, confidence, contributions = heuristic.fuse(factors)
    engine = dict(kind='heuristic', modelId=None, modelVersion=None, featureSchema=FEATURE_SCHEMA, nnAvailable=False)
    events = dict(cloud=None, satellite=None, obstruction=None)
    limitations = ['Оценката е препоръка за ръчна проверка; не променя подбора.', 'Без реална валидация изходите не са калибрирани вероятности.']
    if model is not None:
        try:
            validate_model(model)
            if prediction is None or prediction.get('coverage')!=1.:
                raise ValueError('Липсва пълно визуално покритие от ResNet.')
            values=prediction['values']
            if not all(finite(v) and 0 <= v <= 1 for v in values.values()):
                raise ValueError('Невалидни изходи.')
            # No evidence remains unknown, even if the network has biases.
            if value is not None:
                value = values['quality']
                attribution = prediction['contributions']
                raw_groups=dict(stars=('fwhm','hfr','fwhmY','roundness','eccentricity','stars','fittedStars','measuredStars','saturatedStars'),noise=('noise',),light=('background','backgroundVariation','pixelScale'))
                contributions = {f['id']: max(-1., min(1., sum(v for k, v in attribution.items() if k.startswith(f['id']+'.') or k in ['measurement.'+n for n in raw_groups.get(f['id'],())]))) for f in factors}
                for f in factors:
                    if 'risk.'+f['id'] in values:
                        f['risk'] = values['risk.'+f['id']]
                        f['status'] = 'estimated'
                        f['notes'].append('Рискът е приблизителен ResNet изход; измерванията са запазени отделно.')
                    for region in prediction.get('regions',[]):
                        if region['head']=='risk.'+f['id'] or dict(cloud='artifacts',satellite='trails',obstruction='defects').get(region['head'].removeprefix('event.'))==f['id']:
                            f['evidence'].append(dict(kind='region',bbox=region['bbox'],severity=region['score'],coordinateSpace='native',note='ResNet '+region['head']+' tile candidate; not a segmentation mask'))
                    f['evidence']=f['evidence'][:64]
                events = {name: values.get('event.'+name) for name in events}
                engine.update(kind='neural', modelId=model['modelId'], modelVersion=model['version'], featureSchema=resnet_model.FEATURE_SCHEMA, nnAvailable=True,architecture=resnet_model.ARCHITECTURE)
                limitations.extend(['ResNet покрива всички native области; локализациите са приблизителни tile кандидати.', 'Приносът е occlusion на измервания при фиксирани пиксели; не е pixel attribution.', 'Наличието на галактика/мъглявина не се използва като дефект.', 'Confidence описва входните измервания; не е калибрирана вероятност за правилен ResNet резултат.'])
        except (ValueError, KeyError, TypeError, ArithmeticError):
            limitations.append('Няма валиден обучен и оценен ResNet с пикселен вход; използвана е евристична оценка.')
    for f in factors:
        f['contribution'] = contributions.get(f['id'])
    reasons = [reason_for(f) for f in factors if f['risk'] is not None and f['risk'] >= .65]
    stack = 'review' if value is None or confidence < .5 or reasons or value < .7 else 'ok'
    if any(f['status'] == 'measured' and f['confidence'] >= .8 and f['risk'] is not None and f['risk'] >= .95 and any(m.get('measured') is True and finite(m.get('value')) for m in f['metrics']) for f in factors):
        stack = 'do_not_stack'
    if value is None:
        reasons.append(dict(factor='overall',code='insufficient_measurements',message='Липсват достатъчни измервания; качеството е неизвестно.',value=None,unit='quality',measured=False,status='unavailable'))
    elif value < .7:
        reasons.append(dict(factor='overall',code='low_quality',message=f'Оценено качество {value:.3f} е под 0.7; прегледай факторите.',value=value,unit='quality',measured=False,status='estimated'))
    if confidence < .5:
        reasons.append(dict(factor='overall',code='low_confidence',message=f'Общото доверие {confidence:.3f} е под 0.5; нужна е ръчна проверка.',value=confidence,unit='confidence',measured=False,status='estimated'))
    if not reasons:
        supported=[f for f in factors if f['risk'] is not None and any(m.get('measured') is True and finite(m.get('value')) for m in f['metrics'])]
        reasons=[reason_for(f,'within_review_threshold') for f in sorted(supported,key=lambda f:f['confidence'],reverse=True)[:3]]
    for f in factors:
        limitations.extend(f['notes'])
    result = dict(schema='astro-ai-result-v1', requestId=request_id, createdAt=utc_now(), frame=frame, engine=engine,
                overall=dict(quality=value, confidence=clamp01(confidence), band='unknown' if value is None else 'good' if value >= .7 else 'fair' if value >= .4 else 'poor'),
                factors=factors, events=events, objects=analyzed['objects'], recommendation=dict(stack=stack, reasons=reasons),
                limitations=list(dict.fromkeys(limitations))[:32], timing=dict(startedAt=started, durationMs=max(0, round((time.monotonic()-clock)*1000))))
    return validate_contract(result,'ai-result.schema.json')
