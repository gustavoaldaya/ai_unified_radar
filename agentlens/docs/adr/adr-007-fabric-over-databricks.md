# ADR-007: Microsoft Fabric over Databricks for Transform + Serving Layer

- **Status:** accepted (downgrade to `proposed` if team input is wanted before committing capacity)
- **Date:** 2026-06-17
- **Deciders:** gustavoaldaya
- **Resolves:** Open Question OQ-1

## Context
AgentLens needs a transform engine (raw→curated→gold) and a serving layer for dashboards (Phase 4). All ingestion is Azure-native (ADLS Gen2, ADF, Foundry, Purview) and the final deliverable is Power BI dashboards. **Synapse Analytics discarded**: absorbed into Microsoft Fabric, not a viable option for a 2026 greenfield. The real decision is Fabric vs Databricks — distinct products that share a Spark engine but differ in governance, serving, and integration.

## Choice
**Microsoft Fabric** as the transform + serving layer in v1:
- **Lakehouse + OneLake shortcuts** to the `raw/` zone of ADLS Gen2 → zero data movement; the extractor framework keeps writing Parquet to ADLS unchanged.
- **PySpark Notebooks** for medallion transform (curated/gold), reusing the canonical Bronze/Silver/Gold pattern from the `fabric-platform` work.
- **Semantic model + Power BI** native for the 4 dashboards, with **TMDL/PBIR serialization in Git**.

## Rationale
1. **Azure-native coherence.** Foundry, ADF, and ADLS Gen2 are already on Azure; OneLake shortcuts directly to ADLS raw with no copy ETL.
2. **Native Purview.** ADR-005 fixes Purview as the compliance source of truth; Fabric has native lineage + sensitivity labels + Purview integration, whereas Databricks needs a bridge.
3. **Direct serving to Power BI.** The Phase 4 deliverable is dashboards; Fabric semantic model → Power BI is direct, with no intermediate layer.
4. **Own prior art.** The medallion + TMDL/PBIR Git + ADF SCD pattern is already proven in `fabric-platform`. Not starting from scratch.
5. **Predictable cost.** Capacity-based (F4+ ~$500/mo) vs variable DBU; pausable F-SKU fits batch workloads (ADR-001).

## Accepted trade-off
Databricks has an edge in **Unity Catalog for cross-cloud governance** and large-scale Spark. In v1 the weight is Azure/M365; the cross-cloud part (Bedrock) is cost/usage ingestion, not governance (AWS governance parity is v2 — ADR-005). As long as governance lives in Purview, Fabric suffices.

## Re-evaluation triggers
- **v2 AWS governance parity** demands unified cross-cloud governance → reconsider Unity Catalog.
- Sustained Spark volume exceeds the economical Fabric capacity tier.
- Need for heavy ML / feature store over the observability data.

## Consequences
- Day 1 adds Fabric workspace + Lakehouse + OneLake shortcut to ADLS raw.
- `dim_agent` SCD2 transform, facts, and the optimization engine are implemented in Fabric Notebooks (not Databricks).
- ADF orchestrates extraction; Fabric pipeline/notebook orchestrates transform (08:00) + optimization (09:00).

## Related
- [ADR-002 (Parquet on ADLS Gen2)](./adr-002-parquet-on-adls-gen2.md) — OneLake shortcut consumes the Parquet unchanged
- [ADR-005 (Purview as Compliance Source of Truth)](./adr-005-purview-as-compliance-source-of-truth.md) — reinforces Fabric (native Purview)
- [ADR-001 (Batch over Streaming)](./adr-001-batch-over-streaming.md) — pausable capacity fits batch

---
> Canonical source: AgentLens design notes in the Obsidian vault (`AI_Observability/`). This file is a Git-tracked mirror for traceability.
