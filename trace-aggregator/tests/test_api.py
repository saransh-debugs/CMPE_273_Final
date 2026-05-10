"""
Tests for api/main.py — FastAPI endpoints, using TestClient with mocked ClickHouse.

Run:
    python -m api.tests
"""
from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch


def _mock_query(rows):
    return MagicMock(result_rows=rows)


def _patched_client(*query_results):
    client = MagicMock()
    client.query.side_effect = [_mock_query(r) for r in query_results]
    client.command.return_value = None
    return client


def _make_client_factory(*query_results):
    client = _patched_client(*query_results)
    return lambda: client


class TestIsoUtc(unittest.TestCase):
    def test_none_returns_empty_string(self):
        from api.main import _iso_utc
        self.assertEqual(_iso_utc(None), "")

    def test_naive_datetime_treated_as_utc(self):
        from api.main import _iso_utc
        dt = datetime(2026, 1, 1, 12, 0, 0)
        result = _iso_utc(dt)
        self.assertIn("2026-01-01", result)
        self.assertIn("Z", result)

    def test_aware_datetime_converted_to_utc_z(self):
        from api.main import _iso_utc
        dt = datetime(2026, 6, 15, 8, 30, 0, tzinfo=timezone.utc)
        result = _iso_utc(dt)
        self.assertTrue(result.endswith("Z"))
        self.assertIn("2026-06-15", result)


class TestHealthEndpoint(unittest.TestCase):
    def test_health_ok_when_clickhouse_up(self):
        from api.main import app
        from fastapi.testclient import TestClient
        with patch("api.main._client", _make_client_factory()):
            with TestClient(app) as tc:
                resp = tc.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])

    def test_health_503_when_clickhouse_down(self):
        from api.main import app
        from fastapi.testclient import TestClient
        failing_client = MagicMock()
        failing_client.command.side_effect = Exception("connection refused")
        with patch("api.main._client", lambda: failing_client):
            with TestClient(app, raise_server_exceptions=False) as tc:
                resp = tc.get("/health")
        self.assertEqual(resp.status_code, 503)


class TestListTraces(unittest.TestCase):
    def _trace_row(self, trace_id="t1", span_count=3, latency=1000,
                   in_tok=50, out_tok=100, errors=0, reconstructed_at=None, input_text=""):
        if reconstructed_at is None:
            reconstructed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        return (trace_id, span_count, latency, in_tok, out_tok, errors, reconstructed_at, input_text)

    def test_empty_returns_empty_list(self):
        from api.main import app
        from fastapi.testclient import TestClient
        with patch("api.main._client", _make_client_factory([])):
            with TestClient(app) as tc:
                resp = tc.get("/traces")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

    def test_returns_trace_fields(self):
        from api.main import app
        from fastapi.testclient import TestClient
        rows = [self._trace_row("trace-abc", span_count=5, errors=1)]
        with patch("api.main._client", _make_client_factory(rows)):
            with TestClient(app) as tc:
                resp = tc.get("/traces")
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["trace_id"], "trace-abc")
        self.assertEqual(data[0]["span_count"], 5)
        self.assertEqual(data[0]["error_count"], 1)

    def test_reconstructed_at_is_iso_z_format(self):
        from api.main import app
        from fastapi.testclient import TestClient
        rows = [self._trace_row()]
        with patch("api.main._client", _make_client_factory(rows)):
            with TestClient(app) as tc:
                resp = tc.get("/traces")
        self.assertTrue(resp.json()[0]["reconstructed_at"].endswith("Z"))

    def test_limit_param_accepted(self):
        from api.main import app
        from fastapi.testclient import TestClient
        with patch("api.main._client", _make_client_factory([])):
            with TestClient(app) as tc:
                resp = tc.get("/traces?limit=10")
        self.assertEqual(resp.status_code, 200)

    def test_limit_out_of_range_rejected(self):
        from api.main import app
        from fastapi.testclient import TestClient
        with patch("api.main._client", _make_client_factory([])):
            with TestClient(app) as tc:
                resp = tc.get("/traces?limit=0")
        self.assertEqual(resp.status_code, 422)


class TestGetTrace(unittest.TestCase):
    def _trace_row(self, trace_id="trace-1"):
        return (
            trace_id, 3, 500, 100, 200, 0,
            json.dumps([]),
            json.dumps([]),
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            "sample input",
        )

    def test_returns_trace_with_dag_and_blame(self):
        from api.main import app
        from fastapi.testclient import TestClient
        trace_rows = [self._trace_row("trace-1")]
        decision_rows = []
        with patch("api.main._client", _make_client_factory(trace_rows, decision_rows)):
            with TestClient(app) as tc:
                resp = tc.get("/traces/trace-1")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["trace_id"], "trace-1")
        self.assertIn("dag", body)
        self.assertIn("blame", body)
        self.assertIn("decisions", body)

    def test_unknown_trace_returns_404(self):
        from api.main import app
        from fastapi.testclient import TestClient
        with patch("api.main._client", _make_client_factory([], [])):
            with TestClient(app, raise_server_exceptions=False) as tc:
                resp = tc.get("/traces/nonexistent-trace")
        self.assertEqual(resp.status_code, 404)


