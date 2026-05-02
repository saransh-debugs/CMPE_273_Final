"""
SLO evaluator. Reads live signals and produces a status row per SLO.

Signal sources:
  - ClickHouse: ingest spans, reconstructed_traces, raw_decisions
  - Collector /metrics: acceptance / flush success counters
  - FastAPI: synthetic probes for API latency

Each evaluation returns a SLOStatus dataclass that is both the API response
shape and the storage shape (see worker._persist).
"""
from __future__ import annotations

import json
import logging
import os
import statistics
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import clickhouse_connect

from .spec import SLOS, SLOSpec

_logger = logging.getLogger("slo.evaluator")

COLLECTOR_METRICS_URL = os.environ.get("COLLECTOR_METRICS_URL", "http://localhost:9090/metrics")
API_PROBE_URL = os.environ.get("API_PROBE_URL", "http://localhost:8000")
API_PROBE_SAMPLES = int(os.environ.get("API_PROBE_SAMPLES", "5"))
# Traces counted toward reconstruction SLO must have spans this fresh (typically
# tighter than engine.worker LOOKBACK_SEC so old backlog traces do not dominate p95).
RECON_LAG_ACTIVE_SEC = int(os.environ.get("SLO_RECON_ACTIVE_LOOKBACK_SEC", "120"))
# Match engine.worker LOOKBACK_SEC semantics (traces eligible for reconstruction).
TRACE_COMPLETION_ACTIVE_SEC = int(os.environ.get("SLO_TRACE_COMPLETION_ACTIVE_SEC", "300"))


@dataclass
class SLOStatus:
    name: str
    title: str
    signal: str
    window_minutes: int
    threshold: float
    comparison: str
    unit: str
    value: float
    passing: bool
    sample_count: int
    notes: str = ""

    def as_dict(self) -> Dict[str, object]:
        return asdict(self)


def _client():
    return clickhouse_connect.get_client(host="localhost", port=8123, username="default", password="")


def _percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    s = sorted(values)
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def _utc_naive_for_delta(dt: datetime) -> datetime:
    """Subtract datetimes reliably (CH often returns naive; avoid mixed naive/aware)."""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _epoch_first_reconstructed(dt: Optional[datetime]) -> bool:
    if dt is None:
        return True
    return dt.year <= 1971


def _reconstruction_latency_ms(last_ingest_at, fr_at: datetime, latest_rc: datetime) -> Optional[float]:
    """TTFR-style lag when ingest precedes anchor; warm rebuild staleness otherwise."""
    last_i = _utc_naive_for_delta(last_ingest_at)
    fr = _utc_naive_for_delta(fr_at)
    latest = _utc_naive_for_delta(latest_rc)
    if latest < last_i:
        return None
    if not _epoch_first_reconstructed(fr_at) and last_i <= fr:
        return max(0.0, (fr - last_i).total_seconds() * 1000.0)
    return max(0.0, (latest - last_i).total_seconds() * 1000.0)


