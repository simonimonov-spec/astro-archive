"""Shared, bounded appearance settings. Never changes archive or image data."""
import copy
import re

NIGHT = dict(background='#0c111a', surface='#131c29', surfaceRaised='#192536', surfaceHover='#1e2e43', border='#29384c', text='#e7edf7', muted='#91a3bd', accent='#74c1ff', accentSurface='#203b54', onAccent='#081626', success='#6de0bb', warning='#ffcf89', error='#ff9eaa', errorSurface='#38202a')
DAY = dict(background='#eef3f8', surface='#ffffff', surfaceRaised='#f4f7fb', surfaceHover='#e6eef7', border='#b9c9db', text='#15263b', muted='#4d627c', accent='#155b99', accentSurface='#daeafa', onAccent='#ffffff', success='#176846', warning='#805000', error='#a5223a', errorSurface='#fbe8ed')
RED = dict(background='#0d0303', surface='#190707', surfaceRaised='#240b0b', surfaceHover='#321010', border='#592020', text='#d77575', muted='#a75b5b', accent='#e46969', accentSurface='#481818', onAccent='#140303', success='#ca6868', warning='#e98c8c', error='#f6a2a2', errorSurface='#3a1010')
FIELDS = frozenset(('themeId', 'customColors', 'redImageTint', 'backgroundMode', 'backgroundColor', 'animations'))
HEX = re.compile(r'^#[0-9a-fA-F]{6}$')

def themes():
    return [dict(id='builtin.'+key, name='theme.'+key, colors=copy.deepcopy(colors))
            for key, colors in (('night', NIGHT), ('day', DAY), ('custom', NIGHT), ('red', RED))]

def validate(values):
    if not isinstance(values, dict) or set(values)-FIELDS:
        raise ValueError('Разрешени са само настройките за външен вид.')
    result=copy.deepcopy(values)
    if 'themeId' in result and result['themeId'] not in {t['id'] for t in themes()}:
        raise ValueError('Непозната тема.')
    for name in ('animations', 'redImageTint'):
        if name in result and type(result[name]) is not bool:raise ValueError('Невалиден превключвател.')
    if 'backgroundMode' in result and result['backgroundMode'] not in ('theme', 'color', 'image'):
        raise ValueError('Невалиден фон.')
    if 'backgroundColor' in result:
        if not isinstance(result['backgroundColor'], str) or not HEX.fullmatch(result['backgroundColor']):raise ValueError('Използвай цвят #RRGGBB.')
        result['backgroundColor']=result['backgroundColor'].lower()
    if 'customColors' in result:
        colors=result['customColors']
        if not isinstance(colors, dict) or set(colors)-set(NIGHT):raise ValueError('Непознат елемент от цветовете.')
        if any(not isinstance(v,str) or not HEX.fullmatch(v) for v in colors.values()):raise ValueError('Използвай цветове #RRGGBB.')
        result['customColors']={k:v.lower() for k,v in colors.items()}
    return result

def save(service, values):
    patch=validate(values)
    with service.settings.lock:
        previous=copy.deepcopy(service.settings.value['preferences'])
        service.settings.value['preferences'].update(patch)
        try:service.settings.save()
        except Exception:
            service.settings.value['preferences']=previous
            raise
    service.events.emit('ui.appearance.changed')
    return service.configuration()
