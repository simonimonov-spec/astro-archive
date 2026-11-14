"""User-controlled official login in an isolated Edge profile; no password API."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request
import urllib.parse
import uuid

from .site import HOST, ORIGIN


class Blob(ctypes.Structure):
    _fields_ = [('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]


def protect(data, decrypt=False):
    if os.name != 'nt':
        raise ValueError('Запазването на IASC сесия изисква Windows DPAPI.')
    source_buffer = (ctypes.c_ubyte*len(data)).from_buffer_copy(data)
    source, output = Blob(len(data),source_buffer), Blob()
    crypt, kernel = ctypes.WinDLL('crypt32',use_last_error=True), ctypes.WinDLL('kernel32')
    kernel.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel.LocalFree.restype = wintypes.HLOCAL
    if decrypt:
        fn = crypt.CryptUnprotectData
        fn.argtypes = [ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
        ok = fn(ctypes.byref(source),None,None,None,None,1,ctypes.byref(output))
    else:
        fn = crypt.CryptProtectData
        fn.argtypes = [ctypes.POINTER(Blob),wintypes.LPCWSTR,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
        ok = fn(ctypes.byref(source),'Astro Archive IASC session',None,None,None,1,ctypes.byref(output))
    if not ok:
        raise ValueError('IASC сесията не може да се отключи за този Windows потребител. Влез отново.')
    try:
        return ctypes.string_at(output.data,output.size)
    finally:
        kernel.LocalFree(ctypes.cast(output.data,wintypes.HLOCAL))


def seal(data):
    return protect(data)


def unseal(data):
    return protect(data,True)


class BrowserLogin:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.ws = None
        self.sequence = 0

    def command(self, method, parameters=None):
        self.sequence += 1
        seq = self.sequence
        self.ws.send(json.dumps(dict(id=seq,method=method,params=parameters or {})))
        deadline = time.monotonic()+10
        while time.monotonic()<deadline:
            reply = json.loads(self.ws.recv())
            if reply.get('id') == seq:
                if 'error' in reply:
                    raise ValueError('Браузърът не предостави IASC сесията.')
                return reply.get('result',{})
        raise TimeoutError('Браузърът не отговори на IASC заявката.')

    def run(self, cancel, progress):
        try:
            import websocket
        except ImportError:
            raise ValueError('Липсва websocket-client. Изпълни Install Login Support.cmd от папката на плъгина.') from None
        edge = next((Path(os.environ.get(env,default))/'Microsoft/Edge/Application/msedge.exe'
                     for env,default in [('ProgramFiles(x86)',r'C:\Program Files (x86)'),('ProgramFiles',r'C:\Program Files')]
                     if (Path(os.environ.get(env,default))/'Microsoft/Edge/Application/msedge.exe').is_file()),None)
        if edge is None:
            raise ValueError('Не е намерен Microsoft Edge. Изисква се за отделния официален прозорец за вход.')
        profile = self.folder / ('login-'+uuid.uuid4().hex)
        profile.mkdir(parents=True)
        subprocess.Popen([str(edge),'--user-data-dir='+str(profile),'--remote-debugging-port=0',
            '--remote-debugging-address=127.0.0.1','--no-first-run','--no-default-browser-check','--inprivate',
            '--app='+ORIGIN+'/Account/Login'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        deadline = time.monotonic()+600
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            while not cancel.is_set() and time.monotonic()<deadline:
                progress(0,1,'Влез сам в официалния IASC прозорец. Паролата не се чете от плъгина.')
                try:
                    port = int((profile/'DevToolsActivePort').read_text().splitlines()[0])
                    if not 1024<=port<=65535:
                        raise ValueError('Невалиден локален браузърен порт.')
                    with opener.open(f'http://127.0.0.1:{port}/json/list',timeout=3) as response:
                        pages = json.load(response)
                    page = next((p for p in pages if p.get('type')=='page' and urllib.parse.urlsplit(p.get('url','')).hostname==HOST),None)
                    if page is not None:
                        endpoint = page['webSocketDebuggerUrl']
                        parts = urllib.parse.urlsplit(endpoint)
                        if parts.scheme!='ws' or parts.hostname not in ('127.0.0.1','localhost') or parts.port!=port:
                            raise ValueError('Невалидна локална браузърна връзка.')
                        if self.ws is None:
                            self.ws = websocket.create_connection(endpoint,timeout=5,suppress_origin=True,http_no_proxy=['127.0.0.1','localhost'])
                        rows = self.command('Network.getCookies',{'urls':[ORIGIN]})['cookies']
                        rows = [r for r in rows if r.get('domain','').lstrip('.')==HOST]
                        if any(requires_session(r['name']) for r in rows):
                            progress(1,1,'IASC входът е получен. Проверка на достъпа до комплектите…')
                            return rows
                except (OSError,ValueError,KeyError,websocket.WebSocketException):
                    if self.ws:
                        self.ws.close()
                        self.ws = None
                cancel.wait(2)
            if cancel.is_set():
                raise InterruptedError('Входът е отменен.')
            raise ValueError('Времето за вход изтече. Натисни „Вход в IASC“ отново.')
        finally:
            if self.ws:
                try:
                    self.command('Browser.close')
                except Exception:
                    pass
                self.ws.close()
                self.ws = None


def requires_session(name):
    return 'applicationcookie' in name.lower() or name.lower() in ('.aspxauth','iascauth')
