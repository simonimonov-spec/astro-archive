"""Declarative screens for each step: Настройки (one panel) and Работа (days + Siril panels).

Pure functions from plugin state to SDK elements; no I/O, so they are unit tested."""
from __future__ import annotations
import os
from .engine import MAX_PARALLEL,MIN_CHUNK,MAX_CHUNK

ACTIVE=('pending','running','merging','paused','error')

def field(kind,ident,label='',**extra):return dict(type=kind,id=ident,label=label,**extra)
def button(ident,label,command,disabled=False,tone='normal'):return field('button',ident,label,command=command,disabled=disabled,tone=tone)
def text(ident,value,tone='normal',span=2):return field('text',ident,'',value=value,tone=tone,span=span)
def day_name(g):return g['object']+' · '+g['night']

def abandon_controls(confirm_cancel):
    result=[button('abandon','Потвърди: откажи цялата работа' if confirm_cancel else 'Откажи работата','abandon',False,'error')]
    if confirm_cancel:result.append(text('abandon-note','Натисни отново, за да спреш и почистиш неприетите дни. Прибраните стакове остават.','error'))
    return result

def settings_screen(options:dict,scope_days:list[dict],ai_available:bool,busy:bool,confirm_cancel:bool,budget_text:str='',has_work:bool=False,running:bool=False)->list[dict]:
    """busy locks the fields; running only blocks the start (Siril still works on other days)."""
    o=options
    def control(kind,ident,label,**extra):return field(kind,ident,label,value=o[ident],command='configure',disabled=busy,**extra)
    choices=lambda pairs:[dict(value=k,label=v) for k,v in pairs]
    children=[]
    if scope_days:children.append(text('scope','Пренастройване. Промените важат само за: '+', '.join(day_name(g) for g in scope_days)+'. Показани са настройките, с които са работили.','warning'))
    else:children.append(text('intro','Настрой обработката и натисни „Стартирай“. Настройките се запазват автоматично.'))
    def group(ident,label,items):return field('group',ident,label,children=[i for i in items if i])
    # Sections with a title; an untitled sub-group whose first field is a switch is turned on by that switch.
    children.append(group('g-siril','Siril',[control('input','executable','Siril CLI (siril-cli.exe)',maxLength=1024),button('choose-executable','Избери siril-cli.exe…','choose-executable',busy)]))
    children.append(group('g-parallel','Паралелна работа',[
        control('number','parallel','Брой Siril, които работят едновременно',min=1,max=MAX_PARALLEL,step=1),
        control('number','chunkSize',f'Кадри на една част (0 = без разделяне, иначе от {MIN_CHUNK})',min=0,max=MAX_CHUNK,step=10),
        control('number','cpu','CPU нишки · общо за всички Siril',min=1,max=os.cpu_count() or 1,step=1),
        control('number','memory','Дял от свободната RAM · общо',min=.05,max=.95,step=.05),
        text('budget',budget_text) if budget_text else None]))
    children.append(group('g-processing','Обработка',[
        control('checkbox','debayer','Debayer при CFA кадри'),control('checkbox','registration','Подравняване'),control('checkbox','mosaic','Пълно поле / мозайка'),
        control('checkbox','background','Премахване на градиент · степен 1'),
        control('select','normalize','Нормализация',options=choices([('addscale','Additive + scale'),('add','Additive'),('mulscale','Multiplicative + scale'),('mul','Multiplicative'),('none','Без нормализация')])),
        group('g-drizzle','',[control('checkbox','drizzle','Drizzle'),control('number','scale','Мащаб',min=.5,max=3,step=.1),control('number','pixfrac','Дял на пиксела',min=.1,max=1,step=.05)]),
        group('g-rejection','',[control('checkbox','rejection','Отхвърляне на пикселни отклонения'),control('number','sigmaLow','Долна sigma',min=.1,max=10,step=.1),control('number','sigmaHigh','Горна sigma',min=.1,max=10,step=.1)])]))
    calibration=[]
    for key,label in (('dark','Master Dark'),('flat','Master Flat'),('bias','Master Bias')):calibration+=[control('input',key,label,maxLength=1024),button('choose-'+key,'Избери '+label+'…','choose-'+key,busy)]
    children.append(group('g-calibration','Калибрация (по желание)',calibration))
    if ai_available:children.append(group('g-ai','AI филтър',[control('checkbox','aiFilter','Махни най-лошите кадри според AI оценителя'),control('number','aiPercent','Процент за махане от всеки ден',min=0,max=90,step=1)]))
    if not scope_days:children.append(group('g-after','След оценката',[control('checkbox','combine','Обедини приетите дни в общ стак')]))
    if running:children.append(text('running-note','Siril още работи по други дни. Стартирането ще е възможно, когато приключат.','warning'))
    children.append(button('start','Стартирай отново избраните дни' if scope_days else 'Стартирай','start',busy or running,'primary'))
    if has_work:children.append(button('back','Назад към работата','back',busy))
    children+=abandon_controls(confirm_cancel)
    return [field('group','settings','Настройки',children=children)]

