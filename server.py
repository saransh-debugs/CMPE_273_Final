"""
Async gRPC collector. Accepts spans from instrumented agents, hands them
to the batch writer, and replies fast so agents never block.

Run:
    python -m collector.server
"""
from __future__ import annotations

import asyncio
import logging
import signal

import grpc

from generated import tracing_pb2, tracing_pb2_grpc

from .writer import BatchWriter

_logger = logging.getLogger("collector.server")
LISTEN_ADDR = "[::]:50051"


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
    )


class TraceCollectorServicer(tracing_pb2_grpc.TraceCollectorServicer):
    def __init__(self, writer: BatchWriter):
        self.writer = writer

    async def RecordSpan(
        self,
        request: tracing_pb2.AgentSpan,
        context: grpc.aio.ServicerContext,
    ) -> tracing_pb2.SpanResponse:
        accepted = self.writer.submit_nowait(_span_to_row(request))
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
            if self.writer.submit_nowait(_span_to_row(span)):
                n += 1
        return tracing_pb2.SpanResponse(success=True, message="ok", spans_received=n)


async def serve() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s | %(message)s",
    )

    writer = BatchWriter()
    writer.start()

    server = grpc.aio.server()
    tracing_pb2_grpc.add_TraceCollectorServicer_to_server(
        TraceCollectorServicer(writer), server
    )
    server.add_insecure_port(LISTEN_ADDR)
    await server.start()
    _logger.info("Collector listening on %s", LISTEN_ADDR)

    stop_event = asyncio.Event()

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
    await writer.stop()
    _logger.info("Bye.")


if __name__ == "__main__":
    asyncio.run(serve())
