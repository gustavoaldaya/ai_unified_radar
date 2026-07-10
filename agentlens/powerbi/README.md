# AgentLens — Kit Power BI (CFO FinOps + CIO Gobierno)

> Contrato de datos: las vistas `agentlens.v_*` de `star/agentlens_schema_pg.sql`
> (sección "CAPA DE REPORTING"). Fuente: PostgreSQL `claude_db`, schema `agentlens`.
> Este kit se construye en Power BI Desktop en 4 pasos: conectar → importar →
> relacionar → pegar TMDL de medidas.

## 1. Conexión

- **Obtener datos → PostgreSQL database**
- Server: `localhost:5432` · Database: `claude_db`
- Modo: **Import** (volumen actual trivial; refresco = re-import)
- Credenciales: Database → usuario `postgres`

## 2. Tablas a importar (y renombrar)

Power BI nombra las tablas `agentlens <nombre>`; renombrarlas al nombre exacto
de la derecha para que el TMDL de medidas funcione sin tocar nada.

| Objeto en el navegador | Nombre en el modelo | Papel |
|---|---|---|
| `agentlens.v_cio_agent_scorecard` | `v_cio_agent_scorecard` | **Dimensión agente** (1 fila/agente de catálogo) + página CIO. Excluye `instructions` a propósito (tamaño de modelo). |
| `agentlens.dim_model` | `dim_model` | Dimensión modelo |
| `agentlens.dim_user` | `dim_user` | Dimensión usuario (UPN) |
| `agentlens.v_finops_cost_allocation` | `v_finops_cost_allocation` | Fact coste asignado (cloud, día, agente, modelo) + línea `unallocated` |
| `agentlens.fact_resource_cost` | `fact_resource_cost` | Fact coste de recurso (aperturas: suscripción, RG, recurso, meter) |
| `agentlens.v_usage_daily` | `v_usage_daily` | Fact uso (audit Purview: día, agente, usuario, record_type) |
| `agentlens.v_traces_daily` | `v_traces_daily` | Fact telemetría (día, agente, modelo, errores, duración) |
| `agentlens.v_finops_funnel_daily` | `v_finops_funnel_daily` | Funnel por (día, cloud) — página portada CFO |
| `agentlens.v_cio_dup_clusters` | `v_cio_dup_clusters` | Clusters de duplicados exactos (config_hash) |

NO importar `dim_agent` (contiene `instructions`, texto pesado; el scorecard ya
lleva todos los atributos descriptivos) ni `fact_agent_traces` /
`fact_agent_audit` en crudo (las vistas diarias ya agregan).

## 3. Calendario (Nueva tabla, DAX)

```dax
Calendario =
ADDCOLUMNS(
    CALENDAR(DATE(2026,6,1), TODAY() + 90),
    "date_key", VALUE(FORMAT([Date], "YYYYMMDD")),
    "Mes", FORMAT([Date], "YYYY-MM"),
    "Dia semana", FORMAT([Date], "ddd")
)
```

Marcar como tabla de fechas sobre `[Date]`.

## 4. Relaciones (todas muchos-a-uno, filtro único)

| Lado muchos | Lado uno |
|---|---|
| `v_finops_cost_allocation[date_key]` | `Calendario[date_key]` |
| `fact_resource_cost[date_key]` | `Calendario[date_key]` |
| `v_usage_daily[date_key]` | `Calendario[date_key]` |
| `v_traces_daily[date_key]` | `Calendario[date_key]` |
| `v_finops_funnel_daily[date_key]` | `Calendario[date_key]` |
| `v_finops_cost_allocation[agent_key]` | `v_cio_agent_scorecard[agent_key]` |
| `v_usage_daily[agent_key]` | `v_cio_agent_scorecard[agent_key]` |
| `v_traces_daily[agent_key]` | `v_cio_agent_scorecard[agent_key]` |
| `v_finops_cost_allocation[model_key]` | `dim_model[model_key]` |
| `v_traces_daily[model_key]` | `dim_model[model_key]` |
| `v_usage_daily[user_key]` | `dim_user[user_key]` |
| `v_cio_agent_scorecard[config_hash]` | `v_cio_dup_clusters[config_hash]` |

Notas:
- Las filas `unallocated` (agent_key NULL) y los eventos de audit al centinela
  quedan sin match en el scorecard → aparecen como *(En blanco)* en visuales por
  agente. Es intencional: es el remanente/no-atribuido, no un error.
- `v_cio_agent_scorecard[agent_key]` debe quedar como lado "uno" (1 fila por agente).

## 5. Medidas

Pegar `agentlens_measures.tmdl` en la **vista TMDL** de Power BI Desktop y
aplicar (crea la tabla `_Medidas`). Alternativa: crear las medidas a mano con
el DAX del propio fichero.

## 6. Páginas del informe (guion)

1. **CFO · Funnel** — tarjetas: `Coste recurso (EUR)`, `Coste asignado (EUR)`,
   `% coste asignado`, `Tokens (M)`, `Invocaciones`, `Eventos de uso`. Embudo
   Eventos → Invocaciones → Tokens → Coste asignado. Área apilada por día:
   asignado vs sin asignar. Slicers: cloud, mes.
2. **CFO · Detalle de coste** — matriz `subscription_id` → `resource_group` →
   `meter_category` → `meter_name` con `Coste recurso (EUR)`; tabla top
   recursos por coste (`resource_id`).
3. **CFO · Unit economics** — tabla top agentes: coste asignado, tokens,
   `EUR por 1M tokens`, `EUR por invocacion`; dispersión tokens vs coste con
   leyenda `dim_model[native_id]`.
4. **CIO · Scorecard** — tarjetas: `Agentes catalogo`, `Agentes con uso`,
   `Candidatos a decomision`, `Agentes en cluster`. Tabla scorecard (nombre,
   tipo, publisher, cluster_size, eventos, usuarios, última actividad, coste,
   tier) con slicers `tier`, `agent_type`, `publisher`, `single_user`.
5. **CIO · Duplicados** — barras top clusters por `members`; tabla de
   `v_cio_dup_clusters` (members, used_members, prompt_sample); drill a
   miembros vía la relación por `config_hash`.

## 7. Advertencias que el informe debe llevar impresas

- **Ventana de audit: 1 día (2026-07-09).** El tier `DECOMMISSION_CANDIDATE`
  es provisional hasta hacer backfill de Purview (retención 180 días en Audit
  Standard) y acumular 30–90 días. Coste y trazas sí cubren 2026-06-01→07-09.
- **El coste asignado cubre el plano Azure/Foundry.** AWS entra cuando se
  active el logging de Bedrock (cola #4); el matching por cloud ya lo soporta.
- **Uso M365 = proxy por audit** (el endpoint nativo `getCopilotAgentUsage`
  es techo de plataforma). Los 2.303 `InferenceCall` sin agent id del tenant
  caen a *(En blanco)* — señal de gobierno, no pérdida de datos.
