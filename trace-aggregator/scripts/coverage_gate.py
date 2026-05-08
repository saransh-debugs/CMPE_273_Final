"""
LLM-10: Decision coverage CI gate.

Checks that a trace (or set of recent traces) contains decisions for all
required coverage point IDs. Coverage points are embedded in decision
metadata as {"coverage_point": "<point_id>"}.

Usage:
    python scripts/coverage_gate.py --trace-id <id>
    python scripts/coverage_gate.py --recent 10
    python scripts/coverage_gate.py --mock

Exit code 0 = all points covered, 1 = missing points, 2 = error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DEFAULT_REQUIRED = {
    "orchestrator_dispatch",
    "research_tool_select",
    "coder_tool_select",
    "reviewer_route",
}


def check_coverage(decisions: list, *, required: set[str]) -> set[str]:
    covered: set[str] = set()
    for d in decisions:
        raw_meta = d.get("metadata", "{}")
        try:
            meta = json.loads(raw_meta) if isinstance(raw_meta, str) else raw_meta
        except Exception:
            meta = {}
        point = meta.get("coverage_point", "")
        if point:
            covered.add(point)
    return required - covered


def _fetch_decisions_for_trace(client, trace_id: str) -> list:
    rows = client.query(
        f"SELECT decision_id, metadata FROM tracing.raw_decisions "
        f"WHERE trace_id = '{trace_id}'"
    ).result_rows
    return [{"decision_id": r[0], "metadata": r[1]} for r in rows]


def _fetch_recent_trace_ids(client, n: int) -> list[str]:
    rows = client.query(
        f"SELECT DISTINCT trace_id FROM tracing.raw_decisions "
        f"ORDER BY ingested_at DESC LIMIT {n}"
    ).result_rows
    return [r[0] for r in rows]


def _get_client():
    import clickhouse_connect

    return clickhouse_connect.get_client(
        host=os.environ.get("CLICKHOUSE_HOST", "localhost"),
        port=int(os.environ.get("CLICKHOUSE_PORT", "8123")),
        username=os.environ.get("CLICKHOUSE_USER", "default"),
        password=os.environ.get("CLICKHOUSE_PASSWORD", ""),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Decision coverage CI gate")
    parser.add_argument("--trace-id", help="Single trace ID to check")
    parser.add_argument("--recent", type=int, default=0, help="Check last N traces")
    parser.add_argument("--mock", action="store_true", help="Use mock data (offline)")
    parser.add_argument(
        "--required", nargs="+", help="Override required coverage points"
    )
    args = parser.parse_args()

    required = set(args.required) if args.required else DEFAULT_REQUIRED

    if args.mock:
        decisions = [
            {"metadata": json.dumps({"coverage_point": p})} for p in required
        ]
        missing = check_coverage(decisions, required=required)
        if missing:
            print(f"FAIL  missing: {sorted(missing)}")
            sys.exit(1)
        print(f"PASS  all {len(required)} coverage points present (mock)")
        sys.exit(0)

    try:
        client = _get_client()

        if args.trace_id:
            trace_ids = [args.trace_id]
        elif args.recent:
            trace_ids = _fetch_recent_trace_ids(client, args.recent)
            if not trace_ids:
                print("No traces found.", file=sys.stderr)
                sys.exit(2)
        else:
            print(
                "Specify --trace-id <id>, --recent N, or --mock", file=sys.stderr
            )
            sys.exit(2)

        all_missing: dict[str, list[str]] = {}
        for tid in trace_ids:
            decisions = _fetch_decisions_for_trace(client, tid)
            missing = check_coverage(decisions, required=required)
            if missing:
                all_missing[tid] = sorted(missing)

        if all_missing:
            for tid, missing in all_missing.items():
                print(f"FAIL  {tid}  missing: {missing}")
            sys.exit(1)

        print(f"PASS  {len(trace_ids)} trace(s) — all coverage points present")
        sys.exit(0)

    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
