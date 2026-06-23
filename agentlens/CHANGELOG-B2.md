# CHANGELOG fragment — merge into agentlens/CHANGELOG.md

## [0.2.0-alpha] — 2026-06-18

### Added
- `BaseExtractor` abstract pipeline (M1.3): auth → paginate → rate_limit →
  track_watermark → validate → dedup → write_parquet, with a fixtures-aware
  `run()` (`USE_FIXTURES=true`, last-business-day harness, no network).
- Core building blocks under `extractors/core/`: `Settings`, `last_business_day`,
  sha256 dedup, `RateLimiter` (backoff + `Retry-After` + hard-limit guard),
  `StorageBackend` (`LocalStorageBackend` + lazy `ADLSStorageBackend` via MI),
  atomic `WatermarkStore`, `Quarantine` sink, OTel `gen_ai.*` + `agent.id` span.
- `RawRecord` base schema (`frozen`, `extra="allow"`) with schema-drift surfacing.
- Reference `sample_agents` fixtures + 22 tests covering AC-1.3-1 … AC-1.3-8.

### Notes
- Suggested tag: `agentlens-v0.2.0-alpha` (SemVer minor: new feature, pre-1.0).
