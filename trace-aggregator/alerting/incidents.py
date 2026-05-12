"""
Incident state machine for alerting/worker.

Replaces the in-memory cooldown dict with a ClickHouse-backed lifecycle
(open -> ack -> resolved). The dedup key IS the lifecycle: an alert that
re-fires while an incident is already 'open' or 'ack' just increments
occurrence_count silently and refreshes last_seen_at — the webhook only
pages on transitions into 'open' (new or re-opened from 'resolved').

This satisfies ENG-11's acceptance: "repeated anomalies do not create
alert storms", even across worker restarts.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

_logger = logging.getLogger("alerting.incidents")

RecordStatus = Literal["new", "existing", "reopened"]

STATE_OPEN = "open"
STATE_ACK = "ack"
STATE_RESOLVED = "resolved"


@dataclass
class Incident:
    incident_key: str
    alert_type: str
    state: str
    severity: str
    message: str
    details: Dict[str, Any]
    opened_at: datetime
    last_seen_at: datetime
    acknowledged_at: Optional[datetime]
    resolved_at: Optional[datetime]
    occurrence_count: int

    def to_dict(self) -> Dict[str, Any]:
        def iso(dt: Optional[datetime]) -> Optional[str]:
            if dt is None:
                return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

        # Treat the sentinel epoch-zero as "never" so the UI can hide it.
        # ClickHouse may return the value shifted into local TZ, so check by year.
        def maybe(dt: Optional[datetime]) -> Optional[str]:
            if dt is None or dt.year < 2000:
                return None
            return iso(dt)

        return {
            "incident_key": self.incident_key,
            "alert_type": self.alert_type,
            "state": self.state,
            "severity": self.severity,
            "message": self.message,
            "details": self.details,
            "opened_at": iso(self.opened_at),
            "last_seen_at": iso(self.last_seen_at),
            "acknowledged_at": maybe(self.acknowledged_at),
            "resolved_at": maybe(self.resolved_at),
            "occurrence_count": self.occurrence_count,
        }


# ----------------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------------

_SELECT_LATEST = """
SELECT incident_key, alert_type, state, severity, message, details,
       opened_at, last_seen_at, acknowledged_at, resolved_at, occurrence_count
