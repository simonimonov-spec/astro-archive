"""Linear trails and transient tracks from the existing bounded Hough search."""
from __future__ import annotations

from .base import evidence, factor, metric, number, weighted


def _line_bbox(line):
    if not isinstance(line, dict):
        return None
    start, end = line.get("a"), line.get("b")
    if not (isinstance(start, (list, tuple)) and isinstance(end, (list, tuple))):
        return None
    if len(start) < 2 or len(end) < 2:
        return None
    x0, y0 = float(start[0]), float(start[1])
    x1, y1 = float(end[0]), float(end[1])
    return [min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)]


def analyze(row, *, analysis=None, group=None, **kwargs):
    analysis = analysis or {}
    features = analysis.get("features") or {}
    regions = analysis.get("regions") or {}
    lines = regions.get("lines") or []
    uncertainty = str(analysis.get("uncertainty") or "")

    length = number(features.get("line_length"))
    snr = number(features.get("line_snr"))
    fill = number(features.get("line_fill"))
    line_list = lines if isinstance(lines, list) else []
    count = len(line_list)

    metrics = [
        metric("line_length_fraction", length, "fraction", "longest Hough segment / frame diagonal", length is not None),
        metric("line_snr_index", snr, "index", "median line significance / 10", snr is not None),
        metric("line_fill", fill, "fraction", "occupied bins / segment length", fill is not None),
        metric("line_count", count, "count", "bounded Hough segments", isinstance(lines, list)),
    ]

    components = []
    if length is not None:
        components.append((max(0.0, min(1.0, (length - 0.14) / 0.5)), 0.4))
    if snr is not None:
        components.append((max(0.0, min(1.0, (snr - 0.8) / 2.0)), 0.35))
    if fill is not None:
        components.append((max(0.0, min(1.0, (fill - 0.32) / 0.5)), 0.25))

    structured = "сензорен" in uncertainty.lower() or "успоредни" in uncertainty.lower()
    risk = weighted(components)
    if risk is not None and structured:
        risk *= 0.35

    if length is not None and fill is not None:
        status, confidence = "measured", 0.8 if not structured else 0.55
    elif components:
        status, confidence = "estimated", 0.4
    else:
        status, confidence = "unavailable", 0.0

    found = []
    for line in line_list[:8]:
        bbox = _line_bbox(line)
        if bbox is not None:
            found.append(evidence("region", bbox=bbox, severity=risk or 0.0, note="Hough line segment"))

    notes = []
    if structured:
        notes.append("Повтарящ се сензорен рисунък; следата е несигурна, а не доказана.")
    if count > 0 and risk is not None and risk >= 0.5:
        notes.append("Линейна структура с висока значимост; провери ръчно за сателит/самолет.")
    if count == 0:
        notes.append("Няма намерена линейна следа; това не доказва чист кадър.")

    return factor(
        "trails",
        status=status,
        metrics=metrics,
        risk=risk,
        confidence=confidence,
        evidence=found,
        notes=notes,
    )
