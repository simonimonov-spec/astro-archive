"""Desktop controls for the managed same-computer prototype."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import secrets
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from .catalog_documents import CatalogView, identifier, saved, store
from .local_sharing import CREDENTIAL, FITS, LocalClient, LocalHost, RequestError, discover
from .validation import atomic
from .windows import private_directory

OPERATIONS = {"pending": "Поискан достъп", "approved": "Разрешен достъп",
              "blocked": "Блокиран", "download": "Изтеглен файл",
              "edit": "Започната редакция", "save": "Записан файл"}
STATES = {"pending": "Чака одобрение", "approved": "Свързан", "blocked": "Блокиран",
          "offline": "Хостът е недостъпен", "": "Избери хост"}


def field(kind, ident, **properties):
    return dict(type=kind, id=ident, **properties)


def archive_checkbox(ident):
    return 'local-export-' + hashlib.sha256(ident.encode()).hexdigest()[:24]


def preview_image(path):
    from PIL import Image

    if path.suffix.lower() in FITS:
        import numpy as np
        from astropy.io import fits

        with fits.open(path, memmap=False) as images:
            hdu = next(hdu for hdu in images if hdu.header.get("NAXIS", 0) >= 2)
            height, width = hdu.header["NAXIS2"], hdu.header["NAXIS1"]
            stride = max(1, (max(height, width) + 479) // 480)
            shape = (0,) * (hdu.header["NAXIS"] - 2) + (slice(None, None, stride),) * 2
            image = np.asarray(hdu.section[shape], dtype=np.float32)
        finite = np.isfinite(image)
        if not finite.any():
            raise ValueError("Кадърът няма стойности за визуализация.")
        low, high = np.percentile(image[finite], (1, 99))
        image[~finite] = low
        image = np.clip((image - low) / max(high - low, 1e-10), 0, 1)
        picture = Image.fromarray(np.flipud((image * 255).astype(np.uint8)))
    else:
        with Image.open(path) as picture:
            picture.thumbnail((480, 480))
            picture = picture.convert("RGB")
    buffer = io.BytesIO()
    picture.save(buffer, "PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


class LocalPanel:
    def __init__(self, context, folder, *, registry=None, smb_active=lambda: False):
        self.ctx = context
        self.smb_active = smb_active
        self.folder = Path(folder).resolve()
        self.registry = (Path(registry) if registry is not None else
                         Path(os.environ["LOCALAPPDATA"]) / "AstroArchive" / "SharedArchivesPeers")
        self.private_registry = registry is None
        self.identity = hashlib.sha256(os.fsencode(str(self.folder).casefold())).hexdigest()[:32]
        self.trust_folder = self.folder / "LocalTrust"
        self.trust_path = self.trust_folder / "client.json"
        self.credential, self.preferred_peer = self.load_identity()
        self.name = "Astro Archive " + self.identity[:8]
        self.lock = threading.RLock()
        self.publish_lock = threading.Lock()
        self.catalog_sync = threading.Lock()
        self.mode = "receive"
        self.host = self.client = None
        self.peers = []
        self.connection = ""
        self.archives = []
        self.selected = ""
        self.files = {}
        self.exports = []
        self.cached = None
        self.preview = ""
        self.lease = ""
        self.lease_problem = ""
        self.catalog_folder = self.folder / "SharedCatalogs" / "Client"
        self.documents = saved(self.catalog_folder)
        self.available_catalogs = set()
        self.catalog_views = {}
        self.last_document = None
        self.closed = False
        self.busy = False

        for name in ("mode", "start", "stop", "join", "refresh", "approve", "block", "unblock",
                     "archive", "download", "begin", "save", "release"):
            command = "local-" + name
            context.command(command, lambda p, command=command: self.action(command, p))
        context.command("local-source", self.source)
        context.command("local-choose-source", self.choose_source)
        context.publish("archive-source", [field("button", "local-choose-source", label="Споделен архив…",
                                                 command="local-choose-source")])
        self.publish()

    @staticmethod
    def choose_source(parameters):
        archive = parameters.get("context", {}).get("archiveId")
        if archive not in ("frames", "stacks", "photos"):
            raise ValueError("Избери архив за кадри, стакове или снимки.")
        return {"uiActions": [dict(type="choose-archive-source", archiveId=archive)]}

    def load_identity(self):
        try:
            state = json.loads(self.trust_path.read_text(encoding="utf-8"))
            if state["installation"] == self.identity and CREDENTIAL.fullmatch(state["credential"]):
                return state["credential"], state.get("host") or ""
        except (OSError, ValueError, KeyError, TypeError):
            pass
        if self.private_registry:
            private_directory(self.trust_folder)
        else:
            self.trust_folder.mkdir(parents=True, exist_ok=True)
        credential = secrets.token_hex(32)
        atomic(self.trust_path, dict(installation=self.identity, credential=credential, host=""))
        return credential, ""

    def tick(self):
        if not self.lock.acquire(blocking=False):
            return
        host = catalog_client = None
        notify_availability = False
        try:
            if self.closed:
                return
            host = self.host
            previous = self.connection
            if self.mode == "receive":
                self.peers = discover(self.registry, self.identity)
                if self.client is None:
                    peer = next((row for row in self.peers if row["identity"] == self.preferred_peer), None)
                    if peer:
                        self.client = LocalClient(peer, self.credential, self.name)
                if self.client:
                    try:
                        current = next((peer for peer in self.peers
                                        if peer["identity"] == self.client.peer["identity"]), None)
                        if current is None:
                            raise RequestError(503, "Хостът е недостъпен.")
                        if current["proof"] != self.client.peer["proof"]:
                            self.available_catalogs.clear()
                            self.connection = ""
                        self.client.peer = current
                        if self.update_client():
                            catalog_client = self.client
                    except RequestError:
                        self.connection = "offline"
                        self.available_catalogs.clear()
                notify_availability = previous == "approved" and self.connection != previous
        finally:
            self.lock.release()
        if catalog_client:
            self.sync_catalogs(catalog_client)
        if host:
            host.refresh_catalogs()
            changed = host.take_catalog_changes()
            with self.lock:
                if self.host is not host:
                    changed = []
            if changed:
                self.note_changes(changed)
        if notify_availability:
            self.note_changes([])
        self.publish()

    def update_client(self):
        previous = self.connection
        joined = self.client.call("join", timeout=1)
        self.connection = joined["status"]
        if self.connection == "approved":
            if previous != "approved":
                self.archives = self.client.call("archives")
            if joined.get("catalog") != 2:
                self.available_catalogs.clear()
            if self.lease and self.matches_cached():
                try:
                    self.client.call("renew", timeout=1, archive=self.cached["archive"], lease=self.lease)
                except RequestError as error:
                    if error.status != 409:
                        raise
                    self.lease_problem = "Заключването изтече. Работното копие е запазено."
                else:
                    self.lease_problem = ""
        else:
            self.available_catalogs.clear()
            self.archives = []
            self.selected = ""
            self.files = {}
            if self.connection == "blocked":
                host = self.client.peer["identity"]
                for key, document in list(self.documents.items()):
                    if document["host"] == host:
                        self.documents.pop(key)
                        self.catalog_views.pop(key, None)
                        (self.catalog_folder / (key + ".json")).unlink(missing_ok=True)
        return self.connection == "approved" and joined.get("catalog") == 2

    def note_changes(self, names):
        self.ctx.archives("source-changed", dict(message=""))
        if names:
            clock = datetime.now().strftime("%H:%M:%S")
            message = f"Споделените архиви са обновени в {clock} · " + ", ".join(names)[:320]
            self.ctx.notify(message)

    def sync_catalogs(self, client):
        if not self.catalog_sync.acquire(blocking=False):
            return
        try:
            self.sync_catalog_documents(client)
        finally:
            self.catalog_sync.release()

    def sync_catalog_documents(self, client):
        with self.lock:
            if self.closed or self.client is not client or self.connection != "approved":
                return
            peer = client.peer
            archives, previous_documents = self.archives, self.documents.copy()

        host = peer["identity"]
        changed = {}
        available = set()
        for row in archives:
            archive = row["id"]
            physical = row["source"]
            key = identifier(host, archive, physical)
            previous = previous_documents.get(key)
            try:
                document = client.read_catalog(archive, previous["revision"] if previous else "", source=physical)
            except RequestError:
                continue
            available.add(key)
            if document is not None:
                document.update(host=host, archive=archive, name=row["name"],
                                hostName=peer["name"], kind=row["kind"], source=physical)
                changed[key] = document

        with self.lock:
            if (self.closed or self.client is not client or self.connection != "approved"
                    or self.client.peer["proof"] != peer["proof"]):
                return
            names = []
            for key, document in changed.items():
                try:
                    store(self.catalog_folder, key, document)
                except OSError:
                    available.discard(key)
                    continue
                if key in self.documents:
                    names.append(document["name"])
                self.documents[key] = document
                self.catalog_views.pop(key, None)
            availability_changed = self.available_catalogs != available
            self.available_catalogs = available
        if names or availability_changed:
            self.note_changes(names)

    def source(self, parameters):
        action = parameters.get("action")
        key = parameters.get("id")
        with self.lock:
            if action == "choices":
                return [dict(id=ident, name=document["name"], host=document.get("hostName", ""))
                        for ident, document in self.documents.items() if ident in self.available_catalogs
                        and document.get("kind") == parameters.get("kind")]
            document = self.documents.get(key)
            if document is None:
                raise ValueError("Няма запазен каталог за този споделен архив.")
            view = self.catalog_views.get(key)
            if view is None:
                view = self.catalog_views[key] = CatalogView(document, key)
            relative = parameters.get("path", "").replace("\\", "/").strip("/")
            if action == "browse":
                return view.browse(relative, parameters.get("search", ""), parameters.get("offset", 0))
            if action == "summary":
                return view.summary(relative)
            if action == "record":
                row = view.files.get(parameters.get("token"))
                if row is None:
                    raise FileNotFoundError("Файлът вече не е в споделения архив.")
                return row
            if action == "records":
                if parameters.get("revision") not in (None, document["revision"]):
                    raise ValueError("Каталогът беше обновен. Опитай отново.")
                offset = int(parameters.get("offset", 0))
                items = view.file_items
                return dict(rows=[dict(row, token=token) for token,row in items[offset:offset+100]],
                            total=len(items), revision=document["revision"])
            if action not in ("ingest", "refresh") and (action != "open" or view.rows.get(relative, {}).get("kind") != "file"):
                raise ValueError("Файлът вече не е в запазения каталог.")
            if key not in self.available_catalogs or self.connection != "approved" or self.client is None:
                raise ValueError("Свържи се с хоста, за да използваш файла.")
            client = self.client
        if action == "ingest":
            result = client.ingest(document["archive"], Path(parameters["file"]), parameters["fingerprint"],
                                   parameters.get("context", {}), document["source"])
            return result
        if action == "refresh":
            client.call("refresh-catalog", archive=document["archive"], source=document["source"], timeout=60)
            with self.catalog_sync:
                self.sync_catalog_documents(client)
            return {}
        cache = self.folder / "ViewerCache"
        cache.mkdir(parents=True, exist_ok=True)
        entry = view.rows[relative]
        folder = identifier(key, relative, str(entry["modified"]), str(entry["bytes"]), str(entry["changed"]))
        filename = Path(relative).name
        target = cache / folder / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file():
            handle = tempfile.NamedTemporaryFile(dir=target.parent, suffix=".part", delete=False)
            temporary = Path(handle.name)
            try:
                with handle:
                    client.download(document["archive"], relative, handle, revision=document["revision"],
                                    compressed=True, source=document["source"])
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        return {"cacheFile": target.relative_to(cache).as_posix()}

    def action(self, command, parameters):
        if command == "local-refresh":
            self.tick()
            return {}
        item = parameters.get("selected", {}).get("id")
        operation = None
        with self.lock:
            if self.closed or self.busy:
                raise ValueError("Изчакай текущата операция.")
            if command == "local-download":
                path = self.files.get(item)
                if self.connection != "approved" or self.lease or path is None:
                    raise ValueError("Избраният файл вече не е достъпен за изтегляне.")
                name = next(row["name"] for row in self.archives if row["id"] == self.selected)
                operation = self.fetch
                arguments = (self.client, self.selected, path, name, None)
                title = "Изтегляне на споделен файл"
            elif command == "local-save":
                if not self.lease or not self.matches_cached():
                    raise ValueError("Свържи се отново с хоста на това работно копие.")
                operation = self.save_copy
                arguments = (self.client, self.cached, self.lease)
                title = "Записване в споделения архив"
            else:
                result = self.dispatch(command, parameters.get("values", {}), item)
            self.busy = operation is not None
        self.publish()
        if operation is None:
            return result or {}

        def work(cancel, progress):
            try:
                return operation(*arguments, cancel, progress)
            finally:
                self.end_operation()

        try:
            return self.ctx.task(title, work, "archive")
        except BaseException:
            self.end_operation()
            raise

    def end_operation(self):
        with self.lock:
            self.busy = False
        self.publish()

    def dispatch(self, command, values, item):
        if command == "local-mode":
            mode = values["local-mode"]
            if mode != self.mode and (self.host or self.lease):
                raise ValueError("Първо приключи редакцията или спри локалния хост.")
            if mode != self.mode:
                self.available_catalogs.clear()
                self.connection = ""
                self.client = None
            self.mode = mode
        elif command == "local-start":
            if self.mode != "host" or self.host or self.smb_active():
                raise ValueError("Избери „Хостване“ и спри другото споделяне, ако е включено.")
            selected = {row["id"]: (row["name"], row["type"] or
                        (row["id"] if row["id"] in ("frames","stacks","photos") else "frames"), Path(row["path"]))
                        for row in self.exports if values.get(archive_checkbox(row["id"])) is True}
            if self.private_registry:
                private_directory(self.registry)
            host = LocalHost(self.registry, self.identity, self.name, selected,
                             catalog_folder=self.folder / "SharedCatalogs" / "Host",
                             access_file=self.trust_folder / "approved.json",
                             metadata=lambda ident, root: self.ctx.archives("source-metadata",
                                 dict(archiveId=ident, root=str(root))),
                             importer=lambda ident, root, path, context: self.ctx.archives("source-ingest",
                                 dict(archiveId=ident, root=str(root), path=str(path), context=context)),
                             incoming=self.folder / "Incoming")
            host.start()
            self.host = host
        elif command == "local-stop" and self.host:
            self.host.close()
            self.host = None
        elif command in ("local-approve", "local-block", "local-unblock") and self.host:
            status = {"local-approve": "approved", "local-block": "blocked",
                      "local-unblock": "pending"}[command]
            self.host.set_access(item, status)
        elif command == "local-join":
            if self.lease:
                raise ValueError("Първо приключи текущата редакция.")
            peer = next((peer for peer in self.peers if peer["identity"] == item), None)
            if peer is None:
                raise ValueError("Хостът вече е недостъпен.")
            self.preferred_peer = peer["identity"]
            atomic(self.trust_path, dict(installation=self.identity, credential=self.credential,
                                         host=self.preferred_peer))
            self.client = LocalClient(peer, self.credential, self.name)
            self.connection, self.selected, self.files = "", "", {}
            self.update_client()
        elif command == "local-archive" and self.connection == "approved" and not self.lease:
            paths = self.client.call("files", archive=item)
            self.selected = item
            key = self.client.peer["identity"] + ":" + self.selected + ":"
            self.files = {hashlib.sha256((key + path).encode()).hexdigest(): path for path in paths}
        elif command == "local-begin" and self.cached and not self.lease:
            if not self.matches_cached():
                raise ValueError("Отвори файла от избрания архив отново.")
            row = self.cached
            self.lease = self.client.call("begin", archive=row["archive"], path=row["relative"],
                                          revision=row["revision"])["lease"]
            row["edited"] = True
            self.lease_problem = ""
            return {"copyText": str(row["path"]), "message": "Пътят до работното копие е копиран."}
        elif command == "local-release" and self.lease:
            try:
                self.client.call("release", archive=self.cached["archive"], lease=self.lease)
            except RequestError as error:
                if error.status not in (403, 409, 503):
                    raise
            self.lease = self.lease_problem = ""
            return {"message": "Работното копие остава запазено."}

    def matches_cached(self):
        return (self.connection == "approved" and self.cached is not None and self.client
                and self.client.peer["identity"] == self.cached["host"]
                and self.selected == self.cached["archive"])

    def save_copy(self, client, row, lease, cancel, progress):
        progress(0, 1, "Изпращане на работното копие")
        with row["path"].open("rb") as data:
            revision = client.upload(row["archive"], lease, data, os.fstat(data.fileno()).st_size)
        with self.lock:
            row["revision"] = revision
            self.lease = self.lease_problem = ""
        return {"message": "Хостът потвърди записа."}

    def fetch(self, client, archive, relative, name, catalog_revision, cancel, progress):
        folder = self.folder / "LocalCache" / client.peer["identity"]
        folder.mkdir(parents=True, exist_ok=True)
        progress(0, 1, "Изтегляне на работно копие")
        with tempfile.NamedTemporaryFile(dir=folder, suffix=Path(relative).suffix, delete=False) as data:
            path = Path(data.name)
            try:
                revision = client.download(archive, relative, data, revision=catalog_revision)
            except BaseException:
                data.close()
                path.unlink(missing_ok=True)
                raise
        try:
            picture = preview_image(path)
        except (OSError, ValueError, StopIteration):
            picture = ""
        with self.lock:
            if self.closed or cancel.is_set():
                path.unlink(missing_ok=True)
                return {}
            previous = self.cached
            self.cached = dict(path=path, relative=relative, archive=archive, revision=revision,
                               host=client.peer["identity"], name=name, edited=False)
            self.preview = picture
        if previous and not previous["edited"]:
            try:
                previous["path"].unlink(missing_ok=True)
            except OSError:
                pass
        self.publish()
        progress(1, 1, "Работното копие е готово")
        return {"message": "Работното копие е изтеглено."}

    def publish(self):
        with self.publish_lock:
            self.publish_latest()

    def publish_latest(self):
        with self.lock:
            if self.closed:
                return
            update_exports = self.mode == "host" and self.host is None
        archives = self.ctx.archives("list")["archives"] if update_exports else []
        with self.lock:
            if self.closed:
                return
            if update_exports:
                self.exports = [row for row in archives if row.get("path")
                                and Path(row["path"]).drive
                                and not row["path"].startswith("\\\\")]
            fields = [field("select", "local-mode", label="Роля на това копие", value=self.mode,
                            options=[dict(value="receive", label="Приемане"), dict(value="host", label="Хостване")],
                            disabled=bool(self.host or self.lease or self.busy), command="local-mode")]
            fields.extend(self.host_fields() if self.mode == "host" else self.receive_fields())
        if fields != self.last_document:
            self.ctx.publish("local-sharing", fields)
            self.last_document = fields

    def host_fields(self):
        if not self.host:
            choices = [field("checkbox", archive_checkbox(row["id"]), label=row["name"], value=False)
                       for row in self.exports]
            return [field("group", "local-exports", label="Избери архиви за пробно споделяне", span=2,
                          children=choices or [field("text", "local-empty",
                                                     value="Това копие няма настроени локални архиви.")]),
                    field("button", "local-start", label="Включи локалния хост",
                          command="local-start", tone="primary", disabled=not choices)]
        status = self.host.status()
        fields = [field("text", "local-state", value="Споделя се на този компютър", tone="success"),
                  field("button", "local-stop", label="Спри локалния хост", command="local-stop"),
                  field("list", "local-shared", label="Споделени архиви",
                        items=[dict(id=ident, label=name)
                               for ident, (name, _, _) in self.host.archives.items()])]
        for access, command, label, action in (
                ("pending", "approve", "Заявки за достъп", "Одобри"),
                ("approved", "block", "Одобрени клиенти", "Блокирай"),
                ("blocked", "unblock", "Блокирани клиенти", "Отблокирай")):
            items = [dict(id=row["id"], label=row["name"]) for row in status["clients"]
                     if row["status"] == access]
            if items:
                fields.append(field("list", "local-" + command, label=label, items=items,
                                    command="local-" + command, actionLabel=action))
        events = [dict(id=index, at=datetime.fromtimestamp(row["time"]).strftime("%H:%M:%S"),
                       client=row["client"], operation=OPERATIONS[row["operation"]], path=row["path"])
                  for index, row in enumerate(reversed(status["activity"]))]
        fields.append(field("table", "local-activity", label="Действия в споделените архиви", span=2,
                            columns=[dict(key=key, label=heading) for key, heading in
                                     (("at", "Час"), ("client", "Клиент"), ("operation", "Действие"),
                                      ("path", "Файл"))], rows=events))
        return fields

    def receive_fields(self):
        peers = [dict(id=peer["identity"], label=peer["name"], status="На този компютър")
                 for peer in self.peers]
        fields = [field("button", "local-refresh", label="Провери за хостове", command="local-refresh"),
                  field("list", "local-peers", label="Открити хостове", items=peers,
                        command="local-join", actionLabel="Свържи", disabled=bool(self.lease or self.busy))]
        if not peers:
            fields.append(field("text", "local-find-help", span=2,
                                value="Включи хостването в другото копие на Astro Archive."))
        if self.client:
            status = STATES[self.connection]
            if self.connection == "pending":
                status += f" · На хоста заявката е от {self.name}."
            fields.append(field("text", "local-connection", value=status))
        if self.connection == "approved":
            fields.append(field("list", "local-archives", label="Споделени архиви",
                                items=[dict(id=row["id"], label=row["name"]) for row in self.archives],
                                command="local-archive", actionLabel="Покажи файловете", disabled=bool(self.busy or self.lease)))
            if self.selected:
                fields.append(field("list", "local-files", label="Файлове",
                                    items=[dict(id=ident, label=path) for ident, path in self.files.items()],
                                    command="local-download", actionLabel="Отвори копие",
                                    disabled=bool(self.lease or self.busy)))
        return fields + self.working_copy_fields()

    def working_copy_fields(self):
        fields = []
        if self.cached:
            fields.append(field("text", "local-file", span=2,
                                value=self.cached["name"] + " · " + self.cached["relative"]))
            if self.preview:
                fields.append(field("image", "local-preview", label="Преглед на работното копие",
                                    span=2, value=self.preview))
            available = self.matches_cached() and not self.busy
            if self.lease_problem:
                fields.append(field("text", "local-lease-problem", span=2,
                                    tone="warning", value=self.lease_problem))
            if self.lease:
                fields.extend([
                    field("button", "local-save", label="Изпрати редакцията", command="local-save",
                          tone="primary", disabled=not available),
                    field("button", "local-release", label="Освободи файла", command="local-release", disabled=self.busy)])
            else:
                fields.append(field("button", "local-begin", label="Заключи за редакция",
                                    command="local-begin", disabled=not available))
        return fields

    def close(self):
        with self.lock:
            self.closed = True
            if self.host:
                self.host.close()
                self.host = None