FROM tracing.incidents FINAL
WHERE incident_key = {incident_key:String}
LIMIT 1
"""


def _row_to_incident(row) -> Incident:
    details_raw = row[5] or "{}"
    try:
        details = json.loads(details_raw)
    except (TypeError, ValueError):
        details = {}
    return Incident(
        incident_key=row[0],
        alert_type=row[1],
        state=row[2],
        severity=row[3],
        message=row[4],
        details=details,
        opened_at=row[6],
        last_seen_at=row[7],
        acknowledged_at=row[8],
        resolved_at=row[9],
        occurrence_count=int(row[10]),
    )


def _fetch_latest(client, incident_key: str) -> Optional[Incident]:
    rows = client.query(_SELECT_LATEST, parameters={"incident_key": incident_key}).result_rows
    return _row_to_incident(rows[0]) if rows else None


def _insert(client, inc: Incident) -> None:
    client.insert(
        "tracing.incidents",
        [[
            inc.incident_key,
            inc.alert_type,
            inc.state,
            inc.severity,
            inc.message,
            json.dumps(inc.details, default=str),
            inc.opened_at,
            inc.last_seen_at,
            inc.acknowledged_at or datetime(1970, 1, 1, tzinfo=timezone.utc),
            inc.resolved_at or datetime(1970, 1, 1, tzinfo=timezone.utc),
            inc.occurrence_count,
        ]],
        column_names=[
            "incident_key", "alert_type", "state", "severity", "message", "details",
            "opened_at", "last_seen_at", "acknowledged_at", "resolved_at", "occurrence_count",
        ],
    )


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------


def record_alert(client, alert) -> RecordStatus:
    """Idempotent state-machine ingest. Returns 'new' / 'reopened' / 'existing'.

    Only 'new' and 'reopened' should fire a webhook page; 'existing' means the
    underlying condition is still trending and we silently bump occurrence_count.
    """
    incident_key = f"{alert.alert_type}:{alert.key}"
    now = datetime.now(timezone.utc)

    existing = _fetch_latest(client, incident_key)

    if existing is None:
        inc = Incident(
            incident_key=incident_key,
            alert_type=alert.alert_type,
            state=STATE_OPEN,
            severity=alert.severity,
            message=alert.message,
            details=dict(alert.details),
            opened_at=now,
            last_seen_at=now,
            acknowledged_at=None,
            resolved_at=None,
            occurrence_count=1,
        )
        _insert(client, inc)
        return "new"

    if existing.state == STATE_RESOLVED:
        # Re-open: keep history of count via a fresh opened_at.
        inc = Incident(
            incident_key=incident_key,
            alert_type=alert.alert_type,
            state=STATE_OPEN,
            severity=alert.severity,
            message=alert.message,
            details=dict(alert.details),
            opened_at=now,
            last_seen_at=now,
            acknowledged_at=None,
            resolved_at=None,
            occurrence_count=existing.occurrence_count + 1,
        )
        _insert(client, inc)
        return "reopened"

    # Still open / ack — bump count, refresh last_seen, keep state.
    existing.last_seen_at = now
    existing.occurrence_count += 1
    existing.message = alert.message
    existing.details = dict(alert.details)
    _insert(client, existing)
    return "existing"


def acknowledge(client, incident_key: str) -> Optional[Incident]:
    inc = _fetch_latest(client, incident_key)
    if inc is None or inc.state == STATE_RESOLVED:
        return inc
    now = datetime.now(timezone.utc)
    inc.state = STATE_ACK
    inc.acknowledged_at = now
    _insert(client, inc)
    return inc


def resolve(client, incident_key: str, reason: str = "manual") -> Optional[Incident]:
    inc = _fetch_latest(client, incident_key)
    if inc is None or inc.state == STATE_RESOLVED:
        return inc
    now = datetime.now(timezone.utc)
    inc.state = STATE_RESOLVED
    inc.resolved_at = now
    inc.details = {**inc.details, "resolved_reason": reason}
    _insert(client, inc)
    return inc


def list_incidents(client, state: Optional[str] = None, limit: int = 200) -> List[Incident]:
    where = ""
    params: Dict[str, Any] = {"limit": limit}
    if state:
        where = "WHERE state = {state:String}"
        params["state"] = state
    rows = client.query(
        f"""
        SELECT incident_key, alert_type, state, severity, message, details,
               opened_at, last_seen_at, acknowledged_at, resolved_at, occurrence_count
        FROM tracing.incidents FINAL
        {where}
        ORDER BY opened_at DESC
        LIMIT {{limit:UInt32}}
        """,
        parameters=params,
    ).result_rows
    return [_row_to_incident(r) for r in rows]


def state_counts(client) -> Dict[str, int]:
    rows = client.query(
        """
        SELECT state, count() FROM tracing.incidents FINAL GROUP BY state
        """
    ).result_rows
    out = {STATE_OPEN: 0, STATE_ACK: 0, STATE_RESOLVED: 0}
    for state, n in rows:
        out[state] = int(n)
    return out


def auto_resolve_stale(client, timeout_sec: int) -> int:
    """Mark open/ack incidents whose last_seen_at is older than `timeout_sec`
    as resolved. Returns the number of incidents auto-resolved."""
    rows = client.query(
        """
        SELECT incident_key
        FROM tracing.incidents FINAL
        WHERE state IN ('open', 'ack')
          AND last_seen_at < now() - INTERVAL {timeout:UInt32} SECOND
        """,
        parameters={"timeout": timeout_sec},
    ).result_rows
    keys = [r[0] for r in rows]
    for k in keys:
        resolve(client, k, reason="auto_timeout")
    if keys:
        _logger.info("Auto-resolved %d stale incident(s): %s", len(keys), keys)
    return len(keys)
