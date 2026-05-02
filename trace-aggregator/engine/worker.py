"""
Causal Engine worker.

Polls ClickHouse for traces with new raw spans, reconstructs the DAG,
computes blame, and writes the result back to `reconstructed_traces`.

Run:
    python -m engine.worker
"""
from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

import clickhouse_connect

from .dag import Span, reconstruct_dag, serialize_dag, detect_gaps
from .blame import compute_blame, blame_to_dicts

_logger = logging.getLogger("engine.worker")

POLL_INTERVAL_SEC = 2.0
LOOKBACK_SEC = 300  # only consider traces that received spans recently
# Parallel reconstruction uses one ClickHouse client per task (connections are not shared).
MAX_RECON_WORKERS = max(1, int(os.environ.get("ENGINE_RECON_MAX_WORKERS", "4")))


def _is_epoch_placeholder(dt: Optional[datetime]) -> bool:
    """True when first_reconstructed_at was never populated (migration / old rows)."""
    if dt is None:
        return True
    return dt.year <= 1971


def _as_utc_aware(dt: datetime) -> datetime:
    """ClickHouse-connect encodes naive datetimes as local time — always insert aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _resolve_first_reconstructed_at(client, trace_id: str, insert_ts_utc: datetime) -> datetime:
    """Carry forward immutable first reconstruction time across ReplacingMergeTree updates."""
    rows = client.query(
        """
        SELECT first_reconstructed_at, reconstructed_at
        FROM tracing.reconstructed_traces FINAL
        WHERE trace_id = {trace_id:String}
        LIMIT 1
        """,
        parameters={"trace_id": trace_id},
    ).result_rows
    if not rows:
        return insert_ts_utc
    prev_first, prev_rec = rows[0][0], rows[0][1]
    if _is_epoch_placeholder(prev_first):
        return _as_utc_aware(prev_rec)
    return _as_utc_aware(prev_first)


@dataclass
class Decision:
    trace_id: str
    decision_id: str
    source_span_id: str
    actor_agent_id: str
    decision_type: str
    selected_candidate_id: str
    confidence: float
    rationale_summary: str
    evidence_refs: List[str]
    candidates_json: str
    timestamp_ms: int
    metadata: str


def _connect():
    return clickhouse_connect.get_client(
        host="localhost", port=8123, username="default", password=""
    )


def find_active_traces(client) -> List[str]:
    """Trace IDs that received spans in the lookback window.

    Ordered by newest last-ingest first so a sequential reconstruct loop clears
    hot traces before backlog. Otherwise stale IDs in the same window can soak
    up poll budget and falsely inflate reconstruction lag against the SLO.
    """
    rows = client.query(f"""
        SELECT trace_id
        FROM tracing.raw_spans
        WHERE ingested_at > now() - INTERVAL {LOOKBACK_SEC} SECOND
        GROUP BY trace_id
        ORDER BY max(ingested_at) DESC
    """).result_rows
    return [r[0] for r in rows]


def fetch_spans(client, trace_id: str) -> List[Span]:
    rows = client.query(
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
            idempotency_key,
            ingested_at
        FROM tracing.raw_spans
        WHERE trace_id = {trace_id:String}
        ORDER BY start_time_ms ASC, ingested_at DESC
        """,
        parameters={"trace_id": trace_id},
    ).result_rows
    
    # Deduplicate by idempotency_key, keeping the most recent (highest ingested_at)
    seen_keys = {}
    for r in rows:
        span_id = r[0]
        parent_span_id = r[1]
        agent_id = r[2]
        vector_clock = r[3]
        event_type = r[4]
        input_tokens = r[5]
        output_tokens = r[6]
        latency_ms = r[7]
        start_time_ms = r[8]
        idempotency_key = r[9]
        ingested_at = r[10]
        
        # Use idempotency_key if present, otherwise use trace_id:span_id
        dedup_key = idempotency_key if idempotency_key else f"{trace_id}:{span_id}"
        
        # Keep the first occurrence (most recent due to ORDER BY ingested_at DESC)
        if dedup_key not in seen_keys:
            seen_keys[dedup_key] = (span_id, parent_span_id, agent_id, vector_clock, event_type, input_tokens, output_tokens, latency_ms, start_time_ms)
    
    spans: List[Span] = []
    for span_id, parent_span_id, agent_id, vector_clock, event_type, input_tokens, output_tokens, latency_ms, start_time_ms in seen_keys.values():
        spans.append(Span(
            span_id=span_id,
            parent_span_id=parent_span_id or "",
            agent_id=agent_id,
            vector_clock=dict(vector_clock) if vector_clock else {},
            event_type=event_type,
            input_tokens=int(input_tokens),
            output_tokens=int(output_tokens),
            latency_ms=int(latency_ms),
            start_time_ms=int(start_time_ms),
        ))
    return spans


