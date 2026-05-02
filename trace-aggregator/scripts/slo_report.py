#!/usr/bin/env python
"""
Print SLO status to stdout.

Usage:
    python scripts/slo_report.py            # evaluate full SLO catalog
    python scripts/slo_report.py --json     # JSON output for piping
    python scripts/slo_report.py --persist  # also write to tracing.slo_status
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running as `python scripts/slo_report.py` from the trace-aggregator
# root: prepend the project root so `import slo` resolves.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from slo.evaluator import evaluate_all  # noqa: E402
from slo.worker import _connect, _persist  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--persist", action="store_true")
    args = parser.parse_args()

    try:
        client = _connect()
    except Exception as e:  # noqa: BLE001
        print(f"clickhouse_unreachable={e}", file=sys.stderr)
        print("hint=Start ClickHouse first (docker compose up -d)", file=sys.stderr)
        sys.exit(2)

    statuses = evaluate_all(client)

    if args.persist:
        _persist(client, statuses)

    if args.json:
        print(json.dumps([s.as_dict() for s in statuses], default=str, indent=2))
        return

    overall_pass = all(s.passing for s in statuses)
    width = max(len(s.name) for s in statuses) if statuses else 12
    print(f"{'name':<{width}}  status  value           threshold       samples  notes")
    for s in statuses:
        verdict = "PASS" if s.passing else "FAIL"
        unit = f" {s.unit}" if s.unit and s.unit != "ratio" else ""
        print(
            f"{s.name:<{width}}  {verdict:<6}  "
            f"{s.value:>12.4f}{unit:<3}  {s.comparison} {s.threshold:>10.4f}  "
            f"{s.sample_count:>7}  {s.notes}"
        )
    print()
    print(f"overall={'PASS' if overall_pass else 'FAIL'}")
    if not overall_pass:
        sys.exit(1)


if __name__ == "__main__":
    main()
