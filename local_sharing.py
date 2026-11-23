"""Managed sharing between copies in the same Windows user profile."""
from __future__ import annotations

import base64
import gzip
import hashlib
import http.client
import json
import os
import re
import secrets
import tempfile
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .catalog_documents import PAGE_SIZE, identifier, snapshot, store
from .managed_files import Conflict, ManagedFiles, transfer
from .validation import atomic, child_name, label

FITS = {".fit", ".fits", ".fts"}
PHOTOS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
MAX_CONTROL = 256 * 1024
MAX_TRANSFER = 8 * 1024 ** 3
MAX_WIRE_TRANSFER = MAX_TRANSFER + MAX_TRANSFER // 500 + 65536
CREDENTIAL = re.compile(r"[a-f0-9]{64}")
LOOPBACK = "127.0.0.1"


class RequestError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def discover(folder, own_identity):
    peers = []
    for path in Path(folder).glob("*.json"):
        try:
            if path.stat().st_size > 4096:
                continue
            peer = json.loads(path.read_text(encoding="utf-8"))
            if (peer["identity"] == own_identity or type(peer["port"]) is not int
                    or not 1 <= peer["port"] <= 65535 or not CREDENTIAL.fullmatch(peer["proof"])
                    or path.stem != peer["identity"] or not label(peer["name"])):
                continue
            probe = LocalClient(peer, "", "")
            if probe.call("ping", timeout=0.3)["identity"] == peer["identity"]:
                peers.append(peer)
        except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
            continue
    return sorted(peers, key=lambda peer: (peer["name"].casefold(), peer["identity"]))


