"""
AgentLens · ETL Viva Insights Credits → agentlens.fact_copilot_credits

Fuente : Lakehouse AI_Observability (Fabric F2, workspace 1cecab71-...)
         Tablas: viva_user_ai_consumption JOIN viva_hr
         Cargadas por: Dataflow Gen2 AgentLens-VivaInsights-Consumption
         Dataflow ID: 49477bc4-d870-4a03-8cf2-7b27c2b5c305
Destino: agentlens.fact_copilot_credits (upsert idempotente, PK date_key × person_id × service_id)
Config : lee .env de agentlens (AZURE_* y AGENTLENS_PG_*)
Uso    : python star/etl_viva_credits.py [--env RUTA_ENV]
Schedule: Scheduled/agentlens-viva-credits-weekly (miércoles 09:30 — después del refresh del Dataflow)

Conexión al Lakehouse vía SQL Endpoint de Fabric (ADLS OneLake API o JDBC).
El SQL Endpoint de AI_Observability expone las tablas Delta como SQL estándar.
Connection string: Server={sql_endpoint}.datawarehouse.fabric.microsoft.com;Database=AI_Observability
"""

from __future__ import annotations

import argparse
import os
import sys

import io

import psycopg2
import psycopg2.extras
import pyarrow.parquet as pq
import requests

# ── Constantes del Lakehouse ───────────────────────────────────────────────────────────────────────────────────────────────────────
WORKSPACE_ID  = "1cecab71-093b-44c8-935f-4242bb516abb"
LAKEHOUSE_ID  = "7f6aa857-fdc2-4255-bdb4-a1e72822166e"
SQL_ENDPOINT  = "0db9a6ee-9780-4c6d-94ae-536bf5e9dea3"
DATAFLOW_ID   = "49477bc4-d870-4a03-8cf2-7b27c2b5c305"

UPSERT = """
INSERT INTO agentlens.fact_copilot_credits (
    date_key, person_id, service_id, service_name, spending_policy_id,
    session_count, total_credits_used, user_limit, spending_policy_limit,
    is_copilot_licensed, organization, function_type, people_historical_id
) VALUES %s
ON CONFLICT (date_key, person_id, service_id) DO UPDATE SET
    service_name          = EXCLUDED.service_name,
    spending_policy_id    = EXCLUDED.spending_policy_id,
    session_count         = EXCLUDED.session_count,
    total_credits_used    = EXCLUDED.total_credits_used,
    user_limit            = EXCLUDED.user_limit,
    spending_policy_limit = EXCLUDED.spending_policy_limit,
    is_copilot_licensed   = EXCLUDED.is_copilot_licensed,
    organization          = EXCLUDED.organization,
    function_type         = EXCLUDED.function_type,
    people_historical_id  = EXCLUDED.people_historical_id,
    loaded_at             = now();
"""


