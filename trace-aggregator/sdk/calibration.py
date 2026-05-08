"""
LLM-04: Confidence calibration.

Checks whether decision confidence scores correlate with observed trace
success rates. Groups decisions into confidence buckets and computes
mean_confidence vs observed_success_rate per bucket.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

_BUCKETS: List[Tuple[str, float, float]] = [
    ("0.0-0.5", 0.0, 0.5),
    ("0.5-0.7", 0.5, 0.7),
    ("0.7-0.9", 0.7, 0.9),
    ("0.9-1.0", 0.9, 1.0),
]


@dataclass
class CalibrationBucket:
    label: str
    min_conf: float
    max_conf: float
    count: int
    mean_confidence: float
    observed_success_rate: float
    calibration_error: float


@dataclass
class CalibrationReport:
    buckets: List[CalibrationBucket]
    overall_calibration_error: float
    verdict: str  # "well_calibrated" | "overconfident" | "underconfident" | "needs_calibration"


def calibrate(
    decisions: List[Dict],
    outcomes: Dict[str, bool],
) -> CalibrationReport:
    """
    decisions: list of dicts with at least {"trace_id": str, "confidence": float}
    outcomes:  trace_id -> True (success) | False (failure)
    """
    if not decisions:
        return CalibrationReport(
            buckets=[], overall_calibration_error=0.0, verdict="well_calibrated"
        )

    bucket_items: Dict[str, List[Tuple[float, bool]]] = {
        label: [] for label, _, _ in _BUCKETS
    }

    for d in decisions:
        conf = float(d.get("confidence", 0.0))
        tid = d.get("trace_id", "")
        success = outcomes.get(tid)
        if success is None:
            continue
        for label, lo, hi in _BUCKETS:
            if lo <= conf <= hi:
                bucket_items[label].append((conf, success))
                break

    buckets: List[CalibrationBucket] = []
    weighted_error = 0.0
    total_count = 0

    for label, lo, hi in _BUCKETS:
        items = bucket_items[label]
        if not items:
            continue
        mean_conf = sum(c for c, _ in items) / len(items)
        success_rate = sum(1 for _, s in items if s) / len(items)
        cal_error = abs(mean_conf - success_rate)
        buckets.append(
            CalibrationBucket(
                label=label,
                min_conf=lo,
                max_conf=hi,
                count=len(items),
                mean_confidence=round(mean_conf, 4),
                observed_success_rate=round(success_rate, 4),
                calibration_error=round(cal_error, 4),
            )
        )
        weighted_error += cal_error * len(items)
        total_count += len(items)

    overall = round(weighted_error / max(total_count, 1), 4)

    if overall < 0.1:
        verdict = "well_calibrated"
    elif overall <= 0.3:
        verdict = "needs_calibration"
    else:
        mean_conf_all = sum(b.mean_confidence for b in buckets) / max(len(buckets), 1)
        mean_sr_all = sum(b.observed_success_rate for b in buckets) / max(len(buckets), 1)
        verdict = "overconfident" if mean_conf_all > mean_sr_all else "underconfident"

    return CalibrationReport(
        buckets=buckets,
        overall_calibration_error=overall,
        verdict=verdict,
    )
