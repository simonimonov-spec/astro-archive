"""Read-only import of the three legacy label/model paths.

Nothing here activates a model or edits the source files. Legacy records stay
legacy and are never treated as the new MLP.
"""
from __future__ import annotations

import math

from .storage import canonical, digest_text, identifier, utc_now

LEGACY_MODEL_FORMATS = (
    "frame-inspector-linear-v1",
    "frame-inspector-user-quality-v2",
    "frame-inspector-user-quality-v1",
)
LEGACY_LABEL_FORMATS = (
    "astroarchive-training-labels-v1",
    "astroarchive-viewer-labels-v1",
)
TARGET = {
    "perfect": 1.0,
    "excellent": 0.92,
    "very_good": 0.84,
    "good": 0.75,
    "fair": 0.5,
    "poor": 0.25,
    "bad": 0.0,
}
ELONGATION = {"mild": 0.5, "moderate": 0.75, "severe": 1.0}
GOOD = tuple(name for name, value in TARGET.items() if value >= 0.75)
KNOWN_DEFECTS = {"cloud", "satellite", "light", "elongation"}


class LegacyError(ValueError):
    """Unsupported or malformed legacy record."""


def _finite_list(value, name, length):
    if not isinstance(value, list) or len(value) != length:
        raise LegacyError(f"Невалиден {name}: очаква се списък с {length} стойности.")
    out = []
    for item in value:
        if not isinstance(item, (int, float)) or isinstance(item, bool) or not math.isfinite(float(item)):
            raise LegacyError(f"Невалидна стойност в {name}.")
        out.append(float(item))
    return out


def _head(value, length, name):
    if not isinstance(value, dict):
        raise LegacyError(f"Липсва {name}.")
    mean = _finite_list(value.get("mean"), name + ".mean", length)
    scale = _finite_list(value.get("scale"), name + ".scale", length)
    if any(item <= 0 for item in scale):
        raise LegacyError(f"{name}.scale трябва да е положителен.")
    weights = _finite_list(value.get("weights"), name + ".weights", length + 1)
    return {"mean": mean, "scale": scale, "weights": weights}


def validate_legacy_model(data):
    if not isinstance(data, dict):
        raise LegacyError("Legacy моделът трябва да е JSON обект.")
    fmt = data.get("format")
    if fmt not in LEGACY_MODEL_FORMATS:
        raise LegacyError("Неподдържан legacy формат на модел.")
    features = data.get("features")
    if not isinstance(features, list) or not all(isinstance(name, str) and name for name in features):
        raise LegacyError("Legacy моделът няма валиден списък с признаци.")
    if fmt == "frame-inspector-linear-v1":
        if len(features) != 15:
            raise LegacyError("Линейният legacy модел трябва да има 15 признака.")
        _finite_list(data.get("mean"), "mean", len(features))
        _finite_list(data.get("scale"), "scale", len(features))
        weights = data.get("weights")
        if not isinstance(weights, dict) or set(weights) != {"cloud", "satellite", "obstruction"}:
            raise LegacyError("Линейният legacy модел няма трите класа.")
        for name in ("cloud", "satellite", "obstruction"):
            _finite_list(weights.get(name), "weights." + name, len(features) + 1)
        model_id = "legacy-linear"
    else:
        expected = 48 if fmt == "frame-inspector-user-quality-v2" else 18
        if len(features) != expected:
            raise LegacyError(f"Legacy quality моделът трябва да има {expected} признака.")
        _head(data.get("quality"), len(features), "quality")
        if data.get("cloud") is not None:
            _head(data.get("cloud"), len(features), "cloud")
        defects = data.get("defects", {})
        if not isinstance(defects, dict) or set(defects) - KNOWN_DEFECTS:
            raise LegacyError("Legacy quality моделът има непознати класове.")
        for name, head in defects.items():
            _head(head, len(features), "defects." + name)
        model_id = "legacy-user-quality"
    version = data.get("version")
    if not isinstance(version, str) or not version.strip():
        version = "legacy"
    provenance = data.get("provenance")
    if not isinstance(provenance, dict):
        provenance = {}
    return {
        "format": fmt,
        "modelId": model_id,
        "version": version,
        "featureSchema": "legacy:" + fmt,
        "provenance": provenance,
    }


def _source_of(row):
    source = row.get("source")
    if not isinstance(source, dict):
        source = row.get("resolvedSource")
    return source if isinstance(source, dict) else {}


def _identity(source):
    raw = [
        source.get("archive"),
        source.get("id") or source.get("path") or "",
        source.get("size"),
        source.get("mtime"),
    ]
    return digest_text(canonical(raw))


def _number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _context(row, source):
    return {
        "archiveId": source.get("archive") if isinstance(source.get("archive"), str) else None,
        "object": source.get("object") if isinstance(source.get("object"), str) else None,
        "telescope": source.get("telescope") if isinstance(source.get("telescope"), str) else None,
        "camera": source.get("camera") if isinstance(source.get("camera"), str) else None,
        "filter": source.get("filter") if isinstance(source.get("filter"), str) else None,
        "night": row.get("night") if isinstance(row.get("night"), str) else (source.get("night") if isinstance(source.get("night"), str) else None),
        "group": row.get("group") if isinstance(row.get("group"), str) else (source.get("group") if isinstance(source.get("group"), str) else None),
        "exposure": _number(source.get("exposure")),
        "gain": _number(source.get("gain")),
        "binning": source.get("binning") if isinstance(source.get("binning"), int) else None,
        "pixelScale": _number(source.get("pixelScale")),
        "zeropoint": _number(source.get("zeropoint")),
    }


