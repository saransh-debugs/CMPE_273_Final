"""
Query API. Reads from ClickHouse and serves JSON to the UI.

Run:
    uvicorn api.main:app --reload --port 8000
"""
from __future__ import annotations

import asyncio  
import json
import time  
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

import clickhouse_connect
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from api.cursor import TraceCursor, maybe_decode
from sse_starlette.sse import EventSourceResponse   
from shared.trace_auth import AuthError, resolve_request_tenant

app = FastAPI(title="Trace Aggregator API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:4173"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _client():
    return clickhouse_connect.get_client(
        host="localhost", port=8123, username="default", password=""
    )


def _iso_utc(dt) -> str:
    if dt is None:
        return ""
    if getattr(dt, "tzinfo", None) is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _require_tenant(request: Request) -> str:
    try:
        return resolve_request_tenant(request.headers)
    except AuthError as e:
        raise HTTPException(401, str(e))


def _tenant_clause(tenant_id: str, clauses: list[str], params: dict) -> None:
    clauses.append("tenant_id = {tenant_id:String}")
    params["tenant_id"] = tenant_id


@app.get("/health")
def health():
    try:
        _client().command("SELECT 1")
        return {"ok": True}
    except Exception as e:
        raise HTTPException(503, f"ClickHouse unreachable: {e}")


@app.get("/traces")
def list_traces(
    # ────────────────────────────────────────────────────────────────────
    # Existing params (backward compatibility)
    # ────────────────────────────────────────────────────────────────────
    limit: int = Query(50, ge=1, le=500),
    has_errors: Optional[bool] = None,

    # ────────────────────────────────────────────────────────────────────
    # ENG-08: Cursor pagination (preferred)
    # ENG-07 offset stays as a deprecated fallback for back-compat
    # ────────────────────────────────────────────────────────────────────
    cursor: Optional[str] = Query(None, description="Opaque pagination cursor from previous response"),
    offset: int = Query(0, ge=0, description="Deprecated — prefer `cursor` for stable pagination"),

    # ────────────────────────────────────────────────────────────────────
    # ENG-07 filters (unchanged)
    # ────────────────────────────────────────────────────────────────────
    hours: Optional[int] = Query(None, ge=1, le=720),
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    agent_id: Optional[str] = Query(None, min_length=1, max_length=128),
    event_type: Optional[str] = Query(None, min_length=1, max_length=64),
    min_tokens: Optional[int] = Query(None, ge=0),
    max_tokens: Optional[int] = Query(None, ge=0),
    min_latency_ms: Optional[int] = Query(None, ge=0),
    max_latency_ms: Optional[int] = Query(None, ge=0),
    metadata_key: Optional[str] = Query(None, min_length=1, max_length=128),
    metadata_value: Optional[str] = Query(None, max_length=1024),
    tenant_id: str = Depends(_require_tenant),
):
    """List recent trace reconstructions with cursor pagination.

    Pagination
    ----------
    Cursor mode (preferred):
        Page 1:  GET /traces?limit=50
        Page 2:  GET /traces?limit=50&cursor=<next_cursor from page 1>
        Last page: response includes `next_cursor: null`.

    Offset mode (deprecated, kept for back-compat):
        GET /traces?limit=50&offset=100
        Stable only for the first few pages; slow at depth.

    Response shape:
        {
          "items":       [ ... traces ... ],
          "next_cursor": "<token>" | null,
          "has_more":    true | false,
          "limit":       50
        }
    """

    # ────────────────────────────────────────────────────────────────────
    # Validation (same as ENG-07)
    # ────────────────────────────────────────────────────────────────────
    if cursor and offset > 0:
        raise HTTPException(400, "Provide either `cursor` OR `offset`, not both")
    if min_tokens is not None and max_tokens is not None and min_tokens > max_tokens:
        raise HTTPException(400, "min_tokens must be <= max_tokens")
    if min_latency_ms is not None and max_latency_ms is not None and min_latency_ms > max_latency_ms:
        raise HTTPException(400, "min_latency_ms must be <= max_latency_ms")
    if start_time and end_time and start_time >= end_time:
        raise HTTPException(400, "start_time must be before end_time")
    if bool(metadata_key) != bool(metadata_value):
        raise HTTPException(400, "metadata_key and metadata_value must be provided together")
    if hours is not None and (start_time or end_time):
        raise HTTPException(400, "Use either `hours` OR `start_time`/`end_time`, not both")

    # Decode cursor — clean 400 on tampering
    try:
        parsed_cursor = maybe_decode(cursor)
    except ValueError as e:
        raise HTTPException(400, f"Invalid cursor: {e}")

    # ────────────────────────────────────────────────────────────────────
    # WHERE clauses (outer query)
    # ────────────────────────────────────────────────────────────────────
    outer_clauses: list[str] = []
    params: dict = {}
    _tenant_clause(tenant_id, outer_clauses, params)

    # Time window
    if hours is not None:
        outer_clauses.append(f"latest_reconstructed_at >= now() - INTERVAL {int(hours)} HOUR")
    if start_time is not None:
        outer_clauses.append("latest_reconstructed_at >= {start_time:DateTime64(3)}")
        params["start_time"] = start_time
    if end_time is not None:
        outer_clauses.append("latest_reconstructed_at <= {end_time:DateTime64(3)}")
        params["end_time"] = end_time

    # Cursor: compound comparison for stable keyset pagination
    if parsed_cursor is not None:
        outer_clauses.append(
            "(latest_reconstructed_at, trace_id) < "
            "({cursor_ts:DateTime64(3)}, {cursor_trace_id:String})"
        )
        params["cursor_ts"] = parsed_cursor.ts
        params["cursor_trace_id"] = parsed_cursor.trace_id

    # Errors
    if has_errors is True:
        outer_clauses.append("error_count > 0")
    elif has_errors is False:
        outer_clauses.append("error_count = 0")

    # Token range
    if min_tokens is not None:
        outer_clauses.append("(total_input_tokens + total_output_tokens) >= {min_tokens:UInt64}")
        params["min_tokens"] = min_tokens
    if max_tokens is not None:
        outer_clauses.append("(total_input_tokens + total_output_tokens) <= {max_tokens:UInt64}")
        params["max_tokens"] = max_tokens

    # Latency
    if min_latency_ms is not None:
        outer_clauses.append("total_latency_ms >= {min_latency_ms:UInt64}")
        params["min_latency_ms"] = min_latency_ms
    if max_latency_ms is not None:
        outer_clauses.append("total_latency_ms <= {max_latency_ms:UInt64}")
        params["max_latency_ms"] = max_latency_ms

    # Span-level filters
    span_subquery_clauses: list[str] = []
    if agent_id is not None:
        span_subquery_clauses.append("agent_id = {agent_id:String}")
        params["agent_id"] = agent_id
    if event_type is not None:
        span_subquery_clauses.append("event_type = {event_type:String}")
        params["event_type"] = event_type
    if metadata_key is not None and metadata_value is not None:
        span_subquery_clauses.append(
            "JSONExtractString(metadata, {metadata_key:String}) = {metadata_value:String}"
        )
        params["metadata_key"] = metadata_key
        params["metadata_value"] = metadata_value
    if span_subquery_clauses:
        outer_clauses.append(f"""trace_id IN (
            SELECT DISTINCT trace_id FROM tracing.raw_spans
            WHERE tenant_id = {tenant_id:String} AND {" AND ".join(span_subquery_clauses)}
        )""")

    where_sql = ("WHERE " + " AND ".join(outer_clauses)) if outer_clauses else ""

    # ────────────────────────────────────────────────────────────────────
    # Fetch limit+1 to detect has_more without a second query
    # ────────────────────────────────────────────────────────────────────
    fetch_size = limit + 1
    params["fetch_size"] = fetch_size
    params["offset"] = offset

    # ORDER BY must match the cursor's compound key exactly
    rows = _client().query(
        f"""
        SELECT trace_id, span_count, total_latency_ms,
               total_input_tokens, total_output_tokens, error_count,
               latest_reconstructed_at AS reconstructed_at,
               input_text
        FROM (
            SELECT
                tenant_id,
                trace_id,
                argMax(span_count, reconstructed_at) AS span_count,
                argMax(total_latency_ms, reconstructed_at) AS total_latency_ms,
                argMax(total_input_tokens, reconstructed_at) AS total_input_tokens,
                argMax(total_output_tokens, reconstructed_at) AS total_output_tokens,
                argMax(error_count, reconstructed_at) AS error_count,
                max(reconstructed_at) AS latest_reconstructed_at,
                argMax(input_text, reconstructed_at) AS input_text
            FROM tracing.reconstructed_traces
            GROUP BY tenant_id, trace_id
        )
        {where_sql}
        ORDER BY latest_reconstructed_at DESC, trace_id DESC
        LIMIT {{fetch_size:UInt32}} OFFSET {{offset:UInt32}}
        """,
        parameters=params,
    ).result_rows

    # ────────────────────────────────────────────────────────────────────
    # Slice off the sentinel row and build next_cursor from the last KEPT row
    # ────────────────────────────────────────────────────────────────────
    has_more = len(rows) > limit
    rows = rows[:limit]

    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        last_ts = last[6]
        last_trace_id = last[0]
        next_cursor = TraceCursor(ts=last_ts, trace_id=last_trace_id).encode()

    items = [{
        "trace_id": r[0],
        "span_count": int(r[1]),
        "total_latency_ms": int(r[2]),
        "total_input_tokens": int(r[3]),
        "total_output_tokens": int(r[4]),
        "error_count": int(r[5]),
        "reconstructed_at": _iso_utc(r[6]),
        "input_text": r[7] or "",
    } for r in rows]

    return {
        "items": items,
        "next_cursor": next_cursor,
        "has_more": has_more,
        "limit": limit,
    }
    """List recent trace reconstructions, optionally filtered.

    All filters are optional and combine with AND. When no filter is supplied,
    behavior is identical to the pre-ENG-07 endpoint (back-compat preserved).

    Filter groups
    -------------
    Time:      `hours` (lookback) or explicit `start_time`/`end_time` window.
    Volume:    `min_tokens`/`max_tokens`, `min_latency_ms`/`max_latency_ms`.
    Errors:    `has_errors=true|false`.
    Agent:     `agent_id` (matches any span with this agent in the trace).
    Event:     `event_type` (matches any span with this type in the trace).
    Metadata:  `metadata_key` + `metadata_value` (both required together).
    """

    # ────────────────────────────────────────────────────────────────────
    # Range validation — fail fast with 400 on contradictory input
    # ────────────────────────────────────────────────────────────────────
    if min_tokens is not None and max_tokens is not None and min_tokens > max_tokens:
        raise HTTPException(400, "min_tokens must be <= max_tokens")
    if (
        min_latency_ms is not None
        and max_latency_ms is not None
        and min_latency_ms > max_latency_ms
    ):
        raise HTTPException(400, "min_latency_ms must be <= max_latency_ms")
    if start_time and end_time and start_time >= end_time:
        raise HTTPException(400, "start_time must be before end_time")
    if bool(metadata_key) != bool(metadata_value):
        raise HTTPException(400, "metadata_key and metadata_value must be provided together")
    if hours is not None and (start_time or end_time):
        raise HTTPException(400, "Use either `hours` OR `start_time`/`end_time`, not both")

    # ────────────────────────────────────────────────────────────────────
    # Build the WHERE clause for the outer query (post-aggregation columns)
    # ────────────────────────────────────────────────────────────────────
    outer_clauses: list[str] = []
    params: dict = {}

    # Time window
    if hours is not None:
        outer_clauses.append(
            f"latest_reconstructed_at >= now() - INTERVAL {int(hours)} HOUR"
        )
    if start_time is not None:
        outer_clauses.append("latest_reconstructed_at >= {start_time:DateTime64(3)}")
        params["start_time"] = start_time
    if end_time is not None:
        outer_clauses.append("latest_reconstructed_at <= {end_time:DateTime64(3)}")
        params["end_time"] = end_time

    # Error filter (preserves existing semantics)
    if has_errors is True:
        outer_clauses.append("error_count > 0")
    elif has_errors is False:
        outer_clauses.append("error_count = 0")

    # Token range — total tokens = input + output
    if min_tokens is not None:
        outer_clauses.append(
            "(total_input_tokens + total_output_tokens) >= {min_tokens:UInt64}"
        )
        params["min_tokens"] = min_tokens
    if max_tokens is not None:
        outer_clauses.append(
            "(total_input_tokens + total_output_tokens) <= {max_tokens:UInt64}"
        )
        params["max_tokens"] = max_tokens

    # Latency range
    if min_latency_ms is not None:
        outer_clauses.append("total_latency_ms >= {min_latency_ms:UInt64}")
        params["min_latency_ms"] = min_latency_ms
    if max_latency_ms is not None:
        outer_clauses.append("total_latency_ms <= {max_latency_ms:UInt64}")
        params["max_latency_ms"] = max_latency_ms

    # Span-level filters (agent_id, event_type, metadata) need a subquery
    # against raw_spans because reconstructed_traces is one row per trace.
    span_subquery_clauses: list[str] = []
    if agent_id is not None:
        span_subquery_clauses.append("agent_id = {agent_id:String}")
        params["agent_id"] = agent_id
    if event_type is not None:
        span_subquery_clauses.append("event_type = {event_type:String}")
        params["event_type"] = event_type
    if metadata_key is not None and metadata_value is not None:
        span_subquery_clauses.append(
            "JSONExtractString(metadata, {metadata_key:String}) = {metadata_value:String}"
        )
        params["metadata_key"] = metadata_key
        params["metadata_value"] = metadata_value

    if span_subquery_clauses:
        subquery_where = " AND ".join(span_subquery_clauses)
        outer_clauses.append(f"""trace_id IN (
            SELECT DISTINCT trace_id FROM tracing.raw_spans
            WHERE {subquery_where}
        )""")

    where_sql = ("WHERE " + " AND ".join(outer_clauses)) if outer_clauses else ""

    # Bind pagination params separately
    params["limit"] = limit
    params["offset"] = offset

    # ────────────────────────────────────────────────────────────────────
    # Execute
    # ────────────────────────────────────────────────────────────────────
    rows = _client().query(
        f"""
        SELECT trace_id, span_count, total_latency_ms,
               total_input_tokens, total_output_tokens, error_count,
               latest_reconstructed_at AS reconstructed_at,
               input_text
        FROM (
            SELECT
                trace_id,
                argMax(span_count, reconstructed_at) AS span_count,
                argMax(total_latency_ms, reconstructed_at) AS total_latency_ms,
                argMax(total_input_tokens, reconstructed_at) AS total_input_tokens,
                argMax(total_output_tokens, reconstructed_at) AS total_output_tokens,
                argMax(error_count, reconstructed_at) AS error_count,
                max(reconstructed_at) AS latest_reconstructed_at,
                argMax(input_text, reconstructed_at) AS input_text
            FROM tracing.reconstructed_traces
            GROUP BY trace_id
        )
        {where_sql}
        ORDER BY latest_reconstructed_at DESC
        LIMIT {{limit:UInt32}} OFFSET {{offset:UInt32}}
        """,
        parameters=params,
    ).result_rows

    return [{
        "trace_id": r[0],
        "span_count": int(r[1]),
        "total_latency_ms": int(r[2]),
        "total_input_tokens": int(r[3]),
        "total_output_tokens": int(r[4]),
        "error_count": int(r[5]),
        "reconstructed_at": _iso_utc(r[6]),
        "input_text": r[7] or "",
    } for r in rows]
    """List the most recent reconstruction per trace_id.

    The engine can rewrite the same trace repeatedly. We collapse to the latest
    row per trace here so callers don't see duplicates while table merges catch up.
    """
    where = ""
    if has_errors is True:
        where = "WHERE error_count > 0"
    elif has_errors is False:
        where = "WHERE error_count = 0"

    rows = _client().query(f"""
        SELECT trace_id, span_count, total_latency_ms,
               total_input_tokens, total_output_tokens, error_count,
               latest_reconstructed_at AS reconstructed_at,
               input_text
        FROM (
            SELECT
                trace_id,
                argMax(span_count, reconstructed_at) AS span_count,
                argMax(total_latency_ms, reconstructed_at) AS total_latency_ms,
                argMax(total_input_tokens, reconstructed_at) AS total_input_tokens,
                argMax(total_output_tokens, reconstructed_at) AS total_output_tokens,
                argMax(error_count, reconstructed_at) AS error_count,
                max(reconstructed_at) AS latest_reconstructed_at,
                argMax(input_text, reconstructed_at) AS input_text
            FROM tracing.reconstructed_traces
            GROUP BY trace_id
        )
        {where}
        ORDER BY latest_reconstructed_at DESC
        LIMIT {limit}
    """).result_rows

    return [{
        "trace_id": r[0],
        "span_count": int(r[1]),
        "total_latency_ms": int(r[2]),
        "total_input_tokens": int(r[3]),
        "total_output_tokens": int(r[4]),
        "error_count": int(r[5]),
        "reconstructed_at": _iso_utc(r[6]),
        "input_text": r[7] or "",
    } for r in rows]

# ─────────────────────────────────────────────────────────────────────────────
# ENG-09: Server-Sent Events streaming endpoint
# ─────────────────────────────────────────────────────────────────────────────

STREAM_POLL_INTERVAL_SEC = 1.0      # how often we ask ClickHouse for new rows
STREAM_HEARTBEAT_SEC = 15.0          # idle-keepalive cadence
STREAM_BATCH_LIMIT = 50              # max rows pushed per tick


@app.get("/traces/stream")
async def stream_traces(
    request: Request,
    has_errors: Optional[bool] = None,
    agent_id: Optional[str] = Query(None, min_length=1, max_length=128),
    tenant_id: str = Depends(_require_tenant),
):
    """Server-Sent Events stream of newly reconstructed traces.

    Connection lifecycle:
        - Client opens GET /traces/stream
        - We poll ClickHouse every 1s for rows with reconstructed_at > our
          last-seen timestamp
        - Each new trace is pushed as a `trace_update` event
        - Every 15s we send a `heartbeat` event so the connection survives
          proxies and load-balancer idle timeouts
        - If the client disconnects, our generator notices and exits cleanly

    Filters supported (subset of /traces — keep it small for clarity):
        - has_errors: only error traces (true) / only clean (false)
        - agent_id:   only traces touching this agent

    Event types emitted:
        - "trace_update": { trace_id, span_count, total_latency_ms, ... }
        - "heartbeat":    { ts: <unix_ms> }
        - "error":        { detail: "..." }     (sent once before close)
    """

    # Seed the cursor to "right now" — we never replay history, only stream
    # future updates. This is the standard SSE-tail behavior.
    last_seen_ms = int(time.time() * 1000)

    async def event_generator() -> AsyncIterator[dict]:
        nonlocal last_seen_ms
        last_heartbeat = time.time()

        # Build the static part of the WHERE clause once per connection.
        # User-controllable filters are bound via ClickHouse parameters.
        clauses = ["toUnixTimestamp64Milli(latest_reconstructed_at) > {cursor_ms:UInt64}"]
        params: dict = {}

        if has_errors is True:
            clauses.append("error_count > 0")
        elif has_errors is False:
            clauses.append("error_count = 0")

        if agent_id is not None:
            clauses.append("""trace_id IN (
                SELECT DISTINCT trace_id FROM tracing.raw_spans
                WHERE tenant_id = {tenant_id:String} AND agent_id = {agent_id:String}
            )""")
            params["agent_id"] = agent_id

        params["tenant_id"] = tenant_id

        where_sql = " AND ".join(clauses)

        try:
            while True:
                # Bail out if the client has gone away.
                if await request.is_disconnected():
                    break

                # Poll ClickHouse for traces newer than our cursor.
                params["cursor_ms"] = last_seen_ms
                try:
                    rows = _client().query(
                        f"""
                        SELECT trace_id, span_count, total_latency_ms,
                               total_input_tokens, total_output_tokens, error_count,
                               latest_reconstructed_at, input_text
                        FROM (
                            SELECT
                                trace_id,
                                argMax(span_count, reconstructed_at) AS span_count,
                                argMax(total_latency_ms, reconstructed_at) AS total_latency_ms,
                                argMax(total_input_tokens, reconstructed_at) AS total_input_tokens,
                                argMax(total_output_tokens, reconstructed_at) AS total_output_tokens,
                                argMax(error_count, reconstructed_at) AS error_count,
                                max(reconstructed_at) AS latest_reconstructed_at,
                                argMax(input_text, reconstructed_at) AS input_text
                            FROM tracing.reconstructed_traces
                            WHERE tenant_id = {tenant_id:String}
                            GROUP BY trace_id
                        )
                        WHERE {where_sql}
                        ORDER BY latest_reconstructed_at ASC
                        LIMIT {STREAM_BATCH_LIMIT}
                        """,
                        parameters=params,
                    ).result_rows
                except Exception as e:
                    # Surface DB errors as an SSE 'error' event then stop.
                    # Browsers will not auto-reconnect after we close cleanly.
                    yield {
                        "event": "error",
                        "data": json.dumps({"detail": f"clickhouse_error: {e}"}),
                    }
                    return

                for r in rows:
                    ts = r[6]
                    # ClickHouse DateTime64 → ms since epoch
                    ts_ms = int(ts.timestamp() * 1000) if ts is not None else 0
                    # Move cursor forward strictly (so we never re-emit).
                    if ts_ms > last_seen_ms:
                        last_seen_ms = ts_ms

                    payload = {
                        "trace_id": r[0],
                        "span_count": int(r[1]),
                        "total_latency_ms": int(r[2]),
                        "total_input_tokens": int(r[3]),
                        "total_output_tokens": int(r[4]),
                        "error_count": int(r[5]),
                        "reconstructed_at": _iso_utc(r[6]),
                        "input_text": r[7] or "",
                    }
                    yield {
                        "event": "trace_update",
                        "data": json.dumps(payload),
                    }

                # Heartbeat every STREAM_HEARTBEAT_SEC so idle connections
                # stay open behind nginx / cloud load balancers.
                now = time.time()
                if now - last_heartbeat >= STREAM_HEARTBEAT_SEC:
                    yield {
                        "event": "heartbeat",
                        "data": json.dumps({"ts": int(now * 1000)}),
                    }
                    last_heartbeat = now

                await asyncio.sleep(STREAM_POLL_INTERVAL_SEC)

        except asyncio.CancelledError:
            # Normal client disconnect — let the response close cleanly.
            return

    # ping=None — we send our own heartbeats with payload, no need for
    # sse-starlette to inject blank ping comments.
    return EventSourceResponse(event_generator(), ping=None)
    
@app.get("/traces/{trace_id}")
@app.get("/traces/{trace_id}")
def get_trace(trace_id: str, tenant_id: str = Depends(_require_tenant)):
    """Full DAG + blame (V1 and V2) for a specific trace."""
    rows = _client().query(
        """
        SELECT trace_id, span_count, total_latency_ms,
               total_input_tokens, total_output_tokens, error_count,
               dag_json, blame_json, reconstructed_at, input_text,
               blame_v2_json
        FROM tracing.reconstructed_traces FINAL
        WHERE tenant_id = {tenant_id:String} AND trace_id = {trace_id:String}
        """,
        parameters={"tenant_id": tenant_id, "trace_id": trace_id},
    ).result_rows
    if not rows:
        raise HTTPException(404, f"Trace {trace_id} not found")
    r = rows[0]
    trace_payload = {
        "trace_id": r[0],
        "span_count": int(r[1]),
        "total_latency_ms": int(r[2]),
        "total_input_tokens": int(r[3]),
        "total_output_tokens": int(r[4]),
        "error_count": int(r[5]),
        "dag": json.loads(r[6]),
        "blame": json.loads(r[7]),
        "reconstructed_at": _iso_utc(r[8]),
        "input_text": r[9] or "",
        "blame_v2": json.loads(r[10] or "[]"),   # ← Now at index 10
    }
    decisions = _query_trace_decisions(trace_id=trace_id, tenant_id=tenant_id, limit=200, offset=0)
    trace_payload["decisions"] = decisions
    trace_payload["decision_count"] = len(decisions)
    return trace_payload

@app.get("/traces/{trace_id}/spans")
def get_raw_spans(trace_id: str, tenant_id: str = Depends(_require_tenant)):
    """Raw spans for the timeline view — pre-reconstruction."""
    rows = _client().query(
        """
         SELECT
             span_id,
             parent_span_id,
             agent_id,
             vector_clock,
             event_type,
             input_tokens,
             output_tokens,
             latency_ms,
             start_time_ms,
             metadata,
             idempotency_key,
             ingested_at
         FROM tracing.raw_spans
         WHERE tenant_id = {tenant_id:String} AND trace_id = {trace_id:String}
         ORDER BY start_time_ms ASC, ingested_at DESC
        """,
        parameters={"tenant_id": tenant_id, "trace_id": trace_id},
    ).result_rows
    
    # Deduplicate by idempotency_key, keeping the most recent
    seen_keys = {}
    for r in rows:
        idempotency_key = r[10]
        dedup_key = idempotency_key if idempotency_key else f"{trace_id}:{r[0]}"
        if dedup_key not in seen_keys:
            seen_keys[dedup_key] = r
    
    return [{
        "span_id": r[0],
        "parent_span_id": r[1],
        "agent_id": r[2],
        "vector_clock": dict(r[3]) if r[3] else {},
        "event_type": r[4],
        "input_tokens": int(r[5]),
        "output_tokens": int(r[6]),
        "latency_ms": int(r[7]),
        "start_time_ms": int(r[8]),
        "metadata": r[9],
    } for r in seen_keys.values()]


def _query_trace_decisions(
    *,
    trace_id: str,
    decision_type: Optional[str] = None,
    actor_agent_id: Optional[str] = None,
    confidence_min: Optional[float] = None,
    confidence_max: Optional[float] = None,
    from_ts: Optional[int] = None,
    to_ts: Optional[int] = None,
    metadata_query: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
    tenant_id: str = "default",
):
    clauses = ["tenant_id = {tenant_id:String}", "trace_id = {trace_id:String}"]
    params = {"tenant_id": tenant_id, "trace_id": trace_id}
    if decision_type:
        clauses.append("decision_type = {decision_type:String}")
        params["decision_type"] = decision_type
    if actor_agent_id:
        clauses.append("actor_agent_id = {actor_agent_id:String}")
        params["actor_agent_id"] = actor_agent_id
    if confidence_min is not None:
        clauses.append("confidence >= {confidence_min:Float64}")
        params["confidence_min"] = float(confidence_min)
    if confidence_max is not None:
        clauses.append("confidence <= {confidence_max:Float64}")
        params["confidence_max"] = float(confidence_max)
    if from_ts is not None:
        clauses.append("timestamp_ms >= {from_ts:UInt64}")
        params["from_ts"] = int(from_ts)
    if to_ts is not None:
        clauses.append("timestamp_ms <= {to_ts:UInt64}")
        params["to_ts"] = int(to_ts)
    if metadata_query:
        clauses.append("positionCaseInsensitive(metadata, {metadata_query:String}) > 0")
        params["metadata_query"] = metadata_query

    query = f"""
         SELECT trace_id,
             decision_id,
             source_span_id,
             actor_agent_id,
             decision_type,
             selected_candidate_id,
             confidence,
             rationale_summary,
             evidence_refs,
             candidates_json,
             timestamp_ms,
             metadata,
             idempotency_key,
             ingested_at
         FROM tracing.raw_decisions
         WHERE {' AND '.join(clauses)}
         ORDER BY timestamp_ms ASC, ingested_at DESC
        """
    rows = _client().query(query, parameters=params).result_rows
    
    # Deduplicate by idempotency_key, keeping the most recent
    seen_keys = {}
    for r in rows:
        idempotency_key = r[12]
        dedup_key = idempotency_key if idempotency_key else f"{r[0]}:{r[1]}"
        if dedup_key not in seen_keys:
            seen_keys[dedup_key] = r
    
    # Apply limit/offset after deduplication
    deduped_rows = list(seen_keys.values())[offset:offset + limit]
    
    out = []
    for r in deduped_rows:
        try:
            candidates = json.loads(r[9] or "[]")
        except Exception:
            candidates = []
        out.append(
            {
                "trace_id": r[0],
                "decision_id": r[1],
                "source_span_id": r[2],
                "actor_agent_id": r[3],
                "decision_type": r[4],
                "selected_candidate_id": r[5],
                "confidence": float(r[6]),
                "rationale_summary": r[7],
                "evidence_refs": list(r[8] or []),
                "candidates": candidates,
                "timestamp_ms": int(r[10]),
                "metadata": r[11],
            }
        )
    return out


@app.get("/traces/{trace_id}/decisions")
def get_trace_decisions(
    trace_id: str,
    decision_type: Optional[str] = None,
    actor_agent_id: Optional[str] = None,
    confidence_min: Optional[float] = Query(None, ge=0.0, le=1.0),
    confidence_max: Optional[float] = Query(None, ge=0.0, le=1.0),
    from_ts: Optional[int] = Query(None, ge=0),
    to_ts: Optional[int] = Query(None, ge=0),
    metadata_query: Optional[str] = None,
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    tenant_id: str = Depends(_require_tenant),
):
    return _query_trace_decisions(
        trace_id=trace_id,
        decision_type=decision_type,
        actor_agent_id=actor_agent_id,
        confidence_min=confidence_min,
        confidence_max=confidence_max,
        from_ts=from_ts,
        to_ts=to_ts,
        metadata_query=metadata_query,
        limit=limit,
        offset=offset,
        tenant_id=tenant_id,
    )


@app.get("/traces/{trace_id}/root-cause")
def get_root_cause(
    trace_id: str,
    decision_type: Optional[str] = None,
    actor_agent_id: Optional[str] = None,
    confidence_min: Optional[float] = Query(None, ge=0.0, le=1.0),
    confidence_max: Optional[float] = Query(None, ge=0.0, le=1.0),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    tenant_id: str = Depends(_require_tenant),
):
    clauses = ["tenant_id = {tenant_id:String}", "trace_id = {trace_id:String}"]
    params = {"tenant_id": tenant_id, "trace_id": trace_id}
    if decision_type:
        clauses.append("decision_type = {decision_type:String}")
        params["decision_type"] = decision_type
    if actor_agent_id:
        clauses.append("actor_agent_id = {actor_agent_id:String}")
        params["actor_agent_id"] = actor_agent_id
    if confidence_min is not None:
        clauses.append("confidence >= {confidence_min:Float64}")
        params["confidence_min"] = float(confidence_min)
    if confidence_max is not None:
        clauses.append("confidence <= {confidence_max:Float64}")
        params["confidence_max"] = float(confidence_max)

    client = _client()
    try:
        rows = client.query(
            f"""
            SELECT decision_id, source_span_id, target_span_id,
                   decision_type, actor_agent_id, selected_candidate_id,
                   confidence, reason_summary,
                   impact_latency_ms, impact_tokens, impact_error_count,
                   impact_score, uncertainty, chain_rank
            FROM tracing.decision_reason_chains FINAL
            WHERE {' AND '.join(clauses)}
            ORDER BY impact_score DESC, chain_rank ASC
            LIMIT {limit} OFFSET {offset}
            """,
            parameters=params,
        ).result_rows
    except Exception:
        # Backward-compatible fallback for environments that haven't created
        # decision_reason_chains yet.
        rows = client.query(
            f"""
            SELECT decision_id, source_span_id, target_span_id,
                   decision_type, actor_agent_id, selected_candidate_id,
                   confidence, rationale_summary,
                   impact_latency_ms, impact_tokens, impact_error_count,
                   0.0 AS impact_score,
                   'unknown' AS uncertainty,
                   999 AS chain_rank
            FROM tracing.decision_edges FINAL
            WHERE {' AND '.join(clauses)}
            ORDER BY impact_error_count DESC, impact_latency_ms DESC, impact_tokens DESC
            LIMIT {limit} OFFSET {offset}
            """,
            parameters=params,
        ).result_rows
    return [
        {
            "decision_id": r[0],
            "source_span_id": r[1],
            "target_span_id": r[2],
            "decision_type": r[3],
            "actor_agent_id": r[4],
            "selected_candidate_id": r[5],
            "confidence": float(r[6]),
            "rationale_summary": r[7],
            "impact_latency_ms": int(r[8]),
            "impact_tokens": int(r[9]),
            "impact_error_count": int(r[10]),
            "impact_score": float(r[11]),
            "uncertainty": r[12],
            "chain_rank": int(r[13]),
        }
        for r in rows
    ]


@app.get("/slo")
def get_slo_status(history_limit: int = Query(20, ge=1, le=500)):
    """Current SLO status + recent history per SLO.

    Live status is computed on demand (collector /metrics + ClickHouse).
    History is read from tracing.slo_status if the worker has been running.
    """
    from slo.evaluator import evaluate_all  # local import to avoid hard dep on import

    try:
        statuses = evaluate_all(_client())
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"SLO evaluation failed: {e}")

    history: dict = {}
    try:
        rows = _client().query(
            f"""
            SELECT slo_name, evaluated_at, value, passing, sample_count, notes
            FROM tracing.slo_status
            ORDER BY evaluated_at DESC
            LIMIT {history_limit * len(statuses) if statuses else history_limit}
            """
        ).result_rows
        for slo_name, evaluated_at, value, passing, sample_count, notes in rows:
            history.setdefault(slo_name, []).append({
                "evaluated_at": _iso_utc(evaluated_at),
                "value": float(value),
                "passing": bool(passing),
                "sample_count": int(sample_count),
                "notes": notes,
            })
        for k in history:
            history[k] = history[k][:history_limit]
    except Exception:
        # Table may not exist yet — fall back to empty history.
        history = {}

    overall = "pass" if statuses and all(s.passing for s in statuses) else (
        "fail" if statuses else "unknown"
    )
    return {
        "overall": overall,
        "statuses": [s.as_dict() for s in statuses],
        "history": history,
    }


