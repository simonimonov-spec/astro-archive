"""AI index percent: 100 * prod(1 - weight * risk) over available risks."""
DEFAULT_WEIGHTS = dict(cloud=.9, satellite=.8, elongation=.85, focus=.85, flare=.65,
                       gradient=.55, obstruction=1., noise=.25, defects=.15, severe=.85)
# 'severe' is the risk of a frame-check 'quality' problem (severely degraded frame).
# Risks of the current analyzers -> index keys.
FACTOR_TO_RISK = dict(noise='noise', defects='defects', trails='satellite', light='gradient', artifacts='flare')


def index(risks, weights=None):
    w = DEFAULT_WEIGHTS if weights is None else weights
    value = 1.
    known = 0
    for key, risk in risks.items():
        weight = w.get(key, DEFAULT_WEIGHTS.get(key))
        if risk is None or weight is None:
            continue
        known += 1
        value *= 1 - float(weight) * min(1., max(0., float(risk)))
    if not known:
        return None
    return max(0., min(100., 100. * value))
