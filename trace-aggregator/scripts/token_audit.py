"""
LLM-08: Token accuracy audit.

Compares reported input_tokens on spans against a word-count estimate of
the captured input_text. Flags spans where the discrepancy exceeds a
configurable threshold.

Usage:
    python scripts/token_audit.py [--threshold PCT] [--limit N] [--mock] [--json]

Exit code 0 = no spans flagged, 1 = flagged spans found.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _word_count(text: str) -> int:
    return max(1, len(text.split()))


def audit_spans(
    spans: list,
    *,
    threshold_pct: float = 10.0,
    count_fn=None,
) -> dict:
    if count_fn is None:
        count_fn = _word_count

    flagged = []
    skipped = 0
    discrepancies: list[float] = []

    for span in spans:
        reported = int(span.get("input_tokens", 0))
        raw_meta = span.get("metadata", "{}")
        try:
            meta = json.loads(raw_meta) if isinstance(raw_meta, str) else raw_meta
        except Exception:
            meta = {}

        input_text = meta.get("input_text", "")
        if not input_text or reported == 0:
            skipped += 1
            continue

        counted = count_fn(input_text)
        pct = abs(reported - counted) / max(reported, 1) * 100
        discrepancies.append(pct)
        if pct > threshold_pct:
            flagged.append(
                {
                    "span_id": span.get("span_id", "?"),
                    "agent_id": span.get("agent_id", "?"),
                    "reported": reported,
                    "counted": counted,
                    "discrepancy_pct": round(pct, 2),
                }
            )

    discrepancies.sort()
    n = len(discrepancies)
    p50 = discrepancies[n // 2] if n else 0.0
    p95 = discrepancies[min(int(n * 0.95), n - 1)] if n else 0.0

    return {
        "total": len(spans),
        "audited": n,
        "skipped": skipped,
        "flagged": len(flagged),
        "p50_discrepancy_pct": round(p50, 2),
        "p95_discrepancy_pct": round(p95, 2),
        "flagged_spans": flagged,
    }


def _fetch_from_ch(limit: int) -> list:
    import clickhouse_connect

    client = clickhouse_connect.get_client(
        host=os.environ.get("CLICKHOUSE_HOST", "localhost"),
        port=int(os.environ.get("CLICKHOUSE_PORT", "8123")),
        username=os.environ.get("CLICKHOUSE_USER", "default"),
        password=os.environ.get("CLICKHOUSE_PASSWORD", ""),
    )
    rows = client.query(
        f"SELECT span_id, agent_id, input_tokens, metadata "
        f"FROM tracing.raw_spans "
        f"WHERE event_type = 'llm_call' AND input_tokens > 0 "
        f"ORDER BY start_time_ms DESC LIMIT {limit}"
    ).result_rows
    return [
        {"span_id": r[0], "agent_id": r[1], "input_tokens": r[2], "metadata": r[3]}
        for r in rows
    ]


_MOCK_SPANS = [
    {
        "span_id": "s1",
        "agent_id": "research_agent",
        "input_tokens": 10,
        "metadata": json.dumps({"input_text": "hello world"}),
    },
    {
        "span_id": "s2",
        "agent_id": "coder_agent",
        "input_tokens": 4,
        "metadata": json.dumps({"input_text": "write a function"}),
    },
    {
        "span_id": "s3",
        "agent_id": "orchestrator",
        "input_tokens": 0,
        "metadata": "{}",
    },
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Token accuracy audit")
    parser.add_argument(
        "--threshold", type=float, default=10.0,
        help="Discrepancy %% above which a span is flagged (default 10)",
    )
    parser.add_argument("--limit", type=int, default=500, help="Max spans to audit")
    parser.add_argument("--mock", action="store_true", help="Use mock data (no ClickHouse)")
    parser.add_argument("--json", action="store_true", dest="as_json", help="JSON output")
    args = parser.parse_args()

    if args.mock:
        spans = _MOCK_SPANS
    else:
        try:
            spans = _fetch_from_ch(args.limit)
        except Exception as exc:
            print(f"ClickHouse error: {exc}\nRun with --mock for offline testing.", file=sys.stderr)
            sys.exit(1)

    result = audit_spans(spans, threshold_pct=args.threshold)

    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        print("Token Accuracy Audit")
        print(f"  Total spans : {result['total']}")
        print(f"  Audited     : {result['audited']}")
        print(f"  Skipped     : {result['skipped']} (no input_text or zero tokens)")
        print(f"  Flagged     : {result['flagged']} (>{args.threshold}% discrepancy)")
        print(f"  p50 discrepancy: {result['p50_discrepancy_pct']}%")
        print(f"  p95 discrepancy: {result['p95_discrepancy_pct']}%")
        if result["flagged_spans"]:
            print("\nFlagged spans:")
            for s in result["flagged_spans"][:20]:
                print(
                    f"  {s['span_id']} [{s['agent_id']}]  "
                    f"reported={s['reported']}  counted={s['counted']}  "
                    f"({s['discrepancy_pct']}%)"
                )

    sys.exit(1 if result["flagged"] > 0 else 0)


if __name__ == "__main__":
    main()
