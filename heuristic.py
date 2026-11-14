"""Transparent confidence-weighted fusion. No learned weights."""
from __future__ import annotations

WEIGHTS = dict(noise=.18, stars=.27, defects=.16, trails=.16, objects=0., light=.13, artifacts=.10)


def fuse(factors):
    usable = [f for f in factors if f['status'] != 'unavailable' and f['risk'] is not None]
    total = sum(WEIGHTS[f['id']] * f['confidence'] for f in usable)
    quality = None if total <= 0 else 1 - sum(WEIGHTS[f['id']] * f['confidence'] * f['risk'] for f in usable) / total
    confidence = sum(WEIGHTS[f['id']] * f['confidence'] for f in usable)
    contributions = {f['id']: (-WEIGHTS[f['id']] * f['confidence'] * f['risk'] / total if total and f['risk'] is not None else None) for f in factors}
    return quality, confidence, contributions
