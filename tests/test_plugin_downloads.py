"""Download lifecycle and hostile archives, isolated from the user's plugins."""
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch
import zipfile

from Backend.Foundation.runtime import Events, Jobs, Settings, Store
from Backend.Foundation.updates import Updates
from Bridge.Core.protocol import LocalizedError, error_payload
from Bridge.Plugins.contracts import PluginGateway
from Bridge.Plugins.manager import PluginManager
from Bridge.Plugins.downloads import Downloads, newer_version, unpack, validate, REPOSITORY


def archive(files):
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as z:
        for name,value in files.items():
            info=zipfile.ZipInfo()
            # Set the raw archive filename after construction: Windows ZipInfo
            # otherwise normalizes backslashes before the adversarial test.
            info.filename='repository-commit/'+name
            z.writestr(info,value)
    return buffer.getvalue()


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        store=Store(self.root/'data');events=Events();settings=Settings(store)
        m=PluginManager.__new__(PluginManager)
        m.service=types.SimpleNamespace(store=store,settings=settings,events=events,host_services=types.SimpleNamespace(owners=set()),jobs=types.SimpleNamespace(cancel=lambda _:None))
        m.service.workflow = Mock()
        m.service.workflow.open_sessions.return_value = []
        m.settings=settings;m.events=events;m.folder=self.root/'Plugins';m.gateway=PluginGateway(settings)
        m.records={};m.live={};m.problems=[];m.lock=threading.RLock();self.manager=m
        self.downloads=Downloads(m)
        manifest=dict(id='sample',apiVersion=1,name='Sample',description='test',version='V-1',entry='main.py')
        self.data=archive({'plugin.json':json.dumps(manifest),'main.py':'raise RuntimeError("must not execute while installing")'})
        self.entry=dict(id='sample',name='Sample',description='test',version='V-1',repository=REPOSITORY,commit='1'*40,sha256=hashlib.sha256(self.data).hexdigest())
        self.downloads.entries=validate(dict(format=1,plugins=[self.entry]))

    def test_clean_scan_download_remove_and_reinstall(self):
        self.assertEqual(self.manager.scan(),[])
        self.assertFalse(self.manager.folder.exists())
        with patch('Bridge.Plugins.downloads.fetch',return_value=self.data):self.downloads.install('sample')
        self.assertEqual(self.manager.records['sample']['version'],'V-1')
        self.assertEqual(self.manager.live,{})
        self.assertFalse(self.manager.state('sample')['enabled'])
        self.assertTrue(self.downloads.list()[0]['installed'])
        settings=self.manager.settings_path('sample');settings.write_text('{"keep":true}')
        result=self.manager.remove('sample',True)
        self.assertFalse(self.manager.folder.exists())
        self.assertTrue(Path(result['preserved']).is_dir())
        self.assertTrue(settings.exists())
        self.assertEqual(self.manager.scan(),[])
        self.assertFalse(self.manager.folder.exists())
        with patch('Bridge.Plugins.downloads.fetch',return_value=self.data):self.downloads.install('sample')
        self.assertEqual(settings.read_text(),'{"keep":true}')

    def test_corruption_and_mismatched_manifest_leave_no_plugin_folder(self):
        with patch('Bridge.Plugins.downloads.fetch',return_value=b'corrupted'),self.assertRaises(ValueError):self.downloads.install('sample')
        self.entry['version']='Beta-1'
        with patch('Bridge.Plugins.downloads.fetch',return_value=self.data),self.assertRaises(ValueError):self.downloads.install('sample')
        self.assertFalse(self.manager.folder.exists())

    def install_and_offer_update(self):
        with patch('Bridge.Plugins.downloads.fetch', return_value=self.data):
            self.downloads.install('sample')
        data = archive({'plugin.json': json.dumps(dict(id='sample', apiVersion=1, name='Sample',
                        description='Updated', version='V-2')), 'main.py': 'raise RuntimeError("must not auto-enable")'})
        self.entry = dict(self.entry, version='V-2', commit='2'*40, sha256=hashlib.sha256(data).hexdigest())
        self.downloads.entries = [self.entry]
        return data

    def test_update_is_staged_then_replaces_package_before_startup_scan(self):
        data = self.install_and_offer_update()
        settings = self.manager.settings_path('sample')
        settings.write_text('{"keep":true}')
        self.manager.state('sample').update(enabled=True, uiAllowed=True, revision=self.manager.records['sample']['uiRevision'])
        self.assertTrue(self.downloads.list()[0]['updateAvailable'])
        with patch('Bridge.Plugins.downloads.fetch', return_value=data), self.downloads.stage_update('sample', self.entry['commit']) as staged:
            self.downloads.queue_update('sample', staged)
        self.assertEqual(self.manager.manifest(self.manager.folder/'sample')['version'], 'V-1')
        self.assertEqual(self.downloads.apply_pending(), [])
        self.manager.scan()
        self.assertEqual(self.manager.records['sample']['version'], 'V-2')
        self.assertEqual(self.manager.live, {})
        self.assertFalse(self.manager.state('sample')['uiAllowed'])
        self.assertEqual(settings.read_text(), '{"keep":true}')
        backup, = (self.downloads.cache.parent/'RemovedPlugins').iterdir()
        self.assertEqual(self.manager.manifest(backup)['version'], 'V-1')
        self.assertFalse(self.downloads.list()[0]['updateAvailable'])
        self.assertEqual(self.downloads.apply_pending(), [])

    def test_failed_swap_restores_previous_package(self):
        data = self.install_and_offer_update()
        with patch('Bridge.Plugins.downloads.fetch', return_value=data), self.downloads.stage_update('sample', self.entry['commit']) as staged:
            self.downloads.queue_update('sample', staged)
        original_rename = Path.rename

        def fail_package_rename(path, target):
            if path.name == 'package':
                raise PermissionError('locked')
            return original_rename(path, target)

        with patch.object(Path, 'rename', fail_package_rename):
            self.assertEqual(self.downloads.apply_pending(), ['sample: locked'])
        self.assertEqual(self.downloads.update_notices, [dict(plugin='sample', error='locked')])
        self.assertEqual(self.manager.manifest(self.manager.folder/'sample')['version'], 'V-1')
        self.assertEqual(self.downloads.apply_pending(), [])
        self.assertEqual(self.downloads.update_notices, [])

    def test_changed_installed_plugin_reports_translatable_update_error(self):
        data = self.install_and_offer_update()
        with patch('Bridge.Plugins.downloads.fetch', return_value=data), self.downloads.stage_update('sample', self.entry['commit']) as staged:
            (self.manager.folder/'sample'/'main.py').write_text('# Changed during update')
            with self.assertRaises(LocalizedError) as caught:
                self.downloads.queue_update('sample', staged)
        self.assertEqual(error_payload(caught.exception)['errorKey'], 'plugins.installedChanged')
        self.assertFalse((self.manager.folder/'.update-sample').exists())

    def test_changed_pending_package_reports_translatable_startup_notice(self):
        data = self.install_and_offer_update()
        with patch('Bridge.Plugins.downloads.fetch', return_value=data), self.downloads.stage_update('sample', self.entry['commit']) as staged:
            self.downloads.queue_update('sample', staged)
        (self.manager.folder/'.update-sample'/'package'/'main.py').write_text('# Changed after queuing')
        self.assertEqual(len(self.downloads.apply_pending()), 1)
        notice, = self.downloads.update_notices
        self.assertEqual((notice['plugin'], notice['errorKey']), ('sample', 'plugins.updateFilesChanged'))
        self.assertEqual(self.manager.manifest(self.manager.folder/'sample')['version'], 'V-1')

    def test_startup_resumes_update_interrupted_between_package_moves(self):
        data = self.install_and_offer_update()
        with patch('Bridge.Plugins.downloads.fetch', return_value=data), self.downloads.stage_update('sample', self.entry['commit']) as staged:
            self.downloads.queue_update('sample', staged)
        pending = self.manager.folder/'.update-sample'
        (self.manager.folder/'sample').rename(pending/'previous')
        self.assertEqual(self.downloads.apply_pending(), [])
        self.assertEqual(self.manager.manifest(self.manager.folder/'sample')['version'], 'V-2')
        self.assertFalse(pending.exists())

    def test_corrupt_or_stale_update_never_reaches_restart(self):
        from Bridge.Desktop.host import restart_for_plugin_update

        self.install_and_offer_update()
        self.manager.service.downloads = self.downloads
        self.manager.service.jobs = Jobs(self.manager.events)
        self.addCleanup(self.manager.service.jobs.pool.shutdown)
        with patch('Bridge.Desktop.host.prepare_restart') as restart, patch('Bridge.Plugins.downloads.fetch', return_value=b'corrupted'):
            for commit in ('1'*40, self.entry['commit']):
                with self.subTest(commit=commit), self.assertRaises(ValueError):
                    restart_for_plugin_update(self.manager.service, dict(id='sample', commit=commit))
            restart.assert_not_called()
        self.assertFalse(self.manager.service.jobs.restarting)
        self.assertEqual(self.manager.manifest(self.manager.folder/'sample')['version'], 'V-1')
        self.assertFalse(list(self.manager.folder.glob('.update-*')))

    def test_busy_app_and_unavailable_restart_leave_update_unqueued(self):
        from Bridge.Desktop.host import restart_for_plugin_update

        data = self.install_and_offer_update()
        service = self.manager.service
        service.downloads = self.downloads
        service.jobs = Jobs(self.manager.events)
        self.addCleanup(service.jobs.pool.shutdown)
        params = dict(id='sample', commit=self.entry['commit'])
        with patch('Bridge.Plugins.downloads.fetch', return_value=data), patch('Bridge.Desktop.host.prepare_restart', side_effect=OSError('restart unavailable')) as restart:
            service.jobs.enter_call()
            try:
                with self.assertRaises(ValueError):
                    restart_for_plugin_update(service, params)
                restart.assert_not_called()
            finally:
                service.jobs.leave_call()
            with self.assertRaises(OSError):
                restart_for_plugin_update(service, params)
        self.assertFalse(service.jobs.restarting)
        self.assertFalse(list(self.manager.folder.glob('.update-*')))

    def test_confirmed_update_waits_for_task_cleanup_before_restart(self):
        from Bridge.Desktop.host import restart_for_plugin_update

        data = self.install_and_offer_update()
        service = self.manager.service
        service.downloads = self.downloads
        service.jobs = Jobs(self.manager.events)
        self.addCleanup(service.jobs.pool.shutdown)
        working_copy = self.root/'temporary.fit'
        working_copy.touch()
        baseline = self.root/'existing.fit'
        baseline.touch()
        started = threading.Event()
        stopped = threading.Event()

        def work(cancel, progress):
            started.set()
            if not cancel.wait(5):
                raise RuntimeError('Task was not cancelled')
            stopped.set()
            raise InterruptedError()

        def clean():
            self.assertTrue(stopped.is_set())
            working_copy.unlink()

        def prepare():
            self.assertTrue(stopped.is_set())
            self.assertFalse(working_copy.exists())
            self.assertTrue(baseline.exists())
            service.workflow.cancel.assert_called_once_with('open', clean=True, confirmed=True)

        service.workflow.open_sessions.return_value = ['open']
        service.jobs.submit('preparation', 'Test', work, on_update_cancel=clean)
        self.assertTrue(started.wait(5))
        params = dict(id='sample', commit=self.entry['commit'])
        with patch('Bridge.Plugins.downloads.fetch', return_value=data), patch('Bridge.Desktop.host.prepare_restart', side_effect=prepare) as restart:
            with self.assertRaises(LocalizedError) as busy:
                restart_for_plugin_update(service, params)
            self.assertEqual(busy.exception.key, 'diagnostics.restartBusy')
            self.assertFalse(stopped.is_set())
            self.assertTrue(working_copy.exists())
            restart.assert_not_called()
            restart_for_plugin_update(service, dict(params, cancelWork=True))
            restart.assert_called_once()
        self.assertEqual(self.downloads.apply_pending(), [])
        self.assertEqual(self.manager.manifest(self.manager.folder/'sample')['version'], 'V-2')

    def test_changed_plugin_and_failed_cleanup_block_confirmed_restart(self):
        from Bridge.Desktop.host import restart_for_plugin_update

        data = self.install_and_offer_update()
        service = self.manager.service
        service.downloads = self.downloads
        service.jobs = Jobs(self.manager.events)
        self.addCleanup(service.jobs.pool.shutdown)
        service.workflow.open_sessions.return_value = ['open']
        service.workflow.cancel.side_effect = OSError('Workspace is locked')
        params = dict(id='sample', commit=self.entry['commit'], cancelWork=True)
        main = self.manager.folder/'sample'/'main.py'
        original = main.read_bytes()
        main.write_text('# Local changes')
        with patch('Bridge.Plugins.downloads.fetch', return_value=data), patch('Bridge.Desktop.host.prepare_restart') as restart:
            with self.assertRaises(LocalizedError) as changed:
                restart_for_plugin_update(service, params)
            self.assertEqual(changed.exception.key, 'plugins.installedChanged')
            service.workflow.cancel.assert_not_called()
            main.write_bytes(original)
            with self.assertRaises(LocalizedError) as locked:
                restart_for_plugin_update(service, params)
            self.assertEqual(locked.exception.key, 'plugins.updateCleanupFailed')
            restart.assert_not_called()
        self.assertFalse(service.jobs.restarting)
        self.assertFalse(list(self.manager.folder.glob('.update-*')))

    def test_update_comparison_does_not_offer_downgrades_or_aliases(self):
        self.assertTrue(newer_version('V-2.9', 'V-2.10'))
        self.assertTrue(newer_version('Beta-1', 'Beta-2'))
        self.assertFalse(newer_version('V-2.3', 'V-2.2'))
        self.assertFalse(newer_version('V-2', '2.0'))
        self.assertFalse(newer_version('V-2', 'unknown'))

    def test_offline_refresh_preserves_catalog_and_reports_problem(self):
        with patch('Bridge.Plugins.downloads.fetch',side_effect=OSError):self.downloads.refresh()
        self.assertEqual(self.downloads.entries,[self.entry]);self.assertTrue(self.downloads.problem)

    def test_rejects_private_plugin_external_repository_and_invalid_commit(self):
        for patch_value in ({'id':'ai-training'},{'repository':'someone/other'},{'commit':'main'}):
            with self.subTest(patch_value=patch_value),self.assertRaises(ValueError):validate(dict(format=1,plugins=[dict(self.entry,**patch_value)]))

    def test_archive_source_package_selects_compatible_download(self):
        newer = archive({'plugin.json':json.dumps(dict(id='sample', apiVersion=1, name='Sample',
                        description='Sources', version='V-2.1', entry='main.py')), 'main.py':''})
        package = dict(description='Sources', version='V-2.1', commit='2'*40,
                       sha256=hashlib.sha256(newer).hexdigest())
        item = dict(self.entry, archiveSourcePackage=package)
        self.downloads.entries = validate(dict(format=1, plugins=[item]))
        self.assertEqual(self.downloads.entries, [dict(self.entry, **package)])
        self.assertEqual(item['version'], 'V-1')
        with patch('Bridge.Plugins.downloads.fetch', return_value=newer) as fetch:
            self.downloads.install('sample')
        self.assertIn(package['commit'], fetch.call_args.args[0])
        self.assertEqual(self.manager.records['sample']['version'], 'V-2.1')

    def test_lan_package_preserves_older_catalog_entries(self):
        sources = dict(version='V-2.2', description='Sources', commit='2'*40, sha256='a'*64)
        lan = dict(version='V-2.3', description='LAN', commit='3'*40, sha256='b'*64)
        item = dict(self.entry, archiveSourcePackage=sources, lanPackage=lan)
        self.assertEqual(validate(dict(format=1, plugins=[item])), [dict(self.entry, **lan)])
        self.assertEqual(item['archiveSourcePackage'], sources)
        self.assertEqual(item['version'], self.entry['version'])
        with self.assertRaises(ValueError):
            validate(dict(format=1, plugins=[dict(item, lanPackage=dict(lan, sha256='invalid'))]))

    def test_rejects_unverified_archive_source_package(self):
        good = dict(version='V-2.1', description='Sources', commit='2'*40, sha256='b'*64)
        for package in (None, dict(good, commit='main'), dict(good, repository='someone/other'),
                        dict(good, version=''), dict(good, sha256='invalid')):
            with self.subTest(package=package), self.assertRaises(ValueError):
                validate(dict(format=1, plugins=[dict(self.entry, archiveSourcePackage=package)]))

    def test_hostile_zip_paths_and_symlinks(self):
        for name in ('../escape','a/../../escape','/outside','a\\evil.py','a:stream','CON.txt','nul','a./file'):
            with self.subTest(name=name),tempfile.TemporaryDirectory(dir=self.root) as folder,self.assertRaises(ValueError):unpack(archive({name:'x'}),Path(folder))
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as z:
            info=zipfile.ZipInfo('root/link');info.external_attr=0o120777<<16;z.writestr(info,'../escape')
        with self.assertRaises(ValueError):unpack(data.getvalue(),self.root)

    def test_unknown_file_is_never_deleted_by_folder_cleanup(self):
        self.manager.folder.mkdir();(self.manager.folder/'keep.txt').write_text('keep')
        self.manager.prune_folder();self.assertTrue((self.manager.folder/'keep.txt').exists())


