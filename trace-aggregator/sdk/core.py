from __future__ import annotations

import json
import os
import re
import time
import traceback
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Tuple

from pydantic import BaseModel, Field, ValidationError

from generated import tracing_pb2

METADATA_CHAR_LIMIT = 4_000
TRACE_ID_KEY = "_trace_id"
VECTOR_CLOCK_KEY = "_vector_clock"
PARENT_SPAN_KEY = "_parent_span_id"
INPUT_TEXT_KEY = "_input_text"

ALLOWED_DECISION_TYPES = {"agent_handoff", "tool_select", "route_branch"}

DEFAULT_REDACT_KEYS = {
    "password", "api_key", "token", "secret", "authorization", "cookie",
}
REDACT_KEYS: set[str] = {
    k.strip().lower()
    for k in os.environ.get("TRACE_REDACT_KEYS", "").split(",")
    if k.strip()
} | DEFAULT_REDACT_KEYS

_REDACT_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'), '[EMAIL]'),
    (re.compile(r'\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b'), '[CARD]'),
    (re.compile(r'\bBearer\s+[A-Za-z0-9\-._~+/]+=*\b'), '[BEARER]'),
]


def add_redact_key(key: str) -> None:
    REDACT_KEYS.add(key.strip().lower())


# LLM-03: structured parse-failure taxonomy
class ParseFailureReason(str, Enum):
    INVALID_JSON     = "invalid_json"
    SCHEMA_MISMATCH  = "schema_mismatch"
    MISSING_REQUIRED = "missing_required_field"
    BAD_TYPE         = "bad_field_type"
    CONFIDENCE_RANGE = "confidence_out_of_range"
    UNKNOWN          = "unknown_parse_error"


# LLM-05: rationale quality rubric
RATIONALE_ACTION_VERBS = {
    "route", "delegate", "select", "dispatch", "reject", "approve",
    "escalate", "retry", "skip", "generate", "assign", "forward",
    "invoke", "trigger", "abort",
}
_FILLER_PHRASES = {
    "as an ai", "i will", "please note", "it should be noted", "in conclusion",
}
RATIONALE_PROMPT_TEMPLATE = (
    "Provide a rationale_summary that: "
    "starts with an action verb ({verbs}), "
    "is 10-512 characters, "
    "explains WHY the decision was made, "
    "contains no filler phrases."
).format(verbs=", ".join(sorted(RATIONALE_ACTION_VERBS)))


# LLM-01: decision coverage registry
DECISION_COVERAGE_POINTS: Dict[str, str] = {}
_coverage_hits: Dict[str, set[str]] = {}


def register_coverage_point(point_id: str, description: str = "") -> None:
    DECISION_COVERAGE_POINTS[point_id] = description


def mark_covered(point_id: str, trace_id: str) -> None:
    _coverage_hits.setdefault(point_id, set()).add(trace_id)


def get_coverage_report() -> Dict[str, Any]:
    return {
        pid: {
            "description": desc,
            "hit_count": len(_coverage_hits.get(pid, set())),
        }
        for pid, desc in DECISION_COVERAGE_POINTS.items()
    }


class DecisionCandidateModel(BaseModel):
    candidate_id: str = Field(default="")
    candidate_type: str = Field(default="")
    score: float = Field(default=0.0)
    reason: str = Field(default="")


class DecisionPayloadModel(BaseModel):
    trace_id: str
    decision_id: str
    source_span_id: str
    actor_agent_id: str
    decision_type: str
    selected_candidate_id: str
    confidence: float = Field(ge=0.0, le=1.0)
    rationale_summary: str = Field(max_length=512)
    evidence_refs: list[str] = Field(default_factory=list)
    candidates: list[DecisionCandidateModel] = Field(default_factory=list)
    timestamp_ms: int
    metadata: str = Field(default="")


@dataclass(frozen=True)
class SpanContext:
    trace_id: str
    span_id: str
    parent_span_id: str
    vector_clock: Dict[str, int]
    start_time_ms: int
    t0_perf: float


def new_trace_context(input_text: str = "") -> Dict[str, Any]:
    ctx: Dict[str, Any] = {
        TRACE_ID_KEY: str(uuid.uuid4()),
        VECTOR_CLOCK_KEY: {},
        PARENT_SPAN_KEY: "",
    }
    if input_text:
        ctx[INPUT_TEXT_KEY] = input_text
    return ctx


