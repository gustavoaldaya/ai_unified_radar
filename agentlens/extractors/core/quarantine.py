"""Quarantine sink for records that fail schema validation.

Invalid records are written to ``raw/_quarantine/{extractor}/dt=.../`` as JSONL
and the batch continues (AC-1.3-5). One bad record never aborts the run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any

from extractors.core.storage import StorageBackend


@dataclass(frozen=True)
class QuarantinedRecord:
    raw: dict[str, Any]
    error: str


class Quarantine:
    def __init__(
        self,
        backend: StorageBackend,
        extractor: str,
        root: str = "_quarantine",
    ) -> None:
        self._backend = backend
        self._extractor = extractor
        self._root = root

    def path(self, dt: date) -> str:
        return f"{self._root}/{self._extractor}/dt={dt.isoformat()}/quarantine.jsonl"

    def write(self, records: list[QuarantinedRecord], dt: date) -> str | None:
        if not records:
            return None
        lines = [
            json.dumps({"error": r.error, "raw": r.raw}, default=str, sort_keys=True)
            for r in records
        ]
        rel = self.path(dt)
        self._backend.write_text_atomic(rel, "\n".join(lines))
        return rel
