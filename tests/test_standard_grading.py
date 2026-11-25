import unittest

from Backend.Analysis.grading import standard_review


def frame(**changes):
    return dict(width=512, height=512, meta={'exposure': 30, 'filter': 'L'}, stars=100,
                fwhm=3.5, roundness=.95, skyMedian=1500, noise=10, brightStarSignal=100,
                warnings=[]) | changes


def types(row):
    return {warning['type'] for warning in row['warnings']}


class StandardGradingTests(unittest.TestCase):
    def test_sky_brightness_and_noise_have_independent_limits(self):
        bright = frame(skyMedian=2900, noise=14)
        bright_extreme = frame(skyMedian=6550, noise=21)
        noisy = frame(noise=30)
        gradient = frame(warnings=[{'type': 'gradient'}])
        standard_review([frame() for _ in range(10)] + [bright, bright_extreme, noisy, gradient], {})
        self.assertEqual(bright['standardStatus'], 'ok')
        self.assertEqual(types(bright_extreme), {'glare'})
        self.assertEqual(types(noisy), {'noise'})
        self.assertEqual(gradient['standardStatus'], 'ok')

    def test_dim_stars_need_another_signal_and_other_exposures_are_separate(self):
        dim = frame(brightStarSignal=30)
        dim_noisy = frame(brightStarSignal=30, noise=30)
        short = frame(meta={'exposure': 5, 'filter': 'L'}, brightStarSignal=5, stars=15)
        standard_review([frame() for _ in range(10)] + [dim, dim_noisy, short], {})
        self.assertEqual(dim['standardStatus'], 'ok')
        self.assertEqual(types(dim_noisy), {'noise', 'cloud'})
        self.assertEqual(short['standardStatus'], 'limited')

    def test_read_errors_and_ai_warnings_keep_their_own_status(self):
        ai = frame(warnings=[{'type': 'cloud', 'origin': 'ai', 'severity': 'high'}])
        unreadable = frame(error='cannot read', warnings=[{'type': 'unreadable'}])
        soft = frame(fwhm=8, decision='excluded')
        rows = [frame() for _ in range(4)] + [ai, unreadable, soft]
        standard_review(rows, {})
        standard_review(rows, {})
        self.assertEqual(ai['standardStatus'], 'ok')
        self.assertEqual(ai['recommended'], 'exclude')
        self.assertEqual(unreadable['standardStatus'], 'unreadable')
        self.assertEqual(soft['decision'], 'excluded')
        self.assertEqual([w['type'] for w in soft['warnings']], ['focus'])


if __name__ == '__main__':
    unittest.main()
