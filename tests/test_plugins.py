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

    def test_manifest_translations_are_available_to_the_interface(self):
        folder = self.fixture()
        manifest = folder / 'plugin.json'
        original = json.loads(manifest.read_text(encoding='utf-8'))
        translations = {'en': {'Sample': 'Example'}, 'pt-BR': {'Sample': 'Exemplo'}}
        manifest.write_text(json.dumps({**original, 'translations': translations}), encoding='utf-8')
        self.assertEqual(self.manager.scan()[0]['translations'], translations)
        self.assertEqual(self.manager.scan()[0]['name'], 'Sample')
        for invalid in ({'en': {'Sample': ['Example']}}, {'../en': {'Sample': 'Example'}}):
            manifest.write_text(json.dumps({**original, 'translations': invalid}), encoding='utf-8')
            self.assertEqual(self.manager.scan(), [])
            self.assertTrue(self.manager.problems)

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

    def ui_fixture(self, contributions, source):
        folder = self.fixture('visual', source)
        manifest = json.loads((folder/'plugin.json').read_text(encoding='utf-8'))
        manifest.update(contributions=contributions, capabilities=[])
        (folder/'plugin.json').write_text(json.dumps(manifest), encoding='utf-8')
        self.manager.scan()
        self.manager.toggle('visual', True, confirmed=True)
        self.manager.allow_ui('visual', self.manager.records['visual']['uiRevision'], True)
        return folder

    def test_anchor_contributions_are_validated(self):
        from Bridge.Plugins.contracts import contribution_layout
        contribution_layout(dict(id='a', slot='anchor', title='A', anchor='archive.empty', mode='replace', presentation='inline'))
        contribution_layout(dict(id='a', slot='archive', title='A'))
        for bad in (dict(slot='anchor', anchor='archive.unknown'),
                    dict(slot='anchor', anchor='archive.layout', mode='replace'),
                    dict(slot='anchor', anchor='archive.empty', presentation='html'),
                    dict(slot='archive', anchor='archive.empty'),
                    dict(slot='anchor', anchor='archive.empty', archiveIds=['frames']),
                    dict(slot='anchor', anchor='archive.location', archiveIds='frames'),
                    dict(slot='anchor', anchor='archive.location', archiveIds=['frames', 'frames']),
                    dict(slot='anchor', anchor='archive.location', archiveIds=['../frames'])):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                contribution_layout(dict(id='a', title='A', **bad))
        self.ui_fixture([dict(id='empty', slot='anchor', title='Empty', anchor='archive.empty', mode='replace')],
                        'def setup(ctx):\n    ctx.publish("empty", [{"type": "text", "id": "t", "value": "x"}])\n')
        listed = self.manager.list()[0]['contributions'][0]
        self.assertEqual((listed['anchor'], listed['mode'], listed['elements'][0]['id']), ('archive.empty', 'replace', 't'))

    def test_theme_and_notify_are_tokens_only_and_follow_unload(self):
        self.ui_fixture([], (
            'def setup(ctx):\n'
            '    ctx.command("theme", lambda p: ctx.theme(p["tokens"], p.get("scope", "plugin")))\n'
            '    ctx.command("say", lambda p: ctx.notify(p["text"], "success"))\n'))
        self.manager.invoke('visual', 'theme', {'tokens': {'accent': '#AABBCC', 'radius': 4}, 'scope': 'app'})
        self.assertEqual(self.manager.list()[0]['theme'], {'scope': 'app', 'tokens': {'accent': '#aabbcc', 'radius': 4}})
        self.assertEqual(self.manager.events.items[-1]['name'], 'plugin.theme.changed')
        for tokens in ({'accent': 'red'}, {'accent': '#fff;}body{'}, {'radius': 99}, {'font-family': 'x'}):
            with self.subTest(tokens=tokens), self.assertRaises(ValueError):
                self.manager.invoke('visual', 'theme', {'tokens': tokens})
        self.manager.invoke('visual', 'say', {'text': 'Готово'})
        self.assertEqual(self.manager.events.items[-1]['name'], 'plugin.message')
        self.manager.toggle('visual', False)
        self.assertIsNone(self.manager.list()[0]['theme'])

    def test_ui_actions_in_responses_are_validated(self):
        self.ui_fixture([dict(id='tools', slot='anchor', title='Tools', anchor='archive.location', mode='after', presentation='inline')], (
            'def setup(ctx):\n'
            '    ctx.publish("tools", [{"type": "button", "id": "go", "command": "go"}])\n'
            '    ctx.command("go", lambda p: {"uiActions": p["context"].get("archiveId") == "frames" and '
            '[{"type": "choose-archive-source", "archiveId": "frames"}, {"type": "open-settings", "tab": "plugins"}] '
            'or [{"type": "eval", "code": "x"}]})\n'))
        result = self.manager.input('visual', 'tools', 'go', context={'anchor': 'archive.location', 'archiveId': 'frames'})
        self.assertEqual([a['type'] for a in result['uiActions']], ['choose-archive-source', 'open-settings'])
        with self.assertRaisesRegex(ValueError, 'Непознато UI действие'):
            self.manager.input('visual', 'tools', 'go', context={'anchor': 'archive.location', 'archiveId': 'stacks'})
        with self.assertRaisesRegex(ValueError, 'котва'):
            self.manager.input('visual', 'tools', 'go', context={'anchor': 'nowhere'})

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
    def shared_archive(self):
        from Bridge.Core.service import Service
        from Backend.Foundation.runtime import atomic_json
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        service = Service(Store(root/'state'), plugin_folder=root/'Plugins')
        self.addCleanup(service.jobs.pool.shutdown, cancel_futures=True)
        source = {'id': 'a'*64, 'name': 'Пробен архив'}
        service.settings.value['archives'][0].update(path='', sharedSource=source)
        catalog = service.plugins.settings_path('shared-archives').parent/'SharedCatalogs'/'Client'/(source['id']+'.json')
        directory = {'path': 'Telescope', 'kind': 'folder'}
        file = dict(path='Telescope/capture.fit', kind='file', bytes=5760, modified=1700000000000000000,
                    meta=dict(kind='frame', telescope='Telescope', frames=1, framesKnown=True,
                              exposure=120, totalExposure=120, observed='2026-11-20T22:00:00',
                              filter='L', sensor='Camera', width=16, height=12))
        atomic_json(catalog, {'format': 1, 'rows': [directory, file]})
        return service, catalog, directory, file

    def test_shared_archive_projects_metadata_from_saved_catalog(self):
        from unittest.mock import patch
        from Backend.Foundation.runtime import atomic_json
        service, catalog, folder, file = self.shared_archive()

        def provider(action, **p):
            entries = [file] if p['path'] else [folder]
            return dict(path=p['path'], rows=entries, total=1, offset=0,
                        files=int(bool(p['path'])), folders=int(not p['path']))

        with patch.object(service.shared_sources, 'request', side_effect=provider) as request:
            root, = service.call('archive.list', {'archiveId':'frames'})['entries']
            self.assertEqual((root['bytes'], root['details']['frames'], root['details']['totalExposure']), (5760, 1, 120))
            self.assertEqual(root['info']['realTelescope'], 'Telescope')
            row, = service.call('archive.list', {'archiveId':'frames','relativePath':'Telescope'})['entries']
            self.assertEqual((row['info']['resolution'], row['info']['sensor'], row['details']['exposure']), ('16 × 12', 'Camera', 120))
            self.assertEqual(row['details']['firstObservation'], '2026-11-20')
            second = dict(file, path='Telescope/second.fit')
            atomic_json(catalog, {'format': 1, 'rows': [folder, file, second]})
            changed, = service.call('archive.list', {'archiveId':'frames'})['entries']
            self.assertEqual((changed['bytes'], changed['details']['frames'], changed['details']['totalExposure']), (11520, 2, 240))
            self.assertTrue(all(call.args == ('browse',) for call in request.call_args_list))

    def test_shared_archive_rows_read_root_and_file_metadata_without_opening_files(self):
        from unittest.mock import patch
        service, _, folder, file = self.shared_archive()

        def provider(action, **p):
            if action == 'browse':
                return dict(path='', rows=[folder], total=1, offset=0, files=0, folders=1)
            if action == 'record':
                return file
            raise ValueError(f'Unexpected provider operation: {action}')

        requests = [{'archiveId':'frames', 'relativePath':''},
                    {'archiveId':'frames', 'relativePath':'Telescope/capture.fit'}]
        with patch.object(service.shared_sources, 'request', side_effect=provider) as request:
            root, row = service.call('archive.rows', {'rows':requests})
            self.assertEqual((root['bytes'], root['details']['kind'], root['details']['frames']), (5760, 'archive', 1))
            self.assertEqual(root['details']['totalExposure'], 120)
            self.assertIsNotNone(root['modified'])
            self.assertEqual((row['bytes'], row['info']['resolution']), (5760, '16 × 12'))
            file_id = service.shared_sources.file_id('frames', 'a'*64, file['path'])
            info = service.call('python.archive.info', {'id': file_id})
            self.assertEqual(info['meta']['resolution'], '16 × 12')
            self.assertNotIn('open', [call.args[0] for call in request.call_args_list])

    def test_shared_archive_disk_uses_host_usage_and_saved_archive_size(self):
        from unittest.mock import patch
        service, _, _, _ = self.shared_archive()
        host_disk = dict(name='Test host', volume='D:', totalBytes=100000, freeBytes=40000)
        with (patch.object(service.shared_sources, 'request', return_value=host_disk) as request,
              patch.object(service.catalog, 'root', side_effect=AssertionError('Local root was used'))):
            result = service.call('archive.disk', {'archiveId': 'frames', 'refresh': True})
        self.assertEqual(result, dict(host_disk, archiveBytes=5760, reading=False, skipped=False, problem=None))
        request.assert_called_once_with('disk', id='a'*64)
        with self.assertRaises(PermissionError):
            service.host_services.invoke('remote-operation', 'core',
                                         {'method': 'archive.disk', 'params': {'archiveId': 'frames'}})

    def test_shared_archive_refresh_routes_to_provider(self):
        from unittest.mock import patch
        service, _, _, _ = self.shared_archive()

        def provider(action, **params):
            return {'files': 1} if action == 'summary' else {}

        with patch.object(service.shared_sources, 'request', side_effect=provider) as request, patch.object(service, 'start_index') as local:
            task = service.call('python.archive.scan', {'archiveId':'frames'})
            result = service.wait_task(task)
            self.assertTrue(result['shared'])
            self.assertEqual(result['files'], 1)
            self.assertEqual([call.args[0] for call in request.call_args_list], ['refresh', 'summary'])
            self.assertEqual(request.call_args_list[0].kwargs['id'], 'a'*64)
            local.assert_not_called()

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
