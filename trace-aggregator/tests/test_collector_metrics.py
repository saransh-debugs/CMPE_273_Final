"""Unit tests for collector.metrics."""
from __future__ import annotations

import threading
import unittest

from collector.metrics import (
    METRICS,
    CollectorMetrics,
    WriterMetrics,
    _Histogram,
    _percentile,
)


class TestPercentile(unittest.TestCase):
    def test_empty(self) -> None:
        self.assertEqual(_percentile([], 0.5), 0.0)

    def test_singleton(self) -> None:
        self.assertEqual(_percentile([42.0], 0.5), 42.0)
        self.assertEqual(_percentile([42.0], 0.95), 42.0)

    def test_interpolation(self) -> None:
        vals = [10.0, 20.0, 30.0, 40.0]
        self.assertEqual(_percentile(sorted(vals), 0.5), 25.0)


class TestHistogram(unittest.TestCase):
    def test_empty_snapshot(self) -> None:
        h = _Histogram(capacity=8)
        s = h.snapshot()
        self.assertEqual(s["count"], 0)
        self.assertEqual(s["p50"], 0.0)

    def test_percentiles_ordering(self) -> None:
        h = _Histogram(capacity=128)
        for i in range(100):
            h.observe(float(i))
        s = h.snapshot()
        self.assertEqual(s["count"], 100)
        self.assertLessEqual(s["p50"], s["p95"])
        self.assertLessEqual(s["p95"], s["p99"])


class TestWriterMetrics(unittest.TestCase):
    def test_counters_and_rates(self) -> None:
        w = WriterMetrics("span")
        w.attach_queue_depth(lambda: 3)
        w.attach_wal_backlog(lambda: 1)
        w.inc_accepted()
        w.inc_accepted()
        w.inc_rejected()
        w.record_flush(batch_size=10, latency_ms=5.0, ok=True)
        s = w.snapshot()
        self.assertEqual(s["accepted"], 2)
        self.assertEqual(s["rejected"], 1)
        self.assertAlmostEqual(s["acceptance_rate"], 2 / 3)
        self.assertEqual(s["queue_depth"], 3)
        self.assertEqual(s["wal_backlog"], 1)
        self.assertEqual(s["flush_latency_ms"]["count"], 1)

    def test_concurrent_observes(self) -> None:
        w = WriterMetrics("decision")

        def worker() -> None:
            for _ in range(50):
                w.inc_accepted()
                w.record_flush(batch_size=1, latency_ms=1.0, ok=True)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        s = w.snapshot()
        self.assertEqual(s["accepted"], 200)
        self.assertEqual(s["flush_attempts"], 200)


class TestCollectorMetrics(unittest.TestCase):
    def test_servicer_and_writers_snapshot(self) -> None:
        c = CollectorMetrics()
        c.inc_span_received()
        c.inc_span_dropped()
        w = c.writer("span")
        w.inc_accepted()
        snap = c.snapshot()
        self.assertEqual(snap["servicer"]["spans_received"], 1)
        self.assertIn("span", snap["writers"])
        self.assertEqual(snap["writers"]["span"]["accepted"], 1)


class TestModuleSingleton(unittest.TestCase):
    def test_metrics_singleton_is_collector_metrics(self) -> None:
        self.assertIsInstance(METRICS, CollectorMetrics)
