"""Metadata-only snapshots for the managed archive browser."""
from __future__ import annotations

import hashlib
import json
import math
import stat
from pathlib import Path

from .validation import atomic

PAGE_SIZE = 100


def identifier(*parts):
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def snapshot(archive, file_type, metadata=None):
    metadata = metadata or {}
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
                if name.startswith(".") or len(relative)>1024:
                    continue
                info = (root / name).lstat()
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                    continue
            except (FileNotFoundError, ValueError):
                continue
            row = dict(path=relative, kind="file", bytes=info.st_size,
                       modified=info.st_mtime_ns, changed=info.st_ctime_ns)
            indexed = metadata.get(relative)
            if indexed and indexed.get("mtime") == info.st_mtime_ns:
                row.update(meta=indexed["fields"], added=indexed["added"])
            rows.append(row)
    payload = json.dumps(rows, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return dict(format=1, revision=hashlib.sha256(payload).hexdigest(), rows=rows)


class CatalogView:
    def __init__(self, document, key):
        self.rows = {row["path"]: row for row in document["rows"]}
        self.files = {identifier(key, path)[:32]: row for path, row in self.rows.items() if row["kind"] == "file"}
        self.file_items = list(self.files.items())
        self.children = {}
        for path, row in self.rows.items():
            self.children.setdefault(path.rpartition("/")[0], []).append(row)
        for rows in self.children.values():
            rows.sort(key=lambda row: (row["kind"] != "folder", row["path"].casefold()))

    def summary(self, path):
        prefix = path + "/" if path else ""
        rows = [row.get("meta", {}) for row in self.files.values() if row["path"].startswith(prefix)]
        exposures = [row["totalExposure"] for row in rows if isinstance(row.get("totalExposure"), (int,float))
                     and math.isfinite(row["totalExposure"])]
        frames = [row["frames"] for row in rows if type(row.get("frames")) is int and row.get("framesKnown", True)]
        return dict(files=len(rows), frames=sum(frames) if frames else None,
                    exposureSeconds=sum(exposures) if exposures else None,
                    unknownExposure=len(rows)-len(exposures), minimumFrames=len(frames)<len(rows),
                    fromIndex=True, building=False)

    def browse(self, path, search, offset):
        if path and self.rows.get(path, {}).get("kind") != "folder":
            raise ValueError("Папката вече не е в запазения каталог.")
        children = self.children.get(path, [])
        matches = [row for row in children if search.casefold() in row["path"].rpartition("/")[2].casefold()]
        offset = min(max(0, int(offset)), max(0, (len(matches)-1) // PAGE_SIZE) * PAGE_SIZE)
        folders = sum(row["kind"] == "folder" for row in children)
        return dict(path=path, rows=matches[offset:offset+PAGE_SIZE], total=len(matches),
                    offset=offset, folders=folders, files=len(children)-folders)


def store(folder, identity, document):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    atomic(folder / (identity + ".json"), document)


def saved(folder):
    result = {}
    for path in Path(folder).glob("*.json"):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            if (doc["format"] != 1 or path.stem != identifier(doc["host"], doc["archive"], doc["source"])
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
