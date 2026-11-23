"""Adapter from the normal archive screen to the Shared Archives provider."""
from contextvars import ContextVar
import datetime as dt
import hashlib
import re
from pathlib import Path

from Backend.Archive.structure import schema
from Backend.Fits.images import FITS
from Backend.Foundation.runtime import within

KINDS = ("frames", "stacks", "photos")
PLUGIN = "shared-archives"
CAPABILITY = "archives.sharing.source"
REMOTE_CONTEXT = ContextVar("shared_archives_remote_context", default=False)


class SharedSources:
    def __init__(self, service):
        self.service = service

    def get(self, archive):
        row = next((a for a in self.service.settings.value["archives"] if a["id"] == archive), {})
        return row.get("sharedSource")

    def request(self, action, **parameters):
        if REMOTE_CONTEXT.get():
            raise PermissionError("Споделеният източник се използва от одобреното настолно копие.")
        return self.service.plugins.invoke_capability(CAPABILITY, dict(action=action, **parameters), PLUGIN)

    def choices(self, archive):
        if archive not in KINDS:
            raise ValueError("Избери един от трите основни архива.")
        if not any(c["id"] == CAPABILITY and c["pluginId"] == PLUGIN
                   for c in self.service.plugins.capabilities()):
            return []
        return self.request("choices", kind=archive)

    def select(self, archive, ident):
        result = next((source for source in self.choices(archive) if source["id"] == ident), None)
        if result is None:
            raise ValueError("Свържи се към споделен архив от съответния тип.")
        return result

    def bind(self, archive, ident):
        source = self.select(archive, ident)
        rows = [dict(row, path="", sharedSource=source) if row["id"] == archive else row
                for row in self.service.settings.value["archives"]]
        self.service.save_settings({"archives": rows})
        return {"archives": self.service.bootstrap()["archives"]}

    def listing(self, archive, p):
        source = self.get(archive)
        relative = p.get("relativePath", "")
        layout = schema(self.service.settings, archive)
        page = self.request("browse", id=source["id"], path=relative, search=p.get("search", ""),
                            offset=p.get("offset", 0))
        entries = []
        levels = layout["levels"]
        for row in page["rows"]:
            path, folder = row["path"], row["kind"] == "folder"
            name = path.rpartition("/")[2]
            kind = "folder" if folder else "fits" if Path(name).suffix.lower() in FITS else "image"
            modified = row.get("modified")
            date = dt.datetime.fromtimestamp(modified / 1e9, dt.timezone.utc).isoformat() if modified else None
            depth = path.count("/")
            meta = row.get("meta", {})
            entries.append(dict(archiveId=archive, relativePath=path, name=name, kind=kind,
                                id=None if folder else self.file_id(archive, source["id"], path),
                                bytes=row.get("bytes"), modified=date, isLink=False,
                                icon=levels[depth]["icon"] if folder and depth < len(levels) else None,
                                info={key: str(value) for key,value in meta.items() if value is not None},
                                details=dict(meta, kind=meta.get("kind",kind))))
        return dict(archiveId=archive, relativePath=page["path"], fullPath=" / ".join(
                    part for part in (source["name"], page["path"]) if part),
                    entries=entries, total=page["total"], offset=page["offset"], pageSize=100,
                    directFiles=page["files"], directFolders=page["folders"], shared=True,
                    columns=self.service.catalog.columns(archive, relative, files=bool(entries)
                                                        and all(e["kind"] != "folder" for e in entries)),
                    structure=layout, problem=None)

    def summary(self, archive, p):
        source = self.get(archive)
        path = p.get("relativePath", "")
        result = self.request("summary", id=source["id"], path=path)
        return dict(result, archiveId=archive, relativePath=path, kind=archive, name=source["name"])

    @staticmethod
    def file_id(archive, source, relative):
        token = hashlib.sha256((source+"\x00"+relative).encode("utf-8")).hexdigest()[:32]
        return f"shared-{archive}-{token}"

    def materialize(self, archive, relative):
        source = self.get(archive)
        result = self.request("open", id=source["id"], path=relative)
        cache = self.service.plugins.settings_path(PLUGIN).parent / "ViewerCache"
        return within(cache, result["cacheFile"])

    def open(self, archive, relative):
        path = self.materialize(archive, relative)
        return self.service.viewer.open(path, self.file_id(archive, self.get(archive)["id"], relative),
                                        can_navigate=False, display_name=Path(relative).name)

    def import_file(self, archive, path, fingerprint, context, source):
        if source != self.get(archive):
            raise ValueError("Споделеният източник е сменен. Повтори внасянето.")
        result = self.request("ingest", id=source["id"], file=str(path.resolve()),
                              fingerprint=fingerprint, context=context)
        return [self.file_id(archive, source["id"], relative) for relative in result["paths"]], result["duplicate"]

    def refresh(self, archive):
        source = self.get(archive)
        self.request("refresh", id=source["id"])

    def to_record(self, archive, source, row, decisions):
        metadata = dict(kind={"frames":"frame","stacks":"stack","photos":"photo"}[archive],
                        telescope="Неизвестен телескоп", object="Неизвестен обект", night="", filter="",
                        width=1, height=1, observed="", frames=1, framesKnown=False,
                        exposure=None, totalExposure=None)
        metadata.update({key:value for key,value in row.get("meta", {}).items() if value is not None})
        relative = row["path"]
        ident = self.file_id(archive, source["id"], relative)
        return dict(id=ident, archive=archive, relative=relative, name=Path(relative).name, size=row["bytes"],
                    mtime=row["modified"], added=row.get("added", row["modified"]/1e9), fingerprint=None,
                    deleted=0, shared=True, decision=decisions.get(ident), meta=metadata)

    def decisions(self, archive):
        with self.service.store.db() as db:
            rows = db.execute("SELECT file,decision FROM decisions WHERE file LIKE ?", (f"shared-{archive}-%",))
            return {row["file"]:row["decision"] for row in rows}

    def record(self, ident):
        _, archive, token = ident.split("-", 2)
        source = self.get(archive)
        if not source or not re.fullmatch(r"[0-9a-f]{32}", token):
            raise FileNotFoundError("Файлът вече не е в този архив.")
        row = self.request("record", id=source["id"], token=token)
        return self.to_record(archive, source, row, self.decisions(archive))

    def records(self, archive, filters):
        if REMOTE_CONTEXT.get():
            return []
        source = self.get(archive)
        offset, revision, rows = 0, None, []
        try:
            while True:
                page = self.request("records", id=source["id"], offset=offset, revision=revision)
                revision = page["revision"]
                rows.extend(page["rows"])
                offset += len(page["rows"])
                if offset >= page["total"] or not page["rows"]:
                    break
        except (OSError, ValueError):
            return []
        prefix = filters.get("relative","").replace(chr(92),"/").rstrip("/")
        decisions = self.decisions(archive)
        result = []
        for row in rows:
            record = self.to_record(archive, source, row, decisions)
            if any(filters.get(key) and record["meta"].get(key) != filters[key]
                   for key in ("telescope","object","night","kind")):
                continue
            if prefix and record["relative"] != prefix and not record["relative"].startswith(prefix+"/"):
                continue
            if filters.get("since") and record["added"] < float(filters["since"]):
                continue
            result.append(record)
        return result

    def validate(self, row, previous):
        source = row.get("sharedSource")
        if source is None:
            row.pop("sharedSource", None)
            return
        if (row["id"] not in KINDS or row["type"] != row["id"] or row.get("path")
                or not isinstance(source, dict) or not isinstance(source.get("id"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", source["id"])):
            raise ValueError("Невалиден споделен източник.")
        if not previous or source != previous.get("sharedSource"):
            row["sharedSource"] = self.select(row["id"], source["id"])
