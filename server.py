"""Private-network HTTPS transport with pairing, CSRF, rate and upload limits."""
from __future__ import annotations
import collections,gzip,hashlib,hmac,ipaddress,json,mimetypes,re,secrets,shutil,socket,ssl,threading,time,uuid
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit,parse_qs,unquote,quote
from .gateway import Gateway
from .tls import TLSIdentity

COOKIE='__Host-aa_remote'
SESSION_SECONDS=12*3600

def accepts_gzip(value):
    weights={}
    for item in value.lower().split(','):
        parts=item.strip().split(';');quality=1.
        for parameter in parts[1:]:
            if parameter.strip().startswith('q='):
                try:quality=float(parameter.strip()[2:])
                except ValueError:quality=0.
        weights[parts[0].strip()]=max(0.,min(1.,quality))
    return weights.get('gzip',weights.get('*',0.))>0

class LimitedServer(ThreadingHTTPServer):
    daemon_threads=True;request_queue_size=32
    def __init__(self,address,owner):self.owner=owner;self.slots=threading.BoundedSemaphore(16);super().__init__(address,Handler)
    def process_request(self,request,address):
        if self.owner.stopping.is_set() or not self.owner.allowed_peer(address[0]):request.close();return
        if not self.slots.acquire(False):request.close();return
        with self.owner.lock:self.owner.sockets.add(request)
        try:super().process_request(request,address)
        except BaseException:
            with self.owner.lock:self.owner.sockets.discard(request)
            self.slots.release();request.close();raise
    def process_request_thread(self,request,address):
        raw=request
        try:
            # Handshake runs inside the bounded worker, never in accept().
            # Incomplete TLS clients cannot block the listener or survive stop().
            if self.owner.tls.expires<=time.time():return
            raw.settimeout(7)
            request=self.owner.tls.context.wrap_socket(raw,server_side=True,do_handshake_on_connect=False)
            with self.owner.lock:
                self.owner.sockets.discard(raw);self.owner.sockets.add(request)
            if self.owner.stopping.is_set():return
            request.do_handshake()
            super().process_request_thread(request,address)
        except (OSError,ssl.SSLError,ValueError):pass
        finally:
            request.close()
            with self.owner.lock:self.owner.sockets.discard(raw);self.owner.sockets.discard(request)
            self.slots.release()
    def handle_error(self,*args):pass

