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
from unittest.mock import patch
import zipfile

from Backend.Foundation.runtime import Events, Settings, Store
from Backend.Foundation.updates import Updates
from Bridge.Plugins.contracts import PluginGateway
from Bridge.Plugins.manager import PluginManager
from Bridge.Plugins.downloads import Downloads, unpack, validate, REPOSITORY


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
        m.settings=settings;m.events=events;m.folder=self.root/'Plugins';m.gateway=PluginGateway(settings,events)
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

    def test_offline_refresh_preserves_catalog_and_reports_problem(self):
        with patch('Bridge.Plugins.downloads.fetch',side_effect=OSError):self.downloads.refresh()
        self.assertEqual(self.downloads.entries,[self.entry]);self.assertTrue(self.downloads.problem)

    def test_rejects_private_plugin_external_repository_and_invalid_commit(self):
        for patch_value in ({'id':'ai-training'},{'repository':'someone/other'},{'commit':'main'}):
            with self.subTest(patch_value=patch_value),self.assertRaises(ValueError):validate(dict(format=1,plugins=[dict(self.entry,**patch_value)]))

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

    def test_source_copy_does_not_claim_to_be_current(self):
        with patch.dict(os.environ,{},clear=True),patch('Backend.Foundation.updates.fetch',return_value=b'[]'):
            result=Updates().check();self.assertFalse(result['available']);self.assertFalse(result['currentKnown'])


if __name__=='__main__':unittest.main()
