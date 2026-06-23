# ADR-008: Direct API Pull from AWS to ADLS (no S3 intermediate)

- **Status:** accepted
- **Date:** 2026-06-17
- **Supersedes:** [ADR-004](./adr-004-cross-cloud-transfer-s3-to-adls.md)

## Context
ADR-004 routed AWS data through an intermediate S3 bucket + ADF Copy Activity to ADLS. Decision reversed: **all data retrieved from AWS lands directly in Azure Storage (ADLS Gen2)**. No self-managed S3 buckets are kept.

## Choice
Bedrock extractors run **Azure-side** (Azure Functions / Fabric Notebooks), **pull via AWS API**, and write Parquet to `raw/` in ADLS with the same `BaseExtractor` as the rest. No S3, no self-managed CUR, no cross-cloud ADF Copy Activity.

| Extractor | New source (API-pull) | Before (ADR-004) |
|---|---|---|
| ext-bedrock-invocations | **CloudWatch Logs** (Model Invocation Logging → CloudWatch) | Invocation Logs in S3 |
| ext-bedrock-metrics | CloudWatch `GetMetricData` (AWS/Bedrock) | (already API) |
| ext-bedrock-traces | CloudWatch / AgentCore OTEL | (already API) |
| ext-bedrock-cost | **Cost Explorer API** `GetCostAndUsage` | CUR 2.0 in S3 |

## Rationale
1. ADR-004's concern (don't give Lambdas write-access to Azure) **disappears**: nothing in AWS writes to Azure; the Azure-side extractor pulls.
2. A single landing zone (ADLS) → unified storage model, no dual custody or sync.
3. Fewer credentials and surface: `BEDROCK_LOG_S3_BUCKET` and `AWS_CUR_S3_BUCKET` removed.
4. Consistent with [ADR-002 (Parquet on ADLS Gen2)](./adr-002-parquet-on-adls-gen2.md) and the single extractor pattern.

## Accepted trade-off
Cost Explorer API is coarser than CUR 2.0: no resource-level line items, T+24h latency, daily granularity grouped by SERVICE/USAGE_TYPE/operation. Sufficient for FinOps attribution at the **Bedrock model/service** level. If v2 requires **per-resource/hour** cost, reconsider a targeted CUR→S3 (re-evaluation trigger).

## Required IAM (AWS account)
`logs:FilterLogEvents` / `logs:GetLogEvents` (invocations), `cloudwatch:GetMetricData` (metrics + AgentCore), `ce:GetCostAndUsage` (cost). Write to ADLS via Azure Managed Identity / SP.

## Consequences
- `.env`: the two S3 buckets removed; AWS only needs keys + region + IAM permissions.
- Latency: ADR-004's ~30 min sync removed; each API's native latency remains (Cost Explorer T+24h).
- Plan block **B6** no longer has "S3→ADLS sync".

## Related
- [ADR-004 (Cross-Cloud Transfer S3 to ADLS)](./adr-004-cross-cloud-transfer-s3-to-adls.md) (superseded)
- [ADR-002 (Parquet on ADLS Gen2)](./adr-002-parquet-on-adls-gen2.md)

---
> Canonical source: AgentLens design notes in the Obsidian vault (`AI_Observability/`). This file is a Git-tracked mirror for traceability.
