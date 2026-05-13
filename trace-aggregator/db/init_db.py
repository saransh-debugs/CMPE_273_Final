"""
Database initialization. Idempotent — run as many times as you like.

Usage:
    python -m db.init_db
"""
import sys
import time
import os

import clickhouse_connect
from clickhouse_connect.driver.exceptions import OperationalError

from shared.governance import retention_days


CLICKHOUSE_HOST = "localhost"
CLICKHOUSE_PORT = 8123
CONNECT_RETRIES = 20
RETRY_DELAY_SEC = 1.5
RETENTION_DAYS = retention_days()

CLICKHOUSE_HOST = os.environ.get("CLICKHOUSE_HOST", CLICKHOUSE_HOST)
CLICKHOUSE_PORT = int(os.environ.get("CLICKHOUSE_PORT", str(CLICKHOUSE_PORT)))


def _ttl_expr(expr: str, days: int) -> str:
    # ClickHouse 24.3 requires TTL expressions to evaluate to Date/DateTime.
    # Some timestamp columns here are DateTime64(3), so cast before applying TTL.
    return f"toDateTime({expr}) + INTERVAL {days} DAY"


def _ttl(expr: str, days: int) -> str:
    return f"TTL {_ttl_expr(expr, days)}"


def get_client(retries: int = CONNECT_RETRIES):
    """ClickHouse takes a few seconds to warm up after `docker compose up`."""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            client = clickhouse_connect.get_client(
                host=CLICKHOUSE_HOST,
                port=CLICKHOUSE_PORT,
                username=os.environ.get("CLICKHOUSE_USER", "default"),
                password=os.environ.get("CLICKHOUSE_PASSWORD", ""),
            )
            client.command("SELECT 1")
            return client
        except OperationalError as e:
            last_err = e
            print(f"  [{attempt}/{retries}] ClickHouse not ready, retrying...")
            time.sleep(RETRY_DELAY_SEC)
    raise RuntimeError(f"Could not reach ClickHouse: {last_err}")


SCHEMA_RAW_SPANS = """
CREATE TABLE IF NOT EXISTS tracing.raw_spans (
    tenant_id LowCardinality(String),
    -- Server-side ingestion timestamp (when the collector wrote the row)
    ingested_at DateTime64(3) DEFAULT now64(3),

    -- Agent-side wall clock (milliseconds since epoch)
    start_time_ms UInt64,

    trace_id      String,
    span_id       String,
    parent_span_id String,
    agent_id      String,

    -- Vector clock: agent_id -> logical counter
    vector_clock Map(String, UInt32),

    event_type    LowCardinality(String),
    input_tokens  UInt32,
    output_tokens UInt32,
    latency_ms    UInt32,

    metadata      String,
    idempotency_key String
) ENGINE = MergeTree()
PARTITION BY toYYYYMMDD(ingested_at)
ORDER BY (tenant_id, trace_id, start_time_ms, span_id)
__TTL__
""".replace("__TTL__", _ttl("ingested_at", RETENTION_DAYS["raw"]))

SCHEMA_RECONSTRUCTED_TRACES = """
CREATE TABLE IF NOT EXISTS tracing.reconstructed_traces (
    tenant_id       LowCardinality(String),
    trace_id        String,
    reconstructed_at DateTime64(3) DEFAULT now64(3),

    -- Number of spans the engine stitched into this trace
    span_count      UInt32,

    -- Computed totals across the trace
    total_latency_ms UInt32,
    total_input_tokens UInt32,
    total_output_tokens UInt32,
    error_count     UInt32,

    -- Full DAG as JSON: [{span_id, parent_span_id, children: [...], ...}]
    dag_json        String,

    -- Per-agent blame breakdown as JSON
    blame_json      String,

    blame_v2_json String DEFAULT '[]',
    -- The user prompt / task description that initiated this trace
    input_text      String DEFAULT '',

    -- Wall time of the first reconstruction insert for this trace (carried forward
    -- on ReplacingMergeTree updates so merges do not erase time-to-first-DAG).
    first_reconstructed_at DateTime64(3) DEFAULT toDateTime64(0, 3, 'UTC')
) ENGINE = ReplacingMergeTree(reconstructed_at)
ORDER BY (tenant_id, trace_id)
__TTL__
""".replace("__TTL__", _ttl("reconstructed_at", RETENTION_DAYS["reconstructed"]))

