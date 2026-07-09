"""Sonda de caller para cerrar formalmente ADR-010 (fuente de usuario = Purview).

Verifica empiricamente, contra el workspace de Log Analytics (no contra lo que
el extractor proyecta), si ALGUN campo de identidad de caller viene poblado en
la telemetria Foundry-OTel:

  1. Columnas nativas ``UserId`` / ``UserAuthenticatedId`` / ``UserAccountId``
     en AppDependencies, AppTraces, AppRequests y AppGenAIContent.
  2. Claves ``user`` / ``enduser`` / ``caller`` dentro del bag ``Properties``
     de los spans y eventos gen_ai.
  3. Muestra de hasta 5 filas ofensivas, si las hay.

Interpretacion del veredicto: solo ``UserAuthenticatedId`` / ``UserAccountId``
o ``enduser.id`` constituyen identidad real de caller (chargeback / forense);
``UserId`` a solas puede ser el id anonimo del SDK y no habilita nada.

Uso (desde agentlens/):
    uv run python star/probe_caller.py
    uv run python star/probe_caller.py --lookback 60d
    uv run python star/probe_caller.py --json caller_probe.json

Requiere en .env / entorno: LOG_ANALYTICS_WORKSPACE_ID (+ credencial Azure con
lectura sobre el workspace). Consulta la API de Log Analytics: NO consume la
cuota horaria de Cost Management.
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

_TABLES = "AppDependencies, AppTraces, AppRequests, AppGenAIContent"


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
    parser = argparse.ArgumentParser(prog="probe_caller")
    parser.add_argument("--lookback", default="40d",
                        help="ventana KQL de las sondas (default 40d)")
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

    # ---- Sonda 1: columnas nativas User* por tabla -----------------------
    print(f"\n=== 1) Columnas nativas User* por tabla (ultimos {lb}) ===")
    kql1 = f"""
union isfuzzy=true {_TABLES}
| where TimeGenerated > ago({lb})
| summarize filas = count(),
    user_id = countif(isnotempty(UserId)),
    user_auth = countif(isnotempty(UserAuthenticatedId)),
    user_account = countif(isnotempty(UserAccountId))
  by Type
| order by filas desc
"""
    rows1 = _rows(_query(probe, token, workspace, kql1))
    _print_rows(rows1)
    results["native_columns"] = rows1

    # ---- Sonda 2: claves user/enduser/caller en el bag Properties de los
    # spans y eventos gen_ai -----------------------------------------------
    print(f"\n=== 2) Claves user/enduser/caller en Properties gen_ai (ultimos {lb}) ===")
    kql2 = f"""
union isfuzzy=true {_TABLES}
| where TimeGenerated > ago({lb}) and isnotempty(Properties)
| where isnotempty(Properties["gen_ai.system"])
    or isnotempty(Properties["gen_ai.operation.name"])
| mv-expand prop_key = bag_keys(Properties)
| extend k = tostring(prop_key)
| where k contains "user" or k contains "enduser" or k contains "caller"
| summarize n = count() by Type, k
| order by n desc
"""
    rows2 = _rows(_query(probe, token, workspace, kql2))
    _print_rows(rows2, limit=60)
    results["property_keys"] = rows2

    # ---- Sonda 2b: muestra de los spans cuyo bag trae user.id/enduser.id --
    print(f"\n=== 2b) Muestra de spans con user.id/enduser.id en el bag (ultimos {lb}) ===")
    kql2b = f"""
union isfuzzy=true {_TABLES}
| where TimeGenerated > ago({lb}) and isnotempty(Properties)
| where isnotempty(Properties["user.id"]) or isnotempty(Properties["enduser.id"])
| project TimeGenerated, Type, OperationId,
    op = tostring(Properties["gen_ai.operation.name"]),
    agent = tostring(Properties["gen_ai.agent.id"]),
    user_id = tostring(Properties["user.id"]),
    enduser_id = tostring(Properties["enduser.id"])
| take 10
"""
    rows2b = _rows(_query(probe, token, workspace, kql2b))
    _print_rows(rows2b, limit=10)
    results["bag_identity_sample"] = rows2b

    # ---- Sonda 3: muestra de filas ofensivas, si existen ------------------
    print(f"\n=== 3) Muestra de filas con algun User* poblado (ultimos {lb}) ===")
    kql3 = f"""
union isfuzzy=true {_TABLES}
| where TimeGenerated > ago({lb})
| where isnotempty(UserId) or isnotempty(UserAuthenticatedId)
    or isnotempty(UserAccountId)
| project TimeGenerated, Type, OperationId,
    UserId, UserAuthenticatedId, UserAccountId
| take 5
"""
    rows3 = _rows(_query(probe, token, workspace, kql3))
    _print_rows(rows3, limit=5)
    results["offending_sample"] = rows3

    # ---- Veredicto --------------------------------------------------------
    print("\n=== Veredicto ===")
    real_identity = sum(
        int(r.get("user_auth") or 0) + int(r.get("user_account") or 0)
        for r in rows1
    )
    anon_only = sum(int(r.get("user_id") or 0) for r in rows1)
    identity_keys = [
        r for r in rows2
        if str(r.get("k", "")).endswith("user.id")
        or "enduser" in str(r.get("k", ""))
    ]
    if real_identity == 0 and not identity_keys:
        print("  0 valores en UserAuthenticatedId / UserAccountId y 0 claves")
        print("  enduser.* en los bags gen_ai de todas las tablas.")
        if anon_only:
            print(f"  (UserId anonimo del SDK poblado en {anon_only} filas:")
            print("   no es identidad real, no habilita chargeback ni forense.)")
        print("  -> CONFIRMADO: caller null-by-nature en Foundry-OTel.")
        print("     La dimension usuario viene de Purview (ADR-005).")
        print("     Cerrar formalmente en ADR-010 y retirar la afirmacion")
        print("     'caller recuperable' del anexo de implementacion.")
    else:
        print(f"  Identidad detectada en columnas nativas ({real_identity} filas)")
        print("  y/o claves de identidad en los bags gen_ai:")
        for row in identity_keys:
            print("    " + json.dumps(row, ensure_ascii=False))
        print("  -> REVISAR la sonda 2b (muestra de spans con user.id/enduser.id)")
        print("     y la sonda 3: si es identidad genuina de caller, el ADR-010")
        print("     mantiene 'caller recuperable' y hay que poblar caller_id")
        print("     tambien desde el bag, no solo desde las columnas nativas.")

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
