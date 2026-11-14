"""Halos, glare and diffuse light-source artifacts (conservative estimate)."""
from __future__ import annotations

from .base import bbox_from_region, evidence, factor, metric, number, weighted


def analyze(row, *, analysis=None, group=None, **kwargs):
    analysis = analysis or {}
    features = analysis.get("features") or {}
    regions = analysis.get("regions") or {}

    diffuse = number(features.get("diffuse_peak"))
    positive = number(features.get("positive_area"))
    contact = number(features.get("edge_contact"))
    variation = number(row.get("backgroundVariation"))
    noise = number(row.get("noise"))
    gradient_index = (
        variation / (12.0 * noise)
        if variation is not None and noise is not None and noise > 0
        else None
    )

    metrics = [
        metric("diffuse_peak_index", diffuse, "index", "99.5th percentile of |smoothed residual| / 50", diffuse is not None),
        metric("bright_area_fraction", positive, "fraction", "significant positive residual area", positive is not None),
        metric("edge_contact", contact, "fraction", "dark region touching the frame rim", contact is not None),
        metric("gradient_index", gradient_index, "ratio", "background variation / (12 * noise)", gradient_index is not None),
    ]

    components = []
    if diffuse is not None:
        components.append((max(0.0, min(1.0, (diffuse - 0.5) / 2.0)), 0.4))
    if positive is not None:
        components.append((max(0.0, min(1.0, positive / 0.05)), 0.3))
    if contact is not None:
        components.append((max(0.0, min(1.0, contact)), 0.2))
    if gradient_index is not None:
        components.append((max(0.0, min(1.0, (gradient_index - 0.5) / 1.5)), 0.1))

    risk = weighted(components)
    if any(value is not None for value in (diffuse, positive, contact)):
        status, confidence = "estimated", 0.45
    elif gradient_index is not None:
        status, confidence = "estimated", 0.25
    else:
        status, confidence = "unavailable", 0.0

    found = []
    bbox = bbox_from_region(regions.get("positive"))
    if bbox is not None:
        found.append(evidence("region", bbox=bbox, severity=risk or 0.0, note="diffuse bright region"))

    notes = ["Halos/glare се оценяват по дифузни структури; причината не е доказана."]
    if contact is not None and contact > 0.2:
        notes.append("Структурата опира до ръба; възможно отражение или препятствие.")

    return factor(
        "artifacts",
        status=status,
        metrics=metrics,
        risk=risk,
        confidence=confidence,
        evidence=found,
        notes=notes,
    )