def fetch_decisions(client, trace_id: str) -> List[Decision]:
    rows = client.query(
        """
        SELECT
            trace_id,
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
        WHERE trace_id = {trace_id:String}
        ORDER BY timestamp_ms ASC, ingested_at DESC
        """,
        parameters={"trace_id": trace_id},
    ).result_rows
    
    # Deduplicate by idempotency_key, keeping the most recent (highest ingested_at)
    seen_keys = {}
    for r in rows:
        trace_id_val = r[0]
        decision_id = r[1]
        source_span_id = r[2]
        actor_agent_id = r[3]
        decision_type = r[4]
        selected_candidate_id = r[5]
        confidence = r[6]
        rationale_summary = r[7]
        evidence_refs = r[8]
        candidates_json = r[9]
        timestamp_ms = r[10]
        metadata = r[11]
        idempotency_key = r[12]
        ingested_at = r[13]
        
        # Use idempotency_key if present, otherwise use trace_id:decision_id
        dedup_key = idempotency_key if idempotency_key else f"{trace_id_val}:{decision_id}"
        
        # Keep the first occurrence (most recent due to ORDER BY ingested_at DESC)
        if dedup_key not in seen_keys:
            seen_keys[dedup_key] = (trace_id_val, decision_id, source_span_id, actor_agent_id, decision_type, selected_candidate_id, confidence, rationale_summary, evidence_refs, candidates_json, timestamp_ms, metadata)
    
    decisions: List[Decision] = []
    for trace_id_val, decision_id, source_span_id, actor_agent_id, decision_type, selected_candidate_id, confidence, rationale_summary, evidence_refs, candidates_json, timestamp_ms, metadata in seen_keys.values():
        decisions.append(
            Decision(
                trace_id=trace_id_val,
                decision_id=decision_id,
                source_span_id=source_span_id,
                actor_agent_id=actor_agent_id,
                decision_type=decision_type,
                selected_candidate_id=selected_candidate_id,
                confidence=float(confidence),
                rationale_summary=rationale_summary,
                evidence_refs=list(evidence_refs or []),
                candidates_json=candidates_json or "[]",
                timestamp_ms=int(timestamp_ms),
                metadata=metadata or "",
            )
        )
    return decisions


def _index_spans(spans: List[Span]) -> Dict[str, Span]:
    return {s.span_id: s for s in spans}


def _collect_descendants(nodes, source_span_id: str) -> List[str]:
    if source_span_id not in nodes:
        return []
    out: List[str] = []
    stack = list(nodes[source_span_id].children)
    seen: Set[str] = set()
    while stack:
        sid = stack.pop()
        if sid in seen:
            continue
        seen.add(sid)
        out.append(sid)
        stack.extend(nodes.get(sid).children if sid in nodes else [])
    return out


def _subtree_spans(nodes, source_span_id: str) -> List[str]:
    if source_span_id not in nodes:
        return []
    descendants = _collect_descendants(nodes, source_span_id)
    return [source_span_id, *descendants]


def _distance_from_source(nodes, source_span_id: str) -> Dict[str, int]:
    if source_span_id not in nodes:
        return {}
    dist: Dict[str, int] = {source_span_id: 0}
    queue: List[str] = [source_span_id]
    while queue:
        cur = queue.pop(0)
        cur_d = dist[cur]
        for child in nodes[cur].children:
            if child not in dist:
                dist[child] = cur_d + 1
                queue.append(child)
    return dist


def _uncertainty_level(nodes, source_span_id: str, target_span_id: str) -> str:
    source_inferred = source_span_id in nodes and nodes[source_span_id].inferred_parent
    target_inferred = target_span_id in nodes and nodes[target_span_id].inferred_parent
    if source_inferred and target_inferred:
        return "high"
    if source_inferred or target_inferred:
        return "medium"
    return "low"


