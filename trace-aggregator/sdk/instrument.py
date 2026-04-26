"""
The decorator your teammates apply to every LangGraph node.

Usage:
    from sdk.instrument import instrument_node, new_trace_context

    @instrument_node("research_agent")
    def research(state):
        ...
        return {"messages": [...]}

The wrapper:
  1. Reads the incoming vector clock from `state` (or starts fresh).
  2. Increments this agent's component of the clock.
  3. Times execution + captures errors.
  4. Builds a span and ships it via the gRPC client (non-blocking).
  5. Updates state with the new clock + this span_id as parent for the next node.

The SDK leaves your node's domain logic untouched — it only adds tracing fields
to the returned state dict.
"""
from __future__ import annotations

import functools
import json
import time
import traceback
import uuid
from typing import Any, Callable, Dict, Iterable, Optional

from generated import tracing_pb2

from .client import emit_decision as _emit_decision
from .client import emit_span

# Reserved keys we inject into the LangGraph state.
TRACE_ID_KEY = "_trace_id"
VECTOR_CLOCK_KEY = "_vector_clock"
PARENT_SPAN_KEY = "_parent_span_id"

METADATA_CHAR_LIMIT = 4_000  # truncate big prompts so spans stay small
ALLOWED_DECISION_TYPES = {"agent_handoff", "tool_select", "route_branch"}


def new_trace_context() -> Dict[str, Any]:
    """Build the initial tracing fields to merge into your initial state."""
    return {
        TRACE_ID_KEY: str(uuid.uuid4()),
        VECTOR_CLOCK_KEY: {},
        PARENT_SPAN_KEY: "",
    }


