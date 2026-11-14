"""Light pollution: instrumental sky background, gradient and noise contribution."""
from __future__ import annotations

import math
import numpy as np

from .base import factor, median, metric, number, safe_ratio, weighted


def analyze(row, *, analysis=None, group=None, **kwargs):
    group = list(group or [])
    meta = row.get("meta") or {}

    background = number(row.get("background"))
    exposure = number(meta.get("exposure")) or number(row.get("exposure"))
    pixel_scale = number(row.get("pixelScale")) or number(meta.get("pixelScale"))
    variation = number(row.get("backgroundVariation"))
    noise = number(row.get("noise"))
    zeropoint = number(row.get("zeropoint")) or number(meta.get("zeropoint"))
    group_background = median([item.get("background") for item in group])
    sky_coverage = None
    gradient_residual = None
    gradient_adu = None
    image, valid, stars = kwargs.get('image'), kwargs.get('valid'), kwargs.get('stars')
    if image is not None and stars is not None:
        image=np.asarray(image,float)
        mask=np.isfinite(image) & ~np.asarray(stars,bool)
        if valid is not None: mask &= np.asarray(valid,bool)
        sky_coverage=float(mask.mean())
        positions=np.flatnonzero(mask)[::max(1,int(mask.sum())//20000)]
        if len(positions)>=100:
            yy,xx=np.unravel_index(positions,image.shape)
            design=np.column_stack((np.ones(len(xx)),xx/max(1,image.shape[1]-1)-.5,yy/max(1,image.shape[0]-1)-.5))
            values=image.ravel()[positions];keep=np.ones(len(values),bool)
            for _ in range(4):
                coef=np.linalg.lstsq(design[keep],values[keep],rcond=None)[0]
                residual=values-design@coef
                sigma=max(1e-8,float(1.4826*np.median(abs(residual[keep]-np.median(residual[keep])))))
                next_keep=abs(residual-np.median(residual[keep]))<3*sigma
                if next_keep.sum()<100:break
                keep=next_keep
            background=float(np.median(values[keep]))
            gradient_adu=float(abs(coef[1])+abs(coef[2]))
            gradient_residual=float(1.4826*np.median(abs(residual[keep]-np.median(residual[keep]))))

    rate = background / exposure if background is not None and exposure is not None and exposure > 0 else None
    surface = (
        rate / (pixel_scale * pixel_scale)
        if rate is not None and pixel_scale is not None and pixel_scale > 0
        else None
    )
    magnitude = (
        -2.5 * math.log10(surface) + zeropoint
        if surface is not None and surface > 0 and zeropoint is not None
        else None
    )
    relative = safe_ratio(background, group_background)
    gradient_index = (
        variation / (12.0 * noise)
        if variation is not None and noise is not None and noise > 0
        else None
    )

    metrics = [
        metric("sky_background_adu", background, "ADU", "sigma-clipped background median", background is not None),
        metric(
            "sky_background_rate",
            rate,
            "ADU/s",
            "background / exposure",
            rate is not None,
        ),
        metric(
            "sky_background_surface",
            surface,
            "ADU/s/arcsec^2",
            "background / exposure / pixel_scale^2",
            False,
            "Изведена стойност; не е калибрирана повърхностна яркост.",
        ),
        metric(
            "sky_mag_per_arcsec2",
            magnitude,
            "mag/arcsec^2",
            "-2.5 log10(surface) + zeropoint",
            False,
            "Показва се само при наличен photometric zeropoint.",
        ),
        metric(
            "background_variation_adu",
            variation,
            "ADU",
            "95th - 5th percentile of blurred reduced frame",
            variation is not None,
        ),
        metric(
            "background_ratio_group",
            relative,
            "ratio",
            "frame background / group median background",
            relative is not None,
        ),
        metric(
            "gradient_index",
            gradient_index,
            "ratio",
            "background variation / (12 * noise)",
            gradient_index is not None,
        ),
    ]
    if sky_coverage is not None:
        metrics.extend([
            metric('star_mask_sky_coverage',sky_coverage,'fraction','finite unmasked sky / sampled pixels',True),
            metric('gradient_fit_residual',gradient_residual,'ADU','MAD residual of robust sky-plane fit',gradient_residual is not None),
            metric('sky_gradient_surface',gradient_adu/exposure/(pixel_scale**2) if gradient_adu is not None and exposure is not None and exposure>0 and pixel_scale is not None and pixel_scale>0 else None,'ADU/s/arcsec^2','robust sky-plane corner range / exposure / native pixel_scale^2',False),
        ])
        metrics[0]['method']='robust median on star-masked block-mean pixels'

    components = []
    if relative is not None:
        components.append((max(0.0, min(1.0, (relative - 1.1) / 0.9)), 0.55))
    if gradient_index is not None:
        components.append((max(0.0, min(1.0, (gradient_index - 0.5) / 1.5)), 0.45))

    risk = weighted(components)
    if relative is not None:
        status, confidence = "measured", 0.75 if gradient_index is not None else 0.6
    elif background is not None:
        status, confidence = "estimated", 0.35
    else:
        status, confidence = "unavailable", 0.0
    if sky_coverage is not None:
        confidence *= min(1.,sky_coverage/.6) / (1+((gradient_residual or 0)/max(abs(background or 0),1e-8)))

    notes = []
    if sky_coverage is None:
        notes.append('Фонът е от общите измервания; липсва отделна звездна маска за този анализ.')
    notes.append('ADU е инструментална скала; gain не се прилага като калибрация без известни единици e-/ADU.')
    if zeropoint is None:
        notes.append("Без photometric zeropoint; mag/arcsec² не се твърди.")
    if pixel_scale is None:
        notes.append("Липсва pixel scale; повърхностната яркост в arcsec² не се изчислява.")
    if exposure is None or exposure <= 0:
        notes.append("Липсва валидна експозиция; нормализирането за секунда не се изчислява.")

    return factor(
        "light",
        status=status,
        metrics=metrics,
        risk=risk,
        confidence=confidence,
        notes=notes,
    )
