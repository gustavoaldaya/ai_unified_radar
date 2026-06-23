# AgentLens — Architecture Decision Records

Decision log for the **AgentLens** subproject (unified observability + FinOps for AI agents across M365/Agents 365, Azure AI Foundry, and Amazon Bedrock).

Format: MADR-light · Conventional Commits · SemVer (namespaced tags `agentlens-vX.Y.Z`). Governance-level ADRs (the **ADR-Kxx** series) live in the repo-root `docs/`; AgentLens ADRs (**ADR-00x**) live here per [ADR-009](./adr-009-unified-monorepo.md).

> These files are a Git-tracked mirror. The canonical source is the Obsidian vault (`AI_Observability/decisions/`).

| ADR | Title | Status |
|-----|-------|--------|
| [001](./adr-001-batch-over-streaming.md) | Batch over Streaming | accepted |
| [002](./adr-002-parquet-on-adls-gen2.md) | Parquet on ADLS Gen2 | accepted |
| [003](./adr-003-agent-as-minimum-control-unit.md) | Agent as Minimum Control Unit | accepted |
| [004](./adr-004-cross-cloud-transfer-s3-to-adls.md) | Cross-Cloud Transfer S3 to ADLS | superseded by 008 |
| [005](./adr-005-purview-as-compliance-source-of-truth.md) | Purview as Compliance Source of Truth | accepted |
| [006](./adr-006-optimizations-ship-with-data.md) | Optimizations Ship with Data | accepted |
| [007](./adr-007-fabric-over-databricks.md) | Microsoft Fabric over Databricks (transform + serving) | accepted |
| [008](./adr-008-direct-api-pull-from-aws-to-adls.md) | Direct API Pull from AWS to ADLS (no S3) | accepted |
| [009](./adr-009-unified-monorepo.md) | Unified Monorepo in ai_unified_radar | accepted |
