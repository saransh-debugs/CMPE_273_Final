"""Replay WAL files directly into ClickHouse.

Usage:
    python -m collector.replay_wal

This scans the configured WAL directory (TRACE_WAL_DIR or ./wal) and
attempts to insert any discovered JSON rows into ClickHouse. On successful
insert the WAL file is removed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable

import clickhouse_connect


def _client():
    return clickhouse_connect.get_client(host=os.environ.get("CLICKHOUSE_HOST", "localhost"), port=int(os.environ.get("CLICKHOUSE_PORT", "8123")), username="default", password="")


def _wal_base(item_name: str) -> Path:
    return Path(os.environ.get("TRACE_WAL_DIR", "./wal")) / item_name


def replay_dir(item_name: str, table: str, columns: Iterable[str]) -> None:
    d = _wal_base(item_name)
    if not d.exists():
        print(f"no WAL dir for {item_name}: {d}")
        return
    client = _client()
    files = sorted(d.glob("*.json"), key=lambda p: p.name)
    for p in files:
        try:
            with open(p, "r", encoding="utf-8") as f:
                obj = json.load(f)
                row = tuple(obj.get("row", []))
        except Exception as e:
            print(f"failed to load {p}: {e}")
            continue
        try:
            client.insert(table, [row], column_names=list(columns))
            p.unlink()
            print(f"replayed and removed {p}")
        except Exception as e:
            print(f"failed to insert {p}: {e}")


def main() -> None:
    # Default: replay spans and decisions
    replay_dir("span", "tracing.raw_spans", [
        "start_time_ms",
        "trace_id",
        "span_id",
        "parent_span_id",
        "agent_id",
        "vector_clock",
        "event_type",
        "input_tokens",
        "output_tokens",
        "latency_ms",
        "metadata",
    ])
    replay_dir("decision", "tracing.raw_decisions", [
        "timestamp_ms",
        "trace_id",
        "decision_id",
        "source_span_id",
        "actor_agent_id",
        "decision_type",
        "selected_candidate_id",
        "confidence",
        "rationale_summary",
        "evidence_refs",
        "candidates_json",
        "metadata",
    ])


if __name__ == "__main__":
    main()
