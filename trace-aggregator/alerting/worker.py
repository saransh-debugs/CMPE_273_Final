"""
Alerting worker for causal trace incidents.

Rules:
  - stuck agents / stale unreconstructed traces
  - runaway token usage
  - repeated errors by agent

Run:
    python -m alerting.worker
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Dict, List

import clickhouse_connect

_logger = logging.getLogger("alerting.worker")

POLL_INTERVAL_SEC = float(os.environ.get("ALERT_POLL_INTERVAL_SEC", "15"))
LOOKBACK_MIN = int(os.environ.get("ALERT_LOOKBACK_MIN", "15"))
COOLDOWN_SEC = int(os.environ.get("ALERT_COOLDOWN_SEC", "300"))
RUNAWAY_TOKENS_THRESHOLD = int(os.environ.get("ALERT_RUNAWAY_TOKENS", "4000"))
ERROR_BURST_THRESHOLD = int(os.environ.get("ALERT_ERROR_BURST", "3"))
STUCK_TRACE_MINUTES = int(os.environ.get("ALERT_STUCK_TRACE_MIN", "5"))
WEBHOOK_URL = os.environ.get("ALERT_WEBHOOK_URL", "").strip()


@dataclass
class Alert:
    alert_type: str
    key: str
    severity: str
    message: str
    details: Dict[str, object]


def _connect():
    return clickhouse_connect.get_client(host="localhost", port=8123, username="default", password="")


def _send_webhook(payload: Dict[str, object]) -> None:
    if not WEBHOOK_URL:
        return
    try:
        import urllib.request

        req = urllib.request.Request(
            WEBHOOK_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3):
            pass
    except Exception as e:  # noqa: BLE001
        _logger.warning("Alert webhook failed: %s", e)


def _rule_runaway_tokens(client) -> List[Alert]:
    rows = client.query(
        f"""
        SELECT trace_id, total_input_tokens + total_output_tokens AS total_tokens
        FROM tracing.reconstructed_traces FINAL
        WHERE reconstructed_at > now() - INTERVAL {LOOKBACK_MIN} MINUTE
          AND (total_input_tokens + total_output_tokens) >= {RUNAWAY_TOKENS_THRESHOLD}
        ORDER BY total_tokens DESC
        """
    ).result_rows
    return [
        Alert(
            alert_type="runaway_tokens",
            key=f"trace:{r[0]}",
            severity="high",
            message=f"Trace {r[0]} exceeded token threshold with {int(r[1])} tokens.",
            details={"trace_id": r[0], "total_tokens": int(r[1])},
        )
        for r in rows
    ]


def _rule_error_burst(client) -> List[Alert]:
    rows = client.query(
        f"""
        SELECT agent_id, countIf(event_type = 'error') AS errors
        FROM tracing.raw_spans
        WHERE ingested_at > now() - INTERVAL {LOOKBACK_MIN} MINUTE
        GROUP BY agent_id
        HAVING errors >= {ERROR_BURST_THRESHOLD}
        ORDER BY errors DESC
        """
    ).result_rows
    return [
        Alert(
            alert_type="error_burst",
            key=f"agent:{r[0]}",
            severity="medium",
            message=f"Agent {r[0]} emitted {int(r[1])} errors in {LOOKBACK_MIN}m window.",
            details={"agent_id": r[0], "errors": int(r[1]), "window_min": LOOKBACK_MIN},
        )
        for r in rows
    ]


def _rule_stuck_traces(client) -> List[Alert]:
    rows = client.query(
        f"""
        SELECT rs.trace_id, max(rs.ingested_at) AS last_ingested
        FROM tracing.raw_spans rs
        LEFT JOIN tracing.reconstructed_traces rt
          ON rs.trace_id = rt.trace_id
        WHERE rs.ingested_at > now() - INTERVAL {LOOKBACK_MIN * 4} MINUTE
        GROUP BY rs.trace_id
        HAVING dateDiff('minute', last_ingested, now()) >= {STUCK_TRACE_MINUTES}
           AND maxOrNull(rt.reconstructed_at) IS NULL
        """
    ).result_rows
    return [
        Alert(
            alert_type="stuck_trace",
            key=f"trace:{r[0]}",
            severity="medium",
            message=f"Trace {r[0]} appears stuck without reconstruction for >= {STUCK_TRACE_MINUTES}m.",
            details={"trace_id": r[0], "last_ingested": str(r[1])},
        )
        for r in rows
    ]


def run_loop() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s | %(message)s")
    client = _connect()
    last_sent: Dict[str, float] = {}
    _logger.info("Alerting worker started. poll=%.1fs cooldown=%ss", POLL_INTERVAL_SEC, COOLDOWN_SEC)
    while True:
        try:
            alerts = []
            alerts.extend(_rule_runaway_tokens(client))
            alerts.extend(_rule_error_burst(client))
            alerts.extend(_rule_stuck_traces(client))
            now = time.time()
            for a in alerts:
                dedupe_key = f"{a.alert_type}:{a.key}"
                if now - last_sent.get(dedupe_key, 0.0) < COOLDOWN_SEC:
                    continue
                payload = {
                    "type": a.alert_type,
                    "severity": a.severity,
                    "message": a.message,
                    "details": a.details,
                    "timestamp_ms": int(now * 1000),
                }
                _logger.warning("ALERT %s | %s | %s", a.severity.upper(), a.alert_type, a.message)
                _send_webhook(payload)
                last_sent[dedupe_key] = now
        except Exception as e:  # noqa: BLE001
            _logger.exception("Alerting loop error: %s", e)
            try:
                client = _connect()
            except Exception:
                pass
        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    run_loop()

