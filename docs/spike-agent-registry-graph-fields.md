---
type: spike
title: Spike — campos reales del Agent Registry vía Graph API (ADR-K23 eje 3)
status: ready-to-run
date: 2026-06-11
service: agent-365
adr: ADR-K23
tags: [type/spike, domain/microsoft-agentic, status/active]
up: "[[KnowledgeBase/microsoft-services/agent-365/agent-365]]"
---

> **Snapshot** — fuente canónica: vault Obsidian `KnowledgeBase/microsoft-services/agent-365/`. Script ejecutable: `spikes/spike_agent_registry_fields.ps1`.

# Spike — campos reales del Agent Registry vía Graph API

> **Objetivo (ADR-K23, eje 3):** determinar si la Graph registry API expone metadata rica por agente (description / instructions / manifest / skills) o solo inventario. De ello depende si la re-plataforma de `detect_duplicates.py` es total o el diseño final es híbrido (registry = universo, Dataverse = enriquecimiento).
> **Esfuerzo estimado:** 30-60 min · **Requisitos:** rol AI Administrator (o Global Admin) · PowerShell 7+ · módulo `Microsoft.Graph.Authentication`.

## Endpoints documentados (verificado 2026-06-11)

| Familia | Endpoint | Qué devuelve según docs |
|---|---|---|
| **Catalog packages** (A365-powered, public preview 2026-05-01) | `GET /beta/copilot/admin/catalog/packages` | Inventario completo: Microsoft, External, Shared, Custom |
| | `GET /beta/copilot/admin/catalog/packages/{id}` | "Detailed metadata… including **properties and manifest details**" ← clave para C2 |
| **Agent Registry** (Entra Agent ID) | `GET /beta/agentRegistry/agentInstances` / `/{id}` | Instancia operacional: endpoint URL, identidad, originating store, owner |
| | `GET /beta/agentRegistry/agentInstances/{id}/agentCardManifest` | **Agent card**: capabilities, skills, discovery metadata ← clave para C3 |

**Avisos:**
- MC1173195 (actualizado 2026-04-30): el uso de las Agent 365 Graph APIs **requiere licencia Agent 365** — si el tenant no la tiene, esperar 403 (→ C4, hallazgo, no fallo).
- MC1297981: la **antigua** agent registry Graph API se retira el **2026-06-15** — no usarla; los agentes creados en Copilot Studio/Foundry no requieren re-registro (fluyen nativos).
- Estado de la API (community, abril 2026): primera iteración **read-only** (LIST/GET); sin acciones de gestión todavía.

## Criterios de aceptación

| # | Pregunta | Si SÍ | Si NO |
|---|---|---|---|
| **C1** | ¿El listado incluye agentes CS + Foundry + declarativos con campos mínimos (id, nombre, tipo, origen, estado, owner)? | Registry = universo canónico confirmado | Revisar permisos/preview gaps |
| **C2** | ¿El detalle de package incluye manifest con description/instructions? | Adapters de enriquecimiento se simplifican → re-plataforma casi total | Híbrido Dataverse confirmado como diseño final |
| **C3** | ¿agentCardManifest existe y está poblado para agentes de plataforma (no solo self-registered), con skills/description? | Segunda fuente de metadata rica para similitud | Agent cards solo útiles para agentes A2A self-registered |
| **C4** | ¿403 por licencia/permiso? | — | Confirma gate de licencia A365 sobre la API (coherente con tiering eje 1); anotar permiso exacto del error |

## Resultado

_(completar tras ejecución)_

- C1:
- C2:
- C3:
- C4:
- Decisión derivada para Decision 2 de ADR-K23:

## Cross-references

- ADR-K23 (docs/) — eje 3
- `detect_duplicates.py` (Copilot Studio analysis) — candidato a re-plataforma
- Fuentes: learn.microsoft.com/microsoft-agent-365/admin/graph-api · learn.microsoft.com/entra/agent-id/identity-platform/publish-agents-to-registry · MC1173195 · MC1297981
