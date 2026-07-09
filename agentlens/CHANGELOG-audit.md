# CHANGELOG — Cost, Caller & Purview Audit (v0.5.0)

Cierre de versión: **2026-07-09 (tarde)**. Enciende dato real en los tres
planos de AgentLens: FinOps (coste jun+jul asignado por cuota de tokens),
caller (cierre empírico de ADR-010) y gobierno/usuario (Purview audit
end-to-end con join directo al catálogo). Sucede a CHANGELOG-attribution.md.

Commiteado a `main` vía la MCP de GitHub en un único commit de release
(los ficheros del working tree local son idénticos a lo empujado;
reconciliar con `git fetch && git reset --soft origin/main`).

## Resultado validado (contra `claude_db.agentlens` y `_local_raw/`)

- `fact_resource_cost`: **1.624 filas · 71,34 € · 2026-06-01..07-09** (antes 0).
- `v_finops_agent_cost` asigna **57,66 € entre 25 agentes / 18 días**
  (top: tiempo-visualizador 15,64 € · finops-ai-agent 14,08 €).
- Caller cerrado en ADR-010 (addendum): columnas nativas `User*` a 0 en
  249.608 filas / 4 tablas / 40d; `user.id` del bag = identidad Entra real
  best-effort; Purview autoritativo.
- Purview audit: **6.853 eventos Copilot/AI** (de 110.335 del tenant),
  0 inválidos; join directo `TargetPlatformAgentId` (`T_…`) ↔
  `dim_agent.native_agent_id`; superficie Agent365 `InferenceCall` activa
  (2.303 eventos).
- Hallazgo estructural: los GUID `gen_ai.agent.id` son **efímeros por
  instancia** (6 GUIDs = 1 "SupplyChainSupervisor"); la identidad estable
  es el nombre → no puentear GUIDs.

## Cambios

### `extractors/foundry_cost.py` — fix
- El `timeframe` estaba hardcoded a `MonthToDate` e ignoraba el cursor del
  watermark: rebobinar no backfilleaba nada. Ahora con cursor construye
  `Custom` + `timePeriod` (since→hoy); sin cursor mantiene `MonthToDate`.
  Bonus: `Custom` desde cursor tampoco pierde la cola del mes anterior al
  cambiar de mes.

### `extractors/foundry_traces.py` — fix
- `caller_id` coalescea las claves del bag `user.id`/`enduser.id` ANTES de
  las columnas nativas (que devuelven `''` no-nulo y taparían el bag).
  Empírico: las nativas están vacías en este workspace; `user.id` trae
  object IDs de Entra cuando la ruta de emisión lo incluye. Forward-only.

### `extractors/purview_audit.py` — feat (live cutover)
- Ventana explícita `startTime/endTime` (≤24h, start ≤7d, clampado);
  primera captura acotada a `_FIRST_CAPTURE_HOURS = 6`. Sin ventana la API
  lista 24h de tenant entero y el run parece colgado.
- `_get_with_retry`: timeout 180s con 3 reintentos visibles (el primer
  listado del feed tarda >60s legítimamente; verificado que el host
  responde <1s desde red externa).
- `_to_record`: mapeo PascalCase→schema con **filtro de alcance** (solo
  eventos con `CopilotEventData` o workload Copilot/AI; el resto se
  descarta como fuera de alcance, NO vía cuarentena). `app_identity` desde
  `TargetPlatformAgentId` (el `AppIdentity` documentado no existe en este
  tenant). `CopilotEventData` + campos raíz no mapeados conservados como
  drift (fidelidad completa para el modelado del fact de audit).
- Progreso a stderr: ventana, token, blobs por página, esperas de 429.

### `extractors/core/azure_http.py` — core
- `_get_with_headers` acepta `timeout` (default 60.0, retrocompatible).

### `star/probe_caller.py` — feat (nuevo)
- Sonda empírica de identidad de caller sobre el workspace (4 KQL +
  veredicto): columnas nativas por tabla, claves user/enduser/caller en
  bags gen_ai, muestra de spans con `user.id`, y muestra de filas User*.
  Lección incorporada: el gate inicial solo miraba `enduser.*` y dio un
  falso CONFIRMADO con `user.id` delante; corregido.

## Deuda que deja abierta

- Fact de audit sin modelar (loader sin handler para
  `m365/purview/audit_log`): siguiente bloque.
- Particionado `dt=` por fecha de ejecución: pisa raw en re-extracciones y
  esquiva el watermark por ruta de `etl_load_log` (dos mordidas hoy).
- Segunda ruta de emisión en `AppTraces` (spans con `user.id`) invisible
  al extractor de trazas.
- ~110k registros de cuarentena en el lake del run fallido de purview.
