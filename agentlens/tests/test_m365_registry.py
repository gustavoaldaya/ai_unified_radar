"""Tests for ext-m365-registry (M1.4) -- AC-1.4-1 … AC-1.4-6."""

from __future__ import annotations

from datetime import date
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from extractors.base import BaseExtractor
from extractors.core.config import Settings
from extractors.core.storage import LocalStorageBackend
from extractors.m365_registry import AgentRegistryLicenseError, M365RegistryExtractor
from schemas.m365_registry import RawAgentPackage

FIXTURES_ROOT = __import__("os").path.join(
    __import__("os").path.dirname(__file__), "fixtures"
)


def _make(tmp_path: Any) -> M365RegistryExtractor:
    settings = Settings(
        use_fixtures=True, raw_root=str(tmp_path), fixtures_root=FIXTURES_ROOT
    )
    return M365RegistryExtractor(
        settings=settings, backend=LocalStorageBackend(str(tmp_path))
    )


# --- AC-1.4-1: extends BaseExtractor, reuses the base pipeline ------------ #
def test_extends_base_extractor(tmp_path: Any) -> None:
    extractor = _make(tmp_path)
    assert isinstance(extractor, BaseExtractor)
    result = extractor.run(run_date=date(2026, 6, 18))
    assert result.written_path is not None


# --- AC-1.4-3: schema validates and rejects malformed -------------------- #
def test_schema_validates_and_rejects_malformed(tmp_path: Any) -> None:
    extractor = _make(tmp_path)
    valid, invalid = extractor.validate(
        [
            {"package_id": "pkg-x", "agent_type": "copilot_studio"},
            {"package_id": None, "agent_type": "copilot_studio"},  # null id -> invalid
            {
                "package_id": "pkg-y",
                "agent_type": "not_a_real_type",
            },  # bad enum -> invalid
        ]
    )
    assert len(valid) == 1
    assert len(invalid) == 2


# --- AC-1.4-2: full catalog pagination across >1 page -------------------- #
def test_pagination_follows_nextlink(tmp_path: Any) -> None:
    pages: dict[str, tuple[int, dict[str, Any], float | None]] = {
        "https://graph/p1": (
            200,
            {
                "value": [{"package_id": "a", "agent_type": "copilot_studio"}],
                "@odata.nextLink": "https://graph/p2",
            },
            None,
        ),
        "https://graph/p2": (
            200,
            {"value": [{"package_id": "b", "agent_type": "agent_builder"}]},
            None,
        ),
    }

    class LiveExtractor(M365RegistryExtractor):
        def _access_token(self) -> str:
            return "fake-token"

        def _http_get_json(
            self, url: str, token: str
        ) -> tuple[int, dict[str, Any], float | None]:
            return pages[url]

    settings = Settings(use_fixtures=False, raw_root=str(tmp_path))
    extractor = LiveExtractor(
        settings=settings, backend=LocalStorageBackend(str(tmp_path))
    )
    import os

    os.environ["GRAPH_REGISTRY_URL"] = "https://graph/p1"
    try:
        collected = [rec for page in extractor.paginate(since=None) for rec in page]
    finally:
        del os.environ["GRAPH_REGISTRY_URL"]
    assert [r["package_id"] for r in collected] == ["a", "b"]  # both pages, in order


def test_pagination_retries_on_429(tmp_path: Any) -> None:
    calls: list[str] = []
    responses = iter(
        [
            (429, {}, 0.0),  # throttled once
            (
                200,
                {"value": [{"package_id": "a", "agent_type": "copilot_studio"}]},
                None,
            ),
        ]
    )

    class LiveExtractor(M365RegistryExtractor):
        def _access_token(self) -> str:
            return "fake-token"

        def _http_get_json(
            self, url: str, token: str
        ) -> tuple[int, dict[str, Any], float | None]:
            calls.append(url)
            return next(responses)

    from extractors.core.ratelimit import RateLimiter

    limiter = RateLimiter(sleep=lambda _: None, clock=lambda: 0.0)
    settings = Settings(use_fixtures=False, raw_root=str(tmp_path))
    extractor = LiveExtractor(
        settings=settings,
        backend=LocalStorageBackend(str(tmp_path)),
        rate_limiter=limiter,
    )
    import os

    os.environ["GRAPH_REGISTRY_URL"] = "https://graph/p1"
    try:
        collected = [rec for page in extractor.paginate(since=None) for rec in page]
    finally:
        del os.environ["GRAPH_REGISTRY_URL"]
    assert len(calls) == 2  # one 429 + one success
    assert collected[0]["package_id"] == "a"


def test_403_raises_license_error(tmp_path: Any) -> None:
    class LiveExtractor(M365RegistryExtractor):
        def _access_token(self) -> str:
            return "fake-token"

        def _http_get_json(
            self, url: str, token: str
        ) -> tuple[int, dict[str, Any], float | None]:
            return 403, {}, None

    settings = Settings(use_fixtures=False, raw_root=str(tmp_path))
    extractor = LiveExtractor(
        settings=settings, backend=LocalStorageBackend(str(tmp_path))
    )
    with pytest.raises(AgentRegistryLicenseError):
        list(extractor.paginate(since=None))


# --- AC-1.4-4 / -5 / -6 via end-to-end fixtures run ---------------------- #
def test_end_to_end_snapshot(tmp_path: Any) -> None:
    extractor = _make(tmp_path)
    result = extractor.run(run_date=date(2026, 6, 18))

    # AC-1.4-4: snapshot written under raw/m365-registry/{date}/
    assert "m365-registry/dt=2026-06-17" in (result.written_path or "")
    # 4 distinct agents (HR dup removed), 1 quarantined (null id), 1 drift (tenantRegion)
    assert result.record_count == 4
    assert result.duplicate_count == 1
    assert result.invalid_count == 1
    assert result.drift_field_count == 1  # AC-1.4-5

    table = pq.read_table(result.written_path)
    # AC-1.4-6: package_id present and non-null in 100% of records.
    assert table.column("package_id").null_count == 0
    assert len(table.column("package_id").to_pylist()) == 4
    # AC-1.4-5: drift captured, not dropped.
    drift_values = [d for d in table.column("_drift").to_pylist() if d]
    assert any("tenantRegion" in d for d in drift_values)
    # typed columns survive (bool / list-as-json-string).
    assert table.schema.field("enabled").type == pa.bool_()
    assert table.schema.field("connectors").type == pa.string()


def test_quarantine_contains_null_id(tmp_path: Any) -> None:
    extractor = _make(tmp_path)
    extractor.run(run_date=date(2026, 6, 18))
    qfile = (
        tmp_path
        / "_quarantine"
        / "ext-m365-registry"
        / "dt=2026-06-17"
        / "quarantine.jsonl"
    )
    assert qfile.exists()  # AC-1.4-3 / AC-1.3-5: malformed isolated, batch survived


def test_schema_roundtrip_lists() -> None:
    rec = RawAgentPackage.model_validate(
        {
            "package_id": "pkg-z",
            "agent_type": "sharepoint",
            "connectors": ["A", "B"],
            "permissions": ["P"],
            "extraField": 1,
        }
    )
    assert rec.connectors == ["A", "B"]
    assert rec.drift_fields == {"extraField": 1}  # extra surfaced as drift
