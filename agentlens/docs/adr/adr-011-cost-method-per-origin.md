# ADR-011: Cost Method per Origin + Driver-Based Costing

- **Status:** accepted
- **Date:** 2026-07-31
- **Deciders:** gustavoaldaya

## Context
La capa FinOps existente (`v_finops_cost_allocation`, ADR-010) solo cubría el
prorrateo de la factura cloud (Azure/AWS) por share de tokens de trazas
atribuidas. Ese modelo asume implícitamente que **todo** coste de agente es
"factura cloud medida", pero el catálogo real de `dim_agent` es
mayoritariamente M365: el 94% de los agentes (Copilot Studio, Agent Builder,
SharePoint, Toolkit, apps Teams) no tiene ninguna fila en
`fact_resource_cost` y por tanto queda invisible en el coste — no porque no
cueste, sino porque su coste no se factura como cloud metering. Además, el
consumidor del dashboard (CFO) no tenía forma de distinguir un coste medido
con precisión de uno estimado o repartido, porque la vista no exponía el
método de cálculo.

## Choice
Modelo de coste explícito por **origen/tipo de agente**, con dos piezas de
configuración versionable y una vista unificada:

1. **`cost_method_map`**: mapea `cloud` + patrón `LIKE` sobre `agent_type` a
   un `cost_method` + `cost_driver`, con `priority` (menor gana) para
   resolver solapes y un centinela `__NULL__` para `agent_type IS NULL`.
2. **Cálculo genérico por drivers**: `coste = driver_qty × tarifa vigente`,
   con las tarifas en `cost_driver_rate` (versionada en el tiempo por
   `valid_from`/`valid_to`, `rate_quality` explícita).
3. **`v_finops_agent_cost_unified`**: vista unificada por método/driver, con
   `cost_method`, `cost_quality` y una fila `unallocated` explícita **por
   método** (no una única bolsa global) para que el remanente sin repartir
   sea auditable método a método.
4. **`v_agent_cost_method`**: etiqueta los 1.909 agentes del catálogo
   (incluidos los 310 `bundled_zero` que no tienen ninguna fila de coste)
   con el método que les corresponde, para que el dashboard pueda mostrar
   "sin coste marginal" como respuesta explícita en vez de un vacío.

### Métodos soportados
- **`metered_allocated`** (Azure/AWS, incluidos los agentes M365 federados
  con runtime Foundry/Databricks/Bedrock): factura cloud prorrateada por
  share de tokens de trazas atribuidas (reutiliza `v_finops_cost_allocation`
  sin modificarla). `cost_quality`: `billed_allocated` / `billed_unallocated`.
- **`credit_rated`** (M365 Copilot Studio, Microsoft 365 Copilot Agent
  Builder y agentes declarativos): créditos Copilot × tarifa PAYG, repartidos
  por share de eventos de auditoría entre agentes elegibles. El pool semanal
  de créditos se parte por `is_copilot_licensed`:
  - créditos de personas **no licenciadas** → PAYG, facturable, **aditivo**
    (`rated_payg_allocated` / `rated_payg_unallocated`).
  - créditos de personas **licenciadas** → coste sombra ya cubierto por el
    asiento Copilot, **memo no aditivo** (`rated_included`).
- **`license_amortized`** (nivel de tenant, no se reparte por agente):
  nº de asientos con licencia Copilot activa × tarifa mensual semanalizada
  (`× 12/52.18`). `cost_quality`: `estimated`.
- **`bundled_zero`** (SharePoint, Microsoft 365 Agents Toolkit, apps Teams /
  `Not Available`): coste marginal cero, ya incluido en la licencia M365
  base. No emite filas en `v_finops_agent_cost_unified`; solo aparece
  etiquetado en `v_agent_cost_method`.

### Regla anti-doble-conteo
El **total aditivo** es `billed_* + rated_payg_* + license_amortized`.
`rated_included` queda **excluido** del total aditivo porque es una
valoración alternativa (memo) del mismo consumo de créditos que ya está
cubierto por el asiento de licencia — sumarlo duplicaría ese coste.

La elegibilidad del reparto de créditos PAYG está gobernada por
`cost_method_map`: los eventos de auditoría de agentes cuyo método NO es
`credit_rated` (p. ej. `bundled_zero` o `metered_allocated` federado) siguen
contando en el denominador semanal (total de eventos), pero no en el
numerador de ningún agente — su share "engorda" la fila
`rated_payg_unallocated` en vez de desaparecer o repartirse indebidamente.

### Tarifas semilla
Todas con `rate_quality = 'estimated'`, pendientes de confirmar la política
PAYG con Microsoft:
- **Crédito**: 0,008684 € (PAYG $0,01/crédito × fx 0,868405 xe.com
  31-jul-2026).
- **Asiento Copilot**: 26,05 €/mes ($30/usuario/mes Enterprise × fx).
- **Pack alternativo**: 0,006947 € (pack $200/25.000 créditos × fx),
  documentado en `cost_driver_rate` pero **no usado** por la vista unificada
  (referencia para comparación, no fuente activa).

## Consequences
- El dashboard CFO gana dos dimensiones nuevas de corte: `cost_method` y
  `cost_quality`, en vez de una única cifra de coste sin contexto de cómo se
  obtuvo.
- Cifras de referencia al 31-jul-2026: **total aditivo 3.369,02 €**
  (metered 167,09 € + PAYG 182,57 € + licencias 3.019,36 €) y **memo sombra
  12.264,44 €** (`rated_included`, no sumado al total).
- Validación independiente con cuadres al céntimo por caminos separados
  (bloque a bloque, contra `v_finops_cost_allocation` y `fact_copilot_credits`
  respectivamente) antes de aceptar la vista unificada como fuente para el
  dashboard.
- `v_finops_cost_allocation` (ADR-010) no se modifica; `v_finops_agent_cost_unified`
  la reutiliza como bloque A, agregada por `model_key`.

## Related
- [ADR-010 (Attribution as Tiered Resolution)](../../star/agentlens_schema_pg.sql)
  — ver sección "Identity resolution + span grain + consumption tiers" en el
  DDL; `v_finops_agent_cost_unified` es la evolución de
  `v_finops_cost_allocation` hacia coste multi-método.
- `agentlens/star/agentlens_schema_pg.sql`, sección `ADR-011: COSTE POR
  MÉTODO Y DRIVERS` — DDL de `cost_method_map`, `cost_driver_rate`,
  `v_agent_cost_method` y `v_finops_agent_cost_unified`.

---
> Canonical source: AgentLens design notes in the Obsidian vault (`AI_Observability/`). This file is a Git-tracked mirror for traceability.
