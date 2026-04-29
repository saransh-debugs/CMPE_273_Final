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
import time
from dataclasses import dataclass
from typing import Dict, List, Set, Tuple

import clickhouse_connect

from .dag import Span, reconstruct_dag, serialize_dag, detect_gaps
from .blame import compute_blame, blame_to_dicts

_logger = logging.getLogger("engine.worker")

POLL_INTERVAL_SEC = 2.0
LOOKBACK_SEC = 300  # only consider traces that received spans recently


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
    """Trace IDs that received spans in the lookback window."""
    rows = client.query(f"""
        SELECT DISTINCT trace_id
        FROM tracing.raw_spans
        WHERE ingested_at > now() - INTERVAL {LOOKBACK_SEC} SECOND
    """).result_rows
    return [r[0] for r in rows]


def fetch_spans(client, trace_id: str) -> List[Span]:
    rows = client.query(
        """
        SELECT span_id, parent_span_id, agent_id, vector_clock,
               event_type, input_tokens, output_tokens, latency_ms, start_time_ms
        FROM tracing.raw_spans
        WHERE trace_id = {trace_id:String}
        ORDER BY start_time_ms ASC
        """,
        parameters={"trace_id": trace_id},
    ).result_rows
    spans: List[Span] = []
    for r in rows:
        spans.append(Span(
            span_id=r[0],
            parent_span_id=r[1] or "",
            agent_id=r[2],
            vector_clock=dict(r[3]) if r[3] else {},
            event_type=r[4],
            input_tokens=int(r[5]),
            output_tokens=int(r[6]),
            latency_ms=int(r[7]),
            start_time_ms=int(r[8]),
        ))
    return spans


def fetch_decisions(client, trace_id: str) -> List[Decision]:
    rows = client.query(
        """
        SELECT trace_id, decision_id, source_span_id, actor_agent_id,
               decision_type, selected_candidate_id, confidence,
               rationale_summary, evidence_refs, candidates_json,
               timestamp_ms, metadata
        FROM tracing.raw_decisions
        WHERE trace_id = {trace_id:String}
        ORDER BY timestamp_ms ASC
        """,
        parameters={"trace_id": trace_id},
    ).result_rows
    decisions: List[Decision] = []
    for r in rows:
        decisions.append(
            Decision(
                trace_id=r[0],
                decision_id=r[1],
                source_span_id=r[2],
                actor_agent_id=r[3],
                decision_type=r[4],
                selected_candidate_id=r[5],
                confidence=float(r[6]),
                rationale_summary=r[7],
                evidence_refs=list(r[8] or []),
                candidates_json=r[9] or "[]",
                timestamp_ms=int(r[10]),
                metadata=r[11] or "",
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
        SELECT metadata FROM tracing.raw_spans
        WHERE trace_id = {trace_id:String}
        ORDER BY start_time_ms ASC LIMIT 1
        """,
        parameters={"trace_id": trace_id},
    ).result_rows
    if not rows or not rows[0][0]:
        return ""
    try:
        meta = json.loads(rows[0][0])
        return str(meta.get("input_text", ""))[:1000]
    except Exception:
        return ""


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

    client.insert(
        "tracing.reconstructed_traces",
        [(
            payload["trace_id"],
            payload["span_count"],
            payload["total_latency_ms"],
            payload["total_input_tokens"],
            payload["total_output_tokens"],
            payload["error_count"],
            payload["dag_json"],
            payload["blame_json"],
            payload["input_text"],
        )],
        column_names=[
            "trace_id", "span_count", "total_latency_ms",
            "total_input_tokens", "total_output_tokens", "error_count",
            "dag_json", "blame_json", "input_text",
        ],
    )
    _write_decision_edges(client, trace_id, decisions, spans, nodes)
    payload["decision_count"] = len(decisions)
    return payload


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
            for tid in traces:
                p = reconstruct_one(client, tid)
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
