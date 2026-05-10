"""Optional integration check when INTEGRATION=1 (live local ClickHouse)."""
from __future__ import annotations

import os
import unittest


@unittest.skipUnless(os.environ.get("INTEGRATION") == "1", "set INTEGRATION=1 for ClickHouse smoke")
class TestClickHousePing(unittest.TestCase):
    def test_select_one(self) -> None:
        import clickhouse_connect

        client = clickhouse_connect.get_client(
            host=os.environ.get("CLICKHOUSE_HOST", "localhost"),
            port=int(os.environ.get("CLICKHOUSE_PORT", "8123")),
            username=os.environ.get("CLICKHOUSE_USER", "default"),
            password=os.environ.get("CLICKHOUSE_PASSWORD", ""),
        )
        out = client.command("SELECT 1")
        self.assertEqual(int(out), 1)


@unittest.skipUnless(os.environ.get("INTEGRATION") == "1", "set INTEGRATION=1 for DB bootstrap smoke")
class TestInitDbSmoke(unittest.TestCase):
    def test_setup_idempotent(self) -> None:
        from db.init_db import setup

        setup()
