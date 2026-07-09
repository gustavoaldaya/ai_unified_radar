"""``build_star.py`` -- local SQLite star for AgentLens, anchored on the AGENT.

Agent-centric star (see AI_Observability session 2026-07-06): ``dim_agent`` is
the conformed spine, cloud-agnostic, and every fact hangs off it.

Sources:
  * M365 agents   -- a JSON export of copilotPackage DETAIL objects
    (GET /beta/copilot/admin/catalog/packages/{id}, delegated CopilotPackages.Read.All).
    The detail carries the real config inside elementDetails[].elements[].definition
    (a nested JSON string): instructions (the prompt), capabilities (skills) and
    actions (connectors). --> dim_agent (+ skill / connector bridges).
  * foundry/traces, bedrock/traces (parquet) -- agent-attributed consumption.
  * foundry/metrics, bedrock/metrics (parquet) -- model-grain, UNATTRIBUTED.

Two consumption grains: fact_agent_consumption (transaction, agent x metric x
model x ts) and fact_agent_snapshot (daily rollup, agent x metric x date).

Full rebuild each run. Multi-valued config (skills, connectors) lives in bridge
tables. Dedup key = config_hash (hash of the prompt) + agent_type + skill/connector sets.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import sqlite3
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

import pyarrow.parquet as pq

UNATTRIBUTED = "(unattributed)"
UNKNOWN_MODEL = "(unknown)"

# traces -> agent-attributed consumption. Each span emits one row per measure.
TRACE_SOURCES = [
    {
        "glob": "foundry/traces/dt=*/*.parquet",
        "cloud": "azure",
        "agent_id": ("gen_ai_agent_id",),
        "agent_name": "gen_ai_agent_name",
        "model": "deployment_name",
        "measures": ["prompt_tokens", "completion_tokens", "total_tokens", "latency_ms"],
    },
    {
        "glob": "bedrock/traces/dt=*/*.parquet",
        "cloud": "aws",
        "agent_id": ("gen_ai_agent_id", "agent_endpoint_id"),
        "agent_name": None,
        "model": None,  # AgentCore traces carry no model_id -- the AWS gap
        "measures": ["token_count", "latency_ms"],
    },
]

# model-grain metrics -> consumption with NO agent (sentinel).
METRIC_SOURCES = [
    {"glob": "bedrock/metrics/dt=*/*.parquet", "cloud": "aws",
     "model": "model_id", "namespace": "namespace"},
    {"glob": "foundry/metrics/dt=*/*.parquet", "cloud": "azure",
     "model": "model_deployment_name", "namespace": None},
]

SCHEMA = """
CREATE TABLE dim_agent (
    agent_key       INTEGER PRIMARY KEY,
    cloud           TEXT NOT NULL,
    native_agent_id TEXT NOT NULL,
    agent_name      TEXT,
    agent_type      TEXT,
    description     TEXT,
    instructions    TEXT,
    config_hash     TEXT,
    status          TEXT,
    enabled         INTEGER,
    publisher       TEXT,
    owner_id        TEXT,
    created         TEXT,
    last_modified   TEXT,
    UNIQUE (cloud, native_agent_id)
);
CREATE TABLE dim_skill (
    skill_key  INTEGER PRIMARY KEY,
    skill_name TEXT NOT NULL,
    UNIQUE (skill_name)
);
CREATE TABLE bridge_agent_skill (
    agent_key INTEGER NOT NULL REFERENCES dim_agent (agent_key),
    skill_key INTEGER NOT NULL REFERENCES dim_skill (skill_key),
    PRIMARY KEY (agent_key, skill_key)
);
CREATE TABLE dim_connector (
    connector_key  INTEGER PRIMARY KEY,
    connector_name TEXT NOT NULL,
    UNIQUE (connector_name)
);
CREATE TABLE bridge_agent_connector (
    agent_key     INTEGER NOT NULL REFERENCES dim_agent (agent_key),
    connector_key INTEGER NOT NULL REFERENCES dim_connector (connector_key),
    PRIMARY KEY (agent_key, connector_key)
);
CREATE TABLE dim_model (
    model_key INTEGER PRIMARY KEY,
    cloud     TEXT NOT NULL,
    native_id TEXT NOT NULL,
    UNIQUE (cloud, native_id)
);
CREATE TABLE dim_metric (
    metric_key  INTEGER PRIMARY KEY,
    cloud       TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    namespace   TEXT NOT NULL,
    UNIQUE (cloud, metric_name, namespace)
);
CREATE TABLE dim_date (
    date_key  INTEGER PRIMARY KEY,
    full_date TEXT NOT NULL,
    year INTEGER NOT NULL, month INTEGER NOT NULL, day INTEGER NOT NULL
);
CREATE TABLE fact_agent_consumption (
    agent_key  INTEGER NOT NULL REFERENCES dim_agent (agent_key),
    metric_key INTEGER NOT NULL REFERENCES dim_metric (metric_key),
    model_key  INTEGER NOT NULL REFERENCES dim_model (model_key),
    date_key   INTEGER NOT NULL REFERENCES dim_date (date_key),
    ts         TEXT NOT NULL,
    value      REAL,
    attributed INTEGER NOT NULL,
    PRIMARY KEY (agent_key, metric_key, model_key, ts)
);
CREATE TABLE fact_agent_snapshot (
    agent_key   INTEGER NOT NULL REFERENCES dim_agent (agent_key),
    metric_key  INTEGER NOT NULL REFERENCES dim_metric (metric_key),
    date_key    INTEGER NOT NULL REFERENCES dim_date (date_key),
    value_sum   REAL,
    value_count INTEGER,
    PRIMARY KEY (agent_key, metric_key, date_key)
);
"""

_DOTNET_DATE = re.compile(r"/Date\((-?\d+)")


def _rows(raw_root: str, pattern: str) -> Iterator[dict[str, Any]]:
    for path in sorted(glob.glob(os.path.join(raw_root, pattern))):
        for row in pq.read_table(path).to_pylist():
            yield row


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _hash(text: str | None) -> str | None:
    if not text:
        return None
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def _dotnet_date(value: Any) -> str | None:
    """Graph returns copilotPackage timestamps as .NET ``/Date(ms)/`` strings."""
    if value is None:
        return None
    match = _DOTNET_DATE.search(str(value))
    if not match:
        return _text(value)
    ms = int(match.group(1))
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _parse_definition(pkg: dict) -> tuple[str | None, list[str], list[str]]:
    """Pull (instructions, capabilities, actions) out of a copilotPackage DETAIL
    object. The real config lives in elementDetails[].elements[].definition, which
    is a JSON STRING that must be parsed."""
    instructions: str | None = None
    capabilities: list[str] = []
    actions: list[str] = []
    for element_detail in pkg.get("elementDetails") or []:
        for element in element_detail.get("elements") or []:
            raw = element.get("definition")
            if not raw:
                continue
            try:
                definition = json.loads(raw)
            except (ValueError, TypeError):
                continue
            instructions = instructions or definition.get("instructions")
            for cap in definition.get("capabilities") or []:
                name = cap.get("name") if isinstance(cap, dict) else str(cap)
                if name:
                    capabilities.append(name)
            for act in definition.get("actions") or []:
                name = (act.get("name") or act.get("id")) if isinstance(act, dict) else str(act)
                if name:
                    actions.append(name)
    return instructions, capabilities, actions


class StarBuilder:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._agents: dict[tuple[str, str], int] = {}
        self._skills: dict[str, int] = {}
        self._connectors: dict[str, int] = {}
        self._models: dict[tuple[str, str], int] = {}
        self._metrics: dict[tuple[str, str, str], int] = {}
        self._dates: set[int] = set()

    def init_schema(self) -> None:
        for table in (
            "fact_agent_consumption", "fact_agent_snapshot",
            "bridge_agent_skill", "bridge_agent_connector",
            "dim_agent", "dim_skill", "dim_connector",
            "dim_model", "dim_metric", "dim_date",
        ):
            self.conn.execute(f"DROP TABLE IF EXISTS {table}")
        self.conn.executescript(SCHEMA)

    # --- dimension get-or-create ------------------------------------------
    def agent_key(self, cloud: str, native_id: str, **attrs: Any) -> int:
        key = (cloud, native_id)
        if key not in self._agents:
            config_hash = _hash(attrs.get("hash_source") or attrs.get("description"))
            cur = self.conn.execute(
                "INSERT INTO dim_agent (cloud, native_agent_id, agent_name, "
                "agent_type, description, instructions, config_hash, status, "
                "enabled, publisher, owner_id, created, last_modified) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    cloud, native_id, attrs.get("agent_name"), attrs.get("agent_type"),
                    attrs.get("description"), attrs.get("instructions"), config_hash,
                    attrs.get("status"), attrs.get("enabled"), attrs.get("publisher"),
                    attrs.get("owner_id"), attrs.get("created"), attrs.get("last_modified"),
                ),
            )
            self._agents[key] = int(cur.lastrowid)
        return self._agents[key]

    def skill_key(self, name: str) -> int:
        if name not in self._skills:
            cur = self.conn.execute(
                "INSERT INTO dim_skill (skill_name) VALUES (?)", (name,)
            )
            self._skills[name] = int(cur.lastrowid)
        return self._skills[name]

    def connector_key(self, name: str) -> int:
        if name not in self._connectors:
            cur = self.conn.execute(
                "INSERT INTO dim_connector (connector_name) VALUES (?)", (name,)
            )
            self._connectors[name] = int(cur.lastrowid)
        return self._connectors[name]

    def model_key(self, cloud: str, native_id: str) -> int:
        key = (cloud, native_id)
        if key not in self._models:
            cur = self.conn.execute(
                "INSERT INTO dim_model (cloud, native_id) VALUES (?, ?)", key
            )
            self._models[key] = int(cur.lastrowid)
        return self._models[key]

    def metric_key(self, cloud: str, metric_name: str, namespace: str) -> int:
        key = (cloud, metric_name, namespace)
        if key not in self._metrics:
            cur = self.conn.execute(
                "INSERT INTO dim_metric (cloud, metric_name, namespace) VALUES (?,?,?)",
                key,
            )
            self._metrics[key] = int(cur.lastrowid)
        return self._metrics[key]

    def date_key(self, ts: str) -> int:
        iso_day = ts[:10]
        date_key = int(iso_day.replace("-", ""))
        if date_key not in self._dates:
            y, m, d = (int(p) for p in iso_day.split("-"))
            self.conn.execute(
                "INSERT INTO dim_date (date_key, full_date, year, month, day) "
                "VALUES (?,?,?,?,?)", (date_key, iso_day, y, m, d),
            )
            self._dates.add(date_key)
        return date_key

    # --- loaders -----------------------------------------------------------
    def load_registry(self, agents_json: str | None) -> int:
        """Load M365 agents from a JSON export of copilotPackage detail objects.
        Accepts a bare list, a single object, or a Graph {"value": [...]} envelope."""
        if not agents_json or not os.path.exists(agents_json):
            return 0
        with open(agents_json, encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            packages = data.get("value", [data])
        else:
            packages = data
        agents = 0
        for pkg in packages or []:
            native_id = _text(pkg.get("id"))
            if native_id is None:
                continue
            instructions, capabilities, actions = _parse_definition(pkg)
            description = _text(pkg.get("longDescription")) or _text(pkg.get("shortDescription"))
            is_blocked = pkg.get("isBlocked")
            agent_key = self.agent_key(
                "m365", native_id,
                agent_name=_text(pkg.get("displayName")),
                agent_type=_text(pkg.get("platform")),
                description=description,
                instructions=instructions,
                hash_source=instructions or description,
                status=_text(pkg.get("availableTo")) or _text(pkg.get("deployedTo")),
                enabled=(0 if is_blocked else 1) if is_blocked is not None else None,
                publisher=_text(pkg.get("publisher")),
                owner_id=_text(pkg.get("ownerId")),
                created=_dotnet_date(pkg.get("createdDateTime")),
                last_modified=_dotnet_date(pkg.get("lastModifiedDateTime")),
            )
            for cap in dict.fromkeys(capabilities):
                self.conn.execute(
                    "INSERT OR IGNORE INTO bridge_agent_skill VALUES (?, ?)",
                    (agent_key, self.skill_key(cap)),
                )
            for act in dict.fromkeys(actions):
                self.conn.execute(
                    "INSERT OR IGNORE INTO bridge_agent_connector VALUES (?, ?)",
                    (agent_key, self.connector_key(act)),
                )
            agents += 1
        return agents

    def _consume(
        self, agent_key: int, cloud: str, metric_name: str, namespace: str,
        model_native: str, ts: str, value: Any, attributed: int,
    ) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO fact_agent_consumption "
            "(agent_key, metric_key, model_key, date_key, ts, value, attributed) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                agent_key, self.metric_key(cloud, metric_name, namespace),
                self.model_key(cloud, model_native), self.date_key(ts),
                ts, value, attributed,
            ),
        )

    def load_traces(self, raw_root: str) -> int:
        facts = 0
        for source in TRACE_SOURCES:
            cloud = source["cloud"]
            for row in _rows(raw_root, source["glob"]):
                ts = _text(row.get("timestamp"))
                if ts is None:
                    continue
                native_id = next(
                    (_text(row.get(f)) for f in source["agent_id"] if _text(row.get(f))),
                    None,
                ) or UNATTRIBUTED
                agent_key = self.agent_key(
                    cloud, native_id,
                    agent_name=_text(row.get(source["agent_name"]))
                    if source["agent_name"] else None,
                )
                model_native = (
                    _text(row.get(source["model"])) if source["model"] else None
                ) or UNKNOWN_MODEL
                for measure in source["measures"]:
                    value = row.get(measure)
                    if value is None:
                        continue
                    self._consume(
                        agent_key, cloud, measure, "gen_ai",
                        model_native, ts, value, attributed=1,
                    )
                    facts += 1
        return facts

    def load_metrics(self, raw_root: str) -> int:
        facts = 0
        for source in METRIC_SOURCES:
            cloud = source["cloud"]
            sentinel = self.agent_key(cloud, UNATTRIBUTED, agent_name=None)
            for row in _rows(raw_root, source["glob"]):
                metric_name = _text(row.get("metric_name"))
                ts = _text(row.get("timestamp"))
                if metric_name is None or ts is None:
                    continue
                namespace = (
                    _text(row.get(source["namespace"])) if source["namespace"] else None
                ) or ""
                model_native = _text(row.get(source["model"])) or UNKNOWN_MODEL
                self._consume(
                    sentinel, cloud, metric_name, namespace,
                    model_native, ts, row.get("value"), attributed=0,
                )
                facts += 1
        return facts

    def build_snapshot(self) -> int:
        self.conn.execute(
            "INSERT INTO fact_agent_snapshot "
            "(agent_key, metric_key, date_key, value_sum, value_count) "
            "SELECT agent_key, metric_key, date_key, SUM(value), COUNT(*) "
            "FROM fact_agent_consumption GROUP BY agent_key, metric_key, date_key"
        )
        (n,) = self.conn.execute("SELECT COUNT(*) FROM fact_agent_snapshot").fetchone()
        return int(n)


def validate(conn: sqlite3.Connection) -> list[str]:
    problems: list[str] = []
    fact_fks = {
        "agent": "agent_key NOT IN (SELECT agent_key FROM dim_agent)",
        "metric": "metric_key NOT IN (SELECT metric_key FROM dim_metric)",
        "model": "model_key NOT IN (SELECT model_key FROM dim_model)",
        "date": "date_key NOT IN (SELECT date_key FROM dim_date)",
    }
    for name, predicate in fact_fks.items():
        (count,) = conn.execute(
            f"SELECT COUNT(*) FROM fact_agent_consumption WHERE {predicate}"
        ).fetchone()
        if count:
            problems.append(f"{count} consumption rows with orphan {name}_key")
    for bridge, dim, fk in (
        ("bridge_agent_skill", "dim_skill", "skill_key"),
        ("bridge_agent_connector", "dim_connector", "connector_key"),
    ):
        (count,) = conn.execute(
            f"SELECT COUNT(*) FROM {bridge} WHERE agent_key NOT IN "
            f"(SELECT agent_key FROM dim_agent) OR {fk} NOT IN "
            f"(SELECT {fk} FROM {dim})"
        ).fetchone()
        if count:
            problems.append(f"{count} orphan rows in {bridge}")
    return problems


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = [
        "dim_agent", "dim_skill", "dim_connector", "dim_model", "dim_metric",
        "dim_date", "bridge_agent_skill", "bridge_agent_connector",
        "fact_agent_consumption", "fact_agent_snapshot",
    ]
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_star")
    parser.add_argument("--raw-root", default="_local_raw")
    parser.add_argument("--agents-json", default="star/m365_agents.json",
                        help="JSON export of copilotPackage detail objects")
    parser.add_argument("--db", default="star/agentlens_star.db")
    args = parser.parse_args(argv)

    os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
    conn = sqlite3.connect(args.db)
    try:
        b = StarBuilder(conn)
        b.init_schema()
        agents = b.load_registry(args.agents_json)
        attributed = b.load_traces(args.raw_root)
        unattributed = b.load_metrics(args.raw_root)
        snapshots = b.build_snapshot()
        conn.commit()
        problems = validate(conn)
        counts = _counts(conn)
    finally:
        conn.close()

    print(f"[star] built {args.db}")
    print(f"  M365 agents: {agents} | consumption: "
          f"{attributed} attributed + {unattributed} unattributed | "
          f"snapshot rows: {snapshots}")
    for table, count in counts.items():
        print(f"  {table:<24} {count}")
    if problems:
        print("[star] VALIDATION FAILED:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("[star] validation OK -- 0 orphans")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
