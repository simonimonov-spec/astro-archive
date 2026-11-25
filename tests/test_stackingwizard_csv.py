from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from Backend.Analysis.quality import Quality
from Backend.Analysis.stackingwizard_csv import match_grades
from Backend.Foundation.runtime import Store, atomic_json


class StackingWizardCSVTests(unittest.TestCase):
    def setUp(self):
        self.frames = [
            dict(id='light1', name='Café, 1.fits', decision='excluded', warnings=[]),
            dict(id='light2', name='next.fits', warnings=[]),
        ]
        self.csv = '\ufefffile,grade,noise,stars,verdict\r\n"CAFE\u0301, 1.FITS",0,0.000158362,130,good\r\n'

    def test_preserves_zero_and_unrounded_measurements_with_partial_match(self):
        imported = match_grades(self.csv + 'other.fits,90,0.01,22,good\r\n', self.frames)
        self.assertEqual(imported['matched'], 1)
        self.assertEqual(imported['csvFrames'], 2)
        self.assertEqual(imported['columns'], ['grade', 'noise', 'stars'])
        self.assertEqual(imported['frames'], {'light1': {'grade': 0, 'noise': .000158362, 'stars': 130}})

    def test_rejects_duplicates_and_non_finite_measurements(self):
        cases = [
            (self.csv + '"café, 1.fits",100,0,1,good\r\n', self.frames),
            (self.csv, self.frames + [{'id': 'duplicate', 'name': 'Café, 1.fits'}]),
            (self.csv.replace('0.000158362', 'NaN'), self.frames),
            ('file,grade\nunknown.fits,90\n', self.frames),
            ('file,score\nnext.fits,50\n', self.frames),
        ]
        for content, frames in cases:
            with self.subTest(content=content), self.assertRaises(ValueError):
                match_grades(content, frames)

    def test_attachment_survives_reopening_and_is_removed_with_report(self):
        with TemporaryDirectory() as folder:
            store = Store(Path(folder))
            catalog = SimpleNamespace(store=store)
            ident = '1' * 32
            source = dict(id=ident, standardAlgorithm='standard-review-1', frames=self.frames)
            path = store.folder / 'Reports' / (ident + '.json')
            atomic_json(path, source)
            unchanged = path.read_bytes()
            quality = Quality(catalog, None)
            data = quality.import_stackingwizard(ident, self.csv)
            reopened = Quality(catalog, None).display_result(ident, compact=True)
            self.assertEqual(reopened['stackingWizard'], data)
            self.assertEqual(reopened['frames'][0]['decision'], 'excluded')
            with self.assertRaises(ValueError):
                quality.import_stackingwizard(ident, 'file,grade\nabsent.fits,20')
            self.assertEqual(quality.display_result(ident)['stackingWizard'], data)
            self.assertEqual(path.read_bytes(), unchanged)
            quality.delete(ident)
            self.assertFalse(path.exists())
            self.assertFalse((path.parent / (ident + '.stackingwizard.json')).exists())


if __name__ == '__main__':
    unittest.main()