def load_env(path: str) -> dict:
    env = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def _token(env: dict, scope: str) -> str:
    resp = requests.post(
        f"https://login.microsoftonline.com/{env['AZURE_TENANT_ID']}/oauth2/v2.0/token",
        data={
            "client_id": env["AZURE_CLIENT_ID"],
            "client_secret": env["AZURE_CLIENT_SECRET"],
            "scope": scope,
            "grant_type": "client_credentials",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def get_fabric_token(env: dict) -> str:
    return _token(env, "https://api.fabric.microsoft.com/.default")


def get_onelake_token(env: dict) -> str:
    """OneLake DFS usa la audiencia de Azure Storage, no Fabric API."""
    return _token(env, "https://storage.azure.com/.default")


def fetch_lakehouse_tables(fab_token: str, ol_token: str) -> list[dict]:
    """Lee las tablas Delta del Lakehouse vía OneLake DFS API.

    Verificación del Dataflow con fab_token (Fabric API).
    Descarga de parquets con ol_token (Azure Storage / OneLake).
    """
    # Verificar último refresh exitoso del Dataflow
    h_fab = {"Authorization": f"Bearer {fab_token}"}
    r = requests.get(
        f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}"
        f"/items/{DATAFLOW_ID}/jobs/instances?$top=5",
        headers=h_fab, timeout=20)
    jobs = r.json().get("value", [])
    last_ok = next((j for j in jobs if j.get("status") in ("Completed", "Succeeded")), None)
    if last_ok:
        print(f"[etl] Último refresh OK: {last_ok.get('endTimeUtc','?')[:19]} UTC")
    else:
        print("[etl] WARN: no se encontró refresh exitoso reciente")

    return _read_via_onelake(ol_token)


def _read_via_onelake(ol_token: str) -> list[dict]:
    """Lee las tablas Delta del Lakehouse vía OneLake DFS API.

    Requiere token con audiencia https://storage.azure.com/.default (no Fabric API).
    Las tablas tienen schema habilitado: ruta es .../Tables/dbo/<table>/.
    El delta log marca qué parquets son activos — se leen solo los del
    commit más reciente para evitar filas duplicadas de commits anteriores.
    """
    h = {"Authorization": f"Bearer {ol_token}", "x-ms-version": "2019-12-12"}
    base = f"https://onelake.dfs.fabric.microsoft.com/{WORKSPACE_ID}/{LAKEHOUSE_ID}/Tables/dbo"

    def active_parquets(table: str) -> list[str]:
        """Lee el delta log y devuelve solo los parquets del commit activo."""
        import json as _json
        log_url = f"{base}/{table}/_delta_log/"
        r = requests.get(log_url, headers=h,
                         params={"resource": "filesystem", "recursive": "false"}, timeout=20)
        if r.status_code != 200:
            print(f"[etl] OneLake delta_log {table}: HTTP {r.status_code}")
            return []
        log_files = sorted(
            p["name"] for p in r.json().get("paths", [])
            if p["name"].endswith(".json") and not p.get("isDirectory")
        )
        if not log_files:
            return []
        # Leer el último commit JSON del delta log
        last_log_path = f"{WORKSPACE_ID}/{log_files[-1]}"
        last_log = requests.get(
            f"https://onelake.dfs.fabric.microsoft.com/{last_log_path}",
            headers=h, timeout=20)
        if last_log.status_code != 200:
            return []
        # Los archivos activos son los que aparecen en líneas "add" del commit
        added = []
        for line in last_log.text.splitlines():
            try:
                entry = _json.loads(line)
                path = (entry.get("add") or {}).get("path")
                if path:
                    added.append(
                        f"{WORKSPACE_ID}/{LAKEHOUSE_ID}/Tables/dbo/{table}/{path}"
                    )
            except Exception:
                continue
        return added

    rows: list[dict] = []
    for table in ["viva_user_ai_consumption", "viva_hr"]:
        parquets = active_parquets(table)
        if not parquets:
            # Fallback: listar todos los parquets del directorio
            r = requests.get(f"{base}/{table}/", headers=h,
                             params={"resource": "filesystem", "recursive": "true"}, timeout=20)
            if r.status_code != 200:
                print(f"[etl] OneLake list {table}: HTTP {r.status_code}")
                continue
            parquets = [
                f"{WORKSPACE_ID}/{p['name']}" for p in r.json().get("paths", [])
                if p["name"].endswith(".parquet") and not p.get("isDirectory")
            ]
        print(f"[etl] {table}: {len(parquets)} parquets activos")
        for pf in parquets:
            pr = requests.get(
                f"https://onelake.dfs.fabric.microsoft.com/{pf}",
                headers=h, timeout=60)
            if pr.status_code == 200 and len(pr.content) > 100:
                rows.extend(pq.read_table(io.BytesIO(pr.content)).to_pylist())
            elif pr.status_code != 200:
                print(f"[etl] Download {pf.split('/')[-1]}: HTTP {pr.status_code}")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default=os.path.join(os.path.dirname(__file__), "..", ".env"))
    args = parser.parse_args()

    env = load_env(args.env)
    fab_token = get_fabric_token(env)      # Fabric API (jobs, workspace)
    ol_token  = get_onelake_token(env)     # Azure Storage (OneLake DFS)
    rows = fetch_lakehouse_tables(fab_token, ol_token)

    if not rows:
        print("[etl] Sin filas. Causas frecuentes: SP sin rol Member en el workspace "
              "de Fabric, o las tablas aún no tienen datos (dataflow pendiente).")
        return 1

    print(f"[etl] {len(rows)} filas leídas del Lakehouse")

    # Separar activity de HR (se distinguen por la presencia de MetricDate)
    activity = [r for r in rows if r.get('MetricDate') is not None]
    hr_list  = [r for r in rows if r.get('MetricDate') is None]
    hr_idx   = {r['PeopleHistoricalId']: r for r in hr_list if r.get('PeopleHistoricalId')}
    print(f"[etl] activity={len(activity)} hr={len(hr_list)}")

    # Deduplicar por PK antes del upsert (dos parquets del mismo commit
    # pueden solapar filas con idéntica PK — last-write-wins por MetricDate)
    by_pk: dict[tuple, tuple] = {}
    for r in activity:
        dt = str(r.get('MetricDate', ''))[:10].replace('-', '')
        if not dt or len(dt) != 8:
            continue
        phid = r.get('PeopleHistoricalId')
        h2 = hr_idx.get(phid, {})
        cred = r.get('TotalCopilotCreditsUsed')
        pk = (int(dt), r.get('PersonId'), r.get('ServiceId'))
        by_pk[pk] = (
            int(dt),
            r.get('PersonId'), r.get('ServiceId'), r.get('ServiceName'),
            r.get('SpendingPolicyId'), r.get('SessionCount'),
            float(cred) if cred is not None else None,
            r.get('UserLimit'), r.get('SpendingPolicyLimit'),
            h2.get('IsCopilotLicensed'), h2.get('Organization') or '',
            h2.get('FunctionType') or '', phid,
        )
    pg_rows = list(by_pk.values())

    dsn = (f"host={env.get('AGENTLENS_PG_HOST','localhost')} "
           f"port={env.get('AGENTLENS_PG_PORT','5432')} "
           f"dbname={env.get('AGENTLENS_PG_DBNAME','claude_db')} "
           f"user={env.get('AGENTLENS_PG_USER','postgres')} "
           f"password={env.get('AGENTLENS_PG_PASSWORD','')}")

    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, UPSERT, pg_rows, page_size=500)
    print(f"[etl] Upsert OK: {len(pg_rows)} filas → agentlens.fact_copilot_credits")
    return 0


if __name__ == "__main__":
    sys.exit(main())
