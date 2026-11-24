# Beta 1.3 UI (в разработка, не е издание) · виж docs/BETA-1.3-UI.md
"""Window start geometry and remembered placement, without touching real windows."""
import tempfile
import unittest
from pathlib import Path

from Bridge.Desktop import window_placement as wp


class DefaultGeometryTests(unittest.TestCase):
    def test_default_is_centered_16_9_inside_work_area(self):
        work = (0, 0, 1920, 1040)
        left, top, right, bottom = wp.default_rect(work)
        width, height = right - left, bottom - top
        self.assertAlmostEqual(width / height, 16 / 9, delta=0.01)
        self.assertLessEqual(width, 1920)
        self.assertLessEqual(height, 1040)
        self.assertLessEqual(abs((left + right) / 2 - 960), 1)
        self.assertLessEqual(abs((top + bottom) / 2 - 520), 1)

    def test_default_respects_monitor_offset_and_portrait_screens(self):
        work = (-1080, 200, 0, 2120)  # portrait monitor left of the primary
        left, top, right, bottom = wp.default_rect(work)
        self.assertGreaterEqual(left, -1080)
        self.assertLessEqual(right, 0)
        self.assertGreaterEqual(top, 200)
        self.assertLessEqual(bottom, 2120)
        self.assertAlmostEqual((right - left) / (bottom - top), 16 / 9, delta=0.01)


class RememberedPlacementTests(unittest.TestCase):
    monitors = [(0, 0, 1920, 1040)]

    def test_saved_placement_round_trips(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'window-placement.json'
            wp.save(path, dict(rect=[100, 80, 1700, 980], maximized=True))
            self.assertEqual(wp.load(path), dict(rect=[100, 80, 1700, 980], maximized=True))

    def test_missing_or_broken_file_gives_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'window-placement.json'
            self.assertIsNone(wp.load(path))
            path.write_text('{broken', encoding='utf-8')
            self.assertIsNone(wp.load(path))
            path.write_text('{"rect":[1,2,"x",4]}', encoding='utf-8')
            self.assertIsNone(wp.load(path))

    def test_choose_uses_saved_rect_when_visible(self):
        saved = dict(rect=[100, 80, 1700, 980], maximized=True)
        self.assertEqual(wp.choose(saved, self.monitors, self.monitors[0]), saved)

    def test_choose_falls_back_to_center_when_monitor_is_gone(self):
        saved = dict(rect=[3000, 80, 4600, 980], maximized=False)
        chosen = wp.choose(saved, self.monitors, self.monitors[0])
        self.assertEqual(chosen, dict(rect=list(wp.default_rect(self.monitors[0])), maximized=False))

    def test_choose_keeps_maximized_even_if_restore_rect_is_off_screen(self):
        saved = dict(rect=[3000, 80, 4600, 980], maximized=True)
        chosen = wp.choose(saved, self.monitors, self.monitors[0])
        self.assertTrue(chosen['maximized'])
        self.assertEqual(chosen['rect'], list(wp.default_rect(self.monitors[0])))

    def test_choose_rejects_tiny_windows(self):
        saved = dict(rect=[100, 100, 150, 130], maximized=False)
        self.assertEqual(wp.choose(saved, self.monitors, self.monitors[0])['rect'], list(wp.default_rect(self.monitors[0])))


class ObservedStateTests(unittest.TestCase):
    monitor = (0, 0, 1920, 1080)

    def test_normal_window(self):
        state = wp.observed(showcmd=wp.SW_SHOWNORMAL, flags=0, normal=(10, 20, 1610, 920), window=(10, 20, 1610, 920), monitor=self.monitor, previous=None)
        self.assertEqual(state, dict(rect=[10, 20, 1610, 920], maximized=False))

    def test_maximized_keeps_restore_rect(self):
        state = wp.observed(showcmd=wp.SW_SHOWMAXIMIZED, flags=0, normal=(10, 20, 1610, 920), window=(-8, -8, 1928, 1048), monitor=self.monitor, previous=None)
        self.assertEqual(state, dict(rect=[10, 20, 1610, 920], maximized=True))

    def test_minimized_is_remembered_as_what_it_restores_to(self):
        restored_max = wp.observed(showcmd=wp.SW_SHOWMINIMIZED, flags=wp.WPF_RESTORETOMAXIMIZED, normal=(10, 20, 1610, 920), window=(0, 0, 0, 0), monitor=self.monitor, previous=None)
        self.assertEqual(restored_max, dict(rect=[10, 20, 1610, 920], maximized=True))
        restored_normal = wp.observed(showcmd=wp.SW_SHOWMINIMIZED, flags=0, normal=(10, 20, 1610, 920), window=(0, 0, 0, 0), monitor=self.monitor, previous=None)
        self.assertEqual(restored_normal, dict(rect=[10, 20, 1610, 920], maximized=False))

    def test_fullscreen_counts_as_maximized_and_keeps_previous_restore_rect(self):
        previous = dict(rect=[10, 20, 1610, 920], maximized=False)
        state = wp.observed(showcmd=wp.SW_SHOWNORMAL, flags=0, normal=(0, 0, 1920, 1080), window=(0, 0, 1920, 1080), monitor=self.monitor, previous=previous)
        self.assertEqual(state, dict(rect=[10, 20, 1610, 920], maximized=True))


if __name__ == '__main__':
    unittest.main()