SCHEMA_RAW_DECISIONS = """
CREATE TABLE IF NOT EXISTS tracing.raw_decisions (
    tenant_id LowCardinality(String),
    ingested_at DateTime64(3) DEFAULT now64(3),
    timestamp_ms UInt64,

    trace_id String,
    decision_id String,
    source_span_id String,
    actor_agent_id String,

    decision_type LowCardinality(String),
    selected_candidate_id String,
    confidence Float64,
    rationale_summary String,

    evidence_refs Array(String),
    candidates_json String,
    metadata String,
    idempotency_key String
) ENGINE = MergeTree()
PARTITION BY toYYYYMMDD(ingested_at)
ORDER BY (tenant_id, trace_id, timestamp_ms, decision_id)
__TTL__
""".replace("__TTL__", _ttl("ingested_at", RETENTION_DAYS["raw"]))

SCHEMA_DECISION_EDGES = """
CREATE TABLE IF NOT EXISTS tracing.decision_edges (
    tenant_id String,
    trace_id String,
    decision_id String,
    source_span_id String,
    target_span_id String,

    decision_type LowCardinality(String),
    actor_agent_id String,
    selected_candidate_id String,
    confidence Float64,
    rationale_summary String,

    impact_latency_ms UInt32,
    impact_tokens UInt32,
    impact_error_count UInt32,

    reconstructed_at DateTime64(3) DEFAULT now64(3)
) ENGINE = ReplacingMergeTree(reconstructed_at)
ORDER BY (tenant_id, trace_id, decision_id, target_span_id)
__TTL__
""".replace("__TTL__", _ttl("reconstructed_at", RETENTION_DAYS["derived"]))

SCHEMA_SLO_STATUS = """
CREATE TABLE IF NOT EXISTS tracing.slo_status (
    evaluated_at DateTime64(3) DEFAULT now64(3),
    slo_name LowCardinality(String),
    title String,
    signal LowCardinality(String),
    window_minutes UInt32,
    threshold Float64,
    comparison LowCardinality(String),
    value Float64,
    passing UInt8,
    sample_count UInt64,
    notes String
) ENGINE = MergeTree()
PARTITION BY toYYYYMMDD(evaluated_at)
ORDER BY (slo_name, evaluated_at)
__TTL__
""".replace("__TTL__", _ttl("evaluated_at", RETENTION_DAYS["slo"]))

SCHEMA_INCIDENTS = """
CREATE TABLE IF NOT EXISTS tracing.incidents (
    incident_key     String,
    alert_type       LowCardinality(String),
    state            LowCardinality(String),
    severity         LowCardinality(String),
    message          String,
    details          String,
    opened_at        DateTime64(3),
    last_seen_at     DateTime64(3),
    acknowledged_at  DateTime64(3) DEFAULT toDateTime64(0, 3, 'UTC'),
    resolved_at      DateTime64(3) DEFAULT toDateTime64(0, 3, 'UTC'),
    occurrence_count UInt32,
    updated_at       DateTime64(3) DEFAULT now64(3)
) ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(opened_at)
ORDER BY incident_key
__TTL__
""".replace("__TTL__", _ttl("updated_at", RETENTION_DAYS["incidents"]))

SCHEMA_DECISION_REASON_CHAINS = """
CREATE TABLE IF NOT EXISTS tracing.decision_reason_chains (
    tenant_id String,
    trace_id String,
    decision_id String,
    source_span_id String,
    target_span_id String,
    chain_rank UInt16,

    actor_agent_id String,
    decision_type LowCardinality(String),
    selected_candidate_id String,
    confidence Float64,
    uncertainty LowCardinality(String),
    reason_summary String,

    impact_latency_ms UInt32,
    impact_tokens UInt32,
    impact_error_count UInt32,
    impact_score Float64,

    reconstructed_at DateTime64(3) DEFAULT now64(3)
) ENGINE = ReplacingMergeTree(reconstructed_at)
ORDER BY (tenant_id, trace_id, decision_id, chain_rank, target_span_id)
__TTL__
""".replace("__TTL__", _ttl("reconstructed_at", RETENTION_DAYS["derived"]))


