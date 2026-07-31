"""
AgentLens · ETL Vía 2 — Graph Reports API -> agentlens.fact_tenant_usage_daily

Fuente : GET /v1.0/reports/getOffice365ActiveUserCounts(period='D180')
Permiso: Reports.Read.All (application) — concedido al SP el 13-jul-2026.
Config : lee el .env de agentlens (AZURE_* y AGENTLENS_PG_*); sin env extra.
Salida : upsert idempotente sobre PK (date_key, workload) — reejecutable.

Uso: python star/etl_graph_m365_usage.py [--period D30] [--env RUTA_ENV]
Programado: Scheduled/agentlens-m365-usage-weekly (D30 semanal).
"""

import argparse
import csv
import io
import os
import sys

import psycopg2
import psycopg2.extras
import requests

WORKLOAD_COLS = {
    "Office 365": "office365",
    "Exchange": "exchange",
    "OneDrive": "onedrive",
    "SharePoint": "sharepoint",
    "Skype For Business": "skype",
    "Yammer": "yammer",
    "Teams": "teams",
}

UPSERT = """
INSERT INTO agentlens.fact_tenant_usage_daily
    (date_key, workload, active_users, report_refresh_date)
VALUES %s
ON CONFLICT (date_key, workload) DO UPDATE SET
    active_users        = EXCLUDED.active_users,
    report_refresh_date = EXCLUDED.report_refresh_date,
    loaded_at           = now();
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


def get_token(env: dict) -> str:
    resp = requests.post(
        f"https://login.microsoftonline.com/{env['AZURE_TENANT_ID']}/oauth2/v2.0/token",
        data={
            "client_id": env["AZURE_CLIENT_ID"],
            "client_secret": env["AZURE_CLIENT_SECRET"],
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def fetch_csv(token: str, period: str) -> str:
    url = f"https://graph.microsoft.com/v1.0/reports/getOffice365ActiveUserCounts(period='{period}')"
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=90)
    if resp.status_code == 403:
        sys.exit("403 de Graph: falta Reports.Read.All (application) + admin consent en el SP.")
    resp.raise_for_status()
    return resp.content.decode("utf-8-sig")


def to_rows(csv_text: str):
    """Wide (una fila/dia, una columna/workload) -> long (dia x workload)."""
    rows = []
    for rec in csv.DictReader(io.StringIO(csv_text)):
        report_date = rec.get("Report Date")
        if not report_date:
            continue
        date_key = int(report_date.replace("-", ""))
        refresh = rec["Report Refresh Date"]
        for col, workload in WORKLOAD_COLS.items():
            val = (rec.get(col) or "").strip()
            if val != "":
                rows.append((date_key, workload, int(val), refresh))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", default="D30", choices=["D7", "D30", "D90", "D180"])
    parser.add_argument("--env", default=os.path.join(os.path.dirname(__file__), "..", ".env"))
    args = parser.parse_args()

    env = load_env(args.env)
    rows = to_rows(fetch_csv(get_token(env), args.period))
    if not rows:
        print("Graph devolvio el informe sin filas de detalle diario; nada que cargar.")
        return 1

    dsn = (
        f"host={env.get('AGENTLENS_PG_HOST', 'localhost')} "
        f"port={env.get('AGENTLENS_PG_PORT', '5432')} "
        f"dbname={env.get('AGENTLENS_PG_DBNAME', 'claude_db')} "
        f"user={env.get('AGENTLENS_PG_USER', 'postgres')} "
        f"password={env.get('AGENTLENS_PG_PASSWORD', '')}"
    )
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, UPSERT, rows, page_size=500)

    days = len({r[0] for r in rows})
    print(f"Upsert OK: {len(rows)} filas ({days} dias), periodo {args.period}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
