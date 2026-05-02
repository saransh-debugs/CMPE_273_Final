"""
SLO catalog. Single source of truth for what we promise and how we measure it.

Each SLO has:
  - name        — stable id used in API/storage/alerts
  - title       — human-friendly label
  - description — what it covers
  - signal      — which evaluator function to use (see evaluator.py)
  - threshold   — numeric target (units depend on the signal)
  - comparison  — "<="  (lower is better, e.g. latency)
                | ">="  (higher is better, e.g. acceptance rate)
  - window_minutes — lookback window for the measurement

Why a flat dict rather than YAML/JSON: this is the production source of
truth, edited by engineers in PRs. A typed Python literal gives us linting
and grep-ability for free; we can switch to a config file later if ops
needs to retune without a deploy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class SLOSpec:
    name: str
    title: str
    description: str
    signal: str
    threshold: float
    comparison: str  # "<=" or ">="
    window_minutes: int
    unit: str = ""

    def passes(self, value: float) -> bool:
        if self.comparison == "<=":
            return value <= self.threshold
        if self.comparison == ">=":
            return value >= self.threshold
        raise ValueError(f"unsupported comparison: {self.comparison}")


SLOS: List[SLOSpec] = [
    SLOSpec(
        name="ingest_acceptance",
        title="Ingest acceptance rate",
        description="Fraction of inbound spans accepted (durably WAL-written) by the collector.",
        signal="ingest_acceptance",
        threshold=0.999,
        comparison=">=",
        window_minutes=15,
        unit="ratio",
    ),
    SLOSpec(
        name="flush_success",
        title="Storage flush success rate",
        description="Fraction of ClickHouse batch flushes that succeeded.",
        signal="flush_success",
        threshold=0.99,
        comparison=">=",
        window_minutes=15,
        unit="ratio",
    ),
    SLOSpec(
        name="reconstruction_lag_p95",
        title="Reconstruction latency (p95)",
        description=(
            "p95 milliseconds from last span ingested until first reconstructed DAG "
            "(first_reconstructed_at) when spans precede first rebuild; otherwise "
            "staleness versus latest reconstructed_at. Designed for ReplacingMergeTree "
            "(only the newest reconstruction row survives merges)."
        ),
        signal="reconstruction_lag_p95",
        threshold=60_000.0,
        comparison="<=",
        window_minutes=60,
        unit="ms",
    ),
    SLOSpec(
        name="reconstruction_lag_p99",
        title="Reconstruction latency (p99)",
        description=(
            "p99 latency using stored first_reconstructed_at + surviving "
            "reconstructed_at semantics (warm traces after incremental spans)."
        ),
        signal="reconstruction_lag_p99",
        threshold=120_000.0,
        comparison="<=",
        window_minutes=60,
        unit="ms",
    ),
    SLOSpec(
        name="trace_completion",
        title="Trace completion rate",
        description="Fraction of recently active traces that have a reconstruction row.",
        signal="trace_completion",
        threshold=0.99,
        comparison=">=",
        window_minutes=60,
        unit="ratio",
    ),
    SLOSpec(
        name="api_latency_p95",
        title="API p95 latency",
        description="p95 latency of /traces and /traces/{id} probes against the FastAPI server.",
        signal="api_latency_p95",
        threshold=500.0,
        comparison="<=",
        window_minutes=5,
        unit="ms",
    ),
]


def by_name(name: str) -> SLOSpec:
    for s in SLOS:
        if s.name == name:
            return s
    raise KeyError(name)
