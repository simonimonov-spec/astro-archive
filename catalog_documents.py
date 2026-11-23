"""Metadata-only snapshots for the managed archive browser."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .validation import atomic

PAGE_SIZE = 100


def identifier(*parts):
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def snapshot(archive, file_type):
    rows = []

    def on_error(error):
        raise error

    for root, folders, names in archive.root.walk(on_error=on_error):
        folders[:] = sorted(name for name in folders if not name.startswith(".")
                            and not (root / name).is_junction() and not (root / name).is_symlink())
        for name in folders:
            relative = (root / name).relative_to(archive.root).as_posix()
            if len(relative) <= 1024:
                rows.append(dict(path=relative, kind="folder"))
        for name in sorted(names):
            relative = (root / name).relative_to(archive.root).as_posix()
            try:
                file_type(relative)
                info = archive.path(relative).stat()
            except (FileNotFoundError, ValueError):
                continue
            rows.append(dict(path=relative, kind="file", bytes=info.st_size,
                             modified=info.st_mtime_ns, changed=info.st_ctime_ns))
    payload = json.dumps(rows, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return dict(format=1, revision=hashlib.sha256(payload).hexdigest(), rows=rows)


def store(folder, identity, document):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    atomic(folder / (identity + ".json"), document)


def saved(folder):
    result = {}
    for path in Path(folder).glob("*.json"):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            if (doc["format"] != 1 or path.stem != identifier(doc["host"], doc["archive"])
                    or not isinstance(doc["name"], str) or not isinstance(doc["revision"], str)
                    or not isinstance(doc["rows"], list)):
                continue
            if any(not isinstance(row["path"], str) or row["kind"] not in ("folder", "file")
                   or (row["kind"] == "file" and type(row["bytes"]) is not int)
                   for row in doc["rows"]):
                continue
            result[path.stem] = doc
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return result
