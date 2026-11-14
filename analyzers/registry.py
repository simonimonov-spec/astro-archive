"""Versioned model registry with explicit activation and rollback.

Installing a model never activates it. Activation is a separate, auditable
operation. Legacy linear models can be stored for comparison but can never be
activated as the new neural evaluator.
"""
from __future__ import annotations

import threading
from pathlib import Path

from . import legacy, neural
from .storage import (
    StorageError,
    atomic_write_json,
    component,
    digest_data,
    identifier,
    read_json,
    utc_now,
)

META_SCHEMA = "astro-ai-model-meta-v1"
STATE_SCHEMA = "astro-ai-registry-state-v1"
MLP_FORMAT = "astro-ai-mlp-v1"


class RegistryError(ValueError):
    """Invalid model, missing version or inconsistent registry state."""


def _require(condition, message):
    if not condition:
        raise RegistryError(message)


def _component(value, name):
    try:
        return component(value, name)
    except StorageError as error:
        raise RegistryError(str(error)) from error


def _identifier(value, name):
    try:
        return identifier(value, name)
    except StorageError as error:
        raise RegistryError(str(error)) from error


def _descriptor(meta):
    return {
        "modelId": meta["modelId"],
        "version": meta["version"],
        "digest": meta["digest"],
        "format": meta["format"],
        "legacy": bool(meta.get("legacy")),
    }


