"""
LLM-04: Confidence calibration report.

Queries recent decisions and trace outcomes from ClickHouse, then checks
whether confidence scores correlate with observed success rates.

Usage:
    python scripts/calibration_report.py [--mock] [--json]

Exit code 0 = well_calibrated, 1 = miscalibrated.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sdk.calibration import calibrate


def _fetch_from_ch() -> tuple[list, dict]:
    import clickhouse_connect

    client = clickhouse_connect.get_client(
        host=os.environ.get("CLICKHOUSE_HOST", "localhost"),
        port=int(os.environ.get("CLICKHOUSE_PORT", "8123")),
        username=os.environ.get("CLICKHOUSE_USER", "default"),
        password=os.environ.get("CLICKHOUSE_PASSWORD", ""),
    )
    dec_rows = client.query(
        "SELECT trace_id, confidence FROM tracing.raw_decisions "
        "ORDER BY ingested_at DESC LIMIT 2000"
    ).result_rows
    decisions = [
        {"trace_id": r[0], "confidence": float(r[1])} for r in dec_rows
    ]
    trace_rows = client.query(
        "SELECT trace_id, error_count FROM tracing.reconstructed_traces "
        "ORDER BY reconstructed_at DESC LIMIT 2000"
    ).result_rows
    outcomes = {r[0]: (int(r[1]) == 0) for r in trace_rows}
    return decisions, outcomes


def main() -> None:
    parser = argparse.ArgumentParser(description="Confidence calibration report")
    parser.add_argument("--mock", action="store_true", help="Use mock data")
    parser.add_argument("--json", action="store_true", dest="as_json", help="JSON output")
    args = parser.parse_args()

    if args.mock:
        decisions = [{"trace_id": f"t{i}", "confidence": 0.8} for i in range(100)]
        outcomes = {f"t{i}": (i < 80) for i in range(100)}
    else:
        try:
            decisions, outcomes = _fetch_from_ch()
        except Exception as exc:
            print(f"ClickHouse error: {exc}\nRun with --mock for offline testing.", file=sys.stderr)
            sys.exit(1)

    report = calibrate(decisions, outcomes)

    if args.as_json:
        print(json.dumps(dataclasses.asdict(report), indent=2))
        sys.exit(0 if report.verdict == "well_calibrated" else 1)

    print("Confidence Calibration Report")
    print(f"  Overall calibration error : {report.overall_calibration_error:.4f}")
    print(f"  Verdict                   : {report.verdict.upper()}")
    print()
    for b in report.buckets:
        status = "OK  " if b.calibration_error < 0.1 else "WARN"
        print(
            f"  [{status}] {b.label:8s}  n={b.count:4d}  "
            f"mean_conf={b.mean_confidence:.3f}  "
            f"success_rate={b.observed_success_rate:.3f}  "
            f"error={b.calibration_error:.3f}"
        )

    sys.exit(0 if report.verdict == "well_calibrated" else 1)


if __name__ == "__main__":
    main()
