"""Shared persistence, cancellation and bounded parallel work. No UI dependencies."""
from __future__ import annotations
import contextlib, hashlib, json, os, sqlite3, threading, time, uuid
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
CACHE=ROOT/'Backend'/'Cache'/'data'
SCHEMA='''
CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY, archive TEXT NOT NULL, relative TEXT NOT NULL,
 size INTEGER, mtime INTEGER, added REAL, fingerprint TEXT, meta TEXT NOT NULL, deleted INTEGER DEFAULT 0,
 UNIQUE(archive,relative));
CREATE INDEX IF NOT EXISTS file_archive ON files(archive,deleted,relative);
CREATE INDEX IF NOT EXISTS file_fingerprint ON files(fingerprint);
CREATE INDEX IF NOT EXISTS file_size ON files(archive,deleted,size,fingerprint);
CREATE TABLE IF NOT EXISTS prefixes(file TEXT PRIMARY KEY,size INTEGER,mtime INTEGER,value TEXT);
CREATE INDEX IF NOT EXISTS file_added ON files(archive,added);
CREATE INDEX IF NOT EXISTS file_telescope ON files(archive,json_extract(meta,'$.telescope'));
CREATE INDEX IF NOT EXISTS file_object ON files(archive,json_extract(meta,'$.object'));
CREATE INDEX IF NOT EXISTS file_night ON files(archive,json_extract(meta,'$.night'));
CREATE TABLE IF NOT EXISTS measurements(file TEXT, signature TEXT, result TEXT, PRIMARY KEY(file,signature));
CREATE TABLE IF NOT EXISTS decisions(file TEXT PRIMARY KEY, decision TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS trash(id TEXT PRIMARY KEY, manifest TEXT NOT NULL, state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workflows(id TEXT PRIMARY KEY, document TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY, document TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS history(id TEXT PRIMARY KEY, created REAL, document TEXT);
CREATE TABLE IF NOT EXISTS archive_index(archive TEXT PRIMARY KEY, document TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS archive_revision(archive TEXT PRIMARY KEY, revision INTEGER DEFAULT 0);
CREATE TRIGGER IF NOT EXISTS archive_insert AFTER INSERT ON files BEGIN
 INSERT INTO archive_revision VALUES(new.archive,1) ON CONFLICT(archive) DO UPDATE SET revision=revision+1; END;
CREATE TRIGGER IF NOT EXISTS archive_update AFTER UPDATE ON files BEGIN
 INSERT INTO archive_revision VALUES(new.archive,1) ON CONFLICT(archive) DO UPDATE SET revision=revision+1; END;
CREATE TRIGGER IF NOT EXISTS archive_delete AFTER DELETE ON files BEGIN
 INSERT INTO archive_revision VALUES(old.archive,1) ON CONFLICT(archive) DO UPDATE SET revision=revision+1; END;
'''

def atomic_json(path:Path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('x',encoding='utf-8') as stream:
            json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path)
    finally:temp.unlink(missing_ok=True)

def read_json(path:Path,default=None):
    if not path.exists():return default
    with path.open(encoding='utf-8-sig') as stream:return json.load(stream)

def digest(path:Path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(4*1024*1024),b''):h.update(block)
    return h.hexdigest()

def within(root:Path,relative:str,existing=True):
    root=Path(root).resolve(strict=existing)
    rel=Path(relative)
    if rel.is_absolute() or '..' in rel.parts or ':' in str(rel):raise ValueError('Път извън разрешената папка.')
    path=root/rel
    for part in [path,*path.parents]:
        if part==root:break
        if part.is_symlink() or getattr(part,'is_junction',lambda:False)():raise ValueError('Връзки към други папки не се следват.')
    if not path.resolve().is_relative_to(root):raise ValueError('Път извън разрешената папка.')
    if existing and not path.exists():raise FileNotFoundError(str(path))
    return path

class Store:
    def __init__(self,folder=CACHE):
        self.folder=Path(folder);self.folder.mkdir(parents=True,exist_ok=True)
        self.path=self.folder/'catalog.sqlite3';self.local=threading.local();self.write_lock=threading.RLock()
        with self.db() as db:db.executescript(SCHEMA)
    @contextlib.contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=30);db.row_factory=sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA foreign_keys=ON');db.execute('PRAGMA busy_timeout=30000')
        try:yield db;db.commit()
        except BaseException:db.rollback();raise
        finally:db.close()
    def history(self,operation,**fields):
        with self.db() as db:db.execute('INSERT INTO history VALUES(?,?,?)',(uuid.uuid4().hex,time.time(),json.dumps(dict(operation=operation,**fields),ensure_ascii=False)))

class Settings:
    def __init__(self,store):
        self.path=store.folder/'profile.json';self.lock=threading.RLock()
        self.value=read_json(self.path,dict(version=1,archives=[dict(id=i,name=n,path='',identity=None) for i,n in
             [('frames','Архив на кадри'),('stacks','Архив на стакове'),('photos','Архив на снимки')]],
             programs=[],aliases={},preferences=dict(themeId='builtin.night',languageId='bg',backgroundMode='theme',backgroundColor='#0c111a',animations=True),
             workers=max(1,min(8,(os.cpu_count() or 2)-1)),memoryMb=1024,cloudPolicy='review',approvedPluginPermissions={}))
    def save(self):
        with self.lock:atomic_json(self.path,self.value)
    def archive(self,ident):
        result=next((a for a in self.value['archives'] if a['id']==ident),None)
        if not result:raise ValueError('Непознат архив.')
        if not result['path']:raise ValueError('Избери папка за този архив от Настройки.')
        return result

