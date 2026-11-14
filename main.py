"""IASC campaign downloads. All integration uses the public Plugin Context."""
from __future__ import annotations

import copy
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import threading
import time

from . import downloads
from .login import BrowserLogin
from .login import requires_session
from .site import Client, LoginRequired
from .state import Store
from .workflows import Workflows
from .continuity import binding_pairs, family_key, resolve_campaign, resolve_team, timeline, timeline_text, dates

STATUS = {'new':'Нов','downloading':'Изтегля се','downloaded':'Изтеглен и проверен',
          'error':'Грешка','interrupted':'Прекъснат — може повторение'}
REPORT = {'yes':'Отбелязан в IASC','no':'Не е отбелязан','unknown':'Неизвестно'}


def field(kind, ident, **values):
    return dict(type=kind,id=ident,**values)


def option(value, label):
    return dict(value=str(value),label=str(label))


def schedule_slot(now, hours):
    slots = []
    for day in (now-timedelta(days=1),now):
        for hour in hours:
            h,m = map(int,hour.split(':'))
            candidate = day.replace(hour=h,minute=m,second=0,microsecond=0)
            if candidate<=now:
                slots.append(candidate)
    return max(slots).isoformat(timespec='minutes')


class CombinedCancel:
    def __init__(self, first, second):
        self.first, self.second = first, second

    def is_set(self):
        return self.first.is_set() or self.second.is_set()

    def wait(self, seconds):
        end = time.monotonic()+seconds
        while not self.is_set() and time.monotonic()<end:
            self.second.wait(min(.2,max(0,end-time.monotonic())))
        return self.is_set()


