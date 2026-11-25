"""Per-frame checks: shake/elongation, cloud, obstruction, flare, gradient, severe cases.

Every rule returns nothing when its inputs are missing or unreliable. Scores are
monotone in the threshold excess: exactly at the threshold the score is 0.6
(medium), saturating at 1.0; >= 0.85 is high. Star angles are in radians.
"""
from __future__ import annotations

import math
import statistics

try:  # loaded as a plugin package, or as a top-level module in tests
    from .problems import problem
except ImportError:
    from problems import problem

DEFAULTS = dict(elongationRatio=1.3, angleCoherence=0.6, minStarsShape=10, cloudTransparencyLoss=0.25,
                cloudStarLoss=0.3, cloudBackgroundRatio=1.1, obstructionDarkArea=0.02,
                obstructionEdgeContact=0.2, flareMaxArea=0.15, severeStarRatio=0.5, severeHfrRatio=1.4)
MEDIUM = 0.6
MIN_GROUP = 3
ROUNDNESS_FLOOR = 0.02  # group median of (1-roundness) never below this, to avoid division by ~0


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _score(excess_ratio, span=1.0):
    """0.6 at the threshold, rising monotonically to 1.0 at span (relative excess)."""
    return MEDIUM + (1 - MEDIUM) * min(1.0, max(0.0, excess_ratio) / span)


def _severity(score):
    return 'high' if score >= 0.85 else 'medium' if score >= MEDIUM else None


def _make(kind, score, message, measures):
    sev = _severity(score)
    return problem(kind, score, sev, message, measures) if sev else None


def _group_median(group, getter):
    values = [v for v in (_num(getter(r)) for r in group or []) if v is not None]
    return statistics.median(values) if len(values) >= MIN_GROUP else None


def _stars(row):
    out = []
    for s in row.get('starList') or []:
        a, r = _num(s.get('angle')), _num(s.get('roundness'))
        if a is not None and r is not None:
            out.append((a, r))
    return out


def _coherence(angles):
    if not angles:
        return None, None
    c = sum(math.cos(2 * a) for a in angles) / len(angles)
    s = sum(math.sin(2 * a) for a in angles) / len(angles)
    return math.hypot(c, s), math.degrees(0.5 * math.atan2(s, c)) % 180


def _group_ecc(group):
    def one(r):
        v = _num(r.get('roundness'))
        if v is not None:
            return 1 - v
        rs = [1 - q for _, q in _stars(r)]
        return statistics.median(rs) if rs else None
    return _group_median(group, one)


def _elongation(row, group, o):
    st = _stars(row)
    if len(st) < o['minStarsShape']:
        return None
    ref = _group_ecc(group)
    if ref is None:
        return None
    ecc = statistics.median(1 - r for _, r in st)
    ratio = ecc / max(ref, ROUNDNESS_FLOOR)
    coh, ang = _coherence([a for a, _ in st])
    if ratio < o['elongationRatio'] or coh < o['angleCoherence']:
        return None
    score = min(_score((ratio - o['elongationRatio']) / o['elongationRatio']),
                _score((coh - o['angleCoherence']) / o['angleCoherence'], 0.5))
    return _make('elongation', score,
                 'Издължени звезди: ексцентрицитет ×%.1f спрямо групата, посока %.0f°' % (ratio, ang),
                 dict(eccentricityRatio=round(ratio, 3), angleDeg=round(ang, 1), coherence=round(coh, 3)))


def _cloud(row, group, o):
    f = row.get('features') or {}
    tl, sl = _num(f.get('transparency_loss')), _num(f.get('star_loss'))
    bg = _num(row.get('background'))
    ref = _group_median(group, lambda r: r.get('background'))
    if bg is None or ref is None or ref <= 0 or (tl is None and sl is None):
        return None
    ratio = bg / ref
    hits = []
    if tl is not None and tl >= o['cloudTransparencyLoss']:
        hits.append((tl - o['cloudTransparencyLoss']) / o['cloudTransparencyLoss'])
    if sl is not None and sl >= o['cloudStarLoss']:
        hits.append((sl - o['cloudStarLoss']) / o['cloudStarLoss'])
    if not hits or ratio < o['cloudBackgroundRatio']:
        return None
    dim = 100 * max(v for v in (tl, sl) if v is not None)
    return _make('cloud', _score(max(hits)),
                 'Облак: звездите са с %.0f%% по-слаби, фонът е ×%.2f спрямо групата' % (dim, ratio),
                 dict(starDimmingPercent=round(dim, 1), transparencyLoss=tl, backgroundRatio=round(ratio, 3)))


