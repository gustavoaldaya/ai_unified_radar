# CHANGELOG — Agent Attribution (ADR-010)

Cierre de versión: **2026-07-09**. Implementa ADR-010 "Attribution as Tiered
Resolution" — resolución de identidad agente↔consumo con nivel de confianza
explícito, sobre grano span preservado, sirviendo observabilidad, FinOps y
seguridad desde un único pipeline.

Commiteado a `main` el 2026-07-09 vía la MCP de GitHub (PAT regenerado con
`Contents: write`). El resto del working tree (backlog previo al 2026-07-02) se
versiona aparte desde local.

## Resultado validado (contra `claude_db.agentlens`)

- **25 agentes** con consumo atribuido, **13,26 M tokens**.
- `unattributed` cae de 11.115 a **381 spans** (−96,6%): señal de gobierno real
  (test / genéricos / guid-only / ruido no-agente), no ruido silenciado.
- Reparto de tiers: `bridge` 11.546 · `correlation` 4 · `unattributed` 381.
- Mayor consumidor `finops-ai-agent` (7,63 M tokens), colisión resuelta al
  registro vivo 1615 (no al duplicado hueco 1578).

## Cambios

### Extractor — `extractors/foundry_traces.py`
- Filtro KQL ampliado: además de `gen_ai.system`, admite spans con
  `gen_ai.agent.id` u operación `invoke_agent`/`create_agent` (antes se
  descartaban los `invoke_agent` portadores del id → causa raíz del cero de
  atribución).
- Proyección de `ParentId` (parent_span_id) y `caller_id`
  (`UserAuthenticatedId`/`UserId` vía `column_ifexists`).

### Schema del raw — `schemas/foundry_traces.py`
- Campos nuevos `parent_span_id` y `caller_id`.

### Loader — `star/build_star_pg.py`
- Cacheo de `bridge_agent_identity` al arrancar; `_resolve_agent` aplica tiers
  (bridge → native → correlación por `trace_id` → centinela).
- Aterrizaje a grano span en `fact_agent_traces`; `resolution_tier` propagado a
  `fact_agent_consumption`. `attributed` se computa del tier.
- `fact_agent_traces` añadido a `_ALL_TABLES` (para `--rebuild`);
  `bridge_agent_identity` deliberadamente EXCLUIDO (dato curado).

### Schema Postgres — `star/agentlens_schema_pg.sql`
- Tablas `fact_agent_traces` (grano trace×span) y `bridge_agent_identity`.
- Columnas `resolution_tier` (fact_agent_consumption) y `cost_basis`
  (fact_resource_cost), vía `ALTER … ADD COLUMN IF NOT EXISTS`.
- Vistas `v_finops_agent_cost` (reparto de coste por cuota de tokens, marcado
  allocated; chargeback solo native+bridge) y
  `v_security_unregistered_consumption` (consumo sin atribuir como señal).

### Datos curados en la base
- `bridge_agent_identity`: 25 mapeos `foundry_otel` (5 `curated`, 20 `name_match`).

## Hallazgo de diseño registrado
- **Caller es null-by-nature en Foundry-OTel**: la doc de Microsoft no define
  atributo de usuario en los spans `gen_ai.*`; validado 0/11.931 en captura. La
  dimensión usuario/caller es del plano Purview (Unified Audit Log, ADR-005), no
  de las trazas. `caller_id` queda como captura oportunista.

## Commits listos (tras regenerar PAT con Contents: write)
```
feat(agentlens/attribution): tiered agent resolution over span-grain facts (ADR-010)
feat(agentlens/schema): fact_agent_traces + bridge_agent_identity + tier/cost_basis cols + finops/security views
fix(agentlens/foundry-traces): capture invoke_agent spans (widen KQL filter) + project parent_span_id/caller_id
docs(agentlens): ADR-010 accepted; CHANGELOG-attribution
```

## Pendiente (ver handoff)
- `ext-foundry-cost` sigue en 429 → `v_finops_agent_cost` sin coste (cuotas de
  tokens ya reales); reintentar SOLO tras reset horario.
- Regenerar PAT y commitear (sin versionar desde 2026-07-02).
- Promover a `curated` los 20 `name_match` tras revisión (o versionarlos).