class Events:
    def __init__(self):self.lock=threading.Lock();self.sequence=0;self.items=[]
    def emit(self,name,**payload):
        with self.lock:
            self.sequence+=1;self.items.append(dict(sequence=self.sequence,type='event',name=name,**payload));self.items=self.items[-1200:]
    def since(self,sequence):
        with self.lock:return [e for e in self.items if e['sequence']>sequence]

class Jobs:
    def __init__(self,events):
        self.events=events;self.pool=ThreadPoolExecutor(max_workers=4,thread_name_prefix='AstroArchive-task');self.tasks={};self.lock=threading.RLock();self.notification=None;self.restarting=False;self.inflight=0
    def submit(self,section,title,work,tier='normal'):
        ident=uuid.uuid4().hex;cancel=threading.Event()
        if tier not in ('important','normal','ambient'):tier='normal'
        task=dict(id=ident,section=section,title=title,tier=tier,state='running',progress=0,stepProgress=0,phase='Подготовка',step=1,steps=1,started=time.time(),cancel=cancel,canStop=True)
        with self.lock:
            if self.restarting:raise RuntimeError('Програмата се рестартира. Нова задача не може да бъде започната.')
            self.tasks[ident]=task
        def progress(done,total,step):
            if cancel.is_set():raise InterruptedError('Задачата е отменена.')
            with self.lock:task.update(progress=done/max(1,total),stepProgress=done/max(1,total),phase=step,done=done,total=total)
            self.publish()
        def execute():
            try:
                value=work(cancel,progress)
                with self.lock:
                    task.update(state='completed',progress=1,result=value,finished=time.time(),canStop=False)
                    # A successful retry clears earlier failures in the same section.
                    for other in self.tasks.values():
                        if other is not task and other['section']==section and other['state']=='blocked':
                            other.update(state='cancelled',finished=time.time(),canStop=False,cleared=True)
                    if tier=='important':
                        self.notification=dict(id=int(time.time()*1000),kind='completed',section=section,title=title,summary='Задачата приключи.',details=[task['phase']],tier=tier)
                self.events.emit('python.task.finished',id=ident,section=section)
            except InterruptedError:
                with self.lock:task.update(state='cancelled',finished=time.time(),canStop=False)
            except Exception as error:
                with self.lock:task.update(state='blocked',problem=str(error),finished=time.time(),canStop=True)
                self.events.emit('action.problem',section=section,message=str(error))
            self.publish()
        self.pool.submit(execute);self.publish();return dict(taskId=ident)
    def publish(self):self.events.emit('activity.changed',snapshot=self.snapshot())
    def enter_call(self):
        with self.lock:
            if self.restarting:raise RuntimeError('Програмата се рестартира.')
            self.inflight+=1
    def leave_call(self):
        with self.lock:self.inflight-=1
    def begin_restart(self):
        with self.lock:
            if self.restarting:raise RuntimeError('Вече се изпълнява рестартиране.')
            if self.inflight or any(t['state']=='running' for t in self.tasks.values()):raise ValueError('Има активни задачи или операции. Изчакай приключването им.')
            self.restarting=True
    def abort_restart(self):
        with self.lock:self.restarting=False
    def snapshot(self):
        with self.lock:
            tasks=[dict({k:v for k,v in t.items() if k not in ('cancel','result')},percent=round(t['progress']*100),overallPercent=round(t['progress']*100),elapsedSeconds=int(time.time()-t['started']),detail=f"{t.get('done',0)} / {t.get('total',0)}") for t in self.tasks.values()]
        return dict(tasks=tasks[-80:],notification=self.notification,state='running' if any(t['state']=='running' for t in tasks) else 'idle')
    def cancel(self,ident=None):
        with self.lock:
            for key,t in self.tasks.items():
                if not ident or key==ident:
                    if t['state']=='running':t['cancel'].set()
                    elif t['state']=='blocked':t.update(state='cancelled',canStop=False)
        self.publish()
        return dict(requested=True)
    def dismiss(self):
        with self.lock:self.notification=None
        self.publish();return dict(dismissed=True)

def parallel(function,arguments,workers,cancel,processes=False):
    """Bound submitted work, rather than queuing an entire archive into RAM."""
    if hasattr(arguments,'__len__'):
        if not len(arguments):return
        workers=max(1,min(workers,len(arguments)))
    executor=ProcessPoolExecutor(max_workers=workers) if processes else ThreadPoolExecutor(max_workers=workers)
    pending={};iterator=iter(arguments)
    try:
        while pending or iterator is not None:
            if cancel.is_set():raise InterruptedError('Задачата е отменена.')
            while iterator is not None and len(pending)<workers*2:
                try:argument=next(iterator)
                except StopIteration:iterator=None;break
                pending[executor.submit(function,argument)]=argument
            if not pending:break
            done,_=wait(pending,timeout=.2,return_when=FIRST_COMPLETED)
            for future in done:
                argument=pending.pop(future)
                yield argument,future.result()
    finally:executor.shutdown(wait=True,cancel_futures=True)
