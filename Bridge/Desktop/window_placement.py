# Beta 1.3 UI (в разработка, не е издание) · виж docs/BETA-1.3-UI.md
"""Centered 16:9 first start and remembered window placement for the app window.

The browser window belongs to Edge, so the host watches it by its unique title,
remembers where it was left (including maximized/full screen) and puts it back
there on the next start. All coordinates are physical screen pixels.
"""
from __future__ import annotations
import ctypes, json, os, threading, time
from ctypes import wintypes
from pathlib import Path

SW_SHOWNORMAL = 1
SW_SHOWMINIMIZED = 2
SW_SHOWMAXIMIZED = 3
WPF_RESTORETOMAXIMIZED = 2
SHARE = 0.85          # part of the work area used by the first, centered window
MINIMUM = (640, 360)  # smaller remembered windows are treated as accidental


def default_rect(work):
    """Largest 16:9 rectangle within SHARE of the work area, centered in it."""
    left, top, right, bottom = work
    width, height = (right - left) * SHARE, (bottom - top) * SHARE
    width = min(width, height * 16 / 9)
    height = width * 9 / 16
    width, height = int(round(width)), int(round(height))
    x = left + (right - left - width) // 2
    y = top + (bottom - top - height) // 2
    return (x, y, x + width, y + height)


def _valid_rect(rect):
    return (isinstance(rect, list) and len(rect) == 4 and all(isinstance(v, int) and not isinstance(v, bool) for v in rect)
            and rect[2] - rect[0] >= MINIMUM[0] and rect[3] - rect[1] >= MINIMUM[1])


def load(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or not isinstance(value.get('maximized'), bool):
        return None
    rect = value.get('rect')
    if not (isinstance(rect, list) and len(rect) == 4 and all(isinstance(v, int) and not isinstance(v, bool) for v in rect)):
        return None
    return dict(rect=rect, maximized=value['maximized'])


def save(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state), encoding='utf-8')
    os.replace(temporary, path)


def _visible(rect, monitors):
    """The title bar must be reachable: enough of the top strip lies on some monitor."""
    left, top, right, _ = rect
    strip = (left, top, right, top + 40)
    for m in monitors:
        width = min(strip[2], m[2]) - max(strip[0], m[0])
        height = min(strip[3], m[3]) - max(strip[1], m[1])
        if width >= 120 and height >= 20:
            return True
    return False


def choose(saved, monitors, primary):
    """Placement to apply at start: the remembered one, or centered 16:9."""
    fallback = list(default_rect(primary))
    if not saved:
        return dict(rect=fallback, maximized=False)
    rect = saved['rect'] if _valid_rect(saved['rect']) and _visible(saved['rect'], monitors) else fallback
    return dict(rect=rect, maximized=saved['maximized'])


def observed(showcmd, flags, normal, window, monitor, previous):
    """Turn a Win32 placement into what should be remembered."""
    restore = list(normal)
    if showcmd == SW_SHOWMAXIMIZED:
        return dict(rect=restore, maximized=True)
    if showcmd == SW_SHOWMINIMIZED:
        return dict(rect=restore, maximized=bool(flags & WPF_RESTORETOMAXIMIZED))
    if tuple(window) == tuple(monitor):
        # Full screen (F11) reports a normal window covering the monitor.
        keep = previous['rect'] if previous and list(previous['rect']) != list(monitor) else list(default_rect(monitor))
        return dict(rect=keep, maximized=True)
    return dict(rect=restore, maximized=False)


# ---------------------------------------------------------------- Win32 side

class _RECT(ctypes.Structure):
    _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long), ('right', ctypes.c_long), ('bottom', ctypes.c_long)]


class _PLACEMENT(ctypes.Structure):
    _fields_ = [('length', wintypes.UINT), ('flags', wintypes.UINT), ('showCmd', wintypes.UINT),
                ('ptMinPosition', wintypes.POINT), ('ptMaxPosition', wintypes.POINT), ('rcNormalPosition', _RECT)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', _RECT), ('rcWork', _RECT), ('dwFlags', wintypes.DWORD)]


def _user32():
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.GetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(_PLACEMENT)]
    user32.SetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(_PLACEMENT)]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HMONITOR
    user32.MonitorFromRect.argtypes = [ctypes.POINTER(_RECT), wintypes.DWORD]
    user32.MonitorFromRect.restype = wintypes.HMONITOR
    user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    user32.MonitorFromPoint.restype = wintypes.HMONITOR
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MONITORINFO)]
    user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, ctypes.c_void_p, wintypes.LPARAM]
    return user32


def _tuple(rect):
    return (rect.left, rect.top, rect.right, rect.bottom)


def _monitor_info(user32, handle):
    info = _MONITORINFO(); info.cbSize = ctypes.sizeof(info)
    return info if handle and user32.GetMonitorInfoW(handle, ctypes.byref(info)) else None