class LocalHost:
    def __init__(self, folder, identity, name, archives, *, catalog_folder=None, access_file=None,
                 metadata=None, importer=None, incoming=None):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", identity):
            raise ValueError("Невалиден идентификатор на инсталацията.")
        if not archives:
            raise ValueError("Избери поне един архив.")
        self.folder = Path(folder)
        self.registration = self.folder / (identity + ".json")
        self.identity = identity
        self.name = label(name)
        self.metadata = metadata
        self.importer = importer
        self.incoming = Path(incoming) if incoming else self.folder / 'Incoming'
        self.archives = {}
        self.archive_sources = {}
        roots = []
        for ident, (archive_name, kind, root) in archives.items():
            files = ManagedFiles(root)
            if any(files.root.is_relative_to(other) or other.is_relative_to(files.root) for other in roots):
                raise ValueError("Избраните архиви имат обща папка.")
            roots.append(files.root)
            self.archives[ident] = (archive_name, kind, files)
            stat = files.root.stat()
            self.archive_sources[ident] = identifier(self.identity, ident, str(files.root).casefold(),
                                                      str(stat.st_dev), str(stat.st_ino))
        self.proof = secrets.token_hex(32)
        self.server = None
        self.thread = None
        self.lock = threading.RLock()
        self.stopping = True
        self.access_file = Path(access_file) if access_file else self.folder / "Access" / (identity + ".json")
        self.clients = self.saved_access()
        self.activity = deque(maxlen=100)
        self.catalog_folder = catalog_folder or self.folder / "Catalogs" / identity
        self.catalogs = {}
        self.catalog_unavailable = set()
        self.catalog_changes = set()
        self.catalog_epoch = secrets.token_hex(16)
        self.catalog_lock = threading.RLock()
        self.scan_lock = threading.Lock()

    def start(self):
        if self.server is not None:
            raise ValueError("Хостът вече е включен.")
        self.folder.mkdir(parents=True, exist_ok=True)
        server = ThreadingHTTPServer((LOOPBACK, 0), LocalHandler)
        self.server = server
        server.host = self
        try:
            atomic(self.registration, dict(identity=self.identity, name=self.name,
                                          port=server.server_port, proof=self.proof))
        except OSError:
            server.server_close()
            self.server = None
            raise
        self.thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1},
                                       daemon=True, name="SharedArchivesLocalHost")
        self.stopping = False
        self.thread.start()

    def close(self):
        with self.lock:
            if self.server is None or self.stopping:
                return
            self.stopping = True
            for _, _, files in self.archives.values():
                files.revoke()
            server, thread = self.server, self.thread
        try:
            self.registration.unlink(missing_ok=True)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            self.server = None

    def status(self):
        with self.lock:
            return dict(clients=[dict(id=ident, **data) for ident, data in self.clients.items()],
                        activity=list(self.activity))

    def saved_access(self):
        try:
            saved = json.loads(self.access_file.read_text(encoding="utf-8"))
            return {ident: dict(name=label(data["name"]), status=data["status"], lastSeen=0)
                    for ident, data in saved.items()
                    if CREDENTIAL.fullmatch(ident) and data["status"] in ("approved", "blocked")}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return {}

    def set_access(self, ident, status):
        with self.lock:
            transitions = {"pending": "approved", "approved": "blocked", "blocked": "pending"}
            if (self.stopping or ident not in self.clients
                    or transitions[self.clients[ident]["status"]] != status):
                raise ValueError("Състоянието на клиента е променено.")
            saved = {key: dict(name=row["name"], status=status if key == ident else row["status"])
                     for key, row in self.clients.items()}
            saved = {key: row for key, row in saved.items() if row["status"] != "pending"}
            self.access_file.parent.mkdir(parents=True, exist_ok=True)
            atomic(self.access_file, saved)
            self.clients[ident]["status"] = status
            if status != "approved":
                for _, _, files in self.archives.values():
                    files.revoke(ident)
            self.record(ident, status)

    def client(self, identity):
        if self.stopping:
            raise RequestError(503, "Локалният хост е спрян.")
        data = self.clients.get(identity)
        if not data or data["status"] != "approved":
            raise RequestError(403, "Хостът трябва да одобри достъпа.")
        data["lastSeen"] = time.time()
        return data

    def record(self, identity, operation, archive="", path=""):
        self.activity.append(dict(time=time.time(), client=self.clients[identity]["name"],
                                  operation=operation, archive=archive, path=path))

    def archive(self, ident):
        if ident not in self.archives:
            raise RequestError(404, "Архивът вече не е споделен.")
        return self.archives[ident]

    @staticmethod
    def file_type(kind, path):
        allowed = PHOTOS | FITS
        if not isinstance(path, str) or Path(path).suffix.casefold() not in allowed:
            raise ValueError("Файлът не е от поддържан вид.")

    def files(self, kind, archive):
        result = []
        for root, folders, names in archive.root.walk():
            folders[:] = sorted(name for name in folders if not name.startswith(".")
                                and not (root / name).is_junction() and not (root / name).is_symlink())
            for name in sorted(names):
                path = (root / name).relative_to(archive.root).as_posix()
                if len(path) > 1024:
                    continue
                try:
                    self.file_type(kind, path)
                    archive.path(path)
                except (OSError, ValueError):
                    continue
                result.append(path)
                if len(result) >= 1000:
                    return result
        return result

    def refresh_catalogs(self, archive_id=None):
        targets = [ident for ident in self.archives if archive_id is None or ident == archive_id]
        with self.scan_lock:
            if len(targets) <= 1:
                results = [self.scan_catalog(ident) for ident in targets]
            else:
                workers = min(4, len(targets), os.cpu_count() or 1)
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    results = list(pool.map(self.scan_catalog, targets))
        return [name for name in results if name]

    def scan_catalog(self, ident):
        name, kind, archive = self.archives[ident]
        try:
            indexed = self.metadata(ident, archive.root) if self.metadata else {}
            document = snapshot(archive, lambda path: self.file_type(kind, path), indexed)
            document["revision"] = identifier(self.catalog_epoch, name, document["revision"])
            with self.catalog_lock:
                previous = self.catalogs.get(ident)
            if not previous or previous["revision"] != document["revision"]:
                physical = self.archive_sources[ident]
                document.update(archive=ident, name=name, host=self.identity, source=physical)
                store(self.catalog_folder, identifier(self.identity, ident, physical), document)
                with self.catalog_lock:
                    self.catalogs[ident] = document
                    if previous:
                        self.catalog_changes.add(name)
                    self.catalog_unavailable.discard(ident)
                return name if previous else None
            with self.catalog_lock:
                self.catalog_unavailable.discard(ident)
        except (OSError, ValueError):
            with self.catalog_lock:
                self.catalog_unavailable.add(ident)
        return None

    def take_catalog_changes(self):
        with self.catalog_lock:
            changes = sorted(self.catalog_changes)
            self.catalog_changes.clear()
        return changes

    def catalog(self, ident, data):
        with self.catalog_lock:
            needs_scan = ident not in self.catalogs
        if needs_scan:
            self.refresh_catalogs(ident)
        with self.catalog_lock:
            document = self.catalogs.get(ident)
            if document is None or ident in self.catalog_unavailable:
                raise RequestError(503, "Каталогът на архива е временно недостъпен.")
            revision = document["revision"]
            offset = data.get("offset", 0)
            if type(offset) is not int or offset < 0:
                raise ValueError("Невалидна страница на каталога.")
            if offset and data.get("revision") != revision:
                raise RequestError(409, "Каталогът беше обновен. Опитай отново.")
            if not offset and data.get("known") == revision:
                return dict(revision=revision, rows=None, next=None)
            rows = document["rows"][offset:offset+PAGE_SIZE]
            remaining = offset + len(rows) < len(document["rows"])
            return dict(revision=revision, rows=rows, next=offset+len(rows) if remaining else None)

    def check_source(self, ident, source):
        if source is not None and self.archive_sources.get(ident) != source:
            raise RequestError(409, "Споделеният архив сочи към друга папка.")

    def dispatch(self, identity, action, data):
        with self.lock:
            if self.stopping:
                raise RequestError(503, "Локалният хост е спрян.")
            if action == "ping":
                return dict(identity=self.identity)
            if action == "join":
                if identity not in self.clients:
                    if len(self.clients) >= 64:
                        raise RequestError(429, "Списъкът за достъп е пълен.")
                    self.clients[identity] = dict(name=label(data["name"]), status="pending",
                                                  lastSeen=time.time())
                    self.record(identity, "pending")
                self.clients[identity]["lastSeen"] = time.time()
                return dict(status=self.clients[identity]["status"], catalog=2)
            client = self.client(identity)
            if action == "archives":
                return [dict(id=ident, name=name, kind=kind, source=self.archive_sources[ident])
                        for ident, (name, kind, _) in self.archives.items()]
            ident = data["archive"]
            _, kind, archive = self.archive(ident)
            self.check_source(ident, data.get("source"))
            if action == "begin":
                path = data["path"]
                self.file_type(kind, path)
                lease = archive.begin(identity, path, data["revision"], editor=client["name"])
                self.record(identity, "edit", ident, path)
                return dict(lease=lease)
            if action in ("renew", "release"):
                getattr(archive, action)(identity, data["lease"])
                return {}
        if action == "refresh-catalog":
            self.refresh_catalogs(ident)
            with self.lock:
                self.client(identity)
            return {}
        if action == "catalog":
            result = self.catalog(ident, data)
            with self.lock:
                self.client(identity)
            return result
        if action == "files":
            return self.files(kind, archive)
        raise RequestError(404, "Непозната операция.")

    def download(self, identity, ident, path, destination, *, revision=None, source=None):
        with self.lock:
            self.client(identity)
            _, kind, archive = self.archive(ident)
            self.check_source(ident, source)
            if revision is not None:
                with self.catalog_lock:
                    if (ident in self.catalog_unavailable
                            or self.catalogs.get(ident, {}).get("revision") != revision):
                        raise RequestError(409, "Каталогът е променен. Избери файла отново.")
            self.file_type(kind, path)
            size = archive.path(path).stat().st_size
            if size > MAX_TRANSFER:
                raise RequestError(413, "Файлът надхвърля ограничението за прехвърляне.")
        revision = archive.read(path, destination, expected_size=size)
        with self.lock:
            self.client(identity)
        return revision, size

    def ingest(self, identity, ident, path, context, source):
        with self.lock:
            self.client(identity)
            _, kind, archive = self.archive(ident)
            self.check_source(ident, source)
            self.file_type(kind, path.name)
            if self.importer is None:
                raise RequestError(503, "Хостът не поддържа внасяне в архива.")
            result = self.importer(ident, archive.root, path, context)
            if result["paths"]:
                self.record(identity, "save", ident, result["paths"][0])
        return result

    def upload(self, identity, ident, lease, source, length):
        with self.lock:
            self.client(identity)
            _, _, archive = self.archive(ident)
            path = archive.write_path(identity, lease)
        revision = archive.commit(identity, lease, source, expected_size=length)
        if path is not None:
            with self.lock:
                self.record(identity, "save", ident, path)
        return revision


class LocalHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def setup(self):
        self.request.settimeout(5)
        super().setup()

    def do_POST(self):
        try:
            host = self.server.host
            if (self.headers.get("Host") != f"{LOOPBACK}:{self.server.server_port}"
                    or self.headers.get("Origin") or self.headers.get("Transfer-Encoding")
                    or not secrets.compare_digest(self.headers.get("Authorization", ""), host.proof)):
                raise RequestError(403, "Невалидна локална връзка.")
            action = self.path.removeprefix("/")
            credential = self.headers.get("X-Archive-Client", "")
            if action != "ping" and not CREDENTIAL.fullmatch(credential):
                raise RequestError(403, "Невалидна самоличност.")
            identity = hashlib.sha256(credential.encode("ascii")).hexdigest()
            length = int(self.headers.get("Content-Length", "-1"))
            if not 0 <= length <= (MAX_TRANSFER if action in ("upload", "ingest") else MAX_CONTROL):
                raise RequestError(413, "Невалиден размер на заявката.")
            if action == "ingest":
                ident = self.headers["X-Archive-Id"]
                with host.lock:
                    host.client(identity)
                    host.archive(ident)
                filename = child_name(base64.b64decode(self.headers["X-Archive-Name"], validate=True).decode("utf-8"))
                context = json.loads(base64.b64decode(self.headers["X-Archive-Context"], validate=True))
                if not isinstance(context, dict):
                    raise ValueError("Невалидни метаданни за внасяне.")
                allowed = {"object", "telescope", "night", "sourceNights", "kind", "sourceStackCount", "telescopes"}
                context = {key: value for key, value in context.items() if key in allowed}
                host.incoming.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(dir=host.incoming) as folder:
                    path = Path(folder) / filename
                    with path.open("xb") as output:
                        digest = hashlib.sha256()
                        remaining = length
                        while remaining:
                            block = self.rfile.read(min(remaining, 1024 * 1024))
                            if not block:
                                raise RequestError(400, "Качването е прекъснато.")
                            output.write(block)
                            digest.update(block)
                            remaining -= len(block)
                    if not secrets.compare_digest(digest.hexdigest(), self.headers.get("X-Archive-Digest", "")):
                        raise RequestError(400, "Каченият файл не съвпада с оригинала.")
                    self.json(200, host.ingest(identity, ident, path, context, self.headers["X-Archive-Source"]))
                return
            if action == "upload":
                with host.lock:
                    host.client(identity)
                    host.archive(self.headers["X-Archive-Id"])
                with tempfile.TemporaryFile() as source:
                    remaining = length
                    while remaining:
                        block = self.rfile.read(min(remaining, 1024 * 1024))
                        if not block:
                            raise RequestError(400, "Качването е прекъснато.")
                        source.write(block)
                        remaining -= len(block)
                    source.seek(0)
                    revision = host.upload(identity, self.headers["X-Archive-Id"],
                                           self.headers["X-Edit-Token"], source, length)
                self.json(200, dict(revision=revision))
                return

            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Очаква се обект.")
            if action == "download":
                with tempfile.SpooledTemporaryFile(max_size=4*1024**2) as data:
                    archive, path = payload["archive"], payload["path"]
                    compressed = payload.get("compression") == "gzip" and Path(path).suffix.casefold() in FITS
                    if compressed:
                        with gzip.GzipFile(fileobj=data, mode="wb", compresslevel=1, mtime=0) as encoded:
                            revision, original_size = host.download(identity, archive, path, encoded,
                                                                   revision=payload.get("revision"), source=payload.get("source"))
                    else:
                        revision, original_size = host.download(identity, archive, path, data,
                                                               revision=payload.get("revision"), source=payload.get("source"))
                    size = data.tell()
                    data.seek(0)
                    self.reply(200, size, "application/octet-stream", revision,
                               uncompressed=original_size if compressed else None)
                    try:
                        while block := data.read(1024 * 1024):
                            with host.lock:
                                host.client(identity)
                            self.wfile.write(block)
                    except (RequestError, OSError):
                        self.close_connection = True
                        return
                    with host.lock:
                        host.record(identity, "download", archive, path)
                return
            self.json(200, host.dispatch(identity, action, payload))
        except RequestError as error:
            self.json(error.status, {"error": str(error)})
        except Conflict as error:
            self.json(409, {"error": str(error)})
        except FileNotFoundError:
            self.json(404, {"error": "Файлът вече не е наличен."})
        except (KeyError, TypeError, ValueError):
            self.json(400, {"error": "Невалидна заявка към споделения архив."})
        except (ConnectionError, TimeoutError):
            return
        except OSError:
            self.json(503, {"error": "Архивният файл е временно недостъпен."})

    def reply(self, status, size, content_type, revision=None, *, uncompressed=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        if revision:
            self.send_header("X-Archive-Revision", revision)
        if uncompressed is not None:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("X-Archive-Size", str(uncompressed))
        self.end_headers()

    def json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=True).encode("ascii")
        self.reply(status, len(body), "application/json")
        self.wfile.write(body)


