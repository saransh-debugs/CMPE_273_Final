"""
Collector metrics. In-process counters/gauges shared between BatchWriter
instances and the metrics HTTP server.

Design notes:
  - Pure Python, no external metric libs (kept dependency-free).
  - Counters monotonically increase; gauges hold an instantaneous value;
    histograms keep a bounded ring buffer for percentile estimation.
  - Snapshot is JSON-serializable so it can feed both /metrics and the
    SLO evaluator without translation.
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Deque, Dict, List


HISTOGRAM_WINDOW = int(os.environ.get("METRICS_HIST_WINDOW", "1024"))


class _Histogram:
    """Bounded ring buffer of recent observations for percentile estimation.

    A real prod system would use HDR or t-digest. For our window sizes
    (single-digit kilobytes per histogram) a sorted percentile read is fine.
    """

    def __init__(self, capacity: int = HISTOGRAM_WINDOW) -> None:
        self._buf: Deque[float] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def observe(self, value: float) -> None:
        with self._lock:
            self._buf.append(float(value))

    def snapshot(self) -> Dict[str, float]:
        with self._lock:
            values: List[float] = list(self._buf)
        if not values:
            return {"count": 0, "avg": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}
        values.sort()
        return {
            "count": len(values),
            "avg": sum(values) / len(values),
            "p50": _percentile(values, 0.50),
            "p95": _percentile(values, 0.95),
            "p99": _percentile(values, 0.99),
            "max": values[-1],
        }


def _percentile(sorted_vals: List[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


class WriterMetrics:
    """Per-BatchWriter counters/gauges/histograms.

    Each item_name (span, decision) gets its own instance so the metrics
    page can break ingest health down by stream.
    """

    def __init__(self, item_name: str) -> None:
        self.item_name = item_name
        self._lock = threading.Lock()
        # Counters
        self.accepted = 0          # WAL durably written
        self.rejected = 0          # WAL write failed (caller saw failure)
        self.queue_full_events = 0 # WAL written but in-memory queue full
        self.flush_attempts = 0
        self.flush_success = 0
        self.flush_failures = 0
        self.rows_flushed = 0
        self.replay_enqueued = 0
        # Gauges (set externally / on demand)
        self._queue_depth_fn = None  # callable -> int
        self._wal_backlog_fn = None  # callable -> int
        # Histograms
        self.flush_latency_ms = _Histogram()
        self.flush_batch_size = _Histogram()

    def attach_queue_depth(self, fn) -> None:
        self._queue_depth_fn = fn

    def attach_wal_backlog(self, fn) -> None:
        self._wal_backlog_fn = fn

    # --- mutators (cheap; called from hot path) ---------------------------
    def inc_accepted(self) -> None:
        with self._lock:
            self.accepted += 1

    def inc_rejected(self) -> None:
        with self._lock:
            self.rejected += 1

    def inc_queue_full(self) -> None:
        with self._lock:
            self.queue_full_events += 1

    def inc_replay(self, n: int = 1) -> None:
        with self._lock:
            self.replay_enqueued += n

    def record_flush(self, *, batch_size: int, latency_ms: float, ok: bool) -> None:
        with self._lock:
            self.flush_attempts += 1
            if ok:
                self.flush_success += 1
                self.rows_flushed += batch_size
            else:
                self.flush_failures += 1
        self.flush_latency_ms.observe(latency_ms)
        self.flush_batch_size.observe(batch_size)

    # --- read --------------------------------------------------------------
    def snapshot(self) -> Dict[str, object]:
        with self._lock:
            base = {
                "item": self.item_name,
                "accepted": self.accepted,
                "rejected": self.rejected,
                "queue_full_events": self.queue_full_events,
                "flush_attempts": self.flush_attempts,
                "flush_success": self.flush_success,
                "flush_failures": self.flush_failures,
                "rows_flushed": self.rows_flushed,
                "replay_enqueued": self.replay_enqueued,
            }
            total_inbound = self.accepted + self.rejected
            base["acceptance_rate"] = (
                self.accepted / total_inbound if total_inbound > 0 else 1.0
            )
            base["flush_success_rate"] = (
                self.flush_success / self.flush_attempts if self.flush_attempts > 0 else 1.0
            )
        base["queue_depth"] = int(self._queue_depth_fn()) if self._queue_depth_fn else 0
        base["wal_backlog"] = int(self._wal_backlog_fn()) if self._wal_backlog_fn else 0
        base["flush_latency_ms"] = self.flush_latency_ms.snapshot()
        base["flush_batch_size"] = self.flush_batch_size.snapshot()
        return base


class CollectorMetrics:
    """Top-level metrics container.

    Held as a module-level singleton so HTTP handlers and BatchWriter
    instances can register/read without a DI tree.
    """

    def __init__(self) -> None:
        self.started_at = time.time()
        self._writers: Dict[str, WriterMetrics] = {}
        # Servicer-level counters for received/dropped (independent of WAL).
        self._lock = threading.Lock()
        self.spans_received = 0
        self.spans_dropped = 0
        self.decisions_received = 0
        self.decisions_dropped = 0

    def writer(self, item_name: str) -> WriterMetrics:
        if item_name not in self._writers:
            self._writers[item_name] = WriterMetrics(item_name)
        return self._writers[item_name]

    def inc_span_received(self) -> None:
        with self._lock:
            self.spans_received += 1

    def inc_span_dropped(self) -> None:
        with self._lock:
            self.spans_dropped += 1

    def inc_decision_received(self) -> None:
        with self._lock:
            self.decisions_received += 1

    def inc_decision_dropped(self) -> None:
        with self._lock:
            self.decisions_dropped += 1

    def snapshot(self) -> Dict[str, object]:
        with self._lock:
            servicer = {
                "spans_received": self.spans_received,
                "spans_dropped": self.spans_dropped,
                "decisions_received": self.decisions_received,
                "decisions_dropped": self.decisions_dropped,
            }
        return {
            "started_at": self.started_at,
            "uptime_sec": time.time() - self.started_at,
            "servicer": servicer,
            "writers": {name: w.snapshot() for name, w in self._writers.items()},
        }


# Module-level singleton: BatchWriter and the metrics server share this.
METRICS = CollectorMetrics()
