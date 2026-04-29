#!/usr/bin/env python
"""
Compute causal tracing SLO metrics over a lookback window.

Usage:
    python scripts/slo_report.py --minutes 60
"""
from __future__ import annotations

import argparse
import statistics
from typing import List

import clickhouse_connect


def percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    k = (len(values) - 1) * p
    f = int(k)
    c = min(f + 1, len(values) - 1)
    if f == c:
        return values[f]
    return values[f] + (values[c] - values[f]) * (k - f)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=int, default=60)
    args = parser.parse_args()

    try:
        c = clickhouse_connect.get_client(host="localhost", port=8123, username="default", password="")
    except Exception as e:  # noqa: BLE001
        print(f"clickhouse_unreachable={e}")
        print("hint=Start ClickHouse first (docker compose up -d)")
        return
    
    # Get all raw spans in the window
    raw_spans = c.query(
        f"""
        SELECT
            trace_id,
            ingested_at,
            idempotency_key
        FROM tracing.raw_spans
        WHERE ingested_at > now() - INTERVAL {args.minutes} MINUTE
        ORDER BY ingested_at DESC
        """
    ).result_rows
    
    # Deduplicate by idempotency_key
    seen_keys = {}
    for trace_id, ingested_at, idempotency_key in raw_spans:
        dedup_key = idempotency_key if idempotency_key else f"span_{ingested_at}_{trace_id}"
        
        if dedup_key not in seen_keys:
            seen_keys[dedup_key] = (trace_id, ingested_at)
    
    # Get max ingested_at per trace
    trace_ingested = {}
    for trace_id, ingested_at in seen_keys.values():
        if trace_id not in trace_ingested:
            trace_ingested[trace_id] = ingested_at
        else:
            if ingested_at > trace_ingested[trace_id]:
                trace_ingested[trace_id] = ingested_at
    
    # Get reconstructed traces
    reconstructed_rows = c.query(
        "SELECT trace_id, reconstructed_at FROM tracing.reconstructed_traces"
    ).result_rows
    
    # Build result rows for processing
    rows = []
    reconstructed_map = {r[0]: r[1] for r in reconstructed_rows}
    
    for trace_id, ingested_at in trace_ingested.items():
        reconstructed_at = reconstructed_map.get(trace_id)
        rows.append((trace_id, ingested_at, reconstructed_at))

    lag_ms: List[float] = []
    invalid_lag = 0
    reconstructed = 0
    for _, ing, rec in rows:
        if rec is None:
            continue
        lag = (rec - ing).total_seconds() * 1000.0
        # Guard against stale/invalid join artifacts and clock skew outliers.
        if lag < 0:
            invalid_lag += 1
            continue
        reconstructed += 1
        lag_ms.append(lag)

    print(f"window_minutes={args.minutes}")
    print(f"trace_count={len(rows)}")
    print(f"reconstructed_count={reconstructed}")
    print(f"missing_reconstruction={len(rows) - reconstructed - invalid_lag}")
    print(f"invalid_negative_lag={invalid_lag}")
    if lag_ms:
        print(f"reconstruct_lag_ms_avg={statistics.mean(lag_ms):.2f}")
        print(f"reconstruct_lag_ms_p95={percentile(sorted(lag_ms), 0.95):.2f}")
        print(f"reconstruct_lag_ms_p99={percentile(sorted(lag_ms), 0.99):.2f}")
    else:
        print("reconstruct_lag_ms_avg=0.00")
        print("reconstruct_lag_ms_p95=0.00")
        print("reconstruct_lag_ms_p99=0.00")


if __name__ == "__main__":
    main()