class LocalClient:
    def __init__(self, peer, credential, name):
        self.peer = peer
        self.credential = credential
        self.identity = hashlib.sha256(credential.encode("ascii")).hexdigest()
        self.name = name

    def request(self, action, body, consumed, *, timeout=8, headers=None):
        connection = http.client.HTTPConnection(LOOPBACK, self.peer["port"], timeout=timeout)
        fields = {"Authorization": self.peer["proof"], "X-Archive-Client": self.credential}
        try:
            connection.request("POST", "/" + action, body, fields | (headers or {}))
            with connection.getresponse() as response:
                if response.status != 200:
                    message = json.loads(response.read(MAX_CONTROL)).get("error")
                    raise RequestError(response.status, message or "Действието е отказано.")
                return consumed(response)
        except (OSError, http.client.HTTPException, json.JSONDecodeError) as error:
            raise RequestError(503, "Локалният хост не отговаря.") from error
        finally:
            connection.close()

    def call(self, action, *, timeout=8, **data):
        if action == "join":
            data["name"] = self.name
        body = json.dumps(data).encode("ascii")
        return self.request(action, body, lambda response: json.load(response), timeout=timeout)

    def read_catalog(self, archive, known, *, source=None):
        page = self.call("catalog", archive=archive, known=known, source=source)
        if page["rows"] is None:
            return None
        revision = page["revision"]
        rows = page["rows"]
        while page["next"] is not None:
            offset = page["next"]
            if type(offset) is not int or offset != len(rows):
                raise RequestError(503, "Невалиден отговор за каталога.")
            page = self.call("catalog", archive=archive, offset=offset, revision=revision, source=source)
            if page["revision"] != revision or not page["rows"]:
                raise RequestError(409, "Каталогът беше обновен. Опитай отново.")
            rows.extend(page["rows"])
        return dict(format=1, revision=revision, rows=rows)

    def download(self, archive, path, destination, *, revision=None, compressed=False, source=None):
        body = json.dumps(dict(archive=archive, path=path, revision=revision, source=source,
                               compression="gzip" if compressed else None)).encode("ascii")

        def consume(response):
            length = int(response.getheader("Content-Length", "-1"))
            if response.getheader("Content-Encoding") == "gzip":
                original_size = int(response.getheader("X-Archive-Size", "-1"))
                if not 0 <= length <= MAX_WIRE_TRANSFER or not 0 <= original_size <= MAX_TRANSFER:
                    raise RequestError(413, "Невалиден размер на компресирания файл.")
                with gzip.GzipFile(fileobj=response, mode="rb") as decoded:
                    revision = transfer(decoded, destination, expected_size=original_size)
            else:
                if not 0 <= length <= MAX_TRANSFER:
                    raise RequestError(413, "Невалиден размер на файла.")
                revision = transfer(response, destination, expected_size=length)
            if not secrets.compare_digest(revision, response.getheader("X-Archive-Revision", "")):
                raise RequestError(503, "Полученият файл е непълен или променен.")
            return revision

        return self.request("download", body, consume, timeout=60)

    def ingest(self, archive, source, fingerprint, context, physical):
        allowed = {"object", "telescope", "night", "sourceNights", "kind", "sourceStackCount", "telescopes"}
        context = {key: value for key, value in context.items() if key in allowed}
        def encode(value):
            return base64.b64encode(value.encode("utf-8")).decode("ascii")
        with source.open("rb") as stream:
            headers = {"Content-Length": str(os.fstat(stream.fileno()).st_size),
                       "X-Archive-Id": archive, "X-Archive-Name": encode(source.name), "X-Archive-Source": physical,
                       "X-Archive-Context": encode(json.dumps(context)), "X-Archive-Digest": fingerprint}
            return self.request("ingest", stream, json.load, timeout=120, headers=headers)

    def upload(self, archive, lease, source, length):
        headers = {"Content-Length": str(length), "X-Archive-Id": archive, "X-Edit-Token": lease}
        result = self.request("upload", source, lambda response: json.load(response),
                              timeout=60, headers=headers)
        return result["revision"]
