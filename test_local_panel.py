"""Declarative host/client flow with two isolated plugin profiles."""
import importlib
import tempfile
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
        return {"archives": self.entries}

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
                ui(host_manager, "local-mode", {"local-mode": "host"})
                ui(host_manager, "local-start", {panel.archive_checkbox("frames"): True})
                client.tick()
                ui(client_manager, "local-peers", item=host.identity)
                host.tick()
                ui(host_manager, "local-approve", item=client.client.identity)
                client.tick()
                catalog_id = next(iter(client.documents))
                ui(client_manager, "local-library-archives", item=catalog_id, contribution="local-library")
                library = client_manager.live["shared-archives"]["documents"]["local-library"]
                files = next(field["items"] for field in library if field["id"] == "local-library-entries")
                self.assertEqual([item["label"] for item in files], ["image.fit"])
                work = ui(client_manager, "local-library-entries", item=files[0]["id"],
                          contribution="local-library")
                client_manager.service.wait_task(work)
                data_path = client.cached["path"]
                np.testing.assert_array_equal(fits.getdata(data_path), fits.getdata(source / "image.fit"))
                ui(client_manager, "local-begin", contribution="local-library")
                with fits.open(data_path, mode="update", memmap=False) as data:
                    data[0].data[0, 0] = 7
                work = ui(client_manager, "local-save", contribution="local-library")
                client_manager.service.wait_task(work)
                self.assertEqual(fits.getdata(source / "image.fit")[0, 0], 7)
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
            self.assertIn("local-library", client_context.documents)
            client.action("local-library-archive", {"selected": {
                "id": next(iter(client.documents))}})
            self.assertIn("image.fit", str(client_context.documents["local-library"]))
            fits.writeto(archive / "added.fit", np.arange(16, dtype=np.int16).reshape(4, 4))
            host.tick()
            client.tick()
            self.assertIn("added.fit", str(client_context.documents["local-library"]))
            self.assertIn("обновен", client.notice)
            self.assertIn("обновен", host.notice)
            saved_document, = (client_context.folder / "SharedCatalogs" / "Client").glob("*.json")
            self.assertIn("added.fit", saved_document.read_text(encoding="utf-8"))
            host.close()
            client.tick()
            self.assertEqual(client.connection, "offline")
            offline = panel.LocalPanel(Context(client_context.folder, []), client_context.folder,
                                       registry=root / "registry")
            self.addCleanup(offline.close)
            self.assertIn("Запазен каталог", str(offline.ctx.documents["local-library"]))
            self.assertIn("added.fit", str(offline.documents))
