# Beta 1.3 UI
from pathlib import Path
import tempfile
import unittest

import numpy as np
from astropy.io import fits
from PIL import Image

from Backend.Foundation.runtime import Store
from Bridge.Core.service import Service


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
