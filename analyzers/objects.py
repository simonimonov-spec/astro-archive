"""Conservative morphology candidates for extended objects.

This is localization, not recognition. A candidate becomes a galaxy/nebula
claim only after a model trained on real labels is active.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage as ndi

from .base import clamp01, evidence, factor, metric


def detect(row, *, analysis=None, image=None, valid=None, z=None, stars=None, **kwargs):
    analysis = analysis or {}
    if image is not None:
        # Morphology belongs to the frame, not to a temporal difference where
        # real extended sky structure has intentionally been subtracted.
        raw=np.asarray(image,float)
        sky=np.isfinite(raw)
        if valid is not None:sky &= np.asarray(valid,bool)
        if stars is not None:sky &= ~np.asarray(stars,bool)
        if sky.sum()<1000:return None
        background=float(np.median(raw[sky]));clean=np.where(np.isfinite(raw),raw,background)
        residual=clean-ndi.gaussian_filter(clean,1.)
        sigma=max(1e-8,float(1.4826*np.median(abs(residual[sky]-np.median(residual[sky])))))
        z=(clean-background)/sigma
        valid=sky
    z = z if z is not None else analysis.get("z")
    valid = valid if valid is not None else analysis.get("valid")
    if z is None or valid is None:
        return None

    values = np.asarray(z, float)
    mask = np.asarray(valid, bool) & np.isfinite(values)
    if stars is not None:
        mask &= ~np.asarray(stars, bool)
    if mask.sum() < 1000:
        return []

    smooth = ndi.gaussian_filter(np.where(mask, values, 0.0), 4.0)
    weights = ndi.gaussian_filter(mask.astype(np.float32), 4.0)
    smooth = np.divide(smooth, weights, out=np.zeros_like(smooth), where=weights > 0.2)
    sample = smooth[mask]
    center = float(np.median(sample))
    sigma = float(1.4826 * np.median(np.abs(sample - center)))
    threshold = max(1.8, center + 2.5 * max(sigma, 1e-6))
    binary = (smooth > threshold) & mask

    labels, count = ndi.label(binary)
    if not count:
        return []

    sizes = np.bincount(labels.ravel())
    min_area = max(64, int(mask.sum() * 0.0005))
    max_area = max(min_area, int(mask.sum() * 0.4))
    candidates = []

    for ident in range(1, count + 1):
        area = int(sizes[ident])
        if area < min_area or area > max_area:
            continue
        ys, xs = np.nonzero(labels == ident)
        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())
        width, height = x1 - x0 + 1, y1 - y0 + 1
        fill = area / max(1, width * height)
        if fill < 0.08:
            continue
        peak = float(smooth[labels == ident].max())
        area_fraction = area / max(1, int(mask.sum()))
        aspect = width / max(1, height)

        if area_fraction < 0.01 and fill < 0.35 and 0.7 <= aspect <= 1.4:
            kind = "planetary_nebula_candidate"
        elif area_fraction < 0.01 and fill >= 0.45 and 0.6 <= aspect <= 1.8:
            kind = "galaxy_candidate"
        else:
            kind = "nebula_candidate"

        confidence = clamp01(
            0.2
            + 0.3 * min(1.0, peak / 6.0)
            + 0.25 * min(1.0, area_fraction / 0.05)
        )
        candidates.append(
            {
                "kind": kind,
                "bbox": [float(x0), float(y0), float(width), float(height)],
                "confidence": confidence,
                "method": "heuristic",
                "coordinateSpace": "analysis_sample",
                "label": "morphological_candidate",
            }
        )

    candidates.sort(key=lambda item: item["confidence"], reverse=True)
    return candidates[:8]


def analyze(row, *, analysis=None, valid=None, z=None, stars=None, candidates=None, **kwargs):
    analysis = analysis or {}
    if candidates is None:
        candidates = detect(row, analysis=analysis, valid=valid, z=z, stars=stars)

    if candidates is None:
        return factor(
            "objects",
            status="unavailable",
            metrics=[
                metric("candidate_count", None, "count", "morphology candidates", False),
            ],
            risk=None,
            confidence=0.0,
            notes=["Липсва significance map за морфологичен анализ."],
        )

    valid_map = valid if valid is not None else analysis.get("valid")
    valid_count = int(np.asarray(valid_map, bool).sum()) if valid_map is not None else None
    largest = 0.0
    for item in candidates:
        bbox = item.get("bbox") or []
        if len(bbox) == 4:
            largest = max(largest, float(bbox[2]) * float(bbox[3]))
    area_fraction = largest / valid_count if valid_count and largest else None

    found = [
        evidence(
            "region",
            bbox=item.get("bbox"),
            severity=item.get("confidence", 0.0),
            note=str(item.get("kind", "candidate")),
        )
        for item in candidates
    ]

    return factor(
        "objects",
        status="measured",
        metrics=[
            metric("candidate_count", len(candidates), "count", "morphology candidates", True),
            metric(
                "largest_candidate_area_fraction",
                area_fraction,
                "fraction",
                "largest candidate bbox area / valid analysis pixels",
                area_fraction is not None,
            ),
        ],
        risk=None,
        confidence=0.55 if candidates else 0.4,
        evidence=found,
        notes=[
            "Морфологични кандидати; не е разпознаване.",
            "bbox е в координати на анализирания sample.",
        ],
    )