@app.get("/agents/blame")
def aggregate_blame(
    hours: int = Query(24, ge=1, le=720),
    model_version: str = Query("v1", pattern="^v[12]$"),
    tenant_id: str = Depends(_require_tenant),
):
    """Aggregate per-agent blame across all traces in the time window.

    Query params:
        hours:         lookback window (1..720h).
        model_version: "v1" (default, point estimates) or "v2" (adds CI bounds,
                       std-dev, error amplification, component breakdown).

    v1 is the default for backward compatibility — existing dashboards
    consuming this endpoint continue to work without changes.
    """
    column = "blame_v2_json" if model_version == "v2" else "blame_json"
    client = _client()
    rows = client.query(f"""
        SELECT {column}
        FROM tracing.reconstructed_traces
                WHERE tenant_id = {tenant_id:String}
                    AND reconstructed_at >= now() - INTERVAL {hours} HOUR
    """).result_rows

    # Aggregate by agent_id across all traces in the window.
    by_agent: Dict[str, Dict[str, Any]] = {}
    for (blame_json,) in rows:
        try:
            for entry in json.loads(blame_json or "[]"):
                agent_id = entry["agent_id"]
                acc = by_agent.setdefault(agent_id, {
                    "agent_id": agent_id,
                    "trace_count": 0,
                    "total_blame_score": 0.0,
                    "total_latency_ms": 0,
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                    "error_count": 0,
                })
                acc["trace_count"] += 1
                acc["total_blame_score"] += entry.get("blame_score", 0.0)
                acc["total_latency_ms"] += entry.get("total_latency_ms", 0)
                acc["total_input_tokens"] += entry.get("total_input_tokens", 0)
                acc["total_output_tokens"] += entry.get("total_output_tokens", 0)
                acc["error_count"] += entry.get("error_count", 0)

                # V2-only aggregation
                if model_version == "v2":
                    acc.setdefault("ci_lows", []).append(entry.get("blame_score_ci_low", 0.0))
                    acc.setdefault("ci_highs", []).append(entry.get("blame_score_ci_high", 0.0))
                    acc.setdefault("stds", []).append(entry.get("blame_score_std", 0.0))
                    acc.setdefault("amps", []).append(entry.get("error_amplification", 0.0))
        except (json.JSONDecodeError, KeyError, TypeError):
            continue  # skip malformed rows defensively

    # Build response. Mean across traces — same convention as V1.
    out = []
    for agent_id, acc in by_agent.items():
        n = max(acc["trace_count"], 1)
        record = {
            "agent_id": agent_id,
            "trace_count": acc["trace_count"],
            "avg_blame_score": round(acc["total_blame_score"] / n, 2),
            "total_latency_ms": acc["total_latency_ms"],
            "total_input_tokens": acc["total_input_tokens"],
            "total_output_tokens": acc["total_output_tokens"],
            "error_count": acc["error_count"],
        }
        if model_version == "v2":
            record["avg_blame_score_ci_low"] = round(
                sum(acc["ci_lows"]) / len(acc["ci_lows"]), 2)
            record["avg_blame_score_ci_high"] = round(
                sum(acc["ci_highs"]) / len(acc["ci_highs"]), 2)
            record["avg_blame_score_std"] = round(
                sum(acc["stds"]) / len(acc["stds"]), 2)
            record["avg_error_amplification"] = round(
                sum(acc["amps"]) / len(acc["amps"]), 2)
            record["model_version"] = "v2.0"
        out.append(record)

    out.sort(key=lambda r: r["avg_blame_score"], reverse=True)
    return {"hours": hours, "model_version": model_version, "agents": out}


# ──────────────────────────────────────────────────────────────────────
# ENG-11: Incident model
# ──────────────────────────────────────────────────────────────────────
from alerting import incidents as _incidents


@app.get("/incidents")
def list_incidents(
    state: Optional[str] = Query(None, pattern="^(open|ack|resolved)$"),
    limit: int = Query(200, ge=1, le=1000),
):
    """List incidents with optional state filter, plus aggregate counts."""
    client = _client()
    items = _incidents.list_incidents(client, state=state, limit=limit)
    return {
        "items": [i.to_dict() for i in items],
        "counts": _incidents.state_counts(client),
    }


@app.post("/incidents/{incident_key:path}/ack")
def ack_incident(incident_key: str):
    client = _client()
    inc = _incidents.acknowledge(client, incident_key)
    if inc is None:
        raise HTTPException(404, f"Incident {incident_key} not found")
    return inc.to_dict()


@app.post("/incidents/{incident_key:path}/resolve")
def resolve_incident(incident_key: str):
    client = _client()
    inc = _incidents.resolve(client, incident_key, reason="manual")
    if inc is None:
        raise HTTPException(404, f"Incident {incident_key} not found")
    return inc.to_dict()