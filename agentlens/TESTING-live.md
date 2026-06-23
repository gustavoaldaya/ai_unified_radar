# AgentLens — Live testing runbook

Run **after** `.env` is filled and `USE_FIXTURES=false`. Order matters: do the
one-time prerequisites, then a connectivity smoke per cloud, then each extractor,
then the full pipeline + idempotency. Every extractor's `paginate()` is a seam
that has only been exercised against fixtures so far — this is where they meet
the real APIs.

Legend: ☐ to do · expected = what success looks like · ⚠ = known gotcha.

---

## 0. One-time prerequisites

- ☐ `.env` filled from `.env.example`; `USE_FIXTURES=false`.
- ☐ Entra SP created + admin-consented; roles assigned per `Credentials and Environment`:
  - ☐ Graph: `AppCatalog.Read.All`, `Reports.Read.All`, `SecurityEvents.Read.All`
  - ☐ O365 Mgmt: `ActivityFeed.Read`
  - ☐ Azure RBAC: `Monitoring Reader`, `Cost Management Reader`, `Log Analytics Reader`, `Storage Blob Data Contributor` (on the ADLS account)
- ☐ Unified audit log **enabled** in Purview (can take ~60 min to take effect).
- ☐ ADLS: container `raw` exists; SP can write.
- ☐ AWS: credentials valid; **Bedrock model invocation logging enabled** to CloudWatch per account+region; AgentCore tracing on.
- ☐ `uv sync` (ensure `azure-identity`, `azure-storage-file-datalake`, `boto3`, `opentelemetry-sdk` resolved).

## 1. Connectivity smoke (no extraction yet)

- ☐ Azure token: `az account get-access-token --resource https://graph.microsoft.com` (or SP via `DefaultAzureCredential`).
- ☐ ADLS write probe: any small blob to `raw/_probe/ok.txt` succeeds, then delete.
- ☐ Log Analytics + ARM tokens acquire (scopes in `extractors/core/azure_http.py`).
- ☐ O365 Mgmt: **start the subscription once** —
  `PurviewAuditExtractor(settings).ensure_subscription()` → expect HTTP 200 (or 400 "already enabled"). ⚠ first content blobs can take **up to 12h**.
- ☐ AWS: `aws sts get-caller-identity` returns the expected account.

## 2. Per-extractor live runs

General pattern:
```bash
USE_FIXTURES=false python -m extractors.run <name>
```
Expected (all): `[ok] <name>: N records ... -> raw/<source_path>/dt=<date>/part-0.parquet`,
the watermark file `raw/_watermarks/<name>.json` advances, and the required key
column is 100% non-null. Verify each Parquet opens and row counts look sane.

### ext-m365-registry
- ☐ Run → agents catalog snapshot in `raw/m365-registry/dt=.../`.
- ⚠ 403 `AgentRegistryLicenseError` = tenant lacks Agent 365 (MC1173195). Set `GRAPH_REGISTRY_URL` to the `agentRegistry/agentInstances` branch (ADR-K23) and re-run.