class TestGetRawSpans(unittest.TestCase):
    def _span_row(self, span_id="s1", ikey="ikey1"):
        return (span_id, "", "agent-a", {"agent-a": 1}, "llm_call", 10, 20, 100, 1000, "{}", ikey,
                datetime(2026, 1, 1, tzinfo=timezone.utc))

    def test_returns_spans_for_trace(self):
        from api.main import app
        from fastapi.testclient import TestClient
        rows = [self._span_row("s1", "k1"), self._span_row("s2", "k2")]
        with patch("api.main._client", _make_client_factory(rows)):
            with TestClient(app) as tc:
                resp = tc.get("/traces/trace-1/spans")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["span_id"], "s1")

    def test_deduplication_by_idempotency_key(self):
        from api.main import app
        from fastapi.testclient import TestClient
        rows = [self._span_row("s1", "same-key"), self._span_row("s2", "same-key")]
        with patch("api.main._client", _make_client_factory(rows)):
            with TestClient(app) as tc:
                resp = tc.get("/traces/trace-1/spans")
        self.assertEqual(len(resp.json()), 1)

    def test_empty_trace_returns_empty_list(self):
        from api.main import app
        from fastapi.testclient import TestClient
        with patch("api.main._client", _make_client_factory([])):
            with TestClient(app) as tc:
                resp = tc.get("/traces/no-spans/spans")
        self.assertEqual(resp.json(), [])


class TestGetTraceDecisions(unittest.TestCase):
    def _decision_row(self, decision_id="d1", ikey="dk1"):
        return (
            "trace-1", decision_id, "s1", "orchestrator", "agent_handoff",
            "research", 0.9, "Route to research.", [], "[]",
            1000, "{}", ikey, datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    def test_returns_decisions(self):
        from api.main import app
        from fastapi.testclient import TestClient
        rows = [self._decision_row()]
        with patch("api.main._client", _make_client_factory(rows)):
            with TestClient(app) as tc:
                resp = tc.get("/traces/trace-1/decisions")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["decision_id"], "d1")
        self.assertAlmostEqual(data[0]["confidence"], 0.9)

    def test_deduplication_by_idempotency_key(self):
        from api.main import app
        from fastapi.testclient import TestClient
        rows = [self._decision_row("d1", "same-key"), self._decision_row("d2", "same-key")]
        with patch("api.main._client", _make_client_factory(rows)):
            with TestClient(app) as tc:
                resp = tc.get("/traces/trace-1/decisions")
        self.assertEqual(len(resp.json()), 1)

    def test_empty_decisions_returns_empty(self):
        from api.main import app
        from fastapi.testclient import TestClient
        with patch("api.main._client", _make_client_factory([])):
            with TestClient(app) as tc:
                resp = tc.get("/traces/no-decisions/decisions")
        self.assertEqual(resp.json(), [])


class TestAggregateBlame(unittest.TestCase):
    def _span_row(self, agent_id, latency, in_tok, out_tok, event_type, ikey):
        return (agent_id, latency, in_tok, out_tok, event_type, ikey,
                datetime(2026, 1, 1, tzinfo=timezone.utc))

    def test_aggregates_by_agent(self):
        from api.main import app
        from fastapi.testclient import TestClient
        rows = [
            self._span_row("agent-a", 100, 10, 20, "llm_call", "k1"),
            self._span_row("agent-a", 200, 5, 10, "llm_call", "k2"),
            self._span_row("agent-b", 50, 2, 4, "error", "k3"),
        ]
        with patch("api.main._client", _make_client_factory(rows)):
            with TestClient(app) as tc:
                resp = tc.get("/agents/blame")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        by_agent = {r["agent_id"]: r for r in data}
        self.assertEqual(by_agent["agent-a"]["spans"], 2)
        self.assertEqual(by_agent["agent-a"]["total_latency_ms"], 300)
        self.assertEqual(by_agent["agent-b"]["error_count"], 1)

    def test_deduplication_by_idempotency_key(self):
        from api.main import app
        from fastapi.testclient import TestClient
        rows = [
            self._span_row("agent-a", 100, 10, 20, "llm_call", "same-key"),
            self._span_row("agent-a", 100, 10, 20, "llm_call", "same-key"),
        ]
        with patch("api.main._client", _make_client_factory(rows)):
            with TestClient(app) as tc:
                resp = tc.get("/agents/blame")
        data = resp.json()
        self.assertEqual(data[0]["spans"], 1)

    def test_empty_spans_returns_empty(self):
        from api.main import app
        from fastapi.testclient import TestClient
        with patch("api.main._client", _make_client_factory([])):
            with TestClient(app) as tc:
                resp = tc.get("/agents/blame")
        self.assertEqual(resp.json(), [])


def main():
    print("Running api/main tests:")
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [
        TestIsoUtc, TestHealthEndpoint, TestListTraces,
        TestGetTrace, TestGetRawSpans, TestGetTraceDecisions, TestAggregateBlame,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
