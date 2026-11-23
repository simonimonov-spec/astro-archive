import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from Tools import apply_update as updater


class ApplyUpdateTest(unittest.TestCase):
    def setUp(self):
        workspace = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(workspace) / 'installation'
        self.stage = self.root / '.runtime' / 'app-update'
        self.stage.mkdir(parents=True)
        self.package = Path(workspace) / 'release.zip'
        self.payload = {name: b'new' for name in updater.REQUIRED}
        self.payload.update({'UI/unchanged.js': b'same', 'UI/added.js': b'added'})
        for name, value in {
            'AstroArchive.exe': b'old', 'UI/unchanged.js': b'same',
            'Backend/Cache/data/profile.json': b'profile', 'Plugins/local.py': b'plugin',
        }.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)

    def write_package(self):
        with ZipFile(self.package, 'w') as archive:
            for name, value in self.payload.items():
                archive.writestr('AstroArchive_New/' + name, value)
        return updater.digest(self.package)

    def test_changed_files_only_and_personal_data_preserved(self):
        expected = self.write_package()
        common = self.root / 'UI/unchanged.js'
        timestamp = common.stat().st_mtime_ns
        updater.apply(self.root, self.stage, self.package, expected)
        self.assertEqual(common.stat().st_mtime_ns, timestamp)
        self.assertEqual((self.root / 'UI/added.js').read_bytes(), b'added')
        self.assertEqual((self.root / 'AstroArchive.exe').read_bytes(), b'new')
        self.assertEqual((self.root / 'Backend/Cache/data/profile.json').read_bytes(), b'profile')
        self.assertEqual((self.root / 'Plugins/local.py').read_bytes(), b'plugin')

    def test_rejects_bad_checksum_and_unsafe_package_before_replacing(self):
        with self.assertRaisesRegex(ValueError, 'Контролната сума'):
            self.write_package()
            updater.apply(self.root, self.stage, self.package, '0' * 64)
        for path in ('../outside.txt', 'Backend/Cache/data/profile.json', 'UI/nul.py', 'ui/added.js'):
            with self.subTest(path=path):
                self.payload[path] = b'bad'
                with self.assertRaises(ValueError):
                    updater.apply(self.root, self.stage, self.package, self.write_package())
                self.payload.pop(path)
        self.assertEqual((self.root / 'AstroArchive.exe').read_bytes(), b'old')
        self.assertFalse((self.root / 'UI/added.js').exists())

    def test_replacement_error_and_interruption_restore_files(self):
        expected = self.write_package()
        replace = updater.os.replace

        for error in (PermissionError('locked launcher'), KeyboardInterrupt()):
            with self.subTest(error=error):
                def interrupt(source, target):
                    if target == self.root / 'AstroArchive.exe':
                        raise error
                    return replace(source, target)

                with patch.object(updater.os, 'replace', side_effect=interrupt):
                    with self.assertRaises(type(error)):
                        updater.apply(self.root, self.stage, self.package, expected)
                pending = self.stage / 'pending.json'
                self.assertEqual(pending.exists(), isinstance(error, KeyboardInterrupt))
                if pending.exists():
                    updater.apply(self.root, self.stage)
                self.assertEqual((self.root / 'AstroArchive.exe').read_bytes(), b'old')
                self.assertFalse((self.root / 'UI/added.js').exists())
                self.assertFalse(pending.exists())