class ModelRegistry:
    def __init__(self, root, audit=None):
        self.root = Path(root)
        self.models_dir = self.root / "models"
        self.state_path = self.root / "state.json"
        self.audit = audit
        self.lock = threading.Lock()

    def _log(self, action, actor, request_id=None, details=None):
        if self.audit is not None:
            self.audit.append(action, actor=actor, request_id=request_id, details=details or {})

    def _model_path(self, model_id, version):
        return self.models_dir / model_id / version / "model.json"

    def _meta_path(self, model_id, version):
        return self.models_dir / model_id / version / "meta.json"

    def weights_path(self,model_id,version):
        return self.models_dir/_component(model_id,'modelId')/_component(version,'version')/'weights.npz'

    def _validate(self, data):
        if not isinstance(data, dict):
            raise RegistryError("Моделът трябва да е JSON обект.")
        fmt = data.get("format")
        if fmt=='astro-ai-resnet-v1':
            from .. import resnet_model
            resnet_model.validate(data)
            return False,dict(modelId=data['modelId'],version=data['version'])
        if fmt == MLP_FORMAT:
            try:
                neural.validate(data)
            except neural.ModelError as error:
                raise RegistryError(str(error)) from error
            _require(
                not (data.get("provenance") or {}).get("legacy"),
                "MLP модел не може да бъде маркиран като legacy.",
            )
            return False, {"modelId": data["modelId"], "version": data["version"]}
        if fmt in legacy.LEGACY_MODEL_FORMATS:
            try:
                summary = legacy.validate_legacy_model(data)
            except legacy.LegacyError as error:
                raise RegistryError(str(error)) from error
            return True, summary
        raise RegistryError("Неподдържан формат на модел.")

    def install(self, data, *, actor, expected_digest=None, request_id=None, weights=None):
        _identifier(actor, "actor")
        with self.lock:
            is_legacy, summary = self._validate(data)
            model_id = _component(summary.get("modelId"), "modelId")
            version = _component(summary.get("version"), "version")
            digest = digest_data(data)
            if expected_digest is not None:
                _require(
                    isinstance(expected_digest, str) and expected_digest == digest,
                    "Контролната сума на модела не съвпада.",
                )
            path = self._model_path(model_id, version)
            meta_path = self._meta_path(model_id, version)
            if data['format']=='astro-ai-resnet-v1':
                from .. import resnet_model
                _require(isinstance(weights,bytes),'Липсват ResNet тегла.')
                resnet_model.load_weights(data,weights)
            if path.exists() or meta_path.exists():
                _require(path.exists() and meta_path.exists(), "Непълен предишен запис на модела.")
                existing = read_json(meta_path)
                _require(existing.get("digest") == digest, "Версията вече съществува с различно съдържание.")
                return _descriptor(existing)
            meta = {
                "schema": META_SCHEMA,
                "modelId": model_id,
                "version": version,
                "format": data["format"],
                "digest": digest,
                "legacy": is_legacy,
                "installedAt": utc_now(),
                "installedBy": actor,
                "provenance": data.get("provenance") or {},
                "metrics": data.get("metrics") or {},
            }
            if data['format']=='astro-ai-resnet-v1':
                from .storage import atomic_write_text
                import os,uuid
                weight_path=self.weights_path(model_id,version);weight_path.parent.mkdir(parents=True,exist_ok=True)
                temp=weight_path.with_name(uuid.uuid4().hex+'.tmp')
                try:
                    with temp.open('xb') as handle:handle.write(weights);handle.flush();os.fsync(handle.fileno())
                    os.replace(temp,weight_path)
                finally:temp.unlink(missing_ok=True)
            atomic_write_json(path, data)
            atomic_write_json(meta_path, meta)
            self._log(
                "model.install",
                actor,
                request_id,
                {"modelId": model_id, "version": version, "digest": digest, "legacy": is_legacy},
            )
            return _descriptor(meta)

    def models(self):
        found = []
        if not self.models_dir.exists():
            return found
        for meta_path in sorted(self.models_dir.glob("*/*/meta.json")):
            try:
                found.append(_descriptor(read_json(meta_path)))
            except (StorageError, KeyError, RegistryError):
                continue
        return found

    def get(self, model_id, version):
        model_id = _component(model_id, "modelId")
        version = _component(version, "version")
        meta_path = self._meta_path(model_id, version)
        if not meta_path.exists():
            raise RegistryError("Моделът не е намерен.")
        return _descriptor(read_json(meta_path))

    def _state(self):
        if not self.state_path.exists():
            return {
                "schema": STATE_SCHEMA,
                "active": None,
                "previous": None,
                "updatedAt": None,
                "updatedBy": None,
            }
        state = read_json(self.state_path)
        _require(isinstance(state, dict) and state.get("schema") == STATE_SCHEMA, "Невалидно registry състояние.")
        return state

    def _write_state(self, state):
        atomic_write_json(self.state_path, state)

    def state(self):
        return self._state()

    def active(self):
        return self._state().get("active")

    def _checked(self, descriptor):
        _require(isinstance(descriptor, dict), "Липсва описание на модел.")
        model_id = _component(descriptor.get("modelId"), "modelId")
        version = _component(descriptor.get("version"), "version")
        meta_path = self._meta_path(model_id, version)
        path = self._model_path(model_id, version)
        _require(meta_path.exists() and path.exists(), "Файловете на модела липсват.")
        meta = read_json(meta_path)
        _require(meta.get("digest") == descriptor.get("digest"), "Описанието не съвпада с meta файла.")
        _require(digest_data(read_json(path)) == meta.get("digest"), "Контролната сума на модела не съвпада.")
        data=read_json(path)
        if data.get('format')=='astro-ai-resnet-v1':
            from .storage import digest_file
            weights=self.weights_path(model_id,version)
            _require(weights.is_file() and not weights.is_symlink() and weights.stat().st_size==data['weights']['size'] and digest_file(weights)==data['weights']['hash'],'ResNet теглата липсват или са променени.')
        return meta

    def activate(self, model_id, version, *, actor, request_id=None):
        _identifier(actor, "actor")
        with self.lock:
            descriptor = self.get(model_id, version)
            meta = self._checked(descriptor)
            _require(not meta.get("legacy"), "Legacy модел не може да бъде активиран като новия AI оценител.")
            state = self._state()
            if state.get("active") == descriptor:
                return state
            state["previous"] = state.get("active")
            state["active"] = descriptor
            state["updatedAt"] = utc_now()
            state["updatedBy"] = actor
            self._write_state(state)
            self._log(
                "model.activate",
                actor,
                request_id,
                {"modelId": descriptor["modelId"], "version": descriptor["version"], "digest": descriptor["digest"]},
            )
            return state

    def rollback(self, *, actor, request_id=None):
        _identifier(actor, "actor")
        with self.lock:
            state = self._state()
            previous = state.get("previous")
            _require(isinstance(previous, dict), "Няма предишна версия за връщане.")
            self._checked(previous)
            old_active = state.get("active")
            state["active"] = previous
            state["previous"] = old_active
            state["updatedAt"] = utc_now()
            state["updatedBy"] = actor
            self._write_state(state)
            self._log(
                "model.rollback",
                actor,
                request_id,
                {"modelId": previous["modelId"], "version": previous["version"], "digest": previous["digest"]},
            )
            return state

    def load_active(self):
        state = self._state()
        active = state.get("active")
        if active is None:
            return None
        self._checked(active)
        data = read_json(self._model_path(active["modelId"], active["version"]))
        return {"model": data, "descriptor": active}

    def verify(self):
        errors = []
        count = 0
        if self.models_dir.exists():
            for meta_path in sorted(self.models_dir.glob("*/*/meta.json")):
                count += 1
                try:
                    meta = read_json(meta_path)
                    path = meta_path.with_name("model.json")
                    if not path.exists():
                        errors.append(f"{meta_path.parent.name}: липсва model.json")
                        continue
                    if digest_data(read_json(path)) != meta.get("digest"):
                        errors.append(f"{meta_path.parent.parent.name}/{meta_path.parent.name}: контролната сума не съвпада")
                    self._checked(_descriptor(meta))
                except (StorageError, KeyError,RegistryError) as error:
                    errors.append(f"{meta_path.parent.name}: {error}")
        state = None
        try:
            state = self._state()
            for name in ("active", "previous"):
                if state.get(name) is not None:
                    self._checked(state[name])
        except (RegistryError, StorageError) as error:
            errors.append(f"state: {error}")
        return {
            "ok": not errors,
            "errors": errors,
            "models": count,
            "active": state.get("active") if state else None,
            "previous": state.get("previous") if state else None,
        }
