"""
Tests for collector/metrics.py — pure Python, no external deps.

Run:
    python -m collector.tests
"""
from __future__ import annotations

import threading
import unittest


class TestPercentile(unittest.TestCase):
    def setUp(self):
        from collector.metrics import _percentile
        self._p = _percentile

    def test_empty(self):
        self.assertEqual(self._p([], 0.5), 0.0)

    def test_single_value(self):
        self.assertEqual(self._p([5.0], 0.5), 5.0)
        self.assertEqual(self._p([5.0], 0.99), 5.0)

    def test_two_values_median(self):
        self.assertAlmostEqual(self._p([1.0, 2.0], 0.5), 1.5)

    def test_p100_returns_max(self):
        self.assertEqual(self._p([1.0, 2.0, 3.0, 4.0, 5.0], 1.0), 5.0)

    def test_p0_returns_min(self):
        self.assertEqual(self._p([1.0, 2.0, 3.0], 0.0), 1.0)

    def test_monotone_across_quantiles(self):
        vals = sorted(float(i) for i in range(100))
        p50 = self._p(vals, 0.50)
        p95 = self._p(vals, 0.95)
        p99 = self._p(vals, 0.99)
        self.assertLessEqual(p50, p95)
        self.assertLessEqual(p95, p99)


class TestHistogram(unittest.TestCase):
    def _hist(self, capacity=1024):
        from collector.metrics import _Histogram
        return _Histogram(capacity)

    def test_empty_snapshot_returns_zeros(self):
        snap = self._hist().snapshot()
        self.assertEqual(snap["count"], 0)
        self.assertEqual(snap["p95"], 0.0)
        self.assertEqual(snap["max"], 0.0)

    def test_single_observation(self):
        h = self._hist()
        h.observe(42.0)
        snap = h.snapshot()
        self.assertEqual(snap["count"], 1)
        self.assertEqual(snap["avg"], 42.0)
        self.assertEqual(snap["max"], 42.0)

    def test_ring_buffer_evicts_oldest(self):
        h = self._hist(capacity=5)
        for i in range(10):
            h.observe(float(i))
        snap = h.snapshot()
        self.assertEqual(snap["count"], 5)
        self.assertEqual(snap["max"], 9.0)

    def test_percentiles_monotone(self):
        h = self._hist()
        for i in range(1, 101):
            h.observe(float(i))
        snap = h.snapshot()
        self.assertLessEqual(snap["p50"], snap["p95"])
        self.assertLessEqual(snap["p95"], snap["p99"])

    def test_thread_safe_concurrent_writes(self):
        h = self._hist()

        def worker():
            for _ in range(100):
                h.observe(1.0)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertGreater(h.snapshot()["count"], 0)


class TestWriterMetrics(unittest.TestCase):
    def _wm(self):
        from collector.metrics import WriterMetrics
        return WriterMetrics("span")

    def test_initial_rates_are_one(self):
        snap = self._wm().snapshot()
        self.assertEqual(snap["acceptance_rate"], 1.0)
        self.assertEqual(snap["flush_success_rate"], 1.0)

    def test_acceptance_rate_with_rejects(self):
        wm = self._wm()
        wm.inc_accepted()
        wm.inc_accepted()
        wm.inc_rejected()
        self.assertAlmostEqual(wm.snapshot()["acceptance_rate"], 2 / 3)

    def test_flush_success_rate_mixed(self):
        wm = self._wm()
        wm.record_flush(batch_size=10, latency_ms=5.0, ok=True)
        wm.record_flush(batch_size=5, latency_ms=3.0, ok=False)
        snap = wm.snapshot()
        self.assertAlmostEqual(snap["flush_success_rate"], 0.5)
        self.assertEqual(snap["rows_flushed"], 10)
        self.assertEqual(snap["flush_failures"], 1)

    def test_counters_increment_correctly(self):
        wm = self._wm()
        wm.inc_accepted()
        wm.inc_rejected()
        wm.inc_queue_full()
        wm.inc_replay(3)
        snap = wm.snapshot()
        self.assertEqual(snap["accepted"], 1)
        self.assertEqual(snap["rejected"], 1)
        self.assertEqual(snap["queue_full_events"], 1)
        self.assertEqual(snap["replay_enqueued"], 3)

    def test_gauge_attachment(self):
        wm = self._wm()
        wm.attach_queue_depth(lambda: 42)
        wm.attach_wal_backlog(lambda: 7)
        snap = wm.snapshot()
        self.assertEqual(snap["queue_depth"], 42)
        self.assertEqual(snap["wal_backlog"], 7)

    def test_no_gauge_attachment_returns_zero(self):
        snap = self._wm().snapshot()
        self.assertEqual(snap["queue_depth"], 0)
        self.assertEqual(snap["wal_backlog"], 0)

    def test_flush_latency_histogram_populated(self):
        wm = self._wm()
        wm.record_flush(batch_size=100, latency_ms=20.0, ok=True)
        snap = wm.snapshot()
        self.assertEqual(snap["flush_latency_ms"]["count"], 1)
        self.assertEqual(snap["flush_latency_ms"]["max"], 20.0)
        self.assertEqual(snap["flush_batch_size"]["count"], 1)

    def test_all_flush_failures_rate_is_zero(self):
        wm = self._wm()
        wm.record_flush(batch_size=0, latency_ms=1.0, ok=False)
        wm.record_flush(batch_size=0, latency_ms=1.0, ok=False)
        self.assertEqual(wm.snapshot()["flush_success_rate"], 0.0)


class TestCollectorMetrics(unittest.TestCase):
    def _cm(self):
        from collector.metrics import CollectorMetrics
        return CollectorMetrics()

    def test_writer_same_name_returns_same_instance(self):
        cm = self._cm()
        self.assertIs(cm.writer("span"), cm.writer("span"))

    def test_different_names_are_distinct_instances(self):
        cm = self._cm()
        self.assertIsNot(cm.writer("span"), cm.writer("decision"))

    def test_servicer_counters(self):
        cm = self._cm()
        cm.inc_span_received()
        cm.inc_span_received()
        cm.inc_span_dropped()
        cm.inc_decision_received()
        cm.inc_decision_dropped()
        svc = cm.snapshot()["servicer"]
        self.assertEqual(svc["spans_received"], 2)
        self.assertEqual(svc["spans_dropped"], 1)
        self.assertEqual(svc["decisions_received"], 1)
        self.assertEqual(svc["decisions_dropped"], 1)

    def test_snapshot_includes_registered_writers(self):
        cm = self._cm()
        cm.writer("span").inc_accepted()
        self.assertIn("span", cm.snapshot()["writers"])

    def test_uptime_is_positive(self):
        import time
        cm = self._cm()
        time.sleep(0.01)
        self.assertGreater(cm.snapshot()["uptime_sec"], 0)

    def test_thread_safe_span_counters(self):
        cm = self._cm()

        def worker():
            for _ in range(50):
                cm.inc_span_received()

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(cm.snapshot()["servicer"]["spans_received"], 500)


def main():
    print("Running collector/metrics tests:")
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [TestPercentile, TestHistogram, TestWriterMetrics, TestCollectorMetrics]:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
