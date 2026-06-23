# CHANGELOG fragment — merge into agentlens/CHANGELOG.md

## [0.4.0-alpha] — 2026-06-18

### Added
- ext-m365-usage (M1.5): 3-day moving window, `package_id+report_date` dedup,
  anonymization flag, `reconcile_orphans` (AC-1.5-2/-4/-5/-6).
- Nine more extractors completing the 12-source v1 inventory:
  ext-m365-user-activity, ext-purview-audit, ext-purview-dspm, ext-foundry-traces,
  ext-foundry-metrics, ext-foundry-cost, ext-bedrock-invocations,
  ext-bedrock-metrics, ext-bedrock-traces, ext-bedrock-cost.
- Access mixins: `AzureJsonSource` (AAD token + GET/POST JSON seams),
  `AwsClientSource` (lazy boto3 client seam).
- `extractors/catalog.py` (name->class registry), `extractors/run.py` (CLI runner,
  fixtures/live), `build_backend()` (Local vs ADLS selection).
- `adf/AgentLens_Daily.pipeline.json` orchestration manifest.
- Fixtures + tests for all extractors: parametrized end-to-end across the catalog
  + dedicated AC-1.5 suite. Full suite: 46 tests green; mypy --strict 47 files.

### Notes
- Suggested tag: `agentlens-v0.4.0-alpha`.
- Live `paginate()` paths are credential-gated seams (`# pragma: no cover`) to be
  exercised in the environment-config + testing phase. See README-EXTRACTORS.md.
- Deferred: physical hour sub-partitioning for hourly sources (timestamp kept as a
  column); Graph Reports CSV fallback; preview endpoint verification.
