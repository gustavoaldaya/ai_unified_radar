"""End-to-end fixtures run of the reference extractor.

Covers AC-1.3-2 (watermark only after success), AC-1.3-3 (dedup), AC-1.3-5
(quarantine without aborting), AC-1.3-6 (typed partitioned Parquet), AC-1.3-7
(OTel gen_ai.* + agent.id) and AC-1.3-8 (fixtures end-to-end, LBD, no network).
"""

from __future__ import annotations

from datetime import date

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from conftest import FIXTURES_ROOT, SampleExtractor
from extractors.core.config import Settings
from extractors.core.storage import LocalStorageBackend
from extractors.core.watermark import Watermark


def _make(tmp_path) -> SampleExtractor:
    settings = Settings(
        use_fixtures=True, raw_root=str(tmp_path), fixtures_root=FIXTURES_ROOT
    )
    return SampleExtractor(
        settings=settings, backend=LocalStorageBackend(str(tmp_path))
    )


def test_fixtures_end_to_end(tmp_path) -> None:
    extractor = _make(tmp_path)
    # Run on a Thursday -> LBD must be the Wednesday before (no network call).
    result = extractor.run(run_date=date(2026, 6, 18))

    assert result.target_date == date(2026, 6, 17)  # AC-1.3-8 LBD
    assert result.record_count == 3  # pkg-001 (deduped), pkg-002, pkg-003
    assert result.duplicate_count == 1  # AC-1.3-3
    assert result.invalid_count == 1  # AC-1.3-5 (null package_id quarantined)
    assert result.drift_field_count == 1  # pkg-001 carries unmapped "region"
    assert result.written_path is not None


def test_parquet_typed_and_deduplicated(tmp_path) -> None:
    extractor = _make(tmp_path)
    result = extractor.run(run_date=date(2026, 6, 18))
    table = pq.read_table(result.written_path)

    # AC-1.3-6: typed schema, partitioned path by date.
    assert "dt=2026-06-17" in (result.written_path or "")
    assert table.schema.field("enabled").type == pa.bool_()
    assert table.schema.field("interaction_count").type == pa.int64()
    assert table.schema.field("package_id").type == pa.string()
    assert "_drift" in table.schema.names

    # AC-1.3-3: zero duplicate (package_id, last_modified) pairs.
    pairs = list(
        zip(
            table.column("package_id").to_pylist(),
            table.column("last_modified").to_pylist(),
        )
    )
    assert len(pairs) == len(set(pairs))


def test_quarantine_file_written(tmp_path) -> None:
    extractor = _make(tmp_path)
    extractor.run(run_date=date(2026, 6, 18))
    qfile = (
        tmp_path
        / "_quarantine"
        / "sample_agents"
        / "dt=2026-06-17"
        / "quarantine.jsonl"
    )
    assert qfile.exists()  # AC-1.3-5


def test_watermark_persisted_after_success(tmp_path) -> None:
    extractor = _make(tmp_path)
    assert extractor.current_watermark() == Watermark.empty(
        "sample_agents"
    )  # read at start
    result = extractor.run(run_date=date(2026, 6, 18))
    persisted = extractor.current_watermark()
    assert persisted.cursor == "2026-06-17"  # AC-1.3-2
    assert persisted.last_success is not None
    assert result.watermark == persisted


def test_watermark_not_persisted_on_write_failure(tmp_path) -> None:
    """AC-1.3-2: a failed write must leave the watermark untouched."""

    class FailingBackend(LocalStorageBackend):
        def write_parquet(self, rel_path: str, table: pa.Table) -> str:
            raise OSError("disk full")

    settings = Settings(
        use_fixtures=True, raw_root=str(tmp_path), fixtures_root=FIXTURES_ROOT
    )
    extractor = SampleExtractor(
        settings=settings, backend=FailingBackend(str(tmp_path))
    )
    with pytest.raises(OSError):
        extractor.run(run_date=date(2026, 6, 18))
    assert extractor.current_watermark() == Watermark.empty("sample_agents")


def test_otel_span_has_gen_ai_and_agent_id(tmp_path, memory_spans) -> None:
    extractor = _make(tmp_path)
    extractor.run(run_date=date(2026, 6, 18))
    spans = memory_spans()
    assert spans, "expected at least one span"
    span = spans[0]
    attrs = dict(span.attributes)
    assert attrs.get("gen_ai.system") == "agentlens"  # AC-1.3-7
    assert attrs.get("gen_ai.operation.name") == "extract"
    assert attrs.get("agent.id") == "pkg-001"
