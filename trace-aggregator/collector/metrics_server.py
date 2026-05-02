"""
Tiny HTTP metrics server for the collector.

Exposes:
  - GET /metrics       JSON snapshot from collector.metrics.METRICS
  - GET /metrics/prom  Prometheus text exposition (subset; counters + gauges)
  - GET /healthz       readiness probe

Implementation note: hand-rolled with asyncio.start_server to avoid pulling
in another HTTP framework (the gRPC server already runs on grpc.aio). The
exposure surface is small and the contract is METRICS.snapshot().
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Tuple

from .metrics import METRICS

_logger = logging.getLogger("collector.metrics_server")

METRICS_BIND_HOST = os.environ.get("METRICS_BIND_HOST", "0.0.0.0")
METRICS_BIND_PORT = int(os.environ.get("METRICS_BIND_PORT", "9090"))


def _prom_lines(snap: dict) -> str:
    lines = []
    lines.append("# HELP collector_uptime_seconds Process uptime in seconds.")
    lines.append("# TYPE collector_uptime_seconds gauge")
    lines.append(f"collector_uptime_seconds {snap['uptime_sec']:.3f}")

    s = snap["servicer"]
    for name, val in s.items():
        lines.append(f"# TYPE collector_{name} counter")
        lines.append(f"collector_{name} {val}")

    for item, w in snap["writers"].items():
        for k in (
            "accepted", "rejected", "queue_full_events",
            "flush_attempts", "flush_success", "flush_failures",
            "rows_flushed", "replay_enqueued",
        ):
            lines.append(f"# TYPE collector_writer_{k} counter")
            lines.append(f'collector_writer_{k}{{item="{item}"}} {w[k]}')
        for k in ("queue_depth", "wal_backlog", "acceptance_rate", "flush_success_rate"):
            lines.append(f"# TYPE collector_writer_{k} gauge")
            lines.append(f'collector_writer_{k}{{item="{item}"}} {w[k]}')
        lat = w["flush_latency_ms"]
        for q in ("avg", "p50", "p95", "p99", "max"):
            lines.append(f"# TYPE collector_writer_flush_latency_ms_{q} gauge")
            lines.append(f'collector_writer_flush_latency_ms_{q}{{item="{item}"}} {lat[q]:.3f}')
    return "\n".join(lines) + "\n"


def _build_response(status: str, body: bytes, content_type: str) -> bytes:
    headers = [
        f"HTTP/1.1 {status}",
        f"Content-Type: {content_type}",
        f"Content-Length: {len(body)}",
        "Connection: close",
        "Access-Control-Allow-Origin: *",
        "",
        "",
    ]
    return ("\r\n".join(headers)).encode("utf-8") + body


async def _handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=2.0)
        if not line:
            return
        try:
            method, path, _ = line.decode("ascii", errors="replace").split(" ", 2)
        except ValueError:
            writer.write(_build_response("400 Bad Request", b"bad request", "text/plain"))
            await writer.drain()
            return
        # Drain remaining headers (we don't need them).
        while True:
            try:
                hl = await asyncio.wait_for(reader.readline(), timeout=1.0)
            except asyncio.TimeoutError:
                break
            if hl in (b"\r\n", b""):
                break

        if method != "GET":
            writer.write(_build_response("405 Method Not Allowed", b"method not allowed", "text/plain"))
        elif path.startswith("/metrics/prom"):
            body = _prom_lines(METRICS.snapshot()).encode("utf-8")
            writer.write(_build_response("200 OK", body, "text/plain; version=0.0.4"))
        elif path.startswith("/metrics"):
            body = json.dumps(METRICS.snapshot(), default=str, indent=2).encode("utf-8")
            writer.write(_build_response("200 OK", body, "application/json"))
        elif path.startswith("/healthz"):
            writer.write(_build_response("200 OK", b"ok\n", "text/plain"))
        else:
            writer.write(_build_response("404 Not Found", b"not found", "text/plain"))
        await writer.drain()
    except Exception as e:  # noqa: BLE001
        _logger.debug("metrics http error: %s", e)
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def start_metrics_server() -> asyncio.base_events.Server:
    server = await asyncio.start_server(_handle_client, METRICS_BIND_HOST, METRICS_BIND_PORT)
    _logger.info("Metrics server on http://%s:%d/metrics", METRICS_BIND_HOST, METRICS_BIND_PORT)
    return server
