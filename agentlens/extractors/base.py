"""``BaseExtractor`` -- the abstract pipeline every extractor extends.

Pipeline (materialises ``architecture/Extractor Architecture``):

    auth -> paginate -> rate_limit -> track_watermark -> validate
         -> dedup -> write_parquet (raw zone)

Each step is a composable method (AC-1.3-1). ``run()`` orchestrates them and is
fixtures-aware: under ``USE_FIXTURES`` it seeds the watermark to the last
business day and reads ``tests/fixtures/{extractor}/`` instead of calling APIs
(AC-1.3-8). The live ``paginate()`` is the only required override.
"""

from __future__ import annotations

import glob
import json
import os
import types
import typing
from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, ClassVar

import pyarrow as pa
from pydantic import ValidationError

from extractors.core.calendar import last_business_day
from extractors.core.config import Settings
from extractors.core.dedup import dedup as dedup_records
from extractors.core.dedup import dedup_key
from extractors.core.quarantine import Quarantine, QuarantinedRecord
from extractors.core.ratelimit import RateLimiter
from extractors.core.storage import LocalStorageBackend, StorageBackend
from extractors.core.telemetry import extractor_span
from extractors.core.watermark import Watermark, WatermarkStore
from schemas.base import RawRecord

Page = list[dict[str, Any]]


@dataclass(frozen=True)
class RunResult:
    extractor: str
    target_date: date
    written_path: str | None
    record_count: int
    duplicate_count: int
    invalid_count: int
    drift_field_count: int
    watermark: Watermark


def _unwrap(annotation: Any) -> Any:
    """Reduce ``X | None`` / ``Optional[X]`` to ``X``; containers -> ``str``."""
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        return _unwrap(args[0]) if len(args) == 1 else str
    if origin in (list, dict, tuple, set):
        return str
    return annotation if isinstance(annotation, type) else str


def _column_kind(annotation: Any) -> str:
    base = _unwrap(annotation)
    if isinstance(base, type) and issubclass(base, bool):
        return "bool"
    if isinstance(base, type) and issubclass(base, int):
        return "int"
    if isinstance(base, type) and issubclass(base, float):
        return "float"
    return "str"


_ARROW_TYPE = {
    "bool": pa.bool_(),
    "int": pa.int64(),
    "float": pa.float64(),
    "str": pa.string(),
}