class RemoteServer:
    def __init__(self,ctx,folder,options,changed):
        self.ctx=Gateway(ctx);self.folder=Path(folder);self.options=options;self.changed=changed;self.lock=threading.RLock();self.sessions={};self.sockets=set();self.rates={};self.stopping=threading.Event();self.upload_slots=threading.BoundedSemaphore(2)
        self.server=None;self.thread=None;self.code='';self.code_until=0;self.new_code()
        self.encoded=collections.OrderedDict();self.encoded_bytes=0
        self.tls=TLSIdentity(self.folder,options);self.renew_thread=None
    def compressed(self,body,cache=False):
        key=hashlib.sha256(body).hexdigest() if cache else None
        with self.lock:
            if key in self.encoded:self.encoded.move_to_end(key);return self.encoded[key]
        encoded=gzip.compress(body,compresslevel=3,mtime=0)
        if cache and len(encoded)<=2*1024**2:
            with self.lock:
                if key not in self.encoded:
                    self.encoded[key]=encoded;self.encoded_bytes+=len(encoded)
                    while len(self.encoded)>64 or self.encoded_bytes>16*1024**2:
                        _,old=self.encoded.popitem(last=False);self.encoded_bytes-=len(old)
        return encoded
    def new_code(self):
        with self.lock:self.code=''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(10));self.code_until=time.monotonic()+600
        return self.code
    def start(self):
        if self.server:return
        self.tls.prepare();self.options['dns']=self.tls.hostname
        address=self.options['vpnAddress'] if self.options.get('mode')=='vpn' else self.options['lanAddress'] if self.options['lan'] else '127.0.0.1'
        self.stopping.clear();self.server=LimitedServer((address,self.options['port']),self)
        self.ctx.remote('hold',dict(active=True));self.thread=threading.Thread(target=self.server.serve_forever,name='AstroArchive-Remote',daemon=True);self.thread.start()
        self.renew_thread=threading.Thread(target=self.renew,name='AstroArchive-Remote-TLS',daemon=True);self.renew_thread.start()
    def renew(self):
        while not self.stopping.wait(12*3600):
            try:self.tls.prepare()
            except Exception:self.tls.problem='HTTPS сертификатът не може да се поднови. Провери Tailscale или LAN сертификата. Няма преминаване към HTTP.'
            if not self.stopping.is_set():self.changed()
    def allowed_peer(self,address):
        try:
            ip=ipaddress.ip_address(address)
            if ip.is_loopback:return True
            if self.options.get('mode')=='vpn':
                peers={a.strip() for a in self.options['vpnPeers'].split(',')};peers.add(self.options['vpnAddress'])
                return str(ip) in peers
            return self.options['lan'] and ip in ipaddress.ip_network(self.options['lanNetwork'])
        except ValueError:return False
    def stop(self):
        self.stopping.set()
        with self.lock:
            self.sessions.clear();sockets=list(self.sockets)
        for connection in sockets:
            try:connection.shutdown(socket.SHUT_RDWR);connection.close()
            except OSError:pass
        server=self.server;self.server=None
        if server:
            server.shutdown();server.server_close()
            if self.thread and self.thread is not threading.current_thread():self.thread.join(timeout=3)
        try:self.ctx.remote('hold',dict(active=False))
        except PermissionError:pass
    def revoke(self,ident=None):
        with self.lock:
            for key in list(self.sessions):
                if ident is None or self.sessions[key]['id']==ident:self.sessions.pop(key,None)
        self.changed()
    def devices(self):
        with self.lock:
            now=time.time()
            for key in list(self.sessions):
                if self.sessions[key]['until']<now:self.sessions.pop(key,None)
            return [dict(id=s['id'],name=s['name'],address=s['address'],last=time.strftime('%H:%M:%S',time.localtime(s['last']))) for s in self.sessions.values()]
    def allow_rate(self,key,count,period):
        now=time.monotonic()
        with self.lock:
            if len(self.rates)>1024:self.rates={k:v for k,v in self.rates.items() if v and now-v[-1]<3600}
            bucket=self.rates.setdefault(key,collections.deque())
            while bucket and bucket[0]<=now-period:bucket.popleft()
            if len(bucket)>=count:return False
            bucket.append(now);return True
    def session(self,headers,address):
        try:cookie=SimpleCookie(headers.get('Cookie',''));token=cookie[COOKIE].value
        except (KeyError,ValueError):return None
        key=hashlib.sha256(token.encode()).hexdigest()
        with self.lock:
            value=self.sessions.get(key)
            if not value or value['until']<time.time():self.sessions.pop(key,None);return None
            if value['address']!=address:return None
            value['last']=time.time();return value
    def pair(self,code,name,address):
        with self.lock:
            if self.stopping.is_set() or time.monotonic()>self.code_until or not hmac.compare_digest(str(code).replace('-','').replace(' ','').upper(),self.code):raise PermissionError('Кодът е неверен или е изтекъл. Вземи нов от компютъра.')
            if len(self.sessions)>=8:raise PermissionError('Достигнат е лимитът от 8 устройства.')
            token=secrets.token_urlsafe(32);key=hashlib.sha256(token.encode()).hexdigest();now=time.time()
            self.sessions[key]=dict(id=uuid.uuid4().hex,name=str(name or 'Браузър')[:80],address=address,csrf=secrets.token_urlsafe(32),last=now,until=now+SESSION_SECONDS)
            # One-use pairing code; existing devices keep their own sessions.
            self.new_code()
        self.changed();return token

