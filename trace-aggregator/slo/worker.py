"""
SLO worker. Periodically evaluates the SLO catalog and writes results to
tracing.slo_status.

Run:
    python -m slo.worker             # loop forever (default)
    python -m slo.worker --once      # single evaluation, useful for cron
"""
from __future__ import annotations

import argparse
import logging
import os
import time
from typing import List

import clickhouse_connect

from .evaluator import SLOStatus, evaluate_all

_logger = logging.getLogger("slo.worker")
POLL_INTERVAL_SEC = float(os.environ.get("SLO_POLL_INTERVAL_SEC", "60"))
CLICKHOUSE_HOST = os.environ.get("CLICKHOUSE_HOST", "localhost")
CLICKHOUSE_PORT = int(os.environ.get("CLICKHOUSE_PORT", "8123"))
CLICKHOUSE_USER = os.environ.get("CLICKHOUSE_USER", "default")
CLICKHOUSE_PASSWORD = os.environ.get("CLICKHOUSE_PASSWORD", "")


def _connect():
    return clickhouse_connect.get_client(host=CLICKHOUSE_HOST, port=CLICKHOUSE_PORT, username=CLICKHOUSE_USER, password=CLICKHOUSE_PASSWORD)


def _persist(client, statuses: List[SLOStatus]) -> None:
    if not statuses:
        return
    rows = [
        (
            s.name,
            s.title,
            s.signal,
            int(s.window_minutes),
            float(s.threshold),
            s.comparison,
            float(s.value),
            1 if s.passing else 0,
            int(s.sample_count),
            s.notes or "",
        )
        for s in statuses
    ]
    client.insert(
        "tracing.slo_status",
        rows,
        column_names=[
            "slo_name", "title", "signal", "window_minutes",
            "threshold", "comparison", "value", "passing",
            "sample_count", "notes",
        ],
    )


def run_once(client) -> List[SLOStatus]:
    statuses = evaluate_all(client)
    _persist(client, statuses)
    for s in statuses:
        verdict = "PASS" if s.passing else "FAIL"
        _logger.info(
            "%s %-28s value=%.4f %s threshold=%.4f samples=%d %s",
            verdict, s.name, s.value, s.comparison, s.threshold, s.sample_count, s.notes,
        )
    return statuses


def run_loop() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s | %(message)s")
    client = _connect()
    _logger.info("SLO worker started. interval=%.1fs", POLL_INTERVAL_SEC)
    while True:
        try:
            run_once(client)
        except Exception as e:  # noqa: BLE001
            _logger.exception("SLO loop error: %s", e)
            try:
                client = _connect()
            except Exception:
                pass
        time.sleep(POLL_INTERVAL_SEC)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Evaluate once and exit (cron-friendly).")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s | %(message)s")
    if args.once:
        client = _connect()
        run_once(client)
    else:
        run_loop()


if __name__ == "__main__":
    main()
