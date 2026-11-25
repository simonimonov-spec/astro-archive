import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from Bridge.Plugins.inspection_services import InspectionServices

RID = 'a' * 32
NEW_TYPES = {'cloud', 'satellite', 'elongation', 'focus', 'flare', 'gradient', 'obstruction', 'quality', 'light'}


class FakeSession:
    def __init__(self):
        self.active = False
        self.calls = []

    def analysis_state(self):
        return {}

    def decisions(self):
        return {}

    def inspection_result(self, report_id, ids, **kw):
        self.calls.append((report_id, list(ids), kw))
        return True


class InspectionSdkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / 'Reports').mkdir()
        self.files = {}
        frames = []
        for name in ('a', 'b', 'c'):
            f = root / (name + '.fits')
            f.write_bytes(b'x')
            st = f.stat()
            self.files[name] = f
            frames.append(dict(id=name, warnings=[], decision='keep', sourceSize=st.st_size,
                               sourceMtime=st.st_mtime_ns))
        self.session = FakeSession()
        self.service = SimpleNamespace(
            quality=SimpleNamespace(results={RID: {'id': RID, 'frames': frames}}),
            catalog=SimpleNamespace(path=lambda i: self.files[i]),
            store=SimpleNamespace(folder=root),
            session=self.session,
        )
        self.sdk = InspectionServices(self.service)

    def annotate(self, **warning):
        base = dict(type='cloud', score=0.5, message='x')
        base.update(warning)
        return self.sdk.invoke('p', 'annotate', dict(reportId=RID, annotations=[dict(id='a', warnings=[base])]))

    def test_capabilities_lists_new_types(self):
        r = self.sdk.invoke('p', 'capabilities', {})
        self.assertEqual(set(r['warningTypes']), NEW_TYPES)
        self.assertEqual(r['scopes'], ['frame', 'series'])
        self.assertIs(r['mark'], True)

    def test_annotate_accepts_new_types_and_scope(self):
        self.annotate(type='focus', scope='series')
        w = self.service.quality.results[RID]['frames'][0]['warnings'][0]
        self.assertEqual(w['scope'], 'series')
        self.assertEqual(w['type'], 'focus')

    def test_annotate_default_scope_frame(self):
        self.annotate(type='gradient')
        self.assertEqual(self.service.quality.results[RID]['frames'][0]['warnings'][0]['scope'], 'frame')

    def test_annotate_rejects_unknown_type(self):
        with self.assertRaises(ValueError):
            self.annotate(type='xyz')

    def test_annotate_rejects_bad_scope(self):
        with self.assertRaises(ValueError):
            self.annotate(scope='night')

    def test_mark_selects_exact_ids(self):
        before = copy.deepcopy(self.service.quality.results[RID])
        r = self.sdk.invoke('p', 'mark', dict(reportId=RID, ids=['a', 'c']))
        self.assertEqual(r, {'marked': 2})
        self.assertEqual(self.session.calls, [(RID, ['a', 'c'], dict(highlight=True))])
        self.assertEqual(self.service.quality.results[RID], before)

    def test_mark_rejects_foreign_ids(self):
        with self.assertRaises(ValueError):
            self.sdk.invoke('p', 'mark', dict(reportId=RID, ids=['a', 'zzz']))
        self.assertEqual(self.session.calls, [])

    def test_mark_raises_when_session_busy(self):
        self.session.inspection_result = lambda *a, **k: False
        with self.assertRaises(ValueError):
            self.sdk.invoke('p', 'mark', dict(reportId=RID, ids=['a']))

    def test_mark_rejects_unknown_report(self):
        with self.assertRaises(ValueError):
            self.sdk.invoke('p', 'mark', dict(reportId='b' * 32, ids=['a']))

    def test_register_preserves_transform_with_more_than_forty_stars(self):
        points = np.random.default_rng(98).uniform(30, 470, size=(48, 2))
        frames = self.service.quality.results[RID]['frames']
        for frame, positions in zip(frames, (points, points + [16, -9])):
            frame.update(width=500, height=500,
                         starList=[dict(x=x, y=y) for x, y in positions])

        result = self.sdk.invoke('p', 'register', dict(reportId=RID, sourceId='a', targetId='b'))

        self.assertEqual(result['matched'], len(points))
        self.assertLess(result['rms'], 1e-8)
        np.testing.assert_allclose(result['matrix'], [[1, 0, 16], [0, 1, -9], [0, 0, 1]], atol=1e-8)


if __name__ == '__main__':
    unittest.main()
