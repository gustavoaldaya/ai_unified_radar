# ADR-009: Unified Monorepo in ai_unified_radar (AgentLens as subproject)

- **Status:** accepted
- **Date:** 2026-06-18
- **Deciders:** gustavoaldaya
- **Supersedes:** M1.1 standalone-repo decision

## Context
M1.1 set the B1 target to a dedicated repo `gustavoaldaya/agentlens`, with `ai_unified_radar` cited only as a sibling repo. On executing B1 the decision was to **unify**: all data collectors and architecture-layer changes live in a single repo. `ai_unified_radar` already existed as the home of 3-plane agentic-fleet governance + custom FinOps layer (ADR-K23), and its README anticipated an ingestion/consolidation `pipeline/` (Bronze/Silver/Gold) — which is exactly what AgentLens implements.

## Choice
**AgentLens lives as subproject `agentlens/` inside `gustavoaldaya/ai_unified_radar`** (monorepo). All future extractors/collectors and architecture changes land in this repo. CI at repo root (`.github/workflows/agentlens-ci.yml`) with `working-directory: agentlens` and `paths: agentlens/**` filter.

## Rationale
1. A single home for observability + FinOps of the agentic fleet; AgentLens is the implementation of the "custom FinOps layer / pipeline" that `ai_unified_radar` already declared.
2. Avoids repo sprawl and duplicated conventions (Conventional Commits / SemVer / MADR-light already in force in `ai_unified_radar`).
3. Unified governance and traceability: ADR-Kxx (governance) in root `docs/`; ADR-00xx (AgentLens) in `agentlens/docs/adr/`.

## Accepted trade-off
- Monorepo coordination: per-subproject tooling (CI with path filter, pre-commit inside `agentlens/`).
- Versioning with **namespaced tags** (`agentlens-vX.Y.Z`) to avoid clashing with `ai_unified_radar`'s repo-level SemVer.

## Consequences
- **Supersedes** the "standalone repo `agentlens`" target from M1.1.
- B1 is added additively under `agentlens/` without touching `ai_unified_radar`'s `README`/`.gitignore`/`docs/`/`spikes/`.
- Remote push completed on 2026-06-23: commit `cb824bf` (ingestion tier B1–B4+, 12 extractors, 46 tests) on `main`, tag `agentlens-v0.4.0-alpha`.

## Related
- [ADR-007 (Fabric over Databricks)](./adr-007-fabric-over-databricks.md)
- `ai_unified_radar` / ADR-K23 (governance, root `docs/`)

---
> Canonical source: AgentLens design notes in the Obsidian vault (`AI_Observability/`). This file is a Git-tracked mirror for traceability.
