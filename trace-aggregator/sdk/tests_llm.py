"""
LLM observability test suite (LLM-01 through LLM-10).
Pure Python — no ClickHouse or gRPC required.

Run:
    python -m sdk.tests_llm
"""
from __future__ import annotations

import json
import sys
import time
import uuid


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_passed = 0
_failed = 0


def _ok(name: str) -> None:
    global _passed
    _passed += 1
    print(f"  ✓ {name}")


def _fail(name: str, reason: str) -> None:
    global _failed
    _failed += 1
    print(f"  ✗ {name}: {reason}")


def _run(name: str, fn) -> None:
    try:
        fn()
        _ok(name)
    except AssertionError as e:
        _fail(name, str(e) or "assertion failed")
    except Exception as e:
        _fail(name, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# LLM-03: structured decision JSON parsing
# ---------------------------------------------------------------------------

def _test_llm03() -> None:
    from sdk.core import (
        ParseFailureReason,
        build_decision_fallback,
        validate_and_parse_llm_json,
    )

    def t_valid_json():
        raw = json.dumps({
            "selected_candidate_id": "research",
            "confidence": 0.9,
            "rationale_summary": "Route to research because findings are required.",
            "evidence_refs": [],
            "candidates": [],
        })
        result, err = validate_and_parse_llm_json(raw)
        assert err is None
        assert result["confidence"] == 0.9

    def t_invalid_json():
        _, err = validate_and_parse_llm_json("not json at all")
        assert err == ParseFailureReason.INVALID_JSON

    def t_confidence_out_of_range():
        raw = json.dumps({
            "selected_candidate_id": "x",
            "confidence": 1.5,
            "rationale_summary": "Select x for speed.",
        })
        _, err = validate_and_parse_llm_json(raw)
        assert err == ParseFailureReason.CONFIDENCE_RANGE

    def t_missing_required_field():
        raw = json.dumps({"confidence": 0.5})
        _, err = validate_and_parse_llm_json(raw)
        assert err == ParseFailureReason.MISSING_REQUIRED

    def t_schema_mismatch_not_dict():
        _, err = validate_and_parse_llm_json("[1, 2, 3]")
        assert err == ParseFailureReason.SCHEMA_MISMATCH

    def t_bad_type_confidence():
        raw = json.dumps({
            "selected_candidate_id": "x",
            "confidence": "high",
            "rationale_summary": "Select x.",
        })
        _, err = validate_and_parse_llm_json(raw)
        assert err == ParseFailureReason.BAD_TYPE

    def t_fallback_metadata_has_reason():
        fb = build_decision_fallback(
            trace_id="t",
            source_span_id="s",
            actor_agent_id="a",
            decision_type="route_branch",
            parse_failure_reason=ParseFailureReason.INVALID_JSON,
        )
        meta = json.loads(fb["metadata"])
        assert meta["fallback_used"] is True
        assert meta["parse_failure_reason"] == "invalid_json"

    print("\nLLM-03: structured decision JSON")
    for fn in [
        t_valid_json, t_invalid_json, t_confidence_out_of_range,
        t_missing_required_field, t_schema_mismatch_not_dict,
        t_bad_type_confidence, t_fallback_metadata_has_reason,
    ]:
        _run(fn.__name__.lstrip("t_"), fn)


# ---------------------------------------------------------------------------
# LLM-05: rationale quality rubric
# ---------------------------------------------------------------------------

def _test_llm05() -> None:
    from sdk.core import validate_rationale, emit_decision_event

    def t_good_rationale_passes():
        violations = validate_rationale("Route to reviewer because both artifacts are ready.")
        assert violations == [], violations

    def t_too_short_flagged():
        violations = validate_rationale("ok")
        assert any("length" in v for v in violations)

    def t_no_action_verb_flagged():
        violations = validate_rationale("The pipeline has completed both tasks successfully.")
        assert any("action verb" in v for v in violations)

    def t_filler_phrase_flagged():
        violations = validate_rationale("As an AI I will route to the next agent now.")
        assert any("filler" in v for v in violations)

    def t_violation_does_not_crash_emit():
        emitted = []
        decision_id = emit_decision_event(
            trace_id="t",
            source_span_id="s",
            actor_agent_id="a",
            decision_type="route_branch",
            selected_candidate_id="x",
            confidence=0.8,
            rationale_summary="bad",
            emit_fn=lambda proto: emitted.append(proto),
        )
        assert decision_id
        assert len(emitted) == 1
        meta = json.loads(emitted[0].metadata)
        assert "rationale_violations" in meta

    print("\nLLM-05: rationale quality")
    for fn in [
        t_good_rationale_passes, t_too_short_flagged,
        t_no_action_verb_flagged, t_filler_phrase_flagged,
        t_violation_does_not_crash_emit,
    ]:
        _run(fn.__name__.lstrip("t_"), fn)


# ---------------------------------------------------------------------------
# LLM-06: metadata redaction
# ---------------------------------------------------------------------------

def _test_llm06() -> None:
    from sdk.core import redact_sensitive, add_redact_key, begin_span, build_span

    def t_dict_key_redaction():
        out = redact_sensitive({"api_key": "sk-abc", "model": "gpt-4"})
        assert out["api_key"] == "[REDACTED]"
        assert out["model"] == "gpt-4"

    def t_nested_dict_redaction():
        out = redact_sensitive({"auth": {"token": "secret", "user": "alice"}})
        assert out["auth"]["token"] == "[REDACTED]"
        assert out["auth"]["user"] == "alice"

    def t_email_pattern_redaction():
        out = redact_sensitive({"msg": "contact user@example.com for support"})
        assert "[EMAIL]" in out["msg"]
        assert "user@example.com" not in out["msg"]

    def t_bearer_token_redaction():
        out = redact_sensitive({"h": "Authorization: Bearer sk-abc123"})
        assert "[BEARER]" in out["h"]
        assert "sk-abc123" not in out["h"]

    def t_card_number_redaction():
        out = redact_sensitive({"note": "card 4111 1111 1111 1111 on file"})
        assert "[CARD]" in out["note"]
        assert "4111" not in out["note"]

    def t_list_redaction():
        out = redact_sensitive([{"password": "pw"}, {"name": "alice"}])
        assert out[0]["password"] == "[REDACTED]"
        assert out[1]["name"] == "alice"

    def t_custom_redact_key():
        add_redact_key("ssn")
        out = redact_sensitive({"ssn": "123-45-6789"})
        assert out["ssn"] == "[REDACTED]"

    def t_span_input_text_redacted():
        ctx = begin_span(
            agent_name="a",
            trace_id="t",
            vector_clock={},
            parent_span_id="",
        )
        span = build_span(
            ctx=ctx,
            agent_name="a",
            event_type="llm_call",
            state={"_input_text": "email me at secret@corp.com", "_trace_id": "t"},
            result={},
            error=None,
        )
        meta = json.loads(span.metadata)
        assert "secret@corp.com" not in meta.get("input_text", ""), meta

    print("\nLLM-06: metadata redaction")
    for fn in [
        t_dict_key_redaction, t_nested_dict_redaction, t_email_pattern_redaction,
        t_bearer_token_redaction, t_card_number_redaction, t_list_redaction,
        t_custom_redact_key, t_span_input_text_redacted,
    ]:
        _run(fn.__name__.lstrip("t_"), fn)


# ---------------------------------------------------------------------------
# LLM-09: semantic failure taxonomy
# ---------------------------------------------------------------------------

def _test_llm09() -> None:
    from sdk.taxonomy import SemanticFailureType, classify_error
    from sdk.core import begin_span, build_span

    def t_hallucinated_import():
        err = RuntimeError("Hallucinated import: `from anthropic import GalaxyBrain`")
        assert classify_error(err, {}) == SemanticFailureType.HALLUCINATED_IMPORT

    def t_timeout():
        err = TimeoutError("Request timed out after 30s")
        assert classify_error(err, {}) == SemanticFailureType.TIMEOUT

    def t_context_overflow():
        err = RuntimeError("maximum context length exceeded: 16385 tokens")
        assert classify_error(err, {}) == SemanticFailureType.CONTEXT_OVERFLOW

    def t_json_parse_failure():
        err = json.JSONDecodeError("Expecting value", "", 0)
        assert classify_error(err, {}) == SemanticFailureType.JSON_PARSE_FAILURE

    def t_unknown_error():
        err = ValueError("something unexpected")
        assert classify_error(err, {}) == SemanticFailureType.UNKNOWN_ERROR

    def t_semantic_type_in_span_metadata():
        ctx = begin_span(agent_name="a", trace_id="t", vector_clock={}, parent_span_id="")
        err = RuntimeError("Hallucinated import: GalaxyBrain")
        span = build_span(
            ctx=ctx, agent_name="a", event_type="llm_call",
            state={}, result=None, error=err,
        )
        meta = json.loads(span.metadata)
        assert meta["semantic_failure_type"] == "hallucinated_import"

    def t_span_event_type_promoted_to_error():
        ctx = begin_span(agent_name="a", trace_id="t", vector_clock={}, parent_span_id="")
        err = ValueError("oops")
        span = build_span(
            ctx=ctx, agent_name="a", event_type="llm_call",
            state={}, result=None, error=err,
        )
        assert span.event_type == "error"

    print("\nLLM-09: semantic failure taxonomy")
    for fn in [
        t_hallucinated_import, t_timeout, t_context_overflow,
        t_json_parse_failure, t_unknown_error,
        t_semantic_type_in_span_metadata, t_span_event_type_promoted_to_error,
    ]:
        _run(fn.__name__.lstrip("t_"), fn)


# ---------------------------------------------------------------------------
# LLM-01: decision coverage registry
# ---------------------------------------------------------------------------

def _test_llm01() -> None:
    from sdk.core import (
        DECISION_COVERAGE_POINTS,
        register_coverage_point,
        mark_covered,
        get_coverage_report,
    )

    def t_register_point():
        register_coverage_point("test_point_01", "a test coverage point")
        assert "test_point_01" in DECISION_COVERAGE_POINTS

    def t_mark_covered_increments_hits():
        register_coverage_point("test_point_02", "another test point")
        mark_covered("test_point_02", "trace-aaa")
        mark_covered("test_point_02", "trace-bbb")
        report = get_coverage_report()
        assert report["test_point_02"]["hit_count"] == 2

    def t_same_trace_not_double_counted():
        register_coverage_point("test_point_03", "dedup test")
        mark_covered("test_point_03", "trace-xyz")
        mark_covered("test_point_03", "trace-xyz")
        report = get_coverage_report()
        assert report["test_point_03"]["hit_count"] == 1

    def t_demo_pipeline_coverage_points_registered():
        import demo.pipeline  # noqa: F401 — triggers register_coverage_point calls
        assert "orchestrator_dispatch" in DECISION_COVERAGE_POINTS
        assert "coder_error_halt" in DECISION_COVERAGE_POINTS

    print("\nLLM-01: decision coverage registry")
    for fn in [
        t_register_point, t_mark_covered_increments_hits,
        t_same_trace_not_double_counted, t_demo_pipeline_coverage_points_registered,
    ]:
        _run(fn.__name__.lstrip("t_"), fn)


# ---------------------------------------------------------------------------
# LLM-02: decide_then_act contract
# ---------------------------------------------------------------------------

def _test_llm02() -> None:
    from sdk.instrument import decide_then_act

    base_state = {"_trace_id": "t-llm02", "_parent_span_id": "p", "_vector_clock": {}}

    def _good_decision(s):
        return {
            "selected_candidate_id": "x",
            "confidence": 0.8,
            "rationale_summary": "Select x because it is faster and more reliable.",
        }

    def t_decision_before_action():
        call_log: list[str] = []

        def decide(s):
            call_log.append("decide")
            return _good_decision(s)

        def act(s):
            call_log.append("act")
            return {}

        wrapped = decide_then_act(decide, act, actor_agent_id="a", decision_type="tool_select")
        wrapped(dict(base_state))
        assert call_log.index("decide") < call_log.index("act")

    def t_action_not_called_if_decision_fails():
        action_called: list[bool] = []

        def bad_decide(s):
            raise ValueError("cannot decide")

        def act(s):
            action_called.append(True)

        wrapped = decide_then_act(bad_decide, act, actor_agent_id="a", decision_type="tool_select")
        try:
            wrapped(dict(base_state))
        except ValueError:
            pass
        assert not action_called

    def t_action_result_passthrough():
        wrapped = decide_then_act(
            _good_decision,
            lambda s: {"output": 42},
            actor_agent_id="a",
            decision_type="tool_select",
        )
        result = wrapped(dict(base_state))
        assert result == {"output": 42}

    def t_coverage_point_marked_when_provided():
        from sdk.core import register_coverage_point, get_coverage_report
        register_coverage_point("llm02_test_point", "test")

        wrapped = decide_then_act(
            _good_decision,
            lambda s: {},
            actor_agent_id="a",
            decision_type="tool_select",
            coverage_point="llm02_test_point",
        )
        state = dict(base_state)
        state["_trace_id"] = "trace-llm02-cov"
        wrapped(state)
        report = get_coverage_report()
        assert report["llm02_test_point"]["hit_count"] >= 1

    print("\nLLM-02: decide_then_act contract")
    for fn in [
        t_decision_before_action, t_action_not_called_if_decision_fails,
        t_action_result_passthrough, t_coverage_point_marked_when_provided,
    ]:
        _run(fn.__name__.lstrip("t_"), fn)


# ---------------------------------------------------------------------------
# LLM-07: multi-provider normalization
# ---------------------------------------------------------------------------

def _test_llm07() -> None:
    from sdk.core import normalize_llm_response

    def t_openai():
        raw = {
            "choices": [{"message": {"content": "hello"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }
        out = normalize_llm_response(raw, "openai")
        assert out == {"content": "hello", "_input_tokens": 10, "_output_tokens": 20}

    def t_anthropic():
        raw = {
            "content": [{"text": "world"}],
            "usage": {"input_tokens": 5, "output_tokens": 15},
        }
        out = normalize_llm_response(raw, "anthropic")
        assert out == {"content": "world", "_input_tokens": 5, "_output_tokens": 15}

    def t_gemini():
        raw = {
            "candidates": [{"content": {"parts": [{"text": "hi"}]}}],
            "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 7},
        }
        out = normalize_llm_response(raw, "gemini")
        assert out == {"content": "hi", "_input_tokens": 3, "_output_tokens": 7}

    def t_unknown_provider_graceful():
        out = normalize_llm_response({}, "unknown_llm_corp")
        assert out["_input_tokens"] == 0
        assert out["_output_tokens"] == 0

    def t_case_insensitive_provider():
        raw = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2},
        }
        out = normalize_llm_response(raw, "OpenAI")
        assert out["_input_tokens"] == 1

    def t_missing_fields_return_zeros():
        out = normalize_llm_response({}, "openai")
        assert out["_input_tokens"] == 0 and out["_output_tokens"] == 0

    print("\nLLM-07: multi-provider normalization")
    for fn in [
        t_openai, t_anthropic, t_gemini,
        t_unknown_provider_graceful, t_case_insensitive_provider,
        t_missing_fields_return_zeros,
    ]:
        _run(fn.__name__.lstrip("t_"), fn)


# ---------------------------------------------------------------------------
# LLM-08: token audit (pure mock, no ClickHouse)
# ---------------------------------------------------------------------------

def _test_llm08() -> None:
    sys.path.insert(0, ".")
    from scripts.token_audit import audit_spans

    def t_no_input_text_skipped():
        spans = [{"span_id": "s1", "input_tokens": 10, "metadata": "{}"}]
        result = audit_spans(spans, threshold_pct=5.0)
        assert result["skipped"] == 1 and result["flagged"] == 0

    def t_within_threshold_not_flagged():
        # 10 words reported, 10 counted → 0% discrepancy
        spans = [
            {
                "span_id": "s2",
                "agent_id": "a",
                "input_tokens": 10,
                "metadata": json.dumps({"input_text": "a " * 10}),
            }
        ]
        result = audit_spans(spans, threshold_pct=5.0, count_fn=lambda t: len(t.split()))
        assert result["flagged"] == 0

    def t_above_threshold_flagged():
        # 10 words reported, 2 counted → 80% discrepancy
        spans = [
            {
                "span_id": "s3",
                "agent_id": "a",
                "input_tokens": 10,
                "metadata": json.dumps({"input_text": "hello world"}),
            }
        ]
        result = audit_spans(spans, threshold_pct=5.0, count_fn=lambda t: len(t.split()))
        assert result["flagged"] == 1
        assert result["flagged_spans"][0]["span_id"] == "s3"

    def t_zero_reported_tokens_skipped():
        spans = [
            {
                "span_id": "s4",
                "input_tokens": 0,
                "metadata": json.dumps({"input_text": "hello"}),
            }
        ]
        result = audit_spans(spans, threshold_pct=5.0)
        assert result["skipped"] == 1

    def t_p50_p95_computed():
        spans = [
            {
                "span_id": f"s{i}",
                "agent_id": "a",
                "input_tokens": 10,
                "metadata": json.dumps({"input_text": "word " * i}),
            }
            for i in range(1, 11)
        ]
        result = audit_spans(spans, threshold_pct=0.0, count_fn=lambda t: len(t.split()))
        assert result["audited"] == 10
        assert result["p95_discrepancy_pct"] >= result["p50_discrepancy_pct"]

    print("\nLLM-08: token audit")
    for fn in [
        t_no_input_text_skipped, t_within_threshold_not_flagged,
        t_above_threshold_flagged, t_zero_reported_tokens_skipped,
        t_p50_p95_computed,
    ]:
        _run(fn.__name__.lstrip("t_"), fn)


# ---------------------------------------------------------------------------
# LLM-04: confidence calibration
# ---------------------------------------------------------------------------

def _test_llm04() -> None:
    from sdk.calibration import calibrate

    def t_empty_input():
        report = calibrate([], {})
        assert report.buckets == []
        assert report.overall_calibration_error == 0.0
        assert report.verdict == "well_calibrated"

    def t_perfect_calibration():
        decisions = [{"trace_id": f"t{i}", "confidence": 0.8} for i in range(100)]
        outcomes = {f"t{i}": (i < 80) for i in range(100)}
        report = calibrate(decisions, outcomes)
        bucket = next(b for b in report.buckets if b.label == "0.7-0.9")
        assert bucket.calibration_error < 0.1
        assert report.verdict == "well_calibrated"

    def t_overconfident_flagged():
        decisions = [{"trace_id": f"t{i}", "confidence": 0.95} for i in range(100)]
        outcomes = {f"t{i}": (i < 50) for i in range(100)}
        report = calibrate(decisions, outcomes)
        assert report.overall_calibration_error > 0.3
        assert report.verdict == "overconfident"

    def t_underconfident_flagged():
        decisions = [{"trace_id": f"t{i}", "confidence": 0.3} for i in range(100)]
        outcomes = {f"t{i}": True for i in range(100)}
        report = calibrate(decisions, outcomes)
        assert report.overall_calibration_error > 0.3
        assert report.verdict == "underconfident"

    def t_unknown_trace_ids_skipped():
        decisions = [{"trace_id": "unknown-xyz", "confidence": 0.9}]
        outcomes = {"different-trace": True}
        report = calibrate(decisions, outcomes)
        assert report.buckets == []

    def t_bucket_counts_correct():
        lo_decisions = [{"trace_id": f"lo{i}", "confidence": 0.3} for i in range(10)]
        hi_decisions = [{"trace_id": f"hi{i}", "confidence": 0.8} for i in range(20)]
        outcomes = {f"lo{i}": True for i in range(10)}
        outcomes.update({f"hi{i}": True for i in range(20)})
        report = calibrate(lo_decisions + hi_decisions, outcomes)
        bucket_map = {b.label: b for b in report.buckets}
        assert bucket_map["0.0-0.5"].count == 10
        assert bucket_map["0.7-0.9"].count == 20

    print("\nLLM-04: confidence calibration")
    for fn in [
        t_empty_input, t_perfect_calibration, t_overconfident_flagged,
        t_underconfident_flagged, t_unknown_trace_ids_skipped, t_bucket_counts_correct,
    ]:
        _run(fn.__name__.lstrip("t_"), fn)


# ---------------------------------------------------------------------------
# LLM-10: coverage gate
# ---------------------------------------------------------------------------

def _test_llm10() -> None:
    from scripts.coverage_gate import check_coverage

    required = {"orchestrator_dispatch", "coder_tool_select", "reviewer_route"}

    def t_all_covered():
        decisions = [
            {"metadata": json.dumps({"coverage_point": p})} for p in required
        ]
        missing = check_coverage(decisions, required=required)
        assert missing == set()

    def t_missing_one():
        decisions = [
            {"metadata": json.dumps({"coverage_point": "orchestrator_dispatch"})},
        ]
        missing = check_coverage(decisions, required=required)
        assert "coder_tool_select" in missing
        assert "reviewer_route" in missing

    def t_empty_decisions_all_missing():
        missing = check_coverage([], required=required)
        assert missing == required

    def t_bad_metadata_json_ignored():
        decisions = [
            {"metadata": "not-json"},
            {"metadata": json.dumps({"coverage_point": "orchestrator_dispatch"})},
        ]
        missing = check_coverage(decisions, required=required)
        assert "orchestrator_dispatch" not in missing

    def t_extra_points_in_decisions_ok():
        decisions = [
            {"metadata": json.dumps({"coverage_point": p})} for p in required
        ] + [
            {"metadata": json.dumps({"coverage_point": "extra_not_required"})}
        ]
        missing = check_coverage(decisions, required=required)
        assert missing == set()

    print("\nLLM-10: coverage gate")
    for fn in [
        t_all_covered, t_missing_one, t_empty_decisions_all_missing,
        t_bad_metadata_json_ignored, t_extra_points_in_decisions_ok,
    ]:
        _run(fn.__name__.lstrip("t_"), fn)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main() -> None:
    print("LLM Observability Test Suite")
    print("=" * 40)
    _test_llm03()
    _test_llm05()
    _test_llm06()
    _test_llm09()
    _test_llm01()
    _test_llm02()
    _test_llm07()
    _test_llm08()
    _test_llm04()
    _test_llm10()

    print("\n" + "=" * 40)
    print(f"Results: {_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