def _write_decision_edges(client, trace_id: str, decisions: List[Decision], spans: List[Span], nodes) -> None:
    if not decisions:
        return
    by_span = _index_spans(spans)
    rows = []
    chain_rows = []
    total_latency = max(1, int(sum(s.latency_ms for s in spans)))
    total_tokens = max(1, int(sum(s.input_tokens + s.output_tokens for s in spans)))
    total_errors = int(sum(1 for s in spans if s.event_type == "error"))

    for d in decisions:
        targets = _collect_descendants(nodes, d.source_span_id)
        if not targets:
            # Persist at least one row for source-only reasoning.
            targets = [d.source_span_id]
        distance = _distance_from_source(nodes, d.source_span_id)
        chain_candidates: List[Tuple[float, Tuple]] = []

        for target_sid in targets:
            target_span = by_span.get(target_sid)
            if target_span:
                impact_latency_ms = int(target_span.latency_ms)
                impact_tokens = int(target_span.input_tokens + target_span.output_tokens)
                impact_error_count = int(1 if target_span.event_type == "error" else 0)
            else:
                impact_latency_ms = 0
                impact_tokens = 0
                impact_error_count = 0
            latency_share = impact_latency_ms / total_latency
            token_share = impact_tokens / total_tokens
            error_share = (impact_error_count / total_errors) if total_errors else 0.0
            impact_score = 100.0 * ((0.5 * latency_share) + (0.3 * token_share) + (0.2 * error_share))
            uncertainty = _uncertainty_level(nodes, d.source_span_id, target_sid)
            rows.append(
                (
                    trace_id,
                    d.decision_id,
                    d.source_span_id,
                    target_sid,
                    d.decision_type,
                    d.actor_agent_id,
                    d.selected_candidate_id,
                    float(d.confidence),
                    d.rationale_summary,
                    impact_latency_ms,
                    impact_tokens,
                    impact_error_count,
                )
            )
            chain_candidates.append(
                (
                    impact_score,
                    (
                        trace_id,
                        d.decision_id,
                        d.source_span_id,
                        target_sid,
                        d.actor_agent_id,
                        d.decision_type,
                        d.selected_candidate_id,
                        float(d.confidence),
                        uncertainty,
                        d.rationale_summary,
                        impact_latency_ms,
                        impact_tokens,
                        impact_error_count,
                        float(impact_score),
                        int(distance.get(target_sid, 9999)),
                    ),
                )
            )

        chain_candidates.sort(key=lambda x: (-x[0], x[1][-1], x[1][3]))
        for idx, (_, row) in enumerate(chain_candidates, start=1):
            chain_rows.append(
                (
                    row[0],  # trace_id
                    row[1],  # decision_id
                    row[2],  # source_span_id
                    row[3],  # target_span_id
                    idx,     # chain_rank
                    row[4],  # actor_agent_id
                    row[5],  # decision_type
                    row[6],  # selected_candidate_id
                    row[7],  # confidence
                    row[8],  # uncertainty
                    row[9],  # reason_summary
                    row[10], # impact_latency_ms
                    row[11], # impact_tokens
                    row[12], # impact_error_count
                    row[13], # impact_score
                )
            )

    client.insert(
        "tracing.decision_edges",
        rows,
        column_names=[
            "trace_id",
            "decision_id",
            "source_span_id",
            "target_span_id",
            "decision_type",
            "actor_agent_id",
            "selected_candidate_id",
            "confidence",
            "rationale_summary",
            "impact_latency_ms",
            "impact_tokens",
            "impact_error_count",
        ],
    )
    if chain_rows:
        client.insert(
            "tracing.decision_reason_chains",
            chain_rows,
            column_names=[
                "trace_id",
                "decision_id",
                "source_span_id",
                "target_span_id",
                "chain_rank",
                "actor_agent_id",
                "decision_type",
                "selected_candidate_id",
                "confidence",
                "uncertainty",
                "reason_summary",
                "impact_latency_ms",
                "impact_tokens",
                "impact_error_count",
                "impact_score",
            ],
        )


