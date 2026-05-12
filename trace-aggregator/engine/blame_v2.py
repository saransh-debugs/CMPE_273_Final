"""
Blame V2: per-agent attribution with uncertainty propagation.

What V1 (blame.py) does:
    Computes one point estimate per agent — `blame_score` 0..100.

What V2 adds on top:
    1. Bootstrap-based 95% confidence intervals on the blame score, so the UI
       can show "Agent X scored 67 (95% CI: 61-72) based on 42 spans". A wide
       CI means low data / unstable ranking, a tight CI means trustworthy.
    2. Error amplification — errors deeper in the DAG (more downstream
       descendants affected) weigh more than leaf-level errors. The component
       weight reflects "how much damage did this error cause".
    3. Sample-size awareness — `n_bootstrap_samples` and `span_count` are
       exposed so the operator can sanity-check small-sample results.
    4. Versioned output — `model_version` lets the API serve both V1 and V2
       behind a query parameter without breaking existing dashboards.

V1 is left bit-identical (do not edit blame.py). Both run side-by-side in the
worker and both JSON payloads are persisted; the API picks which to serve.
"""
from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

from .dag import DAGNode, Span


MODEL_VERSION = "v2.0"

# Same weights as V1 so rankings agree on identical input.
W_LATENCY = 0.5
W_TOKENS = 0.3
W_ERRORS = 0.2

# Bootstrap configuration.
DEFAULT_BOOTSTRAP_ITERATIONS = 1000
CI_LOWER_QUANTILE = 0.025   # 2.5th percentile
CI_UPPER_QUANTILE = 0.975   # 97.5th percentile

# Error amplification: max multiplier on the error component is
# 1 + CAP * COEFF = 1 + 5 * 0.2 = 2.0x. Tuned so a single error at the
# root of a 5-agent fanout cannot completely dominate latency/tokens.
ERROR_AMP_CAP = 5.0
ERROR_AMP_COEFF = 0.2


@dataclass
class AgentBlameV2:
    """Mirror of AgentBlame plus V2-only fields. Same field names where they
    overlap so the UI can render either with one component."""
    agent_id: str
    span_count: int
    total_latency_ms: int
    total_input_tokens: int
    total_output_tokens: int
    error_count: int

    latency_share_pct: float
    token_share_pct: float
    blame_score: float                 # point estimate, 0..100

    # New in V2
    blame_score_ci_low: float          # 95% CI lower bound (2.5th percentile)
    blame_score_ci_high: float         # 95% CI upper bound (97.5th percentile)
    blame_score_std: float             # std-dev across bootstrap samples
    n_bootstrap_samples: int           # how many resamples were used
    error_amplification: float         # avg descendant count for error spans
    components: Dict[str, float]       # {latency: X, tokens: Y, errors: Z}
    model_version: str = MODEL_VERSION


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────


def _count_descendants(node_map: Dict[str, DAGNode], span_id: str) -> int:
    """BFS over `children` lists to count strict descendants of `span_id`.

    The DAG can have multiple parents (fan-in), so we use a visited set to
    avoid double-counting nodes reachable via more than one path.
    """
    if span_id not in node_map:
        return 0
    visited = {span_id}
    queue = [span_id]
    while queue:
        nxt = []
        for sid in queue:
            for child in node_map[sid].children:
                if child not in visited:
                    visited.add(child)
                    nxt.append(child)
        queue = nxt
    return len(visited) - 1  # exclude self


def _compute_error_amplification(
    spans: List[Span],
    node_map: Optional[Dict[str, DAGNode]],
) -> Dict[str, float]:
    """For each agent, returns the mean descendant count across its error
    spans. Higher value → that agent's errors cascade further downstream.

    Returns 0.0 for agents with no errors. Returns {} when no DAG is
    supplied (V2 still works without the DAG, just without amplification).
    """
    if not node_map:
        return {}
    sums: Dict[str, int] = {}
    counts: Dict[str, int] = {}
    for s in spans:
        if s.event_type != "error":
            continue
        desc = _count_descendants(node_map, s.span_id)
        sums[s.agent_id] = sums.get(s.agent_id, 0) + desc
        counts[s.agent_id] = counts.get(s.agent_id, 0) + 1
    return {
        agent: sums[agent] / counts[agent]
        for agent in sums
    }


def _compute_scores(
    spans: List[Span],
    error_amp: Dict[str, float],
) -> Dict[str, float]:
    """Single-pass per-agent blame score for one sample of spans.

    Pulled out as a standalone function so the bootstrap loop can call it
    cheaply 1000 times on resampled span lists.
    """
    if not spans:
        return {}

    total_lat = sum(s.latency_ms for s in spans) or 1
    total_tok = sum(s.input_tokens + s.output_tokens for s in spans) or 1
    total_err = sum(1 for s in spans if s.event_type == "error") or 1

    by_agent: Dict[str, Dict[str, float]] = {}
    for s in spans:
        b = by_agent.setdefault(s.agent_id, {"lat": 0, "tok": 0, "err": 0})
        b["lat"] += s.latency_ms
        b["tok"] += s.input_tokens + s.output_tokens
        if s.event_type == "error":
            b["err"] += 1

    scores: Dict[str, float] = {}
    for agent_id, b in by_agent.items():
        lat_share = b["lat"] / total_lat
        tok_share = b["tok"] / total_tok
        err_share = b["err"] / total_err
        # Error amplification: cap then scale so single huge cascades don't
        # blow past blame_score=100.
        amp = 1.0 + min(error_amp.get(agent_id, 0.0), ERROR_AMP_CAP) * ERROR_AMP_COEFF
        scores[agent_id] = 100.0 * (
            W_LATENCY * lat_share
            + W_TOKENS * tok_share
            + W_ERRORS * err_share * amp
        )
    return scores