def day_status_text(g:dict)->str:
    state=g.get('state');parts=g.get('parts') or [{}];count=len(parts)
    if g.get('repeat') and state!='approved':return 'За повторение'
    if state=='approved':
        value=f"Прибран · {len(g.get('usedIds',[]))}/{len(g['ids'])} кадъра"
        if count>1:value+=f' · {count} части'
        if g.get('aiRemovedIds'):value+=f" · AI махна {len(g['aiRemovedIds'])}"
        return value
    if state=='running':
        done=sum(p.get('state')=='done' for p in parts)
        return f"Обработва се · части {done}/{count} · {g.get('progress',0)} %" if count>1 else f"Обработва се · {g.get('progress',0)} %"
    if state=='merging':return 'Слива частите'
    if state=='review':return 'Готов за оценка'
    if state=='error':return 'Грешка: '+str(g.get('problem',''))[:160]
    if state=='paused':return 'Спрян'+(' · '+str(g.get('problem'))[:120] if g.get('problem') else '')
    if state=='skipped':return 'Пропуснат'
    return 'Чака'

def tone(g:dict)->str:
    state=g.get('state')
    if state=='approved':return 'success'
    if g.get('repeat') or state=='review':return 'warning'
    if state=='error':return 'error'
    if state in ('running','merging'):return 'primary'
    return 'normal'

def day_item(g):
    parts=len(g.get('parts') or [])
    description=str(len(g['ids']))+' кадъра'+(f' · {parts} части' if parts>1 else '')
    if g.get('problem') and g.get('state') in ('review','approved','running','merging'):description+=' · '+str(g['problem'])[:160]
    return dict(id=g['id'],label=day_name(g),description=description,status=day_status_text(g),tone=tone(g))

BUCKETS=[('list-work','В работа','select','Избери',lambda g:g['state'] in ACTIVE and not g.get('repeat')),
         ('list-review','За оценка','review','Оцени',lambda g:g['state']=='review' and not g.get('repeat')),
         ('list-repeat','За повторение','reconfigure','Пренастрой',lambda g:bool(g.get('repeat')) and g['state']!='approved'),
         ('list-done','Готови','select','Подробно',lambda g:g['state'] in ('approved','skipped'))]

def day_lists(groups:list[dict])->list[dict]:
    result=[]
    for ident,label,command,action,test in BUCKETS:
        items=[day_item(g) for g in groups if test(g)]
        if items:result.append(field('list',ident,label,items=items,command=command,actionLabel=action))
    return result

SLOT_TONE=dict(green='success',yellow='warning',red='error')
SLOT_WORD=dict(green='работи',yellow='проблем',red='не е включен')