class UpdateTests(unittest.TestCase):
    def test_release_identity_and_manual_retry(self):
        releases=[dict(tag_name='v-next',published_at='2026-11-14T10:00:00Z',draft=False)]
        with patch.dict(os.environ,{'ASTRO_ARCHIVE_RELEASE_TAG':'v-current','ASTRO_ARCHIVE_RELEASE_CUTOFF':'2026-11-13T00:00:00+00:00'}):u=Updates()
        # Environment is normally inherited from the launcher for the whole process.
        with patch.dict(os.environ,{'ASTRO_ARCHIVE_RELEASE_CUTOFF':'2026-11-13T00:00:00+00:00'}),patch('Backend.Foundation.updates.fetch',return_value=json.dumps(releases).encode()):
            self.assertTrue(u.check()['available'])
        with patch('Backend.Foundation.updates.fetch',side_effect=OSError):self.assertTrue(u.check()['problem'])

    def test_release_check_rejects_newly_published_lower_beta(self):
        for current, cutoff, tag, published in (
            ('beta1.4', '2026-11-23T11:00:00Z', 'beta1.2.2', '2026-11-24T12:00:00Z'),
            ('beta1.4', '2026-11-23T11:00:00Z', 'beta1.4.0', '2026-11-25T12:00:00Z'),
            ('beta1.2.2', '2026-11-24T11:00:00Z', 'beta1.4', '2026-11-23T12:00:00Z'),
        ):
            releases = [dict(tag_name=tag, published_at=published, draft=False)]
            with self.subTest(current=current, offered=tag), patch.dict(os.environ, {
                'ASTRO_ARCHIVE_RELEASE_TAG': current, 'ASTRO_ARCHIVE_RELEASE_CUTOFF': cutoff,
            }), patch('Backend.Foundation.updates.fetch', return_value=json.dumps(releases).encode()):
                updates = Updates()
                self.assertFalse(updates.check()['available'])
                self.assertIsNone(updates.release)

    def test_release_check_prefers_highest_number_among_new_beta_releases(self):
        releases = [
            dict(tag_name='beta1.9', published_at='2026-11-27T12:00:00Z', draft=False),
            dict(tag_name='beta1.10', published_at='2026-11-25T12:00:00Z', draft=False),
            dict(tag_name='beta1.11', published_at='2026-11-28T12:00:00Z', draft=True),
            dict(tag_name='beta2', published_at='2026-11-21T12:00:00Z', draft=False),
            dict(tag_name='plugin-testing', published_at='2026-11-29T12:00:00Z', draft=False),
        ]
        with patch.dict(os.environ, {
            'ASTRO_ARCHIVE_RELEASE_TAG': 'beta1-launcher',
            'ASTRO_ARCHIVE_RELEASE_CUTOFF': '2026-11-23T11:00:00Z',
        }), patch('Backend.Foundation.updates.fetch', return_value=json.dumps(releases).encode()):
            result = Updates().check()
        self.assertTrue(result['available'])
        self.assertEqual(result['latest'], 'beta1.10')

    def test_source_copy_does_not_claim_to_be_current(self):
        with patch.dict(os.environ,{},clear=True),patch('Backend.Foundation.updates.fetch',return_value=b'[]'):
            result=Updates().check();self.assertFalse(result['available']);self.assertFalse(result['currentKnown'])


if __name__=='__main__':unittest.main()