def _frame(row, source):
    identity = _identity(source)
    frame_id = source.get("id") or row.get("id")
    if not isinstance(frame_id, str) or not frame_id.strip():
        frame_id = "legacy-" + identity[7:31]
    elif len(frame_id) > 120:
        frame_id = "legacy-" + identity[7:31]
    return {
        "id": frame_id,
        "hash": identity,
        "sourceSize": source.get("size") if isinstance(source.get("size"), int) else None,
        "sourceMtime": source.get("mtime") if isinstance(source.get("mtime"), int) else None,
    }


def _base(row, source, *, labeler, origin_format, key=""):
    identity = _identity(source)
    suffix = key[:24] if isinstance(key, str) and len(key) >= 24 and all(ch in "0123456789abcdef" for ch in key[:24].lower()) else identity[7:31]
    example = {
        "schema": "astro-ai-example-v1",
        "exampleId": "legacy-" + suffix,
        "frame": _frame(row, source),
        "context": _context(row, source),
        "labels": {
            "quality": None,
            "cloud": None,
            "trail": None,
            "light": None,
            "elongation": None,
            "defects": None,
            "artifacts": None,
            "objects": [],
        },
        "labeler": {"id": labeler},
        "createdAt": utc_now(),
        "split": "unassigned",
        "origin": "legacy",
        "notes": f"Read-only import from {origin_format}; frame hash is an identity hash, not a content hash.",
    }
    return example


def _from_trainer(key, row):
    label = row.get("label")
    source = _source_of(row)
    if not source:
        return None, "липсва източник"
    example = _base(row, source, labeler="legacy:trainer", origin_format="astroarchive-training-labels-v1", key=key)
    if label == "perfect":
        example["labels"].update(quality=1.0, cloud="clear", trail="none")
    elif label == "bad":
        example["labels"].update(quality=0.0)
    elif label == "cloud":
        example["labels"].update(cloud="cloud")
    else:
        return None, "неопределен или празен етикет"
    return example, None


def _from_viewer(key, row):
    source = _source_of(row)
    if not source:
        return None, "липсва източник"
    quality_name = row.get("quality")
    defects = row.get("defects") if isinstance(row.get("defects"), list) else []
    if not all(isinstance(name, str) for name in defects):
        return None, "невалиден списък с дефекти"
    if quality_name is not None and quality_name not in TARGET:
        return None, "непознато качество"
    quality = TARGET.get(quality_name)
    example = _base(row, source, labeler="legacy:ai-training", origin_format="astroarchive-viewer-labels-v1", key=key)
    labels = example["labels"]
    labels["quality"] = quality
    labels["cloud"] = "cloud" if "cloud" in defects else ("clear" if quality is not None else None)
    labels["trail"] = "satellite" if "satellite" in defects else ("none" if quality is not None else None)
    labels["light"] = 1.0 if "light" in defects else (0.0 if quality is not None and not defects else None)
    if "elongation" in defects:
        labels["elongation"] = ELONGATION.get(row.get("elongationLevel"), 0.75)
    elif quality is not None and not defects:
        labels["elongation"] = 0.0
    unknown = set(defects) - KNOWN_DEFECTS
    if unknown:
        labels["defects"] = "present"
    if not any(
        value is not None
        for value in (
            labels["quality"],
            labels["cloud"],
            labels["trail"],
            labels["light"],
            labels["elongation"],
            labels["defects"],
            labels["artifacts"],
        )
    ) and not labels["objects"]:
        return None, "няма използваем етикет"
    return example, None


def legacy_labels(data, *, labeler=None):
    """Map a legacy label file to new examples without writing anything."""
    if not isinstance(data, dict):
        raise LegacyError("Legacy етикетите трябва да са JSON обект.")
    fmt = data.get("format")
    if fmt not in LEGACY_LABEL_FORMATS:
        raise LegacyError("Неподдържан legacy формат на етикети.")
    rows = data.get("rows")
    if not isinstance(rows, dict):
        raise LegacyError("Legacy етикетите нямат rows.")
    examples = []
    skipped = []
    for key, row in rows.items():
        if not isinstance(row, dict):
            skipped.append({"key": str(key), "reason": "невалиден ред"})
            continue
        try:
            example, reason = _from_trainer(key, row) if fmt == "astroarchive-training-labels-v1" else _from_viewer(key, row)
        except (ValueError, TypeError) as error:
            example, reason = None, str(error)
        if example is None:
            skipped.append({"key": str(key), "reason": reason})
        else:
            if labeler:
                example["labeler"]["id"] = identifier(labeler, "labeler")
            examples.append(example)
    return {"examples": examples, "skipped": skipped}