def _work_areas(user32):
    areas = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(_RECT), wintypes.LPARAM)
    def visit(handle, *_):
        info = _monitor_info(user32, handle)
        if info: areas.append(_tuple(info.rcWork))
        return True
    user32.EnumDisplayMonitors(None, None, ctypes.cast(callback_type(visit), ctypes.c_void_p), 0)
    callback_type = None
    return areas


def _primary_work(user32):
    info = _monitor_info(user32, user32.MonitorFromPoint(wintypes.POINT(0, 0), 1))
    return _tuple(info.rcWork) if info else (0, 0, 1280, 720)


def _offset(info):
    """Placement rectangles use workspace coordinates: screen minus the work-area offset."""
    return (info.rcWork.left - info.rcMonitor.left, info.rcWork.top - info.rcMonitor.top) if info else (0, 0)


def _apply(user32, window, state):
    placement = _PLACEMENT(); placement.length = ctypes.sizeof(placement)
    user32.GetWindowPlacement(window, ctypes.byref(placement))
    left, top, right, bottom = state['rect']
    dx, dy = _offset(_monitor_info(user32, user32.MonitorFromRect(ctypes.byref(_RECT(left, top, right, bottom)), 2)))
    placement.rcNormalPosition = _RECT(left - dx, top - dy, right - dx, bottom - dy)
    placement.showCmd = SW_SHOWMAXIMIZED if state['maximized'] else SW_SHOWNORMAL
    placement.flags = 0
    user32.SetWindowPlacement(window, ctypes.byref(placement))


def _read(user32, window, previous):
    placement = _PLACEMENT(); placement.length = ctypes.sizeof(placement)
    if not user32.GetWindowPlacement(window, ctypes.byref(placement)):
        return None
    rect = _RECT(); user32.GetWindowRect(window, ctypes.byref(rect))
    info = _monitor_info(user32, user32.MonitorFromWindow(window, 2))
    monitor = _tuple(info.rcMonitor) if info else (0, 0, 0, 0)
    normal = _tuple(placement.rcNormalPosition)
    dx, dy = _offset(info)
    normal = (normal[0] + dx, normal[1] + dy, normal[2] + dx, normal[3] + dy)
    return observed(placement.showCmd, placement.flags, normal, _tuple(rect), monitor, previous)


def launch_arguments(path):
    """Edge flags that open the window close to its final place, to avoid a visible jump."""
    if os.name != 'nt':
        return []
    previous = _dpi_aware()
    try:
        user32 = _user32()
        state = choose(load(path), _work_areas(user32), _primary_work(user32))
        scale = _scale(user32, state['rect'])
    except (OSError, AttributeError):
        return []
    finally:
        _dpi_restore(previous)
    left, top, right, bottom = (round(v / scale) for v in state['rect'])
    arguments = [f'--window-position={left},{top}', f'--window-size={right - left},{bottom - top}']
    return arguments + (['--start-maximized'] if state['maximized'] else [])


def _dpi_aware():
    """Physical pixels for this thread only, so native dialogs keep their own scaling."""
    try:
        function = ctypes.windll.user32.SetThreadDpiAwarenessContext
        function.argtypes = [ctypes.c_void_p]; function.restype = ctypes.c_void_p
        return function(ctypes.c_void_p(-4))
    except (OSError, AttributeError):
        return None


def _dpi_restore(previous):
    if previous:
        try: ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(previous))
        except (OSError, AttributeError): pass


def _scale(user32, rect):
    """Edge reads its window flags in device-independent pixels."""
    try:
        x, y = wintypes.UINT(), wintypes.UINT()
        monitor = user32.MonitorFromRect(ctypes.byref(_RECT(*rect)), 2)
        if ctypes.windll.shcore.GetDpiForMonitor(monitor, 0, ctypes.byref(x), ctypes.byref(y)) == 0 and x.value:
            return x.value / 96
    except (OSError, AttributeError):
        pass
    return 1.0


def track(title, path, stopped, interval=0.5):
    """Place each new app window, then remember its placement until `stopped` is set."""
    if os.name != 'nt':
        return None
    def run():
        _dpi_aware()
        user32 = _user32()
        window, state = None, load(path)
        while not stopped.is_set():
            current = user32.FindWindowW('Chrome_WidgetWin_1', title)
            if current and current != window:
                window = current
                start = choose(state, _work_areas(user32), _primary_work(user32))
                _apply(user32, window, start)
                state = start
            elif current:
                seen = _read(user32, window, state)
                if seen and seen != state:
                    state = seen
                    try: save(path, state)
                    except OSError: pass
            else:
                window = None
            stopped.wait(interval)
    thread = threading.Thread(target=run, name='window-placement', daemon=True)
    thread.start()
    return thread
