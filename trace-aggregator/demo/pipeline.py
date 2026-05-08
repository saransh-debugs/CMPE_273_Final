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

import json
import os
import random
import time
import urllib.error
import urllib.request
from typing import Annotated, Any, Dict, List, TypedDict

from langgraph.graph import StateGraph, END

from sdk import emit_decision, instrument_node, new_trace_context
from sdk.core import register_coverage_point, mark_covered

register_coverage_point("orchestrator_dispatch", "orchestrator fans out to research+coder")
register_coverage_point("research_tool_select", "research agent selects LLM tool")
register_coverage_point("coder_tool_select", "coder agent selects coding tool")
register_coverage_point("coder_error_halt", "coder halts on hallucinated import")
register_coverage_point("reviewer_route", "reviewer routes to next branch")


# --- State -------------------------------------------------------------------

def _add_messages(left: List[str], right: List[str]) -> List[str]:
    return (left or []) + (right or [])


def _merge_trace_id(left: str, right: str) -> str:
    """Trace id must remain stable across parallel branches."""
    if left and right and left != right:
        raise ValueError(f"Conflicting trace ids at merge: {left} vs {right}")
    return left or right or ""


def _merge_vector_clock(left: Dict[str, int], right: Dict[str, int]) -> Dict[str, int]:
    """Vector clock merge = component-wise max."""
    merged: Dict[str, int] = dict(left or {})
    for agent, counter in (right or {}).items():
        merged[agent] = max(int(merged.get(agent, 0)), int(counter))
    return merged


def _merge_parent_span(left: str, right: str) -> str:
    """
    Merge helper for joined branches.
    We keep one deterministic parent id (lexicographically smallest non-empty).
    Full causality remains reconstructible from vector clocks.
    """
    candidates = [p for p in (left, right) if p]
    if not candidates:
        return ""
    return min(candidates)


class AgentState(TypedDict, total=False):
    messages: Annotated[List[str], _add_messages]
    research_findings: str
    code: str
    review: str

    # SDK-managed (auto-injected)
    _trace_id: Annotated[str, _merge_trace_id]
    _vector_clock: Annotated[Dict[str, int], _merge_vector_clock]
    _parent_span_id: Annotated[str, _merge_parent_span]
    _input_text: str


# --- Mock LLM ----------------------------------------------------------------

DEMO_MODE = os.environ.get("DEMO_MODE", "true").lower() in {"1", "true", "yes", "on"}
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
DEMO_FAILURE_RATE = float(os.environ.get("DEMO_CODER_FAILURE_RATE", "0.5"))
REQUEST_TIMEOUT_SEC = float(os.environ.get("LLM_TIMEOUT_SEC", "30"))


def _mock_llm(latency_range=(0.1, 0.4), tokens=(100, 200)) -> Dict[str, int]:
    time.sleep(random.uniform(*latency_range))
    return {
        "_input_tokens": random.randint(*tokens),
        "_output_tokens": random.randint(*tokens),
    }


