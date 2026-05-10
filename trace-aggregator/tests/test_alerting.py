"""
Tests for alerting/worker.py — mocks ClickHouse; no live DB required.

Run:
    python -m alerting.tests
"""
from __future__ import annotations

import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock


def _mock_query(rows):
    return MagicMock(result_rows=rows)


def _client_returning(*row_sets):
    client = MagicMock()
    client.query.side_effect = [_mock_query(rs) for rs in row_sets]
    return client


class TestRunawayTokens(unittest.TestCase):
    def test_empty_rows_no_alerts(self):
        from alerting.worker import _rule_runaway_tokens
        alerts = _rule_runaway_tokens(_client_returning([]))
        self.assertEqual(alerts, [])

    def test_single_runaway_trace_generates_alert(self):
        from alerting.worker import _rule_runaway_tokens
        alerts = _rule_runaway_tokens(_client_returning([("trace-xyz", 9999)]))
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].alert_type, "runaway_tokens")
        self.assertIn("trace-xyz", alerts[0].message)
        self.assertEqual(alerts[0].severity, "high")
        self.assertEqual(alerts[0].key, "trace:trace-xyz")

    def test_multiple_runaway_traces(self):
        from alerting.worker import _rule_runaway_tokens
        alerts = _rule_runaway_tokens(_client_returning([("t1", 5000), ("t2", 6000)]))
        self.assertEqual(len(alerts), 2)
        keys = {a.key for a in alerts}
        self.assertIn("trace:t1", keys)
        self.assertIn("trace:t2", keys)

    def test_details_include_trace_id_and_tokens(self):
        from alerting.worker import _rule_runaway_tokens
        alerts = _rule_runaway_tokens(_client_returning([("my-trace", 5500)]))
        details = alerts[0].details
        self.assertEqual(details["trace_id"], "my-trace")
        self.assertEqual(details["total_tokens"], 5500)


class TestErrorBurst(unittest.TestCase):
    def _span_rows(self, entries):
        now = datetime.now(timezone.utc)
        return [(agent_id, event_type, ikey, now) for agent_id, event_type, ikey in entries]

    def test_no_errors_no_alert(self):
        from alerting.worker import _rule_error_burst
        rows = self._span_rows([("agent-a", "llm_call", "k1"), ("agent-a", "llm_call", "k2")])
        alerts = _rule_error_burst(_client_returning(rows))
        self.assertEqual(alerts, [])

    def test_burst_at_threshold_triggers_alert(self):
        from alerting.worker import _rule_error_burst, ERROR_BURST_THRESHOLD
        rows = self._span_rows(
            [("bad-agent", "error", f"k{i}") for i in range(ERROR_BURST_THRESHOLD)]
        )
        alerts = _rule_error_burst(_client_returning(rows))
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].alert_type, "error_burst")
        self.assertEqual(alerts[0].details["agent_id"], "bad-agent")
        self.assertEqual(alerts[0].severity, "medium")

    def test_deduplication_by_idempotency_key(self):
        from alerting.worker import _rule_error_burst, ERROR_BURST_THRESHOLD
        rows = self._span_rows(
            [("dup-agent", "error", "same-key")] * (ERROR_BURST_THRESHOLD + 5)
        )
        alerts = _rule_error_burst(_client_returning(rows))
        self.assertEqual(alerts, [])

    def test_below_threshold_no_alert(self):
        from alerting.worker import _rule_error_burst, ERROR_BURST_THRESHOLD
        rows = self._span_rows(
            [("agent-x", "error", f"key{i}") for i in range(ERROR_BURST_THRESHOLD - 1)]
        )
        alerts = _rule_error_burst(_client_returning(rows))
        self.assertEqual(alerts, [])

    def test_mixed_event_types_only_errors_counted(self):
        from alerting.worker import _rule_error_burst, ERROR_BURST_THRESHOLD
        rows = self._span_rows(
            [("mixed-agent", "llm_call", f"ok{i}") for i in range(10)]
            + [("mixed-agent", "error", f"err{i}") for i in range(ERROR_BURST_THRESHOLD)]
        )
        alerts = _rule_error_burst(_client_returning(rows))
        agent_alert = next((a for a in alerts if a.details["agent_id"] == "mixed-agent"), None)
        self.assertIsNotNone(agent_alert)
        self.assertEqual(agent_alert.details["errors"], ERROR_BURST_THRESHOLD)