def slot_item(s):
    detail=s.get('task') or 'Свободен'
    if s.get('pid'):detail+=f" · PID {s['pid']}"
    if s.get('cpu'):detail+=f" · {s['cpu']} CPU · RAM {round((s.get('memory') or 0)*100,1)} %"
    return dict(id=str(s['number']),label='Siril '+str(s['number']),description=detail,status='● '+SLOT_WORD[s['status']],tone=SLOT_TONE[s['status']])

def work_screen(state:dict,slots:list[dict],selected_slot:int,busy:bool,confirm_cancel:bool,image:str|None=None,combine:bool=False)->list[dict]:
    groups=state['groups'];selected=next((g for g in groups if g['id']==state.get('selected')),None)
    days=[text('summary',f"{len(groups)} дни · прибрани {sum(g['state']=='approved' for g in groups)} · за оценка {sum(g['state']=='review' for g in groups)}"+(' · Siril работи' if busy else ''))]
    days+=day_lists(groups)
    waiting=[g for g in groups if g['state'] in ('pending','paused','error') and not g.get('repeat')]
    if waiting and not busy:days.append(button('start','Продължи работата','start',False,'primary'))
    if selected:
        days.append(text('selection','Избран ден: '+day_name(selected)+' · '+day_status_text(selected),'normal' if tone(selected)=='primary' else tone(selected)))
        if image and selected['state'] in ('review','approved'):days.append(field('image','result','Резултат',value=image))
        if selected['state']=='review' and not selected.get('repeat'):
            days+=[button('accept','Приеми и прибери','accept',False,'primary'),button('repeat','Повтори','repeat'),button('preview','Отвори в прегледа','preview')]
        if selected.get('rejectedIds'):days.append(text('rejected',f"{len(selected['rejectedIds'])} кадъра не участват според Siril. Оригиналите остават в архива.",'warning'))
        if selected['state']=='error':days.append(button('retry','Повтори само неуспешните части','retry',busy))
        if selected.get('accepted'):days.append(button('graph','От кои кадри е направен','graph'))
    if state.get('graph'):days.append(field('table','sources','Изходни стакове и кадри · натисни за папката',command='navigate',columns=[dict(key='name',label='Източник'),dict(key='archive',label='Архив'),dict(key='location',label='Местоположение')],rows=state['graph'][:500]))
    singles=[g for g in groups if not g.get('combined')]
    if combine and not busy and singles and all(g['state'] in ('approved','skipped') for g in singles) and sum(g['state']=='approved' for g in singles)>1:
        days.append(button('merge','Обедини приетите дни','merge'))
    days+=abandon_controls(confirm_cancel)
    current=next((s for s in slots if s['number']==selected_slot),slots[0] if slots else None)
    sirils=[field('list','slots','Siril процеси',items=[slot_item(s) for s in slots],command='slot',actionLabel='Конзола')]
    if current:
        sirils+=[field('console','console','Конзола · Siril '+str(current['number']),value='\n'.join(current.get('log',[]))[-80000:]),
                 button('visual','Отвори визуално Siril '+str(current['number']),'visual',False)]
    return [field('group','days','Избрани дни',children=days),field('group','sirils','Siril',children=sirils)]

def activity(groups:list[dict],busy:bool,confirm:bool=False)->list[dict]:
    unfinished=[g for g in groups if g['state'] not in ('approved','skipped')]
    if not unfinished:return []
    objects=sorted({g['object'] for g in unfinished})
    item=dict(id='siril-work',label='Siril · '+', '.join(objects)[:120],description=f'{len(unfinished)} незавършени дни',
              status='Работи' if busy else 'Прекъсната / чака',tone='primary' if busy else 'warning')
    return [field('list','unfinished-siril','Незавършена Siril работа',items=[item],command='resume',actionLabel='Продължи'),
            button('finish','Потвърди: приключи и почисти' if confirm else 'Приключи Siril работата','finish',False,'error')]
