"""
Causal DAG reconstruction from spans tagged with vector clocks.

Why we need this even when LangGraph gives us parent_span_id:
  - In a real multi-agent system, spans cross process boundaries and arrive
    out of order. Some collectors drop spans or duplicate them.
  - Parallel branches (e.g. orchestrator dispatches to research AND coder
    simultaneously) need merge-aware reconstruction at the join.
  - Vector clocks let us detect orphans, infer the most-likely parent for
    a span whose parent was lost, and verify that the parent_span_id graph
    is consistent.

Vector clock semantics (we use Lamport-style component-wise ordering):
  Clock A causally precedes Clock B  iff
        for every agent k:  A[k] <= B[k]
        AND  A != B

  A and B are concurrent iff neither precedes the other.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class Span:
    span_id: str
    parent_span_id: str
    agent_id: str
    vector_clock: Dict[str, int]
    event_type: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    start_time_ms: int


@dataclass
class DAGNode:
    span: Span
    children: List[str] = field(default_factory=list)
    parent_ids: List[str] = field(default_factory=list)
    inferred_parent: bool = False  # True if we filled in a missing parent
    parent_resolution: str = "none"  # explicit | inferred | explicit_plus_fanin | root
    inferred_candidates: List[str] = field(default_factory=list)


def _precedes(a: Dict[str, int], b: Dict[str, int]) -> bool:
    """True iff vector clock `a` causally precedes vector clock `b`."""
    if a == b:
        return False
    seen_strict_lt = False
    keys = set(a) | set(b)
    for k in keys:
        av, bv = a.get(k, 0), b.get(k, 0)
        if av > bv:
            return False
        if av < bv:
            seen_strict_lt = True
    return seen_strict_lt


def _clock_size(clock: Dict[str, int]) -> int:
    """Sum of all components — used as a tiebreaker proxy for 'depth'."""
    return sum(clock.values())


def _parent_sort_key(span: Span) -> Tuple[int, int, str]:
    """
    Deterministic parent ordering:
    1) deeper vector-clock first
    2) later start_time first
    3) lexical span_id for stable total ordering
    """
    return (-_clock_size(span.vector_clock), -int(span.start_time_ms), span.span_id)


def _causal_frontier_parents(current: Span, spans: List[Span]) -> List[str]:
    """
    Return immediate causal predecessors of `current` (can be multiple for fan-in).
    A predecessor is in the frontier if no other predecessor lies strictly between it and current.
    """
    preds = [cand for cand in spans if cand.span_id != current.span_id and _precedes(cand.vector_clock, current.vector_clock)]
    frontier: List[Span] = []
    for cand in preds:
        dominated = False
        for other in preds:
            if other.span_id == cand.span_id:
                continue
            if _precedes(cand.vector_clock, other.vector_clock) and _precedes(other.vector_clock, current.vector_clock):
                dominated = True
                break
        if not dominated:
            frontier.append(cand)
    frontier.sort(key=_parent_sort_key)
    return [p.span_id for p in frontier]


def reconstruct_dag(spans: List[Span]) -> Dict[str, DAGNode]:
    """
    Build a parent-child DAG keyed by span_id.

    Strategy:
      1. Trust explicit parent_span_id when both ends exist in the batch.
      2. For spans whose parent_span_id is missing or dangling, infer a
         parent using vector clocks: the maximum span that strictly
         precedes this one in causal order is the most likely parent.
    """
    by_id: Dict[str, Span] = {s.span_id: s for s in spans}
    nodes: Dict[str, DAGNode] = {sid: DAGNode(span=s) for sid, s in by_id.items()}

    for s in spans:
        parent_ids: List[str] = []
        explicit_parent = s.parent_span_id if s.parent_span_id and s.parent_span_id in by_id else ""

        # Step 1: explicit parent, if it landed in this batch.
        if explicit_parent:
            parent_ids.append(explicit_parent)
            nodes[s.span_id].parent_resolution = "explicit"
        else:
            # Step 2: infer using vector clocks. Keep immediate causal frontier.
            inferred = _causal_frontier_parents(s, spans)
            if inferred:
                parent_ids.extend(inferred)
                nodes[s.span_id].inferred_parent = True
                nodes[s.span_id].inferred_candidates = inferred
                nodes[s.span_id].parent_resolution = "inferred"

        # Step 3: preserve fan-in semantics from frontier even when explicit parent exists.
        if explicit_parent:
            frontier = _causal_frontier_parents(s, spans)
            extra = [pid for pid in frontier if pid != explicit_parent]
            if extra:
                nodes[s.span_id].parent_resolution = "explicit_plus_fanin"
                nodes[s.span_id].inferred_candidates = extra
                parent_ids.extend(extra)

        # Stable de-dup ordering.
        deduped: List[str] = []
        for pid in parent_ids:
            if pid and pid not in deduped and pid != s.span_id:
                deduped.append(pid)
        nodes[s.span_id].parent_ids = deduped
        if not deduped and not nodes[s.span_id].inferred_parent:
            nodes[s.span_id].parent_resolution = "root"

        for pid in deduped:
            nodes[pid].children.append(s.span_id)

    return nodes


def find_roots(nodes: Dict[str, DAGNode]) -> List[str]:
    """Spans that no one points to — the entry points of the trace."""
    has_parent = set()
    for n in nodes.values():
        for c in n.children:
            has_parent.add(c)
    return [sid for sid in nodes if sid not in has_parent]


def detect_gaps(nodes: Dict[str, DAGNode]) -> List[str]:
    """Span IDs whose parent had to be inferred — useful for the UI."""
    return [sid for sid, n in nodes.items() if n.inferred_parent]


def serialize_dag(nodes: Dict[str, DAGNode]) -> List[dict]:
    """Flat JSON-friendly representation."""
    out = []
    for sid, n in nodes.items():
        s = n.span
        primary_parent = n.parent_ids[0] if n.parent_ids else s.parent_span_id
        out.append({
            "span_id": sid,
            "parent_span_id": primary_parent,
            "parent_span_ids": n.parent_ids,
            "inferred_parent": n.inferred_parent,
            "parent_resolution": n.parent_resolution,
            "inferred_candidates": n.inferred_candidates,
            "children": n.children,
            "agent_id": s.agent_id,
            "event_type": s.event_type,
            "vector_clock": s.vector_clock,
            "latency_ms": s.latency_ms,
            "input_tokens": s.input_tokens,
            "output_tokens": s.output_tokens,
            "start_time_ms": s.start_time_ms,
        })
    # Stable sort by start time so the timeline UI gets a sensible default order.
    out.sort(key=lambda x: x["start_time_ms"])
    return out
