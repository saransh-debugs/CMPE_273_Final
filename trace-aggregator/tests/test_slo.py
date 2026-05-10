"""Regression checks for SLO evaluator (stdlib unittest; no pytest)."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from slo.evaluator import _reconstruction_lags_ms


class _FakeRows:
    def __init__(self, result_rows):
        self.result_rows = result_rows


class ReconstructionLagTest(unittest.TestCase):
    def test_first_touch_uses_first_reconstructed_anchor(self):
        last_ingest = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        fr_at = datetime(2026, 1, 1, 12, 0, 2, tzinfo=timezone.utc)
        latest_rc = datetime(2026, 1, 1, 12, 5, 0, tzinfo=timezone.utc)
        client = MagicMock()
        client.query.return_value = _FakeRows([(last_ingest, fr_at, latest_rc)])
        self.assertEqual(_reconstruction_lags_ms(client, 60), [2000.0])

    def test_warm_trace_uses_latest_reconstructed_only(self):
        last_ingest = datetime(2026, 1, 1, 12, 0, 10, tzinfo=timezone.utc)
        fr_at = datetime(2026, 1, 1, 12, 0, 2, tzinfo=timezone.utc)
        latest_rc = datetime(2026, 1, 1, 12, 0, 12, tzinfo=timezone.utc)
        client = MagicMock()
        client.query.return_value = _FakeRows([(last_ingest, fr_at, latest_rc)])
        self.assertEqual(_reconstruction_lags_ms(client, 60), [2000.0])

    def test_negative_delta_dropped(self):
        last_ingest = datetime(2026, 1, 1, 12, 0, 5, tzinfo=timezone.utc)
        fr_same = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        earliest_rc = datetime(2026, 1, 1, 12, 0, 3, tzinfo=timezone.utc)
        client = MagicMock()
        client.query.return_value = _FakeRows([(last_ingest, fr_same, earliest_rc)])
        self.assertEqual(_reconstruction_lags_ms(client, 60), [])


class SLOSpecTest(unittest.TestCase):
    def test_passes_gte_above_threshold(self):
        from slo.spec import SLOSpec
        s = SLOSpec("s", "S", "", "sig", 0.99, ">=", 15)
        self.assertTrue(s.passes(1.0))
        self.assertTrue(s.passes(0.99))
        self.assertFalse(s.passes(0.98))

    def test_passes_lte_below_threshold(self):
        from slo.spec import SLOSpec
        s = SLOSpec("s", "S", "", "sig", 60_000.0, "<=", 60)
        self.assertTrue(s.passes(59_999.0))
        self.assertTrue(s.passes(60_000.0))
        self.assertFalse(s.passes(60_001.0))

    def test_unsupported_comparison_raises(self):
        from slo.spec import SLOSpec
        s = SLOSpec("s", "S", "", "sig", 0.5, "==", 15)
        with self.assertRaises(ValueError):
            s.passes(0.5)

    def test_by_name_returns_correct_spec(self):
        from slo.spec import by_name
        spec = by_name("ingest_acceptance")
        self.assertEqual(spec.name, "ingest_acceptance")
        self.assertEqual(spec.comparison, ">=")

    def test_by_name_raises_for_unknown(self):
        from slo.spec import by_name
        with self.assertRaises(KeyError):
            by_name("no_such_slo")

    def test_all_catalog_slos_have_valid_comparisons(self):
        from slo.spec import SLOS
        for s in SLOS:
            self.assertIn(s.comparison, ("<=", ">="), f"{s.name} has bad comparison")

    def test_catalog_has_expected_slo_names(self):
        from slo.spec import SLOS
        names = {s.name for s in SLOS}
        expected = {
            "ingest_acceptance", "flush_success",
            "reconstruction_lag_p95", "reconstruction_lag_p99",
            "trace_completion", "api_latency_p95",
        }
        self.assertEqual(names, expected)


if __name__ == "__main__":
    unittest.main()
