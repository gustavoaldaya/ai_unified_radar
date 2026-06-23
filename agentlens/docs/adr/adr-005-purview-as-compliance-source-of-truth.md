# ADR-005: Purview as Compliance Source of Truth

- **Status:** accepted
- **Date:** 2026-06-17

**Choice:** Microsoft Purview (Unified Audit Log + DSPM AI Observability) is the authoritative compliance data source in v1. AppIdentity in Purview audit events validates against Agent Registry packageId. AWS governance parity deferred to v2.

**Rationale:** Purview GA for agent observability since May 2026. Treats agents as first-class entities with DLP enforcement, sensitivity labels, insider risk scoring. Office 365 Management Activity API provides programmatic access. AWS lacks single equivalent — requires composing 5+ services.

**Risk:** v1 governance asymmetry: M365/Foundry agents get full coverage, Bedrock agents get usage/cost/traces but no compliance. Explicit in dashboards. v2 closes it.

**Critical Rule:** Daily transform cross-references every M365 `agent_id` against Purview audit data. No AppIdentity match after 7 days → flagged as `unobserved_agent` warning.

---
> Canonical source: AgentLens design notes in the Obsidian vault (`AI_Observability/`). This file is a Git-tracked mirror for traceability.
