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


if __name__ == "__main__":
    unittest.main()
