"""Validate the vocabulary used by the bundled v1 result/model contracts.

No optional runtime dependency. Unsupported schema keywords fail closed when
the contracts change; this is not a general JSON Schema implementation.
"""
from __future__ import annotations
import datetime
import functools
import json
import math
import re
from pathlib import Path


@functools.lru_cache(maxsize=4)
def schema(name):
    if name not in ('ai-result.schema.json','ai-model.schema.json','ai-resnet.schema.json','ai-example.schema.json'):
        raise ValueError('Неподдържан договор.')
    return json.loads((Path(__file__).parent/name).read_text(encoding='utf-8'))


def validate(value,name):
    root=schema(name)
    types={'object':lambda v:isinstance(v,dict),'array':lambda v:isinstance(v,list),'string':lambda v:isinstance(v,str),'number':lambda v:type(v) in (int,float) and math.isfinite(v),'integer':lambda v:type(v) is int,'boolean':lambda v:type(v) is bool,'null':lambda v:v is None}
    supported={'$schema','$id','$ref','$defs','title','description','type','const','enum','required','additionalProperties','properties','items','minItems','maxItems','minLength','maxLength','minimum','maximum','exclusiveMinimum','pattern','format','uniqueItems'}
    def check(v,s,path):
        if set(s)-supported:raise ValueError('Неподдържана схема: '+','.join(set(s)-supported))
        if '$ref' in s:
            ref=s['$ref']
            if not ref.startswith('#/$defs/'):raise ValueError('Невалиден schema ref.')
            return check(v,root['$defs'][ref.split('/')[-1]],path)
        expected=s.get('type')
        if expected and not any(types[t](v) for t in ([expected] if isinstance(expected,str) else expected)):
            raise ValueError('Невалиден тип: '+path)
        if 'const' in s and v!=s['const']:raise ValueError('Невалидна версия: '+path)
        if 'enum' in s and v not in s['enum']:raise ValueError('Невалиден enum: '+path)
        if isinstance(v,dict):
            if set(s.get('required',[]))-set(v):raise ValueError('Липсва поле: '+path)
            props=s.get('properties',{})
            if s.get('additionalProperties') is False and set(v)-set(props):raise ValueError('Непознато поле: '+path)
            for key,item in v.items():
                if key in props:check(item,props[key],path+'.'+key)
        elif isinstance(v,list):
            if not s.get('minItems',0)<=len(v)<=s.get('maxItems',float('inf')):raise ValueError('Невалиден размер: '+path)
            if s.get('uniqueItems') and len({json.dumps(item,sort_keys=True) for item in v})!=len(v):raise ValueError('Повторен елемент: '+path)
            for i,item in enumerate(v):check(item,s.get('items',{}),path+'['+str(i)+']')
        elif isinstance(v,str):
            if not s.get('minLength',0)<=len(v)<=s.get('maxLength',float('inf')):raise ValueError('Невалиден текст: '+path)
            if 'pattern' in s and re.search(s['pattern'],v) is None:raise ValueError('Невалиден pattern: '+path)
            if s.get('format')=='date-time':
                dt=datetime.datetime.fromisoformat(v.replace('Z','+00:00'))
                if dt.tzinfo is None:raise ValueError('Нужно е време с timezone: '+path)
        elif type(v) in (int,float):
            if not math.isfinite(v) or not s.get('minimum',-float('inf'))<=v<=s.get('maximum',float('inf')):raise ValueError('Невалиден диапазон: '+path)
            if 'exclusiveMinimum' in s and v<=s['exclusiveMinimum']:raise ValueError('Невалиден минимум: '+path)
    check(value,root,'$')
    return value
