"""Apply a verified Windows release after the launcher exits."""
import ctypes
from ctypes import wintypes
import hashlib
import json
import ntpath
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import zipfile


LAUNCHER = 'AstroArchive.exe'
REQUIRED = {LAUNCHER, 'start.pyw', 'requirements-lock.txt', 'Tools/runtime_probe.py'}
ROOT_FILES = REQUIRED | {'README.md', 'LICENSE.txt', 'requirements.txt', 'Tools/apply_update.py'}
PROGRAM_DIRS = {'Backend', 'Bridge', 'UI', 'docs'}
PROTECTED = {'cache', '__pycache__', 'plugins', '.runtime', '.venv'}
MAX_UNPACKED = 1_000_000_000


def program_path(name):
    parts = name.split('/')
    if any(not part or part in {'.', '..'} or '\\' in part or ntpath.isreserved(part)
           or part.casefold() in PROTECTED for part in parts):
        raise ValueError('Невалиден програмен път в обновяването: ' + name)
    if name not in ROOT_FILES and (len(parts) == 1 or parts[0] not in PROGRAM_DIRS):
        raise ValueError('Неочакван файл в обновяването: ' + name)
    return Path(*parts)


def destination(root, relative):
    target = root / relative
    for path in (target, *target.parents):
        if path == root:
            break
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError('Обновяването не може да записва през връзка: ' + str(path))
    return target


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def prepare(root, stage, package, expected):
    incoming, backup = stage / 'incoming', stage / 'backup'
    for tree in (incoming, backup):
        if tree.exists():
            shutil.rmtree(tree)
        tree.mkdir()
    names, seen, changes = set(), set(), []
    with package.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
            raise ValueError('Контролната сума на изтегления пакет не съвпада.')
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            entries = archive.infolist()
            folders = {entry.filename.partition('/')[0] for entry in entries}
            if (len(folders) != 1 or not next(iter(folders)).startswith('AstroArchive_')
                    or sum(entry.file_size for entry in entries) > MAX_UNPACKED):
                raise ValueError('Неочаквана структура на Windows пакета.')
            for entry in entries:
                name = entry.filename.partition('/')[2]
                if entry.is_dir():
                    continue
                relative = program_path(name)
                if name.casefold() in seen or stat.S_ISLNK(entry.external_attr >> 16):
                    raise ValueError('Повторен файл или връзка в пакета: ' + name)
                names.add(name)
                seen.add(name.casefold())
                target = destination(root, relative)
                staged = incoming / relative
                staged.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, staged.open('xb') as output:
                    shutil.copyfileobj(source, output)
                existed = target.exists()
                if existed:
                    if digest(staged) == digest(target):
                        continue
                    saved = backup / relative
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, saved)
                changes.append((name, existed))
    if not REQUIRED <= names:
        raise ValueError('В Windows пакета липсват необходими програмни файлове.')
    # Replace the launcher last so either launcher can recover an interrupted install.
    return sorted(changes, key=lambda item: item[0] == LAUNCHER)


def restore(root, stage, pending):
    for name, existed in reversed(json.loads(pending.read_text(encoding='utf-8'))):
        relative = program_path(name)
        target = destination(root, relative)
        if existed:
            saved = stage / 'backup' / relative
            if target.is_file() and digest(target) == digest(saved):
                continue
            temporary = stage / 'restore.tmp'
            shutil.copy2(saved, temporary)
            os.replace(temporary, target)
        else:
            target.unlink(missing_ok=True)
    pending.unlink()


def apply(root, stage, package=None, expected=None):
    pending = stage / 'pending.json'
    if pending.exists():
        restore(root, stage, pending)
    if package is None:
        return
    changes = prepare(root, stage, package, expected)
    temporary = stage / 'pending.tmp'
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(changes, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, pending)
    try:
        for name, _ in changes:
            relative = program_path(name)
            target = destination(root, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(stage / 'incoming' / relative, target)
        pending.unlink()
    except Exception:
        restore(root, stage, pending)
        raise


def run(parent, package=None, expected=None):
    stage = Path(__file__).resolve().parent
    root = stage.parent.parent
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    parent_handle = kernel.OpenProcess(0x00100000, False, parent)  # SYNCHRONIZE
    if parent_handle:
        try:
            if kernel.WaitForSingleObject(parent_handle, 30000) != 0:
                raise RuntimeError('Стартерът все още работи.')
        finally:
            kernel.CloseHandle(parent_handle)
    identity = hashlib.sha256(str(root).casefold().encode('utf-8')).hexdigest()
    launcher = kernel.CreateMutexW(None, False, 'Local\\AstroArchive-Launcher-' + identity)
    if not launcher:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if kernel.WaitForSingleObject(launcher, 30000) not in (0, 0x80):
            raise RuntimeError('Друга проверка за обновяване все още работи.')
        try:
            desktop = kernel.CreateMutexW(None, False, 'Local\\AstroArchive-' + identity[:32])
            already_running = ctypes.get_last_error() == 183
            if not desktop:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                if already_running:
                    raise RuntimeError('Затвори работещото копие и опитай отново.')
                apply(root, stage, package, expected)
                shutil.rmtree(stage, ignore_errors=True)
            finally:
                kernel.CloseHandle(desktop)
        finally:
            kernel.ReleaseMutex(launcher)
    finally:
        kernel.CloseHandle(launcher)
    args = [str(root / LAUNCHER)]
    if package is None:
        args.append('--skip-update-once')
    subprocess.Popen(args, cwd=root)


if __name__ == '__main__':
    parent = int(sys.argv[1])
    try:
        if sys.argv[2:] == ['--recover']:
            run(parent)
        else:
            run(parent, Path(sys.argv[2]), sys.argv[3])
    except Exception as error:
        ctypes.windll.user32.MessageBoxW(0, str(error), 'Astro Archive | Обновяването е спряно', 0x10)
        sys.exit(1)
