"""Loopback-only browser host, strict named RPC and local event polling."""
from __future__ import annotations
import json, mimetypes, os, secrets, subprocess, sys, threading, time, traceback
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer as StandardHTTPServer
from urllib.parse import urlsplit,unquote,parse_qs
from urllib.request import urlopen
from pathlib import Path
from Backend.Foundation.branding import APP_NAME, KEY_HEADER, LEGACY_KEY_HEADER
from Backend.Foundation.runtime import ROOT,CACHE,within,atomic_json,read_json
from Bridge.Core.service import Service
from Bridge.Desktop.single_instance import acquire,activate,release
from Bridge.Desktop.identity import installation_id,window_title,matches,signal_ready

class ThreadingHTTPServer(StandardHTTPServer):
    # Chromium loads UI resources and RPC concurrently; Windows' default backlog
    # of five can refuse startup requests while an archive check is running.
    request_queue_size=64
    daemon_threads=True

def prepare_restart(root=ROOT):
    """Start a helper only after it has opened a handle to this exact process."""
    if os.name!='nt':raise RuntimeError('Пълното рестартиране е налично само в Windows.')
    root=Path(root).resolve();helper=Path(__file__).with_name('restart_wait.py')
    if not helper.is_file() or not (root/'start.pyw').is_file():raise FileNotFoundError('Липсват файлове за рестартиране.')
    process=subprocess.Popen([sys.executable,'-B',str(helper),str(os.getpid()),str(root)],cwd=str(root),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    result=[];ready=threading.Event()
    def read_ready():
        try:result.append(process.stdout.readline(32))
        finally:ready.set()
    threading.Thread(target=read_ready,daemon=True).start()
    if not ready.wait(5) or result!=[b'READY\n']:
        try:process.terminate();process.wait(timeout=3)
        except (OSError,subprocess.TimeoutExpired):pass
        raise RuntimeError('Помощният процес не е готов. Програмата остава отворена.')
    process.stdout.close();return process

class Handler(BaseHTTPRequestHandler):
    # FITS tile exchange uses many bounded RPCs; keep one local connection
    # instead of exhausting Windows ephemeral sockets with per-tile handshakes.
    protocol_version='HTTP/1.1'
    def log_message(self,*args):pass
    def send(self,status,value,mime='application/json; charset=utf-8'):
        body=json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8') if isinstance(value,(dict,list)) or value is None else value
        self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Cross-Origin-Resource-Policy','same-origin');self.end_headers();self.wfile.write(body)
    def allowed(self,mutation=False):
        origin='http://127.0.0.1:'+str(self.server.server_port)
        return self.headers.get('Host')==origin[7:] and self.headers.get('Origin',origin)==origin and urlsplit(self.path).path.startswith(self.server.prefix) and (not mutation or secrets.compare_digest(self.headers.get(KEY_HEADER,self.headers.get(LEGACY_KEY_HEADER,'')),self.server.key))
    def do_GET(self):
        try:
            if not self.allowed():return self.send(403,dict(error='Само локален достъп от този интерфейс.'))
            parsed=urlsplit(self.path);route=unquote(parsed.path[len(self.server.prefix):]);q=parse_qs(parsed.query)
            if route=='api/events':
                self.server.last_seen=time.monotonic();return self.send(200,dict(events=self.server.service.events.since(int(q.get('after',['0'])[0]))))
            if route=='api/hello':return self.send(200,dict(app=APP_NAME,installation=installation_id(ROOT),key=self.server.key,protocol=1,eventSequence=self.server.service.events.sequence))
            if route.startswith('plugin-image/'):
                parts=route.split('/')
                if len(parts)!=3:raise ValueError('Невалиден ресурс на плъгин.')
                return self.send(200,self.server.service.plugins.image_asset(parts[1],parts[2]),'image/png')
            if route.startswith('analysis-image/'):
                return self.send(200,self.server.service.quality.coverage_image(route.partition('/')[2]),'image/png')
            if route.startswith('viewer-image/'):
                ident=route.partition('/')[2];asset=self.server.service.viewer.assets.get(ident)
                return self.send(200,asset,'image/png') if asset else self.send(404,dict(error='Прегледът е затворен.'))
            route=route or 'index.html'
            if route.split('/')[0] in ('Archive','Analysis','Processing'):
                section,_,tail=route.partition('/');relative=f'{section}/Web/{tail}'
            else:relative='Shared/Web/'+route
            path=within(ROOT/'UI',relative)
            if not path.is_file() or path.suffix.lower() not in ('.html','.js','.css','.svg','.png','.jpg','.woff2','.ico'):return self.send(404,dict(error='Липсва UI ресурс.'))
            mime=mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            if path.suffix=='.js':mime='text/javascript'
            body=path.read_bytes()
            if route=='index.html':body=body.replace(b'<title>Astro Archive</title>',('<title>'+window_title(ROOT)+'</title>').encode('utf-8'))
            self.send(200,body,mime+('; charset=utf-8' if mime.startswith('text/') else ''))
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
        except Exception as error:self.send(400,dict(error=str(error)))
    def do_POST(self):
        try:
            if not self.allowed(True):return self.send(403,dict(error='Неразрешена заявка.'))
            if urlsplit(self.path).path[len(self.server.prefix):]!='api/rpc':return self.send(404,dict(error='Непозната команда.'))
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=4_000_000:raise ValueError('Заявката е твърде голяма.')
            request=json.loads(self.rfile.read(length))
            if not isinstance(request,dict):raise ValueError('Невалидна заявка.')
            ident=request.get('id');method=request.get('method');parameters=request.get('params',{})
            if not isinstance(method,str) or not isinstance(parameters,dict):raise ValueError('Невалидна команда.')
            try:
                if method=='python.app.restart':
                    if request.get('version')!=1 or not isinstance(ident,str) or not 0<len(ident)<=80:raise ValueError('Невалиден протокол на заявката.')
                    if parameters.get('confirmed') is not True:raise ValueError('Потвърди рестартирането.')
                    self.server.service.jobs.begin_restart()
                    try:prepare_restart()
                    except BaseException:
                        self.server.service.jobs.abort_restart();raise
                    try:self.send(200,dict(type='response',version=1,id=ident,ok=True,result=dict(accepted=True)))
                    finally:threading.Thread(target=self.server.shutdown,daemon=True).start()
                    return
                if method=='python.app.close':
                    if request.get('version')!=1 or not isinstance(ident,str) or not 0<len(ident)<=80:raise ValueError('Невалиден протокол на заявката.')
                    if any(t['state']=='running' for t in self.server.service.jobs.snapshot()['tasks']):raise ValueError('Има активни задачи. Отмени ги или изчакай приключването.')
                    result=dict(closed=True);threading.Thread(target=self.server.shutdown,daemon=True).start()
                else:
                    response=self.server.service.dispatch(request)
                    if not response['ok']:self.server.service.events.emit('app.log',entries=[dict(section='archive' if method.startswith('archive.') else 'analysis' if 'analysis' in method else 'preparation',time=time.strftime('%Y-%m-%dT%H:%M:%S'),category='ВРЪЗКА',level='warning',message=response.get('error','Проблем при командата.'))])
                    return self.send(200,response)
                self.send(200,dict(type='response',version=1,id=ident,ok=True,result=result))
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
            except InterruptedError as error:self.send(200,dict(type='response',id=ident,ok=False,cancelled=True,error=str(error)))
            except Exception as error:
                self.server.service.events.emit('app.log',entries=[dict(section='archive' if method.startswith('archive.') else 'analysis' if 'analysis' in method else 'preparation',time=time.strftime('%Y-%m-%dT%H:%M:%S'),category='ГРЕШКА',level='error',message=str(error))])
                self.send(200,dict(type='response',id=ident,ok=False,error=str(error)))
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
        except Exception as error:self.send(400,dict(error=str(error)))

def open_browser(url):
    edge=Path(os.environ.get('ProgramFiles(x86)',r'C:\Program Files (x86)'))/'Microsoft/Edge/Application/msedge.exe'
    if edge.is_file():subprocess.Popen([str(edge),'--user-data-dir='+str(CACHE/'browser'),'--no-first-run','--app='+url],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    else:
        import webbrowser
        webbrowser.open(url)

def reuse(runtime):
    """Focus the copy that is already starting or running; never open a second one."""
    deadline=time.monotonic()+15
    while True:
        url=read_json(runtime,{}).get('url','')
        if url.startswith('http://127.0.0.1:'):
            try:
                with urlopen(url+'api/hello',timeout=1) as stream:hello=json.load(stream)
                if matches(hello,ROOT):
                    if not activate(window_title(ROOT)):open_browser(url)
                    return True
            except (OSError,ValueError):pass
        if time.monotonic()>=deadline:return False
        time.sleep(.25)

def run():
    CACHE.mkdir(parents=True,exist_ok=True);runtime=CACHE/'desktop-session.json'
    instance=acquire(ROOT)
    if not instance:
        if reuse(runtime):signal_ready(ROOT)
        return
    prior=read_json(runtime,{})
    if prior.get('url','').startswith('http://127.0.0.1:'):
        try:
            with urlopen(prior['url']+'api/hello',timeout=1) as stream:hello=json.load(stream)
            if matches(hello,ROOT):
                if not activate(window_title(ROOT)):open_browser(prior['url'])
                release(instance);signal_ready(ROOT);return
        except (OSError,ValueError):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
    server.service=Service(desktop_actions={'show':lambda:open_browser(server.url),'quit':server.shutdown});server.key=secrets.token_urlsafe(32);server.prefix='/s/'+server.key+'/'
    server.url=f'http://127.0.0.1:{server.server_port}'+server.prefix;server.last_seen=time.monotonic()
    atomic_json(runtime,dict(url=server.url,pid=os.getpid(),installation=installation_id(ROOT)));open_browser(server.url);signal_ready(ROOT)
    def idle():
        while True:
            time.sleep(10)
            if time.monotonic()-server.last_seen>90 and not server.service.host_services.owners and not any(t['state']=='running' for t in server.service.jobs.snapshot()['tasks']):server.shutdown();return
    threading.Thread(target=idle,daemon=True).start()
    try:server.serve_forever()
    finally:
        for ident in list(server.service.plugins.live):server.service.plugins.unload(ident)
        server.server_close()
        if read_json(runtime,{}).get('pid')==os.getpid():runtime.unlink(missing_ok=True)
        server.service.jobs.pool.shutdown(wait=True,cancel_futures=True)
        release(instance)
