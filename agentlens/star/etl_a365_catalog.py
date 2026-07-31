"""
AgentLens · ETL registro A365 -> agentlens.a365_catalog_snapshot

Fuente : GET /beta/copilot/admin/catalog/packages ($select compacto, paginado)
Permiso: CopilotPackages.Read.All (application) — concedido al SP el 13-jul-2026.
Config : lee el .env de agentlens (AZURE_* y AGENTLENS_PG_*).
Salida : snapshot del dia (PK capture_date x package_id) — reejecutable.

Uso: python star/etl_a365_catalog.py [--env RUTA_ENV]
"""

import argparse
import os
import sys
from datetime import date

import psycopg2
import psycopg2.extras
import requests

SELECT = ("id,displayName,type,publisher,platform,ownerId,"
          "isBlocked,availableTo,deployedTo,lastModifiedDateTime")

UPSERT = """
INSERT INTO agentlens.a365_catalog_snapshot
    (capture_date, package_id, display_name, pkg_type, publisher, platform,
     owner_id, is_blocked, available_to, deployed_to, last_modified)
VALUES %s
ON CONFLICT (capture_date, package_id) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    pkg_type     = EXCLUDED.pkg_type,
    publisher    = EXCLUDED.publisher,
    platform     = EXCLUDED.platform,
    owner_id     = EXCLUDED.owner_id,
    is_blocked   = EXCLUDED.is_blocked,
    available_to = EXCLUDED.available_to,
    deployed_to  = EXCLUDED.deployed_to,
    last_modified = EXCLUDED.last_modified,
    loaded_at    = now();
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


def fetch_all(token: str) -> list[dict]:
    url = ("https://graph.microsoft.com/beta/copilot/admin/catalog/packages"
           f"?$top=200&$select={SELECT}")
    items: list[dict] = []
    while url:
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=90)
        if resp.status_code == 403:
            sys.exit("403 de Graph: falta CopilotPackages.Read.All en el SP.")
        resp.raise_for_status()
        data = resp.json()
        items += data.get("value", [])
        url = data.get("@odata.nextLink")
    return items


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default=os.path.join(os.path.dirname(__file__), "..", ".env"))
    args = parser.parse_args()
    env = load_env(args.env)

    items = fetch_all(get_token(env))
    if not items:
        print("Graph devolvio 0 paquetes; nada que cargar.")
        return 1

    capture = int(date.today().strftime("%Y%m%d"))
    rows = [
        (capture, i["id"], i.get("displayName"), i.get("type"), i.get("publisher"),
         i.get("platform"), i.get("ownerId"), bool(i.get("isBlocked")),
         i.get("availableTo"), i.get("deployedTo"), i.get("lastModifiedDateTime"))
        for i in items
    ]
    dsn = (
        f"host={env.get('AGENTLENS_PG_HOST', 'localhost')} "
        f"port={env.get('AGENTLENS_PG_PORT', '5432')} "
        f"dbname={env.get('AGENTLENS_PG_DBNAME', 'claude_db')} "
        f"user={env.get('AGENTLENS_PG_USER', 'postgres')} "
        f"password={env.get('AGENTLENS_PG_PASSWORD', '')}"
    )
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, UPSERT, rows, page_size=500)
    print(f"Upsert OK: {len(rows)} paquetes, capture_date={capture}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
