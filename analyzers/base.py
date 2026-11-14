"""Shared analyzer contract. No archive, UI or application imports."""
from __future__ import annotations

import math

FACTOR_IDS = ("noise", "stars", "defects", "trails", "objects", "light", "artifacts")
STATUSES = ("measured", "estimated", "unavailable")
VERSION = "1.0"


def finite(value):
    """True only for finite int/float values; bool is not a measurement."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def number(value):
    return float(value) if finite(value) else None


def clamp01(value):
    return max(0.0, min(1.0, float(value)))


def safe_ratio(value, baseline):
    """value / baseline, or None when either side is missing/non-positive."""
    if not finite(value) or not finite(baseline) or float(baseline) <= 0:
        return None
    return float(value) / float(baseline)


def median(values):
    items = sorted(float(value) for value in values if finite(value))
    if not items:
        return None
    middle = len(items) // 2
    if len(items) % 2:
        return items[middle]
    return 0.5 * (items[middle - 1] + items[middle])


def weighted(values):
    """Weighted mean of (value, weight) pairs with positive weights."""
    pairs = [(float(value), float(weight)) for value, weight in values if finite(value) and finite(weight) and weight > 0]
    total = sum(weight for _, weight in pairs)
    if total <= 0:
        return None
    return sum(value * weight for value, weight in pairs) / total


def metric(name, value, unit, method, measured, note=None):
    out = {
        "name": str(name),
        "value": number(value),
        "unit": str(unit),
        "method": str(method),
        "measured": bool(measured),
    }
    if note:
        out["note"] = str(note)
    return out


def evidence(kind, bbox=None, severity=0.0, note=None):
    out = {"kind": kind, "severity": clamp01(severity)}
    if bbox is not None:
        out["bbox"] = [float(item) for item in list(bbox)[:4]]
    if note:
        out["note"] = str(note)
    return out


def bbox_from_region(region):
    """Convert an engine region dict to [x, y, width, height]."""
    if not isinstance(region, dict):
        return None
    x, y = number(region.get("x")), number(region.get("y"))
    width, height = number(region.get("width")), number(region.get("height"))
    if None in (x, y, width, height):
        return None
    return [x, y, width, height]


def factor(factor_id, *, status, metrics, risk, confidence, evidence=(), notes=()):
    if factor_id not in FACTOR_IDS:
        raise ValueError(f"Unknown factor id: {factor_id}")
    if status not in STATUSES:
        raise ValueError(f"Unknown analyzer status: {status}")
    return {
        "id": factor_id,
        "version": VERSION,
        "status": status,
        "metrics": [item for item in metrics if item is not None],
        "risk": None if risk is None else clamp01(risk),
        "confidence": clamp01(confidence),
        "evidence": list(evidence),
        "notes": [str(note) for note in notes if note],
    }
