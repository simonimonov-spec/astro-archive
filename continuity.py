"""Stable subscriptions and date-based campaign periods, independent of the host."""
from datetime import date
import hashlib
import unicodedata


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC',value).casefold().split())


def family_key(name):
    return hashlib.sha256(normalized(name).encode()).hexdigest()[:24]


def binding_pairs(watch):
    return {(b['campaign']['id'],b['team']['id']) for b in [watch,*watch.get('history',[])]}


def resolve_campaign(watch, campaigns):
    matches=[c for c in campaigns if normalized(c['name'])==normalized(watch['campaign']['name'])]
    return (matches[0],'ready') if len(matches)==1 else (None,'missing-campaign' if not matches else 'ambiguous-campaign')


def resolve_team(watch, teams):
    country=normalized(watch['team'].get('country',''))
    matches=[t for t in teams if normalized(t['name'])==normalized(watch['team']['name'])
             and (not country or normalized(t.get('country',''))==country)]
    return (matches[0],'ready') if len(matches)==1 else (None,'missing-team' if not matches else 'ambiguous-team')


def timeline(name, schedule, current=None, today=None):
    today=today or date.today();periods=[]
    for p in [*schedule,*([dict(current,name=name)] if current else [])]:
        if normalized(p['name'])!=normalized(name):continue
        try:start,end=date.fromisoformat(p['start']),date.fromisoformat(p['end'])
        except (ValueError,KeyError,TypeError):continue
        if start>end:continue
        row=dict(name=name,start=start.isoformat(),end=end.isoformat())
        if row not in periods:periods.append(row)
    periods.sort(key=lambda p:(p['start'],p['end']))
    active=next((p for p in reversed(periods) if p['start']<=today.isoformat()<=p['end']),None)
    upcoming=next((p for p in periods if p['start']>today.isoformat()),None)
    ended=next((p for p in reversed(periods) if p['end']<today.isoformat()),None)
    return dict(state='active' if active else 'upcoming' if upcoming else 'ended' if ended else 'unknown',active=active,next=upcoming,last=ended)


def dates(period):
    return date.fromisoformat(period['start']).strftime('%d.%m.%Y')+' – '+date.fromisoformat(period['end']).strftime('%d.%m.%Y')


def timeline_text(value):
    if value['active']:text='Активна · '+dates(value['active'])
    elif value['state']=='upcoming':text='Предстояща · '+dates(value['next'])
    elif value['last']:text='Приключила · '+dates(value['last'])
    else:text='Периодът още не е потвърден от IASC.'
    if value['active'] or value['last']:
        text+=' · Следваща: '+(dates(value['next']) if value['next'] else 'IASC още не е публикувал дата.')
    return text
