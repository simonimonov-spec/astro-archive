"""Small native Windows tray menu, without an extra GUI dependency."""
from __future__ import annotations
import ctypes,os,threading,uuid
from ctypes import wintypes as w
from pathlib import Path

class Tray:
    def __init__(self,controller,show,quit_):self.controller=controller;self.show=show;self.quit=quit_;self.hwnd=None;self.problem=None;self.ready=threading.Event();self.thread=None
    def start(self):
        if os.name!='nt':return
        self.thread=threading.Thread(target=self.run,name='AstroArchive-tray',daemon=True);self.thread.start();self.ready.wait(3)
    def stop(self):
        if self.hwnd:ctypes.windll.user32.PostMessageW(self.hwnd,0x10,0,0)
        if self.thread and self.thread is not threading.current_thread():self.thread.join(3)
    def remote(self):
        try:return self.controller.status({})
        except (ValueError,PermissionError):return dict(running=False,url='')
    def copy(self,text):
        user=ctypes.WinDLL('user32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.GlobalAlloc.argtypes=[w.UINT,ctypes.c_size_t];kernel.GlobalAlloc.restype=w.HGLOBAL
        kernel.GlobalLock.argtypes=[w.HGLOBAL];kernel.GlobalLock.restype=w.LPVOID;kernel.GlobalUnlock.argtypes=[w.HGLOBAL]
        user.SetClipboardData.argtypes=[w.UINT,w.HANDLE];user.SetClipboardData.restype=w.HANDLE
        raw=(str(text)+'\0').encode('utf-16-le');handle=kernel.GlobalAlloc(0x42,len(raw))
        if not handle:return
        pointer=kernel.GlobalLock(handle);ctypes.memmove(pointer,raw,len(raw));kernel.GlobalUnlock(handle)
        if user.OpenClipboard(self.hwnd):
            try:user.EmptyClipboard();user.SetClipboardData(13,handle)
            finally:user.CloseClipboard()
    def run(self):
        try:self.loop()
        except Exception as error:self.problem=str(error);self.ready.set()
    def loop(self):
        user=ctypes.WinDLL('user32',use_last_error=True);shell=ctypes.WinDLL('shell32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        callback=ctypes.WINFUNCTYPE(ctypes.c_ssize_t,w.HWND,w.UINT,w.WPARAM,w.LPARAM)
        class WNDCLASS(ctypes.Structure):
            _fields_=[('style',w.UINT),('proc',callback),('classExtra',ctypes.c_int),('windowExtra',ctypes.c_int),('instance',w.HINSTANCE),('icon',w.HICON),('cursor',w.HANDLE),('background',w.HBRUSH),('menu',w.LPCWSTR),('name',w.LPCWSTR)]
        class GUID(ctypes.Structure):_fields_=[('d1',w.DWORD),('d2',w.WORD),('d3',w.WORD),('d4',ctypes.c_ubyte*8)]
        class NOTIFY(ctypes.Structure):
            _fields_=[('size',w.DWORD),('window',w.HWND),('id',w.UINT),('flags',w.UINT),('message',w.UINT),('icon',w.HICON),('tip',w.WCHAR*128),('state',w.DWORD),('mask',w.DWORD),('info',w.WCHAR*256),('version',w.UINT),('title',w.WCHAR*64),('infoFlags',w.DWORD),('guid',GUID),('balloonIcon',w.HICON)]
        user.DefWindowProcW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM];user.DefWindowProcW.restype=ctypes.c_ssize_t
        user.CreateWindowExW.argtypes=[w.DWORD,w.LPCWSTR,w.LPCWSTR,w.DWORD,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,w.HWND,w.HMENU,w.HINSTANCE,w.LPVOID];user.CreateWindowExW.restype=w.HWND
        user.CreatePopupMenu.restype=w.HMENU;user.AppendMenuW.argtypes=[w.HMENU,w.UINT,ctypes.c_size_t,w.LPCWSTR]
        user.TrackPopupMenu.argtypes=[w.HMENU,w.UINT,ctypes.c_int,ctypes.c_int,ctypes.c_int,w.HWND,w.LPVOID];user.TrackPopupMenu.restype=w.UINT;user.DestroyMenu.argtypes=[w.HMENU]
        user.SetForegroundWindow.argtypes=[w.HWND];user.LoadImageW.argtypes=[w.HINSTANCE,w.LPCWSTR,w.UINT,ctypes.c_int,ctypes.c_int,w.UINT];user.LoadImageW.restype=w.HANDLE
        kernel.GetModuleHandleW.argtypes=[w.LPCWSTR];kernel.GetModuleHandleW.restype=w.HMODULE;shell.Shell_NotifyIconW.argtypes=[w.DWORD,ctypes.POINTER(NOTIFY)]
        notice=NOTIFY();self._notice=notice
        def action(ident):
            if ident==1:self.show()
            if ident==2:self.copy(self.remote().get('url',''))
            if ident==3:
                try:self.controller.stop({})
                except (ValueError,PermissionError):pass
            if ident==4:
                if any(t['state']=='running' for t in self.controller.ctx.remote('tasks')['tasks']):self.show()
                else:self.quit()
        @callback
        def procedure(hwnd,message,wp,lp):
            if message==0x414:
                if lp==0x203:threading.Thread(target=self.show,daemon=True).start()
                elif lp==0x205:
                    menu=user.CreatePopupMenu();state=self.remote();user.AppendMenuW(menu,0,1,'Отвори Astro Archive');user.AppendMenuW(menu,0 if state.get('url') else 1,2,'Копирай Remote адреса');user.AppendMenuW(menu,0 if state.get('running') else 1,3,'Спри Remote и всички сесии');user.AppendMenuW(menu,0x800,0,None);user.AppendMenuW(menu,0,4,'Изход от Astro Archive')
                    point=w.POINT();user.GetCursorPos(ctypes.byref(point));user.SetForegroundWindow(hwnd);ident=user.TrackPopupMenu(menu,0x100|2,point.x,point.y,0,hwnd,None);user.DestroyMenu(menu)
                    if ident:threading.Thread(target=action,args=(ident,),daemon=True).start()
                return 0
            if message==0x10:user.DestroyWindow(hwnd);return 0
            if message==2:shell.Shell_NotifyIconW(2,ctypes.byref(notice));user.PostQuitMessage(0);return 0
            return user.DefWindowProcW(hwnd,message,wp,lp)
        self._procedure=procedure;instance=kernel.GetModuleHandleW(None);name='AstroArchiveTray'+uuid.uuid4().hex;wc=WNDCLASS();wc.proc=procedure;wc.instance=instance;wc.name=name
        if not user.RegisterClassW(ctypes.byref(wc)):raise ctypes.WinError(ctypes.get_last_error())
        self.hwnd=user.CreateWindowExW(0,name,'Astro Archive',0,0,0,0,0,None,None,instance,None)
        if not self.hwnd:raise ctypes.WinError(ctypes.get_last_error())
        notice.size=ctypes.sizeof(NOTIFY);notice.window=self.hwnd;notice.id=1;notice.flags=1|2|4;notice.message=0x414;notice.tip='Astro Archive · Remote Operation';notice.icon=user.LoadImageW(None,str(Path(__file__).parent/'logo.ico'),1,0,0,0x10|0x40)
        if not shell.Shell_NotifyIconW(0,ctypes.byref(notice)):raise ctypes.WinError(ctypes.get_last_error())
        self.ready.set();message=w.MSG()
        while user.GetMessageW(ctypes.byref(message),None,0,0)>0:user.TranslateMessage(ctypes.byref(message));user.DispatchMessageW(ctypes.byref(message))
        user.UnregisterClassW.argtypes=[w.LPCWSTR,w.HINSTANCE];user.UnregisterClassW(name,instance)
        self.hwnd=None
