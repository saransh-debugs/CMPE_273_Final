"""
gRPC client used by the SDK to ship spans to the collector.

Design notes:
- The channel is created lazily and reused (creating one per span is expensive).
- Emission is non-blocking: spans go onto a thread-local queue and are
  flushed by a background worker thread. If the agent exits, we drain on
  shutdown via atexit.
- If the collector is unreachable we drop spans rather than crash the agent.
  Tracing must never break the user's pipeline.
"""
from __future__ import annotations

import atexit
import logging
import os
import queue
import threading
import time
from typing import Optional, Tuple, Union

import grpc

from generated import tracing_pb2, tracing_pb2_grpc

_logger = logging.getLogger("trace_sdk")

DEFAULT_TARGET = os.environ.get("TRACE_COLLECTOR", "localhost:50051")
QUEUE_MAX = 10_000
SHUTDOWN_TIMEOUT_SEC = 3.0


class _SpanShipper:
    """Singleton — one background thread that ships spans to the collector."""

    _instance: Optional["_SpanShipper"] = None
    _lock = threading.Lock()

    def __init__(self, target: str = DEFAULT_TARGET):
        self.target = target
        self._queue: "queue.Queue[Optional[Tuple[str, Union[tracing_pb2.AgentSpan, tracing_pb2.DecisionEvent]]]]" = queue.Queue(QUEUE_MAX)
        self._channel: Optional[grpc.Channel] = None
        self._stub: Optional[tracing_pb2_grpc.TraceCollectorStub] = None
        self._stopped = threading.Event()
        self._worker = threading.Thread(target=self._run, name="trace-shipper", daemon=True)
        self._worker.start()
        atexit.register(self.shutdown)

    @classmethod
    def get(cls) -> "_SpanShipper":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = _SpanShipper()
        return cls._instance

    def _ensure_channel(self) -> tracing_pb2_grpc.TraceCollectorStub:
        if self._stub is None:
            self._channel = grpc.insecure_channel(self.target)
            self._stub = tracing_pb2_grpc.TraceCollectorStub(self._channel)
        return self._stub

    def submit_span(self, span: tracing_pb2.AgentSpan) -> None:
        try:
            self._queue.put_nowait(("span", span))
        except queue.Full:
            _logger.warning("Span queue full, dropping span %s", span.span_id)

    def submit_decision(self, decision: tracing_pb2.DecisionEvent) -> None:
        try:
            self._queue.put_nowait(("decision", decision))
        except queue.Full:
            _logger.warning("Span queue full, dropping decision %s", decision.decision_id)

    def _run(self) -> None:
        while not self._stopped.is_set():
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                break
            try:
                stub = self._ensure_channel()
                kind, payload = item
                if kind == "span":
                    stub.RecordSpan(payload, timeout=2.0)
                else:
                    stub.RecordDecision(payload, timeout=2.0)
            except grpc.RpcError as e:
                ident = (
                    payload.span_id
                    if kind == "span"
                    else payload.decision_id
                )
                _logger.warning("Failed to ship %s %s: %s", kind, ident, e.code())
                # Reset channel — next attempt will reconnect.
                self._channel = None
                self._stub = None
            except Exception as e:
                _logger.exception("Unexpected error shipping span: %s", e)

    def shutdown(self) -> None:
        if self._stopped.is_set():
            return
        deadline = time.time() + SHUTDOWN_TIMEOUT_SEC
        # Drain the queue politely.
        while not self._queue.empty() and time.time() < deadline:
            time.sleep(0.05)
        self._stopped.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._channel is not None:
            self._channel.close()


def emit_span(span: tracing_pb2.AgentSpan) -> None:
    _SpanShipper.get().submit_span(span)


def emit_decision(decision: tracing_pb2.DecisionEvent) -> None:
    _SpanShipper.get().submit_decision(decision)
