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
        body = resp.json()
        self.assertEqual(body["items"], [])
        self.assertIsNone(body["next_cursor"])
        self.assertFalse(body["has_more"])

    def test_returns_trace_fields(self):
        from api.main import app
        from fastapi.testclient import TestClient
        rows = [self._trace_row("trace-abc", span_count=5, errors=1)]
        with patch("api.main._client", _make_client_factory(rows)):
            with TestClient(app) as tc:
                resp = tc.get("/traces")
        data = resp.json()["items"]
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
        self.assertTrue(resp.json()["items"][0]["reconstructed_at"].endswith("Z"))

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
            json.dumps([]),   # dag_json
            json.dumps([]),   # blame_json
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            "sample input",
            json.dumps([]),   # blame_v2_json (index 10 — added in ENG-06)
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
    def _blame_row(self, entries: list) -> tuple:
        """Return a single-column row as the endpoint reads: (blame_json_string,)."""
        return (json.dumps(entries),)

    def test_aggregates_by_agent(self):
        from api.main import app
        from fastapi.testclient import TestClient
        # Two traces; agent-a appears in both, agent-b in the second only.
        rows = [
            self._blame_row([
                {"agent_id": "agent-a", "blame_score": 60.0, "total_latency_ms": 100,
                 "total_input_tokens": 10, "total_output_tokens": 20, "error_count": 0},
            ]),
            self._blame_row([
                {"agent_id": "agent-a", "blame_score": 40.0, "total_latency_ms": 200,
                 "total_input_tokens": 5, "total_output_tokens": 10, "error_count": 0},
                {"agent_id": "agent-b", "blame_score": 80.0, "total_latency_ms": 50,
                 "total_input_tokens": 2, "total_output_tokens": 4, "error_count": 1},
            ]),
        ]
        with patch("api.main._client", _make_client_factory(rows)):
            with TestClient(app) as tc:
                resp = tc.get("/agents/blame")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        agents = data["agents"]
        by_agent = {r["agent_id"]: r for r in agents}
        self.assertEqual(by_agent["agent-a"]["trace_count"], 2)
        self.assertEqual(by_agent["agent-a"]["total_latency_ms"], 300)
        self.assertEqual(by_agent["agent-b"]["error_count"], 1)

    def test_same_agent_across_traces_summed(self):
        from api.main import app
        from fastapi.testclient import TestClient
        # Same agent in two separate trace blame_json rows → counts should sum.
        rows = [
            self._blame_row([
                {"agent_id": "agent-a", "blame_score": 50.0, "total_latency_ms": 100,
                 "total_input_tokens": 10, "total_output_tokens": 20, "error_count": 0},
            ]),
            self._blame_row([
                {"agent_id": "agent-a", "blame_score": 50.0, "total_latency_ms": 100,
                 "total_input_tokens": 10, "total_output_tokens": 20, "error_count": 0},
            ]),
        ]
        with patch("api.main._client", _make_client_factory(rows)):
            with TestClient(app) as tc:
                resp = tc.get("/agents/blame")
        agents = resp.json()["agents"]
        self.assertEqual(len(agents), 1)
        self.assertEqual(agents[0]["trace_count"], 2)
        self.assertEqual(agents[0]["total_latency_ms"], 200)

    def test_empty_returns_empty_agents(self):
        from api.main import app
        from fastapi.testclient import TestClient
        with patch("api.main._client", _make_client_factory([])):
            with TestClient(app) as tc:
                resp = tc.get("/agents/blame")
        body = resp.json()
        self.assertEqual(body["agents"], [])
        self.assertEqual(body["model_version"], "v1")


class TestIncidents(unittest.TestCase):
    def _incident_row(self, key="error_burst:agent:foo", state="open", count=1):
        return (
            key, "error_burst", state, "medium", "Test message", '{"agent_id": "foo"}',
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(1970, 1, 1, tzinfo=timezone.utc),
            datetime(1970, 1, 1, tzinfo=timezone.utc),
            count,
        )

    def test_list_incidents_empty(self):
        from api.main import app
        from fastapi.testclient import TestClient
        # list_incidents queries twice: list_incidents() + state_counts()
        with patch("api.main._client", _make_client_factory([], [])):
            with TestClient(app) as tc:
                resp = tc.get("/incidents")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["items"], [])
        self.assertEqual(body["counts"], {"open": 0, "ack": 0, "resolved": 0})

    def test_list_incidents_returns_items(self):
        from api.main import app
        from fastapi.testclient import TestClient
        rows = [self._incident_row("k1", "open", 3)]
        counts = [("open", 1), ("resolved", 5)]
        with patch("api.main._client", _make_client_factory(rows, counts)):
            with TestClient(app) as tc:
                resp = tc.get("/incidents?state=open")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(body["items"][0]["state"], "open")
        self.assertEqual(body["items"][0]["occurrence_count"], 3)
        self.assertEqual(body["counts"]["open"], 1)
        self.assertEqual(body["counts"]["resolved"], 5)

    def test_ack_unknown_returns_404(self):
        from api.main import app
        from fastapi.testclient import TestClient
        with patch("api.main._client", _make_client_factory([])):  # _fetch_latest returns []
            with TestClient(app, raise_server_exceptions=False) as tc:
                resp = tc.post("/incidents/missing/ack")
        self.assertEqual(resp.status_code, 404)

    def test_ack_existing_transitions_state(self):
        from api.main import app
        from fastapi.testclient import TestClient
        # acknowledge() does 1 SELECT then 1 INSERT
        with patch("api.main._client", _make_client_factory([self._incident_row("k1", "open")])):
            with TestClient(app) as tc:
                resp = tc.post("/incidents/k1/ack")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["state"], "ack")

    def test_resolve_existing(self):
        from api.main import app
        from fastapi.testclient import TestClient
        with patch("api.main._client", _make_client_factory([self._incident_row("k1", "open")])):
            with TestClient(app) as tc:
                resp = tc.post("/incidents/k1/resolve")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["state"], "resolved")
        self.assertEqual(body["details"]["resolved_reason"], "manual")


def main():
    print("Running api/main tests:")
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [
        TestIsoUtc, TestHealthEndpoint, TestListTraces,
        TestGetTrace, TestGetRawSpans, TestGetTraceDecisions, TestAggregateBlame,
        TestIncidents,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