def _extract_input_text(client, trace_id: str) -> str:
    rows = client.query(
        """
        SELECT
            metadata,
            start_time_ms,
            idempotency_key,
            ingested_at
        FROM tracing.raw_spans
        WHERE trace_id = {trace_id:String}
        ORDER BY start_time_ms ASC, ingested_at DESC
        LIMIT 1
        """,
        parameters={"trace_id": trace_id},
    ).result_rows
    
    if not rows:
        return ""
    
    return rows[0][0] or ""


def reconstruct_one(client, trace_id: str) -> dict:
    spans = fetch_spans(client, trace_id)
    if not spans:
        return {"trace_id": trace_id, "span_count": 0}

    nodes = reconstruct_dag(spans)
    dag = serialize_dag(nodes)
    gaps = detect_gaps(nodes)
    blame = compute_blame(spans)
    decisions = fetch_decisions(client, trace_id)
    input_text = _extract_input_text(client, trace_id)

    total_latency = sum(s.latency_ms for s in spans)
    total_in_tok = sum(s.input_tokens for s in spans)
    total_out_tok = sum(s.output_tokens for s in spans)
    error_count = sum(1 for s in spans if s.event_type == "error")

    payload = {
        "trace_id": trace_id,
        "span_count": len(spans),
        "total_latency_ms": int(total_latency),
        "total_input_tokens": int(total_in_tok),
        "total_output_tokens": int(total_out_tok),
        "error_count": int(error_count),
        "dag_json": json.dumps({"nodes": dag, "inferred_parents": gaps}),
        "blame_json": json.dumps(blame_to_dicts(blame)),
        "input_text": input_text,
    }

    # Timezone-aware UTC: naive datetimes are interpreted as local time by clickhouse-connect
    # during insert (.timestamp()), which corrupts DateTime64 relative to Collector/CH defaults.
    insert_ts = datetime.now(timezone.utc)
    anchor_first = _as_utc_aware(_resolve_first_reconstructed_at(client, trace_id, insert_ts))
    # Bad historical rows could leave first_reconstructed_at > reconstructed_at; never allow that.
    if anchor_first > insert_ts:
        anchor_first = insert_ts

    client.insert(
        "tracing.reconstructed_traces",
        [(
            payload["trace_id"],
            insert_ts,
            payload["span_count"],
            payload["total_latency_ms"],
            payload["total_input_tokens"],
            payload["total_output_tokens"],
            payload["error_count"],
            payload["dag_json"],
            payload["blame_json"],
            payload["input_text"],
            anchor_first,
        )],
        column_names=[
            "trace_id",
            "reconstructed_at",
            "span_count",
            "total_latency_ms",
            "total_input_tokens",
            "total_output_tokens",
            "error_count",
            "dag_json",
            "blame_json",
            "input_text",
            "first_reconstructed_at",
        ],
    )
    _write_decision_edges(client, trace_id, decisions, spans, nodes)
    payload["decision_count"] = len(decisions)
    return payload


def _reconstruct_one_task(trace_id: str) -> Tuple[str, dict]:
    """Isolated CH client per thread for concurrent reconstruct_one runs."""
    c = _connect()
    return trace_id, reconstruct_one(c, trace_id)


def run_loop() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s | %(message)s",
    )
    client = _connect()
    _logger.info("Causal engine started. Polling every %.1fs.", POLL_INTERVAL_SEC)

    while True:
        try:
            traces = find_active_traces(client)
            if traces:
                workers = min(MAX_RECON_WORKERS, len(traces))
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = [pool.submit(_reconstruct_one_task, tid) for tid in traces]
                    for fut in as_completed(futures):
                        try:
                            tid, p = fut.result()
                        except Exception:
                            _logger.exception("Single-trace reconstruct failed")
                            continue
                        _logger.info(
                            "Reconstructed trace=%s spans=%d decisions=%d latency=%dms errors=%d",
                            tid, p.get("span_count", 0),
                            p.get("decision_count", 0),
                            p.get("total_latency_ms", 0),
                            p.get("error_count", 0),
                        )
        except Exception as e:  # noqa: BLE001
            _logger.exception("Engine loop error: %s", e)
            # Reconnect on the next iteration in case the client is borked.
            try:
                client = _connect()
            except Exception:
                pass
        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    run_loop()
