"""Real plugin discovery/load/unload with isolated state and tiny SDK fixtures."""
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import types
import unittest

from Backend.Foundation.runtime import Events, Settings, Store
from Bridge.Plugins.contracts import PluginGateway
from Bridge.Plugins.manager import PluginManager


class PluginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        store = Store(self.root/'state')
        events = Events()
        settings = Settings(store)
        self.cancelled = []
        service = types.SimpleNamespace(store=store, settings=settings, events=events,
            jobs=types.SimpleNamespace(cancel=self.cancelled.append),
            host_services=types.SimpleNamespace(owners=set()))
        # These tests exercise the plugin lifecycle without the optional scientific
        # archive/inspection services. A full Service integration is tested below.
        manager = PluginManager.__new__(PluginManager)
        manager.service = service
        manager.settings = settings
        manager.events = events
        manager.folder = self.root/'Plugins'
        manager.folder.mkdir()
        manager.gateway = PluginGateway(settings, events)
        manager.records = {}
        manager.live = {}
        manager.problems = []
        manager.lock = threading.RLock()
        self.manager = manager
        self.addCleanup(self.close_plugins)

    def close_plugins(self):
        for ident in list(self.manager.live):
            self.manager.unload(ident)

    def fixture(self, ident='sample', source=None):
        folder = self.manager.folder/ident
        folder.mkdir()
        (folder/'plugin.json').write_text(json.dumps(dict(apiVersion=1, id=ident,
            name='Sample', description='Lifecycle fixture', version='fixture', entry='main.py',
            permissions=[], contributions=[], capabilities=[dict(id='sample.echo', command='echo')]
        )), encoding='utf-8')
        (folder/'main.py').write_text(source or (
            'def setup(ctx):\n    ctx.command("echo", lambda p: {"echo": p})\n'), encoding='utf-8')
        return folder

    def enable(self, ident='sample'):
        self.manager.toggle(ident, True, confirmed=True)

    def test_all_shipped_plugin_manifests_are_valid(self):
        plugin_root = Path(__file__).resolve().parents[1]/'Plugins'
        folders = [p.parent for p in plugin_root.glob('*/plugin.json') if not p.parent.name.startswith('.')]
        # The core distribution intentionally ships no installed plugin folders.
        for folder in folders:
            with self.subTest(plugin=folder.name):
                self.assertEqual(self.manager.manifest(folder)['id'], folder.name)

    def test_empty_discovery_and_missing_feature_are_nonfatal(self):
        self.assertEqual(self.manager.scan(), [])
        with self.assertRaisesRegex(ValueError, 'не е инсталиран'):
            self.manager.invoke('missing', 'echo')
        with self.assertRaisesRegex(ValueError, 'Настройки'):
            self.manager.invoke_capability('missing.feature')
        self.assertEqual(self.manager.list(), [])

    def test_scan_never_imports_and_bad_plugin_does_not_block_good(self):
        self.fixture('broken', 'raise RuntimeError("must only run on enable")\n')
        self.fixture()
        invalid = self.manager.folder/'invalid'
        invalid.mkdir()
        (invalid/'plugin.json').write_text('not JSON')
        self.manager.scan()
        self.assertEqual(set(self.manager.records), {'broken', 'sample'})
        self.assertTrue(self.manager.problems)
        with self.assertRaises(RuntimeError):
            self.enable('broken')
        self.assertNotIn('broken', self.manager.live)
        self.enable()
        self.assertEqual(self.manager.invoke_capability('sample.echo', {'value': 2}), {'echo': {'value': 2}})

    def test_source_update_revokes_enable_and_ui(self):
        folder = self.fixture()
        self.manager.scan()
        self.enable()
        self.manager.allow_ui('sample', self.manager.records['sample']['uiRevision'], True)
        with (folder/'main.py').open('a') as stream:
            stream.write('\n# changed source\n')
        self.manager.scan()
        self.assertNotIn('sample', self.manager.live)
        self.assertFalse(self.manager.state('sample')['enabled'])
        self.assertFalse(self.manager.state('sample')['uiAllowed'])

    def test_install_from_github_folder_and_remove_preserves_data(self):
        original = self.fixture()
        download = self.root/'repository-main'
        original.rename(download)
        self.manager.install(download)
        self.assertIn('sample', self.manager.records)
        self.assertNotIn('sample', self.manager.live)
        self.enable()
        state = self.manager.settings_path('sample')
        state.write_text('{"keep": true}')
        with self.assertRaises(FileExistsError):
            self.manager.install(download)
        result = self.manager.remove('sample', confirmed=True)
        self.assertTrue(Path(result['preserved']).is_dir())
        self.assertTrue(state.exists())
        self.assertEqual(self.manager.scan(), [])


@unittest.skipUnless(all(importlib.util.find_spec(m) for m in ('numpy', 'scipy', 'astropy', 'photutils', 'PIL')),
                     'Full Service integration requires the core scientific dependencies')
class CoreIntegrationTests(unittest.TestCase):
    def test_core_bootstrap_without_any_plugins(self):
        from Bridge.Core.service import Service
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder:
            root = Path(folder)
            service = Service(Store(root/'state'), plugin_folder=root/'Plugins')
            try:
                self.assertEqual(service.plugins.list(), [])
                self.assertEqual(service.plugins.capabilities(), [])
                snapshot = service.bootstrap()
                self.assertIsNone(snapshot['problem'])
            finally:
                service.jobs.pool.shutdown(wait=True, cancel_futures=True)


if __name__ == '__main__':
    unittest.main()
