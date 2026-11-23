"""Managed sharing between copies in the same Windows user profile."""
from __future__ import annotations

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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .managed_files import Conflict, ManagedFiles, transfer
from .validation import atomic, label

FITS = {".fit", ".fits", ".fts"}
PHOTOS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
MAX_CONTROL = 256 * 1024
MAX_TRANSFER = 8 * 1024 ** 3
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
    def __init__(self, folder, identity, name, archives):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", identity):
            raise ValueError("Невалиден идентификатор на инсталацията.")
        if not archives:
            raise ValueError("Избери поне един архив.")
        self.folder = Path(folder)
        self.registration = self.folder / (identity + ".json")
        self.identity = identity
        self.name = label(name)
        self.archives = {}
        roots = []
        for ident, (archive_name, kind, root) in archives.items():
            files = ManagedFiles(root)
            if any(files.root.is_relative_to(other) or other.is_relative_to(files.root) for other in roots):
                raise ValueError("Избраните архиви имат обща папка.")
            roots.append(files.root)
            self.archives[ident] = (archive_name, kind, files)
        self.proof = secrets.token_hex(32)
        self.server = None
        self.thread = None
        self.lock = threading.RLock()
        self.stopping = True
        self.clients = {}
        self.activity = deque(maxlen=100)

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

    def set_access(self, ident, status):
        with self.lock:
            transitions = {"pending": "approved", "approved": "blocked", "blocked": "pending"}
            if (self.stopping or ident not in self.clients
                    or transitions[self.clients[ident]["status"]] != status):
                raise ValueError("Състоянието на клиента е променено.")
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
        allowed = PHOTOS | FITS if kind == "photos" else FITS
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
                return dict(status=self.clients[identity]["status"])
            client = self.client(identity)
            if action == "archives":
                return [dict(id=ident, name=name, kind=kind)
                        for ident, (name, kind, _) in self.archives.items()]
            ident = data["archive"]
            _, kind, archive = self.archive(ident)
            if action == "begin":
                path = data["path"]
                self.file_type(kind, path)
                lease = archive.begin(identity, path, data["revision"], editor=client["name"])
                self.record(identity, "edit", ident, path)
                return dict(lease=lease)
            if action in ("renew", "release"):
                getattr(archive, action)(identity, data["lease"])
                return {}
        if action == "files":
            return self.files(kind, archive)
        raise RequestError(404, "Непозната операция.")

    def download(self, identity, ident, path, destination):
        with self.lock:
            self.client(identity)
            _, kind, archive = self.archive(ident)
            self.file_type(kind, path)
            if archive.path(path).stat().st_size > MAX_TRANSFER:
                raise RequestError(413, "Файлът надхвърля ограничението за прехвърляне.")
        revision = archive.read(path, destination)
        with self.lock:
            self.client(identity)
        return revision

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
            if not 0 <= length <= (MAX_TRANSFER if action == "upload" else MAX_CONTROL):
                raise RequestError(413, "Невалиден размер на заявката.")
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
                with tempfile.TemporaryFile() as data:
                    archive, path = payload["archive"], payload["path"]
                    revision = host.download(identity, archive, path, data)
                    size = data.tell()
                    data.seek(0)
                    self.reply(200, size, "application/octet-stream", revision)
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

    def reply(self, status, size, content_type, revision=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        if revision:
            self.send_header("X-Archive-Revision", revision)
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

    def download(self, archive, path, destination):
        body = json.dumps(dict(archive=archive, path=path)).encode("ascii")

        def consume(response):
            length = int(response.getheader("Content-Length", "-1"))
            if not 0 <= length <= MAX_TRANSFER:
                raise RequestError(413, "Невалиден размер на файла.")
            revision = transfer(response, destination, expected_size=length)
            if not secrets.compare_digest(revision, response.getheader("X-Archive-Revision", "")):
                raise RequestError(503, "Полученият файл е непълен или променен.")
            return revision

        return self.request("download", body, consume, timeout=60)

    def upload(self, archive, lease, source, length):
        headers = {"Content-Length": str(length), "X-Archive-Id": archive, "X-Edit-Token": lease}
        result = self.request("upload", source, lambda response: json.load(response),
                              timeout=60, headers=headers)
        return result["revision"]
