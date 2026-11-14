"""Portable NumPy MLP inference and attribution.

This module never trains and never activates a model. It validates the
portable JSON format, runs a forward pass, and explains one output with
gradient x input (occlusion fallback).
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import numpy as np

from .base import finite

FORMAT = "astro-ai-mlp-v1"
HIDDEN_ACTIVATIONS = {"relu", "tanh", "sigmoid", "linear"}
OUTPUT_ACTIVATIONS = {"sigmoid", "linear"}
OUTPUT_NAMES = (
    "quality",
    "risk.noise",
    "risk.stars",
    "risk.defects",
    "risk.trails",
    "risk.objects",
    "risk.light",
    "risk.artifacts",
    "event.cloud",
    "event.satellite",
    "event.obstruction",
)
MAX_INPUTS = 256
MAX_HIDDEN = 8
MAX_WIDTH = 256
MAX_OUTPUTS = 64
CLIP = 15.0
HASH = re.compile(r"^sha256:[a-f0-9]{64}$")


class ModelError(ValueError):
    """Invalid portable model or unsupported explanation request."""


def _require(condition, message):
    if not condition:
        raise ModelError("Невалиден AI модел: " + message)


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _vector(value, name, limit=MAX_WIDTH):
    _require(isinstance(value, list) and 1 <= len(value) <= limit, f"{name} трябва да е списък с 1..{limit} числа.")
    out = []
    for item in value:
        _require(finite(item), f"{name} съдържа невалидно число.")
        out.append(float(item))
    return out


def _matrix(value, name, max_rows=MAX_WIDTH, max_columns=MAX_WIDTH):
    _require(isinstance(value, list) and 1 <= len(value) <= max_rows, f"{name} трябва да е матрица с 1..{max_rows} реда.")
    rows = []
    for row in value:
        _require(isinstance(row, list) and 1 <= len(row) <= max_columns, f"{name} съдържа невалиден ред.")
        _require(all(finite(item) for item in row), f"{name} съдържа невалидно число.")
        rows.append([float(item) for item in row])
    return rows


def validate(data):
    """Validate the portable format and matrix dimensions; return data unchanged."""
    _require(isinstance(data, dict), "коренът трябва да е JSON обект.")
    _require(data.get("format") == FORMAT, f"форматът трябва да е {FORMAT}.")
    for key in ("modelId", "version", "createdAt", "featureSchema"):
        value = data.get(key)
        _require(isinstance(value, str) and value.strip(), f"липсва или е празно полето {key}.")

    source = data.get("input")
    _require(isinstance(source, dict), "липсва input.")
    names = source.get("names")
    _require(isinstance(names, list) and 1 <= len(names) <= MAX_INPUTS, f"input.names трябва да е списък с 1..{MAX_INPUTS} имена.")
    _require(all(isinstance(name, str) and name.strip() for name in names), "input.names съдържа невалидно име.")
    _require(len(set(names)) == len(names), "input.names съдържа дубликати.")
    mean = _vector(source.get("mean"), "input.mean", MAX_INPUTS)
    scale = _vector(source.get("scale"), "input.scale", MAX_INPUTS)
    _require(len(mean) == len(names), "input.mean трябва да е със същата дължина като input.names.")
    _require(len(scale) == len(names), "input.scale трябва да е със същата дължина като input.names.")
    _require(all(value > 0 for value in scale), "input.scale трябва да е положителен.")

    hidden = data.get("hidden")
    _require(isinstance(hidden, list) and 1 <= len(hidden) <= MAX_HIDDEN, f"hidden трябва да е списък с 1..{MAX_HIDDEN} слоя.")
    width = len(names)
    for index, layer in enumerate(hidden):
        _require(isinstance(layer, dict), f"hidden[{index}] трябва да е обект.")
        _require(layer.get("activation") in HIDDEN_ACTIVATIONS, f"hidden[{index}].activation е неподдържана.")
        weights = _matrix(layer.get("weights"), f"hidden[{index}].weights", MAX_WIDTH, width)
        biases = _vector(layer.get("biases"), f"hidden[{index}].biases", MAX_WIDTH)
        _require(len(weights) == len(biases), f"hidden[{index}] има различен брой редове и bias-ове.")
        _require(all(len(row) == width for row in weights), f"hidden[{index}] има грешна входна ширина.")
        width = len(biases)

    output = data.get("output")
    _require(isinstance(output, dict), "липсва output.")
    _require(output.get("activation") in OUTPUT_ACTIVATIONS, "output.activation е неподдържана.")
    output_names = output.get("names")
    _require(isinstance(output_names, list) and 1 <= len(output_names) <= MAX_OUTPUTS, f"output.names трябва да е списък с 1..{MAX_OUTPUTS} имена.")
    _require(all(name in OUTPUT_NAMES for name in output_names), "output.names съдържа непознат изход.")
    _require(len(set(output_names)) == len(output_names), "output.names съдържа дубликати.")
    weights = _matrix(output.get("weights"), "output.weights", MAX_OUTPUTS, width)
    biases = _vector(output.get("biases"), "output.biases", MAX_OUTPUTS)
    _require(len(weights) == len(output_names), "output.weights трябва да има ред за всеки изход.")
    _require(len(biases) == len(output_names), "output.biases трябва да има стойност за всеки изход.")
    _require(all(len(row) == width for row in weights), "output.weights има грешна входна ширина.")

    training = data.get("training")
    _require(isinstance(training, dict), "липсва training.")
    for key in ("datasetId", "datasetHash", "codeVersion"):
        value = training.get(key)
        _require(isinstance(value, str) and value.strip(), f"training.{key} е задължително.")
    _require(HASH.match(training["datasetHash"]) is not None, "training.datasetHash трябва да е sha256:...")
    _require(_is_int(training.get("exampleCount")) and training["exampleCount"] >= 1, "training.exampleCount трябва да е положително цяло число.")
    _require(_is_int(training.get("seed")) and training["seed"] >= 0, "training.seed трябва да е неотрицателно цяло число.")

    provenance = data.get("provenance")
    _require(isinstance(provenance, dict), "липсва provenance.")
    _require(isinstance(provenance.get("createdBy"), str) and provenance["createdBy"].strip(), "provenance.createdBy е задължително.")
    _require(isinstance(provenance.get("legacy"), bool), "provenance.legacy трябва да е boolean.")
    return data


def loads(text):
    try:
        data = json.loads(text)
    except (TypeError, json.JSONDecodeError) as error:
        raise ModelError("Невалиден AI модел: JSON не може да бъде прочетен.") from error
    return validate(data)


def load(path):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as error:
        raise ModelError("AI моделът не може да бъде прочетен.") from error
    return loads(text)


def _sigmoid(values):
    return 1.0 / (1.0 + np.exp(-np.clip(values, -60.0, 60.0)))


def _activate(values, name):
    if name == "relu":
        return np.maximum(values, 0.0)
    if name == "tanh":
        return np.tanh(values)
    if name == "sigmoid":
        return _sigmoid(values)
    if name == "linear":
        return values
    raise ModelError(f"Неподдържана активация: {name}")


def _derivative(pre, activated, name):
    if name == "relu":
        return (pre > 0).astype(float)
    if name == "tanh":
        return 1.0 - activated * activated
    if name == "sigmoid":
        return activated * (1.0 - activated)
    if name == "linear":
        return np.ones_like(pre)
    raise ModelError(f"Неподдържана активация: {name}")


def prepare(model, features):
    """Normalize inputs; missing values become the training mean (normalized 0)."""
    features = features if isinstance(features, dict) else {}
    names = model["input"]["names"]
    mean = np.asarray(model["input"]["mean"], float)
    scale = np.asarray(model["input"]["scale"], float)
    values = np.zeros(len(names), float)
    missing = []
    for index, name in enumerate(names):
        value = features.get(name)
        if finite(value):
            values[index] = (float(value) - mean[index]) / scale[index]
        else:
            values[index] = 0.0
            missing.append(name)
    return np.clip(values, -CLIP, CLIP), missing


def _forward(model, values):
    current = values
    for layer in model["hidden"]:
        weights = np.asarray(layer["weights"], float)
        biases = np.asarray(layer["biases"], float)
        current = _activate(weights @ current + biases, layer["activation"])
    output = model["output"]
    weights = np.asarray(output["weights"], float)
    biases = np.asarray(output["biases"], float)
    raw = weights @ current + biases
    activated = _activate(raw, output["activation"])
    return {name: float(value) for name, value in zip(output["names"], activated)}


def forward(model, features):
    """Run one deterministic forward pass. Model must be validated by load()."""
    values, _ = prepare(model, features)
    return _forward(model, values)


def gradient_x_input(model, features, output_name="quality"):
    """Signed contribution of each normalized input to one output."""
    names = model["output"]["names"]
    if output_name not in names:
        raise ModelError(f"Непознат изход: {output_name}")
    target = names.index(output_name)

    values, missing = prepare(model, features)
    activations = [values]
    pre_activations = []
    current = values
    for layer in model["hidden"]:
        weights = np.asarray(layer["weights"], float)
        biases = np.asarray(layer["biases"], float)
        pre = weights @ current + biases
        current = _activate(pre, layer["activation"])
        pre_activations.append(pre)
        activations.append(current)

    output = model["output"]
    output_weights = np.asarray(output["weights"], float)
    output_biases = np.asarray(output["biases"], float)
    pre_output = output_weights @ current + output_biases
    activated = _activate(pre_output, output["activation"])

    delta = np.zeros(len(names), float)
    delta[target] = 1.0
    delta *= _derivative(pre_output, activated, output["activation"])
    gradient = output_weights.T @ delta

    for index in range(len(model["hidden"]) - 1, -1, -1):
        layer = model["hidden"][index]
        delta = gradient * _derivative(pre_activations[index], activations[index + 1], layer["activation"])
        gradient = np.asarray(layer["weights"], float).T @ delta

    contributions = gradient * values
    return {
        "output": output_name,
        "method": "gradient_x_input",
        "values": {name: float(value) for name, value in zip(model["input"]["names"], contributions)},
        "missing": missing,
    }


def occlusion(model, features, output_name="quality"):
    """Fallback attribution: replace one input with its mean and measure the change."""
    names = model["output"]["names"]
    if output_name not in names:
        raise ModelError(f"Непознат изход: {output_name}")
    values, missing = prepare(model, features)
    full = _forward(model, values)[output_name]
    source = features if isinstance(features, dict) else {}
    result = {}
    for name in model["input"]["names"]:
        reduced = dict(source)
        reduced.pop(name, None)
        result[name] = float(full - forward(model, reduced)[output_name])
    return {
        "output": output_name,
        "method": "occlusion_mean",
        "values": result,
        "missing": missing,
    }


def attribution(model, features, output_name="quality", method="auto"):
    """Explain one output. Auto uses gradient, then occlusion on failure."""
    if method == "gradient":
        return gradient_x_input(model, features, output_name)
    if method == "occlusion":
        return occlusion(model, features, output_name)
    if method != "auto":
        raise ModelError(f"Неподдържан метод за обяснение: {method}")
    try:
        return gradient_x_input(model, features, output_name)
    except (ModelError, ValueError, FloatingPointError):
        return occlusion(model, features, output_name)


def ranked(result, limit=8):
    """Largest absolute contributions first, without dropping the sign."""
    items = sorted(result["values"].items(), key=lambda item: abs(item[1]), reverse=True)
    return [{"name": name, "contribution": value} for name, value in items[: max(1, int(limit))]]
