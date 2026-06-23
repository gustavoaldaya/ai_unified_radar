"""Per-extractor watermark.

Persisted at ``raw/_watermarks/{extractor}.json`` (timestamp of last success +
cursor). Read at the start of a run; persisted **only after** a successful write,
atomically (AC-1.3-2). Atomicity is delegated to the storage backend.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from extractors.core.storage import StorageBackend


@dataclass(frozen=True)
class Watermark:
    extractor: str
    last_success: str | None = None  # ISO-8601 UTC
    cursor: str | None = None

    @classmethod
    def empty(cls, extractor: str) -> Watermark:
        return cls(extractor=extractor)

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, payload: str) -> Watermark:
        data = json.loads(payload)
        return cls(
            extractor=data["extractor"],
            last_success=data.get("last_success"),
            cursor=data.get("cursor"),
        )


class WatermarkStore:
    def __init__(self, backend: StorageBackend, root: str = "_watermarks") -> None:
        self._backend = backend
        self._root = root

    def path(self, extractor: str) -> str:
        return f"{self._root}/{extractor}.json"

    def read(self, extractor: str) -> Watermark:
        raw = self._backend.read_text(self.path(extractor))
        if raw is None:
            return Watermark.empty(extractor)
        return Watermark.from_json(raw)

    def persist(self, watermark: Watermark) -> None:
        self._backend.write_text_atomic(
            self.path(watermark.extractor), watermark.to_json()
        )