class BaseExtractor(ABC):
    # --- subclass contract ---
    name: ClassVar[str]
    schema: ClassVar[type[RawRecord]]
    source_path: ClassVar[str]  # e.g. "m365/agent_registry"
    source_id_field: ClassVar[str]
    timestamp_field: ClassVar[str]
    agent_id_field: ClassVar[str | None] = None

    def __init__(
        self,
        settings: Settings | None = None,
        backend: StorageBackend | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self.settings = settings or Settings.from_env()
        self.backend = backend or LocalStorageBackend(self.settings.raw_root)
        self.rate_limiter = rate_limiter or RateLimiter()
        self.watermarks = WatermarkStore(self.backend)
        self.quarantine = Quarantine(self.backend, self.name)

    # ------------------------------------------------------------------ #
    # Pipeline steps (composable; one unit test each -- AC-1.3-1)        #
    # ------------------------------------------------------------------ #
    def auth(self) -> None:
        """No-op under fixtures. Live auth runs through the backend (MI) and a
        subclass Key Vault secret fetch."""
        return

    def rate_limit(self) -> RateLimiter:
        return self.rate_limiter

    def current_watermark(self) -> Watermark:
        return self.watermarks.read(self.name)

    def track_watermark(self, watermark: Watermark) -> None:
        """Persist atomically. Called ONLY after a successful write (AC-1.3-2)."""
        self.watermarks.persist(watermark)

    @abstractmethod
    def paginate(self, *, since: str | None) -> Iterator[Page]:
        """Yield raw pages from the live API. Must use ``self.rate_limit()``."""

    def validate(
        self, raw_records: Sequence[dict[str, Any]]
    ) -> tuple[list[RawRecord], list[QuarantinedRecord]]:
        valid: list[RawRecord] = []
        invalid: list[QuarantinedRecord] = []
        for raw in raw_records:
            try:
                valid.append(self.schema.model_validate(raw))
            except ValidationError as exc:
                invalid.append(QuarantinedRecord(raw=raw, error=exc.json()))
        return valid, invalid

    def dedup(self, records: Sequence[RawRecord]) -> list[RawRecord]:
        return dedup_records(records, self._dedup_key)

    def write_parquet(self, records: Sequence[RawRecord], dt: date) -> str | None:
        if not records:
            return None
        return self.backend.write_parquet(
            self._partition_path(dt), self._to_table(records)
        )

    # ------------------------------------------------------------------ #
    # Orchestration                                                      #
    # ------------------------------------------------------------------ #
    def run(self, run_date: date | None = None) -> RunResult:
        effective_run_date = run_date or datetime.now(timezone.utc).date()
        with extractor_span(
            self.name, attributes={"agentlens.use_fixtures": self.settings.use_fixtures}
        ) as span:
            self.auth()
            previous = self.current_watermark()

            if self.settings.use_fixtures:
                target = last_business_day(
                    effective_run_date,
                    holidays=self.settings.holidays,
                    business_week_mon_fri=self.settings.business_week_mon_fri,
                )
                pages: Iterator[Page] = self._fixture_pages()
            else:
                target = effective_run_date
                pages = self.paginate(since=previous.cursor)

            raw = [record for page in pages for record in page]
            valid, invalid = self.validate(raw)
            self.quarantine.write(invalid, target)
            deduped = self.dedup(valid)
            duplicate_count = len(valid) - len(deduped)
            drift_field_count = sum(1 for record in deduped if record.drift_fields)

            if span is not None and self.agent_id_field and deduped:
                first = getattr(deduped[0], self.agent_id_field, None)
                if first is not None:
                    span.set_attribute("agent.id", str(first))

            written = self.write_parquet(deduped, target)

            watermark = previous
            if written is not None:
                watermark = Watermark(
                    extractor=self.name,
                    last_success=datetime.now(timezone.utc).isoformat(),
                    cursor=target.isoformat(),
                )
                self.track_watermark(watermark)

            return RunResult(
                extractor=self.name,
                target_date=target,
                written_path=written,
                record_count=len(deduped),
                duplicate_count=duplicate_count,
                invalid_count=len(invalid),
                drift_field_count=drift_field_count,
                watermark=watermark,
            )

    # ------------------------------------------------------------------ #
    # Helpers                                                            #
    # ------------------------------------------------------------------ #
    def _dedup_key(self, record: RawRecord) -> str:
        source_id = str(getattr(record, self.source_id_field))
        timestamp = str(getattr(record, self.timestamp_field))
        return dedup_key(source_id, timestamp)

    def _fixture_pages(self) -> Iterator[Page]:
        base = os.path.join(self.settings.fixtures_root, self.name)
        for fixture in sorted(glob.glob(os.path.join(base, "*.json"))):
            with open(fixture, encoding="utf-8") as handle:
                data = json.load(handle)
            yield data if isinstance(data, list) else [data]

    def _partition_path(self, dt: date) -> str:
        return f"{self.source_path}/dt={dt.isoformat()}/part-0.parquet"

    def _column_kinds(self) -> dict[str, str]:
        return {
            name: _column_kind(info.annotation)
            for name, info in self.schema.model_fields.items()
        }

    def _arrow_schema(self, kinds: dict[str, str]) -> pa.Schema:
        fields = [pa.field(name, _ARROW_TYPE[kind]) for name, kind in kinds.items()]
        fields.append(pa.field("_drift", pa.string()))
        return pa.schema(fields)

    def _to_table(self, records: Sequence[RawRecord]) -> pa.Table:
        kinds = self._column_kinds()
        rows: list[dict[str, Any]] = []
        for record in records:
            declared = record.declared_dump()
            row = {
                name: _coerce(declared.get(name), kind) for name, kind in kinds.items()
            }
            row["_drift"] = record.drift_json()
            rows.append(row)
        return pa.Table.from_pylist(rows, schema=self._arrow_schema(kinds))


def _coerce(value: Any, kind: str) -> Any:
    if value is None:
        return None
    if kind in ("int", "float", "bool"):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, (list, dict)):
        return json.dumps(value, default=str, sort_keys=True)
    return str(getattr(value, "value", value))
