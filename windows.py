"""Windows SMB transport. No passwords enter Python, settings or logs."""
from __future__ import annotations
import ctypes, ipaddress, json, os, socket, subprocess, threading, time, uuid
from ctypes import wintypes
from pathlib import Path
from .validation import unc, lan, share_name, label, atomic

POWERSHELL = str(Path(os.environ.get('SystemRoot', r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe')
HIDDEN = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

def powershell(code, timeout=20):
    result = subprocess.run([POWERSHELL,'-NoProfile','-NonInteractive','-Command',code],capture_output=True,timeout=timeout,creationflags=HIDDEN)
    if result.returncode:raise ValueError('Windows не предостави мрежовата информация. Провери SMB и разрешенията.')
    text = result.stdout.decode('utf-8-sig').strip()
    return json.loads(text) if text else None

def environment():
    code = "[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); $ErrorActionPreference='Stop'; $v=@(Get-NetIPConfiguration | Where-Object {$_.IPv4DefaultGateway -and $_.IPv4Address} | ForEach-Object {foreach($a in $_.IPv4Address){[pscustomobject]@{address=$a.IPAddress;prefix=[int]$a.PrefixLength;interface=$_.InterfaceAlias}}}); [pscustomobject]@{networks=$v;account=[Security.Principal.WindowsIdentity]::GetCurrent().Name;computer=$env:COMPUTERNAME;smb1=(Get-SmbServerConfiguration).EnableSMB1Protocol;smb2=(Get-SmbServerConfiguration).EnableSMB2Protocol;rejectPlaintext=(Get-SmbServerConfiguration).RejectUnencryptedAccess;service=(Get-Service LanmanServer).Status.ToString()} | ConvertTo-Json -Depth 5 -Compress"
    data = powershell(code)
    data['networks'] = [n for n in data['networks'] if _valid_lan(n)]
    return data

def _valid_lan(n):
    try:lan(n['address'],n['prefix']);return True
    except (ValueError,TypeError,KeyError):return False

def filetime():
    kernel = ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE,*([ctypes.POINTER(wintypes.FILETIME)]*4)]
    values=[wintypes.FILETIME() for _ in range(4)]
    if not kernel.GetProcessTimes(kernel.GetCurrentProcess(),*[ctypes.byref(v) for v in values]):raise ctypes.WinError(ctypes.get_last_error())
    return (values[0].dwHighDateTime<<32)|values[0].dwLowDateTime

def private_directory(folder):
    folder.mkdir(parents=True,exist_ok=True)
    data=powershell('[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); [Security.Principal.WindowsIdentity]::GetCurrent().User.Value | ConvertTo-Json -Compress')
    result=subprocess.run(['icacls.exe',str(folder),'/inheritance:r','/grant:r',f'*{data}:(OI)(CI)F','*S-1-5-18:(OI)(CI)F','*S-1-5-32-544:(OI)(CI)F'],capture_output=True,creationflags=HIDDEN)
    if result.returncode:raise ValueError('Windows не може да защити собствената служебна папка на плъгина.')

def elevate(script, request):
    class EXECUTE(ctypes.Structure):
        _fields_=[('cbSize',wintypes.DWORD),('fMask',ctypes.c_ulong),('hwnd',wintypes.HWND),('lpVerb',wintypes.LPCWSTR),('lpFile',wintypes.LPCWSTR),('lpParameters',wintypes.LPCWSTR),('lpDirectory',wintypes.LPCWSTR),('nShow',ctypes.c_int),('hInstApp',wintypes.HINSTANCE),('lpIDList',ctypes.c_void_p),('lpClass',wintypes.LPCWSTR),('hkeyClass',wintypes.HKEY),('dwHotKey',wintypes.DWORD),('hIcon',wintypes.HANDLE),('hProcess',wintypes.HANDLE)]
    args=['-NoProfile','-NonInteractive','-WindowStyle','Hidden','-ExecutionPolicy','Bypass','-File',str(script),'-Request',str(request)]
    call=EXECUTE();call.cbSize=ctypes.sizeof(call);call.fMask=0x40;call.lpVerb='runas';call.lpFile=POWERSHELL;call.lpParameters=subprocess.list2cmdline(args);call.lpDirectory=str(script.parent);call.nShow=0
    shell=ctypes.WinDLL('shell32',use_last_error=True);shell.ShellExecuteExW.argtypes=[ctypes.POINTER(EXECUTE)];shell.ShellExecuteExW.restype=wintypes.BOOL
    if not shell.ShellExecuteExW(ctypes.byref(call)):
        error=ctypes.get_last_error()
        if error==1223:raise InterruptedError('Администраторското разрешение е отказано. Нищо не е включено.')
        raise ctypes.WinError(error)
    if call.hProcess:
        kernel=ctypes.WinDLL('kernel32');kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle(call.hProcess)

class Controller:
    def __init__(self,folder,helper):
        self.folder=Path(folder);self.helper=Path(helper);self.session=None;self.closing=threading.Event();self.lock=threading.RLock();self.pulse=None

    def status(self):
        if not self.session:return dict(state='off',encrypted=False,openFiles=0)
        try:
            path=self.session/'status.json';data=json.loads(path.read_text(encoding='utf-8-sig'))
            if data.get('state')=='running' and time.time()-path.stat().st_mtime>12:return dict(state='unknown',encrypted=False,problem='Няма актуален отговор от Windows хоста.')
            return data
        except (OSError,ValueError):return dict(state='starting',encrypted=False,openFiles=0)

    def start(self,plan,cancel,progress):
        with self.lock:
            if self.session and self.status()['state'] not in ('off','stopped','error'):raise ValueError('Хостът вече е включен или се включва.')
            session=self.folder/'Runtime'/uuid.uuid4().hex;private_directory(session)
            nonce=uuid.uuid4().hex;request=dict(plan,nonce=nonce,parentPid=os.getpid(),parentFiletime=str(filetime()))
            atomic(session/'request.json',request);self.session=session;self.closing.clear()
            def heartbeat():
                while not self.closing.is_set():
                    try:atomic(session/'heartbeat.json',dict(nonce=nonce,at=time.time()))
                    except OSError:pass
                    self.closing.wait(1)
            self.pulse=threading.Thread(target=heartbeat,daemon=True,name='SharedArchives-lease');self.pulse.start()
            progress(0,1,'Windows изчаква администраторско потвърждение за разрешения план')
            try:elevate(self.helper,session/'request.json')
            except BaseException as error:
                self.stop()
                atomic(session/'status.json',dict(state='off' if isinstance(error,InterruptedError) else 'error',encrypted=False,openFiles=0,problem=str(error)))
                raise
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            if cancel.is_set():self.stop();raise InterruptedError('Включването е отменено.')
            status=self.status()
            if status['state']=='running':progress(1,1,'SMB 3 хостът е включен със задължително шифроване');return status
            if status['state'] in ('error','stopped'):raise ValueError(status.get('problem') or 'Windows не включи хоста.')
            progress(0,1,'Изчаква се администраторско потвърждение от Windows');cancel.wait(.4)
        self.stop();raise ValueError('Windows не потвърди включването. Провери администраторския диалог.')

    def stop(self):
        with self.lock:
            if self.session:
                try:
                    request=json.loads((self.session/'request.json').read_text(encoding='utf-8'))
                    atomic(self.session/'stop.json',dict(nonce=request['nonce']))
                except (OSError,ValueError,KeyError):pass
            self.closing.set()

class NETRESOURCE(ctypes.Structure):
    _fields_=[('dwScope',wintypes.DWORD),('dwType',wintypes.DWORD),('dwDisplayType',wintypes.DWORD),('dwUsage',wintypes.DWORD),('lpLocalName',wintypes.LPWSTR),('lpRemoteName',wintypes.LPWSTR),('lpComment',wintypes.LPWSTR),('lpProvider',wintypes.LPWSTR)]

def connection(value,*,interactive=False,check_lan=True):
    path,server,share=unc(value)
    if check_lan:
        networks=environment()['networks'];allowed=[ipaddress.ip_network(f"{n['address']}/{n['prefix']}",strict=False) for n in networks]
        addresses={r[4][0] for r in socket.getaddrinfo(server,445,socket.AF_INET,socket.SOCK_STREAM)}
        if not addresses or any(not any(ipaddress.ip_address(a) in n for n in allowed) for a in addresses):
            raise ValueError('Компютърът трябва да е в същата LAN мрежа. VPN и публични адреси не се използват от този плъгин.')
    resource=NETRESOURCE();resource.dwType=1;resource.lpRemoteName='\\\\'+server+'\\'+share
    mpr=ctypes.WinDLL('mpr',use_last_error=True);mpr.WNetAddConnection3W.argtypes=[wintypes.HWND,ctypes.POINTER(NETRESOURCE),wintypes.LPCWSTR,wintypes.LPCWSTR,wintypes.DWORD];mpr.WNetAddConnection3W.restype=wintypes.DWORD
    # Windows SDK CONNECT_REQUIRE_PRIVACY, CONNECT_REQUIRE_INTEGRITY and
    # CONNECT_TEMPORARY. Authentication UI stays entirely in the native provider.
    flags=0x8000|0x4000|0x4|(0x8|0x10 if interactive else 0)
    error=mpr.WNetAddConnection3W(None,ctypes.byref(resource),None,None,flags)
    if error==1223:raise InterruptedError('Свързването е отменено.')
    if error:raise ValueError(f'Windows не свърза шифрования дял (код {error}). Използвай Windows паролата на разрешения потребител; PIN не е мрежова парола.')
    # Verify the negotiated connection before exposing any astronomical file.
    if not Path(path).is_dir():raise ValueError('Мрежовата папка не е достъпна.')
    result=powershell("[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); @(Get-SmbConnection | Select-Object ServerName,ShareName,Dialect,Encrypted) | ConvertTo-Json -Compress") or []
    if isinstance(result,dict):result=[result]
    names={server.casefold(),server.split('.')[0].casefold()}
    rows=[r for r in result if str(r.get('ServerName','')).casefold() in names and str(r.get('ShareName','')).casefold()==share.casefold()]
    if not rows or any(r.get('Encrypted') is not True or tuple(int(v) for v in str(r['Dialect']).split('.'))<(3,0) for r in rows):
        raise ValueError('Делът не потвърждава шифрована SMB 3 връзка. Добавянето е отказано; няма нешифрован резервен режим.')
    return dict(path=path,server=server,share=share,encrypted=True,dialect=str(rows[0]['Dialect']))
