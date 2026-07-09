"""``build_star_pg.py`` -- AgentLens star loader for PostgreSQL.

Postgres counterpart of ``star/build_star.py`` (SQLite prototype). Same
agent-centric star (see AI_Observability session 2026-07-06): ``dim_agent`` is
the conformed spine, cloud-agnostic, and every fact hangs off it. Same three
sources (M365 detail JSON, cloud traces parquets, cloud metrics parquets),
same two consumption grains (transaction ``fact_agent_consumption`` +
daily-rollup ``fact_agent_snapshot``).

Differences vs. the SQLite loader (per Stock/01 - System/postgres-access.md §4):
  * ``psycopg2`` connection to ``claude_db`` / schema ``agentlens``.
  * Placeholders are ``%s``; bulk inserts use ``psycopg2.extras.execute_values``.
  * Upserts use ``INSERT ... ON CONFLICT ... DO UPDATE`` (idempotent).
  * Dim keys come back via ``RETURNING`` on the upsert.
  * ``--rebuild`` does ``TRUNCATE ... RESTART IDENTITY CASCADE`` before load.
  * ``--apply-schema`` runs ``agentlens_schema_pg.sql`` first (bootstrap).

Env vars (see ``.env`` block ``AGENTLENS_PG_*``):
  AGENTLENS_PG_HOST     default localhost (Windows) / host.docker.internal (Cowork)
  AGENTLENS_PG_PORT     default 5432
  AGENTLENS_PG_DBNAME   default claude_db
  AGENTLENS_PG_USER     default postgres
  AGENTLENS_PG_PASSWORD required
  AGENTLENS_PG_SCHEMA   default agentlens

Usage:
  uv run python star/build_star_pg.py --apply-schema  # first run only
  uv run python star/build_star_pg.py --rebuild       # full rebuild
  uv run python star/build_star_pg.py                 # incremental upsert
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

import pyarrow.parquet as pq
import psycopg2
import psycopg2.extras

UNATTRIBUTED = "(unattributed)"
UNKNOWN_MODEL = "(unknown)"

_SCHEMA_SQL_PATH = os.path.join(os.path.dirname(__file__), "agentlens_schema_pg.sql")

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

# FOCUS-lite cost parquets -> fact_resource_cost (resource grain; agent_key
# stays NULL until a resource->agent mapping exists). Currency arrives as a
# schema-drift field, so it is recovered from the _drift JSON column.
COST_SOURCES = [
    {"glob": "foundry/cost/dt=*/*.parquet", "cloud": "azure"},
    {"glob": "bedrock/cost/dt=*/*.parquet", "cloud": "aws"},
]

_ALL_TABLES = (
    "etl_load_log",
    "fact_resource_cost",
    "fact_agent_snapshot",
    "fact_agent_consumption",
    "bridge_agent_skill",
    "bridge_agent_connector",
    "dim_agent",
    "dim_skill",
    "dim_connector",
    "dim_model",
    "dim_metric",
    "dim_date",
)

_DOTNET_DATE = re.compile(r"/Date\((-?\d+)")


# ============================================================
# Helpers
# ============================================================

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
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()
    except (OSError, ValueError, OverflowError):
        return None


_PP_CONNECTORS = (
    "Excel Online (Business)", "Office 365 Outlook", "Microsoft Dataverse",
    "Microsoft Teams", "OneDrive for Business", "Azure DevOps",
    "Generative actions", "Human review", "MSN Weather", "SharePoint",
    "Redmine", "Jira", "Mail",
)


def _connector_service(name: str | None) -> str | None:
    """Map a Copilot Studio ``api_action`` tool name to its connector/service,
    e.g. 'Office 365 Outlook Send an email (V2)' -> 'Office 365 Outlook'.
    Unknown connectors fall back to the full name."""
    if not name:
        return None
    for service in _PP_CONNECTORS:
        if name == service or name.startswith(service + " "):
            return service
    return name.strip() or None


def _parse_definition(pkg: dict) -> tuple[str | None, list[str], list[str]]:
    """Pull (instructions, capabilities, actions) out of a copilotPackage DETAIL
    object. The real config lives in elementDetails[].elements[].definition,
    which is a JSON STRING that must be parsed."""
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
            # Copilot Studio (AgentMetadatas) stores the prompt under PascalCase
            # "Instructions"; declarative agents use lowercase "instructions".
            instructions = (
                instructions
                or definition.get("instructions")
                or definition.get("Instructions")
            )
            for cap in definition.get("capabilities") or []:
                name = cap.get("name") if isinstance(cap, dict) else str(cap)
                if name:
                    capabilities.append(name)
            for act in definition.get("actions") or []:
                name = (act.get("name") or act.get("id")) if isinstance(act, dict) else str(act)
                if name:
                    actions.append(name)
            # Copilot Studio (AgentMetadatas) fuses skills + connectors into a
            # single ``Tools`` array (declarative agents use capabilities/actions):
            # tool_type=capability -> skill (capability_type); api_action -> connector.
            for tool in definition.get("Tools") or []:
                if not isinstance(tool, dict):
                    continue
                meta = tool.get("metadata") or {}
                tool_type = meta.get("tool_type")
                if tool_type == "capability":
                    skill = meta.get("capability_type") or tool.get("name")
                    if skill:
                        capabilities.append(skill)
                elif tool_type == "api_action":
                    service = _connector_service(tool.get("name"))
                    if service:
                        actions.append(service)
    return instructions, capabilities, actions


# ============================================================
# Connection
# ============================================================

def _connect(schema: str) -> psycopg2.extensions.connection:
    conn = psycopg2.connect(
        host=os.environ.get("AGENTLENS_PG_HOST", "localhost"),
        port=int(os.environ.get("AGENTLENS_PG_PORT", "5432")),
        dbname=os.environ.get("AGENTLENS_PG_DBNAME", "claude_db"),
        user=os.environ.get("AGENTLENS_PG_USER", "postgres"),
        password=os.environ["AGENTLENS_PG_PASSWORD"],
    )
    conn.autocommit = False
    with conn.cursor() as cur:
        cur.execute(f"SET search_path TO {schema}")
    return conn


def _apply_schema(conn: psycopg2.extensions.connection) -> None:
    with open(_SCHEMA_SQL_PATH, encoding="utf-8") as handle:
        ddl = handle.read()
    with conn.cursor() as cur:
        cur.execute(ddl)
    conn.commit()


def _rebuild(conn: psycopg2.extensions.connection, schema: str) -> None:
    with conn.cursor() as cur:
        existing = []
        for table in _ALL_TABLES:  # tolerate legacy schemas missing new tables
            cur.execute("SELECT to_regclass(%s)", (f"{schema}.{table}",))
            if cur.fetchone()[0] is not None:
                existing.append(f"{schema}.{table}")
        cur.execute(f"TRUNCATE {', '.join(existing)} RESTART IDENTITY CASCADE")
    conn.commit()


# ============================================================
# Star builder
# ============================================================

class StarBuilder:
    def __init__(
        self,
        conn: psycopg2.extensions.connection,
        schema: str,
        *,
        full_scan: bool = False,
    ) -> None:
        self.conn = conn
        self.schema = schema
        self.cur = conn.cursor()
        # File-level load watermark (etl_load_log): already-loaded parquets
        # are skipped, making incremental loads O(new) instead of O(history).
        # A legacy schema without the table degrades to full-scan behaviour.
        self.cur.execute("SELECT to_regclass(%s)", (f"{schema}.etl_load_log",))
        self._log_enabled = self.cur.fetchone()[0] is not None
        self._loaded_files: set[str] = set()
        if self._log_enabled and not full_scan:
            self.cur.execute(f"SELECT file_path FROM {schema}.etl_load_log")
            self._loaded_files = {r[0] for r in self.cur.fetchall()}
        self._new_files: dict[str, int] = {}
        self._skipped_files = 0
        # local caches: natural key -> generated key
        self._agents: dict[tuple[str, str], int] = {}
        self._skills: dict[str, int] = {}
        self._connectors: dict[str, int] = {}
        self._models: dict[tuple[str, str], int] = {}
        self._metrics: dict[tuple[str, str, str], int] = {}
        self._dates: set[int] = set()
        # fact rows staged for bulk upsert
        self._consumption: list[tuple[int, int, int, int, str, Any, int]] = []

    # ---- dimensions -----------------------------------------------------

    def agent_key(self, cloud: str, native_id: str, **attrs: Any) -> int:
        key = (cloud, native_id)
        if key in self._agents:
            return self._agents[key]
        config_hash = _hash(attrs.get("hash_source") or attrs.get("description"))
        self.cur.execute(
            f"""
            INSERT INTO {self.schema}.dim_agent (
                cloud, native_agent_id, agent_name, agent_type, description,
                instructions, config_hash, status, enabled, publisher,
                owner_id, created, last_modified
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (cloud, native_agent_id) DO UPDATE SET
                agent_name    = COALESCE(EXCLUDED.agent_name,    {self.schema}.dim_agent.agent_name),
                agent_type    = COALESCE(EXCLUDED.agent_type,    {self.schema}.dim_agent.agent_type),
                description   = COALESCE(EXCLUDED.description,   {self.schema}.dim_agent.description),
                instructions  = COALESCE(EXCLUDED.instructions,  {self.schema}.dim_agent.instructions),
                config_hash   = COALESCE(EXCLUDED.config_hash,   {self.schema}.dim_agent.config_hash),
                status        = COALESCE(EXCLUDED.status,        {self.schema}.dim_agent.status),
                enabled       = COALESCE(EXCLUDED.enabled,       {self.schema}.dim_agent.enabled),
                publisher     = COALESCE(EXCLUDED.publisher,     {self.schema}.dim_agent.publisher),
                owner_id      = COALESCE(EXCLUDED.owner_id,      {self.schema}.dim_agent.owner_id),
                created       = COALESCE(EXCLUDED.created,       {self.schema}.dim_agent.created),
                last_modified = COALESCE(EXCLUDED.last_modified, {self.schema}.dim_agent.last_modified)
            RETURNING agent_key
            """,
            (
                cloud, native_id,
                attrs.get("agent_name"), attrs.get("agent_type"),
                attrs.get("description"), attrs.get("instructions"),
                config_hash, attrs.get("status"), attrs.get("enabled"),
                attrs.get("publisher"), attrs.get("owner_id"),
                attrs.get("created"), attrs.get("last_modified"),
            ),
        )
        agent_key = int(self.cur.fetchone()[0])
        self._agents[key] = agent_key
        return agent_key

    def _simple_dim_key(
        self,
        table: str,
        cache: dict,
        cache_key: Any,
        columns: tuple[str, ...],
        values: tuple[Any, ...],
        conflict_cols: tuple[str, ...],
        return_col: str,
    ) -> int:
        """Get-or-create pattern for simple dims (skill/connector/model/metric).

        The DO UPDATE is a no-op that only exists so RETURNING fires on both
        insert and conflict paths.
        """
        if cache_key in cache:
            return cache[cache_key]
        col_list = ", ".join(columns)
        placeholders = ", ".join(["%s"] * len(values))
        conflict = ", ".join(conflict_cols)
        # No-op update on the first conflict column, purely to enable RETURNING
        noop = f"{conflict_cols[0]} = EXCLUDED.{conflict_cols[0]}"
        self.cur.execute(
            f"""
            INSERT INTO {self.schema}.{table} ({col_list}) VALUES ({placeholders})
            ON CONFLICT ({conflict}) DO UPDATE SET {noop}
            RETURNING {return_col}
            """,
            values,
        )
        key = int(self.cur.fetchone()[0])
        cache[cache_key] = key
        return key

    def skill_key(self, name: str) -> int:
        return self._simple_dim_key(
            "dim_skill", self._skills, name,
            ("skill_name",), (name,), ("skill_name",), "skill_key",
        )

    def connector_key(self, name: str) -> int:
        return self._simple_dim_key(
            "dim_connector", self._connectors, name,
            ("connector_name",), (name,), ("connector_name",), "connector_key",
        )

    def model_key(self, cloud: str, native_id: str) -> int:
        return self._simple_dim_key(
            "dim_model", self._models, (cloud, native_id),
            ("cloud", "native_id"), (cloud, native_id),
            ("cloud", "native_id"), "model_key",
        )

    def metric_key(self, cloud: str, metric_name: str, namespace: str) -> int:
        return self._simple_dim_key(
            "dim_metric", self._metrics, (cloud, metric_name, namespace),
            ("cloud", "metric_name", "namespace"),
            (cloud, metric_name, namespace),
            ("cloud", "metric_name", "namespace"), "metric_key",
        )

    def date_key(self, ts: str) -> int:
        iso_day = ts[:10]
        date_key = int(iso_day.replace("-", ""))
        if date_key in self._dates:
            return date_key
        y, m, d = (int(p) for p in iso_day.split("-"))
        self.cur.execute(
            f"""
            INSERT INTO {self.schema}.dim_date (date_key, full_date, year, month, day)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (date_key) DO NOTHING
            """,
            (date_key, iso_day, y, m, d),
        )
        self._dates.add(date_key)
        return date_key

    # ---- landing iterator -----------------------------------------------

    def iter_rows(self, raw_root: str, pattern: str) -> Iterator[dict[str, Any]]:
        """Rows from every parquet matching ``pattern``, skipping files already
        registered in ``etl_load_log`` (unless ``--full-scan``). Newly read
        files are recorded and flushed to the log inside the SAME transaction
        as the data, so a rollback keeps log and facts consistent."""
        for path in sorted(glob.glob(os.path.join(raw_root, pattern))):
            rel = os.path.relpath(path, raw_root).replace(os.sep, "/")
            if rel in self._loaded_files:
                self._skipped_files += 1
                continue
            count = 0
            for row in pq.read_table(path).to_pylist():
                count += 1
                yield row
            self._new_files[rel] = count

    # ---- loaders --------------------------------------------------------

    def load_registry(self, agents_json: str | None) -> int:
        """Load M365 agents from a JSON export of copilotPackage detail objects.
        Accepts a bare list, a single object, or a Graph {"value": [...]} envelope.
        """
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
                self.cur.execute(
                    f"INSERT INTO {self.schema}.bridge_agent_skill (agent_key, skill_key) "
                    f"VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (agent_key, self.skill_key(cap)),
                )
            for act in dict.fromkeys(actions):
                self.cur.execute(
                    f"INSERT INTO {self.schema}.bridge_agent_connector (agent_key, connector_key) "
                    f"VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (agent_key, self.connector_key(act)),
                )
            agents += 1
        return agents

    def _stage(
        self, agent_key: int, cloud: str, metric_name: str, namespace: str,
        model_native: str, ts: str, value: Any, attributed: int,
    ) -> None:
        self._consumption.append((
            agent_key,
            self.metric_key(cloud, metric_name, namespace),
            self.model_key(cloud, model_native),
            self.date_key(ts),
            ts,
            float(value) if isinstance(value, (int, float)) else value,
            attributed,
        ))

    def load_traces(self, raw_root: str) -> int:
        facts = 0
        for source in TRACE_SOURCES:
            cloud = source["cloud"]
            for row in self.iter_rows(raw_root, source["glob"]):
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
                    # attributed means "carries real agent identity", not
                    # "came from a trace": sentinel-routed spans stay 0.
                    self._stage(
                        agent_key, cloud, measure, "gen_ai",
                        model_native, ts, value,
                        attributed=1 if native_id != UNATTRIBUTED else 0,
                    )
                    facts += 1
        return facts

    def load_metrics(self, raw_root: str) -> int:
        facts = 0
        for source in METRIC_SOURCES:
            cloud = source["cloud"]
            sentinel = self.agent_key(cloud, UNATTRIBUTED, agent_name=None)
            for row in self.iter_rows(raw_root, source["glob"]):
                metric_name = _text(row.get("metric_name"))
                ts = _text(row.get("timestamp"))
                if metric_name is None or ts is None:
                    continue
                namespace = (
                    _text(row.get(source["namespace"])) if source["namespace"] else None
                ) or ""
                model_native = _text(row.get(source["model"])) or UNKNOWN_MODEL
                self._stage(
                    sentinel, cloud, metric_name, namespace,
                    model_native, ts, row.get("value"), attributed=0,
                )
                facts += 1
        return facts

    def load_cost(self, raw_root: str) -> int:
        """FOCUS-lite cost parquets -> ``fact_resource_cost`` (resource grain).

        Kept OUT of fact_agent_consumption on purpose: different grain
        (resource+meter+day vs agent+metric+model+ts) and different unit
        semantics (currency vs tokens/calls). agent_key stays NULL until a
        resource->agent mapping exists."""
        self.cur.execute(
            "SELECT to_regclass(%s)", (f"{self.schema}.fact_resource_cost",)
        )
        if self.cur.fetchone()[0] is None:
            print("[star:pg] WARN fact_resource_cost missing -- "
                  "run --apply-schema once; cost load skipped")
            return 0
        staged: list[tuple] = []
        for source in COST_SOURCES:
            cloud = source["cloud"]
            for row in self.iter_rows(raw_root, source["glob"]):
                resource_id = _text(row.get("resource_id"))
                day = _text(row.get("charge_period_start"))
                if resource_id is None or day is None:
                    continue
                currency = None
                drift = row.get("_drift")
                if drift:
                    try:
                        currency = _text(json.loads(drift).get("currency"))
                    except (ValueError, TypeError, AttributeError):
                        pass
                staged.append((
                    cloud, resource_id, self.date_key(day),
                    _text(row.get("meter_name")) or "",
                    row.get("billed_cost"), row.get("effective_cost"),
                    row.get("consumed_quantity"), _text(row.get("consumed_unit")),
                    _text(row.get("meter_category")), currency,
                    _text(row.get("subscription_id")),
                    _text(row.get("resource_group")),
                ))
        if not staged:
            return 0
        # MonthToDate captures overlap across daily runs: dedupe by PK within
        # the batch (last wins) to avoid "cannot affect row a second time".
        by_pk = {(r[0], r[1], r[2], r[3]): r for r in staged}
        rows = list(by_pk.values())
        psycopg2.extras.execute_values(
            self.cur,
            f"""
            INSERT INTO {self.schema}.fact_resource_cost
                (cloud, resource_id, date_key, meter_name, billed_cost,
                 effective_cost, consumed_quantity, consumed_unit,
                 meter_category, currency, subscription_id, resource_group)
            VALUES %s
            ON CONFLICT (cloud, resource_id, date_key, meter_name) DO UPDATE SET
                billed_cost       = EXCLUDED.billed_cost,
                effective_cost    = EXCLUDED.effective_cost,
                consumed_quantity = EXCLUDED.consumed_quantity,
                consumed_unit     = EXCLUDED.consumed_unit,
                meter_category    = EXCLUDED.meter_category,
                currency          = EXCLUDED.currency,
                subscription_id   = EXCLUDED.subscription_id,
                resource_group    = EXCLUDED.resource_group
            """,
            rows, page_size=1000,
        )
        return len(rows)

    def flush_load_log(self) -> tuple[int, int]:
        """Persist newly loaded file paths into etl_load_log (same transaction
        as the data). Returns (new_files, skipped_files)."""
        if self._new_files and self._log_enabled:
            psycopg2.extras.execute_values(
                self.cur,
                f"INSERT INTO {self.schema}.etl_load_log (file_path, row_count) "
                "VALUES %s ON CONFLICT (file_path) DO UPDATE SET "
                "loaded_at = now(), row_count = EXCLUDED.row_count",
                list(self._new_files.items()),
            )
        return len(self._new_files), self._skipped_files

    def flush_consumption(self) -> int:
        """Bulk-upsert the staged consumption rows via ``execute_values``."""
        if not self._consumption:
            return 0
        # Same-PK rows can coexist in one batch (daily captures whose 24h
        # windows overlap, or --full-scan): last wins, matching ON CONFLICT
        # semantics and avoiding "cannot affect row a second time".
        by_pk = {(r[0], r[1], r[2], r[4]): r for r in self._consumption}
        self._consumption = list(by_pk.values())
        sql = f"""
            INSERT INTO {self.schema}.fact_agent_consumption
                (agent_key, metric_key, model_key, date_key, ts, value, attributed)
            VALUES %s
            ON CONFLICT (agent_key, metric_key, model_key, ts) DO UPDATE SET
                value      = EXCLUDED.value,
                attributed = EXCLUDED.attributed,
                date_key   = EXCLUDED.date_key
        """
        psycopg2.extras.execute_values(self.cur, sql, self._consumption, page_size=1000)
        n = len(self._consumption)
        self._consumption.clear()
        return n

    def rebuild_snapshot(self) -> int:
        """Recompute ``fact_agent_snapshot`` from ``fact_agent_consumption``.
        Rebuild strategy (idempotent): truncate + INSERT ... SELECT ... GROUP BY."""
        self.cur.execute(f"TRUNCATE {self.schema}.fact_agent_snapshot")
        self.cur.execute(
            f"""
            INSERT INTO {self.schema}.fact_agent_snapshot
                (agent_key, metric_key, date_key, value_sum, value_count)
            SELECT agent_key, metric_key, date_key, SUM(value), COUNT(*)
            FROM {self.schema}.fact_agent_consumption
            GROUP BY agent_key, metric_key, date_key
            """
        )
        self.cur.execute(f"SELECT COUNT(*) FROM {self.schema}.fact_agent_snapshot")
        return int(self.cur.fetchone()[0])


# ============================================================
# Validation & reporting
# ============================================================

def validate(conn: psycopg2.extensions.connection, schema: str) -> list[str]:
    problems: list[str] = []
    checks = [
        (
            "consumption agent",
            f"SELECT COUNT(*) FROM {schema}.fact_agent_consumption f "
            f"LEFT JOIN {schema}.dim_agent d USING (agent_key) WHERE d.agent_key IS NULL",
        ),
        (
            "consumption metric",
            f"SELECT COUNT(*) FROM {schema}.fact_agent_consumption f "
            f"LEFT JOIN {schema}.dim_metric d USING (metric_key) WHERE d.metric_key IS NULL",
        ),
        (
            "consumption model",
            f"SELECT COUNT(*) FROM {schema}.fact_agent_consumption f "
            f"LEFT JOIN {schema}.dim_model d USING (model_key) WHERE d.model_key IS NULL",
        ),
        (
            "consumption date",
            f"SELECT COUNT(*) FROM {schema}.fact_agent_consumption f "
            f"LEFT JOIN {schema}.dim_date d USING (date_key) WHERE d.date_key IS NULL",
        ),
        (
            "bridge_agent_skill agent",
            f"SELECT COUNT(*) FROM {schema}.bridge_agent_skill b "
            f"LEFT JOIN {schema}.dim_agent a USING (agent_key) WHERE a.agent_key IS NULL",
        ),
        (
            "bridge_agent_skill skill",
            f"SELECT COUNT(*) FROM {schema}.bridge_agent_skill b "
            f"LEFT JOIN {schema}.dim_skill s USING (skill_key) WHERE s.skill_key IS NULL",
        ),
        (
            "bridge_agent_connector agent",
            f"SELECT COUNT(*) FROM {schema}.bridge_agent_connector b "
            f"LEFT JOIN {schema}.dim_agent a USING (agent_key) WHERE a.agent_key IS NULL",
        ),
        (
            "bridge_agent_connector connector",
            f"SELECT COUNT(*) FROM {schema}.bridge_agent_connector b "
            f"LEFT JOIN {schema}.dim_connector c USING (connector_key) WHERE c.connector_key IS NULL",
        ),
    ]
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (f"{schema}.fact_resource_cost",))
        if cur.fetchone()[0] is not None:
            checks.append((
                "resource_cost date",
                f"SELECT COUNT(*) FROM {schema}.fact_resource_cost f "
                f"LEFT JOIN {schema}.dim_date d USING (date_key) "
                "WHERE d.date_key IS NULL",
            ))
        for name, sql in checks:
            cur.execute(sql)
            (count,) = cur.fetchone()
            if count:
                problems.append(f"{count} orphan rows in {name}")
    return problems


def _counts(conn: psycopg2.extensions.connection, schema: str) -> dict[str, int]:
    out: dict[str, int] = {}
    with conn.cursor() as cur:
        # ordered for readability, dims first then facts
        for table in (
            "dim_agent", "dim_skill", "dim_connector", "dim_model", "dim_metric",
            "dim_date", "bridge_agent_skill", "bridge_agent_connector",
            "fact_agent_consumption", "fact_agent_snapshot",
            "fact_resource_cost", "etl_load_log",
        ):
            cur.execute("SELECT to_regclass(%s)", (f"{schema}.{table}",))
            if cur.fetchone()[0] is None:
                continue  # legacy schema without the 2026-07-08 additions
            cur.execute(f"SELECT COUNT(*) FROM {schema}.{table}")
            out[table] = int(cur.fetchone()[0])
    return out


# ============================================================
# Main
# ============================================================

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_star_pg")
    parser.add_argument("--raw-root", default="_local_raw",
                        help="local directory with the ADLS raw parquets")
    parser.add_argument("--agents-json", default="star/m365_agents.json",
                        help="JSON export of copilotPackage detail objects")
    parser.add_argument("--apply-schema", action="store_true",
                        help="apply agentlens_schema_pg.sql before loading")
    parser.add_argument("--rebuild", action="store_true",
                        help="TRUNCATE all agentlens tables before loading (destructive)")
    parser.add_argument("--full-scan", action="store_true",
                        help="ignore etl_load_log and re-read every parquet")
    parser.add_argument("--schema", default=os.environ.get("AGENTLENS_PG_SCHEMA", "agentlens"))
    args = parser.parse_args(argv)

    # simple .env loader (repo convention: uv run reads via extractors/run.py;
    # for standalone star runs, honour AGENTLENS_PG_* + minimal fallback)
    _load_env_file(".env")

    if "AGENTLENS_PG_PASSWORD" not in os.environ:
        parser.error("AGENTLENS_PG_PASSWORD is required (see .env AGENTLENS_PG_* block)")

    conn = _connect(args.schema)
    try:
        if args.apply_schema:
            _apply_schema(conn)
            print("[star:pg] applied schema DDL")
        if args.rebuild:
            _rebuild(conn, args.schema)
            print("[star:pg] rebuilt (TRUNCATE ... RESTART IDENTITY CASCADE)")

        b = StarBuilder(conn, args.schema, full_scan=args.full_scan)
        agents = b.load_registry(args.agents_json)
        attributed = b.load_traces(args.raw_root)
        unattributed = b.load_metrics(args.raw_root)
        cost_rows = b.load_cost(args.raw_root)
        facts_written = b.flush_consumption()
        snapshots = b.rebuild_snapshot()
        new_files, skipped_files = b.flush_load_log()
        conn.commit()

        problems = validate(conn, args.schema)
        counts = _counts(conn, args.schema)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(f"[star:pg] loaded into {args.schema}")
    print(f"  M365 agents:        {agents}")
    print(f"  consumption staged: {attributed} trace rows + {unattributed} metric rows")
    print(f"  consumption upsert: {facts_written} rows written to fact_agent_consumption")
    print(f"  snapshot rows:      {snapshots}")
    print(f"  cost rows:          {cost_rows} upserted into fact_resource_cost")
    print(f"  parquet files:      {new_files} new, {skipped_files} already loaded")
    for table, count in counts.items():
        print(f"  {table:<24} {count}")
    if problems:
        print("[star:pg] VALIDATION FAILED:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("[star:pg] validation OK -- 0 orphans")
    return 0


def _load_env_file(path: str) -> None:
    """Minimal .env loader (stdlib). Sets ``setdefault`` so shell wins over file
    (mirrors extractors/run.py convention)."""
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
