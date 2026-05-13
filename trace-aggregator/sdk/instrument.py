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
from typing import Any, Callable, Dict, Optional

from .client import emit_decision as _emit_decision
from .client import emit_span
from .core import (
    ALLOWED_DECISION_TYPES,
    PARENT_SPAN_KEY,
    TENANT_ID_KEY,
    TRACE_ID_KEY,
    VECTOR_CLOCK_KEY,
    begin_span,
    build_decision_fallback,
    build_span,
    emit_decision_event,
    new_trace_context,
    normalize_node_result,
    validate_decision_payload,
    mark_covered,
)


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
    return emit_decision_event(
        trace_id=trace_id,
        source_span_id=source_span_id,
        actor_agent_id=actor_agent_id,
        decision_type=decision_type,
        selected_candidate_id=selected_candidate_id,
        confidence=confidence,
        rationale_summary=rationale_summary,
        evidence_refs=evidence_refs,
        candidates=candidates,
        metadata=metadata,
        emit_fn=_emit_decision,
    )


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
                    tenant_id=str(state.get(TENANT_ID_KEY, "")),
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


def decide_then_act(
    decision_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
    action_fn: Callable[[Dict[str, Any]], Any],
    *,
    actor_agent_id: str,
    decision_type: str,
    coverage_point: str = "",
) -> Callable[[Dict[str, Any]], Any]:
    """
    LLM-02: enforce that a decision is emitted BEFORE the action executes.
    decision_fn must return a dict with keys consumed by emit_decision.
    If decision_fn raises, action_fn never runs.
    """
    def wrapped(state: Dict[str, Any]) -> Any:
        payload = decision_fn(state)
        trace_id = str(state.get(TRACE_ID_KEY, ""))
        source_span_id = str(state.get(PARENT_SPAN_KEY, ""))
        if trace_id and source_span_id:
            meta = dict(payload.get("metadata") or {})
            if coverage_point:
                meta["coverage_point"] = coverage_point
            emit_decision(
                trace_id=trace_id,
                source_span_id=source_span_id,
                actor_agent_id=actor_agent_id,
                tenant_id=str(state.get(TENANT_ID_KEY, "")),
                decision_type=decision_type,
                selected_candidate_id=str(payload.get("selected_candidate_id", "")),
                confidence=float(payload.get("confidence", 0.0) or 0.0),
                rationale_summary=str(payload.get("rationale_summary", "")),
                evidence_refs=[str(x) for x in payload.get("evidence_refs", [])],
                candidates=payload.get("candidates", []),
                metadata=meta,
            )
            if coverage_point:
                mark_covered(coverage_point, trace_id)
        return action_fn(state)

    return wrapped


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
            span_ctx = begin_span(
                agent_name=agent_name,
                trace_id=state.get(TRACE_ID_KEY),
                vector_clock=state.get(VECTOR_CLOCK_KEY),
                parent_span_id=state.get(PARENT_SPAN_KEY),
                tenant_id=str(state.get(TENANT_ID_KEY, "")),
            )
            state[PARENT_SPAN_KEY] = span_ctx.span_id

            error: Optional[BaseException] = None
            result: Any = None
            try:
                result = node_func(state, *args, **kwargs)
            except BaseException as e:  # noqa: BLE001
                error = e
            span = build_span(
                ctx=span_ctx,
                agent_name=agent_name,
                event_type=event_type,
                state=state,
                result=result,
                error=error,
                capture_metadata=capture_metadata,
            )
            emit_span(span)

            if error is not None:
                raise error

            out = normalize_node_result(result)
            out[TRACE_ID_KEY] = span_ctx.trace_id
            out[VECTOR_CLOCK_KEY] = span_ctx.vector_clock
            out[PARENT_SPAN_KEY] = span_ctx.span_id
            out[TENANT_ID_KEY] = span_ctx.tenant_id
            return out

        return wrapped

    return decorator
