"""
Async gRPC collector. Accepts spans from instrumented agents, hands them
to the batch writer, and replies fast so agents never block.

Run:
    python -m collector.server
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal

import grpc

from generated import tracing_pb2, tracing_pb2_grpc

from .metrics import METRICS
from .metrics_server import start_metrics_server
from .writer import BatchWriter

_logger = logging.getLogger("collector.server")
LISTEN_ADDR = os.environ.get("TRACE_COLLECTOR_BIND", "[::]:50051")
OPS_LOG_INTERVAL_SEC = float(os.environ.get("OPS_LOG_INTERVAL_SEC", "30"))

DECISION_COLUMNS = [
    "timestamp_ms",
    "trace_id",
    "decision_id",
    "source_span_id",
    "actor_agent_id",
    "decision_type",
    "selected_candidate_id",
    "confidence",
    "rationale_summary",
    "evidence_refs",
    "candidates_json",
    "metadata",
    "idempotency_key",
]


def _span_to_row(s: tracing_pb2.AgentSpan) -> tuple:
    """Map a Protobuf span to a tuple matching writer.COLUMNS."""
    # ClickHouse Map(String, UInt32) maps from a Python dict.
    vc = {k: int(v) for k, v in s.vector_clock.items()}
    return (
        int(s.start_time_ms),
        s.trace_id,
        s.span_id,
        s.parent_span_id,
        s.agent_id,
        vc,
        s.event_type,
        int(s.input_tokens),
        int(s.output_tokens),
        int(s.latency_ms),
        s.metadata,
        f"{s.trace_id}:{s.span_id}",
    )


def _decision_to_row(d: tracing_pb2.DecisionEvent) -> tuple:
    candidates = [
        {
            "candidate_id": c.candidate_id,
            "candidate_type": c.candidate_type,
            "score": float(c.score),
            "reason": c.reason,
        }
        for c in d.candidates
    ]
    return (
        int(d.timestamp_ms),
        d.trace_id,
        d.decision_id,
        d.source_span_id,
        d.actor_agent_id,
        d.decision_type,
        d.selected_candidate_id,
        float(d.confidence),
        d.rationale_summary,
        list(d.evidence_refs),
        json.dumps(candidates, default=str),
        d.metadata,
        f"{d.trace_id}:{d.decision_id}",
    )


class TraceCollectorServicer(tracing_pb2_grpc.TraceCollectorServicer):
    def __init__(self, span_writer: BatchWriter, decision_writer: BatchWriter):
        self.span_writer = span_writer
        self.decision_writer = decision_writer
        self._span_received = 0
        self._decision_received = 0
        self._span_dropped = 0
        self._decision_dropped = 0

    async def RecordSpan(
        self,
        request: tracing_pb2.AgentSpan,
        context: grpc.aio.ServicerContext,
    ) -> tracing_pb2.SpanResponse:
        accepted = self.span_writer.submit_nowait(_span_to_row(request))
        self._span_received += 1
        METRICS.inc_span_received()
        if not accepted:
            self._span_dropped += 1
            METRICS.inc_span_dropped()
        return tracing_pb2.SpanResponse(
            success=accepted,
            message="ok" if accepted else "buffer_full",
            spans_received=1 if accepted else 0,
        )

    async def StreamSpans(
        self,
        request_iterator,
        context: grpc.aio.ServicerContext,
    ) -> tracing_pb2.SpanResponse:
        n = 0
        async for span in request_iterator:
            self._span_received += 1
            METRICS.inc_span_received()
            if self.span_writer.submit_nowait(_span_to_row(span)):
                n += 1
            else:
                self._span_dropped += 1
                METRICS.inc_span_dropped()
        return tracing_pb2.SpanResponse(success=True, message="ok", spans_received=n)

    async def RecordDecision(
        self,
        request: tracing_pb2.DecisionEvent,
        context: grpc.aio.ServicerContext,
    ) -> tracing_pb2.DecisionResponse:
        accepted = self.decision_writer.submit_nowait(_decision_to_row(request))
        self._decision_received += 1
        METRICS.inc_decision_received()
        if not accepted:
            self._decision_dropped += 1
            METRICS.inc_decision_dropped()
        return tracing_pb2.DecisionResponse(
            success=accepted,
            message="ok" if accepted else "buffer_full",
            decisions_received=1 if accepted else 0,
        )

    async def StreamDecisions(
        self,
        request_iterator,
        context: grpc.aio.ServicerContext,
    ) -> tracing_pb2.DecisionResponse:
        n = 0
        async for decision in request_iterator:
            self._decision_received += 1
            METRICS.inc_decision_received()
            if self.decision_writer.submit_nowait(_decision_to_row(decision)):
                n += 1
            else:
                self._decision_dropped += 1
                METRICS.inc_decision_dropped()
        return tracing_pb2.DecisionResponse(success=True, message="ok", decisions_received=n)

    def log_ingest_stats(self) -> None:
        _logger.info(
            "Ingest stats spans(received=%d dropped=%d) decisions(received=%d dropped=%d)",
            self._span_received,
            self._span_dropped,
            self._decision_received,
            self._decision_dropped,
        )


async def _ops_log_loop(stop_event: asyncio.Event) -> None:
    """Emit a one-line structured metrics summary every OPS_LOG_INTERVAL_SEC.

    Why: hosts/k8s can scrape /metrics, but a human reading collector logs
    needs the same signal without leaving the terminal.
    """
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=OPS_LOG_INTERVAL_SEC)
        except asyncio.TimeoutError:
            pass
        if stop_event.is_set():
            return
        snap = METRICS.snapshot()
        for item, w in snap["writers"].items():
            _logger.info(
                "ops/%s accepted=%d rejected=%d queue_full=%d queue_depth=%d "
                "wal_backlog=%d flush_ok=%d flush_fail=%d flush_p95_ms=%.1f "
                "acceptance=%.4f flush_success=%.4f",
                item,
                w["accepted"], w["rejected"], w["queue_full_events"],
                w["queue_depth"], w["wal_backlog"],
                w["flush_success"], w["flush_failures"],
                w["flush_latency_ms"]["p95"],
                w["acceptance_rate"], w["flush_success_rate"],
            )


async def serve() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s | %(message)s",
    )

    span_writer = BatchWriter(item_name="span")
    decision_writer = BatchWriter(
        table="tracing.raw_decisions",
        columns=DECISION_COLUMNS,
        item_name="decision",
    )
    span_writer.start()
    decision_writer.start()

    server = grpc.aio.server()
    servicer = TraceCollectorServicer(span_writer, decision_writer)
    tracing_pb2_grpc.add_TraceCollectorServicer_to_server(
        servicer, server
    )
    server.add_insecure_port(LISTEN_ADDR)
    await server.start()
    _logger.info("Collector listening on %s", LISTEN_ADDR)

    metrics_server = await start_metrics_server()

    stop_event = asyncio.Event()
    ops_task = asyncio.create_task(_ops_log_loop(stop_event), name="ops-log")

    def _shutdown():
        _logger.info("Shutdown requested")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            # Windows
            pass

    await stop_event.wait()
    _logger.info("Stopping gRPC server...")
    await server.stop(grace=2.0)
    metrics_server.close()
    await metrics_server.wait_closed()
    await span_writer.stop()
    await decision_writer.stop()
    ops_task.cancel()
    servicer.log_ingest_stats()
    _logger.info("Bye.")


if __name__ == "__main__":
    asyncio.run(serve())
