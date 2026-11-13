"""Installation isolation and launcher handshake boundaries."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from Bridge.Desktop.identity import installation_id, matches, signal_ready, window_title
from Bridge.Desktop.single_instance import mutex_name
from Bridge.Desktop.dialogs import native_dialog

ROOT = Path(__file__).resolve().parents[1]


class LauncherIsolationTests(unittest.TestCase):
    def test_copies_do_not_share_identity_mutex_or_window(self):
        first, second = ROOT / 'copy-a', ROOT / 'copy-b'
        self.assertNotEqual(installation_id(first), installation_id(second))
        self.assertNotEqual(mutex_name(first), mutex_name(second))
        self.assertNotEqual(window_title(first), window_title(second))
        self.assertTrue(matches({'installation': installation_id(first)}, first))
        self.assertFalse(matches({'installation': installation_id(first)}, second))
        self.assertFalse(matches({'app': 'Astro Archive'}, first))
        self.assertFalse(matches(None, first))

    def test_canonical_paths_are_same_copy(self):
        self.assertEqual(installation_id(ROOT), installation_id(ROOT / 'Bridge' / '..'))

    def test_ready_file_is_limited_to_own_cache(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'publication-review') as temp:
            root = Path(temp)
            folder = root / 'Backend' / 'Cache' / 'data'
            folder.mkdir(parents=True)
            ready = folder / 'launcher-ready-test.json'
            with patch.dict(os.environ, {'ASTRO_ARCHIVE_READY': str(ready)}):
                signal_ready(root)
                self.assertNotIn('ASTRO_ARCHIVE_READY', os.environ)
            self.assertEqual(json.loads(ready.read_text()), {'ready': True})
            for invalid in (root / 'launcher-ready-test.json', folder / 'profile.json'):
                with self.subTest(path=invalid), patch.dict(os.environ, {'ASTRO_ARCHIVE_READY': str(invalid)}):
                    with self.assertRaises(ValueError):
                        signal_ready(root)
                self.assertFalse(invalid.exists())

    def test_direct_developer_start_needs_no_handshake(self):
        with patch.dict(os.environ, {}, clear=True):
            signal_ready(ROOT)

    def test_native_dialog_preserves_unicode_paths_and_multiple_files(self):
        expected = ['D:/Снимки/кадър 1.fits', 'D:/Снимки/кадър 2.fits']
        response = SimpleNamespace(returncode=0, stdout=json.dumps({'value': expected}))
        with patch('Bridge.Desktop.dialogs.subprocess.run', return_value=response) as run:
            self.assertEqual(native_dialog(ROOT / 'AstroArchive.exe', 'files', {'title': 'Избери кадри'}), expected)
        args, kwargs = run.call_args
        self.assertEqual(args[0], [str(ROOT / 'AstroArchive.exe'), '--native-dialog'])
        self.assertEqual(json.loads(kwargs['input'])['options']['title'], 'Избери кадри')

    def test_native_dialog_cancellation_and_errors(self):
        with patch('Bridge.Desktop.dialogs.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout='{"value":[]}')):
            self.assertEqual(native_dialog(ROOT / 'AstroArchive.exe', 'files', {}), [])
        with patch('Bridge.Desktop.dialogs.subprocess.run', return_value=SimpleNamespace(returncode=1, stdout='{"error":"failed"}')):
            with self.assertRaisesRegex(RuntimeError, 'failed'):
                native_dialog(ROOT / 'AstroArchive.exe', 'file', {})


if __name__ == '__main__':
    unittest.main()