class TestStuckTraces(unittest.TestCase):
    def test_reconstructed_trace_not_stuck(self):
        from alerting.worker import _rule_stuck_traces
        old = datetime.now() - timedelta(minutes=20)
        client = MagicMock()
        client.query.side_effect = [
            _mock_query([("trace-ok", old, "k1")]),
            _mock_query([("trace-ok", datetime.now())]),
        ]
        self.assertEqual(_rule_stuck_traces(client), [])

    def test_old_unreconstructed_trace_triggers_alert(self):
        from alerting.worker import _rule_stuck_traces, STUCK_TRACE_MINUTES
        old = datetime.now() - timedelta(minutes=STUCK_TRACE_MINUTES + 5)
        client = MagicMock()
        client.query.side_effect = [
            _mock_query([("stuck-trace", old, "key-stuck")]),
            _mock_query([]),
        ]
        alerts = _rule_stuck_traces(client)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].alert_type, "stuck_trace")
        self.assertIn("stuck-trace", alerts[0].key)

    def test_fresh_unreconstructed_trace_not_stuck(self):
        from alerting.worker import _rule_stuck_traces
        recent = datetime.now() - timedelta(seconds=30)
        client = MagicMock()
        client.query.side_effect = [
            _mock_query([("new-trace", recent, "k1")]),
            _mock_query([]),
        ]
        self.assertEqual(_rule_stuck_traces(client), [])

    def test_dedup_by_idempotency_key_for_same_trace(self):
        from alerting.worker import _rule_stuck_traces, STUCK_TRACE_MINUTES
        old = datetime.now() - timedelta(minutes=STUCK_TRACE_MINUTES + 10)
        client = MagicMock()
        client.query.side_effect = [
            _mock_query([
                ("trace-a", old, "same-ikey"),
                ("trace-a", old, "same-ikey"),
            ]),
            _mock_query([]),
        ]
        alerts = _rule_stuck_traces(client)
        self.assertEqual(len(alerts), 1)


class TestSLOBreach(unittest.TestCase):
    def _slo_row(self, slo_name, title, value, passing):
        return (slo_name, title, value, int(passing), 0.999, ">=", datetime.now(timezone.utc))

    def test_all_passing_no_alerts(self):
        from alerting.worker import _rule_slo_breach
        rows = [self._slo_row("ingest_acceptance", "Ingest", 0.999, True)] * 5
        alerts = _rule_slo_breach(_client_returning(rows))
        self.assertEqual(alerts, [])

    def test_k_of_n_breach_triggers_alert(self):
        from alerting.worker import _rule_slo_breach, SLO_BREACH_THRESHOLD
        rows = [self._slo_row("ingest_acceptance", "Ingest", 0.5, False)] * SLO_BREACH_THRESHOLD
        alerts = _rule_slo_breach(_client_returning(rows))
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].alert_type, "slo_breach")
        self.assertEqual(alerts[0].severity, "high")
        self.assertIn("ingest_acceptance", alerts[0].key)

    def test_below_k_no_alert(self):
        from alerting.worker import _rule_slo_breach, SLO_BREACH_THRESHOLD
        rows = [self._slo_row("ingest_acceptance", "Ingest", 0.5, False)] * (SLO_BREACH_THRESHOLD - 1)
        alerts = _rule_slo_breach(_client_returning(rows))
        self.assertEqual(alerts, [])

    def test_missing_table_returns_empty(self):
        from alerting.worker import _rule_slo_breach
        client = MagicMock()
        client.query.side_effect = Exception("Table doesn't exist")
        self.assertEqual(_rule_slo_breach(client), [])

    def test_mixed_pass_fail_below_k_no_alert(self):
        from alerting.worker import _rule_slo_breach, SLO_BREACH_THRESHOLD
        rows = (
            [self._slo_row("flush_success", "Flush", 0.99, True)] * 3
            + [self._slo_row("flush_success", "Flush", 0.5, False)] * (SLO_BREACH_THRESHOLD - 1)
        )
        alerts = _rule_slo_breach(_client_returning(rows))
        self.assertEqual(alerts, [])


class TestCooldownLogic(unittest.TestCase):
    def test_first_fire_allowed(self):
        last_sent = {}
        now = time.time()

        def should_send(alert_type, key, cooldown=300):
            dk = f"{alert_type}:{key}"
            if now - last_sent.get(dk, 0.0) < cooldown:
                return False
            last_sent[dk] = now
            return True

        self.assertTrue(should_send("runaway_tokens", "trace:t1"))

    def test_second_fire_within_cooldown_suppressed(self):
        last_sent = {}
        now = time.time()

        def should_send(alert_type, key, cooldown=300):
            dk = f"{alert_type}:{key}"
            if now - last_sent.get(dk, 0.0) < cooldown:
                return False
            last_sent[dk] = now
            return True

        should_send("runaway_tokens", "trace:t1")
        self.assertFalse(should_send("runaway_tokens", "trace:t1"))

    def test_different_keys_not_suppressed(self):
        last_sent = {}
        now = time.time()

        def should_send(alert_type, key, cooldown=300):
            dk = f"{alert_type}:{key}"
            if now - last_sent.get(dk, 0.0) < cooldown:
                return False
            last_sent[dk] = now
            return True

        should_send("runaway_tokens", "trace:t1")
        self.assertTrue(should_send("runaway_tokens", "trace:t2"))

    def test_expired_cooldown_allows_re_fire(self):
        last_sent = {"runaway_tokens:trace:t1": time.time() - 400}
        now = time.time()

        dk = "runaway_tokens:trace:t1"
        is_suppressed = (now - last_sent.get(dk, 0.0)) < 300
        self.assertFalse(is_suppressed)


class TestSendWebhook(unittest.TestCase):
    def test_empty_url_is_noop(self):
        import alerting.worker as aw
        orig = aw.WEBHOOK_URL
        aw.WEBHOOK_URL = ""
        try:
            aw._send_webhook({"type": "test"})
        finally:
            aw.WEBHOOK_URL = orig


def main():
    print("Running alerting/worker tests:")
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [
        TestRunawayTokens, TestErrorBurst, TestStuckTraces,
        TestSLOBreach, TestCooldownLogic, TestSendWebhook,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