### ext-m365-usage
- ☐ Run → `raw/m365-usage/dt=.../`; confirm 3-day window rows present.
- ☐ Reconcile: `M365UsageExtractor.reconcile_orphans(usage_ids, registry_ids)` → orphans logged, not dropped.
- ⚠ Data latency ~T+72h (today's agents may show zero). ⚠ report may return **CSV**; confirm `$format=application/json` works, else add a CSV parse step.

### ext-m365-user-activity
- ☐ Run → `raw/m365-user-activity/dt=.../`.
- ⚠ Monthly aggregates / rolling 12 months; same CSV caveat.

### ext-purview-audit
- ☐ Prereq: subscription started (step 1) and blobs available.
- ☐ Run → `raw/m365/purview/audit_log/dt=.../`; spot-check `app_identity` populated (join key to registry `package_id`).
- ⚠ Pagination is the **`NextPageUri` response header** (handled). ⚠ window ≤24h, start ≤7 days back. ⚠ no content until ~12h after first `/start`.

### ext-purview-dspm
- ☐ Run → `raw/m365/purview/dspm_posture/dt=.../`.
- ⚠ Preview Graph surface; if the endpoint 404s, set `GRAPH_DSPM_URL` to the current path. Requires E7/Agent 365.

### ext-foundry-traces
- ☐ Prereq: `LOG_ANALYTICS_WORKSPACE_ID`; tracing enabled on the Foundry project.
- ☐ Run → `raw/foundry/traces/dt=.../`; confirm `gen_ai_agent_id` present.
- ⚠ KQL `AppTraces` table/columns may differ per workspace; tune the query.

### ext-foundry-metrics
- ☐ Prereq: `FOUNDRY_ACCOUNT_RESOURCE_ID`.
- ☐ Run → `raw/foundry/metrics/dt=.../`.
- ⚠ Azure Monitor metrics call may need `metricnames`/`timespan`/`interval` params for real data; confirm the dimension split.

### ext-foundry-cost
- ☐ Prereq: `AZURE_COST_SCOPE`.
- ☐ Run → `raw/foundry/cost/dt=.../` (FOCUS columns).
- ⚠ **Hard ~15 reads/h** — extractor raises `HardRateLimitExceeded` instead of retrying. Prefer scheduled FOCUS export to ADLS in prod.

### ext-bedrock-invocations
- ☐ Prereq: invocation logging on; `BEDROCK_INVOCATION_LOG_GROUP` correct.
- ☐ Run → `raw/bedrock/invocation_logs/dt=.../`; confirm `request_id` non-null.
- ⚠ Log group must exist per region; empty if logging was just enabled.

### ext-bedrock-metrics
- ☐ Run → `raw/bedrock/metrics/dt=.../`.
- ⚠ `get_metric_data` needs a populated `MetricDataQueries` set — wire the metric list (Invocations, InvocationLatency, token counts, AgentCore SessionCount) before the real run.

### ext-bedrock-traces
- ☐ Prereq: AgentCore/ADOT instrumentation; `BEDROCK_AGENTCORE_LOG_GROUP`.
- ☐ Run → `raw/bedrock/traces/dt=.../`.

### ext-bedrock-cost
- ☐ Run → `raw/bedrock/cost/dt=.../`; filter `SERVICE = Amazon Bedrock`.
- ⚠ Cost Explorer latency T+24h; model-level only (no per-resource line items — ADR-008 trade-off).

## 3. Full pipeline

- ☐ `USE_FIXTURES=false python -m extractors.run --all` → all 12 land Parquet in ADLS; failures are reported per-extractor (the runner continues).
- ☐ Cross-source coherence spot-check: a known `package_id` from registry appears in usage and (via `app_identity`) in audit.

## 4. Idempotency + watermark

- ☐ Re-run any extractor the same day → `duplicate_count` reflects dedup (no duplicate `(source_id, timestamp)` rows in Parquet); watermark `cursor` advances only on a successful write.
- ☐ Simulate a write failure (bad ADLS perms) → run raises, watermark unchanged (no half-committed state).

## 5. Quality gates

- ☐ Required key column 100% non-null in every output (e.g. `package_id`, `record_id`, `span_id`).
- ☐ Review `_quarantine/<name>/dt=.../quarantine.jsonl` for malformed records — counts plausible, not silently swallowing good data.
- ☐ `_drift` column: inspect a few values to catch new upstream fields early (preview APIs).

## 6. Known issues / rollback

- Any extractor failing → re-run just that one; the daily pipeline is per-extractor isolated.
- Hourly sources currently land a single daily `dt=` partition (hour kept as a column) — physical hour sub-partitioning is deferred to the Fabric transform.
- To fall back to a safe state at any point: set `USE_FIXTURES=true` (no creds, local writes) and the whole suite still runs green.
