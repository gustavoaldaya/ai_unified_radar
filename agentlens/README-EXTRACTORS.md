# AgentLens — Extractor tier (complete)

All 12 v1 extractors built on the `BaseExtractor` framework (B2). Everything runs
today against fixtures (`USE_FIXTURES=true`, zero credentials). This doc is the
bridge to the **environment-config + live-testing** phase: it lists the live
seams and the exact env vars each extractor needs at cutover.

## Inventory

| Extractor | Source API | source_path | dedup key (source_id · ts) |
|-----------|-----------|-------------|----------------------------|
| ext-m365-registry | Graph catalog/packages | `m365-registry` | package_id · last_modified |
| ext-m365-usage | Graph Reports getCopilotAgentUsage | `m365-usage` | package_id · report_date |
| ext-m365-user-activity | Graph Reports general usage | `m365-user-activity` | user_id · report_date |
| ext-purview-audit | O365 Management Activity API | `m365/purview/audit_log` | record_id · creation_date |
| ext-purview-dspm | Graph Security / Purview | `m365/purview/dspm_posture` | dspm_agent_instance_id · assessment_date |
| ext-foundry-traces | App Insights / Log Analytics (KQL) | `foundry/traces` | span_id · timestamp |
| ext-foundry-metrics | Azure Monitor metrics | `foundry/metrics` | (metric+deployment) · timestamp |
| ext-foundry-cost | Cost Management (FOCUS) | `foundry/cost` | (resource+meter) · charge_period_start |
| ext-bedrock-invocations | CloudWatch Logs (ModelInvocationLog) | `bedrock/invocation_logs` | request_id · timestamp |
| ext-bedrock-metrics | CloudWatch GetMetricData | `bedrock/metrics` | (metric+model) · timestamp |
| ext-bedrock-traces | AgentCore OTEL via CloudWatch | `bedrock/traces` | span_id · timestamp |
| ext-bedrock-cost | Cost Explorer GetCostAndUsage | `bedrock/cost` | (usage_type+operation) · time_period_start |

Composite-key extractors override `_dedup_key`. `ext-foundry-cost` defaults to a
hard-limit rate limiter (~15 reads/h, raises instead of retrying).

## Run

```bash
# everything, against fixtures
USE_FIXTURES=true python -m extractors.run --all
# a subset
USE_FIXTURES=true python -m extractors.run ext-foundry-traces ext-bedrock-cost
```

`extractors/run.py` selects the backend via `build_backend(settings)`: Local
filesystem under fixtures, ADLS Gen2 (Managed Identity) when live + configured.

## Live cutover — env vars to set (`USE_FIXTURES=false`)

Shared Azure SP (DefaultAzureCredential): `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`,
`AZURE_CLIENT_SECRET`, `AZURE_SUBSCRIPTION_ID`. ADLS sink: `ADLS_ACCOUNT_URL`,
`ADLS_FILESYSTEM`.

| Extractor | Required live env | Optional override |
|-----------|-------------------|-------------------|
| registry | (SP) | `GRAPH_REGISTRY_URL` |
| usage | (SP) | `GRAPH_USAGE_URL` |
| user-activity | (SP) | `GRAPH_USER_ACTIVITY_URL` |
| purview-audit | `O365_MGMT_PUBLISHER_ID` | — |
| purview-dspm | (SP) | `GRAPH_DSPM_URL` |
| foundry-traces | `LOG_ANALYTICS_WORKSPACE_ID` | — |
| foundry-metrics | `FOUNDRY_ACCOUNT_RESOURCE_ID` | — |
| foundry-cost | `AZURE_COST_SCOPE` | — |
| bedrock-* | `AWS_REGION` + AWS creds | `BEDROCK_INVOCATION_LOG_GROUP`, `BEDROCK_AGENTCORE_LOG_GROUP` |

SP permissions matrix: see `architecture/Credentials and Environment` in the vault.

## Live seams (where to focus testing)

* Azure/Graph: `AzureJsonSource._aad_token / _get_json / _post_json`
  (`extractors/core/azure_http.py`).
* AWS: `AwsClientSource._aws_client` (`extractors/core/aws_client.py`).
* Each extractor's `paginate()` is `# pragma: no cover` (network) — these are the
  methods to exercise once credentials land.

## Known deferrals (revisit during testing / transform)

1. **Hour sub-partitioning** for hourly sources (traces/metrics/audit) is not
   physical yet — records carry the timestamp as a column and land in a single
   daily `dt=` partition. `Storage Architecture` calls for a secondary `hour`
   partition; fold it into the Fabric transform or extend `_partition_path`.
2. **Graph Reports CSV**: report endpoints can return CSV; the live path requests
   `$format=application/json`. Confirm/parse CSV fallback during testing.
3. **Purview/DSPM Graph surface** is still expanding (preview); the endpoint is
   overridable and the schema is drift-tolerant.
4. **Endpoints marked TODO-verify**: exact preview URLs (DSPM, Copilot usage) may
   shift — all are env-overridable and validated against fixtures only so far.

## Orchestration

`adf/AgentLens_Daily.pipeline.json` — 4 parallel extractor groups → Fabric
transform (08:00 UTC) → optimization engine (09:00 UTC). Hourly extractors run on
their own triggers.
