"""Discovery del workspace de Log Analytics para ``ext-foundry-traces``.

Sustituye el paso manual de KQL en el portal: ejecuta por API (con las mismas
credenciales ``DefaultAzureCredential`` que usa el extractor) tres sondas de
diagnostico y, al final, la KQL DE PRODUCCION exacta de ``foundry_traces.py``
con un ``take`` pequeno. Si la sonda 4 devuelve filas con ``gen_ai_agent_id``
poblado, el extractor esta listo para correr sin cambios; si devuelve 0 filas,
las sondas 1-3 dicen exactamente que ajustar.

Uso (desde agentlens/):
    uv run python star/discover_foundry_traces.py
    uv run python star/discover_foundry_traces.py --lookback 14d
    uv run python star/discover_foundry_traces.py --json out.json   # dump crudo

Requiere en .env / entorno: LOG_ANALYTICS_WORKSPACE_ID (+ credencial Azure con
permiso de lectura sobre el workspace: az login o service principal).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

# Ejecutable como script suelto: anclar el repo root al sys.path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from extractors.core.azure_http import LOG_ANALYTICS_SCOPE, AzureJsonSource  # noqa: E402
from extractors.foundry_traces import build_kql  # noqa: E402


class _Probe(AzureJsonSource):
    """AzureJsonSource es un mixin sin estado; alcanza para sondear."""


def _query(probe: _Probe, token: str, workspace: str, kql: str) -> dict[str, Any]:
    url = f"https://api.loganalytics.io/v1/workspaces/{workspace}/query"
    status, body, _ = probe._post_json(url, token, {"query": kql})
    if status != 200:
        raise RuntimeError(f"HTTP {status}: {json.dumps(body)[:500]}")
    return body


def _rows(body: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for table in body.get("tables", []):
        columns = [c["name"] for c in table.get("columns", [])]
        for values in table.get("rows", []):
            out.append(dict(zip(columns, values, strict=False)))
    return out


def _print_rows(rows: list[dict[str, Any]], *, limit: int = 30) -> None:
    if not rows:
        print("  (0 filas)")
        return
    for row in rows[:limit]:
        print("  " + json.dumps(row, default=str, ensure_ascii=False))
    if len(rows) > limit:
        print(f"  ... y {len(rows) - limit} filas mas")


def main() -> int:
    parser = argparse.ArgumentParser(prog="discover_foundry_traces")
    parser.add_argument("--lookback", default="7d",
                        help="ventana KQL para las sondas (default 7d)")
    parser.add_argument("--json", default=None,
                        help="si se indica, vuelca los resultados crudos a este fichero")
    args = parser.parse_args()

    _load_env_file(".env")
    workspace = os.environ.get("LOG_ANALYTICS_WORKSPACE_ID")
    if not workspace:
        print("ERROR: LOG_ANALYTICS_WORKSPACE_ID no definido (.env)", file=sys.stderr)
        return 2

    probe = _Probe()
    token = probe._aad_token(LOG_ANALYTICS_SCOPE)
    lb = args.lookback
    results: dict[str, Any] = {}

    # ---- Sonda 1: que tablas estan ingiriendo datos (via meta-tabla Usage,
    # barata: no escanea los datos) --------------------------------------
    print(f"\n=== 1) Tablas con ingesta (ultimos {lb}, via Usage) ===")
    kql1 = (
        f"Usage | where TimeGenerated > ago({lb}) "
        "| summarize mb = round(sum(Quantity), 2) by DataType "
        "| order by mb desc"
    )
    rows1 = _rows(_query(probe, token, workspace, kql1))
    _print_rows(rows1)
    results["tables"] = rows1

    # ---- Sonda 2: spans gen_ai en AppDependencies: cuantos, de que sistema,
    # que operaciones, rango temporal -------------------------------------
    print(f"\n=== 2) Spans gen_ai en AppDependencies (ultimos {lb}) ===")
    kql2 = f"""
AppDependencies
| where TimeGenerated > ago({lb}) and isnotempty(Properties["gen_ai.system"])
| summarize n = count(),
    first_seen = min(TimeGenerated), last_seen = max(TimeGenerated)
    by system = tostring(Properties["gen_ai.system"]),
       op = tostring(Properties["gen_ai.operation.name"])
| order by n desc
"""
    rows2 = _rows(_query(probe, token, workspace, kql2))
    _print_rows(rows2)
    results["gen_ai_spans"] = rows2

    # ---- Sonda 3: censo de claves reales del bag Properties (para ajustar
    # la KQL de produccion si el tenant usa otra variante de convencion) ---
    print(f"\n=== 3) Claves observadas en Properties (muestra 200 spans) ===")
    kql3 = f"""
AppDependencies
| where TimeGenerated > ago({lb}) and isnotempty(Properties["gen_ai.system"])
| take 200
| mv-expand prop_key = bag_keys(Properties)
| summarize n = count() by prop_key = tostring(prop_key)
| order by n desc
"""
    rows3 = _rows(_query(probe, token, workspace, kql3))
    _print_rows(rows3, limit=60)
    results["property_keys"] = rows3

    # ---- Sonda 4: la KQL DE PRODUCCION tal cual, con take 5 --------------
    print("\n=== 4) KQL de produccion (build_kql) | take 5 ===")
    kql4 = build_kql(None) + "\n| take 5"
    rows4 = _rows(_query(probe, token, workspace, kql4))
    _print_rows(rows4, limit=5)
    results["production_sample"] = rows4

    # ---- Veredicto -------------------------------------------------------
    print("\n=== Veredicto ===")
    if rows4:
        with_agent = [r for r in rows4 if (r.get("gen_ai_agent_id") or "").strip()]
        print(f"  KQL de produccion devuelve filas: {len(rows4)} (muestra)")
        print(f"  ... con gen_ai_agent_id poblado: {len(with_agent)}")
        if with_agent:
            print("  -> LISTO: uv run python -m extractors.run ext-foundry-traces")
        else:
            print("  -> Spans capturados pero SIN agent id: consumo quedara")
            print("     attributed=0 salvo spans del servicio de Agents. Revisar")
            print("     sonda 3 por si el id viaja bajo otra clave.")
    elif rows2:
        print("  Hay spans gen_ai pero la proyeccion no matchea: revisar sonda 3")
        print("  (nombres de claves) y ajustar build_kql en foundry_traces.py.")
    else:
        print("  0 spans gen_ai en la ventana: o el recurso Foundry no tiene")
        print("  tracing conectado a ESTE workspace, o no hubo trafico. Generar")
        print("  una invocacion de prueba y re-sondear; verificar workspace id.")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(results, handle, indent=2, default=str, ensure_ascii=False)
        print(f"\n[dump crudo -> {args.json}]")
    return 0


def _load_env_file(path: str) -> None:
    """Mismo loader minimo que build_star_pg.py (shell gana sobre fichero)."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, value)


if __name__ == "__main__":
    raise SystemExit(main())
