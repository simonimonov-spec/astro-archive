"""Spatial defects: bright/dark areas, edges and coverage damage."""
from __future__ import annotations

from .base import bbox_from_region, evidence, factor, metric, number, weighted


def analyze(row, *, analysis=None, group=None, **kwargs):
    analysis = analysis or {}
    features = analysis.get("features") or {}
    regions = analysis.get("regions") or {}

    positive = number(features.get("positive_area"))
    negative = number(features.get("negative_area"))
    dark = number(features.get("dark_area"))
    dark_edge = number(features.get("dark_edge"))
    contact = number(features.get("edge_contact"))
    dark_fraction = number(row.get("darkFraction"))

    metrics = [
        metric("bright_area_fraction", positive, "fraction", "significant positive residual area", positive is not None),
        metric("negative_area_fraction", negative, "fraction", "significant negative residual area", negative is not None),
        metric("dark_area_fraction", dark, "fraction", "significant dark residual area", dark is not None),
        metric("dark_edge_index", dark_edge, "index", "98th percentile gradient on dark-region boundary / 10", dark_edge is not None),
        metric("edge_contact", contact, "fraction", "dark region touching the frame rim", contact is not None),
        metric("dark_fraction", dark_fraction, "fraction", "pixels below -4 sigma in the reduced frame", dark_fraction is not None),
    ]

    components = []
    if positive is not None:
        components.append((max(0.0, min(1.0, positive / 0.05)), 0.2))
    if negative is not None:
        components.append((max(0.0, min(1.0, negative / 0.05)), 0.2))
    if dark is not None:
        components.append((max(0.0, min(1.0, dark / 0.25)), 0.25))
    if dark_edge is not None:
        components.append((max(0.0, min(1.0, dark_edge / 1.0)), 0.15))
    if contact is not None:
        components.append((max(0.0, min(1.0, contact)), 0.1))
    if dark_fraction is not None:
        components.append((max(0.0, min(1.0, dark_fraction / 0.3)), 0.1))

    risk = weighted(components)
    if any(value is not None for value in (positive, negative, dark, dark_edge, contact)):
        status, confidence = "measured", 0.75
    elif dark_fraction is not None:
        status, confidence = "estimated", 0.4
    else:
        status, confidence = "unavailable", 0.0

    found = []
    for key, kind, label in (
        ("positive", "region", "bright residual region"),
        ("negative", "region", "negative residual region"),
        ("dark", "region", "dark residual region"),
    ):
        bbox = bbox_from_region(regions.get(key))
        if bbox is not None:
            found.append(evidence(kind, bbox=bbox, severity=risk or 0.0, note=label))

    notes = []
    if dark is not None and dark > 0.15:
        notes.append("Голяма тъмна област; възможно препятствие или реална структура.")
    if positive is not None and positive > 0.05:
        notes.append("Голяма ярка област; възможно отражение, облак или реален обект.")

    return factor(
        "defects",
        status=status,
        metrics=metrics,
        risk=risk,
        confidence=confidence,
        evidence=found,
        notes=notes,
    )
