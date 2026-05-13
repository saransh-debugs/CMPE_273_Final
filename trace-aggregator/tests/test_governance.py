from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from db.init_db import SCHEMA_RAW_SPANS, governance_migration_statements
from shared.governance import normalize_metadata_payload


class TestGovernancePolicy(unittest.TestCase):
    def test_metadata_allowlist_and_redaction(self) -> None:
        payload = {
            "stage": "reviewer_entry",
            "mode": "demo",
            "reasoning": "Contact user@example.com or use Bearer sk-abc123",
            "details": {
                "api_key": "sk-live-123",
                "note": "all good",
            },
            "coverage_point": "reviewer_route",
            "custom": "drop-me",
        }

        with patch.dict(
            os.environ,
            {"TRACE_METADATA_ALLOWLIST": "stage,mode,reasoning,details,coverage_point"},
            clear=False,
        ):
            normalized = normalize_metadata_payload(payload)

        self.assertEqual(normalized["stage"], "reviewer_entry")
        self.assertEqual(normalized["mode"], "demo")
        self.assertEqual(normalized["coverage_point"], "reviewer_route")
        self.assertNotIn("custom", normalized)
        self.assertEqual(normalized["details"]["api_key"], "[REDACTED]")
        self.assertIn("[EMAIL]", normalized["reasoning"])
        self.assertIn("[BEARER]", normalized["reasoning"])

    def test_invalid_metadata_string_is_wrapped(self) -> None:
        with patch.dict(os.environ, {"TRACE_METADATA_ALLOWLIST": "stage,mode"}, clear=False):
            normalized = normalize_metadata_payload("not-json")
        self.assertIn("raw_metadata", normalized)

    def test_retention_migrations_include_ttl(self) -> None:
        stmts = governance_migration_statements()
        self.assertEqual(len(stmts), 7)
        self.assertTrue(all("MODIFY TTL" in stmt for stmt in stmts))
        self.assertIn("TTL ingested_at + INTERVAL", SCHEMA_RAW_SPANS)


if __name__ == "__main__":
    unittest.main()
