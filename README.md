# ai_unified_radar

Gobierno unificado de flota agéntica Microsoft en **3 planos** + capa **FinOps de consolidación custom**. Operacionaliza **ADR-K23** (baseline 2026-06-11).

| Plano | Producto | Dueño |
|---|---|---|
| Organizacional | Agent 365 (registry, Entra Agent ID, Purview, Defender) | IT / compliance / seguridad |
| Técnico | Foundry Control Plane + APIM | Platform engineering |
| **FinOps** | **No existe como producto Microsoft** → capa custom (este repo) | FinOps |

## Estructura

- `docs/` — snapshots versionados de ADRs y specs. **La fuente canónica es el vault Obsidian (`KnowledgeBase/`)**; aquí viajan baselines para trazabilidad git.
- `spikes/` — código de validación. Los outputs (`spikes/output/`) contienen datos de tenant y están **git-ignorados**.
- `agentlens/` — subproyecto **AgentLens**: observabilidad + FinOps de agentes (M365/Agents 365, Azure AI Foundry, Amazon Bedrock). Implementa la capa de ingesta de la flota (12 extractores sobre `BaseExtractor`, contra fixtures; cutover live gated en Service Principal). Decisiones propias en `agentlens/docs/adr/` (serie ADR-00x; ver [ADR-009](agentlens/docs/adr/adr-009-unified-monorepo.md)). Versionado con tags namespaced `agentlens-vX.Y.Z`.
- `pipeline/` *(próximo)* — ingesta y consolidación Bronze/Silver/Gold de consumos (Anexo B del ADR): push-first, ventana de re-statement D-1..D-5, hechos en unidad nativa + valoración efectivo-fechada.

## Estado (2026-06-11)

- **ADR-K23**: `PROPOSED`, baseline fijada. Bloqueante único para `ACCEPTED`: **eje 3** — spike de campos reales de la Agent Registry Graph API (`spikes/spike_agent_registry_fields.ps1`).
- Roadmap: spike → Decision 2 definitiva (re-plataforma total vs adapter-based) → ACCEPTED → pipeline de consolidación → vehículo MVP (se decide tras el piloto del Microsoft OTel Distro en `finops-ai-agent`).
- En cola tras ACCEPTED: OBS-01 → v0.5.0 · amendment ADR-K16 (deadline licenciamiento seguridad de agentes: **2026-07-01**).

## Convenciones

Conventional Commits 1.0 · SemVer 2.0 · ADRs en MADR-light · ruta local: `C:\Claude_environment\projects\ai_unified_radar`