class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1';server_version='AstroArchive';sys_version=''
    def log_message(self,*args):pass
    @property
    def owner(self):return self.server.owner
    def setup(self):
        super().setup();self.connection.settimeout(25)
        with self.owner.lock:self.owner.sockets.add(self.connection)
    def finish(self):
        try:super().finish()
        except OSError:pass
        finally:
            with self.owner.lock:self.owner.sockets.discard(self.connection)
    def end_headers(self):
        self.send_header('Strict-Transport-Security','max-age=31536000')
        super().end_headers()
    def send(self,status,value,mime='application/json; charset=utf-8',headers=None,cache=False):
        body=json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8') if isinstance(value,(dict,list)) else value
        textual=mime.startswith('text/') or mime.split(';')[0] in ('application/json','application/javascript','image/svg+xml')
        encoding=''
        if status==200 and textual and len(body)>=1024 and accepts_gzip(self.headers.get('Accept-Encoding','')) and not (headers or {}).get('Set-Cookie') and urlsplit(self.path).path!='/api/hello':
            compressed=self.owner.compressed(body,cache)
            if len(compressed)<len(body):body=compressed;encoding='gzip'
        etag='"'+hashlib.sha256(body).hexdigest()+'"' if cache else None
        if etag and etag in [v.strip().removeprefix('W/') for v in self.headers.get('If-None-Match','').split(',')]:status=304
        self.send_response(status);self.send_header('Content-Type',mime)
        if status!=304:self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','private, max-age=0, must-revalidate' if cache else 'no-store')
        if textual:self.send_header('Vary','Accept-Encoding')
        if encoding:self.send_header('Content-Encoding',encoding)
        if etag:self.send_header('ETag',etag)
        self.send_header('Server-Timing','app;dur='+str(round((time.perf_counter()-getattr(self,'request_started',time.perf_counter()))*1000,2)))
        self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer');self.send_header('X-Frame-Options','DENY')
        self.send_header('Cross-Origin-Resource-Policy','same-origin');self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'; frame-src 'self'")
        for name,value in (headers or {}).items():self.send_header(name,value)
        self.end_headers()
        if status!=304:self.wfile.write(body)
    def origin(self):
        o=self.owner;host=self.headers.get('Host','');ip=ipaddress.ip_address(self.client_address[0]);port=o.options['port']
        expected=o.tls.hostname+':'+str(port)
        if host.lower()!=expected or len(self.headers.get_all('Host',[]))!=1:return None,False
        if not isinstance(self.connection,ssl.SSLSocket) or not o.allowed_peer(str(ip)):return None,False
        return 'https://'+expected,True
    def guard(self,mutation=False):
        self.request_started=time.perf_counter()
        origin,secure=self.origin()
        if not origin or self.owner.stopping.is_set():raise PermissionError('Адресът или мрежата не са разрешени.')
        incoming=self.headers.get('Origin');fetch=self.headers.get('Sec-Fetch-Site')
        # A chat/browser link is a cross-site *navigation*, not a cross-site API
        # request. Only a top-level GET of an entry document may cross sites.
        # Frames, assets, downloads, JSON APIs and every mutation remain gated.
        entry=(not mutation and self.command=='GET'
            and urlsplit(self.path).path in ('/','/index.html')
            and self.headers.get('Sec-Fetch-Mode')=='navigate'
            and self.headers.get('Sec-Fetch-Dest')=='document')
        self.external_entry=fetch in ('cross-site','same-site') and entry
        if incoming and incoming!=origin or fetch in ('cross-site','same-site') and not entry:raise PermissionError('Заявка от чужд сайт е блокирана.')
        if mutation and incoming!=origin:raise PermissionError('Липсва правилният произход на заявката.')
        session=self.owner.session(self.headers,self.client_address[0])
        if not self.owner.allow_rate('request:'+(session['id'] if session else self.client_address[0]),180,30):raise OverflowError('Твърде много заявки. Изчакай малко.')
        return origin,secure
    def json_body(self,limit=2_000_000):
        if self.headers.get('Transfer-Encoding') or not self.headers.get('Content-Type','').startswith('application/json'):raise ValueError('Очаква се JSON заявка с известна дължина.')
        length=int(self.headers.get('Content-Length','0'))
        if not 0<length<=limit:raise ValueError('Невалиден размер на заявката.')
        value=json.loads(self.rfile.read(length))
        if not isinstance(value,dict):raise ValueError('Очаква се обект.')
        return value
    def require_session(self,mutation=False):
        session=self.owner.session(self.headers,self.client_address[0])
        if not session:raise PermissionError('Сесията е прекратена. Свържи устройството отново.')
        if mutation and not hmac.compare_digest(self.headers.get('X-AstroArchive-Key',''),session['csrf']):raise PermissionError('Невалидна защита на заявката.')
        return session
    def do_GET(self):
        try:
            self.guard();parsed=urlsplit(self.path);route=unquote(parsed.path).lstrip('/');query=parse_qs(parsed.query);o=self.owner
            if route=='plugin-logo.png':return self.send(200,(Path(__file__).parent/'logo.png').read_bytes(),'image/png',cache=True)
            if route=='api/health':return self.send(200,dict(app='Astro Archive',remote=True,running=True,secure=True,mode=o.options.get('mode','lan')))
            if route in ('login.css','login.js'):
                path=Path(__file__).parent/'Web'/route;return self.send(200,path.read_bytes(),'text/javascript' if path.suffix=='.js' else 'text/css',cache=True)
            if route in ('','index.html') and (self.external_entry or not o.session(self.headers,self.client_address[0])):
                return self.send(200,(Path(__file__).parent/'Web/login.html').read_bytes(),'text/html; charset=utf-8')
            session=self.require_session()
            if route=='api/ping':return self.send(200,dict(running=True,secure=True,mode=o.options.get('mode','lan'),server=socket.gethostname()))
            if route=='api/hello':return self.send(200,dict(app='Astro Archive',remote=True,key=session['csrf'],protocol=1,eventSequence=o.ctx.remote('sequence'),device=session['name'],mode=o.options.get('mode','lan')))
            if route=='api/events':return self.send(200,o.ctx.remote('event_batch',dict(after=int(query.get('after',['0'])[0]))))
            if route=='api/download':
                path=o.ctx.remote('download',dict(id=query.get('id',[''])[0]));size=path.stat().st_size
                self.send_response(200);self.send_header('Content-Type','application/octet-stream');self.send_header('Content-Length',str(size));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+quote(path.name));self.end_headers()
                with path.open('rb') as stream:
                    while not o.stopping.is_set() and o.session(self.headers,self.client_address[0]):
                        block=stream.read(1024*1024)
                        if not block:break
                        self.wfile.write(block)
                return
            if route=='python/transport.js':data=(Path(__file__).parent/'Web/transport.js').read_bytes();mime='text/javascript'
            elif route in ('remote.js','remote.css'):data=(Path(__file__).parent/'Web'/route).read_bytes();mime='text/javascript' if route.endswith('.js') else 'text/css'
            else:data,mime=o.ctx.remote('asset',dict(route=route))
            if route in ('','index.html'):
                data=data.replace(b'</head>',b'<link rel="stylesheet" href="remote.css"><script src="remote.js" defer></script></head>')
            self.send(200,data,mime,cache=route not in ('','index.html') and not route.startswith('viewer-image/'))
        except PermissionError as error:self.close_connection=True;self.send(401,dict(error=str(error)))
        except FileNotFoundError:self.send(404,dict(error='Файлът не е наличен.'))
        except OverflowError as error:self.close_connection=True;self.send(429,dict(error=str(error)))
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError,socket.timeout):self.close_connection=True
        except Exception as error:self.close_connection=True;self.send(400,dict(error=str(error)))
    def do_POST(self):
        try:
            _,secure=self.guard(True);route=urlsplit(self.path).path;o=self.owner
            if route=='/api/pair':
                if not o.allow_rate('pair:'+self.client_address[0],5,60) or not o.allow_rate('pair:all',30,600):raise OverflowError('Много опити за сдвояване. Изчакай една минута.')
                p=self.json_body(4096);token=o.pair(p.get('code',''),p.get('name',''),self.client_address[0]);return self.send(200,dict(paired=True),headers={'Set-Cookie':COOKIE+'='+token+'; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age='+str(SESSION_SECONDS)})
            session=self.require_session(True)
            if route=='/api/logout':
                self.json_body(1024);o.revoke(session['id']);return self.send(200,dict(loggedOut=True),headers={'Set-Cookie':COOKIE+'=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Strict'})
            if route=='/api/upload':return self.upload(session)
            if route!='/api/rpc':return self.send(404,dict(error='Непознат маршрут.'))
            request=self.json_body();ident=request.get('id')
            if request.get('version')!=1 or not isinstance(ident,str) or not 0<len(ident)<=80:raise ValueError('Невалиден протокол.')
            try:result=o.ctx.remote('rpc',dict(method=request.get('method'),params=request.get('params',{})));response=dict(type='response',version=1,id=ident,ok=True,result=result)
            except Exception as error:response=dict(type='response',version=1,id=ident,ok=False,error=str(error),code='remote_restricted' if isinstance(error,PermissionError) else 'operation_failed')
            self.send(200,response)
        except PermissionError as error:self.close_connection=True;self.send(401,dict(error=str(error)))
        except OverflowError as error:self.close_connection=True;self.send(429,dict(error=str(error)))
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError,socket.timeout):self.close_connection=True
        except Exception as error:self.close_connection=True;self.send(400,dict(error=str(error)))
    def upload(self,session):
        o=self.owner
        if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Type')!='application/octet-stream':raise ValueError('Невалиден поток за качване.')
        length=int(self.headers.get('Content-Length','0'));name=unquote(self.headers.get('X-Upload-Name',''));archive=self.headers.get('X-Archive-Id','frames')
        if not 0<length<=o.options['uploadMb']*1024**2:raise ValueError('Файлът надвишава разрешения размер.')
        if not name or Path(name).name!=name or any(c in name for c in '/\\:\x00\r\n') or len(name)>200 or Path(name).suffix.lower() not in ('.fit','.fits','.fts','.png','.jpg','.jpeg','.tif','.tiff'):raise ValueError('Невалидно име или формат.')
        if shutil.disk_usage(o.folder).free<2*length+64*1024**2:raise ValueError('Няма достатъчно свободно място за безопасно качване и внасяне.')
        if not o.upload_slots.acquire(False):raise OverflowError('Вече се качват два файла. Изчакай.')
        ident=uuid.uuid4().hex;folder=o.folder/'Uploads'/ident;folder.mkdir(parents=True);path=folder/name
        try:
            remaining=length
            with path.open('xb') as output:
                while remaining:
                    if o.stopping.is_set() or not o.session(self.headers,self.client_address[0]):raise PermissionError('Сесията е прекратена.')
                    block=self.rfile.read(min(remaining,1024*1024))
                    if not block:raise ValueError('Прекъснато качване.')
                    output.write(block);remaining-=len(block)
            started=o.ctx.remote('upload',dict(id=ident,name=name,archiveId=archive));self.send(200,started)
        except BaseException:
            path.unlink(missing_ok=True)
            try:folder.rmdir()
            except OSError:pass
            raise
        finally:o.upload_slots.release()
