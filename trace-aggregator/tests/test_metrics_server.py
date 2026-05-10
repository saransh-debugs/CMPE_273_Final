"""Unit tests for collector.metrics_server helpers."""
from __future__ import annotations

import json
import unittest

from collector.metrics_server import _build_response, _prom_lines


class TestPromLines(unittest.TestCase):
    def test_contains_counters_and_writer_labels(self) -> None:
        snap = {
            "uptime_sec": 1.234,
            "servicer": {
                "spans_received": 10,
                "spans_dropped": 0,
                "decisions_received": 2,
                "decisions_dropped": 0,
            },
            "writers": {
                "span": {
                    "accepted": 5,
                    "rejected": 1,
                    "queue_full_events": 0,
                    "flush_attempts": 3,
                    "flush_success": 3,
                    "flush_failures": 0,
                    "rows_flushed": 15,
                    "replay_enqueued": 0,
                    "queue_depth": 0,
                    "wal_backlog": 0,
                    "acceptance_rate": 0.9,
                    "flush_success_rate": 1.0,
                    "flush_latency_ms": {
                        "count": 3,
                        "avg": 4.0,
                        "p50": 3.0,
                        "p95": 8.0,
                        "p99": 9.0,
                        "max": 10.0,
                    },
                    "flush_batch_size": {
                        "count": 3,
                        "avg": 5.0,
                        "p50": 5.0,
                        "p95": 5.0,
                        "p99": 5.0,
                        "max": 5.0,
                    },
                }
            },
        }
        text = _prom_lines(snap)
        self.assertIn("collector_uptime_seconds", text)
        self.assertIn("collector_spans_received", text)
        self.assertIn('collector_writer_accepted{item="span"}', text)
        self.assertIn('collector_writer_flush_latency_ms_p95{item="span"}', text)


class TestBuildResponse(unittest.TestCase):
    def test_headers_and_body(self) -> None:
        raw = _build_response("200 OK", b'{"x":1}', "application/json")
        head, _, body = raw.partition(b"\r\n\r\n")
        self.assertIn(b"HTTP/1.1 200 OK", head)
        self.assertIn(b"Content-Length: 7", head)
        self.assertEqual(json.loads(body.decode()), {"x": 1})
