"""Backfill dim_agent.instructions + config_hash for agents whose prompt was
missed on the original load.

Context: Copilot Studio agents store their prompt under the PascalCase
``Instructions`` key of the ``AgentMetadatas`` element (declarative agents use
lowercase ``instructions``). The current build_star_pg._parse_definition already
resolves both, so this backfill just applies that resolution to rows that are
still empty -- without a full reload.

Surgical: only touches dim_agent (instructions, config_hash) for cloud='m365'
rows that are currently NULL/empty. Does NOT reload metrics/traces or rebuild
the snapshot. Reuses the loader's own _connect / _parse_definition / _hash so
hashing and the DB connection stay identical to build_star_pg.

Run from the agentlens/ directory:
    uv run python star/backfill_prompts.py
    uv run python star/backfill_prompts.py path\\to\\m365_agents.json
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_star_pg as bs  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
AGENTS = sys.argv[1] if len(sys.argv) > 1 else os.path.join(_HERE, "m365_agents.json")

# honour AGENTLENS_PG_* from .env whether launched from agentlens/ or star/
bs._load_env_file(".env")
bs._load_env_file(os.path.join(_HERE, "..", ".env"))
SCHEMA = os.environ.get("AGENTLENS_PG_SCHEMA", "agentlens")

if "AGENTLENS_PG_PASSWORD" not in os.environ:
    sys.exit("AGENTLENS_PG_PASSWORD not set (run from agentlens/ so .env is found)")

with open(AGENTS, encoding="utf-8") as handle:
    data = json.load(handle)
packages = data.get("value", [data]) if isinstance(data, dict) else data

rows = []
for pkg in packages or []:
    if not isinstance(pkg, dict) or not pkg.get("id"):
        continue
    instructions, _caps, _acts = bs._parse_definition(pkg)
    if instructions and instructions.strip():
        rows.append((instructions.strip(), bs._hash(instructions), str(pkg["id"])))

conn = bs._connect(SCHEMA)
try:
    with conn.cursor() as cur:
        cur.executemany(
            f"UPDATE {SCHEMA}.dim_agent "
            f"SET instructions = %s, config_hash = %s "
            f"WHERE cloud = 'm365' AND native_agent_id = %s "
            f"AND (instructions IS NULL OR instructions = '')",
            rows,
        )
        filled = cur.rowcount
    conn.commit()
    print(f"[backfill] resolvable prompts: {len(rows)}")
    print(f"[backfill] rows filled (were empty): {filled}")
finally:
    conn.close()
