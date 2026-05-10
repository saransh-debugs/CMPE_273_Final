"""Unit tests for slo.evaluator helpers and signals (no live ClickHouse)."""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from slo.evaluator import (
    _epoch_first_reconstructed,
    _fetch_collector_metrics,
    _percentile,
    _reconstruction_latency_ms,
    _signal_api_latency_p95,
    _signal_flush_success,
    _signal_ingest_acceptance,
    _utc_naive_for_delta,
    evaluate_all,
)
from slo.spec import SLOSpec


class TestUtcNaiveForDelta(unittest.TestCase):
    def test_timezone_stripped_to_utc_naive(self) -> None:
        dt = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        n = _utc_naive_for_delta(dt)
        self.assertIsNone(n.tzinfo)
        self.assertEqual(n.hour, 12)


class TestEpochFirstReconstructed(unittest.TestCase):
    def test_none_is_epoch_sentinel(self) -> None:
        self.assertTrue(_epoch_first_reconstructed(None))

    def test_old_epoch_true(self) -> None:
        self.assertTrue(_epoch_first_reconstructed(datetime(1970, 1, 1)))


class TestReconstructionLatencyMs(unittest.TestCase):
    def test_ttfr_when_ingest_before_anchor(self) -> None:
        last_i = datetime(2026, 1, 1, 12, 0, 0)
        fr = datetime(2026, 1, 1, 12, 0, 2)
        latest = datetime(2026, 1, 1, 12, 5, 0)
        ms = _reconstruction_latency_ms(last_i, fr, latest)
        self.assertAlmostEqual(ms, 2000.0)

    def test_none_when_latest_before_ingest(self) -> None:
        last_i = datetime(2026, 1, 1, 12, 0, 10)
        fr = datetime(2026, 1, 1, 12, 0, 2)
        latest = datetime(2026, 1, 1, 12, 0, 5)
        self.assertIsNone(_reconstruction_latency_ms(last_i, fr, latest))

    def test_warm_trace_uses_latest_minus_ingest(self) -> None:
        last_i = datetime(2026, 1, 1, 12, 0, 10)
        fr = datetime(2026, 1, 1, 12, 0, 2)
        latest = datetime(2026, 1, 1, 12, 0, 12)
        ms = _reconstruction_latency_ms(last_i, fr, latest)
        self.assertAlmostEqual(ms, 2000.0)


class TestEvaluatorPercentile(unittest.TestCase):
    def test_matches_ordering(self) -> None:
        self.assertEqual(_percentile([1.0, 2.0, 3.0, 4.0], 0.5), 2.5)


class TestSignals(unittest.TestCase):
    def test_ingest_acceptance_no_traffic(self) -> None:
        spec = SLOSpec("x", "t", "", "ingest_acceptance", 0.9, ">=", 5)
        val, n, note = _signal_ingest_acceptance(spec, None, {"writers": {"span": {}}})
        self.assertEqual(val, 1.0)
        self.assertEqual(n, 0)
        self.assertIn("no traffic", note)

    def test_ingest_acceptance_ratio(self) -> None:
        spec = SLOSpec("x", "t", "", "ingest_acceptance", 0.9, ">=", 5)
        metrics = {"writers": {"span": {"accepted": 99, "rejected": 1}}}
        val, n, _ = _signal_ingest_acceptance(spec, None, metrics)
        self.assertAlmostEqual(val, 0.99)
        self.assertEqual(n, 100)

    def test_flush_success_no_metrics(self) -> None:
        spec = SLOSpec("x", "t", "", "flush_success", 0.9, ">=", 5)
        val, n, note = _signal_flush_success(spec, None, None)
        self.assertEqual(val, 0.0)
        self.assertIn("unreachable", note)

    def test_flush_success_aggregate(self) -> None:
        spec = SLOSpec("x", "t", "", "flush_success", 0.9, ">=", 5)
        metrics = {
            "writers": {
                "span": {"flush_attempts": 10, "flush_success": 9},
                "decision": {"flush_attempts": 10, "flush_success": 10},
            }
        }
        val, n, _ = _signal_flush_success(spec, None, metrics)
        self.assertAlmostEqual(val, 19 / 20)
        self.assertEqual(n, 20)


class TestFetchCollectorMetrics(unittest.TestCase):
    def test_parses_json(self) -> None:
        payload = json.dumps({"writers": {}}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = payload
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = mock_resp
        mock_cm.__exit__.return_value = None
        with patch("slo.evaluator.urllib.request.urlopen", return_value=mock_cm):
            got = _fetch_collector_metrics()
        self.assertEqual(got, {"writers": {}})


class TestSignalApiLatency(unittest.TestCase):
    def test_uses_samples_when_urlopen_succeeds(self) -> None:
        spec = SLOSpec(
            name="api_latency_p95",
            title="API p95",
            description="",
            signal="api_latency_p95",
            threshold=500.0,
            comparison="<=",
            window_minutes=5,
            unit="ms",
        )
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"[]"
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = mock_resp
        mock_cm.__exit__.return_value = None
        with patch("slo.evaluator.urllib.request.urlopen", return_value=mock_cm):
            with patch("slo.evaluator.API_PROBE_SAMPLES", 1):
                val, n, note = _signal_api_latency_p95(spec, None, None)
        self.assertGreaterEqual(n, 1)
        self.assertGreaterEqual(val, 0.0)
        self.assertEqual(note, "")


class TestEvaluateAllMocked(unittest.TestCase):
    def test_end_to_end_with_mock_client_and_metrics(self) -> None:
        fake_metrics = {
            "writers": {
                "span": {
                    "accepted": 1000,
                    "rejected": 0,
                    "flush_attempts": 100,
                    "flush_success": 100,
                },
            }
        }
        client = MagicMock()
        client.query.return_value = MagicMock(result_rows=[])
        with patch("slo.evaluator._fetch_collector_metrics", return_value=fake_metrics):
            with patch("slo.evaluator._signal_api_latency_p95", return_value=(1.0, 2, "")):
                rows = evaluate_all(client=client, fetch_metrics=True)
        self.assertGreater(len(rows), 0)
        by_name = {r.name: r for r in rows}
        self.assertIn("ingest_acceptance", by_name)
        self.assertTrue(by_name["ingest_acceptance"].passing)
