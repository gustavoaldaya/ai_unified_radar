"""End-to-end fixtures run for every extractor in the catalog.

Each extractor reads its ``tests/fixtures/{name}/`` snapshot, validates, dedups,
and writes a typed Parquet under its raw ``source_path`` partition -- no network.
"""

from __future__ import annotations

import os
from datetime import date

import pyarrow.parquet as pq
import pytest

from extractors.catalog import CATALOG
from extractors.core.config import Settings
from extractors.core.storage import LocalStorageBackend

FIXTURES_ROOT = os.path.join(os.path.dirname(__file__), "fixtures")
RUN_DATE = date(2026, 6, 18)  # Thursday -> LBD 2026-06-17


@pytest.mark.parametrize("name", sorted(CATALOG))
def test_extractor_runs_against_fixtures(name: str, tmp_path) -> None:
    settings = Settings(
        use_fixtures=True, raw_root=str(tmp_path), fixtures_root=FIXTURES_ROOT
    )
    extractor = CATALOG[name](
        settings=settings, backend=LocalStorageBackend(str(tmp_path))
    )

    result = extractor.run(run_date=RUN_DATE)

    assert result.target_date == date(2026, 6, 17)
    assert result.record_count >= 1
    assert result.written_path is not None
    assert extractor.source_path in result.written_path
    assert "dt=2026-06-17" in result.written_path

    table = pq.read_table(result.written_path)
    assert "_drift" in table.schema.names
    # the required key field of each schema is never null in the output
    key = extractor.source_id_field
    assert table.column(key).null_count == 0
    # watermark persisted only after the successful write
    assert extractor.current_watermark().cursor == "2026-06-17"
