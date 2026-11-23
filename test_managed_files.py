"""Behavior of managed archive files using synthetic data."""
import importlib
import io
import tempfile
import threading
import unittest
from pathlib import Path

managed = importlib.import_module("Plugins.shared-archives.managed_files")


class ManagedFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.file = self.root / "frame.fit"
        self.file.write_bytes(b"original")
        self.now = 10
        self.archive = managed.ManagedFiles(self.root, clock=lambda: self.now, lease_seconds=5)

    def read(self, name):
        output = io.BytesIO()
        return self.archive.read(name, output), output.getvalue()

    def test_two_readers_and_one_editor(self):
        revision, data = self.read("frame.fit")
        self.assertEqual(data, b"original")
        lease = self.archive.begin("first", "frame.fit", revision)
        self.assertEqual(self.read("frame.fit"), (revision, b"original"))
        with self.assertRaisesRegex(managed.Conflict, "first"):
            self.archive.begin("second", "FRAME.FIT", revision)
        self.archive.release("first", lease)
        self.archive.begin("second", "frame.fit", revision)

    def test_simultaneous_requests_get_only_one_lease(self):
        revision, _ = self.read("frame.fit")
        barrier = threading.Barrier(2)
        outcomes = {}

        def begin(owner):
            barrier.wait()
            try:
                outcomes[owner] = self.archive.begin(owner, "frame.fit", revision)
            except managed.Conflict:
                outcomes[owner] = None

        workers = [threading.Thread(target=begin, args=(name,)) for name in ("first", "second")]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=3)
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(sum(lease is not None for lease in outcomes.values()), 1)

    def test_expired_or_foreign_lease_cannot_write(self):
        revision, _ = self.read("frame.fit")
        lease = self.archive.begin("first", "frame.fit", revision)
        with self.assertRaises(managed.Conflict):
            self.archive.commit("second", lease, io.BytesIO(b"replacement"))
        self.now += 6
        with self.assertRaises(managed.Conflict):
            self.archive.commit("first", lease, io.BytesIO(b"replacement"))
        self.assertEqual(self.file.read_bytes(), b"original")

    def test_stale_version_after_direct_host_edit(self):
        revision, _ = self.read("frame.fit")
        lease = self.archive.begin("first", "frame.fit", revision)
        self.file.write_bytes(b"host data")
        with self.assertRaises(managed.Conflict):
            self.archive.commit("first", lease, io.BytesIO(b"replacement"))
        self.assertEqual(self.file.read_bytes(), b"host data")

    def test_retry_returns_the_committed_version_and_releases_lease(self):
        revision, _ = self.read("frame.fit")
        lease = self.archive.begin("first", "frame.fit", revision)
        updated = self.archive.commit("first", lease, io.BytesIO(b"replacement"))
        self.assertEqual(self.read("frame.fit"), (updated, b"replacement"))
        self.assertEqual(self.archive.commit("first", lease, io.BytesIO(b"replacement")), updated)
        with self.assertRaises(managed.Conflict):
            self.archive.commit("first", lease, io.BytesIO(b"something else"))
        self.archive.begin("second", "frame.fit", updated)

    def test_short_source_keeps_original(self):
        revision, _ = self.read("frame.fit")
        lease = self.archive.begin("first", "frame.fit", revision)
        with self.assertRaises(OSError):
            self.archive.commit("first", lease, io.BytesIO(b"cut"), expected_size=9)
        self.assertEqual(self.file.read_bytes(), b"original")
        self.archive.commit("first", lease, io.BytesIO(b"complete!"), expected_size=9)

    def test_concurrent_read_has_no_partial_result_and_a_retry_can_commit(self):
        revision, _ = self.read("frame.fit")
        lease = self.archive.begin("first", "frame.fit", revision)
        entered = threading.Event()
        release = threading.Event()
        received = io.BytesIO()

        class WaitingOutput:
            def write(self, block):
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("Reader was not released")
                return received.write(block)

        reader = threading.Thread(target=self.archive.read, args=("frame.fit", WaitingOutput()))
        reader.start()
        try:
            self.assertTrue(entered.wait(3))
            with self.assertRaises(managed.Conflict):
                self.archive.commit("first", lease, io.BytesIO(b"new"))
        finally:
            release.set()
            reader.join(3)
        self.assertEqual(received.getvalue(), b"original")
        self.archive.commit("first", lease, io.BytesIO(b"new"))
        self.assertEqual(self.file.read_bytes(), b"new")

    def test_rejects_paths_outside_archive_and_internal_files(self):
        for name in ("../frame.fit", "C:/frame.fit", "frame.fit:extra", ".config.fit", "frame.fit."):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.read(name)
