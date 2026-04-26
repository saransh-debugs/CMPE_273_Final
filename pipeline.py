"""
A runnable multi-agent demo. Uses LangGraph topology but mocks the LLM calls
so it works offline and on any laptop — no API keys required.

Topology:
        orchestrator
           /      \\
     research    coder
           \\      /
           reviewer

The coder agent has a 50% chance of "hallucinating" and raising an error
on this run, which is exactly the kind of failure we want our Blame View
to surface.

Run (with collector + ClickHouse already up):
    python -m demo.pipeline
"""
from __future__ import annotations

import random
import time
from typing import Annotated, Any, Dict, List, TypedDict

from langgraph.graph import StateGraph, END

from sdk import instrument_node, new_trace_context


# --- State -------------------------------------------------------------------

def _add_messages(left: List[str], right: List[str]) -> List[str]:
    return (left or []) + (right or [])


class AgentState(TypedDict, total=False):
    messages: Annotated[List[str], _add_messages]
    research_findings: str
    code: str
    review: str

    # SDK-managed (auto-injected)
    _trace_id: str
    _vector_clock: Dict[str, int]
    _parent_span_id: str


# --- Mock LLM ----------------------------------------------------------------

def _mock_llm(latency_range=(0.1, 0.4), tokens=(100, 200)) -> Dict[str, int]:
    time.sleep(random.uniform(*latency_range))
    return {
        "_input_tokens": random.randint(*tokens),
        "_output_tokens": random.randint(*tokens),
    }


# --- Agents ------------------------------------------------------------------

@instrument_node("orchestrator")
def orchestrator(state: AgentState) -> Dict[str, Any]:
    tok = _mock_llm((0.05, 0.15), (50, 100))
    return {
        "messages": ["orchestrator: dispatching research + code tasks"],
        **tok,
    }


@instrument_node("research_agent")
def research(state: AgentState) -> Dict[str, Any]:
    tok = _mock_llm((0.4, 0.8), (200, 400))  # research is slow + chatty
    return {
        "research_findings": "market size estimated at $X; key players: A, B, C",
        "messages": ["research_agent: findings ready"],
        **tok,
    }


@instrument_node("coder_agent")
def coder(state: AgentState) -> Dict[str, Any]:
    tok = _mock_llm((0.3, 0.6), (300, 600))
    if random.random() < 0.5:
        raise RuntimeError("Hallucinated import: `from anthropic import GalaxyBrain`")
    return {
        "code": "def solve(): return 42",
        "messages": ["coder_agent: code drafted"],
        **tok,
    }


@instrument_node("reviewer_agent")
def reviewer(state: AgentState) -> Dict[str, Any]:
    tok = _mock_llm((0.15, 0.3), (100, 200))
    return {
        "review": "LGTM with nits",
        "messages": ["reviewer_agent: review complete"],
        **tok,
    }


# --- Graph -------------------------------------------------------------------

def build_graph():
    g = StateGraph(AgentState)
    g.add_node("orchestrator", orchestrator)
    g.add_node("research", research)
    g.add_node("coder", coder)
    g.add_node("reviewer", reviewer)

    g.set_entry_point("orchestrator")
    # Fan out: orchestrator -> research and coder
    g.add_edge("orchestrator", "research")
    g.add_edge("orchestrator", "coder")
    # Both join into reviewer
    g.add_edge("research", "reviewer")
    g.add_edge("coder", "reviewer")
    g.add_edge("reviewer", END)
    return g.compile()


def run_once() -> None:
    app = build_graph()
    state: AgentState = {
        "messages": ["start"],
        **new_trace_context(),
    }
    print(f"→ Running trace_id={state['_trace_id']}")
    try:
        final = app.invoke(state)
        print(f"✓ Pipeline finished. Messages: {final.get('messages')}")
    except Exception as e:
        print(f"✗ Pipeline raised: {type(e).__name__}: {e}")
    # Give the SDK background thread a moment to flush.
    time.sleep(0.5)


def main():
    n = 5
    print(f"Running {n} pipeline executions...")
    for i in range(n):
        print(f"\n--- Run {i + 1}/{n} ---")
        run_once()
    print("\nAll done. Spans should be in ClickHouse — check the API:")
    print("  curl http://localhost:8000/traces")


if __name__ == "__main__":
    main()
