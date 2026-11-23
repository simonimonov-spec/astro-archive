"""Exclusive edits and versioned writes for a physical archive."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import stat
import tempfile
import threading
import time
from collections import Counter, OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


class Conflict(ValueError):
    """A write no longer has exclusive access to the requested version."""


@dataclass
class Lease:
    owner: str
    editor: str
    relative: str
    key: Path
    revision: str
    expires: float


def transfer(source: BinaryIO, destination: BinaryIO | None = None, *, expected_size=None) -> str:
    digest = hashlib.sha256()
    received = 0
    while True:
        block = source.read(1024 * 1024)
        if block is None:
            raise OSError("Прехвърлянето е прекъснато.")
        if not block:
            break
        received += len(block)
        if expected_size is not None and received > expected_size:
            raise OSError("Размерът на прехвърления файл не съвпада.")
        if destination is not None and destination.write(block) != len(block):
            raise OSError("Файлът не е записан докрай.")
        digest.update(block)
    if expected_size is not None and received != expected_size:
        raise OSError("Файлът не е получен докрай.")
    return digest.hexdigest()


def replace_file(temporary: Path, target: Path) -> None:
    if os.name != "nt":
        os.replace(temporary, target)
        return

    import ctypes
    from ctypes import wintypes

    replace = ctypes.WinDLL("kernel32", use_last_error=True).ReplaceFileW
    replace.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                        wintypes.DWORD, wintypes.LPVOID, wintypes.LPVOID]
    replace.restype = wintypes.BOOL
    backup = temporary.with_suffix(".backup")
    if not replace(str(target), str(temporary), str(backup), 0, None, None):
        error = ctypes.WinError(ctypes.get_last_error())
        if backup.exists() and not target.exists():
            os.replace(backup, target)
        raise error
    try:
        backup.unlink()
    except OSError:
        # The write succeeded. A locked backup can be recovered or removed later.
        pass


class ManagedFiles:
    def __init__(self, root: Path, *, clock=time.monotonic, lease_seconds=120):
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Споделеният архив трябва да е папка.")
        self.clock = clock
        self.lease_seconds = lease_seconds
        self.lock = threading.RLock()
        self.leases: dict[str, Lease] = {}
        self.readers: Counter[Path] = Counter()
        self.completed: OrderedDict[str, tuple[str, str]] = OrderedDict()

    def path(self, relative: str) -> Path:
        if not isinstance(relative, str) or len(relative) > 1024:
            raise ValueError("Невалиден път в споделения архив.")
        parts = relative.split("/")
        path = self.root
        for part in parts:
            if (not part or part.startswith(".") or part.endswith((".", " "))
                    or re.search(r'[\x00-\x1f<>:"\\|?*]', part) or os.path.isreserved(part)):
                raise ValueError("Невалиден път в споделения архив.")
            path /= part
            info = path.lstat()
            if (stat.S_ISLNK(info.st_mode) or
                    getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                raise ValueError("Връзки към други файлове или папки са недостъпни.")
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Очаква се обикновен файл от споделения архив.")
        return path.resolve(strict=True)

    def read(self, relative: str, destination: BinaryIO, *, expected_size=None) -> str:
        with self.lock:
            path = self.path(relative)
            stream = path.open("rb")
            self.readers[path] += 1
        try:
            with stream:
                return transfer(stream, destination, expected_size=expected_size)
        finally:
            with self.lock:
                self.readers[path] -= 1
                if not self.readers[path]:
                    del self.readers[path]

    def revision(self, path: Path) -> str:
        with path.open("rb") as source:
            return transfer(source)

    def begin(self, owner: str, relative: str, revision: str, *, editor: str | None = None) -> str:
        with self.lock:
            path = self.path(relative)
            now = self.clock()
            self.leases = {
                token: lease for token, lease in self.leases.items() if now < lease.expires
            }
            for lease in self.leases.values():
                if lease.key == path:
                    raise Conflict(f"Редактира се от {lease.editor}.")
            if self.revision(path) != revision:
                raise Conflict("Файлът е променен. Отвори актуалната версия.")
            token = secrets.token_urlsafe(24)
            self.leases[token] = Lease(owner, editor or owner, relative, path, revision, self.clock() + self.lease_seconds)
            return token

    def active(self, owner: str, token: str, now=None) -> Lease:
        lease = self.leases.get(token)
        if now is None:
            now = self.clock()
        if lease is None or lease.owner != owner or now >= lease.expires:
            raise Conflict("Заключването за редакция е изтекло или е освободено.")
        return lease

    def renew(self, owner: str, token: str) -> None:
        with self.lock:
            now = self.clock()
            self.active(owner, token, now).expires = now + self.lease_seconds

    def release(self, owner: str, token: str) -> None:
        with self.lock:
            self.active(owner, token)
            del self.leases[token]

    def revoke(self, owner: str | None = None) -> None:
        with self.lock:
            if owner is None:
                self.leases.clear()
            else:
                self.leases = {token: lease for token, lease in self.leases.items() if lease.owner != owner}

    def write_path(self, owner: str, token: str) -> str | None:
        with self.lock:
            if token in self.completed:
                return None
            return self.active(owner, token).relative

    def edit_target(self, lease: Lease) -> Path:
        try:
            path = self.path(lease.relative)
        except FileNotFoundError:
            raise Conflict("Файлът е преместен или изтрит.") from None
        if path != lease.key:
            raise Conflict("Местоположението на файла е променено.")
        return path

    def commit(self, owner: str, token: str, source: BinaryIO, *, expected_size=None) -> str:
        with self.lock:
            previous = self.completed.get(token)
            if previous is None:
                lease = self.active(owner, token)
                path = self.edit_target(lease)
                handle = tempfile.NamedTemporaryFile(dir=path.parent, prefix=".astroarchive-", delete=False)
        if previous is not None:
            expected_owner, revision = previous
            if owner != expected_owner or transfer(source, expected_size=expected_size) != revision:
                raise Conflict("Този запис вече е завършен с различни данни.")
            return revision

        temporary = Path(handle.name)
        try:
            with handle:
                revision = transfer(source, handle, expected_size=expected_size)
                handle.flush()
                os.fsync(handle.fileno())
            with self.lock:
                previous = self.completed.get(token)
                if previous is not None:
                    if previous == (owner, revision):
                        return revision
                    raise Conflict("Този запис вече е завършен с различни данни.")
                self.active(owner, token)
                if self.readers[path]:
                    raise Conflict("Файлът се чете. Опитай да запишеш отново.")
                if self.revision(self.edit_target(lease)) != lease.revision:
                    raise Conflict("Файлът е променен. Работното копие е запазено отделно.")
                self.active(owner, token)
                replace_file(temporary, path)
                del self.leases[token]
                self.completed[token] = (owner, revision)
                if len(self.completed) > 256:
                    self.completed.popitem(last=False)
                return revision
        finally:
            temporary.unlink(missing_ok=True)
