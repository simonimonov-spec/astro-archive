"""Read-only IASC adapter: stdlib HTMLParser, HTTPS and CookieJar."""
from __future__ import annotations

import http.cookiejar
import json
import os
import re
import ssl
import threading
from datetime import date
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

ORIGIN = 'https://iasc.cosmosearch.org'
HOST = 'iasc.cosmosearch.org'


class LoginRequired(ValueError):
    pass


class LayoutChanged(ValueError):
    pass


def trusted_context():
    if os.name != 'nt':
        return ssl.create_default_context()
    # Windows ROOT only: do not make stale intermediate certificates trust anchors.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    for cert, encoding, trust in ssl.enum_certificates('ROOT'):
        if encoding == 'x509_asn' and (trust is True or ssl.Purpose.SERVER_AUTH.oid in trust):
            context.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(cert))
    return context


def checked_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname != HOST or parsed.port not in (None,443) or parsed.username or parsed.password:
        raise ValueError('Разрешени са само HTTPS връзки към официалния IASC сайт.')
    return url


class SiteRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        checked_url(newurl)
        return super().redirect_request(request, fp, code, message, headers, newurl)


class TableReader(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.tables, self.table, self.row, self.cell = [], None, None, None
        self.heading, self.headings, self.login = None, [], False
        self.content = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'input' and a.get('type','').lower() == 'password':
            self.login = True
        if tag in ('h1','h2','h3'):
            self.heading = []
        if tag == 'table':
            self.table = []
            self.tables.append(self.table)
        if tag == 'tr' and self.table is not None:
            self.row = {'id':a.get('id',''), 'cells':[]}
            self.table.append(self.row)
        if tag in ('td','th') and self.row is not None:
            self.cell = {'id':a.get('id',''),'text':[], 'links':[], 'attributes':[], 'header':tag=='th'}
            self.row['cells'].append(self.cell)
        if self.cell is not None:
            self.cell['attributes'].extend(a.get(k,'') for k in ('class','title','alt','aria-label'))
            if tag == 'a' and a.get('href'):
                self.cell['links'].append(a['href'])

    def handle_data(self, data):
        self.content.append(data)
        if self.cell is not None:
            self.cell['text'].append(data)
        if self.heading is not None:
            self.heading.append(data)

    def handle_endtag(self, tag):
        if tag in ('td','th'):
            self.cell = None
        if tag == 'tr':
            self.row = None
        if tag == 'table':
            self.table = None
        if tag in ('h1','h2','h3') and self.heading is not None:
            self.headings.append(' '.join(''.join(self.heading).split()))
            self.heading = None


def text(cell):
    return ' '.join(''.join(cell['text']).split())


def campaigns(source):
    doc = TableReader(source)
    rows = [dict(id=r['id'],name=text(r['cells'][0]),teams=text(r['cells'][1]))
            for table in doc.tables for r in table if re.fullmatch(r'\d+',r['id']) and len(r['cells']) >= 2]
    if not rows:
        raise LayoutChanged('Списъкът с кампании не е разпознат. Данните не са изтрити.')
    return rows


def teams(source, campaign):
    doc = TableReader(source)
    if doc.login:
        raise LoginRequired('IASC изисква вход за тази кампания.')
    rows = [dict(id=r['id'].split()[0],campaign=campaign,name=text(r['cells'][0]),country=text(r['cells'][1]))
            for table in doc.tables for r in table if re.fullmatch(r'\d+\s+'+re.escape(campaign),r['id']) and len(r['cells']) >= 2]
    if not rows and not any('team name' in ' '.join(text(c).lower() for c in r['cells']) for t in doc.tables for r in t):
        raise LayoutChanged('Списъкът с екипи не е разпознат. Данните не са изтрити.')
    return rows


MONTHS = {name:i for i,name in enumerate(('january','february','march','april','may','june','july','august','september','october','november','december'),1)}
DATE = r'(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})'


def calendar_date(value):
    match=re.fullmatch(DATE,value.strip())
    if not match:raise LayoutChanged('Датата в календара на IASC не е разпозната.')
    month,day,year=match.groups()
    try:return date(int(year),MONTHS[month.casefold()],int(day)).isoformat()
    except (ValueError,KeyError):raise LayoutChanged('Невалидна дата в календара на IASC.') from None


def schedule(source):
    doc=TableReader(source);result=[];recognized=False
    for table in doc.tables:
        header=next((r for r in table if [text(c).casefold() for c in r['cells'][:3]]==['start date','end date','campaign name']),None)
        if header is None:continue
        recognized=True
        for row in table:
            if row is header or len(row['cells'])<3:continue
            start,end,name=[text(c) for c in row['cells'][:3]]
            if not name:continue
            item=dict(name=name,start=calendar_date(start),end=calendar_date(end))
            if item['start']>item['end']:raise LayoutChanged('Обърнат период в календара на IASC.')
            if item not in result:result.append(item)
    if not recognized:raise LayoutChanged('Календарът на IASC не е разпознат. Последните потвърдени дати са запазени.')
    return result


def campaign_period(source):
    content=' '.join(' '.join(TableReader(source).content).split())
    match=re.search('('+DATE+r')\s+through\s+('+DATE+')',content)
    if not match:return None
    start,end=calendar_date(match.group(1)),calendar_date(match.group(5))
    if start>end:raise LayoutChanged('Обърнат период на кампанията в IASC.')
    return dict(start=start,end=end)


def report_status(cell):
    value = (' '.join(cell['attributes'])+' '+text(cell)).lower()
    if any(mark in value for mark in ('✓','✔','☑')):
        return 'yes'
    if re.search(r'not (?:received|submitted|reported)|no report|fa-times|glyphicon-remove|\bfalse\b|\bno\b', value):
        return 'no'
    if re.search(r'\breceived\b|\bsubmitted\b|\breported\b|fa-check|glyphicon-ok|checkmark|\btrue\b|\byes\b', value):
        return 'yes'
    # Confirmed IASC markup: these named cells are empty until the site adds &check;.
    if re.fullmatch(r'\d+-(Analyzed|Downloaded)',cell.get('id','')) and not value.strip():
        return 'no'
    return 'unknown'


def image_sets(source):
    doc = TableReader(source)
    if doc.login:
        raise LoginRequired('Влез в IASC, за да се заредят Image Sets.')
    result, recognized = [], False
    for table in doc.tables:
        header = next((r for r in table if any(re.search(r'image\s*set\s*name',text(c),re.I) for c in r['cells'])), None)
        if not header:
            continue
        recognized = True
        labels = [text(c).lower() for c in header['cells']]
        name_index = next(i for i,label in enumerate(labels) if re.search(r'image\s*set\s*name',label))
        report_index = next((i for i,label in enumerate(labels) if 'report' in label), None)
        download_index = next((i for i,label in enumerate(labels) if label.strip()=='downloaded'), None)
        for row in table:
            cells = row['cells']
            if row is header or len(cells) <= name_index:
                continue
            name = text(cells[name_index]).strip()
            if not name or len(name)>500 or 'no data available' in name.lower():
                continue
            links = [urllib.parse.urljoin(ORIGIN+'/',link) for c in cells for link in c['links']]
            downloads = [link for link in links if re.search(r'\.zip(?:$|[?#])|download|imageset',link,re.I)
                         and not re.search(r'report|\.pdf(?:$|[?#])',link,re.I)]
            # A missing/unsupported download link is shown, never guessed from a name.
            result.append(dict(name=name,url=downloads[0] if downloads else '',
                               reported=report_status(cells[report_index]) if report_index is not None and len(cells)>report_index else 'unknown',
                               downloaded=report_status(cells[download_index]) if download_index is not None and len(cells)>download_index else 'unknown'))
    if not recognized:
        raise LayoutChanged('Таблицата Image Set Name не е разпозната. Предишните комплекти са запазени.')
    if len({r['name'] for r in result}) != len(result):
        raise LayoutChanged('IASC върна повторени имена на комплекти; необходима е проверка преди изтегляне.')
    return result


class Client:
    def __init__(self, cookie_path=None):
        self.jar = http.cookiejar.CookieJar()
        self.lock = threading.RLock()
        self.cookie_path = cookie_path
        self.opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=trusted_context()),
                                                  urllib.request.HTTPCookieProcessor(self.jar), SiteRedirect())
        if cookie_path and cookie_path.is_file():
            try:
                from .login import unseal
                self.set_cookies(json.loads(unseal(cookie_path.read_bytes())), save=False)
            except (OSError,ValueError,TypeError,KeyError):
                pass

    def set_cookies(self, rows, save=True):
        accepted = []
        with self.lock:
            self.jar.clear()
            for row in rows:
                if row.get('domain','').lstrip('.') != HOST or not isinstance(row.get('name'),str) or not isinstance(row.get('value'),str):
                    continue
                if any('\r' in row[k] or '\n' in row[k] for k in ('name','value')):
                    continue
                expires = row.get('expires',0)
                cookie = http.cookiejar.Cookie(0,row['name'],row['value'],None,False,HOST,False,False,
                    row.get('path','/'),True,True,int(expires) if expires>0 else None,False,None,None,{})
                self.jar.set_cookie(cookie)
                accepted.append(row)
            if save and self.cookie_path:
                from .login import seal
                temporary = self.cookie_path.with_suffix('.tmp')
                temporary.write_bytes(seal(json.dumps(accepted).encode()))
                temporary.replace(self.cookie_path)

    def forget(self):
        with self.lock:
            self.jar.clear()
            if self.cookie_path:
                self.cookie_path.unlink(missing_ok=True)

    def request(self, path, data=None, timeout=25):
        url = checked_url(urllib.parse.urljoin(ORIGIN+'/',path))
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        request = urllib.request.Request(url,data=body,headers={'User-Agent':'AstroArchive-IASC/0.1','Accept-Encoding':'identity'})
        with self.lock:
            response = self.opener.open(request,timeout=timeout)
        if '/account/login' in response.url.lower():
            response.close()
            raise LoginRequired('Сесията в IASC липсва или е изтекла. Използвай „Вход в IASC“.')
        return response

    def html(self, path, data=None):
        with self.request(path,data) as response:
            body = response.read(8_000_001)
            if len(body)>8_000_000:
                raise ValueError('Страницата на IASC е прекалено голяма.')
            return body.decode(response.headers.get_content_charset() or 'utf-8',errors='replace')

    def campaigns(self):
        return campaigns(self.html('/Home/Campaigns'))

    def teams(self, campaign):
        return self.directory(campaign)['teams']

    def schedule(self):
        return schedule(self.html('/Home'))

    def directory(self, campaign):
        if not re.fullmatch(r'\d+',campaign):
            raise ValueError('Невалидна кампания.')
        source=self.html('/Home/ViewCampaign',{'ID':campaign})
        rows=teams(source,campaign);period=campaign_period(source)
        if period is None:raise LayoutChanged('Периодът на тази кампания не е разпознат. Последните данни са запазени.')
        return dict(teams=rows,period=period)

    def sets(self, campaign, team):
        if not all(re.fullmatch(r'\d+',x) for x in (campaign,team)):
            raise ValueError('Невалиден екип.')
        return image_sets(self.html('/Home/ViewTeam',{'ID':team+' '+campaign}))
