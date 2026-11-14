"""Append-only audit log for model, dataset and evaluation events."""
from __future__ import annotations

import threading
import uuid
from pathlib import Path

from .storage import StorageError, append_jsonl, identifier, read_jsonl, utc_now


class AuditLog:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / "audit.jsonl"
        self.lock = threading.Lock()

    def append(self, action, *, actor, request_id=None, details=None):
        identifier(action, "audit action")
        identifier(actor, "audit actor")
        if request_id is not None:
            identifier(request_id, "request id")
        if details is None:
            details = {}
        if not isinstance(details, dict):
            raise StorageError("audit details трябва да е обект.")
        record = {
            "schema": "astro-ai-audit-v1",
            "id": uuid.uuid4().hex,
            "at": utc_now(),
            "action": action,
            "actor": actor,
            "requestId": request_id,
            "details": details,
        }
        with self.lock:
            append_jsonl(self.path, record)
        return record

    def list(self, *, cursor=None, limit=100):
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise StorageError("limit трябва да е цяло число от 1 до 100.")
        items = read_jsonl(self.path)
        start = 0
        if cursor is not None:
            try:
                start = int(cursor)
            except (TypeError, ValueError) as error:
                raise StorageError("Невалиден cursor.") from error
            if start < 0 or start > len(items):
                raise StorageError("Невалиден cursor.")
        page = items[start : start + limit]
        end = start + len(page)
        return {
            "items": page,
            "next": str(end) if end < len(items) else None,
            "total": len(items),
        }

    def read(self):
        return read_jsonl(self.path)

    def count(self):
        return len(read_jsonl(self.path))
