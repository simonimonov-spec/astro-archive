# Beta 1.3 UI (в разработка, не е издание) · виж docs/BETA-1.3-UI.md
"""Data tag (astrophotography / photometry) stored next to the frames."""
import json
import tempfile
import threading
import unittest
from pathlib import Path

import numpy as np
from astropy.io import fits

from Backend.Archive import tags
from Backend.Archive.catalog import Catalog
from Backend.Foundation.branding import companion
from Backend.Foundation.runtime import Events, Settings, Store

PHOTOMETRY = {'kind': 'photometry', 'type': 'exoplanet', 'label': None}


def write_fits(path, obj='WASP-12', night='2026-10-10', exposure=10.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    header = fits.Header()
    header['OBJECT'] = obj
    header['TELESCOP'] = 'S50 Monolit'
    header['DATE-OBS'] = night + 'T21:00:00'
    header['PSNIGHT'] = night
    header['EXPTIME'] = exposure
    fits.PrimaryHDU(np.zeros((8, 8), dtype=np.uint16), header=header).writeto(path)
    return path


class TagModelTests(unittest.TestCase):
    def test_normalize_rules(self):
        self.assertIsNone(tags.normalize(None))
        self.assertEqual(tags.normalize({'kind': 'astro'}), {'kind': 'astro', 'type': None, 'label': None})
        self.assertEqual(tags.normalize({'kind': 'photometry', 'type': 'variable', 'label': 'x'}),
                         {'kind': 'photometry', 'type': 'variable', 'label': None})
        long = '  ' + 'А' * 60 + '  '
        self.assertEqual(tags.normalize({'kind': 'photometry', 'type': 'other', 'label': long})['label'], 'А' * 40)
        self.assertEqual(tags.normalize(PHOTOMETRY, stack=True), {'kind': 'photometry', 'type': None, 'label': None})
        with self.assertRaises(ValueError):
            tags.normalize({'kind': 'photometry', 'type': 'comet'})
        with self.assertRaises(ValueError):
            tags.normalize({'kind': 'other'})

    def test_matches(self):
        astro = {'kind': 'astro', 'type': None, 'label': None}
        variable = {'kind': 'photometry', 'type': 'variable', 'label': None}
        self.assertTrue(tags.matches(astro, 'astro'))
        self.assertFalse(tags.matches(astro, 'photometry'))
        self.assertTrue(tags.matches(variable, 'photometry'))
        self.assertTrue(tags.matches(variable, 'variable'))
        self.assertFalse(tags.matches(variable, 'exoplanet'))
        self.assertTrue(tags.matches(None, 'none'))
        self.assertFalse(tags.matches(astro, 'none'))
        self.assertTrue(tags.matches(None, ''))


class TagStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = Store(root / 'state')
        self.settings = Settings(self.store)
        self.archive = root / 'frames'
        self.archive.mkdir()
        self.settings.value['archives'][0]['path'] = str(self.archive)
        self.catalog = Catalog(self.store, self.settings, Events())
        self.frame = write_fits(self.archive / 'S50' / 'WASP-12' / '2026-10-10' / 'a.fits')
        self.scan()

    def tearDown(self):
        self.temp.cleanup()

    def scan(self):
        self.catalog.scan('frames', threading.Event(), lambda *args, **kwargs: None)

    def ident(self):
        return self.catalog.query({'archive': 'frames'})[0]['id']

    def test_set_tag_survives_rescan(self):
        self.catalog.set_tag([self.ident()], PHOTOMETRY)
        self.scan()
        self.assertEqual(self.catalog.query({'archive': 'frames'})[0]['meta']['tag'], PHOTOMETRY)

    def test_set_tag_keeps_other_companion_keys(self):
        side = companion(self.frame)
        side.write_text(json.dumps({'object': 'WASP-12 b'}), encoding='utf-8')
        self.scan()
        self.catalog.set_tag([self.ident()], PHOTOMETRY)
        stored = json.loads(side.read_text(encoding='utf-8'))
        self.assertEqual(stored['object'], 'WASP-12 b')
        self.assertEqual(stored['tag'], PHOTOMETRY)

    def test_clear_tag_removes_key(self):
        ident = self.ident()
        self.catalog.set_tag([ident], PHOTOMETRY)
        self.assertEqual(self.catalog.set_tag([ident], None), {'updated': 1})
        self.scan()
        self.assertNotIn('tag', self.catalog.query({'archive': 'frames'})[0]['meta'])
        side = companion(self.frame)
        self.assertNotIn('tag', json.loads(side.read_text(encoding='utf-8')) if side.exists() else {})

    def test_tag_set_by_folder_prefix(self):
        other = write_fits(self.archive / 'S50' / 'M 16' / '2026-10-08' / 'b.fits', obj='M 16', night='2026-10-08')
        self.scan()
        self.assertEqual(self.catalog.tag_paths('frames', ['S50/WASP-12'], PHOTOMETRY), {'updated': 1})
        by_name = {Path(r['relative']).name: r['meta'].get('tag') for r in self.catalog.query({'archive': 'frames'})}
        self.assertEqual(by_name, {'a.fits': PHOTOMETRY, 'b.fits': None})
        self.assertTrue(other.exists())

    def test_query_filters_by_tag(self):
        write_fits(self.archive / 'S50' / 'M 16' / '2026-10-08' / 'b.fits', obj='M 16', night='2026-10-08')
        self.scan()
        self.catalog.tag_paths('frames', ['S50/WASP-12'], PHOTOMETRY)
        names = lambda wanted: sorted(Path(r['relative']).name for r in self.catalog.query({'archive': 'frames', 'tag': wanted}))
        self.assertEqual(names('photometry'), ['a.fits'])
        self.assertEqual(names('exoplanet'), ['a.fits'])
        self.assertEqual(names('variable'), [])
        self.assertEqual(names('none'), ['b.fits'])
        self.assertEqual(names(''), ['a.fits', 'b.fits'])
        filtered = self.catalog.filtered({'archive': 'frames', 'tag': 'none'})
        self.assertEqual(filtered['total'], 1)
        json.dumps(filtered)  # the RPC answer must be serialisable
        json.dumps(self.catalog.browse.details('frames', 'S50'))

    def test_folder_details_show_uniform_or_mixed_tag(self):
        write_fits(self.archive / 'S50' / 'WASP-12' / '2026-10-10' / 'b.fits')
        self.scan()
        self.catalog.tag_paths('frames', ['S50/WASP-12/2026-10-10/a.fits'], PHOTOMETRY)
        self.assertEqual(self.catalog.browse.details('frames', 'S50/WASP-12/2026-10-10')['tag'], 'mixed')
        self.catalog.tag_paths('frames', ['S50/WASP-12/2026-10-10'], PHOTOMETRY)
        self.assertEqual(self.catalog.browse.details('frames', 'S50/WASP-12')['tag'], PHOTOMETRY)
        self.catalog.tag_paths('frames', ['S50/WASP-12'], None)
        self.assertIsNone(self.catalog.browse.details('frames', 'S50/WASP-12')['tag'])

    def test_set_tag_failure_keeps_disk_and_catalog_in_step(self):
        second = write_fits(self.archive / 'S50' / 'WASP-12' / '2026-10-10' / 'b.fits')
        self.scan()
        companion(second).write_text('{broken', encoding='utf-8')
        ids = [r['id'] for r in sorted(self.catalog.query({'archive': 'frames'}), key=lambda r: r['relative'])]
        with self.assertRaises(Exception):
            self.catalog.set_tag(ids, PHOTOMETRY)
        first = sorted(self.catalog.query({'archive': 'frames'}), key=lambda r: r['relative'])[0]
        on_disk = json.loads(companion(self.frame).read_text(encoding='utf-8')).get('tag') if companion(self.frame).exists() else None
        self.assertEqual(first['meta'].get('tag'), on_disk)

    def test_set_tag_waits_for_running_scan(self):
        lock = self.catalog.scan_locks.setdefault('frames', threading.Lock())
        with lock:
            with self.assertRaisesRegex(ValueError, 'обновява'):
                self.catalog.set_tag([self.ident()], PHOTOMETRY)

    def test_malformed_tag_in_companion_is_ignored(self):
        companion(self.frame).write_text(json.dumps({'tag': 'exoplanet'}), encoding='utf-8')
        self.scan()
        self.assertNotIn('tag', self.catalog.query({'archive': 'frames'})[0]['meta'])
        self.assertEqual(len(self.catalog.query({'archive': 'frames', 'tag': 'none'})), 1)

    def test_shared_archive_read_only(self):
        ident = self.ident()
        self.settings.value['archives'][0]['sharedSource'] = {'name': 'remote'}
        with self.assertRaisesRegex(ValueError, 'само за четене'):
            self.catalog.set_tag([ident], PHOTOMETRY)



class ImportTagTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = Store(root / 'state')
        self.settings = Settings(self.store)
        self.archive = root / 'frames'
        self.archive.mkdir()
        self.settings.value['archives'][0]['path'] = str(self.archive)
        self.catalog = Catalog(self.store, self.settings, Events())
        self.incoming = root / 'incoming'
        self.a = write_fits(self.incoming / 'a.fits', obj='WASP-12', night='2026-10-10')
        self.b = write_fits(self.incoming / 'b.fits', obj='WASP-12', night='2026-10-10', exposure=60.0)
        self.c = write_fits(self.incoming / 'c.fits', obj='M 16', night='2026-10-08')

    def tearDown(self):
        self.temp.cleanup()

    def test_preview_groups_by_night_object(self):
        groups = tags.preview_groups([str(self.a), str(self.b), str(self.c)])
        self.assertEqual([(g['night'], g['object']) for g in groups], [('2026-10-10', 'WASP-12'), ('2026-10-08', 'M 16')])
        self.assertEqual([f['index'] for f in groups[0]['files']], [0, 1])
        self.assertEqual(groups[0]['files'][1]['exposure'], 60.0)

    def test_import_applies_per_file_tag(self):
        result = self.catalog.import_files([str(self.a), str(self.b)], threading.Event(), lambda *a, **k: None,
                                           tags={str(self.a): PHOTOMETRY, str(self.b): {'kind': 'astro'}})
        self.assertEqual(len(result['imported']), 2)
        by_name = {Path(r['relative']).name: r['meta'].get('tag') for r in self.catalog.query({'archive': 'frames'})}
        self.assertEqual(by_name['a.fits'], PHOTOMETRY)
        self.assertEqual(by_name['b.fits'], {'kind': 'astro', 'type': None, 'label': None})

    def test_import_tag_found_with_forward_slash_path(self):
        self.catalog.import_files([self.a.as_posix()], threading.Event(), lambda *a, **k: None, tags={self.a.as_posix(): PHOTOMETRY})
        self.assertEqual(self.catalog.query({'archive': 'frames'})[0]['meta'].get('tag'), PHOTOMETRY)

    def test_preview_token_can_be_put_back(self):
        previews = tags.ImportPreviews()
        token = previews.add(['x.fits'], None)
        entry = previews.take(token)
        previews.put_back(token, entry)
        self.assertEqual(previews.take(token)['paths'], ['x.fits'])

    def test_import_duplicate_keeps_tag(self):
        self.catalog.import_files([str(self.a)], threading.Event(), lambda *a, **k: None, tags={str(self.a): PHOTOMETRY})
        again = self.catalog.import_files([str(self.a)], threading.Event(), lambda *a, **k: None, tags={str(self.a): {'kind': 'astro'}})
        self.assertEqual(len(again['duplicates']), 1)
        self.assertEqual(self.catalog.query({'archive': 'frames'})[0]['meta']['tag'], PHOTOMETRY)

    def test_service_preview_then_import_with_tags(self):
        import time
        from Bridge.Core.service import Service
        service = Service(Store(Path(self.temp.name) / 'svc'), plugin_folder=Path(self.temp.name) / 'Plugins')
        self.addCleanup(service.jobs.pool.shutdown, cancel_futures=True)
        service.settings.value['archives'][0]['path'] = str(self.archive)
        service.dialogs.choose = lambda *args, **kwargs: [str(self.a), str(self.c)]

        def finish(started):
            for _ in range(200):
                task = service.call('python.task', {'id': started['taskId']})
                if task['state'] in ('completed', 'blocked', 'cancelled'):
                    return task
                time.sleep(.05)
            self.fail('task did not finish')

        preview = finish(service.call('archive.import.preview', {}))['result']
        self.assertEqual(preview['count'], 2)
        index = next(f['index'] for g in preview['groups'] for f in g['files'] if f['name'] == 'a.fits')
        done = finish(service.call('archive.import', {'token': preview['token'], 'tags': {str(index): PHOTOMETRY}}))
        self.assertEqual(done['state'], 'completed', done.get('problem'))
        rows = {Path(r['relative']).name: r['meta'].get('tag') for r in service.catalog.query({'archive': 'frames'})}
        self.assertEqual(rows, {'a.fits': PHOTOMETRY, 'c.fits': None})
        with self.assertRaisesRegex(ValueError, 'изтекъл'):
            service.call('archive.import', {'token': preview['token'], 'tags': {}})

    def test_import_unknown_token_rejected(self):
        previews = tags.ImportPreviews()
        token = previews.add(['x.fits'], None)
        self.assertEqual(previews.take(token)['paths'], ['x.fits'])
        with self.assertRaisesRegex(ValueError, 'изтекъл'):
            previews.take(token)
        with self.assertRaisesRegex(ValueError, 'изтекъл'):
            previews.take('missing')


if __name__ == '__main__':
    unittest.main()