def governance_migration_statements() -> list[str]:
    """Return idempotent ALTER statements for retention policy enforcement."""

    return [
        f"ALTER TABLE tracing.raw_spans MODIFY TTL {_ttl_expr('ingested_at', RETENTION_DAYS['raw'])}",
        f"ALTER TABLE tracing.raw_decisions MODIFY TTL {_ttl_expr('ingested_at', RETENTION_DAYS['raw'])}",
        f"ALTER TABLE tracing.reconstructed_traces MODIFY TTL {_ttl_expr('reconstructed_at', RETENTION_DAYS['reconstructed'])}",
        f"ALTER TABLE tracing.decision_edges MODIFY TTL {_ttl_expr('reconstructed_at', RETENTION_DAYS['derived'])}",
        f"ALTER TABLE tracing.decision_reason_chains MODIFY TTL {_ttl_expr('reconstructed_at', RETENTION_DAYS['derived'])}",
        f"ALTER TABLE tracing.slo_status MODIFY TTL {_ttl_expr('evaluated_at', RETENTION_DAYS['slo'])}",
        f"ALTER TABLE tracing.incidents MODIFY TTL {_ttl_expr('updated_at', RETENTION_DAYS['incidents'])}",
    ]


def apply_governance_policies(client) -> None:
    print("→ Applying retention TTL policies...")
    for stmt in governance_migration_statements():
        client.command(stmt)


def setup() -> None:
    print("→ Connecting to ClickHouse...")
    client = get_client()
    print("✓ Connected.")

    print("→ Creating database 'tracing'...")
    client.command("CREATE DATABASE IF NOT EXISTS tracing")

    print("→ Creating raw_spans table...")
    client.command(SCHEMA_RAW_SPANS)

    print("→ Creating reconstructed_traces table...")
    client.command(SCHEMA_RECONSTRUCTED_TRACES)

    print("→ Creating raw_decisions table...")
    client.command(SCHEMA_RAW_DECISIONS)

    print("→ Creating decision_edges table...")
    client.command(SCHEMA_DECISION_EDGES)

    print("→ Creating decision_reason_chains table...")
    client.command(SCHEMA_DECISION_REASON_CHAINS)

    print("→ Creating slo_status table...")
    client.command(SCHEMA_SLO_STATUS)

    print("→ Creating incidents table...")
    client.command(SCHEMA_INCIDENTS)

    apply_governance_policies(client)

    print("→ Ensuring input_text column on reconstructed_traces...")
    try:
        client.command("ALTER TABLE tracing.reconstructed_traces ADD COLUMN IF NOT EXISTS input_text String DEFAULT ''")
    except Exception:
        pass

    print("→ Ensuring tenant_id columns on trace tables...")
    for stmt in [
        "ALTER TABLE tracing.raw_spans ADD COLUMN IF NOT EXISTS tenant_id LowCardinality(String) DEFAULT 'default'",
        "ALTER TABLE tracing.raw_decisions ADD COLUMN IF NOT EXISTS tenant_id LowCardinality(String) DEFAULT 'default'",
        "ALTER TABLE tracing.reconstructed_traces ADD COLUMN IF NOT EXISTS tenant_id LowCardinality(String) DEFAULT 'default'",
        "ALTER TABLE tracing.decision_edges ADD COLUMN IF NOT EXISTS tenant_id LowCardinality(String) DEFAULT 'default'",
        "ALTER TABLE tracing.decision_reason_chains ADD COLUMN IF NOT EXISTS tenant_id LowCardinality(String) DEFAULT 'default'",
    ]:
        try:
            client.command(stmt)
        except Exception:
            pass

    print("→ Ensuring first_reconstructed_at on reconstructed_traces...")
    try:
        client.command(
            "ALTER TABLE tracing.reconstructed_traces "
            "ADD COLUMN IF NOT EXISTS first_reconstructed_at DateTime64(3) DEFAULT toDateTime64(0, 3, 'UTC')"
        )
    except Exception:
        pass

    print("→ Ensuring blame_v2_json column on reconstructed_traces...")
    try:
        client.command(
            "ALTER TABLE tracing.reconstructed_traces "
            "ADD COLUMN IF NOT EXISTS blame_v2_json String DEFAULT '[]'"
        )
    except Exception:
        pass
    print("→ Ensuring idempotency_key columns on raw tables...")
    try:
        client.command("ALTER TABLE tracing.raw_spans ADD COLUMN IF NOT EXISTS idempotency_key String DEFAULT ''")
    except Exception:
        pass
    try:
        client.command("ALTER TABLE tracing.raw_decisions ADD COLUMN IF NOT EXISTS idempotency_key String DEFAULT ''")
    except Exception:
        pass

    print("\n✅ Schema ready. Tables in `tracing`:")
    rows = client.query("SHOW TABLES FROM tracing").result_rows
    for (t,) in rows:
        print(f"   - {t}")


if __name__ == "__main__":
    try:
        setup()
    except Exception as e:
        print(f"\n❌ Setup failed: {e}", file=sys.stderr)
        print("   Is the ClickHouse container running? `docker compose up -d`", file=sys.stderr)
        sys.exit(1)