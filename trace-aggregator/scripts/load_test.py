#!/usr/bin/env python
"""
Synthetic load generator for span + decision ingestion.

Usage:
  python scripts/load_test.py --traces 500 --concurrency 32 --collector localhost:50051
"""
from __future__ import annotations

import argparse
import pathlib
import random
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List

import grpc

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from generated import tracing_pb2, tracing_pb2_grpc


def _span(
    trace_id: str,
    span_id: str,
    parent_span_id: str,
    agent_id: str,
    vector_clock: Dict[str, int],
    event_type: str,
    latency_ms: int,
    in_tok: int,
    out_tok: int,
) -> tracing_pb2.AgentSpan:
    return tracing_pb2.AgentSpan(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        agent_id=agent_id,
        vector_clock={k: int(v) for k, v in vector_clock.items()},
        event_type=event_type,
        input_tokens=int(in_tok),
        output_tokens=int(out_tok),
        latency_ms=int(latency_ms),
        start_time_ms=int(time.time() * 1000),
        metadata='{"load_test":true}',
    )


def _emit_trace(stub: tracing_pb2_grpc.TraceCollectorStub, include_error: bool = False) -> str:
    trace_id = str(uuid.uuid4())
    orch = str(uuid.uuid4())
    res = str(uuid.uuid4())
    code = str(uuid.uuid4())
    rev = str(uuid.uuid4())

    spans: List[tracing_pb2.AgentSpan] = [
        _span(trace_id, orch, "", "orchestrator", {"orchestrator": 1}, "llm_call", random.randint(30, 120), 40, 55),
        _span(trace_id, res, orch, "research_agent", {"orchestrator": 1, "research_agent": 1}, "llm_call", random.randint(120, 450), 150, 200),
        _span(trace_id, code, orch, "coder_agent", {"orchestrator": 1, "coder_agent": 1}, "error" if include_error else "llm_call", random.randint(100, 400), 120, 180),
        _span(trace_id, rev, res, "reviewer_agent", {"orchestrator": 1, "research_agent": 1, "coder_agent": 1, "reviewer_agent": 1}, "llm_call", random.randint(80, 220), 90, 110),
    ]
    for s in spans:
        stub.RecordSpan(s, timeout=2.0)

    decision = tracing_pb2.DecisionEvent(
        trace_id=trace_id,
        decision_id=str(uuid.uuid4()),
        source_span_id=res,
        actor_agent_id="reviewer_agent",
        decision_type="route_branch",
        selected_candidate_id="review",
        confidence=0.8,
        rationale_summary="load-test decision",
        evidence_refs=["load_test"],
        candidates=[
            tracing_pb2.DecisionCandidate(
                candidate_id="review", candidate_type="branch", score=0.8, reason="default"
            )
        ],
        timestamp_ms=int(time.time() * 1000),
        metadata='{"load_test":true}',
    )
    stub.RecordDecision(decision, timeout=2.0)
    return trace_id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--traces", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--collector", type=str, default="localhost:50051")
    parser.add_argument("--error-rate", type=float, default=0.2)
    args = parser.parse_args()

    ch = grpc.insecure_channel(args.collector)
    stub = tracing_pb2_grpc.TraceCollectorStub(ch)
    start = time.time()
    ok = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futures = [
            ex.submit(_emit_trace, stub, random.random() < args.error_rate)
            for _ in range(args.traces)
        ]
        for f in as_completed(futures):
            try:
                _ = f.result()
                ok += 1
            except Exception:
                failed += 1

    elapsed = time.time() - start
    print(f"collector={args.collector}")
    print(f"traces_requested={args.traces}")
    print(f"traces_sent_ok={ok}")
    print(f"traces_failed={failed}")
    print(f"elapsed_sec={elapsed:.3f}")
    print(f"throughput_traces_per_sec={(ok / elapsed) if elapsed > 0 else 0:.2f}")


if __name__ == "__main__":
    main()