class Campaigns:
    def __init__(self, context):
        self.ctx = context
        self.folder = Path(context.storage())
        self.store = Store(self.folder)
        self.client = Client(self.folder/'session.dpapi')
        self.closed = threading.Event()
        self.lock = threading.RLock()
        self.options = dict(campaign='',watches=[],root='',archiveId='',monitor=True,autoDownload=True,showCampaign=True,showTeam=True,showSiteStatus=True,
                            morning='08:00',noon='12:00',evening='20:00',maxMib=2048,viewCampaign='all',viewTeam='all')
        self.options.update(context.settings())
        self.register_archive()
        self.selected, self.team_choice, self.watch_choice = '', '', ''
        self.filter, self.page = 'all', 0
        self.busy, self.problem, self.phase, self.index = False, '', '', ''
        self.cancel = None
        self.last = self.store.cache('lastCheck','')
        if not any(requires_session(c.name) and not c.is_expired() for c in self.client.jar):
            self.store.save_cache('sessionState','Необходим е вход в IASC за Image Sets.')
        for name, handler in {
            'refresh':self.refresh,'campaigns':self.load_campaigns,'campaign':self.change_campaign,
            'archive-refresh':self.refresh,
            'tracking-check':self.check_tracking,
            'team':self.change_team,'watch':self.add_watch,'watch-choice':self.change_watch,
            'unwatch':self.remove_watch,'preferences':self.preferences,'choose':self.choose_archive,
            'login':self.login,'logout':self.logout,'check':self.check,'stop':self.stop,
            'row':self.select_row,'filter':self.change_filter,'previous':self.previous,'next':self.next,
            'download':self.download_selected,'download-new':self.download_new,'verify':self.verify,
            'navigate':self.navigate,'local-report':self.local_report,'read':self.mark_read,
            'copy':self.copy,'status':self.status
        }.items():
            context.command(name,handler)
        self.work=Workflows(self)
        self.persist()
        self.publish()
        self.thread = threading.Thread(target=self.watch_loop,daemon=True,name='IASC-monitor')
        self.thread.start()

    def persist(self):
        self.ctx.save_settings(self.options)

    def register_archive(self):
        layout=dict(version=1,preset='frames',levels=[dict(field='filename',formula='{filename}',icon='folder',columns=['files','frames','totalExposure','size']) for _ in range(3)],
                    fileFormula='{filename}',fileColumns=['exposure','observed','filter','size','sensor','resolution'])
        archive=self.ctx.archives('register',dict(key='campaigns',name='IASC · кампании',kind='frames',structure=layout,
            managedStructure=True,previousId=self.options.get('archiveId')))
        self.options.update(archiveId=archive['id'],root=archive.get('path') or '',archiveName=archive['name'])
        self.persist()

    def sync_archive(self):
        archive=next((a for a in self.ctx.archives('list')['archives'] if a['id']==self.options['archiveId']),None)
        if archive is None:
            return False
        changed=self.options['root']!=(archive.get('path') or '') or self.options.get('archiveName')!=archive['name']
        if changed:
            self.options.update(root=archive.get('path') or '',archiveName=archive['name'])
            self.persist()
        return changed

    def refresh(self, p):
        self.sync_archive()
        self.publish()
        return dict(refreshed=True)

    def task(self, title, work, tier='important'):
        with self.lock:
            if self.busy:
                raise ValueError('IASC вече изпълнява задача. Изчакай или натисни „Спри“.')
            self.busy, self.problem, self.phase = True, '', title
            self.cancel = threading.Event()
        self.publish()
        def run(cancel, progress):
            combined = CombinedCancel(CombinedCancel(cancel,self.closed),self.cancel)
            def report(done,total,description):
                with self.lock:
                    self.phase = description[:400]
                progress(done,total,description)
            try:
                return work(combined,report)
            except Exception as error:
                with self.lock:
                    self.problem = str(error)[:500]
                raise
            finally:
                with self.lock:
                    self.busy = False
                    self.cancel = None
                    self.phase = ''
                if not self.closed.is_set():
                    self.publish()
        try:
            return self.ctx.task(title,run,'archive',tier=tier)
        except BaseException:
            with self.lock:
                self.busy = False
                self.cancel = None
            self.publish()
            raise

    def load_campaigns(self, p):
        def work(cancel, progress):
            progress(0,1,'Зареждане на кампаниите от IASC…')
            rows = self.public_refresh(cancel)
            self.period_notifications()
            progress(1,1,f'Налични кампании: {len(rows)}')
            return dict(campaigns=len(rows))
        return self.task('IASC · кампании',work,tier='normal')

    def change_campaign(self, p):
        ident = p['values']['campaign']
        known = self.store.cache('campaigns',[])
        if not any(c['id']==ident for c in known):
            raise ValueError('Обнови и избери налична кампания.')
        with self.lock:
            if self.busy:
                raise ValueError('Изчакай текущата задача.')
            self.options['campaign'], self.team_choice = ident, ''
            self.persist()
        def work(cancel, progress):
            progress(0,1,'Зареждане на екипите…')
            directory = self.client.directory(ident)
            rows = directory['teams']
            downloads.check_cancel(cancel)
            self.store.save_cache('teams-'+ident,rows)
            if directory.get('period'):self.store.save_cache('period-'+ident,directory['period'])
            progress(1,1,f'Налични екипи: {len(rows)}')
            return dict(teams=len(rows))
        return self.task('IASC · екипи',work,tier='normal')

    def public_refresh(self, cancel):
        rows=self.client.campaigns()
        downloads.check_cancel(cancel)
        self.store.save_cache('campaigns',rows)
        try:
            periods=self.client.schedule()
            downloads.check_cancel(cancel)
            self.store.save_cache('schedule',periods)
            self.store.save_cache('scheduleProblem','')
        except InterruptedError:raise
        except Exception as error:
            self.store.save_cache('scheduleProblem','Календарът не е обновен: '+str(error)[:300])
        return rows

    def notify_once(self, key, signature, title, message, tier='normal'):
        notices=self.store.cache('notices',{})
        if notices.get(key)==signature:return
        def work(cancel,progress):
            progress(1,1,message)
            return dict(message=message)
        self.ctx.task(title,work,'archive',tier=tier)
        notices[key]=signature
        self.store.save_cache('notices',notices)

    def campaign_timelines(self, today=None):
        result={};schedule=self.store.cache('schedule',[])
        for watch in self.options['watches']:
            name=watch['campaign']['name'];key=family_key(name)
            if key not in result:
                period=self.store.cache('period-'+watch['campaign']['id'],watch.get('period'))
                result[key]=dict(name=name,**timeline(name,schedule,period,today))
        return result

    def period_notifications(self, today=None):
        today=today or date.today();previous=self.store.cache('periodStates',{})
        for key,current in self.campaign_timelines(today).items():
            old=previous.get(key);message=current['name']+' · '+timeline_text(current)
            if current['state']=='ended':
                self.notify_once('period-'+key,json.dumps(current,sort_keys=True),'IASC · кампанията приключи',message,'important')
            elif current['next'] and (not old or old.get('next')!=current['next']):
                self.notify_once('next-'+key,json.dumps(current['next'],sort_keys=True),'IASC · следващ период',message)
            if current['active']:
                active=current['active']
                if old and (not old.get('active') or old['active']['start']!=active['start']):
                    self.notify_once('started-'+key,active['start'],'IASC · нов период започна',message)
                remaining=(date.fromisoformat(active['end'])-today).days
                if remaining<=3:
                    self.notify_once('ending-'+key,active['start'],'IASC · наближава краят',current['name']+' приключва на '+date.fromisoformat(active['end']).strftime('%d.%m.%Y')+'. '+timeline_text(current),'important')
            previous[key]=current
        self.store.save_cache('periodStates',previous)

    def refresh_tracking(self, cancel, progress):
        try:campaigns=self.public_refresh(cancel)
        except InterruptedError:raise
        except Exception as error:
            statuses=self.store.cache('watchStatus',{})
            for w in self.options['watches']:
                statuses[w['key']]=dict(ok=False,code='check-error',checked=datetime.now().isoformat(timespec='seconds'),problem='Списъкът с кампании не е проверен: '+str(error)[:250])
            self.store.save_cache('watchStatus',statuses)
            raise
        directories={};statuses=self.store.cache('watchStatus',{})
        watches=copy.deepcopy(self.options['watches']);ready=[];errors=[]
        messages={'missing-campaign':'Кампанията не е налична в текущия списък на IASC.',
                  'ambiguous-campaign':'Има няколко кампании със същото име. Избери правилната ръчно.',
                  'missing-team':'Екипът не е наличен в тази кампания.',
                  'ambiguous-team':'Има няколко екипа със същото име. Избери правилния ръчно.'}
        for index,watch in enumerate(watches):
            downloads.check_cancel(cancel);progress(index,max(1,len(watches)),'Проверка на екип · '+watch['team']['name'])
            old_status=statuses.get(watch['key'],{})
            campaign,code=resolve_campaign(watch,campaigns);team=None;period=None
            if campaign:
                ident=campaign['id']
                if ident not in directories:
                    try:
                        directories[ident]=self.client.directory(ident)
                        self.store.save_cache('teams-'+ident,directories[ident]['teams'])
                        if directories[ident].get('period'):self.store.save_cache('period-'+ident,directories[ident]['period'])
                    except Exception as error:directories[ident]=error
                directory=directories[ident]
                if isinstance(directory,Exception):
                    code='check-error';problem='Проверката на екипа не успя: '+str(directory)[:250]
                else:
                    team,code=resolve_team(watch,directory['teams']);period=directory.get('period')
            if code!='ready':
                problem=problem if code=='check-error' else messages[code]
                statuses[watch['key']]=dict(ok=False,code=code,checked=datetime.now().isoformat(timespec='seconds'),problem=problem)
                errors.append(watch['team']['name']+': '+problem)
                if code!='check-error':
                    self.notify_once('team-'+watch['key'],code,'IASC · предупреждение за екип',watch['team']['name']+' · '+problem+' Следенето е запазено. Можеш ръчно да премахнеш екипа и да потърсиш отново.','important')
                continue
            changed=(campaign['id'],team['id'])!=(watch['campaign']['id'],watch['team']['id'])
            if changed:
                history=watch.setdefault('history',[])
                old=dict(campaign=watch['campaign'],team=watch['team'])
                if old not in history:history.append(old)
            watch.update(campaign=campaign,team=team,period=period or {})
            if changed or old_status.get('code') in {*messages,'check-error','access-error'}:
                self.notify_once('team-'+watch['key'],'ready:'+campaign['id']+':'+team['id'],'IASC · екипът е наличен',team['name']+' е намерен в '+campaign['name']+'. Следенето продължава.')
            statuses[watch['key']]=dict(ok=False,available=True,code='available',checked=datetime.now().isoformat(timespec='seconds'),problem='')
            ready.append(watch)
        downloads.check_cancel(cancel)
        with self.lock:
            self.options['watches']=watches
            self.persist()
        self.store.save_cache('watchStatus',statuses)
        self.period_notifications()
        return ready,errors,statuses

    def check_tracking(self, p):
        def work(cancel,progress):
            ready,errors,statuses=self.refresh_tracking(cancel,progress)
            if errors:raise ValueError('\n'.join(errors)[:500])
            progress(len(ready),max(1,len(self.options['watches'])),f'Потвърдени екипи: {len(ready)}. Датите са обновени; Image Sets не са изтегляни.')
            return dict(availableTeams=len(ready),downloaded=0)
        return self.task('IASC · проверка на екипи и дати',work,tier='normal')

    def change_team(self, p):
        self.team_choice = p['values']['team']
        self.publish()
        return dict(selected=True)

    def change_watch(self, p):
        self.watch_choice = p['values']['watched']
        self.publish()
        return dict(selected=True)

    def add_watch(self, p):
        with self.lock:
            campaign = next(c for c in self.store.cache('campaigns',[]) if c['id']==self.options['campaign'])
            team = next((t for t in self.store.cache('teams-'+campaign['id'],[]) if t['id']==p.get('values',{}).get('team',self.team_choice)),None)
            if team is None:
                raise ValueError('Избери екип от кампанията.')
            key = campaign['id']+':'+team['id']
            if not any(w['key']==key for w in self.options['watches']):
                if len(self.options['watches'])>=50:
                    raise ValueError('Могат да се следят до 50 екипа.')
                self.options['watches'].append(dict(key=key,campaign=campaign,team=team))
                self.persist()
        self.publish()
        return dict(message='Екипът е добавен за следене. Използвай „Провери сега“.')

    def remove_watch(self, p):
        key = p.get('selected',{}).get('id') or p.get('values',{}).get('watched',self.watch_choice)
        with self.lock:
            self.options['watches'] = [w for w in self.options['watches'] if w['key']!=key]
            self.watch_choice = ''
            self.persist()
        self.publish()
        return dict(message='Следенето е спряно за този екип. Изтеглените кадри и историята са запазени.')

    def preferences(self, p):
        values = p.get('values',{})
        with self.lock:
            updated = copy.deepcopy(self.options)
            for key in ('monitor','autoDownload','showCampaign','showTeam','showSiteStatus','morning','noon','evening','maxMib'):
                if key in values:
                    updated[key] = values[key]
            for key in ('morning','noon','evening'):
                try:
                    datetime.strptime(updated[key],'%H:%M')
                except (ValueError,TypeError):
                    raise ValueError('Часовете са във формат ЧЧ:ММ, например 08:00.') from None
                if len(updated[key])!=5:
                    raise ValueError('Използвай двуцифрен час и минути: ЧЧ:ММ.')
            if len({updated[k] for k in ('morning','noon','evening')})!=3:
                raise ValueError('Задай три различни часа за проверка.')
            if type(updated['maxMib']) is not int or not 16<=updated['maxMib']<=8192:
                raise ValueError('Лимитът е цяло число от 16 до 8192 MB.')
            if any(type(updated[key]) is not bool for key in ('monitor','autoDownload','showCampaign','showTeam','showSiteStatus')):
                raise ValueError('Невалидна отметка.')
            self.options = updated
            self.persist()
        self.publish()
        return dict(saved=True)

    def choose_archive(self, p):
        if self.busy:
            raise ValueError('Изчакай текущата задача преди смяна на папката.')
        chosen = self.ctx.archives('choose',{'title':'Избери отделна празна папка за IASC архив'})
        if not chosen.get('path'):
            return dict(cancelled=True)
        root = Path(chosen['path']).resolve(strict=True)
        existing = self.ctx.archives('list')['archives']
        if any(a.get('path') and (root.is_relative_to(Path(a['path']).resolve()) or Path(a['path']).resolve().is_relative_to(root))
               and not (a.get('ownerPlugin')=='iasc-campaigns' and Path(a['path']).resolve()==root) for a in existing):
            raise ValueError('Избери отделна папка извън другите архиви.')
        marker = root/'.iasc-archive.json'
        owned = any(a.get('ownerPlugin')=='iasc-campaigns' and a.get('path') and Path(a['path']).resolve()==root for a in existing)
        if any(root.iterdir()) and not marker.is_file() and not owned:
            raise ValueError('Избери празна папка или вече създаден IASC архив.')
        added = self.ctx.archives('attach',dict(id=self.options['archiveId'],path=str(root),name=self.options.get('archiveName','IASC · кампании'),kind='frames',initialize=True))
        if not marker.exists():
            marker.write_text(json.dumps(dict(version=1,plugin='iasc-campaigns')),encoding='utf-8')
        with self.lock:
            self.options.update(root=str(root),archiveId=added['id'])
            self.persist()
        self.publish()
        return dict(message='IASC архивът е свързан. Комплектите ще се подреждат по кампания, екип и Image Set Name.',archiveSettings=added['archive'])

    def login(self, p):
        def work(cancel, progress):
            cookies = BrowserLogin(self.folder/'Browser').run(cancel,progress)
            downloads.check_cancel(cancel)
            self.client.set_cookies(cookies)
            # The first selected team request will confirm actual dataset access.
            self.store.save_cache('sessionState','Сесията е получена; достъпът се проверява при „Провери сега“.')
            progress(1,1,'IASC входът е завършен. Използвай „Провери сега“.')
            return dict(connected=True)
        return self.task('IASC · вход през официалния сайт',work,tier='normal')

    def logout(self, p):
        if self.busy:
            raise ValueError('Първо спри текущата IASC задача.')
        self.client.forget()
        self.store.save_cache('sessionState','Необходим е вход.')
        self.publish()
        return dict(message='Локалната сесия на плъгина е премахната. Отчетите и кадрите са запазени.')

    def scan(self, cancel, progress, download_new):
        self.sync_archive()
        watches,errors,watch_status=self.refresh_tracking(cancel,progress)
        added = 0
        for index, watch in enumerate(watches):
            downloads.check_cancel(cancel)
            progress(index,len(watches),'Проверка · '+watch['team']['name'])
            try:
                rows = self.client.sets(watch['campaign']['id'],watch['team']['id'])
                downloads.check_cancel(cancel)
                added += self.store.merge(watch['campaign'],watch['team'],rows,watch.get('period'))
                self.store.save_cache('sessionState','Достъпът до Image Sets е потвърден.')
                watch_status[watch['key']]=dict(ok=True,code='ready',checked=datetime.now().isoformat(timespec='seconds'),problem='')
            except InterruptedError:
                raise
            except Exception as error:
                errors.append(watch['team']['name']+': '+str(error))
                watch_status[watch['key']]=dict(ok=False,code='access-error' if isinstance(error,LoginRequired) else 'check-error',checked=datetime.now().isoformat(timespec='seconds'),problem=str(error)[:300])
                if isinstance(error,LoginRequired):
                    self.store.save_cache('sessionState','Сесията липсва или е изтекла. Влез отново.')
                    break
            cancel.wait(.5)
        self.last = datetime.now().isoformat(timespec='seconds')
        self.store.save_cache('lastCheck',self.last)
        self.store.save_cache('lastErrors',errors)
        self.store.save_cache('watchStatus',watch_status)
        if errors:
            self.problem = '\n'.join(errors)[:500]
        self.publish()
        if added:
            def notify(c,p):
                p(1,1,f'Намерени са {added} нови IASC комплекта. Отвори Архив → IASC · Image Sets.')
                return dict(newSets=added)
            self.ctx.task(f'IASC · {added} нови комплекта',notify,'archive',tier='important')
        if download_new and self.options['root']:
            self.transfer(self.pending(),cancel,progress)
        progress(len(watches),max(1,len(watches)),f'IASC проверка: {added} нови комплекта'+(f'; {len(errors)} проблема' if errors else ''))
        if errors:
            raise ValueError(self.problem)
        return dict(newSets=added)

    def check(self, p):
        if not self.options['watches']:
            raise ValueError('Добави поне един екип от настройките на IASC плъгина.')
        return self.task('IASC · проверка за нови комплекти',lambda c,r:self.scan(c,r,self.options['autoDownload']),tier='normal')

    def scoped_watches(self):
        return [w for w in self.options['watches'] if self.options['viewCampaign'] in ('all',family_key(w['campaign']['name']))
                and self.options['viewTeam'] in ('all',w['key'])]

    def tracked_rows(self, rows, scoped=False):
        watches=self.scoped_watches() if scoped else self.options['watches']
        pairs=set().union(*(binding_pairs(w) for w in watches)) if watches else set()
        return [r for r in rows if (r['campaign'],r['team']) in pairs]

    def pending(self, scoped=False, rows=None):
        watches=self.scoped_watches() if scoped else self.options['watches']
        statuses=self.store.cache('watchStatus',{})
        watches=[w for w in watches if not statuses.get(w['key']) or statuses[w['key']].get('ok') or statuses[w['key']].get('available')]
        return [row for row in (self.store.rows() if rows is None else rows) if row['status'] in ('new','interrupted') and any(
            (row['campaign'],row['team'])==(w['campaign']['id'],w['team']['id']) and (not w.get('period',{}).get('start') or row.get('period')==w['period']['start']) for w in watches)]

    def transfer(self, rows, cancel, progress):
        self.sync_archive()
        root, archive_id, limit = self.options['root'], self.options['archiveId'], self.options['maxMib']
        if not root or not archive_id:
            raise ValueError('Първо избери отделната IASC архивна папка от настройките.')
        problems, saved = [], 0
        try:
            for row in rows:
                downloads.check_cancel(cancel)
                self.store.update(row['id'],status='downloading',error='')
                self.publish()
                try:
                    result = downloads.download(self.client,row,root,self.folder/'Downloads',cancel,progress,limit)
                    self.store.update(row['id'],status='downloaded',error='',**result)
                    saved += 1
                except InterruptedError:
                    self.store.update(row['id'],status='interrupted',error='Отменено; може да се повтори.')
                    raise
                except Exception as error:
                    self.store.update(row['id'],status='error',error=str(error)[:500])
                    problems.append(row['name']+': '+str(error))
                finally:
                    self.publish()
        finally:
            if saved:
                try:
                    self.ctx.archives('refresh',{'id':archive_id})
                except PermissionError:
                    if not self.closed.is_set():
                        raise
        if problems:
            raise ValueError('\n'.join(problems)[:500])
        progress(1,1,f'Запазени и проверени комплекти: {saved}. Архивният индекс се обновява отделно.')
        return dict(downloaded=saved)

    def download_selected(self, p):
        row = self.store.get(self.selected)
        return self.task('IASC · изтегляне на комплект',lambda c,r:self.transfer([row],c,r))

    def download_new(self, p):
        rows = self.pending(scoped=True)
        if not rows:
            return dict(message='Няма нови неизтеглени комплекти в следените екипи.')
        return self.task('IASC · изтегляне на новите комплекти',lambda c,r:self.transfer(rows,c,r))

    def stop(self, p):
        if self.cancel is not None:
            self.cancel.set()
        return dict(message='Заявено е безопасно спиране на IASC задачата.')

    def select_row(self, p):
        self.selected = self.store.get(p['selected']['key'])['id']
        self.index = ''
        self.publish()
        return dict(selected=True)

    def change_filter(self, p):
        v = p.get('values',{})
        campaign=v.get('viewCampaign',self.options['viewCampaign']);team=v.get('viewTeam',self.options['viewTeam'])
        if campaign not in {'all',*(family_key(w['campaign']['name']) for w in self.options['watches'])}:raise ValueError('Избери следена кампания.')
        if campaign!=self.options['viewCampaign']:team='all'
        if team not in {'all',*(w['key'] for w in self.options['watches'] if campaign in ('all',family_key(w['campaign']['name'])))}:raise ValueError('Избери следен екип.')
        status=v.get('filter',self.filter)
        if status not in {'all',*STATUS}:raise ValueError('Невалидно състояние.')
        self.options.update(viewCampaign=campaign,viewTeam=team);self.persist()
        self.filter,self.page,self.selected=status,0,''
        self.publish()
        return dict(filtered=True)

    def previous(self, p):
        self.page = max(0,self.page-1)
        return self.refresh(p)

    def next(self, p):
        self.page += 1
        return self.refresh(p)

    def verify(self, p):
        row = self.store.get(self.selected)
        def work(cancel, progress):
            progress(0,1,'Проверка на файловете и SHA-256…')
            if not downloads.verify_target(row,cancel,full=True):
                self.store.update(row['id'],status='error',error='Файл липсва или е променен. Съществуващата папка не се презаписва.')
                raise ValueError('Комплектът е променен или непълен. Съществуващите файлове са запазени.')
            summary = self.ctx.host('archive.summary',dict(archiveId=self.options['archiveId'],relativePath=Path(row['target']).relative_to(self.options['root']).as_posix()))
            expected = sum(Path(f['name']).suffix.lower() in downloads.FITS for f in json.loads(row['manifest']))
            self.index = f"Индекс: {summary['files']} / {expected} FITS файла"+(' · обновява се' if summary.get('building') or summary.get('partial') else '')
            self.store.update(row['id'],status='downloaded',error='')
            progress(1,1,'Файловете са потвърдени. '+self.index)
            return dict(verified=True,indexed=summary['files'],expected=expected)
        return self.task('IASC · проверка на запазения комплект',work,tier='normal')

    def navigate(self, p):
        row = self.store.get(self.selected)
        if row['status']!='downloaded':
            raise ValueError('Първо изтегли комплекта успешно.')
        relative = Path(row['target']).resolve().relative_to(Path(self.options['root']).resolve()).as_posix()
        return dict(navigation=dict(archiveId=self.options['archiveId'],relativePath=relative))

    def local_report(self, p):
        row = self.store.get(self.selected)
        self.store.update(row['id'],local_report=int(p['values'].get('local-report',False)))
        self.publish()
        return dict(message='Локалният статус е запазен ръчно. Не е изпращан отчет към IASC.')

    def mark_read(self, p):
        self.store.mark_read()
        return self.refresh(p)

    def copy(self, p):
        rows = [r for r in self.tracked_rows(self.store.rows(slim=True),True) if self.filter in ('all',r['status'])]
        text = '\n'.join('\t'.join((r['campaign_name'],r['team_name'],r['name'],STATUS[r['status']],REPORT.get(r['remote_report'],'Неизвестно'))) for r in rows)
        return dict(copyText='Кампания\tЕкип\tImage Set Name\tИзтегляне\tОтчет в IASC\n'+text)

    def status(self, p):
        rows = self.store.rows(slim=True)
        return dict(busy=self.busy,total=len(rows),new=sum(r['unread'] for r in rows),
                    downloaded=sum(r['status']=='downloaded' for r in rows),lastCheck=self.last,problem=self.problem)

    def publish(self):
        if self.closed.is_set():
            return
        with self.lock:
            rows = self.tracked_rows(self.store.rows(slim=True))
            campaigns = self.store.cache('campaigns',[])
            campaign = self.options['campaign']
            team_rows = self.store.cache('teams-'+campaign,[])
            team_ids = {t['id'] for t in team_rows}
            if self.team_choice not in team_ids:
                self.team_choice = ''
            watches = self.options['watches']
            families={family_key(w['campaign']['name']):w['campaign']['name'] for w in watches}
            if self.options['viewCampaign'] not in {'all',*families}:self.options.update(viewCampaign='all',viewTeam='all');self.persist()
            view_teams=[w for w in watches if self.options['viewCampaign'] in ('all',family_key(w['campaign']['name']))]
            if self.options['viewTeam'] not in {'all',*(w['key'] for w in view_teams)}:self.options['viewTeam']='all';self.persist()
            pending = bool(self.pending(scoped=True,rows=rows))
            if self.watch_choice not in {w['key'] for w in watches}:
                self.watch_choice = ''
            session_state=self.store.cache('sessionState','')
            connected=any(requires_session(c.name) and not c.is_expired() for c in self.client.jar) and not any(s in session_state.lower() for s in ('изтекла','необходим','липсва'))
            connection=[field('metric','session',label='Връзка с IASC',value='Влязъл в IASC',tone='success'),
                field('button','logout',label='Изход от IASC',command='logout',disabled=self.busy,help='Изключва входа на този плъгин. За следваща проверка ще трябва да влезеш отново.')] if connected else [
                field('text','session',value='Влез в IASC, за да заредиш комплектите.',tone='warning'),
                field('button','login',label='Вход в IASC',command='login',disabled=self.busy,tone='primary')]
            watch_status=self.store.cache('watchStatus',{})
            items=[]
            for watch in watches:
                status=watch_status.get(watch['key'],{})
                code=status.get('code','')
                label=('Следене без проблем' if self.options['monitor'] else 'Последната проверка е успешна') if status.get('ok') else ('Екипът е наличен · Image Sets още не са проверени' if code=='available' else 'Екипът не е наличен' if code=='missing-team' else 'Кампанията не е налична' if code=='missing-campaign' else 'Необходим е ръчен избор' if code.startswith('ambiguous-') else 'Проверката не успя') if status else 'Очаква първа проверка'
                items.append(dict(id=watch['key'],label=watch['team']['name'],description=watch['campaign']['name'],
                    status=label+(' · '+status.get('problem','') if status and status.get('problem') else ''),tone='success' if status.get('ok') or status.get('available') else 'warning' if status else 'normal'))
            timelines=self.campaign_timelines()
            calendar=[field('text','period-'+key,span=2,value=c['name']+' · '+timeline_text(c),tone='success' if c['state']=='active' else 'warning' if c['state']=='ended' else 'normal') for key,c in timelines.items()]
            schedule_problem=self.store.cache('scheduleProblem','')
            options = [field('text','intro',span=2,value='Избери кампания, добави екипите и следи новите Image Sets.'),*connection,
                field('select','campaign',label='Кампания',value=campaign if campaign in {c['id'] for c in campaigns} else '',
                      options=[option('','Избери кампания')]+[option(c['id'],c['name']) for c in campaigns],command='campaign',disabled=self.busy),
                field('button','campaigns',label='Обнови кампаниите' if campaigns else 'Зареди кампаниите',command='campaigns',disabled=self.busy),
                field('select','team',label='Екип в избраната кампания',value=self.team_choice,options=[option('','Избери екип')]+[option(t['id'],t['name']) for t in team_rows],command='team',disabled=self.busy),
                field('button','watch',label='Следи този екип',command='watch',disabled=self.busy or not self.team_choice),
                field('list','watched',label=f'Следени екипи ({len(watches)})',span=2,items=items,command='unwatch',actionLabel='Премахни',disabled=self.busy),
                field('button','tracking-check',label='Провери екипите и датите',command='tracking-check',disabled=self.busy or not watches),
                *calendar,
                field('text','tracking-help',span=2,value='Екипите се запазват и за следващи периоди. При липсващ или нееднозначен екип има само предупреждение. „Премахни“ спира следенето ръчно, без да изтрива кадри или история.'),
                field('text','archive-path',label='Отделен IASC архив',span=2,value=self.options['root'] or 'Не е избрана папка. Изтеглянията изчакват избора ѝ.'),
                field('button','choose',label='Избери отделна архивна папка',command='choose',disabled=self.busy),
                field('checkbox','monitor',label='Автоматична проверка три пъти дневно',value=self.options['monitor'],command='preferences'),
                field('checkbox','autoDownload',label='Изтегляй новите комплекти автоматично',value=self.options['autoDownload'],command='preferences'),
                *[field('input',key,label=label,value=self.options[key],maxLength=5,command='preferences') for key,label in [('morning','Сутрин · ЧЧ:ММ'),('noon','На обяд · ЧЧ:ММ'),('evening','Вечер · ЧЧ:ММ')]],
                field('number','maxMib',label='Максимален ZIP размер · MB',value=self.options['maxMib'],min=16,max=8192,step=1,command='preferences'),
                field('text','clock-help',span=2,value='Часовете са по часовника на този компютър. След пропуснати часове при затворена програма се прави една наваксваща проверка. Настройките се запазват автоматично.')]
            self.ctx.publish('options',options)
            self.ctx.publish('archive-options',[
                field('text','archive-name',span=2,value=self.options.get('archiveName','IASC · кампании')+' · ПЛЪГИН'),
                field('text','path',span=2,value='Папка: '+(self.options['root'] or 'избери местоположение отгоре и запази архивните места.')),
                field('text','hierarchy',span=2,value='Физическата подредба е кампания → екип → Image Set Name. Формулите и колоните отгоре управляват изгледа, без да разделят комплектите.'),
                field('checkbox','showCampaign',label='Показвай кампанията в IASC списъка',value=self.options['showCampaign'],command='preferences'),
                field('checkbox','showTeam',label='Показвай екипа в IASC списъка',value=self.options['showTeam'],command='preferences'),
                field('checkbox','showSiteStatus',label='Показвай сайтовите отметки на IASC',value=self.options['showSiteStatus'],command='preferences'),
                field('button','choose',label='Избери папка за този архив',command='choose',disabled=self.busy)])
            visible = [r for r in self.tracked_rows(rows,True) if self.filter=='all' or r['status']==self.filter]
            self.page = min(self.page,max(0,(len(visible)-1)//100))
            page = visible[self.page*100:(self.page+1)*100]
            selected = next((r for r in visible if r['id']==self.selected),None)
            table = [dict(key=r['id'],name=r['name'],campaign=r['campaign_name'],team=r['team_name'],period=dates(dict(start=r['period'],end=r['period_end'])) if r.get('period') and r.get('period_end') else 'Не е потвърден',status=STATUS[r['status']],
                          siteDownload={'yes':'Да','no':'Не','unknown':'Неизвестно'}.get(r['site_download'],'Неизвестно'),
                          archive=('Записан' if Path(r['target']).is_dir() else 'Папката липсва') if r['status']=='downloaded' else '—',local='Готов · ръчно' if r['local_report'] else 'Не е отбелязан',
                          remote=REPORT.get(r['remote_report'],'Неизвестно')) for r in page]
            archive_message=('Не е зададена IASC архивна папка. Избери папка от настройките или от бутона по-долу.' if not self.options['root'] else 'Зададената IASC архивна папка не е достъпна. Провери диска или местоположението.' if not Path(self.options['root']).is_dir() else 'За момента няма изтеглени архивни кадри. Провери следените екипи и изтегли наличните комплекти.' if not any(r['status']=='downloaded' for r in rows) else 'IASC архивът е настроен. Избери кампания и екип за преглед на комплектите.')
            document = [field('text','state',span=2,tone='error' if self.problem else 'warning' if not self.options['root'] else 'normal',value=self.problem or self.phase or archive_message),
                field('button','choose',label='Избери IASC архивна папка',command='choose',disabled=self.busy,visible=not bool(self.options['root'])),
                *calendar,
                field('text','schedule-problem',span=2,value=schedule_problem,tone='warning',visible=bool(schedule_problem)),
                field('list','team-warnings',label='Предупреждения за следени екипи',span=2,items=[i for i in items if i['tone']=='warning'],visible=any(i['tone']=='warning' for i in items)),
                field('metric','new',label='Непрочетени нови комплекти',value=sum(r['unread'] for r in rows)),
                field('metric','downloaded',label='Проверени изтегляния',value=sum(r['status']=='downloaded' for r in rows)),
                field('text','last-check',span=2,value='Последна проверка: '+(self.last.replace('T',' ') if self.last else 'няма')),
                field('button','check',label='Провери сега',command='check',disabled=self.busy or not watches,tone='primary'),
                field('button','download-new',label='Изтегли новите от избора',command='download-new',disabled=self.busy or not self.options['root'] or not pending),
                field('button','stop',label='Спри безопасно',command='stop',disabled=not self.busy),
                field('button','read',label='Отбележи известията като прочетени',command='read'),
                field('select','viewCampaign',label='Следена кампания',value=self.options['viewCampaign'],options=[option('all','Всички следени кампании')]+[option(k,n) for k,n in families.items()],command='filter'),
                field('select','viewTeam',label='Следен екип',value=self.options['viewTeam'],options=[option('all','Всички екипи в избора')]+[option(w['key'],w['team']['name']+(' · '+w['campaign']['name'] if self.options['viewCampaign']=='all' else '')) for w in view_teams],command='filter'),
                field('select','filter',label='Състояние',value=self.filter,options=[option('all','Всички')]+[option(k,v) for k,v in STATUS.items()],command='filter'),
                field('table','image-sets',span=2,label='Image Sets',columns=[dict(key=k,label=l) for k,l in [('name','Image Set Name'),('campaign','Кампания'),('team','Екип'),('period','Период'),('status','Изтегляне тук'),('siteDownload','Downloaded в IASC'),('archive','В архива'),('local','Локален отчет'),('remote','Отчет в IASC')]
                    if not (k=='campaign' and not self.options['showCampaign'] or k=='team' and not self.options['showTeam'] or k in ('siteDownload','remote') and not self.options['showSiteStatus'])],rows=table,command='row'),
                field('text','page',span=2,value=f'{self.page*100+1 if visible else 0}–{self.page*100+len(page)} от {len(visible)} комплекта'),
                field('button','previous',label='Предишни',command='previous',disabled=self.page==0),
                field('button','next',label='Следващи',command='next',disabled=(self.page+1)*100>=len(visible)),
                field('button','copy',label='Копирай справката',command='copy'),
                field('text','report-help',span=2,value='„Локален отчет“ е ръчна отметка. Сайтовите Downloaded и Report Submitted са отделни прочетени отметки от IASC; не доказват научно приемане. Този етап не променя сайтовите отметки и не изпраща отчети.')]
            if selected:
                document.extend([field('text','selected',span=2,label='Избран комплект',value=selected['name']+' · '+selected['team_name']),
                    field('text','selected-problem',span=2,value=selected['error'] or self.index or ('Папка: '+selected['target'] if selected['target'] else 'Още не е изтеглен.')),
                    field('button','download',label='Изтегли / провери този комплект',command='download',disabled=self.busy or not self.options['root']),
                    field('button','verify',label='Провери файловете и индекса',command='verify',disabled=self.busy or not selected['target']),
                    field('button','navigate',label='Покажи в архива',command='navigate',disabled=selected['status']!='downloaded'),
                    field('checkbox','local-report',label='Локалният отчет е готов · ръчно отбелязване',value=bool(selected['local_report']),command='local-report')])
            self.ctx.publish('sets',document)
            self.ctx.publish('status',[field('text','summary',label='IASC',tone='error' if self.problem else 'warning' if any(r['unread'] for r in rows) else 'normal',
                value=f"IASC · {sum(r['unread'] for r in rows)} нови · {sum(r['status']=='downloaded' for r in rows)} изтеглени"+(' · работа' if self.busy else ''))])

        self.work.publish()

    def watch_loop(self):
        while not self.closed.wait(15):
            try:
                with self.lock:
                    if not self.busy and self.sync_archive():
                        self.publish()
                    if not self.busy and self.work.poll_reports():self.publish()
                    if self.busy or not self.options['monitor'] or not self.options['watches']:
                        continue
                    slot = schedule_slot(datetime.now(),[self.options[k] for k in ('morning','noon','evening')])
                    if slot<=self.store.cache('lastSlot',''):
                        continue
                    self.store.save_cache('lastSlot',slot)
                self.task('IASC · планова проверка',lambda c,r:self.scan(c,r,self.options['autoDownload']),tier='ambient')
            except Exception as error:
                if not self.closed.is_set():
                    self.problem = str(error)[:500]
                    try:
                        self.publish()
                    except PermissionError:
                        pass

    def close(self):
        self.closed.set()
        if self.cancel is not None:
            self.cancel.set()


def setup(context):
    plugin = Campaigns(context)
    return plugin.close
