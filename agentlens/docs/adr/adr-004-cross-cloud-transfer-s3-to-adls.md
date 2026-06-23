# ADR-004: Cross-Cloud Transfer S3 to ADLS

- **Status:** superseded (by [ADR-008](./adr-008-direct-api-pull-from-aws-to-adls.md))
- **Date:** 2026-06-17

> **SUPERSEDED (2026-06-17) by [ADR-008: Direct API Pull from AWS to ADLS](./adr-008-direct-api-pull-from-aws-to-adls.md).** AWS data no longer transits S3; extractors pull directly via API into ADLS. Kept for traceability.

**Choice (obsolete):** AWS extractors write to S3 first, then ADF Copy Activity syncs to ADLS Gen2.

**Rationale:** Avoids giving AWS Lambdas direct Azure write access. S3 is natural landing zone for AWS-native data. Sync is simple, auditable, retryable.

**Risk:** Adds ~30 minutes latency to Bedrock data. Acceptable given 4-hour SLA.

---
> Canonical source: AgentLens design notes in the Obsidian vault (`AI_Observability/`). This file is a Git-tracked mirror for traceability.
