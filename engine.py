"""Own CLI script builder and owned-process driver. No Siril modules imported."""
from __future__ import annotations
import math,os,queue,re,shutil,subprocess,threading,time
from pathlib import Path

DEFAULTS=dict(executable='',debayer=True,registration=True,mosaic=True,drizzle=False,scale=1.,pixfrac=1.,
              normalize='addscale',rejection=True,sigmaLow=3.,sigmaHigh=3.,background=False,
              cpu=max(1,os.cpu_count() or 1),memory=.8,parallel=1,dark='',flat='',bias='',combine=False,override=False)

MAX_PARALLEL=16

def settings(values):
    result=dict(DEFAULTS,**{k:v for k,v in values.items() if k in DEFAULTS})
    for key in ('debayer','registration','mosaic','drizzle','rejection','background','combine','override'):
        if type(result[key]) is not bool:raise ValueError('Невалидна отметка: '+key)
    for key,lo,hi in [('scale',.5,3),('pixfrac',.1,1),('sigmaLow',.1,10),('sigmaHigh',.1,10),('memory',.05,.95),('cpu',1,os.cpu_count() or 1),('parallel',1,MAX_PARALLEL)]:
        value=result[key]
        if type(value) not in (int,float) or not lo<=value<=hi:raise ValueError('Невалидна стойност: '+key)
    if result['parallel']!=int(result['parallel']):raise ValueError('Броят паралелни процеси трябва да е цяло число.')
    result['cpu']=int(result['cpu']);result['parallel']=int(result['parallel'])
    if result['normalize'] not in ('addscale','add','mulscale','mul','none'):raise ValueError('Непозната нормализация.')
    for key in ('executable','dark','flat','bias'):
        if not isinstance(result[key],str) or any(c in result[key] for c in '\r\n\x00"'):raise ValueError('Невалиден път за '+key)
    if result['drizzle'] and not result['registration']:raise ValueError('Drizzle изисква подравняване.')
    return result

def allocation(options,count):
    """A fixed batch budget: no worker inherits the full machine allocation."""
    o=settings(options)
    workers=max(1,min(o['parallel'],o['cpu'],MAX_PARALLEL,max(1,count),math.floor(o['memory']/.05+1e-9)))
    base,extra=divmod(o['cpu'],workers)
    return [dict(cpu=base+(slot<extra),memory=o['memory']/workers) for slot in range(workers)]

def isolated_config(folder):
    """Use a workspace-owned init file; concurrent CLIs never write shared prefs."""
    target=Path(folder)/'siril-worker.ini'
    if not target.exists():
        base=Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'.config')))/'siril'
        source=next((base/name for name in ('config.1.4.ini','config1.4.ini','config.ini') if (base/name).is_file() and not (base/name).is_symlink() and (base/name).stat().st_size<=2_000_000),None)
        if source:shutil.copyfile(source,target)
        else:target.write_text('[core]\n',encoding='utf-8')
    return target

def plan(options,cfa=False,combined=False):
    """Fixed commands; paths in scripts are our ASCII workspace names only."""
    o=settings(options);lines=['requires 1.4.0','setext fit','set32bits',f"setcpu {o['cpu']}",f"setmem {o['memory']:g}",'cd source']
    # RGB stacks are never debayered again. CFA drizzle receives CFA originals.
    direct_debayer=cfa and o['debayer'] and not o['drizzle'] and not any(o[k] for k in ('dark','flat','bias'))
    lines+=['convert light -out=..'+(' -debayer' if direct_debayer else ''),'cd ..'];sequence='light'
    calibration=[f'-{key}=master-{key}' for key in ('dark','flat','bias') if o[key] and not combined]
    if calibration or cfa and o['debayer'] and not o['drizzle'] and not direct_debayer:
        flags=calibration+(['-cfa'] if cfa else [])+(['-debayer'] if cfa and o['debayer'] and not o['drizzle'] else [])
        lines+=['calibrate light '+' '.join(flags)];sequence='pp_light'
    if o['background'] and not combined:lines+=['seqsubsky '+sequence+' 1 -prefix=bkg_'];sequence='bkg_'+sequence
    if o['registration']:
        flags=['-interp=lanczos4','-framing='+('max' if o['mosaic'] else 'current'),f"-scale={o['scale']:g}"]
        if o['drizzle']:flags+=['-drizzle',f"-pixfrac={o['pixfrac']:g}",'-kernel=square']
        # In Siril 1.4 framing belongs to SEQAPPLYREG, not REGISTER.
        lines+=['register '+sequence+' -2pass -transf=homography','seqapplyreg '+sequence+' -filter-included '+' '.join(flags)];registered='r_'+sequence
    else:registered=sequence
    # No quality-filter flags: registration failures remain visible for review.
    reject=f"w {o['sigmaLow']:g} {o['sigmaHigh']:g}" if o['rejection'] else 'n'
    normalize='-nonorm' if o['normalize']=='none' else '-norm='+o['normalize']
    lines+=['stack '+registered+' rej '+reject+' '+normalize+' -filter-included -32b -out=result'+(' -maximize' if o['registration'] and o['mosaic'] else '')+(' -weight=nbstack' if combined else '')]
    return lines,registered

