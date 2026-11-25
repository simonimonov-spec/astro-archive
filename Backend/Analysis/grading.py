"""Standard frame review based on measurements from comparable exposures."""
from collections import defaultdict
from math import isfinite, sqrt
from statistics import median, quantiles

VERSION = 'standard-review-1'
BASE_WARNINGS = {'no-psf', 'bright-track', 'obstruction', 'unreadable', 'unavailable'}


def comparable_key(row):
    meta = row.get('meta') or {}
    header = meta.get('headers') or {}
    return (
        meta.get('object'), meta.get('night'), meta.get('telescope'), meta.get('sensor'),
        meta.get('filter'), meta.get('exposure'), row.get('width'), row.get('height'),
        header.get('XBINNING'), header.get('YBINNING'), header.get('GAIN'),
        header.get('OFFSET'), header.get('PANEL'),
    )


def finite(value):
    return isinstance(value, (int, float)) and isfinite(value)


def baseline(values):
    values = [value for value in values if finite(value)]
    return median(values) if len(values) >= 3 else None


def standard_review(frames, settings):
    def limit(name, default):
        try:
            value = float(settings.get(name, default))
        except (TypeError, ValueError):
            return default
        return value if isfinite(value) and value >= 0 else default

    star_fraction = limit('starsPercent', 65) / 100
    focus_ratio = limit('fwhmRatio', 1.5)
    background_ratio = limit('backgroundRatio', 1.25)
    min_roundness = limit('roundness', .6)
    absolute_width = limit('absoluteFwhm', 0)
    check_sky = settings.get('checkBackground', True)
    check_stars = settings.get('checkStars', True)
    check_focus = settings.get('checkFwhm', True)
    check_roundness = settings.get('checkRoundness', True)
    check_absolute = settings.get('checkAbsolute', True)
    groups = defaultdict(list)
    for row in frames:
        row['warnings'] = [
            warning for warning in row.get('warnings', [])
            if warning.get('origin') == 'ai' or warning.get('type') in BASE_WARNINGS
        ]
        if row.get('error'):
            row.update(standardStatus='unreadable', recommended='exclude',
                       statusText='Кадърът не може да се измери.')
        else:
            groups[comparable_key(row)].append(row)

    for group in groups.values():
        sky = [row.get('skyMedian', row.get('background')) for row in group]
        typical_sky = baseline(sky)
        if typical_sky is not None:
            low_sky = quantiles([value for value in sky if finite(value)], n=4, method='inclusive')[0]
        else:
            low_sky = None

        adjusted_noise = []
        for row, background in zip(group, sky):
            noise = row.get('noise')
            # For positive, comparable sky levels, allow the increase expected
            # from photon noise. Near or below zero, compare measured noise directly.
            if finite(noise) and finite(background) and typical_sky and typical_sky > 0 and background > typical_sky:
                noise /= sqrt(background / typical_sky)
            adjusted_noise.append(noise)
        typical_noise = baseline(adjusted_noise)
        noise_limit = None
        if typical_noise is not None and typical_noise > 0:
            mad = baseline(abs(n - typical_noise) for n in adjusted_noise if finite(n))
            noise_limit = typical_noise + max(.5 * typical_noise, 3 * 1.4826 * mad)
        typical_width = baseline(row.get('fwhm') for row in group)
        registered = [row for row in group if row.get('transform') is not None]
        star_group = registered if registered else group
        typical_stars = baseline(row.get('stars') for row in star_group)
        typical_signal = baseline(row.get('brightStarSignal') for row in star_group)

        for row, background, noise in zip(group, sky, adjusted_noise):
            findings = row['warnings']

            def flag(kind, message, severity='medium'):
                findings.append(dict(type=kind, severity=severity, confidence='candidate', message=message))

            width, shape, count = row.get('fwhm'), row.get('roundness'), row.get('stars')
            noisy = finite(noise) and noise_limit is not None and noise > noise_limit
            bright = (check_sky and finite(background) and typical_sky is not None and typical_sky > 0
                      and background > background_ratio * typical_sky)
            very_bright = bright and low_sky > 0 and background > 3 * low_sky
            compare_stars = check_stars and (not registered or row.get('transform') is not None)
            few = compare_stars and finite(count) and typical_stars is not None and typical_stars > 12 and count < star_fraction * typical_stars
            signal = row.get('brightStarSignal')
            dim = compare_stars and finite(signal) and typical_signal is not None and typical_signal > 0 and signal < star_fraction * typical_signal
            if few or (dim and (noisy or bright)):
                causes = []
                if few:
                    causes.append('по-малко открити звезди')
                if dim:
                    causes.append('по-слаб сигнал на ярките звезди')
                flag('cloud', 'Възможна облачност. ' + ', '.join(causes).capitalize() + '.')
            if noisy:
                flag('noise', 'Шумът е необичайно висок за яркостта на фона в тази серия.')
            if very_bright:
                flag('glare', 'Фонът е над три пъти по-светъл от долната четвърт на серията.')
            if check_stars and finite(count) and 0 < count < 6:
                flag('few-stars', 'Измерени са под шест звезди.')
            if check_focus and finite(width) and typical_width and width > focus_ratio * typical_width:
                flag('focus', 'Звездите са по-широки от допустимото спрямо медианата на серията.', 'high')
            if check_roundness and finite(shape) and shape < min_roundness:
                flag('elongation', 'Кръглостта на звездите е под зададения минимум.', 'high')
            if check_absolute and absolute_width and finite(width) and width > absolute_width:
                flag('absolute-focus', 'FWHM е над зададения абсолютен праг.', 'high')

            review = any(warning.get('origin') != 'ai' for warning in findings)
            row['standardStatus'] = 'review' if review else 'limited' if typical_width is None else 'ok'
            row['recommended'] = ('exclude' if any(w.get('severity') == 'high' for w in findings)
                                  else 'review' if findings else 'approved')
            row['statusText'] = ('За преглед' if findings else 'Недостатъчно сравними кадри'
                                 if row['standardStatus'] == 'limited' else 'Без отклонения')
