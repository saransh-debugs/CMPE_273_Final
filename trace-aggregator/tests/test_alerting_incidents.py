"""Tests for the incidents state machine — mocks ClickHouse, no live DB."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from alerting.incidents import (
    Incident,
    STATE_ACK,
    STATE_OPEN,
    STATE_RESOLVED,
    acknowledge,
    auto_resolve_stale,
    record_alert,
    resolve,
)
from alerting.worker import Alert


def make_alert(alert_type="error_burst", key="agent:foo", severity="medium"):
    return Alert(
        alert_type=alert_type,
        key=key,
        severity=severity,
        message=f"Test alert for {key}",
        details={"agent_id": key.split(":")[-1], "errors": 5},
    )


def _row(state, last_seen_at=None, occurrence_count=1):
    """Build a mock row matching the SELECT order in incidents.py."""
    now = datetime.now(timezone.utc)
    return (
        "error_burst:agent:foo",       # incident_key
        "error_burst",                 # alert_type
        state,                         # state
        "medium",                      # severity
        "old message",                 # message
        '{"x": 1}',                    # details
        now - timedelta(minutes=5),    # opened_at
        last_seen_at or now,           # last_seen_at
        datetime(1970, 1, 1, tzinfo=timezone.utc),  # acknowledged_at
        datetime(1970, 1, 1, tzinfo=timezone.utc),  # resolved_at
        occurrence_count,              # occurrence_count
    )


class TestRecordAlert(unittest.TestCase):
    def test_new_alert_returns_new(self):
        client = MagicMock()
        client.query.return_value = MagicMock(result_rows=[])  # no existing
        result = record_alert(client, make_alert())
        self.assertEqual(result, "new")
        client.insert.assert_called_once()

    def test_repeat_alert_while_open_returns_existing(self):
        client = MagicMock()
        client.query.return_value = MagicMock(result_rows=[_row(STATE_OPEN, occurrence_count=2)])
        result = record_alert(client, make_alert())
        self.assertEqual(result, "existing")
        client.insert.assert_called_once()  # bumps count, no new page

    def test_repeat_alert_while_ack_returns_existing(self):
        client = MagicMock()
        client.query.return_value = MagicMock(result_rows=[_row(STATE_ACK, occurrence_count=4)])
        result = record_alert(client, make_alert())
        self.assertEqual(result, "existing")

    def test_alert_after_resolve_returns_reopened(self):
        client = MagicMock()
        client.query.return_value = MagicMock(result_rows=[_row(STATE_RESOLVED, occurrence_count=6)])
        result = record_alert(client, make_alert())
        self.assertEqual(result, "reopened")

    def test_occurrence_count_increments_on_existing(self):
        client = MagicMock()
        client.query.return_value = MagicMock(result_rows=[_row(STATE_OPEN, occurrence_count=3)])
        record_alert(client, make_alert())
        # Inspect the insert payload — column 10 is occurrence_count
        inserted_row = client.insert.call_args[0][1][0]
        self.assertEqual(inserted_row[10], 4)


class TestAcknowledge(unittest.TestCase):
    def test_ack_open_incident_transitions_to_ack(self):
        client = MagicMock()
        client.query.return_value = MagicMock(result_rows=[_row(STATE_OPEN)])
        result = acknowledge(client, "error_burst:agent:foo")
        self.assertIsNotNone(result)
        self.assertEqual(result.state, STATE_ACK)
        self.assertIsNotNone(result.acknowledged_at)

    def test_ack_resolved_is_noop(self):
        client = MagicMock()
        client.query.return_value = MagicMock(result_rows=[_row(STATE_RESOLVED)])
        result = acknowledge(client, "error_burst:agent:foo")
        self.assertEqual(result.state, STATE_RESOLVED)
        client.insert.assert_not_called()

    def test_ack_unknown_returns_none(self):
        client = MagicMock()
        client.query.return_value = MagicMock(result_rows=[])
        self.assertIsNone(acknowledge(client, "missing-key"))


class TestResolve(unittest.TestCase):
    def test_resolve_open_incident(self):
        client = MagicMock()
        client.query.return_value = MagicMock(result_rows=[_row(STATE_OPEN)])
        result = resolve(client, "error_burst:agent:foo")
        self.assertEqual(result.state, STATE_RESOLVED)
        self.assertEqual(result.details["resolved_reason"], "manual")

    def test_resolve_already_resolved_is_noop(self):
        client = MagicMock()
        client.query.return_value = MagicMock(result_rows=[_row(STATE_RESOLVED)])
        resolve(client, "error_burst:agent:foo")
        client.insert.assert_not_called()


class TestAutoResolveStale(unittest.TestCase):
    def test_no_stale_no_op(self):
        client = MagicMock()
        client.query.return_value = MagicMock(result_rows=[])
        self.assertEqual(auto_resolve_stale(client, 600), 0)
        client.insert.assert_not_called()

    def test_stale_incidents_are_resolved(self):
        client = MagicMock()
        # First query → stale keys. Subsequent queries → SELECTs done inside resolve().
        stale = [("error_burst:agent:foo",), ("error_burst:agent:bar",)]
        existing_row = _row(STATE_OPEN)
        client.query.side_effect = [
            MagicMock(result_rows=stale),                   # auto_resolve_stale's own SELECT
            MagicMock(result_rows=[existing_row]),          # _fetch_latest for foo
            MagicMock(result_rows=[existing_row]),          # _fetch_latest for bar
        ]
        n = auto_resolve_stale(client, 600)
        self.assertEqual(n, 2)
        self.assertEqual(client.insert.call_count, 2)


if __name__ == "__main__":
    unittest.main()
