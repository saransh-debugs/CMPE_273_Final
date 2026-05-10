"""API route tests with FastAPI TestClient (ClickHouse and SLO mocked)."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from api.main import app
from slo.evaluator import SLOStatus


class TestHealth(unittest.TestCase):
    def test_health_ok(self) -> None:
        mock_ch = MagicMock()
        with patch("api.main._client", return_value=mock_ch):
            client = TestClient(app)
            r = client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True})
        mock_ch.command.assert_called_once()

    def test_health_503_when_ch_fails(self) -> None:
        mock_ch = MagicMock()
        mock_ch.command.side_effect = RuntimeError("down")
        with patch("api.main._client", return_value=mock_ch):
            client = TestClient(app)
            r = client.get("/health")
        self.assertEqual(r.status_code, 503)


class TestSloEndpoint(unittest.TestCase):
    def test_slo_shape(self) -> None:
        status = SLOStatus(
            name="ingest_acceptance",
            title="Ingest acceptance rate",
            signal="ingest_acceptance",
            window_minutes=15,
            threshold=0.999,
            comparison=">=",
            unit="ratio",
            value=1.0,
            passing=True,
            sample_count=10,
            notes="",
        )
        mock_ch = MagicMock()
        mock_ch.query.return_value = MagicMock(result_rows=[])

        def eval_side_effect(_client_arg):
            return [status]

        with patch("api.main._client", return_value=mock_ch):
            with patch("slo.evaluator.evaluate_all", side_effect=eval_side_effect):
                client = TestClient(app)
                r = client.get("/slo")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("overall", body)
        self.assertIn("statuses", body)
        self.assertIn("history", body)
        self.assertEqual(len(body["statuses"]), 1)
        self.assertEqual(body["statuses"][0]["name"], "ingest_acceptance")
        self.assertEqual(body["overall"], "pass")
