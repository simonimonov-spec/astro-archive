"""Small, atomic storage helpers shared by registry, dataset and audit."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
MAX_JSON = 25_000_000
MAX_JSONL = 100_000_000


class StorageError(ValueError):
    """Invalid path, unreadable file or malformed stored record."""


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def component(value, name="id"):
    if not isinstance(value, str) or COMPONENT.fullmatch(value) is None:
        raise StorageError(f"Невалидно {name}: използвай букви, цифри, точка, тире и долно тире.")
    return value


def identifier(value, name="id"):
    if not isinstance(value, str) or IDENTIFIER.fullmatch(value) is None:
        raise StorageError(f"Невалидно {name}: до 120 знака, букви, цифри и . _ : -")
    return value


def canonical(data):
    try:
        return json.dumps(
            data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise StorageError("Данните не са валиден JSON.") from error


def digest_text(text):
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def digest_bytes(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def digest_data(data):
    return digest_text(canonical(data))


def digest_file(path):
    path = Path(path)
    try:
        return digest_bytes(path.read_bytes())
    except OSError as error:
        raise StorageError(f"Файлът не може да бъде прочетен: {path.name}") from error


def atomic_write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except OSError as error:
        raise StorageError(f"Файлът не може да бъде записан: {path.name}") from error
    finally:
        temp.unlink(missing_ok=True)


def atomic_write_json(path, data):
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n")


def read_json(path, default=None, max_bytes=MAX_JSON):
    path = Path(path)
    if not path.exists():
        if default is None:
            raise StorageError(f"Липсва файл: {path.name}")
        return copy.deepcopy(default)
    if path.is_symlink():
        raise StorageError(f"Символична връзка не се приема: {path.name}")
    try:
        if path.stat().st_size > max_bytes:
            raise StorageError(f"Файлът е прекалено голям: {path.name}")
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise StorageError(f"Невалиден JSON файл: {path.name}") from error


def read_jsonl(path, max_bytes=MAX_JSONL):
    path = Path(path)
    if not path.exists():
        return []
    if path.is_symlink():
        raise StorageError(f"Символична връзка не се приема: {path.name}")
    try:
        if path.stat().st_size > max_bytes:
            raise StorageError(f"Файлът е прекалено голям: {path.name}")
        items = []
        with path.open("r", encoding="utf-8-sig") as handle:
            for number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError as error:
                    raise StorageError(f"Невалиден JSON на ред {number}: {path.name}") from error
        return items
    except OSError as error:
        raise StorageError(f"Файлът не може да бъде прочетен: {path.name}") from error


def append_jsonl(path, record):
    line = canonical(record) + "\n"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as error:
        raise StorageError(f"Записът не може да бъде добавен: {path.name}") from error


def write_jsonl(path, records):
    text = "".join(canonical(record) + "\n" for record in records)
    atomic_write_text(path, text)
