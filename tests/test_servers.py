"""Regression checks for Astrometrica's public server list formats."""
import importlib
from pathlib import Path
import sys
import tempfile
import types
import unittest


PACKAGE = '_iasc_server_tests'
package = types.ModuleType(PACKAGE)
package.__path__ = [str(Path(__file__).resolve().parents[1])]
sys.modules[PACKAGE] = package
servers = importlib.import_module(PACKAGE + '.servers')


class ServerListTests(unittest.TestCase):
    def definitions(self, vizier, mpc=None):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / 'VizierServers.dat').write_text(vizier, encoding='utf-8-sig')
            if mpc is not None:
                (folder / 'MPCOrbServers.dat').write_text(mpc, encoding='utf-8')
            return servers.definitions(str(folder / 'Astrometrica.exe'))

    def test_bare_hostname_and_explicit_protocols(self):
        entries, source = self.definitions(
            'CDS;vizier.cds.unistra.fr;/viz-bin/VizieR\n'
            'Tokyo;http://vizier.nao.ac.jp;/viz-bin/VizieR\n'
            'India;https://vizier.iucaa.in;/viz-bin/VizieR\n',
            'MPC;https://www.minorplanetcenter.net;/iau/MPCORB;;\n')
        self.assertEqual([entry['base'] for entry in entries], [
            'http://vizier.cds.unistra.fr', 'http://vizier.nao.ac.jp',
            'https://vizier.iucaa.in', 'https://www.minorplanetcenter.net'])
        self.assertIn('VizierServers.dat', source)
        self.assertIn('MPCOrbServers.dat', source)
        self.assertTrue(servers.probe_url(entries[0], 'listed', 'gaia').startswith('http://'))
        self.assertTrue(servers.probe_url(entries[0], 'https', 'gaia').startswith('https://'))

    def test_bare_hosts_still_enforce_public_service_restrictions(self):
        entries, _ = self.definitions('\n'.join([
            'Local;127.0.0.1;/viz-bin/VizieR',
            'LAN;192.168.1.1;/viz-bin/VizieR',
            'Other;example.com;/viz-bin/VizieR',
            'Credentials;user:password@vizier.cds.unistra.fr;/viz-bin/VizieR',
            'Port;vizier.cds.unistra.fr:8080;/viz-bin/VizieR',
            'Query;vizier.cds.unistra.fr?x=1;/viz-bin/VizieR',
            'Path;vizier.cds.unistra.fr/other;/viz-bin/VizieR',
            'Endpoint;vizier.cds.unistra.fr;/other',
            'CDS;vizier.cds.unistra.fr;/viz-bin/VizieR',
            'Duplicate;https://vizier.cds.unistra.fr;/viz-bin/VizieR',
        ]))
        self.assertEqual([entry['label'] for entry in entries if entry['kind'] == 'vizier'], ['CDS'])

    def test_missing_installation_uses_defaults(self):
        entries, _ = servers.definitions('')
        self.assertEqual(len([entry for entry in entries if entry['kind'] == 'vizier']), len(servers.DEFAULTS))


if __name__ == '__main__':
    unittest.main()
