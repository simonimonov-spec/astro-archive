"""Windows helper: hold the old process handle before requesting its exit."""
from pathlib import Path
import ctypes, os, subprocess, sys
from ctypes import wintypes

def wait_and_restart(pid,root,timeout=90000):
    if os.name!='nt':raise RuntimeError('Неподдържана система.')
    root=Path(root).resolve()
    if root!=Path(__file__).resolve().parents[2] or not (root/'start.pyw').is_file():raise ValueError('Невалидна програмна папка.')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD];kernel.WaitForSingleObject.restype=wintypes.DWORD
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.OpenProcess(0x00100000,False,pid)
    if not handle:raise OSError('Старият процес не може да бъде проверен.')
    try:
        sys.stdout.buffer.write(b'READY\n');sys.stdout.buffer.flush()
        if kernel.WaitForSingleObject(handle,timeout)!=0:raise TimeoutError('Старото приложение още работи. Ново копие не е стартирано.')
    finally:kernel.CloseHandle(handle)
    subprocess.Popen([sys.executable,'-B',str(root/'start.pyw')],cwd=str(root),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))

if __name__=='__main__':
    try:
        if len(sys.argv)!=3:raise ValueError('Невалидна команда за рестартиране.')
        wait_and_restart(int(sys.argv[1]),sys.argv[2])
    except Exception as error:
        if os.name=='nt':ctypes.windll.user32.MessageBoxW(0,str(error),'Astro Archive · Рестартиране',16)
        sys.exit(1)
