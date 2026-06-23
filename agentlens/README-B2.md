# AgentLens — B2 / M1.3: Extractor base framework

Additive overlay onto the B1 scaffold (`agentlens/` subproject inside
`ai_unified_radar`). Drop these files over the existing tree — same layout
(`extractors/ schemas/ tests/`). No real credentials needed: everything runs
against fixtures (`USE_FIXTURES=true`).

## What's included

```
extractors/base.py                 BaseExtractor (ABC) — orchestrates the pipeline
extractors/core/config.py          Settings (USE_FIXTURES, business week, ...)
extractors/core/calendar.py        last_business_day (LBD)
extractors/core/dedup.py           sha256 dedup key + order-preserving dedup
extractors/core/ratelimit.py       backoff + Retry-After + hard-limit guard
extractors/core/storage.py         StorageBackend: Local (fixtures) + ADLS (live, MI)
extractors/core/watermark.py       Watermark + atomic WatermarkStore
extractors/core/quarantine.py      invalid-record sink (_quarantine/)
extractors/core/telemetry.py       OTel gen_ai.* + agent.id span
schemas/base.py                    RawRecord (frozen, extra="allow" drift)
tests/                             22 tests, all AC-1.3-* covered
tests/fixtures/sample_agents/      reference fixtures (dup + invalid + drift baked in)
```

> `extractors/__init__.py` and `schemas/__init__.py` already exist from B1 (empty
> markers). `extractors/core/__init__.py` is new and included. There is **no**
> `tests/__init__.py` (tests run in rootdir mode so `conftest` is importable).

## Pipeline (materialises `architecture/Extractor Architecture`)

`auth → paginate → rate_limit → track_watermark → validate → dedup → write_parquet`

A concrete extractor only overrides `paginate()` (live API pull) and sets six
class attrs: `name`, `schema`, `source_path`, `source_id_field`,
`timestamp_field`, `agent_id_field`. Everything else is inherited. Under
`USE_FIXTURES` the live `paginate()` is never called — the harness reads
`tests/fixtures/{name}/*.json` and seeds the watermark to the LBD.

## Acceptance criteria → test

| AC | What | Where |
|----|------|-------|
| 1.3-1 | every step is a composable method + unit test | `test_core_steps.py` |
| 1.3-2 | watermark read at start, persisted only after a successful (atomic) write | `test_base_extractor_fixtures.py::test_watermark_*` |
| 1.3-3 | dedup → 0 duplicates in Parquet | `test_parquet_typed_and_deduplicated` |
| 1.3-4 | 429 honours `Retry-After`, no retry in window; hard-limit raises | `test_core_steps.py::test_429_*`, `test_hard_limit_*` |
| 1.3-5 | invalid record → `_quarantine/` without aborting the batch | `test_quarantine_file_written` |
| 1.3-6 | typed schema, partitioned by date | `test_parquet_typed_and_deduplicated` |
| 1.3-7 | OTel span with `agent.id` + `gen_ai.*` | `test_otel_span_has_gen_ai_and_agent_id` |
| 1.3-8 | fixtures end-to-end, LBD correct (Mon→prev Fri), no network | `test_fixtures_end_to_end` + calendar tests |

## Run it

```bash
uv sync                       # ensure dev deps below are present
USE_FIXTURES=true uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run mypy --strict extractors schemas tests
```

## Deps / config to add to `pyproject.toml`

Runtime deps are already in B1 (pydantic, pyarrow, azure-*, opentelemetry-api).
Add as **dev** deps if missing: `pytest`, `opentelemetry-sdk` (the in-memory span
exporter used by the telemetry test).

`mypy --strict` config (B1 already strict): add these overrides so pyarrow's
missing stubs and test ergonomics don't break CI —

```toml
[[tool.mypy.overrides]]
module = ["pyarrow.*", "azure.*", "opentelemetry.*"]
ignore_missing_imports = true

[[tool.mypy.overrides]]
module = ["tests.*", "conftest", "test_core_steps", "test_base_extractor_fixtures"]
disallow_untyped_defs = false
disallow_untyped_calls = false
```

Pytest path (if B1 didn't already set it):

```toml
[tool.pytest.ini_options]
pythonpath = ["."]
```

## Notes

* Raw partition style is Hive `dt=YYYY-MM-DD/` (matches `Storage Architecture`).
* Schema drift (unmapped fields under `extra="allow"`) is preserved in a `_drift`
  JSON string column and counted per run — feeds the drift signal for B3 (M1.4).
* `ADLSStorageBackend` imports azure libs lazily; it is **not** exercised under
  fixtures and is the live-cutover seam (set `USE_FIXTURES=false` + provide the SP).
* Next: **B3 = ext-m365-registry (M1.4)** — first concrete subclass of
  `BaseExtractor`; then B4–B6 reuse the same base.
```
