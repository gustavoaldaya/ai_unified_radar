"""Tests for ext-m365-usage (M1.5) -- AC-1.5-2, -4, -5, -6."""

from __future__ import annotations

import os
from datetime import date
from typing import Any

import pyarrow.parquet as pq

from extractors.core.config import Settings
from extractors.core.storage import LocalStorageBackend
from extractors.m365_usage import M365UsageExtractor

FIXTURES_ROOT = os.path.join(os.path.dirname(__file__), "fixtures")


def _make(tmp_path: Any) -> M365UsageExtractor:
    settings = Settings(
        use_fixtures=True, raw_root=str(tmp_path), fixtures_root=FIXTURES_ROOT
    )
    return M365UsageExtractor(
        settings=settings, backend=LocalStorageBackend(str(tmp_path))
    )


def test_window_start_is_three_day_window() -> None:
    assert M365UsageExtractor.window_start(date(2026, 6, 17)) == date(2026, 6, 15)


def test_dedup_by_package_id_and_report_date(tmp_path: Any) -> None:
    extractor = _make(tmp_path)
    result = extractor.run(run_date=date(2026, 6, 18))
    # hr 15/16/17 (3) + fin 17 (1) + orphan 17 (1) = 5; one full duplicate dropped
    assert result.record_count == 5
    assert result.duplicate_count == 1  # AC-1.5-2 (late-arriving overlap idempotent)
    assert result.invalid_count == 1  # malformed null package_id quarantined

    table = pq.read_table(result.written_path)
    pairs = list(
        zip(
            table.column("package_id").to_pylist(),
            table.column("report_date").to_pylist(),
            strict=False,
        )
    )
    assert len(pairs) == len(set(pairs))  # no dup (package_id, report_date)


def test_snapshot_path_and_anonymization_flag(tmp_path: Any) -> None:
    extractor = _make(tmp_path)
    result = extractor.run(run_date=date(2026, 6, 18))
    assert "m365-usage/dt=2026-06-17" in (result.written_path or "")  # AC-1.5-5
    table = pq.read_table(result.written_path)
    assert "is_anonymized" in table.schema.names  # AC-1.5-4
    assert any(table.column("is_anonymized").to_pylist())  # at least one anonymized row


def test_reconciliation_flags_orphans(tmp_path: Any) -> None:
    extractor = _make(tmp_path)
    result = extractor.run(run_date=date(2026, 6, 18))
    table = pq.read_table(result.written_path)
    usage_ids = set(table.column("package_id").to_pylist())
    registry_ids = {"pkg-hr-001", "pkg-fin-002", "pkg-it-003", "pkg-orphan-004"}
    orphans = M365UsageExtractor.reconcile_orphans(usage_ids, registry_ids)
    assert orphans == {"pkg-orphan-999"}  # AC-1.5-6
