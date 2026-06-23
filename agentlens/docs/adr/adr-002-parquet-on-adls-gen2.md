# ADR-002: Parquet on ADLS Gen2

- **Status:** accepted
- **Date:** 2026-06-17

**Choice:** Parquet files on ADLS Gen2 as the primary storage, queryable by any engine.

**Rationale:** Maximum flexibility — team hasn't committed to Synapse, Databricks, or Fabric. Parquet on ADLS is the common denominator. Avoids vendor lock-in. Storage costs minimal (~$0.02/GB/month Hot tier).

**Risk:** Ad-hoc query performance depends on partition pruning and file sizes. Compaction required. Can layer Synapse Serverless or Delta Lake on top if needed.

**Related:** [ADR-007 (Fabric over Databricks)](./adr-007-fabric-over-databricks.md) — OneLake shortcut consumes this Parquet without changes.

---
> Canonical source: AgentLens design notes in the Obsidian vault (`AI_Observability/`). This file is a Git-tracked mirror for traceability.
