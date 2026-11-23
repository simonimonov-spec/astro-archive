"""Declarative host/client flow with two isolated plugin profiles."""
import importlib
import tempfile
import threading
import uuid
import unittest
from pathlib import Path

import numpy as np
from astropy.io import fits
from Bridge.Plugins.contracts import ui_document

panel = importlib.import_module("Plugins.shared-archives.local_panel")


class Context:
    def __init__(self, folder, archives):
        self.folder = folder
        self.entries = archives
        self.commands = {}
        self.elements = []
        self.documents = {}

    def command(self, name, call):
        self.commands[name] = call

    def archives(self, action, parameters=None):
        if action == "source-changed":
            self.refreshes = getattr(self, "refreshes", 0) + 1
            return {}
        return {"archives": self.entries}

    def notify(self, message):
        self.events = getattr(self, "events", []) + [message]

    def publish(self, section, elements):
        self.elements = self.documents[section] = ui_document(elements)

    def task(self, title, work, section):
        import threading
        return work(threading.Event(), lambda *_args: None)


class LocalPanelTests(unittest.TestCase):
    def test_plugin_manager_routes_real_list_selections(self):
        import os
        import shutil
        from unittest.mock import patch
        from Backend.Foundation.runtime import Store
        from Bridge.Core.service import Service

        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"LOCALAPPDATA": temporary}):
            root = Path(temporary)
            source = root / "data"
            source.mkdir()
            fits.writeto(source / "image.fit", np.arange(16, dtype=np.int16).reshape(4, 4))

            def copy(label, archive=None):
                folder = root / label
                shutil.copytree(Path(__file__).parent, folder / "Plugins" / "shared-archives",
                                ignore=shutil.ignore_patterns("__pycache__"))
                service = Service(store=Store(folder / "cache"), plugin_folder=folder / "Plugins")
                self.addCleanup(service.jobs.pool.shutdown, cancel_futures=True)
                self.addCleanup(service.plugins.unload, "shared-archives")
                if archive:
                    service.settings.value["archives"][0]["path"] = str(archive)
                    service.settings.save()
                manager = service.plugins
                manager.scan()
                manager.toggle("shared-archives", True, confirmed=True)
                manager.allow_ui("shared-archives", manager.records["shared-archives"]["uiRevision"], True)
                live = manager.live["shared-archives"]
                return manager, live["stop"].__self__.local

            host_manager, host = copy("host", source)
            client_manager, client = copy("empty")

            def ui(manager, element, values=None, item=None, contribution="local-sharing"):
                return manager.input("shared-archives", contribution, element, values, item_id=item)

            try:
                for archive_id in ("frames", "stacks", "photos"):
                    context = dict(anchor="archive.location", archiveId=archive_id)
                    result = client_manager.input("shared-archives", "archive-source", "local-choose-source", context=context)
                    self.assertEqual(result["uiActions"], [dict(type="choose-archive-source", archiveId=archive_id)])
                    self.assertIsNone(client_manager.service.shared_sources.get(archive_id))
                ui(host_manager, "local-mode", {"local-mode": "host"})
                ui(host_manager, "local-start", {panel.archive_checkbox("frames"): True})
                client.tick()
                ui(client_manager, "local-peers", item=host.identity)
                host.tick()
                ui(host_manager, "local-approve", item=client.client.identity)
                client.tick()
                service = client_manager.service
                choices = service.call("archive.shared.choices", {"archiveId": "frames"})
                self.assertEqual(len(choices), 1)
                self.assertEqual(service.call("archive.shared.choices", {"archiveId": "photos"}), [])
                binding = service.call("archive.shared.bind", {"archiveId": "frames", "sourceId": choices[0]["id"]})
                frame_archive = next(row for row in binding["archives"] if row["id"] == "frames")
                self.assertEqual(frame_archive["path"], "")
                self.assertEqual(frame_archive["sharedSource"], choices[0])
                page = service.call("archive.list", {"archiveId": "frames"})
                self.assertEqual([row["name"] for row in page["entries"]], ["image.fit"])
                cache = client.folder / "ViewerCache"
                self.assertFalse(cache.exists())
                opened = service.call("image.open", {"archiveId": "frames", "relativePath": "image.fit"})
                self.assertTrue(opened["opened"])
                data_path, = cache.rglob("*.fit")
                np.testing.assert_array_equal(fits.getdata(data_path), fits.getdata(source / "image.fit"))
                self.assertNotIn("local-library", client_manager.live["shared-archives"]["documents"])
                imported = root / "client-import.fit"
                fits.writeto(imported, np.full((16, 16), 55, dtype=np.int16))
                from types import SimpleNamespace
                service._dialogs = SimpleNamespace(choose=lambda *_args, **_kwargs: [str(imported)])
                result = service.wait_task(service.call("archive.import", {"archiveId": "frames"}))
                self.assertEqual(result["errors"], [])
                imported_id, = result["imported"]
                rows = service.call("python.archive.query", {"filters":{"archive":"frames"}})["rows"]
                self.assertIn(imported_id, [row["id"] for row in rows])
                remote = service.catalog.record(imported_id)
                self.assertTrue(remote["shared"])
                self.assertEqual(fits.getdata(source / remote["relative"])[0,0], 55)
                with self.assertRaises(ValueError):
                    service.catalog.trash([imported_id], confirmed=True)
                with self.assertRaises(PermissionError):
                    service.host_services.invoke("remote-operation", "core", {"method":"image.open",
                        "params":{"archiveId":"frames","relativePath":remote["relative"]}})
                self.assertNotIn("frames", [row["id"] for row in
                                 service.host_services.invoke("remote-operation","bootstrap",{})["archives"]])
                self.assertFalse(any(path.name == imported.name for path in cache.rglob("*.fit")))
                analysis = service.quality.run([imported_id], threading.Event(),
                                               lambda *_: None)
                self.assertTrue(any(file["id"] == imported_id and not file.get("error")
                                    for file in analysis["frames"]))
                work = root / "preparation"
                work.mkdir()
                exported = service.workflow.export([imported_id], work, threading.Event(),
                                                   lambda *_: None)
                self.assertTrue(any(path.suffix==".fit" for path in work.iterdir()))
                self.assertIsNotNone(exported)
                returning = root / "returning"
                returning.mkdir()
                output = returning / "returned.fit"
                fits.writeto(output, np.full((16,16),77,dtype=np.int16))
                from Backend.Foundation.branding import marker
                from Backend.Foundation.runtime import atomic_json
                job_id = uuid.uuid4().hex
                service.workflow.save(dict(id=job_id, state="waiting", workspace=str(returning),
                                           baseline={}, created=[], asStack=False,
                                           resultArchive="frames", ids=[imported_id], started=1))
                atomic_json(marker(returning,"session"), dict(id=job_id))
                finish = service.wait_task(service.call("python.preparation.finish",
                    {"id":job_id,"outputs":[output.name],"confirmed":True}))
                self.assertEqual(finish["state"], "completed")
                returned_id, = finish["imported"]
                record = service.catalog.record(returned_id)
                self.assertEqual(fits.getdata(source / record["relative"])[0,0], 77)
            finally:
                client_manager.unload("shared-archives")
                host_manager.unload("shared-archives")

    def test_setup_registers_desktop_prototype(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        entry = importlib.import_module("Plugins.shared-archives.main")
        with tempfile.TemporaryDirectory() as temporary:
            context = Context(Path(temporary), [])
            context.storage = lambda: context.folder
            context.settings = dict
            context.save_settings = lambda _value: None
            context.documents = {}
            context.publish = lambda name, document: context.documents.update({name: ui_document(document)})
            controller = SimpleNamespace(status=lambda: {"state": "off"}, stop=lambda: None)
            with patch.object(entry.windows, "Controller", return_value=controller), patch.object(
                    entry.windows, "environment", return_value={}):
                close = entry.setup(context)
                try:
                    self.assertIn("local-sharing", context.documents)
                    self.assertIn("network", context.documents)
                    self.assertIn("local-start", context.commands)
                finally:
                    close()

    def test_empty_copy_discovers_host_gets_approved_and_reads_fits(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "archive"
            archive.mkdir()
            fits.writeto(archive / "image.fit", np.arange(256, dtype=np.uint16).reshape(16, 16))
            rows = [{"id": "frames", "name": "Кадри", "path": str(archive), "type": "frames"}]
            host_context = Context(root / "host", rows)
            client_context = Context(root / "empty", [])
            host = panel.LocalPanel(host_context, host_context.folder, registry=root / "registry")
            client = panel.LocalPanel(client_context, client_context.folder, registry=root / "registry")
            self.addCleanup(host.close)
            self.addCleanup(client.close)
            host.action("local-mode", {"values": {"local-mode": "host"}})
            host.action("local-start", {"values": {panel.archive_checkbox("frames"): True}})
            client.tick()
            peer = host.identity
            client.action("local-join", {"selected": {"id": peer}})
            self.assertEqual(client.connection, "pending")
            host.action("local-approve", {"selected": {"id": client.client.identity}})
            client.tick()
            self.assertEqual(client.connection, "approved")
            client.action("local-archive", {"selected": {"id": "frames"}})
            client.action("local-download", {"selected": {"id": next(iter(client.files))}})
            self.assertEqual(client.cached["archive"], "frames")
            self.assertTrue(client.cached["path"].is_file())
            np.testing.assert_array_equal(fits.getdata(client.cached["path"]), fits.getdata(archive / "image.fit"))
            self.assertTrue(client.preview.startswith("data:image/png;base64,"))
            key = next(iter(client.documents))
            self.assertIn("image.fit", str(client.source({"action": "browse", "id": key})))
            fits.writeto(archive / "added.fit", np.arange(16, dtype=np.int16).reshape(4, 4))
            host.tick()
            client.tick()
            self.assertIn("added.fit", str(client.source({"action": "browse", "id": key})))
            self.assertIn("обновени", client_context.events[-1])
            self.assertIn("обновени", host_context.events[-1])
            self.assertGreater(client_context.refreshes, 0)
            self.assertGreater(host_context.refreshes, 0)
            saved_document, = (client_context.folder / "SharedCatalogs" / "Client").glob("*.json")
            self.assertIn("added.fit", saved_document.read_text(encoding="utf-8"))
            host.close()
            client.tick()
            self.assertEqual(client.connection, "offline")
            offline = panel.LocalPanel(Context(client_context.folder, []), client_context.folder,
                                       registry=root / "registry")
            self.addCleanup(offline.close)
            self.assertIn("added.fit", str(offline.source({"action": "browse", "id": key})))
            with self.assertRaises(ValueError):
                offline.source({"action": "open", "id": key, "path": "added.fit"})
            self.assertEqual(offline.credential, client.credential)
            restarted = panel.LocalPanel(Context(host_context.folder, rows), host_context.folder,
                                         registry=root / "registry")
            self.addCleanup(restarted.close)
            restarted.action("local-mode", {"values": {"local-mode": "host"}})
            restarted.action("local-start", {"values": {panel.archive_checkbox("frames"): True}})
            offline.tick()
            self.assertEqual(offline.connection, "approved")
            restarted.close()