def truncate_text(text: str, limit: int = METADATA_CHAR_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[truncated {len(text) - limit} chars]"


def extract_token_counts(result: Any) -> tuple[int, int]:
    if isinstance(result, dict):
        if "_input_tokens" in result or "_output_tokens" in result:
            return int(result.get("_input_tokens", 0)), int(result.get("_output_tokens", 0))
    return 0, 0


def normalize_candidates(
    candidates: Iterable[Dict[str, Any]],
) -> list[tracing_pb2.DecisionCandidate]:
    out: list[tracing_pb2.DecisionCandidate] = []
    for cand in candidates:
        if not cand:
            continue
        out.append(
            tracing_pb2.DecisionCandidate(
                candidate_id=str(cand.get("candidate_id", "")),
                candidate_type=str(cand.get("candidate_type", "")),
                score=float(cand.get("score", 0.0) or 0.0),
                reason=truncate_text(str(cand.get("reason", ""))),
            )
        )
    return out


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: "[REDACTED]" if str(k).lower() in REDACT_KEYS else redact_sensitive(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(v) for v in value]
    if isinstance(value, str):
        for pattern, replacement in _REDACT_PATTERNS:
            value = pattern.sub(replacement, value)
        return value
    return value


# LLM-05
def validate_rationale(text: str) -> list[str]:
    violations: list[str] = []
    if len(text) < 10:
        violations.append("length: must be at least 10 characters")
    if len(text) > 512:
        violations.append("length: exceeds 512 character limit")
    words = text.strip().split()
    first_word = words[0].lower().rstrip(".,;:") if words else ""
    if first_word not in RATIONALE_ACTION_VERBS:
        violations.append(
            f"action verb: must start with one of {sorted(RATIONALE_ACTION_VERBS)!r}, "
            f"got '{first_word}'"
        )
    lower = text.lower()
    for phrase in _FILLER_PHRASES:
        if phrase in lower:
            violations.append(f"filler phrase: contains '{phrase}'")
    return violations


# LLM-03
def validate_and_parse_llm_json(
    raw: str,
    *,
    trace_id: str = "",
    source_span_id: str = "",
    actor_agent_id: str = "",
    decision_type: str = "",
) -> Tuple[Optional[Dict[str, Any]], Optional[ParseFailureReason]]:
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None, ParseFailureReason.INVALID_JSON

    if not isinstance(parsed, dict):
        return None, ParseFailureReason.SCHEMA_MISMATCH

    required = {"selected_candidate_id", "confidence", "rationale_summary"}
    if required - parsed.keys():
        return None, ParseFailureReason.MISSING_REQUIRED

    try:
        confidence = float(parsed["confidence"])
    except (TypeError, ValueError):
        return None, ParseFailureReason.BAD_TYPE

    if not (0.0 <= confidence <= 1.0):
        return None, ParseFailureReason.CONFIDENCE_RANGE

    return parsed, None


def validate_decision_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    return DecisionPayloadModel(**payload).model_dump()


def build_decision_fallback(
    *,
    trace_id: str,
    source_span_id: str,
    actor_agent_id: str,
    decision_type: str,
    selected_candidate_id: str = "fallback",
    reason: str = "decision_payload_validation_failed",
    parse_failure_reason: Optional[ParseFailureReason] = None,
) -> Dict[str, Any]:
    meta: Dict[str, Any] = {"fallback_used": True, "parse_error_reason": reason}
    if parse_failure_reason is not None:
        meta["parse_failure_reason"] = parse_failure_reason.value
    return {
        "trace_id": trace_id,
        "decision_id": str(uuid.uuid4()),
        "source_span_id": source_span_id,
        "actor_agent_id": actor_agent_id,
        "decision_type": decision_type,
        "selected_candidate_id": selected_candidate_id,
        "confidence": 0.0,
        "rationale_summary": "Fallback decision emitted due to invalid decision payload.",
        "evidence_refs": [reason],
        "candidates": [],
        "timestamp_ms": int(time.time() * 1000),
        "metadata": json.dumps(meta),
    }


def emit_decision_event(
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
    emit_fn: Callable[[tracing_pb2.DecisionEvent], None],
) -> str:
    if decision_type not in ALLOWED_DECISION_TYPES:
        raise ValueError(
            f"decision_type must be one of {sorted(ALLOWED_DECISION_TYPES)}, got {decision_type}"
        )
    decision_id = str(uuid.uuid4())

    raw_meta = dict(metadata or {})
    violations = validate_rationale(str(rationale_summary))
    if violations:
        raw_meta["rationale_violations"] = violations

    raw_payload = {
        "trace_id": str(trace_id),
        "decision_id": decision_id,
        "source_span_id": str(source_span_id),
        "actor_agent_id": str(actor_agent_id),
        "decision_type": decision_type,
        "selected_candidate_id": str(selected_candidate_id),
        "confidence": float(confidence),
        "rationale_summary": truncate_text(str(rationale_summary), 512),
        "evidence_refs": [str(e) for e in (evidence_refs or [])],
        "candidates": [
            {
                "candidate_id": str(c.get("candidate_id", "")),
                "candidate_type": str(c.get("candidate_type", "")),
                "score": float(c.get("score", 0.0) or 0.0),
                "reason": truncate_text(str(c.get("reason", ""))),
            }
            for c in (candidates or [])
            if isinstance(c, dict)
        ],
        "timestamp_ms": int(time.time() * 1000),
        "metadata": truncate_text(json.dumps(redact_sensitive(raw_meta), default=str)),
    }
    try:
        normalized = validate_decision_payload(raw_payload)
    except ValidationError as e:
        normalized = build_decision_fallback(
            trace_id=str(trace_id),
            source_span_id=str(source_span_id),
            actor_agent_id=str(actor_agent_id),
            decision_type=decision_type,
            reason=str(e.errors()[0].get("msg", "validation_error")),
        )
        decision_id = normalized["decision_id"]

    proto = tracing_pb2.DecisionEvent(
        trace_id=normalized["trace_id"],
        decision_id=normalized["decision_id"],
        source_span_id=normalized["source_span_id"],
        actor_agent_id=normalized["actor_agent_id"],
        decision_type=normalized["decision_type"],
        selected_candidate_id=normalized["selected_candidate_id"],
        confidence=float(normalized["confidence"]),
        rationale_summary=str(normalized["rationale_summary"]),
        evidence_refs=[str(e) for e in normalized["evidence_refs"]],
        candidates=normalize_candidates(normalized["candidates"]),
        timestamp_ms=int(normalized["timestamp_ms"]),
        metadata=str(normalized["metadata"]),
    )
    emit_fn(proto)
    return decision_id


def begin_span(
    *,
    agent_name: str,
    trace_id: Optional[str],
    vector_clock: Optional[Mapping[str, int]],
    parent_span_id: Optional[str],
) -> SpanContext:
    next_trace_id = str(trace_id) if trace_id else str(uuid.uuid4())
    clock: Dict[str, int] = dict(vector_clock or {})
    clock[agent_name] = clock.get(agent_name, 0) + 1
    return SpanContext(
        trace_id=next_trace_id,
        span_id=str(uuid.uuid4()),
        parent_span_id=parent_span_id or "",
        vector_clock=clock,
        start_time_ms=int(time.time() * 1000),
        t0_perf=time.perf_counter(),
    )


def build_span(
    *,
    ctx: SpanContext,
    agent_name: str,
    event_type: str,
    state: Mapping[str, Any],
    result: Any,
    error: Optional[BaseException],
    capture_metadata: bool = True,
    token_counter: Optional[Callable[[Any], tuple[int, int]]] = None,
) -> tracing_pb2.AgentSpan:
    from .taxonomy import classify_error  # late import avoids circular

    latency_ms = int((time.perf_counter() - ctx.t0_perf) * 1000)
    token_counter_fn = token_counter or extract_token_counts
    in_tok, out_tok = token_counter_fn(result)

    meta: Dict[str, Any] = {}
    if capture_metadata:
        user_state = {k: v for k, v in state.items() if not str(k).startswith("_")}
        meta["input_state_keys"] = list(user_state.keys())
        if isinstance(result, dict):
            meta["output_state_keys"] = [k for k in result if not str(k).startswith("_")]

    input_text = state.get(INPUT_TEXT_KEY)
    if input_text:
        # LLM-06: redact PII before storing input_text in span metadata
        meta["input_text"] = truncate_text(redact_sensitive(str(input_text)), 1000)

    if error is not None:
        meta["error_type"] = type(error).__name__
        meta["error_message"] = str(error)
        meta["traceback"] = truncate_text(traceback.format_exc())
        # LLM-09: semantic failure classification
        meta["semantic_failure_type"] = classify_error(error, meta).value

    return tracing_pb2.AgentSpan(
        trace_id=ctx.trace_id,
        span_id=ctx.span_id,
        parent_span_id=ctx.parent_span_id,
        agent_id=agent_name,
        vector_clock={k: int(v) for k, v in ctx.vector_clock.items()},
        event_type="error" if error else event_type,
        input_tokens=in_tok,
        output_tokens=out_tok,
        latency_ms=latency_ms,
        start_time_ms=ctx.start_time_ms,
        metadata=truncate_text(json.dumps(meta, default=str)),
    )


def normalize_node_result(result: Any) -> Dict[str, Any]:
    if isinstance(result, dict):
        return result
    if result is None:
        return {}
    return {"_node_return": result}


# LLM-07: multi-provider response normalization
def normalize_llm_response(raw: Dict[str, Any], provider: str) -> Dict[str, Any]:
    p = provider.lower()
    try:
        if p == "openai":
            usage = raw.get("usage") or {}
            choices = raw.get("choices") or [{}]
            content = ((choices[0] or {}).get("message") or {}).get("content", "")
            return {
                "content": content,
                "_input_tokens": int(usage.get("prompt_tokens", 0)),
                "_output_tokens": int(usage.get("completion_tokens", 0)),
            }
        if p == "anthropic":
            usage = raw.get("usage") or {}
            blocks = raw.get("content") or [{}]
            content = (blocks[0] or {}).get("text", "")
            return {
                "content": content,
                "_input_tokens": int(usage.get("input_tokens", 0)),
                "_output_tokens": int(usage.get("output_tokens", 0)),
            }
        if p == "gemini":
            meta = raw.get("usageMetadata") or {}
            cands = raw.get("candidates") or [{}]
            parts = ((cands[0] or {}).get("content") or {}).get("parts") or [{}]
            content = (parts[0] or {}).get("text", "")
            return {
                "content": content,
                "_input_tokens": int(meta.get("promptTokenCount", 0)),
                "_output_tokens": int(meta.get("candidatesTokenCount", 0)),
            }
    except Exception:
        pass
    return {"content": "", "_input_tokens": 0, "_output_tokens": 0}
