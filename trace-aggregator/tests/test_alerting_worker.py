"""Unit tests for alerting.worker rules (mock ClickHouse)."""
from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from alerting.worker import _rule_error_burst, _rule_slo_breach


class TestRuleSloBreach(unittest.TestCase):
    def test_fires_when_k_of_n_fail(self) -> None:
        client = MagicMock()
        now = datetime(2026, 1, 1, 12, 0, 0)
        rows = []
        for i in range(5):
            passing = 1 if i >= 3 else 0
            rows.append(
                ("reconstruction_lag_p95", "Lag", 90000.0, passing, 60000.0, "<=", now),
            )
        client.query.return_value = MagicMock(result_rows=rows)
        with patch("alerting.worker.SLO_BREACH_LOOKBACK", 5):
            with patch("alerting.worker.SLO_BREACH_THRESHOLD", 3):
                alerts = _rule_slo_breach(client)
        self.assertTrue(any(a.alert_type == "slo_breach" for a in alerts))

    def test_silent_when_failures_below_threshold(self) -> None:
        client = MagicMock()
        now = datetime(2026, 1, 1, 12, 0, 0)
        rows = [
            ("api_latency_p95", "API", 100.0, 0, 50.0, "<=", now),
            ("api_latency_p95", "API", 80.0, 1, 50.0, "<=", now),
            ("api_latency_p95", "API", 70.0, 1, 50.0, "<=", now),
        ]
        client.query.return_value = MagicMock(result_rows=rows)
        with patch("alerting.worker.SLO_BREACH_LOOKBACK", 5):
            with patch("alerting.worker.SLO_BREACH_THRESHOLD", 3):
                alerts = _rule_slo_breach(client)
        self.assertEqual([a for a in alerts if a.alert_type == "slo_breach"], [])


class TestRuleErrorBurst(unittest.TestCase):
    def test_dedupes_idempotency_key(self) -> None:
        client = MagicMock()
        t = datetime(2026, 1, 1, 12, 0, 0)
        client.query.return_value = MagicMock(
            result_rows=[
                ("agent-a", "error", "key-1", t),
                ("agent-a", "error", "key-1", t),
                ("agent-a", "error", "key-1", t),
            ]
        )
        with patch("alerting.worker.ERROR_BURST_THRESHOLD", 2):
            alerts = _rule_error_burst(client)
        self.assertEqual(alerts, [])

    def test_alert_when_unique_errors_exceed_threshold(self) -> None:
        client = MagicMock()
        t = datetime(2026, 1, 1, 12, 0, 0)
        client.query.return_value = MagicMock(
            result_rows=[
                ("agent-x", "error", "k1", t),
                ("agent-x", "error", "k2", t),
                ("agent-x", "error", "k3", t),
            ]
        )
        with patch("alerting.worker.ERROR_BURST_THRESHOLD", 3):
            alerts = _rule_error_burst(client)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].alert_type, "error_burst")