def _obstruction(row, o):
    f = row.get('features') or {}
    da, ec, de = (_num(f.get(k)) for k in ('dark_area', 'edge_contact', 'dark_edge'))
    if da is None or ec is None or de is None:
        return None
    if da < o['obstructionDarkArea'] or ec < o['obstructionEdgeContact'] or de <= 0:
        return None
    score = _score(max((da - o['obstructionDarkArea']) / o['obstructionDarkArea'],
                       (ec - o['obstructionEdgeContact']) / o['obstructionEdgeContact']), 2.0)
    return _make('obstruction', score, 'Препятствие: тъмна област %.1f%% от кадъра, докосва ръба' % (100 * da),
                 dict(coveredPercent=round(100 * da, 2), edgeContact=round(ec, 3)))


def _metric(row, fid, name):
    for fac in (row.get('analyzerData') or {}).get('factors') or []:
        if fac.get('id') != fid:
            continue
        risk = _num(fac.get('risk'))
        for m in fac.get('metrics') or []:
            if m.get('name') == name and m.get('measured') is not False:
                v = _num(m.get('value'))
                if v is not None and risk is not None:
                    return v, risk
    return None


def _flare_gradient(row, o):
    out = []
    peak = _metric(row, 'artifacts', 'diffuse_peak_index')
    area = _metric(row, 'artifacts', 'bright_area_fraction')
    grad = _metric(row, 'light', 'gradient_fit_residual')
    art_ok = peak is not None and peak[1] >= MEDIUM
    if art_ok and area is not None and area[0] <= o['flareMaxArea']:
        out.append(_make('flare', peak[1],
                         'Отблясък: локален светъл участък (%.1f%% от кадъра), индекс %.2f' % (100 * area[0], peak[0]),
                         dict(diffuseIndex=round(peak[0], 3), brightAreaPercent=round(100 * area[0], 2))))
    elif grad is not None and grad[1] >= MEDIUM:
        bg = _num(row.get('background'))
        pct = 100 * grad[0] / bg if bg and bg > 0 else None
        text = 'Неравномерен фон: остатък %.0f ADU' % grad[0] + (' (%.1f%% от фона)' % pct if pct is not None else '')
        measures = dict(residualAdu=round(grad[0], 2))
        if pct is not None:
            measures['residualPercentOfBackground'] = round(pct, 2)
        out.append(_make('gradient', grad[1], text + ' след прилягане на равнина', measures))
    elif art_ok and area is not None:  # wide-area artifacts without a qualifying light factor
        out.append(_make('gradient', peak[1], 'Неравномерен фон: светла област %.0f%% от кадъра' % (100 * area[0]),
                         dict(diffuseIndex=round(peak[0], 3), brightAreaPercent=round(100 * area[0], 2))))
    return out


def _severe(row, group, o):
    n, hfr = _num(row.get('stars')), _num(row.get('hfr'))
    ref_n = _group_median(group, lambda r: r.get('stars'))
    ref_h = _group_median(group, lambda r: r.get('hfr'))
    if None in (n, hfr, ref_n, ref_h) or ref_n <= 0 or ref_h <= 0:
        return None
    sr, hr = n / ref_n, hfr / ref_h
    if sr > o['severeStarRatio'] or hr < o['severeHfrRatio']:
        return None
    score = max(_score((o['severeStarRatio'] - sr) / o['severeStarRatio']),
                _score((hr - o['severeHfrRatio']) / o['severeHfrRatio']))
    measures = dict(starRatio=round(sr, 3), hfrRatio=round(hr, 3))
    st = _stars(row)
    if len(st) < o['minStarsShape']:
        return _make('quality', score, 'Тежко влошен кадър: звезди ×%.2f, HFR ×%.2f спрямо групата' % (sr, hr), measures)
    coh, ang = _coherence([a for a, _ in st])
    if coh >= o['angleCoherence']:
        return _make('elongation', score,
                     'Издължени звезди при тежка загуба: звезди ×%.2f, HFR ×%.2f, посока %.0f°' % (sr, hr, ang),
                     dict(measures, angleDeg=round(ang, 1), coherence=round(coh, 3)))
    return _make('focus', score, 'Разфокус: звезди ×%.2f, HFR ×%.2f спрямо групата' % (sr, hr), measures)


def check(row, group, options=None):
    o = dict(DEFAULTS)
    o.update(options or {})
    found = [_elongation(row, group, o), _cloud(row, group, o), _obstruction(row, o), _severe(row, group, o)]
    found += _flare_gradient(row, o)
    best = {}
    for p in found:
        if p and (p['type'] not in best or p['score'] > best[p['type']]['score']):
            best[p['type']] = p
    return list(best.values())
