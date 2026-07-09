-- agentlens_star_schema.sql
-- Canonical agent-centric star schema for AgentLens (dim_agent as the spine).
-- Mirrors the SCHEMA constant and init_schema() in star/build_star.py.
-- Full rebuild: DROP TABLE IF EXISTS (FK-safe order) followed by CREATE TABLE.
-- Apply with agl_execute_script against agentlens.db.
-- Source: AI_Observability session 2026-07-06.

DROP TABLE IF EXISTS fact_agent_consumption;
DROP TABLE IF EXISTS fact_agent_snapshot;
DROP TABLE IF EXISTS bridge_agent_skill;
DROP TABLE IF EXISTS bridge_agent_connector;
DROP TABLE IF EXISTS dim_agent;
DROP TABLE IF EXISTS dim_skill;
DROP TABLE IF EXISTS dim_connector;
DROP TABLE IF EXISTS dim_model;
DROP TABLE IF EXISTS dim_metric;
DROP TABLE IF EXISTS dim_date;

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
