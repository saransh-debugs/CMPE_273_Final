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
from datetime import datetime
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
        SELECT
            agent_id,
            event_type,
            idempotency_key,
            ingested_at
        FROM tracing.raw_spans
        WHERE ingested_at > now() - INTERVAL {LOOKBACK_MIN} MINUTE
        ORDER BY ingested_at DESC
        """
    ).result_rows
    
    # Deduplicate by idempotency_key
    seen_keys = {}
    for r in rows:
        agent_id = r[0]
        event_type = r[1]
        idempotency_key = r[2]
        ingested_at = r[3]
        
        dedup_key = idempotency_key if idempotency_key else f"span_{ingested_at}_{agent_id}"
        
        if dedup_key not in seen_keys:
            seen_keys[dedup_key] = (agent_id, event_type)
    
    # Count errors by agent
    agent_errors = {}
    for agent_id, event_type in seen_keys.values():
        if agent_id not in agent_errors:
            agent_errors[agent_id] = 0
        if event_type == "error":
            agent_errors[agent_id] += 1
    
    alerts = [
        Alert(
            alert_type="error_burst",
            key=f"agent:{agent_id}",
            severity="medium",
            message=f"Agent {agent_id} emitted {errors} errors in {LOOKBACK_MIN}m window.",
            details={"agent_id": agent_id, "errors": errors, "window_min": LOOKBACK_MIN},
        )
        for agent_id, errors in sorted(agent_errors.items(), key=lambda x: x[1], reverse=True)
        if errors >= ERROR_BURST_THRESHOLD
    ]
    return alerts


def _rule_stuck_traces(client) -> List[Alert]:
    # Get raw spans deduplicated
    spans = client.query(
        f"""
        SELECT
            trace_id,
            ingested_at,
            idempotency_key
        FROM tracing.raw_spans
        WHERE ingested_at > now() - INTERVAL {LOOKBACK_MIN * 4} MINUTE
        ORDER BY ingested_at DESC
        """
    ).result_rows
    
    # Deduplicate spans by idempotency_key
    seen_keys = {}
    for trace_id, ingested_at, idempotency_key in spans:
        dedup_key = idempotency_key if idempotency_key else f"span_{ingested_at}_{trace_id}"
        
        if dedup_key not in seen_keys:
            seen_keys[dedup_key] = (trace_id, ingested_at)
    
    # Get max ingested_at per trace
    trace_last_ingested = {}
    for trace_id, ingested_at in seen_keys.values():
        if trace_id not in trace_last_ingested:
            trace_last_ingested[trace_id] = ingested_at
        else:
            if ingested_at > trace_last_ingested[trace_id]:
                trace_last_ingested[trace_id] = ingested_at
    
    # Get reconstructed traces
    reconstructed = client.query(
        """
        SELECT trace_id, reconstructed_at
        FROM tracing.reconstructed_traces
        """
    ).result_rows
    reconstructed_set = {r[0] for r in reconstructed}
    
    # Find stuck traces
    alerts = []
    now = datetime.now()
    for trace_id, last_ingested in trace_last_ingested.items():
        # Check if trace is NOT reconstructed
        if trace_id not in reconstructed_set:
            # Calculate time difference in minutes
            minutes_since_ingest = (now - last_ingested.replace(tzinfo=None)).total_seconds() / 60
            
            if minutes_since_ingest >= STUCK_TRACE_MINUTES:
                alerts.append(
                    Alert(
                        alert_type="stuck_trace",
                        key=f"trace:{trace_id}",
                        severity="medium",
                        message=f"Trace {trace_id} appears stuck without reconstruction for >= {STUCK_TRACE_MINUTES}m.",
                        details={"trace_id": trace_id, "last_ingested": str(last_ingested)},
                    )
                )
    
    return alerts


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

