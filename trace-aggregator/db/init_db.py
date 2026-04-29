"""
Database initialization. Idempotent — run as many times as you like.

Usage:
    python -m db.init_db
"""
import sys
import time

import clickhouse_connect
from clickhouse_connect.driver.exceptions import OperationalError


CLICKHOUSE_HOST = "localhost"
CLICKHOUSE_PORT = 8123
CONNECT_RETRIES = 20
RETRY_DELAY_SEC = 1.5


def get_client(retries: int = CONNECT_RETRIES):
    """ClickHouse takes a few seconds to warm up after `docker compose up`."""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            client = clickhouse_connect.get_client(
                host=CLICKHOUSE_HOST,
                port=CLICKHOUSE_PORT,
                username="default",
                password="",
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
ORDER BY (trace_id, start_time_ms, span_id)
"""

SCHEMA_RECONSTRUCTED_TRACES = """
CREATE TABLE IF NOT EXISTS tracing.reconstructed_traces (
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

    -- The user prompt / task description that initiated this trace
    input_text      String DEFAULT ''
) ENGINE = ReplacingMergeTree(reconstructed_at)
ORDER BY trace_id
"""

SCHEMA_RAW_DECISIONS = """
CREATE TABLE IF NOT EXISTS tracing.raw_decisions (
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
ORDER BY (trace_id, timestamp_ms, decision_id)
"""

SCHEMA_DECISION_EDGES = """
CREATE TABLE IF NOT EXISTS tracing.decision_edges (
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
ORDER BY (trace_id, decision_id, target_span_id)
"""

SCHEMA_DECISION_REASON_CHAINS = """
CREATE TABLE IF NOT EXISTS tracing.decision_reason_chains (
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
ORDER BY (trace_id, decision_id, chain_rank, target_span_id)
"""


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

    print("→ Ensuring input_text column on reconstructed_traces...")
    try:
        client.command("ALTER TABLE tracing.reconstructed_traces ADD COLUMN IF NOT EXISTS input_text String DEFAULT ''")
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