def included(folder,sequence,count):
    """Read Siril file numbers; registration may omit failed original numbers."""
    import shlex
    def read(name):
        paths=[p for p in (Path(folder)/(name+'.seq'),Path(folder)/(name+'_.seq')) if p.is_file()]
        if len(paths)!=1 or paths[0].stat().st_size>128*1024**2:raise ValueError('Липсва еднозначна последователност за проверка на използваните кадри.')
        flags={};expected=None;selected=None
        for line in paths[0].read_text(encoding='utf-8-sig').splitlines():
            if line.startswith('S '):
                parts=shlex.split(line)
                if expected is not None or len(parts)<5:raise ValueError('Нееднозначен S запис.')
                expected=int(parts[3]);selected=int(parts[4])
            elif line.startswith('I '):
                p=line.split();number=int(p[1]);flag=int(p[2])
                if number in flags or flag not in (0,1):raise ValueError('Нееднозначни I записи.')
                flags[number]=bool(flag)
        if expected!=len(flags) or sum(flags.values())!=selected or not flags or set(flags)-set(range(1,count+1)):raise ValueError('Последователността не съвпада с подадените кадри. Прибирането е спряно за проверка.')
        return flags
    flags=read(sequence)
    if len(flags)!=count:
        original=read('light')
        if len(original)!=count:raise ValueError('Липсва пълна първоначална последователност за връзката с отказаните кадри.')
    return [i for i,flag in flags.items() if flag]

class Engine:
    def __init__(self):self.process=None;self.lock=threading.RLock();self.stopped=False
    def stop(self):
        with self.lock:
            self.stopped=True;p=self.process
            if p and p.poll() is None:
                p.terminate()
                try:p.wait(timeout=3)
                except subprocess.TimeoutExpired:p.kill();p.wait(timeout=3)
    def run(self,executable,folder,commands,cancel,log,status,progress):
        executable=Path(executable)
        if not executable.is_file() or executable.name.lower()!='siril-cli.exe':raise ValueError('Посочи отделно инсталирания siril-cli.exe от Настройки.')
        folder=Path(folder);script=folder/'commands.ssf';script.write_text('\n'.join(commands)+'\n',encoding='utf-8');config=isolated_config(folder)
        environment=dict(os.environ,LC_ALL='C',LANG='C');messages=queue.Queue(maxsize=512);reader_stop=threading.Event()
        with self.lock:
            if cancel.is_set():raise InterruptedError('Задачата е спряна преди стартирането на Siril.')
            self.stopped=False
            self.process=subprocess.Popen([str(executable),'-i',str(config),'-d',str(folder),'-s',str(script)],cwd=str(folder),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,encoding='utf-8',errors='replace',env=environment,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),bufsize=1)
            process=self.process
        log('Siril PID '+str(process.pid)+' · '+script.name);status('green','Siril е включен · изпълнява скрипта')
        def enqueue(line):
            while not reader_stop.is_set():
                try:messages.put(line,timeout=.15);return
                except queue.Full:pass
        def drain():
            pending=''
            try:
                while True:
                    char=process.stdout.read(1)
                    if not char:break
                    if char in '\r\n':
                        if pending:enqueue(pending);pending=''
                    elif len(pending)<16000:pending+=char
                if pending:enqueue(pending)
            finally:enqueue(None)
        reader=threading.Thread(target=drain,daemon=True);reader.start();error=False;finished=False;last=0
        try:
            with (folder/'siril.log').open('a',encoding='utf-8') as transcript:
                while not finished or process.poll() is None:
                    if cancel.is_set() or self.stopped:raise InterruptedError('Siril е спрян. Работните копия и дневникът са запазени.')
                    try:line=messages.get(timeout=.15)
                    except queue.Empty:continue
                    if line is None:finished=True;continue
                    line=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',line);transcript.write(line+'\n');transcript.flush();log(line)
                    if re.search(r'status:\s*error|script execution failed|error in line',line,re.I):error=True;status('yellow','Siril работи, но съобщава проблем')
                    elif re.search(r'\bwarning\b',line,re.I):status('yellow','Siril работи с предупреждения')
                    match=re.search(r'(?:progress:\s*)?(\d+(?:\.\d+)?)\s*%',line)
                    if match and time.monotonic()-last>.2:progress(min(99,float(match[1])),100,'Siril · '+line[:100]);last=time.monotonic()
                code=process.wait()
                if code or error:raise RuntimeError(f'Siril приключи с грешка ({code}). Виж конзолата и повтори избрания ден.')
        finally:
            reader_stop.set()
            if process.poll() is None:self.stop()
            reader.join(timeout=2)
            if not reader.is_alive():process.stdout.close()
            with self.lock:self.process=None
            status('red','Siril е изключен · процесът е приключил')
