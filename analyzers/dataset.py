"""Append-only training dataset store with a reproducible manifest.

Examples are validated before they are appended and are never rewritten in
place. The manifest is derived from the append-only files, so a frozen dataset
can be verified byte-for-byte. Overrides are dataset-level records; they do not
mutate the source example.

Nothing in this module trains a model or activates one.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .storage import (
    StorageError,
    append_jsonl,
    canonical,
    atomic_write_json,
    atomic_write_text,
    digest_data,
    digest_text,
    identifier,
    read_json,
    read_jsonl,
    utc_now,
)

SCHEMA = "astro-ai-example-v1"
MANIFEST_SCHEMA = "astro-ai-dataset-v1"
LABEL_SCHEMA = "astro-ai-labels-v1"
SPLITS = ("train", "validation", "test", "unassigned")
LABEL_FIELDS = ("quality", "cloud", "trail", "light", "elongation", "defects", "artifacts", "objects")
OPTIONAL_LABEL_FIELDS = ("noise", "objectRisk", "obstruction", "defocus")
CLOUD = ("clear", "cloud", "uncertain", None)
TRAIL = ("none", "satellite", "aircraft", "uncertain", None)
TRISTATE = ("none", "present", "uncertain", None)
OBJECT_KINDS = ("galaxy", "nebula", "planetary_nebula", "unknown")
CONTEXT_KEYS = (
    "archiveId",
    "object",
    "telescope",
    "camera",
    "filter",
    "night",
    "group",
    "exposure",
    "gain",
    "binning",
    "pixelScale",
    "zeropoint",
)
EXPLANATION_KEYS = LABEL_FIELDS + ("defocus",)
HASH = re.compile(r"^sha256:[a-f0-9]{64}$")
NUMERIC_TOLERANCE = 0.25
MAX_EXAMPLES = 200_000


class DatasetError(ValueError):
    """Invalid example, manifest state or dataset operation."""


def _require(condition, message):
    if not condition:
        raise DatasetError(message)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _finite(value):
    return _is_number(value) and value == value and value not in (float("inf"), float("-inf"))


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _timestamp(value, name):
    _require(isinstance(value, str) and value.strip(), f"{name} трябва да е ISO време.")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise DatasetError(f"{name} трябва да е ISO време.") from error


def _text(value, name, limit, *, required=False):
    if value is None and not required:
        return
    _require(isinstance(value, str), f"{name} трябва да е текст.")
    _require(len(value) <= limit, f"{name} е по-дълго от {limit} знака.")
    if required:
        _require(bool(value.strip()), f"{name} не може да е празно.")


def _unit(value, name):
    if value is None:
        return
    _require(_finite(value) and 0.0 <= float(value) <= 1.0, f"{name} трябва да е число от 0 до 1 или null.")


def _enum(value, name, allowed):
    _require(value in allowed, f"{name} има неподдържана стойност.")


def _validate_object_label(item, index):
    _require(isinstance(item, dict), f"labels.objects[{index}] трябва да е обект.")
    _require(set(item) <= {"kind", "bbox", "confidence", "note"}, f"labels.objects[{index}] има непознато поле.")
    _enum(item.get("kind"), f"labels.objects[{index}].kind", OBJECT_KINDS)
    bbox = item.get("bbox")
    _require(isinstance(bbox, list) and len(bbox) == 4, f"labels.objects[{index}].bbox трябва да е 4 числа.")
    _require(all(_finite(value) and float(value) >= 0 for value in bbox), f"labels.objects[{index}].bbox е невалиден.")
    if "confidence" in item:
        _unit(item["confidence"], f"labels.objects[{index}].confidence")
    if "note" in item:
        _text(item["note"], f"labels.objects[{index}].note", 400)


def _validate_labels(labels):
    _require(isinstance(labels, dict), "липсва labels.")
    _require(set(LABEL_FIELDS)<=set(labels)<=set(LABEL_FIELDS+OPTIONAL_LABEL_FIELDS), "labels трябва да съдържа основните полета и само поддържани допълнителни глави.")
    _unit(labels.get("quality"), "labels.quality")
    _enum(labels.get("cloud"), "labels.cloud", CLOUD)
    _enum(labels.get("trail"), "labels.trail", TRAIL)
    _unit(labels.get("light"), "labels.light")
    _unit(labels.get("elongation"), "labels.elongation")
    _unit(labels.get("defocus"), "labels.defocus")
    _enum(labels.get("defects"), "labels.defects", TRISTATE)
    _enum(labels.get("artifacts"), "labels.artifacts", TRISTATE)
    _unit(labels.get('noise'),'labels.noise')
    _unit(labels.get('objectRisk'),'labels.objectRisk')
    _enum(labels.get('obstruction'),'labels.obstruction',TRISTATE)
    objects = labels.get("objects")
    _require(isinstance(objects, list) and len(objects) <= 256, "labels.objects трябва да е списък до 256 обекта.")
    for index, item in enumerate(objects):
        _validate_object_label(item, index)
    _require(
        any(labels.get(name) is not None for name in LABEL_FIELDS+OPTIONAL_LABEL_FIELDS if name != "objects") or bool(objects),
        "примерът няма нито един етикет.",
    )


def _validate_frame(frame):
    _require(isinstance(frame, dict), "липсва frame.")
    _require(set(frame) <= {"id", "hash", "sourceSize", "sourceMtime"}, "frame има непознато поле.")
    identifier(frame.get("id"), "frame.id")
    _require(isinstance(frame.get("hash"), str) and HASH.fullmatch(frame["hash"]), "frame.hash трябва да е sha256:...")
    for name in ("sourceSize", "sourceMtime"):
        value = frame.get(name)
        _require(value is None or (_is_int(value) and value >= 0), f"frame.{name} трябва да е неотрицателно цяло число или null.")


def _validate_context(context):
    _require(isinstance(context, dict), "липсва context.")
    _require(set(context) <= set(CONTEXT_KEYS), "context има непознато поле.")
    for name in ("archiveId", "object", "telescope", "camera", "filter", "night", "group"):
        if name in context:
            _text(context[name], f"context.{name}", 800 if name == "group" else 200)
    for name in ("exposure", "gain", "pixelScale", "zeropoint"):
        if name in context and context[name] is not None:
            _require(_finite(context[name]), f"context.{name} трябва да е число или null.")
    if context.get("exposure") is not None:
        _require(float(context["exposure"]) >= 0, "context.exposure не може да е отрицателна.")
    if context.get("pixelScale") is not None:
        _require(float(context["pixelScale"]) > 0, "context.pixelScale трябва да е положителен.")
    if context.get("binning") is not None:
        _require(_is_int(context["binning"]) and context["binning"] >= 1, "context.binning трябва да е положително цяло число.")


def _validate_explanations(explanations):
    _require(isinstance(explanations, dict), "explanations трябва да е обект.")
    _require(set(explanations) <= set(EXPLANATION_KEYS), "explanations има непознато поле.")
    for name, value in explanations.items():
        _text(value, f"explanations.{name}", 1000)


def validate_example(example):
    """Strict validation matching ai-example.schema.json. Returns the example."""
    try:
        return _validate_example(example)
    except StorageError as error:
        raise DatasetError(str(error)) from error


def _validate_example(example):
    _require(isinstance(example, dict), "примерът трябва да е JSON обект.")
    _require(example.get("schema") == SCHEMA, f"примерът трябва да е {SCHEMA}.")
    allowed = {
        "schema",
        "exampleId",
        "frame",
        "context",
        "labels",
        "explanations",
        "labeler",
        "createdAt",
        "split",
        "origin",
        "datasetVersion",
        "notes",
    }
    _require(set(example) <= allowed, "примерът има непознато поле.")
    identifier(example.get("exampleId"), "exampleId")
    _validate_frame(example.get("frame"))
    _validate_context(example.get("context"))
    _validate_labels(example.get("labels"))
    if "explanations" in example:
        _validate_explanations(example["explanations"])
    labeler = example.get("labeler")
    _require(isinstance(labeler, dict), "липсва labeler.")
    _require(set(labeler) <= {"id", "name"}, "labeler има непознато поле.")
    identifier(labeler.get("id"), "labeler.id")
    if "name" in labeler:
        _text(labeler["name"], "labeler.name", 200)
    _timestamp(example.get("createdAt"), "createdAt")
    _enum(example.get("split"), "split", SPLITS)
    if "origin" in example:
        _enum(example["origin"], "origin", ("manual", "imported", "legacy"))
    if "datasetVersion" in example and example["datasetVersion"] is not None:
        _text(example["datasetVersion"], "datasetVersion", 64)
    if "notes" in example:
        _text(example["notes"], "notes", 2000)
    return example


def conflicts(first, second):
    """Label fields where two examples give different definitive values."""
    left = first["labels"]
    right = second["labels"]
    fields = []
    for name in ("quality", "light", "elongation", "defocus", "noise", "objectRisk"):
        a, b = left.get(name), right.get(name)
        if _is_number(a) and _is_number(b) and abs(float(a) - float(b)) > NUMERIC_TOLERANCE:
            fields.append(name)
    for name in ("cloud", "trail", "defects", "artifacts", "obstruction"):
        a, b = left.get(name), right.get(name)
        if a is not None and b is not None and a != b and a != "uncertain" and b != "uncertain":
            fields.append(name)
    return fields


def _label_stats(examples):
    stats = {}
    qualities = [float(item["labels"]["quality"]) for item in examples if _is_number(item["labels"]["quality"])]
    stats["quality"] = {
        "count": len(qualities),
        "mean": round(sum(qualities) / len(qualities), 6) if qualities else None,
    }
    for name, values in (
        ("cloud", ("clear", "cloud", "uncertain")),
        ("trail", ("none", "satellite", "aircraft", "uncertain")),
        ("defects", ("none", "present", "uncertain")),
        ("artifacts", ("none", "present", "uncertain")),
    ):
        stats[name] = {value: sum(1 for item in examples if item["labels"].get(name) == value) for value in values}
    stats["objects"] = {"count": sum(len(item["labels"].get("objects") or []) for item in examples)}
    return stats


class DatasetStore:
    """One dataset directory: manifest.json plus append-only examples.jsonl."""

    def __init__(self, root, audit=None):
        self.root = Path(root)
        self.manifest_path = self.root / "manifest.json"
        self.examples_path = self.root / "examples.jsonl"
        self.audit = audit

    def _log(self, action, actor, request_id=None, details=None):
        if self.audit is not None:
            self.audit.append(action, actor=actor, request_id=request_id, details=details or {})

    def exists(self):
        return self.manifest_path.exists()

    def create(self, dataset_id, version, *, actor, source="astro-archive", notes=None):
        identifier(dataset_id, "datasetId")
        _text(version, "version", 64, required=True)
        identifier(actor, "actor")
        if self.exists():
            raise DatasetError("Наборът вече съществува.")
        if self.examples_path.exists() and self.examples_path.stat().st_size > 0:
            raise DatasetError("Съществува examples.jsonl без manifest.")
        _require(source in ("astro-archive", "import", "legacy"), "Невалиден provenance.source.")
        atomic_write_text(self.examples_path, "")
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "datasetId": dataset_id,
            "version": version,
            "createdAt": utc_now(),
            "labelSchema": LABEL_SCHEMA,
            "status": "draft",
            "frozenAt": None,
            "exampleCount": 0,
            "hash": digest_text(""),
            "splits": {name: 0 for name in SPLITS},
            "frames": [],
            "labelers": [],
            "exclusions": [],
            "overrides": [],
            "stats": {},
            "provenance": {
                "source": source,
                "exportedAt": utc_now(),
                "exportedBy": actor,
                "filters": {},
                "notes": notes or "",
            },
        }
        manifest = self._derive(manifest, [], [], [])
        self._write(manifest)
        self._log("dataset.create", actor, details={"datasetId": dataset_id, "version": version})
        return manifest

    def _read_manifest(self):
        try:
            manifest = read_json(self.manifest_path)
        except StorageError as error:
            raise DatasetError(str(error)) from error
        _require(isinstance(manifest, dict) and manifest.get("schema") == MANIFEST_SCHEMA, "Невалиден dataset manifest.")
        return manifest

    def _write(self, manifest):
        atomic_write_json(self.manifest_path, manifest)

    def _examples(self):
        try:
            return read_jsonl(self.examples_path)
        except StorageError as error:
            raise DatasetError(str(error)) from error

    def _derive(self, base, examples, exclusions, overrides):
        effective=self._apply_overrides(examples,overrides)
        splits = {name: 0 for name in SPLITS}
        for example in effective:
            splits[example["split"]] += 1
        payload = {"examples": examples, "exclusions": exclusions, "overrides": overrides}
        manifest = dict(base)
        manifest.update(
            {
                "exampleCount": len(examples),
                "hash": digest_data(payload),
                "splits": splits,
                "frames": sorted({example["frame"]["id"] for example in examples}),
                "labelers": sorted({example["labeler"]["id"] for example in examples}),
                "exclusions": list(exclusions),
                "overrides": list(overrides),
                "stats": _label_stats(effective),
            }
        )
        return manifest

    def append(self, example, *, actor="system", allow_disagreement=False, request_id=None):
        identifier(actor, "actor")
        manifest = self._read_manifest()
        _require(manifest["status"] == "draft", "Замразен набор не приема нови примери.")
        try:
            validate_example(example)
        except StorageError as error:
            raise DatasetError(str(error)) from error
        examples = self._examples()
        _require(len(examples) < MAX_EXAMPLES, "Наборът е достигнал максималния брой примери.")
        for existing in examples:
            _require(existing["exampleId"] != example["exampleId"], "Дублиран exampleId.")
            if existing["frame"]["hash"] == example["frame"]["hash"] and existing["labeler"]["id"] == example["labeler"]["id"]:
                raise DatasetError("Същият кадър вече е маркиран от същия labeler.")
        if not allow_disagreement:
            for existing in examples:
                if existing["frame"]["hash"] != example["frame"]["hash"]:
                    continue
                fields = conflicts(existing, example)
                _require(not fields, "Противоречиви етикети за кадъра: " + ", ".join(fields) + ".")
        try:
            append_jsonl(self.examples_path, example)
        except StorageError as error:
            raise DatasetError(str(error)) from error
        examples.append(example)
        updated = self._derive(manifest, examples, manifest.get("exclusions", []), manifest.get("overrides", []))
        self._write(updated)
        self._log(
            "dataset.append",
            actor,
            request_id,
            {"exampleId": example["exampleId"], "frameId": example["frame"]["id"]},
        )
        return example

    def append_many(self, examples, *, actor="system", allow_disagreement=False, request_id=None):
        _require(isinstance(examples, list), "examples трябва да е списък.")
        added = []
        for example in examples:
            added.append(
                self.append(
                    example,
                    actor=actor,
                    allow_disagreement=allow_disagreement,
                    request_id=request_id,
                )
            )
        return {"added": len(added), "examples": added}

    def get(self, example_id):
        identifier(example_id, "exampleId")
        for example in self._examples():
            if example.get("exampleId") == example_id:
                return example
        raise DatasetError("Примерът не е намерен.")

    def examples(self, *, cursor=None, limit=100):
        _require(_is_int(limit) and 1 <= limit <= 500, "limit трябва да е цяло число от 1 до 500.")
        items = self._examples()
        start = 0
        if cursor is not None:
            try:
                start = int(cursor)
            except (TypeError, ValueError) as error:
                raise DatasetError("Невалиден cursor.") from error
            _require(0 <= start <= len(items), "Невалиден cursor.")
        page = items[start : start + limit]
        end = start + len(page)
        return {"items": page, "next": str(end) if end < len(items) else None, "total": len(items)}

    def manifest(self):
        return self._read_manifest()

    def freeze(self, *, actor, request_id=None):
        identifier(actor, "actor")
        manifest = self._read_manifest()
        _require(manifest["status"] == "draft", "Наборът вече е замразен.")
        examples = self._examples()
        _require(examples, "Празен набор не може да бъде замразен.")
        for example in examples:
            try:
                validate_example(example)
            except DatasetError as error:
                raise DatasetError("Наборът съдържа невалиден пример: " + str(error)) from error
        manifest = self._derive(manifest, examples, manifest.get("exclusions", []), manifest.get("overrides", []))
        manifest["status"] = "frozen"
        manifest["frozenAt"] = utc_now()
        self._write(manifest)
        self._log("dataset.freeze", actor, request_id, {"datasetId": manifest["datasetId"], "hash": manifest["hash"]})
        return manifest

    def rebuild(self, *, actor="system", force=False):
        identifier(actor, "actor")
        manifest = self._read_manifest()
        if manifest["status"] == "frozen" and not force:
            raise DatasetError("Замразен набор не се преизгражда без force.")
        examples = self._examples()
        for example in examples:
            validate_example(example)
        base = dict(manifest)
        if force:
            base["status"] = "draft"
            base["frozenAt"] = None
        rebuilt = self._derive(base, examples, manifest.get("exclusions", []), manifest.get("overrides", []))
        self._write(rebuilt)
        self._log("dataset.rebuild", actor, details={"force": bool(force), "exampleCount": len(examples)})
        return rebuilt

    def verify(self):
        errors = []
        try:
            manifest = self._read_manifest()
        except DatasetError as error:
            return {"ok": False, "errors": [str(error)], "exampleCount": 0}
        try:
            examples = self._examples()
        except DatasetError as error:
            return {"ok": False, "errors": [str(error)], "exampleCount": 0}
        seen = set()
        for index, example in enumerate(examples, 1):
            try:
                validate_example(example)
            except (DatasetError, StorageError) as error:
                errors.append(f"пример {index}: {error}")
                continue
            if example["exampleId"] in seen:
                errors.append(f"пример {index}: дублиран exampleId {example['exampleId']}")
            seen.add(example["exampleId"])
        expected = self._derive(
            manifest,
            examples,
            manifest.get("exclusions", []),
            manifest.get("overrides", []),
        )
        for name in ("exampleCount", "hash", "splits", "frames", "labelers"):
            if expected[name] != manifest.get(name):
                errors.append(f"manifest.{name} не съвпада с примерите.")
        if manifest.get("status") == "frozen" and not manifest.get("frozenAt"):
            errors.append("Замразеният набор няма frozenAt.")
        return {
            "ok": not errors,
            "errors": errors,
            "exampleCount": len(examples),
            "hash": expected["hash"],
            "frozen": manifest.get("status") == "frozen",
        }

    def exclude(self, frame_id, reason, *, actor="system", request_id=None):
        identifier(frame_id, "frameId")
        _text(reason, "reason", 400, required=True)
        identifier(actor, "actor")
        manifest = self._read_manifest()
        _require(manifest["status"] == "draft", "Замразен набор не се променя.")
        exclusions = list(manifest.get("exclusions", []))
        _require(
            all(item.get("frameId") != frame_id for item in exclusions),
            "Кадърът вече е изключен.",
        )
        exclusions.append({"frameId": frame_id, "reason": reason})
        examples = self._examples()
        updated = self._derive(manifest, examples, exclusions, manifest.get("overrides", []))
        self._write(updated)
        self._log("dataset.exclude", actor, request_id, {"frameId": frame_id})
        return updated

    def override(self, example_id, field, value, reason, *, actor="system", request_id=None):
        identifier(example_id, "exampleId")
        _enum(field, "field", LABEL_FIELDS + ("defocus",))
        _text(reason, "reason", 400, required=True)
        identifier(actor, "actor")
        manifest = self._read_manifest()
        _require(manifest["status"] == "draft", "Замразен набор не се променя.")
        self.get(example_id)
        probe = {
            "quality": None,
            "cloud": None,
            "trail": None,
            "light": None,
            "elongation": None,
            "defects": None,
            "artifacts": None,
            "objects": [],
        }
        probe[field] = value
        _validate_labels(probe)
        overrides = list(manifest.get("overrides", []))
        overrides.append(
            {
                "exampleId": example_id,
                "field": field,
                "value": value,
                "reason": reason,
                "actor": actor,
                "createdAt": utc_now(),
            }
        )
        examples = self._examples()
        updated = self._derive(manifest, examples, manifest.get("exclusions", []), overrides)
        self._write(updated)
        self._log("dataset.override", actor, request_id, {"exampleId": example_id, "field": field})
        return updated

    def effective_examples(self):
        """Examples with dataset-level overrides applied; source stays untouched."""
        manifest = self._read_manifest()
        examples = self._examples()
        return self._apply_overrides(examples,manifest.get('overrides',[]))

    @staticmethod
    def _apply_overrides(examples,overrides):
        import copy
        by_id = {example['exampleId']:copy.deepcopy(example) for example in examples}
        for item in overrides:
            example = by_id.get(item.get("exampleId"))
            if example is None:
                continue
            example = dict(example)
            example["labels"] = dict(example["labels"])
            if item['field']=='_record':example.update(item['value'])
            else:example["labels"][item["field"]] = item["value"]
            example["overridden"] = True
            by_id[item["exampleId"]] = example
        return [by_id[example["exampleId"]] for example in examples]

    def revise(self,example_id,*,labels,split,notes,actor,expected_revision,request_id=None):
        """Atomic full-label correction; original JSONL records stay untouched."""
        import copy
        identifier(actor,'actor');manifest=self._read_manifest()
        _require(manifest['status']=='draft','Замразен набор не се редактира; създай копие.')
        current=next((e for e in self.effective_examples() if e['exampleId']==example_id),None)
        _require(current is not None,'Примерът не е намерен.')
        _require(expected_revision==digest_data(current),'Оценката е променена от друг прозорец. Обнови я.')
        updated=copy.deepcopy(current);updated.pop('overridden',None)
        updated.update(labels=copy.deepcopy(labels),split=split,notes=notes)
        validate_example(updated)
        changes=dict(labels=updated['labels'],split=updated['split'],notes=updated['notes'])
        overrides=list(manifest.get('overrides',[]))
        overrides.append(dict(exampleId=example_id,field='_record',value=changes,reason='Manual label revision',actor=actor,createdAt=utc_now()))
        self._write(self._derive(manifest,self._examples(),manifest.get('exclusions',[]),overrides))
        self._log('dataset.revise',actor,request_id,dict(exampleId=example_id,frameId=updated['frame']['id'],previousRevision=expected_revision))
        return next(e for e in self.effective_examples() if e['exampleId']==example_id)

    def copy_to(self,destination,dataset_id,version,*,actor,request_id=None):
        """Create a draft version from effective labels, preserving the source."""
        import copy
        manifest=self.manifest();_require(self.verify()['ok'],'Изходният набор не премина проверката.')
        items=copy.deepcopy(self.effective_examples())
        for e in items:e.pop('overridden',None);validate_example(e)
        target=DatasetStore(destination,self.audit)
        target.create(dataset_id,version,actor=actor,source='import',notes='Copy of '+manifest['datasetId']+' / '+manifest['version']+' / '+manifest['hash'])
        atomic_write_text(target.examples_path,''.join(canonical(e)+'\n' for e in items))
        base=target.manifest();target._write(target._derive(base,items,manifest.get('exclusions',[]),[]))
        self._log('dataset.fork',actor,request_id,dict(sourceDatasetId=manifest['datasetId'],sourceHash=manifest['hash'],datasetId=dataset_id))
        return target.manifest()

    def contradictions(self):
        """Pairs of examples for the same frame whose labels disagree."""
        examples = self._examples()
        found = []
        for index, first in enumerate(examples):
            for second in examples[index + 1 :]:
                if first["frame"]["hash"] != second["frame"]["hash"]:
                    continue
                fields = conflicts(first, second)
                if fields:
                    found.append(
                        {
                            "frameId": first["frame"]["id"],
                            "examples": [first["exampleId"], second["exampleId"]],
                            "fields": fields,
                        }
                    )
        return found
