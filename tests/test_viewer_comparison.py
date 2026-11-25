# Beta 1.3 UI
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from astropy.io import fits
from PIL import Image

from Backend.Foundation.runtime import Events, Store
from Backend.Imaging.viewer import Viewer
from Bridge.Core.service import Service


class ViewerBufferTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.files = [Path(folder.name) / f'frame-{i:02}.png' for i in range(17)]
        for i, path in enumerate(self.files):
            Image.new('RGB', (256, 256), (i, 36, 64)).save(path)
        catalog = SimpleNamespace(settings=SimpleNamespace(value={'memoryMb': 32}))
        self.viewer = Viewer(catalog, Events())
        self.addCleanup(self.finish_prefetch)

    def finish_prefetch(self):
        worker = self.viewer.worker
        if worker:
            worker.join(timeout=10)
            self.assertFalse(worker.is_alive())

    def test_sliding_window_reuses_rendered_frames_and_releases_them(self):
        viewer = self.viewer
        active = viewer.open(self.files[7])['viewerDocument']
        self.finish_prefetch()
        self.assertEqual(set(viewer.buffer), set(self.files[2:7] + self.files[8:13]))
        with patch.object(viewer, '_prepare', wraps=viewer._prepare) as prepare:
            following = viewer.neighbor(active['id'], 1)['viewerDocument']
            self.finish_prefetch()
            self.assertEqual([call.args[0]['path'] for call in prepare.call_args_list], [self.files[13]])
        self.assertEqual(set(viewer.buffer), set(self.files[3:8] + self.files[9:14]))
        self.assertEqual(set(viewer.frames), {following['id']})
        self.assertEqual(set(viewer.assets), {following['id']})

        first = viewer.open(self.files[0])['viewerDocument']
        self.finish_prefetch()
        self.assertEqual(set(viewer.buffer), set(self.files[1:6]))
        viewer.close(first['id'])
        self.assertFalse(viewer.buffer or viewer.frames or viewer.assets)

    def test_explicit_neighbours_and_memory_limit(self):
        viewer = self.viewer
        candidates = [self.files[11], self.files[3]]
        current = viewer.open(self.files[7], nearby=candidates)['viewerDocument']
        self.finish_prefetch()
        self.assertEqual(set(viewer.buffer), set(candidates))
        viewer.close(current['id'])

        viewer.catalog.settings.value['memoryMb'] = 1
        current = viewer.open(self.files[7], nearby=candidates)['viewerDocument']
        self.finish_prefetch()
        self.assertFalse(viewer.buffer)
        self.assertLessEqual(viewer._memory_used(), 1024**2)
        self.assertIn(current['id'], viewer.frames)


class ViewerComparisonTests(unittest.TestCase):
    def test_independent_fits_and_photo_views_do_not_update_shared_viewer(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / 'photos'
            archive.mkdir()
            fits.writeto(archive / 'first.fits', np.arange(120, dtype=np.float32).reshape(10, 12))
            Image.new('RGB', (20, 14), 'blue').save(archive / 'second.png')
            service = Service(Store(root / 'data'), plugin_folder=root / 'Plugins')
            self.addCleanup(service.jobs.pool.shutdown)
            service.settings.value['archives'][2]['path'] = str(archive)

            source = dict(archiveId='photos', relativePath='first.fits')
            primary = service.call('image.open', source)['viewerDocument']
            event_sequence = service.events.sequence
            first = service.call('viewer.compare', dict(action='open', sourceFrameId=primary['id']))['viewerDocument']
            second = service.call('viewer.compare', dict(action='open', sourceFrameId=primary['id'], delta=1))['viewerDocument']
            updated = service.call('viewer.compare', dict(action='render', id=first['id'], options={'negative': True}))

            self.assertTrue(first['fits'])
            self.assertTrue(second['color'])
            self.assertEqual(len({primary['id'], first['id'], second['id']}), 3)
            self.assertTrue(updated['options']['negative'])
            self.assertEqual(service.session.channels['viewer']['value']['document']['id'], primary['id'])
            self.assertEqual(service.events.sequence, event_sequence)
            self.assertFalse(service.viewer_state.display()['options'].get('negative'))
            self.assertNotEqual(service.viewer.assets[first['id']], service.viewer.assets[primary['id']])

            self.assertFalse(service.call('viewer.compare', dict(action='open', sourceFrameId=second['id'], delta=1))['opened'])
            replacement = service.call('viewer.compare', dict(action='neighbor', id=second['id'], delta=-1))['viewerDocument']
            self.assertEqual(replacement['name'], primary['name'])
            self.assertIn(first['id'], service.viewer.assets)
            self.assertNotIn(second['id'], service.viewer.assets)
            self.assertEqual(service.session.channels['viewer']['value']['document']['id'], primary['id'])
            self.assertEqual(service.events.sequence, event_sequence)

            with self.assertRaises(ValueError):
                service.call('viewer.compare', dict(action='close', id=primary['id']))
            service.call('viewer.compare', dict(action='close', id=first['id']))
            self.assertNotIn(first['id'], service.viewer.assets)
            self.assertIn(replacement['id'], service.viewer.assets)
            self.assertIn(primary['id'], service.viewer.assets)


if __name__ == '__main__':
    unittest.main()
