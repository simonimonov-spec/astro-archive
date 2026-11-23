"""Two independent clients and real loopback connections."""
import importlib
import io
import secrets
import tempfile
import threading
import time
import unittest
from pathlib import Path

local = importlib.import_module("Plugins.shared-archives.local_sharing")


class LocalSharingTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        source = root / "archive"
        source.mkdir()
        self.frame = source / "test.fit"
        self.frame.write_bytes(b"first version")
        (source / "settings.json").write_text("{}", encoding="utf-8")
        self.registry = root / "registry"
        self.host = local.LocalHost(self.registry, "host-copy", "Архиви на хоста",
                                   {"frames": ("Кадри", "frames", source)})
        self.host.start()
        self.addCleanup(self.host.close)

    def client(self, identity="empty-copy", name="Празно копие"):
        peers = local.discover(self.registry, identity)
        self.assertEqual(len(peers), 1)
        return local.LocalClient(peers[0], secrets.token_hex(32), name)

    def test_discovery_approval_file_listing_and_download(self):
        self.assertEqual(local.discover(self.registry, "host-copy"), [])
        client = self.client()
        self.assertEqual(client.call("join")["status"], "pending")
        with self.assertRaises(local.RequestError):
            client.call("archives")
        self.host.set_access(client.identity, "approved")
        self.assertEqual(client.call("archives"), [{"id": "frames", "name": "Кадри", "kind": "frames"}])
        self.assertEqual(client.call("files", archive="frames"), ["test.fit"])
        output = io.BytesIO()
        revision = client.download("frames", "test.fit", output)
        self.assertEqual(output.getvalue(), b"first version")
        self.assertEqual(len(revision), 64)
        deadline = time.monotonic() + 2
        while self.host.status()["activity"][-1]["operation"] != "download" and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.host.status()["activity"][-1]["operation"], "download")

    def test_two_clients_edit_conflict_and_block(self):
        a, b = self.client(), self.client("other-copy", "Друго копие")
        for client in (a, b):
            client.call("join")
            self.host.set_access(client.identity, "approved")
        revision = a.download("frames", "test.fit", io.BytesIO())
        lease = a.call("begin", archive="frames", path="test.fit", revision=revision)["lease"]
        with self.assertRaises(local.RequestError) as conflict:
            b.call("begin", archive="frames", path="test.fit", revision=revision)
        self.assertEqual(conflict.exception.status, 409)
        data = b"replacement"
        newer = a.upload("frames", lease, io.BytesIO(data), len(data))
        self.assertEqual(self.frame.read_bytes(), data)
        self.assertEqual(newer, a.upload("frames", lease, io.BytesIO(data), len(data)))
        self.host.set_access(a.identity, "blocked")
        self.assertEqual(a.call("join")["status"], "blocked")
        with self.assertRaises(local.RequestError):
            a.call("archives")
        self.assertEqual(b.call("files", archive="frames"), ["test.fit"])

    def test_stopped_host_rejects_previously_approved_write(self):
        client = self.client()
        client.call("join")
        self.host.set_access(client.identity, "approved")
        revision = client.download("frames", "test.fit", io.BytesIO())
        lease = client.call("begin", archive="frames", path="test.fit", revision=revision)["lease"]
        self.host.close()
        with self.assertRaises(local.RequestError):
            self.host.upload(client.identity, "frames", lease, io.BytesIO(b"change"), 6)
        self.assertEqual(self.frame.read_bytes(), b"first version")

    def test_stop_revokes_upload_that_has_not_committed(self):
        client = self.client()
        client.call("join")
        self.host.set_access(client.identity, "approved")
        revision = client.download("frames", "test.fit", io.BytesIO())
        lease = client.call("begin", archive="frames", path="test.fit", revision=revision)["lease"]
        copying = threading.Event()
        unpause = threading.Event()
        stopped = threading.Event()
        errors = []

        class WaitingSource(io.BytesIO):
            def read(self, size):
                copying.set()
                if not unpause.wait(3):
                    raise TimeoutError("Test did not resume the source.")
                return super().read(size)

        def save():
            try:
                self.host.upload(client.identity, "frames", lease, WaitingSource(b"change"), 6)
            except ValueError as error:
                errors.append(error)

        def stop():
            self.host.close()
            stopped.set()

        uploader = threading.Thread(target=save)
        uploader.start()
        stopper = threading.Thread(target=stop)
        try:
            self.assertTrue(copying.wait(2))
            stopper.start()
            self.assertTrue(stopped.wait(1))
        finally:
            unpause.set()
            uploader.join(3)
            if stopper.ident is not None:
                stopper.join(3)
        self.assertEqual(len(errors), 1)
        self.assertEqual(self.frame.read_bytes(), b"first version")

    def test_host_stop_leaves_no_discovery_entry(self):
        self.host.close()
        self.assertEqual(local.discover(self.registry, "empty-copy"), [])