def _call_openai_compatible(prompt: str, *, system: str = "You are a helpful agent.") -> Dict[str, Any]:
    if not OPENAI_API_KEY:
        raise RuntimeError(
            "DEMO_MODE=false but OPENAI_API_KEY is missing. "
            "Set OPENAI_API_KEY (or switch back to DEMO_MODE=true)."
        )
    payload = {
        "model": OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        f"{OPENAI_BASE_URL.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OPENAI_API_KEY}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SEC) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM HTTP {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"LLM connection error: {e}") from e

    choice = (raw.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    usage = raw.get("usage") or {}
    return {
        "content": msg.get("content", ""),
        "_input_tokens": int(usage.get("prompt_tokens", 0)),
        "_output_tokens": int(usage.get("completion_tokens", 0)),
    }


def _agent_llm(prompt: str, *, system: str) -> Dict[str, Any]:
    if DEMO_MODE:
        tok = _mock_llm((0.1, 0.4), (100, 250))
        return {"content": prompt[:80], **tok}
    return _call_openai_compatible(prompt, system=system)


def _decision_prompt(state: AgentState) -> str:
    return (
        "You are deciding the next branch for a reviewer agent.\n"
        "Return ONLY compact JSON with keys: selected_candidate_id, confidence, rationale_summary, "
        "evidence_refs (array), candidates (array of objects with candidate_id,candidate_type,score,reason).\n"
        f"Context keys present: {list((state or {}).keys())}\n"
        "Candidates: review, request_rework."
    )


def _decision_from_llm(state: AgentState) -> Dict[str, Any]:
    if DEMO_MODE:
        return {
            "selected_candidate_id": "review",
            "confidence": 0.92,
            "rationale_summary": "Both research and code outputs available; route to final review step.",
            "evidence_refs": ["research_findings", "code"],
            "candidates": [
                {
                    "candidate_id": "review",
                    "candidate_type": "branch",
                    "score": 0.92,
                    "reason": "all required artifacts present",
                },
                {
                    "candidate_id": "request_rework",
                    "candidate_type": "branch",
                    "score": 0.08,
                    "reason": "fallback when required inputs missing",
                },
            ],
        }
    res = _call_openai_compatible(
        _decision_prompt(state),
        system="Return only valid JSON for decision logging.",
    )
    content = (res.get("content") or "").strip()
    try:
        parsed = json.loads(content)
    except Exception:
        parsed = {
            "selected_candidate_id": "review",
            "confidence": 0.55,
            "rationale_summary": f"Model returned non-JSON; fallback used. Raw: {content[:180]}",
            "evidence_refs": ["fallback_json_parse_error"],
            "candidates": [],
        }
    return parsed


def _maybe_emit_decision(
    state: AgentState,
    payload: Dict[str, Any],
    coverage_point: str = "",
) -> None:
    trace_id = str(state.get("_trace_id", ""))
    source_span_id = str(state.get("_parent_span_id", ""))
    if not trace_id:
        return
    meta = dict(payload.get("metadata") or {})
    if coverage_point:
        meta["coverage_point"] = coverage_point
    emit_decision(
        trace_id=trace_id,
        source_span_id=source_span_id,
        actor_agent_id=str(payload.get("actor_agent_id", "unknown_agent")),
        decision_type=str(payload.get("decision_type", "route_branch")),
        selected_candidate_id=str(payload.get("selected_candidate_id", "unknown")),
        confidence=float(payload.get("confidence", 0.5)),
        rationale_summary=str(payload.get("rationale_summary", "decision emitted")),
        evidence_refs=[str(x) for x in payload.get("evidence_refs", [])],
        candidates=payload.get("candidates", []),
        metadata=meta,
    )
    if coverage_point:
        mark_covered(coverage_point, trace_id)


# --- Agents ------------------------------------------------------------------

@instrument_node("orchestrator")
def orchestrator(state: AgentState) -> Dict[str, Any]:
    if DEMO_MODE:
        reasoning = (
            "I have two independent tasks: market research and code generation. "
            "Neither depends on the other, so I'll fan out in parallel to minimize "
            "wall-clock time. Once both complete, the reviewer can cross-check."
        )
        tok = _mock_llm((0.05, 0.15), (50, 100))
    else:
        res = _agent_llm(
            "Create a concise execution plan: run research and code in parallel, then review.",
            system="You are an orchestrator agent for software workflow planning.",
        )
        reasoning = (res.get("content") or "")[:400]
        tok = res
    _maybe_emit_decision(
        state,
        {
            "actor_agent_id": "orchestrator",
            "decision_type": "agent_handoff",
            "selected_candidate_id": "parallel_research_coder",
            "confidence": 0.9 if DEMO_MODE else 0.78,
            "rationale_summary": "Dispatch research and coding in parallel to reduce total turnaround.",
            "evidence_refs": ["workflow=parallel_fanout", "goal=minimize_latency"],
            "candidates": [
                {
                    "candidate_id": "parallel_research_coder",
                    "candidate_type": "branch",
                    "score": 0.9 if DEMO_MODE else 0.78,
                    "reason": "maximizes throughput",
                },
                {
                    "candidate_id": "serial_research_then_coder",
                    "candidate_type": "branch",
                    "score": 0.1 if DEMO_MODE else 0.22,
                    "reason": "simpler ordering but slower",
                },
            ],
            "metadata": {"stage": "orchestrator_dispatch", "mode": "demo" if DEMO_MODE else "real", "reasoning": reasoning},
        },
        coverage_point="orchestrator_dispatch",
    )
    return {
        "messages": [f"orchestrator: dispatching research + code tasks ({'demo' if DEMO_MODE else 'real'})"],
        **tok,
    }


@instrument_node("research_agent")
def research(state: AgentState) -> Dict[str, Any]:
    if DEMO_MODE:
        reasoning = (
            "The task requires market analysis. I'll use the LLM to synthesize "
            "concise findings that downstream agents — the coder and reviewer — "
            "can consume without re-querying."
        )
    else:
        reasoning = ""
    _maybe_emit_decision(
        state,
        {
            "actor_agent_id": "research_agent",
            "decision_type": "tool_select",
            "selected_candidate_id": "llm_research_summary",
            "confidence": 0.86 if DEMO_MODE else 0.73,
            "rationale_summary": "Generate concise market findings for downstream coding and review.",
            "evidence_refs": ["task=market_analysis"],
            "candidates": [
                {
                    "candidate_id": "llm_research_summary",
                    "candidate_type": "tool",
                    "score": 0.86 if DEMO_MODE else 0.73,
                    "reason": "quick structured synthesis",
                }
            ],
            "metadata": {"stage": "research_execute", "mode": "demo" if DEMO_MODE else "real", "reasoning": reasoning},
        },
        coverage_point="research_tool_select",
    )
    if DEMO_MODE:
        tok = _mock_llm((0.4, 0.8), (200, 400))
        findings = "market size estimated at $X; key players: A, B, C"
    else:
        res = _agent_llm(
            "Give 3 concise bullets for market analysis of an AI observability product.",
            system="You are a research analyst agent. Be concise and factual.",
        )
        tok = {"_input_tokens": res["_input_tokens"], "_output_tokens": res["_output_tokens"]}
        findings = res["content"] or "research output unavailable"
    return {
        "research_findings": findings,
        "messages": ["research_agent: findings ready"],
        **tok,
    }


@instrument_node("coder_agent")
def coder(state: AgentState) -> Dict[str, Any]:
    if DEMO_MODE:
        reasoning = (
            "I need to produce a code artifact. The spec asks for a Python function, "
            "so I'll generate a candidate implementation. The reviewer will validate "
            "correctness before it ships."
        )
    else:
        reasoning = ""
    _maybe_emit_decision(
        state,
        {
            "actor_agent_id": "coder_agent",
            "decision_type": "tool_select",
            "selected_candidate_id": "write_python_function",
            "confidence": 0.79 if DEMO_MODE else 0.69,
            "rationale_summary": "Generate candidate implementation before reviewer validation.",
            "evidence_refs": ["task=produce_code_artifact"],
            "candidates": [
                {
                    "candidate_id": "write_python_function",
                    "candidate_type": "tool",
                    "score": 0.79 if DEMO_MODE else 0.69,
                    "reason": "directly satisfies coding task",
                }
            ],
            "metadata": {"stage": "coder_execute", "mode": "demo" if DEMO_MODE else "real", "reasoning": reasoning},
        },
        coverage_point="coder_tool_select",
    )
    if DEMO_MODE:
        tok = _mock_llm((0.3, 0.6), (300, 600))
    else:
        res = _agent_llm(
            "Write a tiny Python function that returns 42 with a short docstring.",
            system="You are a coding agent. Return only code.",
        )
        tok = {"_input_tokens": res["_input_tokens"], "_output_tokens": res["_output_tokens"]}
    if DEMO_MODE and random.random() < DEMO_FAILURE_RATE:
        _maybe_emit_decision(
            state,
            {
                "actor_agent_id": "coder_agent",
                "decision_type": "route_branch",
                "selected_candidate_id": "error_halt",
                "confidence": 1.0,
                "rationale_summary": "Reject: hallucinated import detected; halting execution.",
                "evidence_refs": ["error=hallucinated_import"],
                "candidates": [
                    {
                        "candidate_id": "error_halt",
                        "candidate_type": "branch",
                        "score": 1.0,
                        "reason": "unrecoverable hallucination",
                    }
                ],
                "metadata": {"stage": "coder_error", "mode": "demo" if DEMO_MODE else "real"},
            },
            coverage_point="coder_error_halt",
        )
        raise RuntimeError("Hallucinated import: `from anthropic import GalaxyBrain`")
    return {
        "code": "def solve():\n    return 42" if DEMO_MODE else res["content"],
        "messages": ["coder_agent: code drafted"],
        **tok,
    }


@instrument_node("reviewer_agent")
def reviewer(state: AgentState) -> Dict[str, Any]:
    decision = _decision_from_llm(state)
    if DEMO_MODE:
        reasoning = (
            "Both the research findings and the code artifact have arrived. "
            "I'll cross-check the code against the research context and verify "
            "correctness before approving."
        )
    else:
        reasoning = str(decision.get("rationale_summary", ""))[:400]
    _maybe_emit_decision(
        state,
        {
            "actor_agent_id": "reviewer_agent",
            "decision_type": "route_branch",
            "selected_candidate_id": str(decision.get("selected_candidate_id", "review")),
            "confidence": float(decision.get("confidence", 0.5)),
            "rationale_summary": str(decision.get("rationale_summary", "decision rationale unavailable")),
            "evidence_refs": [str(x) for x in decision.get("evidence_refs", [])],
            "candidates": decision.get("candidates", []),
            "metadata": {
                "component": "demo.pipeline",
                "stage": "reviewer_entry",
                "mode": "demo" if DEMO_MODE else "real",
                "fallback_used": "fallback_json_parse_error" in decision.get("evidence_refs", []),
                "reasoning": reasoning,
            },
        },
        coverage_point="reviewer_route",
    )
    if DEMO_MODE:
        tok = _mock_llm((0.15, 0.3), (100, 200))
        review_text = "LGTM with nits"
    else:
        res = _agent_llm(
            f"Review this code for correctness and style:\n{state.get('code','')}",
            system="You are a reviewer agent. Return a brief code review.",
        )
        tok = {"_input_tokens": res["_input_tokens"], "_output_tokens": res["_output_tokens"]}
        review_text = res["content"] or "review unavailable"
    return {
        "review": review_text,
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


DEMO_TASKS = [
    "Research the AI observability market and write a Python function that returns 42.",
    "Analyze competitor pricing for trace aggregation tools, then code a health-check endpoint.",
    "Summarize recent LLM-ops trends and produce a minimal data pipeline in Python.",
    "Gather market sizing data for multi-agent platforms and implement a retry decorator.",
    "Survey open-source tracing libraries and write a span-batching utility function.",
]


def run_once(task: str = "") -> None:
    app = build_graph()
    state: AgentState = {
        "messages": ["start"],
        **new_trace_context(input_text=task),
    }
    print(f"→ Running trace_id={state['_trace_id']}")
    if task:
        print(f"  task: {task}")
    try:
        final = app.invoke(state)
        print(f"✓ Pipeline finished. Messages: {final.get('messages')}")
    except Exception as e:
        print(f"✗ Pipeline raised: {type(e).__name__}: {e}")
    time.sleep(0.5)


def main():
    n = 5
    print(f"Running {n} pipeline executions... mode={'demo' if DEMO_MODE else 'real'} model={OPENAI_MODEL if not DEMO_MODE else 'mock'}")
    for i in range(n):
        print(f"\n--- Run {i + 1}/{n} ---")
        run_once(task=DEMO_TASKS[i % len(DEMO_TASKS)])
    print("\nAll done. Spans should be in ClickHouse — check the API:")
    print("  curl http://localhost:8000/traces")


if __name__ == "__main__":
    main()
