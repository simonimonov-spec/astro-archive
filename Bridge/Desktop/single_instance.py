"""One running copy per installation and activation of its existing window."""
from __future__ import annotations
import ctypes, hashlib, os
from ctypes import wintypes
from pathlib import Path

TITLE = 'Astro Archive'
_ERROR_ALREADY_EXISTS = 183
_SW_SHOW = 5
_SW_RESTORE = 9


def _kernel():
    library = ctypes.WinDLL('kernel32', use_last_error=True)
    library.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    library.CreateMutexW.restype = wintypes.HANDLE
    library.CloseHandle.argtypes = [wintypes.HANDLE]
    library.CloseHandle.restype = wintypes.BOOL
    return library


def mutex_name(root):
    digest = hashlib.sha256(str(Path(root).resolve()).casefold().encode('utf-8')).hexdigest()[:32]
    return 'Local\\AstroArchive-' + digest


def acquire(root):
    """Take the per-installation mutex; None means another copy owns it."""
    if os.name != 'nt':
        return None
    library = _kernel()
    handle = library.CreateMutexW(None, False, mutex_name(root))
    if not handle:
        return None
    if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
        library.CloseHandle(handle)
        return None
    return handle


def release(handle):
    if os.name == 'nt' and handle:
        _kernel().CloseHandle(handle)


def activate(title=TITLE):
    """Bring the existing application window to the front without opening another one."""
    if os.name != 'nt':
        return False
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.AttachThreadInput.restype = wintypes.BOOL
    user32.FlashWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
    user32.FlashWindow.restype = wintypes.BOOL

    found = []
    def visit(window, _):
        if not user32.IsWindowVisible(window):
            return True
        length = user32.GetWindowTextLengthW(window)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(window, buffer, length + 1)
        if buffer.value.strip() == title:
            found.append(window)
            return False
        return True

    user32.EnumWindows(callback(visit), 0)
    if not found:
        return False
    window = found[0]
    user32.ShowWindow(window, _SW_RESTORE if user32.IsIconic(window) else _SW_SHOW)
    user32.BringWindowToTop(window)
    if not user32.SetForegroundWindow(window):
        foreground = user32.GetForegroundWindow()
        target = user32.GetWindowThreadProcessId(window, None)
        current = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
        attached = bool(current and current != target and user32.AttachThreadInput(current, target, True))
        try:
            user32.SetForegroundWindow(window)
            user32.BringWindowToTop(window)
        finally:
            if attached:
                user32.AttachThreadInput(current, target, False)
    if user32.GetForegroundWindow() != window:
        user32.FlashWindow(window, True)
    return True
