"""Specialized frame analyzers with one shared, data-only contract.

Used by the local inspection pipeline and the scoped evaluator API.
"""
from __future__ import annotations

from . import artifacts, audit, dataset, defects, legacy, light, neural, noise, objects, registry, stars, storage, trails
from .base import FACTOR_IDS, STATUSES, VERSION

FACTOR_ORDER = ("noise", "stars", "defects", "trails", "objects", "light", "artifacts")


def run_all(
    row,
    *,
    analysis=None,
    group=None,
    image=None,
    valid=None,
    z=None,
    star_mask=None,
    reference=None,
):
    """Return factors in stable order plus separate object candidates."""
    common = {
        "analysis": analysis,
        "group": group,
        "image": image,
        "valid": valid,
        "z": z,
        "stars": star_mask,
        "reference": reference,
    }
    candidates = objects.detect(row, **common)
    factors = [
        noise.analyze(row, **common),
        stars.analyze(row, **common),
        defects.analyze(row, **common),
        trails.analyze(row, **common),
        objects.analyze(row, candidates=candidates, **common),
        light.analyze(row, **common),
        artifacts.analyze(row, **common),
    ]
    return {"factors": factors, "objects": candidates or []}


__all__ = [
    "FACTOR_IDS",
    "FACTOR_ORDER",
    "STATUSES",
    "VERSION",
    "run_all",
    "artifacts",
    "audit",
    "dataset",
    "defects",
    "legacy",
    "light",
    "neural",
    "noise",
    "objects",
    "registry",
    "stars",
    "storage",
    "trails",
]