def _fetch_collector_metrics() -> Optional[Dict[str, object]]:
    try:
        req = urllib.request.Request(COLLECTOR_METRICS_URL, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as e:
        _logger.warning("collector metrics unreachable: %s", e)
        return None


# --- signal implementations ----------------------------------------------

def _signal_ingest_acceptance(spec: SLOSpec, _client_, metrics: Optional[dict]) -> Tuple[float, int, str]:
    if not metrics:
        return 0.0, 0, "collector metrics unreachable"
    span = metrics.get("writers", {}).get("span", {})
    accepted = int(span.get("accepted", 0))
    rejected = int(span.get("rejected", 0))
    total = accepted + rejected
    if total == 0:
        # No traffic: SLO vacuously passes; report 1.0 to avoid false alarms.
        return 1.0, 0, "no traffic in window"
    return accepted / total, total, ""


def _signal_flush_success(spec: SLOSpec, _client_, metrics: Optional[dict]) -> Tuple[float, int, str]:
    if not metrics:
        return 0.0, 0, "collector metrics unreachable"
    # Aggregate across all writers — span and decision health are joint.
    attempts = 0
    successes = 0
    for w in metrics.get("writers", {}).values():
        attempts += int(w.get("flush_attempts", 0))
        successes += int(w.get("flush_success", 0))
    if attempts == 0:
        return 1.0, 0, "no flushes in window"
    return successes / attempts, attempts, ""


def _reconstruction_lags_ms(client, window_minutes: int) -> List[float]:
    """Reconstruction lag resilient to ReplacingMergeTree collapsing history.

    `reconstructed_traces` keeps only the row with maximum `reconstructed_at` per
    trace once parts merge — ``min()`` over insert times cannot be queried
    reliably. ``engine.worker`` therefore persists immutable
    ``first_reconstructed_at`` (carried on every overwrite).

    For each hot trace (**max(``ingested_at``)** within ``RECON_LAG_ACTIVE_SEC``),
    lag is:
    - if ``last_ingest_at <= first_reconstructed_at``: time from last ingest to
      first DAG (**TTFR anchor**).
    else: staleness versus the latest merged snapshot (**``reconstructed_at``**).

    **``window_minutes``:** catalog field only here; eligibility uses the hot window.
    """
    _ = window_minutes  # catalog field; hot-window defines eligibility
    rows = client.query(
        f"""
        WITH
            active_trace AS (
                SELECT trace_id
                FROM tracing.raw_spans
                GROUP BY trace_id
                HAVING max(ingested_at) > now() - INTERVAL {RECON_LAG_ACTIVE_SEC} SECOND
            ),
            last_ingest AS (
                SELECT
                    rs.trace_id AS trace_id,
                    max(rs.ingested_at) AS last_ingest_at
                FROM tracing.raw_spans AS rs
                INNER JOIN active_trace AS a ON rs.trace_id = a.trace_id
                GROUP BY rs.trace_id
            ),
            latest_rec AS (
                SELECT
                    trace_id,
                    first_reconstructed_at AS fr_at,
                    reconstructed_at AS latest_rc
                FROM tracing.reconstructed_traces FINAL
            )
        SELECT
            li.last_ingest_at,
            lr.fr_at,
            lr.latest_rc
        FROM last_ingest AS li
        INNER JOIN latest_rec AS lr ON lr.trace_id = li.trace_id
        WHERE lr.latest_rc >= li.last_ingest_at
        """
    ).result_rows

    lags: List[float] = []
    for last_ingest_at, fr_at, latest_rc in rows:
        lag = _reconstruction_latency_ms(last_ingest_at, fr_at, latest_rc)
        if lag is None:
            continue
        lags.append(lag)
    return lags


def _signal_reconstruction_lag(spec: SLOSpec, client, _metrics) -> Tuple[float, int, str]:
    lags = _reconstruction_lags_ms(client, spec.window_minutes)
    if not lags:
        return 0.0, 0, "no reconstructed traces in window"
    p = 0.95 if spec.signal.endswith("p95") else 0.99
    return _percentile(lags, p), len(lags), ""


def _signal_trace_completion(spec: SLOSpec, client, _metrics) -> Tuple[float, int, str]:
    _ = spec.window_minutes  # metric uses engine-aligned hot-window, not rolling 60m alone
    spans = client.query(
        f"""
        SELECT trace_id
        FROM tracing.raw_spans
        GROUP BY trace_id
        HAVING max(ingested_at) > now() - INTERVAL {TRACE_COMPLETION_ACTIVE_SEC} SECOND
        """
    ).result_rows
    trace_ids = [r[0] for r in spans]
    if not trace_ids:
        return 1.0, 0, "no active traces"
    rec_rows = client.query(
        "SELECT DISTINCT trace_id FROM tracing.reconstructed_traces"
    ).result_rows
    rec_set = {r[0] for r in rec_rows}
    completed = sum(1 for t in trace_ids if t in rec_set)
    return completed / len(trace_ids), len(trace_ids), ""


def _signal_api_latency_p95(spec: SLOSpec, _client_, _metrics) -> Tuple[float, int, str]:
    """Synthetic probe — measures /traces and /health response latency."""
    samples: List[float] = []
    for _ in range(max(1, API_PROBE_SAMPLES)):
        for path in ("/traces?limit=20", "/health"):
            t0 = time.monotonic()
            try:
                with urllib.request.urlopen(API_PROBE_URL + path, timeout=2.5) as resp:
                    resp.read()
            except (urllib.error.URLError, OSError):
                # Fail open on this signal — alerting handles unreachable API
                # via its own rule; we don't want a paused process to freeze SLOs.
                continue
            samples.append((time.monotonic() - t0) * 1000.0)
    if not samples:
        return 0.0, 0, "api unreachable"
    return _percentile(samples, 0.95), len(samples), ""


SIGNAL_FUNCS = {
    "ingest_acceptance": _signal_ingest_acceptance,
    "flush_success": _signal_flush_success,
    "reconstruction_lag_p95": _signal_reconstruction_lag,
    "reconstruction_lag_p99": _signal_reconstruction_lag,
    "trace_completion": _signal_trace_completion,
    "api_latency_p95": _signal_api_latency_p95,
}


def evaluate_all(client=None, *, fetch_metrics: bool = True) -> List[SLOStatus]:
    if client is None:
        client = _client()
    metrics = _fetch_collector_metrics() if fetch_metrics else None
    out: List[SLOStatus] = []
    for spec in SLOS:
        fn = SIGNAL_FUNCS.get(spec.signal)
        if fn is None:
            _logger.warning("no signal function for %s", spec.signal)
            continue
        try:
            value, samples, notes = fn(spec, client, metrics)
        except Exception as e:  # noqa: BLE001
            _logger.exception("signal %s failed: %s", spec.signal, e)
            value, samples, notes = 0.0, 0, f"error: {e}"
        out.append(SLOStatus(
            name=spec.name,
            title=spec.title,
            signal=spec.signal,
            window_minutes=spec.window_minutes,
            threshold=spec.threshold,
            comparison=spec.comparison,
            unit=spec.unit,
            value=float(value),
            passing=spec.passes(value) if not notes.startswith("error") else False,
            sample_count=int(samples),
            notes=notes,
        ))
    return out
