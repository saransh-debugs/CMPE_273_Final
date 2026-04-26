"""
Blame: per-agent attribution of latency, token spend, and errors.

The Blame View answers "which agent is costing me the most?" in one screen.
We compute three metrics per agent in a trace:

  - latency_share_pct: % of total trace wall time spent in this agent
  - token_share_pct:   % of total tokens consumed by this agent
  - error_count:       number of error spans attributed to this agent

We surface a single composite `blame_score` (0-100) so the UI can rank
agents at a glance. The weights are tunable — we default to favoring
latency since that's usually what wakes engineers up at night.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, List

from .dag import Span


# Tweak these for your team's priorities.
W_LATENCY = 0.5
W_TOKENS = 0.3
W_ERRORS = 0.2


@dataclass
class AgentBlame:
    agent_id: str
    span_count: int
    total_latency_ms: int
    total_input_tokens: int
    total_output_tokens: int
    error_count: int

    latency_share_pct: float
    token_share_pct: float

    blame_score: float  # 0-100


def compute_blame(spans: List[Span]) -> List[AgentBlame]:
    if not spans:
        return []

    totals_latency = sum(s.latency_ms for s in spans) or 1
    totals_tokens = sum(s.input_tokens + s.output_tokens for s in spans) or 1
    totals_errors = sum(1 for s in spans if s.event_type == "error") or 1

    by_agent: Dict[str, Dict] = {}
    for s in spans:
        b = by_agent.setdefault(s.agent_id, {
            "span_count": 0,
            "lat": 0,
            "in_tok": 0,
            "out_tok": 0,
            "err": 0,
        })
        b["span_count"] += 1
        b["lat"] += s.latency_ms
        b["in_tok"] += s.input_tokens
        b["out_tok"] += s.output_tokens
        if s.event_type == "error":
            b["err"] += 1

    out: List[AgentBlame] = []
    for agent_id, b in by_agent.items():
        lat_share = b["lat"] / totals_latency
        tok_share = (b["in_tok"] + b["out_tok"]) / totals_tokens
        err_share = b["err"] / totals_errors

        score = 100.0 * (
            W_LATENCY * lat_share + W_TOKENS * tok_share + W_ERRORS * err_share
        )

        out.append(AgentBlame(
            agent_id=agent_id,
            span_count=b["span_count"],
            total_latency_ms=b["lat"],
            total_input_tokens=b["in_tok"],
            total_output_tokens=b["out_tok"],
            error_count=b["err"],
            latency_share_pct=round(lat_share * 100, 2),
            token_share_pct=round(tok_share * 100, 2),
            blame_score=round(score, 2),
        ))

    out.sort(key=lambda x: x.blame_score, reverse=True)
    return out


def blame_to_dicts(blames: List[AgentBlame]) -> List[dict]:
    return [asdict(b) for b in blames]
