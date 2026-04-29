"""
Query API. Reads from ClickHouse and serves JSON to the UI.

Run:
    uvicorn api.main:app --reload --port 8000
"""
from __future__ import annotations

import json
from datetime import timezone
from typing import Optional

import clickhouse_connect
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

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


@app.get("/health")
def health():
    try:
        _client().command("SELECT 1")
        return {"ok": True}
    except Exception as e:
        raise HTTPException(503, f"ClickHouse unreachable: {e}")


@app.get("/traces")
def list_traces(
    limit: int = Query(50, ge=1, le=500),
    has_errors: Optional[bool] = None,
):
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


@app.get("/traces/{trace_id}")
def get_trace(trace_id: str):
    """Full DAG + blame for a specific trace."""
    rows = _client().query(
        """
        SELECT trace_id, span_count, total_latency_ms,
               total_input_tokens, total_output_tokens, error_count,
               dag_json, blame_json, reconstructed_at, input_text
        FROM tracing.reconstructed_traces FINAL
        WHERE trace_id = {trace_id:String}
        """,
        parameters={"trace_id": trace_id},
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
    }
    decisions = _query_trace_decisions(trace_id=trace_id, limit=200, offset=0)
    trace_payload["decisions"] = decisions
    trace_payload["decision_count"] = len(decisions)
    return trace_payload


@app.get("/traces/{trace_id}/spans")
def get_raw_spans(trace_id: str):
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
         WHERE trace_id = {trace_id:String}
         ORDER BY start_time_ms ASC, ingested_at DESC
        """,
        parameters={"trace_id": trace_id},
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
):
    clauses = ["trace_id = {trace_id:String}"]
    params = {"trace_id": trace_id}
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
):
    clauses = ["trace_id = {trace_id:String}"]
    params = {"trace_id": trace_id}
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


@app.get("/agents/blame")
def aggregate_blame(hours: int = Query(24, ge=1, le=720)):
    """Cross-trace agent ranking — useful for the global Blame leaderboard."""
    rows = _client().query(f"""
        SELECT
            agent_id,
            latency_ms,
            input_tokens,
            output_tokens,
            event_type,
            idempotency_key,
            ingested_at
        FROM tracing.raw_spans
        WHERE ingested_at > now() - INTERVAL {hours} HOUR
        ORDER BY ingested_at DESC
    """).result_rows
    
    # Deduplicate by idempotency_key
    seen_keys = {}
    for r in rows:
        agent_id = r[0]
        latency_ms = r[1]
        input_tokens = r[2]
        output_tokens = r[3]
        event_type = r[4]
        idempotency_key = r[5]
        ingested_at = r[6]
        
        # Create span_id from the query context - we need trace_id:span_id
        # Since we don't have all data, use idempotency_key if present
        if idempotency_key:
            dedup_key = idempotency_key
        else:
            # For spans without explicit idempotency_key, they shouldn't be deduplicated
            # This shouldn't happen if collector is working properly
            dedup_key = f"raw_{ingested_at}_{agent_id}"
        
        if dedup_key not in seen_keys:
            seen_keys[dedup_key] = (agent_id, latency_ms, input_tokens, output_tokens, event_type)
    
    # Aggregate by agent_id
    agent_stats = {}
    for agent_id, latency_ms, input_tokens, output_tokens, event_type in seen_keys.values():
        if agent_id not in agent_stats:
            agent_stats[agent_id] = {
                "spans": 0,
                "total_latency_ms": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "error_count": 0,
            }
        agent_stats[agent_id]["spans"] += 1
        agent_stats[agent_id]["total_latency_ms"] += latency_ms or 0
        agent_stats[agent_id]["total_input_tokens"] += input_tokens or 0
        agent_stats[agent_id]["total_output_tokens"] += output_tokens or 0
        if event_type == "error":
            agent_stats[agent_id]["error_count"] += 1
    
    return [
        {
            "agent_id": agent_id,
            "spans": stats["spans"],
            "total_latency_ms": int(stats["total_latency_ms"]),
            "total_input_tokens": int(stats["total_input_tokens"]),
            "total_output_tokens": int(stats["total_output_tokens"]),
            "error_count": int(stats["error_count"]),
        }
        for agent_id, stats in sorted(agent_stats.items(), key=lambda x: x[1]["total_latency_ms"], reverse=True)
    ]
