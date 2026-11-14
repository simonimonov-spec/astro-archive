"""Noise level and character relative to the frame group."""
from __future__ import annotations

from .base import factor, median, metric, number, safe_ratio, weighted


def analyze(row, *, analysis=None, group=None, **kwargs):
    group = list(group or [])
    analysis = analysis or {}
    features = analysis.get("features") or {}

    noise = number(row.get("noise"))
    background = number(row.get("background"))
    per_second = number(analysis.get("noise"))
    correlation = number(features.get("noise_ratio"))
    group_noise = median([item.get("noise") for item in group])
    relative = safe_ratio(noise, group_noise)

    metrics = [
        metric("noise_adu", noise, "ADU", "robust second-difference sigma", noise is not None),
        metric(
            "noise_per_second",
            per_second,
            "ADU/s",
            "robust noise of exposure-normalized analysis image",
            per_second is not None,
        ),
        metric("background_adu", background, "ADU", "sigma-clipped background median", background is not None),
        metric(
            "noise_ratio_group",
            relative,
            "ratio",
            "frame noise / group median noise",
            relative is not None,
        ),
        metric(
            "noise_correlation_ratio",
            correlation,
            "ratio",
            "analysis noise / reference-difference noise",
            correlation is not None,
        ),
    ]

    components = []
    if relative is not None:
        components.append((max(0.0, min(1.0, (relative - 1.0) / 1.0)), 0.6))
    if correlation is not None:
        components.append((max(0.0, min(1.0, (correlation - 1.2) / 1.8)), 0.4))

    risk = weighted(components)
    if relative is not None:
        status, confidence = "measured", 0.8 if correlation is not None else 0.7
    elif noise is not None or per_second is not None:
        status, confidence = "estimated", 0.3
    else:
        status, confidence = "unavailable", 0.0

    notes = []
    if group_noise is None:
        notes.append("Липсва групова медиана за шума; оценката е само абсолютна.")
    if correlation is not None and correlation > 1.5:
        notes.append("Шумът е силно корелиран; относителната оценка може да подценява.")

    return factor(
        "noise",
        status=status,
        metrics=metrics,
        risk=risk,
        confidence=confidence,
        notes=notes,
    )
