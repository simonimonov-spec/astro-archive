"""Star quality, shape and count relative to the frame group."""
from __future__ import annotations

from .base import factor, median, metric, number, safe_ratio, weighted


def analyze(row, *, analysis=None, group=None, **kwargs):
    group = list(group or [])

    fwhm = number(row.get("fwhm"))
    hfr = number(row.get("hfr"))
    roundness = number(row.get("roundness"))
    eccentricity = number(row.get("eccentricity"))
    stars = number(row.get("stars"))
    saturated = number(row.get("saturatedStars"))
    fitted = number(row.get("fittedStars"))
    measured = number(row.get("measuredStars"))

    group_fwhm = median([item.get("fwhm") for item in group])
    group_hfr = median([item.get("hfr") for item in group])
    group_stars = median([item.get("stars") for item in group])

    fwhm_ratio = safe_ratio(fwhm, group_fwhm)
    hfr_ratio = safe_ratio(hfr, group_hfr)
    stars_ratio = safe_ratio(stars, group_stars)
    saturated_fraction = safe_ratio(saturated, fitted) if fitted else None
    measured_fraction = safe_ratio(measured, stars) if stars else None

    metrics = [
        metric("fwhm_px", fwhm, "px", "mean unsaturated Gaussian FWHM", fwhm is not None),
        metric("hfr_px", hfr, "px", "mean unsaturated half-flux radius", hfr is not None),
        metric("roundness", roundness, "ratio", "fwhmY / fwhm", roundness is not None),
        metric("eccentricity", eccentricity, "ratio", "mean unsaturated star eccentricity", eccentricity is not None),
        metric("star_count", stars, "count", "detected stars", stars is not None),
        metric(
            "fwhm_ratio_group",
            fwhm_ratio,
            "ratio",
            "frame FWHM / group median FWHM",
            fwhm_ratio is not None,
        ),
        metric(
            "hfr_ratio_group",
            hfr_ratio,
            "ratio",
            "frame HFR / group median HFR",
            hfr_ratio is not None,
        ),
        metric(
            "star_count_ratio_group",
            stars_ratio,
            "ratio",
            "frame star count / group median star count",
            stars_ratio is not None,
        ),
        metric(
            "saturated_fraction",
            saturated_fraction,
            "fraction",
            "saturated stars / fitted stars",
            saturated_fraction is not None,
        ),
        metric(
            "measured_fraction",
            measured_fraction,
            "fraction",
            "unsaturated measured stars / detected stars",
            measured_fraction is not None,
        ),
    ]

    components = []
    if fwhm_ratio is not None:
        components.append((max(0.0, min(1.0, (fwhm_ratio - 1.05) / 0.95)), 0.35))
    if hfr_ratio is not None:
        components.append((max(0.0, min(1.0, (hfr_ratio - 1.05) / 0.95)), 0.25))
    if stars_ratio is not None:
        components.append((max(0.0, min(1.0, (0.8 - stars_ratio) / 0.5)), 0.25))
    if roundness is not None:
        components.append((max(0.0, min(1.0, (0.85 - roundness) / 0.35)), 0.15))
    if eccentricity is not None:
        components.append((max(0.0, min(1.0, (eccentricity - 0.4) / 0.4)), 0.15))
    if saturated_fraction is not None:
        components.append((max(0.0, min(1.0, (saturated_fraction - 0.05) / 0.45)), 0.1))

    risk = weighted(components)
    if fwhm_ratio is not None and stars_ratio is not None:
        status, confidence = "measured", 0.8
    elif components:
        status, confidence = "estimated", 0.55
    else:
        status, confidence = "unavailable", 0.0

    notes = []
    if group_fwhm is None and group_stars is None:
        notes.append("Липсва групова медиана за FWHM/звезди; оценката е само абсолютна.")
    if measured_fraction is not None and measured_fraction < 0.5:
        notes.append("Под половината звезди са надеждно измерени; формата е по-несигурна.")
    if saturated_fraction is not None and saturated_fraction > 0.3:
        notes.append("Висока част наситени звезди; FWHM може да е подценен.")

    return factor(
        "stars",
        status=status,
        metrics=metrics,
        risk=risk,
        confidence=confidence,
        notes=notes,
    )