def _truncate(text: str, limit: int = METADATA_CHAR_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[truncated {len(text) - limit} chars]"


def _extract_token_counts(result: Any) -> tuple[int, int]:
    """
    Best-effort token extraction. LangChain/LangGraph responses vary widely.
    Falls back to (0, 0) and lets the engine note "unknown".
    Teams can override this by setting `_input_tokens` / `_output_tokens`
    in the returned state dict directly.
    """
    if isinstance(result, dict):
        if "_input_tokens" in result or "_output_tokens" in result:
            return int(result.get("_input_tokens", 0)), int(result.get("_output_tokens", 0))
    return 0, 0


def _normalize_candidates(candidates: Iterable[Dict[str, Any]]) -> list[tracing_pb2.DecisionCandidate]:
    normalized: list[tracing_pb2.DecisionCandidate] = []
    for cand in candidates:
        if not cand:
            continue
        normalized.append(
            tracing_pb2.DecisionCandidate(
                candidate_id=str(cand.get("candidate_id", "")),
                candidate_type=str(cand.get("candidate_type", "")),
                score=float(cand.get("score", 0.0) or 0.0),
                reason=_truncate(str(cand.get("reason", ""))),
            )
        )
    return normalized


def emit_decision(
    *,
    trace_id: str,
    source_span_id: str,
    actor_agent_id: str,
    decision_type: str,
    selected_candidate_id: str,
    confidence: float,
    rationale_summary: str,
    evidence_refs: Optional[list[str]] = None,
    candidates: Optional[list[Dict[str, Any]]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Emit a first-class decision event.
    Returns the generated decision_id to support local correlation/debugging.
    """
    if decision_type not in ALLOWED_DECISION_TYPES:
        raise ValueError(
            f"decision_type must be one of {sorted(ALLOWED_DECISION_TYPES)}, got {decision_type}"
        )
    decision_id = str(uuid.uuid4())
    payload = tracing_pb2.DecisionEvent(
        trace_id=str(trace_id),
        decision_id=decision_id,
        source_span_id=str(source_span_id),
        actor_agent_id=str(actor_agent_id),
        decision_type=decision_type,
        selected_candidate_id=str(selected_candidate_id),
        confidence=float(confidence),
        rationale_summary=_truncate(str(rationale_summary), 512),
        evidence_refs=[str(e) for e in (evidence_refs or [])],
        candidates=_normalize_candidates(candidates or []),
        timestamp_ms=int(time.time() * 1000),
        metadata=_truncate(json.dumps(metadata or {}, default=str)),
    )
    _emit_decision(payload)
    return decision_id


def instrument_decision(
    actor_agent_id: str,
    *,
    decision_type: str,
) -> Callable:
    """
    Decorator for helper functions that return a decision payload dict.
    The wrapped function should return keys consumed by emit_decision.
    """

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapped(state: Dict[str, Any], *args, **kwargs):
            result = fn(state, *args, **kwargs) or {}
            if not isinstance(result, dict):
                raise TypeError("instrument_decision functions must return a dict")

            trace_id = str(state.get(TRACE_ID_KEY, ""))
            source_span_id = str(state.get(PARENT_SPAN_KEY, ""))
            if trace_id and source_span_id:
                emit_decision(
                    trace_id=trace_id,
                    source_span_id=source_span_id,
                    actor_agent_id=actor_agent_id,
                    decision_type=decision_type,
                    selected_candidate_id=str(result.get("selected_candidate_id", "")),
                    confidence=float(result.get("confidence", 0.0) or 0.0),
                    rationale_summary=str(result.get("rationale_summary", "")),
                    evidence_refs=[str(x) for x in result.get("evidence_refs", [])],
                    candidates=result.get("candidates", []),
                    metadata=result.get("metadata", {}),
                )
            return result

        return wrapped

    return decorator


def instrument_node(
    agent_name: str,
    *,
    event_type: str = "llm_call",
    capture_metadata: bool = True,
) -> Callable:
    """
    Wrap a LangGraph node so every invocation emits a span.

    Args:
        agent_name: Logical name. Shows up in the Blame View — pick something
                    the team will recognize ("research_agent", not "node_3").
        event_type: One of "llm_call", "tool_use", "agent_handoff".
                    Errors are auto-promoted to "error".
        capture_metadata: Include a JSON snapshot of input + output state.
                    Disable for sensitive workflows.
    """

    def decorator(node_func: Callable) -> Callable:
        @functools.wraps(node_func)
        def wrapped(state: Dict[str, Any], *args, **kwargs):
            trace_id = state.get(TRACE_ID_KEY)
            if not trace_id:
                # Auto-init if the team forgot to call new_trace_context.
                trace_id = str(uuid.uuid4())

            clock: Dict[str, int] = dict(state.get(VECTOR_CLOCK_KEY) or {})
            clock[agent_name] = clock.get(agent_name, 0) + 1

            parent_span_id = state.get(PARENT_SPAN_KEY) or ""
            span_id = str(uuid.uuid4())

            start_wall_ms = int(time.time() * 1000)
            t0 = time.perf_counter()

            error: Optional[BaseException] = None
            result: Any = None
            try:
                result = node_func(state, *args, **kwargs)
            except BaseException as e:  # noqa: BLE001  — we re-raise after emitting
                error = e

            latency_ms = int((time.perf_counter() - t0) * 1000)

            in_tok, out_tok = _extract_token_counts(result)

            # Build the metadata payload
            meta: Dict[str, Any] = {}
            if capture_metadata:
                # Strip our own internal keys before serializing user state.
                user_state = {
                    k: v for k, v in state.items()
                    if not k.startswith("_")
                }
                meta["input_state_keys"] = list(user_state.keys())
                if isinstance(result, dict):
                    meta["output_state_keys"] = [k for k in result.keys() if not k.startswith("_")]
            if error is not None:
                meta["error_type"] = type(error).__name__
                meta["error_message"] = str(error)
                meta["traceback"] = _truncate(traceback.format_exc())

            span = tracing_pb2.AgentSpan(
                trace_id=trace_id,
                span_id=span_id,
                parent_span_id=parent_span_id,
                agent_id=agent_name,
                vector_clock={k: int(v) for k, v in clock.items()},
                event_type="error" if error else event_type,
                input_tokens=in_tok,
                output_tokens=out_tok,
                latency_ms=latency_ms,
                start_time_ms=start_wall_ms,
                metadata=_truncate(json.dumps(meta, default=str)),
            )
            emit_span(span)

            if error is not None:
                raise error  # preserve user-visible behavior

            # Merge tracing fields back into state for the next node.
            if not isinstance(result, dict):
                # Some LangGraph nodes return None/list. Pass tracing through state by attaching.
                result = {} if result is None else {"_node_return": result}

            result[TRACE_ID_KEY] = trace_id
            result[VECTOR_CLOCK_KEY] = clock
            result[PARENT_SPAN_KEY] = span_id
            return result

        return wrapped

    return decorator