def _bootstrap_resample(spans: List[Span], rng: random.Random) -> List[Span]:
    """Sample N spans with replacement from N original spans.

    Standard non-parametric bootstrap: a few spans appear twice, a few are
    absent, the rest unchanged. Repeating this and recomputing the score
    each time gives us the empirical distribution of the estimator.
    """
    n = len(spans)
    return [spans[rng.randint(0, n - 1)] for _ in range(n)]


def _quantile(sorted_values: List[float], q: float) -> float:
    """Linear-interpolation quantile on an already-sorted list."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return sorted_values[lo] * (1.0 - frac) + sorted_values[hi] * frac


def _std(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    return var ** 0.5


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────


def compute_blame_v2(
    spans: List[Span],
    node_map: Optional[Dict[str, DAGNode]] = None,
    bootstrap_iters: int = DEFAULT_BOOTSTRAP_ITERATIONS,
    seed: int = 42,
) -> List[AgentBlameV2]:
    """Compute V2 blame with confidence intervals via non-parametric bootstrap.

    Args:
        spans:           All spans in the trace (post-dedupe).
        node_map:        Reconstructed DAG (from `engine.dag.reconstruct_dag`).
                         Required for error amplification; without it amp=0.
        bootstrap_iters: Number of bootstrap resamples. 1000 is plenty for
                         95% CIs on small per-agent samples; use 100-200 in
                         tests to keep things fast.
        seed:            RNG seed. Same seed → identical output.

    Returns: list of AgentBlameV2, sorted by blame_score descending.
    """
    if not spans:
        return []

    rng = random.Random(seed)
    error_amp = _compute_error_amplification(spans, node_map)

    # 1. Point estimate on the original data — this is the headline number.
    point_scores = _compute_scores(spans, error_amp)

    # 2. Bootstrap: collect score distribution per agent across resamples.
    distributions: Dict[str, List[float]] = {a: [] for a in point_scores}
    for _ in range(bootstrap_iters):
        resampled = _bootstrap_resample(spans, rng)
        sample_scores = _compute_scores(resampled, error_amp)
        # Some agents may be absent from a resample — record 0.0 so the
        # distribution length is constant across agents.
        for agent_id in distributions:
            distributions[agent_id].append(sample_scores.get(agent_id, 0.0))

    # 3. Pre-compute trace totals once for the *_share_pct fields (reported
    # against the original spans, not bootstrap samples).
    total_lat = sum(s.latency_ms for s in spans) or 1
    total_tok = sum(s.input_tokens + s.output_tokens for s in spans) or 1
    total_err = sum(1 for s in spans if s.event_type == "error") or 1

    # 4. Build per-agent records.
    results: List[AgentBlameV2] = []
    for agent_id, dist in distributions.items():
        agent_spans = [s for s in spans if s.agent_id == agent_id]

        a_lat = sum(s.latency_ms for s in agent_spans)
        a_in = sum(s.input_tokens for s in agent_spans)
        a_out = sum(s.output_tokens for s in agent_spans)
        a_err = sum(1 for s in agent_spans if s.event_type == "error")

        dist_sorted = sorted(dist)
        ci_low = _quantile(dist_sorted, CI_LOWER_QUANTILE)
        ci_high = _quantile(dist_sorted, CI_UPPER_QUANTILE)
        std = _std(dist)

        lat_share = a_lat / total_lat
        tok_share = (a_in + a_out) / total_tok
        err_share = a_err / total_err
        amp = 1.0 + min(error_amp.get(agent_id, 0.0), ERROR_AMP_CAP) * ERROR_AMP_COEFF

        results.append(AgentBlameV2(
            agent_id=agent_id,
            span_count=len(agent_spans),
            total_latency_ms=a_lat,
            total_input_tokens=a_in,
            total_output_tokens=a_out,
            error_count=a_err,
            latency_share_pct=round(lat_share * 100, 2),
            token_share_pct=round(tok_share * 100, 2),
            blame_score=round(point_scores[agent_id], 2),
            blame_score_ci_low=round(ci_low, 2),
            blame_score_ci_high=round(ci_high, 2),
            blame_score_std=round(std, 2),
            n_bootstrap_samples=bootstrap_iters,
            error_amplification=round(error_amp.get(agent_id, 0.0), 2),
            components={
                "latency": round(100.0 * W_LATENCY * lat_share, 2),
                "tokens": round(100.0 * W_TOKENS * tok_share, 2),
                "errors": round(100.0 * W_ERRORS * err_share * amp, 2),
            },
            model_version=MODEL_VERSION,
        ))

    results.sort(key=lambda x: x.blame_score, reverse=True)
    return results


def blame_v2_to_dicts(blames: List[AgentBlameV2]) -> List[dict]:
    """JSON-serializable form for ClickHouse / API."""
    return [asdict(b) for b in blames]