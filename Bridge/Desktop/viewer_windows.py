# Beta 1.3 UI
"""Associate comparison windows with this installation's main Edge window."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import re
import threading
import time

from Bridge.Desktop.window_placement import _user32


class ViewerWindows:
    def __init__(self, title):
        self.title = title
        self.windows = {}
        self.lock = threading.Lock()

    def command(self, params):
        token, action = params.get('token'), params.get('action')
        if not isinstance(token, str) or not re.fullmatch(r'[0-9a-f]{32}', token):
            raise ValueError('Невалиден прозорец за преглед.')
        if os.name != 'nt':
            raise RuntimeError('Отделните прозорци са достъпни в Windows.')
        with self.lock:
            if action == 'forget':
                self.windows.pop(token, None)
                return dict(ok=True)
            user32 = _window_api()
            if action == 'bind':
                owner, window = _find_pair(user32, self.title, token)
                _attach(user32, window, owner)
                self.windows[token] = (window, owner)
            else:
                window, owner = self.windows.get(token, (None, None))
                if not window or not user32.IsWindow(window) or user32.GetWindow(window, 4) != owner:
                    raise ValueError('Прозорецът за преглед вече е затворен.')
                if action == 'minimize':
                    user32.ShowWindowAsync(window, 6)
                elif action in ('focus', 'maximize'):
                    if action == 'maximize':
                        user32.ShowWindowAsync(window, 9 if user32.IsZoomed(window) else 3)
                    elif user32.IsIconic(window):
                        user32.ShowWindowAsync(window, 9)
                    user32.SetForegroundWindow(window)
                else:
                    raise ValueError('Непозната команда за прозореца.')
        return dict(ok=True)


def _window_api():
    user32 = _user32()
    hwnd, integer = wintypes.HWND, ctypes.c_ssize_t
    user32.GetWindowThreadProcessId.argtypes = [hwnd, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindow.argtypes = [hwnd, wintypes.UINT]
    user32.GetWindow.restype = hwnd
    user32.GetWindowLongPtrW.argtypes = [hwnd, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = integer
    user32.SetWindowLongPtrW.argtypes = [hwnd, ctypes.c_int, integer]
    user32.SetWindowLongPtrW.restype = integer
    for name in ('SetForegroundWindow', 'IsIconic', 'IsZoomed'):
        getattr(user32, name).argtypes = [hwnd]
    for name in ('ShowWindow', 'ShowWindowAsync'):
        getattr(user32, name).argtypes = [hwnd, ctypes.c_int]
    return user32


def _process(user32, window):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(window, ctypes.byref(pid))
    return pid.value


def _find_pair(user32, title, token):
    def find(name):
        return (user32.FindWindowW('Chrome_WidgetWin_1', name)
                or user32.FindWindowW('Chrome_WidgetWin_1', name + ' - Microsoft Edge'))

    for _ in range(25):
        owner, window = find(title), find(title + ' · ' + token)
        if owner and window and owner != window and _process(user32, owner) == _process(user32, window):
            return owner, window
        time.sleep(0.08)
    raise RuntimeError('Windows не намери отделения прозорец на Astro Archive.')


def _attach(user32, window, owner):
    owner_index, style_index, app_window = -8, -20, 0x40000
    style = user32.GetWindowLongPtrW(window, style_index)
    user32.ShowWindow(window, 0)
    try:
        user32.SetWindowLongPtrW(window, owner_index, owner)
        user32.SetWindowLongPtrW(window, style_index, style & ~app_window)
        if user32.GetWindow(window, 4) != owner or user32.GetWindowLongPtrW(window, style_index) & app_window:
            raise RuntimeError('Windows не свърза отделения прозорец с Astro Archive.')
    finally:
        user32.ShowWindow(window, 5)
