"""Privacy boundaries and contents of the exact proposed distribution."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('prepare_publication', ROOT/'Tools'/'prepare_publication.py')
publication = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publication)


class PublicationTests(unittest.TestCase):
    def test_workspace_allowlist_separates_plugins_and_trainer(self):
        has_plugins = (ROOT/'Plugins'/'frame-inspector-ai'/'plugin.json').is_file()
        repositories = publication.plan(core_only=not has_plugins)
        self.assertEqual(len(repositories), 7 if has_plugins else 1)
        core = {f['destination'] for f in repositories['astro-archive']}
        self.assertIn('start.pyw', core)
        self.assertFalse(any(p.startswith('Plugins/') or 'AI Trainer' in p for p in core))
        self.assertNotIn('astro-archive-plugin-ai-training', repositories)
        if has_plugins:
            inspector = {f['destination'] for f in repositories['astro-archive-plugin-frame-inspector-ai']}
            self.assertTrue({'plugin.json', 'requirements-ai.txt', 'requirements-gpu.txt'} <= inspector)
            self.assertFalse(any(p.startswith('Trainer/') for p in inspector))
        for name, files in repositories.items():
            destinations = {f['destination'] for f in files}
            rights_document = 'docs/PROJECT-RIGHTS.md' if name == 'astro-archive' else 'LICENSE-STATUS.md'
            self.assertTrue({'README.md', rights_document, '.gitignore'} <= destinations)
            if name != 'astro-archive':
                self.assertIn('plugin.json', destinations)

    def test_rejects_data_even_if_allowlisted(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder:
            root = Path(folder)
            for relative in ('Backend/Cache/profile.json', 'observation.fits', '.env', 'tokens.json'):
                path = root/relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('{}')
                with self.subTest(relative=relative), self.assertRaises(ValueError):
                    publication.source_file(root, relative)
            for relative in ('../outside.py', '/outside.py', 'C:/outside.py', 'a/../outside.py'):
                with self.subTest(relative=relative), self.assertRaises(ValueError):
                    publication.safe_relative(relative)

    def test_rejects_known_secret_without_echoing_it(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder:
            source = Path(folder)/'example.py'
            secret = 'gh' + 'p_' + 'a'*36
            source.write_text('value = '+repr(secret), encoding='utf-8')
            findings = publication.inspect_text(source)
            self.assertEqual(findings, [dict(kind='GitHub token', line=1)])
            self.assertNotIn(secret, str(findings))

    def test_export_is_exact_and_preserves_private_files(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder:
            root = Path(folder)
            (root/'program.py').write_text('print("hello")\n', encoding='utf-8')
            private = root/'Backend'/'Cache'/'profile.json'
            private.parent.mkdir(parents=True)
            private.write_text('{"private": true}')
            manifest = root/'allowlist.json'
            manifest.write_text(json.dumps(dict(format=1, repositories={
                'astro-archive': [dict(source='program.py', destination='program.py')]})))
            output, repositories, count = publication.build(root, manifest)
            self.assertTrue(private.exists())
            self.assertEqual(private.read_text(), '{"private": true}')
            with zipfile.ZipFile(output/'astro-archive.zip') as archive:
                self.assertEqual(archive.namelist(), ['astro-archive/program.py'])
            self.assertIn('Backend/Cache/profile.json', (output/'excluded-files.csv').read_text(encoding='utf-8-sig'))
            self.assertGreater(count, 0)


if __name__ == '__main__':
    unittest.main()
