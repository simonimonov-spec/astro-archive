"""Small, read-only catalogue service probes, owned entirely by this plugin."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
import copy
from http.client import HTTPException
import json
import re
import socket
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from .downloads import check_cancel
from .site import trusted_context

# Only public catalogue services; a local .dat file cannot turn this into a LAN scanner.
VIZIER_HOSTS = frozenset(('vizier.cds.unistra.fr', 'vizier.u-strasbg.fr',
    'vizier.nao.ac.jp', 'vizier.iucaa.in', 'vizier.china-vo.org',
    'vizier.cfa.harvard.edu', 'vizier.idia.ac.za', 'vizieridia.saao.ac.za',
    'vizier.inasan.ru', 'vizier.hia.nrc.ca', 'vizier.ast.cam.ac.uk'))
MPC_HOSTS = frozenset(('www.minorplanetcenter.net', 'minorplanetcenter.net',
    'www.minorplanetcenter.org', 'minorplanetcenter.org', 'mpcorb.klet.org'))
CATALOGS = {'gaia': ('Gaia DR3', 'I/355/gaiadr3', 'Source'),
            'ucac4': ('UCAC4', 'I/322A/out', 'UCAC4')}
DEFAULTS = (
    ('CDS, Strasbourg, France', 'http://vizier.cds.unistra.fr'),
    ('ADAC, Tokyo, Japan', 'http://vizier.nao.ac.jp'),
    ('IUCAA, Pune, India', 'https://vizier.iucaa.in'),
    ('NAOC, China', 'http://vizier.china-vo.org'),
    ('CfA, Harvard, USA', 'https://vizier.cfa.harvard.edu'),
    ('IDIA, South Africa', 'http://vizier.idia.ac.za'),
    ('INASAN, Russia', 'https://vizier.inasan.ru'))
LIMIT = 65536


def checked_url(url, hosts):
    p = urlsplit(url)
    if (p.scheme not in ('http', 'https') or p.hostname not in hosts or
            p.port not in (None, 80 if p.scheme == 'http' else 443) or
            p.username or p.password or p.fragment):
        raise ValueError('Адресът не е разрешен публичен каталог.')
    return url


class CatalogRedirect(HTTPRedirectHandler):
    def __init__(self, hosts, secure):
        self.hosts, self.secure = hosts, secure

    def redirect_request(self, request, fp, code, message, headers, newurl):
        checked_url(newurl, self.hosts)
        if self.secure and urlsplit(newurl).scheme != 'https':
            raise ValueError('Отказано пренасочване от HTTPS към HTTP.')
        return super().redirect_request(request, fp, code, message, headers, newurl)


def definitions(program):
    """Read server lists only. Never read credentials or modify Astrometrica.cfg."""
    folder = Path(program).parent if program else None
    servers, origins = [], []
    for kind, filename, defaults in (
        ('vizier', 'VizierServers.dat', [(a, b, '/viz-bin/VizieR') for a, b in DEFAULTS]),
        ('mpc', 'MPCOrbServers.dat', [('Minor Planet Center (https)', 'https://www.minorplanetcenter.net', '/iau/MPCORB'),
                                    ('Klet Observatory (http)', 'http://mpcorb.klet.org', '/ftp')])):
        entries = defaults
        path = folder / filename if folder else None
        if path and path.is_file() and path.stat().st_size <= 32768:
            raw = path.read_bytes()
            try:
                text = raw.decode('utf-8-sig')
            except UnicodeDecodeError:
                text = raw.decode('cp1252')
            entries = [line.split(';')[:3] for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')]
            origins.append(filename)
        seen = set()
        for entry in entries[:32]:
            if len(entry) != 3:
                continue
            label, base, path_ = (part.strip() for part in entry)
            # Astrometrica's VizieR list stores bare hostnames. Its legacy
            # catalogue requests use HTTP; keep that protocol in "listed" mode.
            if '://' not in base:
                base = 'http://' + base
            try:
                checked_url(base, VIZIER_HOSTS if kind == 'vizier' else MPC_HOSTS)
                p = urlsplit(base)
                # Paths are only catalogue locations, not query parameters or arbitrary URLs.
                if p.path not in ('', '/') or p.query or not re.fullmatch(r'/?[A-Za-z0-9_/-]{1,100}', path_):
                    continue
                if kind == 'vizier' and path_.rstrip('/') not in ('/viz-bin/VizieR', 'viz-bin/VizieR'):
                    continue
                if kind == 'mpc' and path_.rstrip('/') not in ('/iau/MPCORB', '/ftp'):
                    continue
            except ValueError:
                continue
            if p.hostname in seen:
                continue
            seen.add(p.hostname)
            servers.append(dict(id=kind+'-'+str(len(servers)), kind=kind,
                label=label[:160], base=base.rstrip('/'), path='/'+path_.lstrip('/'), host=p.hostname))
    return servers, ' + '.join(origins) or 'вграден списък'


def probe_url(server, mode, catalog):
    base = server['base']
    if mode == 'https':
        base = 'https://' + server['host']
    if server['kind'] == 'mpc':
        # A bounded byte range; do not download the full MPCORB database.
        return base + server['path'].rstrip('/') + '/MPCORB.DAT'
    _, source, column = CATALOGS[catalog]
    return base + '/viz-bin/asu-tsv?' + urlencode({
        '-source': source, '-c': '83.6331 +22.0145', '-c.rm': '5',
        '-out': column, '-out.max': '1'})


def valid_catalog(data, catalog):
    text = data.decode('utf-8', errors='replace')
    if '<html' in text.lower() or catalogue_error(data):
        return False
    source = CATALOGS[catalog][1]
    column = CATALOGS[catalog][2]
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith('#')]
    # Must be the requested table, with its expected identifier and an actual data row.
    return source.lower() in text.lower() and column in lines and any(
        re.fullmatch(r'\d{4,20}' if catalog == 'gaia' else r'\d{3}-\d{6}', line) for line in lines)


def catalogue_error(data):
    text = data.decode('utf-8', errors='replace')
    return bool(re.search(r'(?im)^#\s*(?:(?:error|fatal)\b|INFO\s+(?:Error=|QUERY_STATUS=ERROR))', text))


def valid_mpc(data):
    # MPCORB fixed-width header + orbital column header; generic HTML is not success.
    text = data.decode('ascii', errors='replace')
    return 'MPCORB' in text and ('Epoch' in text and 'Peri.' in text) and '<html' not in text.lower()


def probe(server, mode, catalog, timeout, cancel, opener=None):
    url = probe_url(server, mode, catalog)
    hosts = VIZIER_HOSTS if server['kind'] == 'vizier' else MPC_HOSTS
    checked_url(url, hosts)
    start = time.monotonic()
    result = dict(id=server['id'], label=server['label'], host=server['host'], kind=server['kind'],
                  url=url, state='unavailable', status='', ms=None, bytes=0,
                  tls=url.startswith('https:'), checked=datetime.now().astimezone().isoformat(timespec='seconds'))
    try:
        check_cancel(cancel)
        if opener is None:
            opener = build_opener(CatalogRedirect(hosts, result['tls']), HTTPSHandler(context=trusted_context()))
        headers = {'User-Agent': 'AstroArchive-IASC-ServiceCheck/0.4.1', 'Accept-Encoding': 'identity', 'Accept': 'text/plain'}
        if server['kind'] == 'mpc':
            headers['Range'] = 'bytes=0-8191'
        with opener.open(Request(url, headers=headers), timeout=timeout) as response:
            final = response.geturl()
            checked_url(final, hosts)
            if result['tls'] and not final.startswith('https:'):
                raise ValueError('Отказано пренасочване от HTTPS към HTTP.')
            result['tls'] = final.startswith('https:')
            chunks = bytearray()
            while len(chunks) < LIMIT:
                check_cancel(cancel)
                if time.monotonic() - start > timeout:
                    raise TimeoutError()
                read = getattr(response, 'read1', response.read)
                part = read(min(4096, LIMIT-len(chunks)))
                if not part:
                    break
                chunks.extend(part)
                # For MPC only the header is needed. Ignore an omitted Range response.
                if server['kind'] == 'mpc' and len(chunks) >= 8192:
                    break
            result['bytes'] = len(chunks)
            valid = valid_catalog(chunks, catalog) if server['kind'] == 'vizier' else valid_mpc(chunks)
            result.update(state='ok' if valid else 'unexpected',
                status='Каталогът отговаря' if valid else 'Има уеб отговор, но каталогът не е потвърден')
            if server['kind'] == 'vizier' and catalogue_error(chunks):
                result.update(state='service-error', status='Сървърът отговаря, но каталогът съобщава грешка')
    except InterruptedError:
        raise
    except HTTPError as error:
        result.update(state='http-error', status=f'HTTP {error.code} · '+('достъпът е отказан' if error.code in (401,403) else 'твърде много заявки' if error.code == 429 else 'услугата не отговори успешно'))
        error.close()
    except (TimeoutError, socket.timeout):
        result.update(state='timeout', status='Няма отговор в зададеното време')
    except (ssl.SSLError, URLError, OSError, ValueError, HTTPException) as error:
        reason = error.reason if isinstance(error, URLError) else error
        if isinstance(reason, (TimeoutError, socket.timeout)):
            result.update(state='timeout', status='Няма отговор в зададеното време')
        elif isinstance(reason, ssl.SSLError):
            result.update(state='tls-error', status='TLS / сертификатът не може да бъде потвърден')
        elif isinstance(reason, socket.gaierror):
            result.update(state='dns-error', status='DNS адресът не може да бъде намерен')
        else:
            result.update(state='connection-error', status='Неуспешна връзка от този компютър')
    result['ms'] = round((time.monotonic()-start)*1000)
    return result


class ServerChecks:
    def __init__(self, parent):
        self.p = parent
        self.visible = False
        self.running = False
        self.rows = []
        self.source = ''
        self.stamp = ''
        self.detail = ''
        self.path = parent.folder/'servers-last-check.json'
        try:
            saved = json.loads(self.path.read_text(encoding='utf-8'))
            if isinstance(saved, dict) and isinstance(saved.get('rows'), list) and len(saved['rows']) <= 32:
                self.rows, self.source, self.stamp = saved['rows'], saved.get('source',''), saved.get('stamp','')
                self.detail = saved.get('detail','')
        except (OSError, ValueError):
            pass
        for key, value in dict(serverCheckKind='vizier', serverCheckMode='listed', serverCheckCatalog='gaia', serverCheckTimeout=8).items():
            self.p.options.setdefault(key, value)
        for name, fn in {'work-servers-show': self.show, 'work-servers-hide': self.hide,
                         'work-servers-options': self.options, 'work-servers-check': self.check,
                         'work-servers-copy': self.copy, 'work-servers-stop': self.stop}.items():
            self.p.ctx.command(name, fn)

    def show(self, parameters):
        with self.p.lock:
            self.visible = True
            self.p.publish()
        return dict(openContribution='astrometrica')

    def hide(self, parameters):
        with self.p.lock:
            self.visible = False
            self.p.publish()
        return dict(saved=True)

    def options(self, parameters):
        values = parameters.get('values', {})
        with self.p.lock:
            if self.p.busy:
                raise ValueError('Изчакай текущата проверка.')
            new = {key: values.get(key, self.p.options[key]) for key in (
                'serverCheckKind', 'serverCheckMode', 'serverCheckCatalog', 'serverCheckTimeout')}
            if (new['serverCheckKind'] not in ('vizier','mpc') or new['serverCheckMode'] not in ('listed','https') or
                    new['serverCheckCatalog'] not in CATALOGS or type(new['serverCheckTimeout']) is not int or not 3 <= new['serverCheckTimeout'] <= 20):
                raise ValueError('Невалидни настройки за проверката.')
            self.p.options.update(new)
            self.p.persist()
        return dict(saved=True)

    def check(self, parameters):
        with self.p.lock:
            if self.p.busy:
                raise ValueError('Изчакай текущата IASC дейност.')
            self.options(parameters)
            config = copy.deepcopy(self.p.options)
            servers, source = definitions(config['workProgram'])
            servers = [s for s in servers if s['kind'] == config['serverCheckKind']]
            if not servers:
                raise ValueError('Не е намерен разрешен сървър в локалния списък на Astrometrica.')
            self.rows = [dict(id=s['id'],label=s['label'],host=s['host'],kind=s['kind'],
                state='pending',status='Очаква проверка',ms=None,bytes=0) for s in servers]
            self.source, self.stamp, self.visible = source, '', True
            self.detail = ('VizieR · '+CATALOGS[config['serverCheckCatalog']][0] if config['serverCheckKind']=='vizier' else 'MPCOrb')+' · '+('HTTPS' if config['serverCheckMode']=='https' else 'протокол от списъка')
            self.running = True

        def work(cancel, progress):
            done = 0
            try:
                with ThreadPoolExecutor(max_workers=3, thread_name_prefix='IASC-service') as pool:
                    futures = {pool.submit(probe, s, config['serverCheckMode'], config['serverCheckCatalog'],
                               config['serverCheckTimeout'], cancel): s for s in servers}
                    try:
                        for future in as_completed(futures):
                            check_cancel(cancel)
                            row = future.result()
                            with self.p.lock:
                                self.rows = [row if r['id'] == row['id'] else r for r in self.rows]
                                self.p.publish()
                            done += 1
                            progress(done, len(servers), 'Проверени сървъри: '+str(done)+' / '+str(len(servers)))
                    finally:
                        for future in futures:
                            future.cancel()
                        with self.p.lock:
                            for row in self.rows:
                                if row['state'] == 'pending':
                                    row.update(state='cancelled',status='Проверката не е завършена')
                            self.stamp = datetime.now().astimezone().isoformat(timespec='seconds')
                            # This cache is historical evidence, not a permanent online flag.
                            from .workflows import atomic
                            atomic(self.path, dict(rows=self.rows,source=self.source,stamp=self.stamp,detail=self.detail))
                return dict(checked=done, available=sum(r['state']=='ok' for r in self.rows))
            finally:
                with self.p.lock:
                    self.running = False
        try:
            return self.p.task('IASC · проверка на сървърите', work, tier='normal')
        except BaseException:
            with self.p.lock:
                self.running = False
            raise

    def stop(self, parameters):
        if not self.running:
            return dict(stopped=False)
        return self.p.stop(parameters)

    def copy(self, parameters):
        ident = parameters.get('selected', {}).get('id')
        with self.p.lock:
            row = next((r for r in self.rows if r['id']==ident), None)
            if row is None or not row.get('url'):
                raise ValueError('Първо провери и избери сървър.')
            p = urlsplit(row['url'])
            text = row['label']+'\n'+p.scheme+'://'+p.netloc+'\n'+('/viz-bin/VizieR' if row['kind']=='vizier' else p.path.rsplit('/',1)[0])
        return dict(copyText=text, message='Адресът е копиран. Избери съответния сървър ръчно в Astrometrica → Settings → Internet.')

    def document(self):
        f = lambda kind, ident, **kw: dict(type=kind,id=ident,**kw)
        choose = lambda value, label: dict(value=value,label=label)
        busy = self.p.busy
        result = [f('button','work-servers-hide',label='← Към комплекта и ръчната работа',command='work-servers-hide'),
            f('text','work-servers-info',span=2,value='Зелено: каталогът отговаря. Жълто/червено: проблем или непотвърден отговор от този компютър. Timeout не доказва, че сървърът е изключен.'),
            f('select','serverCheckKind',label='Услуга',value=self.p.options['serverCheckKind'],options=[choose('vizier','VizieR · звездни каталози'),choose('mpc','MPCOrb · орбитални данни')],command='work-servers-options',disabled=busy),
            f('select','serverCheckMode',label='Връзка',value=self.p.options['serverCheckMode'],options=[choose('listed','От списъка на Astrometrica'),choose('https','Само HTTPS')],command='work-servers-options',disabled=busy),
            f('select','serverCheckCatalog',label='Тестов звезден каталог',value=self.p.options['serverCheckCatalog'],options=[choose(k,v[0]) for k,v in CATALOGS.items()],command='work-servers-options',disabled=busy),
            f('number','serverCheckTimeout',label='Изчакване за заявка · секунди',value=self.p.options['serverCheckTimeout'],min=3,max=20,step=1,command='work-servers-options',disabled=busy),
            f('button','work-servers-check',label='Провери сървърите',tone='primary',command='work-servers-check',disabled=busy),
            f('button','work-servers-stop',label='Спри проверката',command='work-servers-stop',disabled=not self.running),
            f('text','work-servers-source',span=2,value='Списък: '+(self.source or 'ще се прочете от избраната Astrometrica')+' · Последни резултати: '+(self.stamp or 'няма завършена проверка')+' · '+self.detail)]
        items = []
        for row in self.rows:
            tone = 'success' if row['state']=='ok' else 'warning' if row['state'] in ('pending','cancelled','timeout','unexpected','http-error') else 'error'
            status = row['status'] + (f" · {row['ms']} ms" if row.get('ms') is not None else '')
            if row.get('url'):
                status += ' · HTTPS' if row.get('tls') else ' · HTTP (нешифровано)'
            items.append(dict(id=row['id'],label=row['label'],description=row['host'],status=status,tone=tone,disabled=not bool(row.get('url'))))
        result.extend([f('list','work-servers-list',span=2,label='Резултати от последната проверка',items=items,command='work-servers-copy',actionLabel='Копирай адреса'),
            f('text','work-servers-limit',span=2,value='Не се изпращат кадри и не се сменят настройки на Astrometrica. Копирай адреса за ръчен избор в Settings → Internet. Резултатите важат за показаната проверка; HTTP и HTTPS могат да се различават.')])
        return result
