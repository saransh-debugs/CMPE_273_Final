# LLM-Specific Observability — Implementation Plan

All 10 tasks (LLM-01 → LLM-10) with files, exact changes, and test cases.
Tests run with: `python -m sdk.tests_llm` (pure Python, no ClickHouse / gRPC).

---

## Execution Order

```
LLM-03 → LLM-05 → LLM-06 → LLM-09     (SDK layer, no dependencies)
LLM-01 → LLM-02                         (needs LLM-09 for error tagging)
LLM-07                                   (standalone, no deps)
LLM-08                                   (needs real-mode token data; can mock)
LLM-04                                   (needs LLM-03 + trace corpus)
LLM-10                                   (needs LLM-01 complete)
```

---

## LLM-03: Structured Decision JSON Reliability

**Why first:** LLM-09, LLM-02, LLM-04 all depend on the schema being stable.

### Files

| File | Change |
|------|--------|
| `sdk/core.py` | Add `ParseFailureReason` enum; add `parse_failure_metadata` field to `build_decision_fallback`; add `validate_and_parse_llm_json` helper |
| `sdk/tests_llm.py` | NEW — test file for all LLM tasks |

### Implementation detail

**Add to `sdk/core.py`** (after `REDACT_KEYS`):

```python
class ParseFailureReason(str, Enum):
    INVALID_JSON     = "invalid_json"
    SCHEMA_MISMATCH  = "schema_mismatch"
    MISSING_REQUIRED = "missing_required_field"
    BAD_TYPE         = "bad_field_type"
    CONFIDENCE_RANGE = "confidence_out_of_range"
    UNKNOWN          = "unknown_parse_error"
```

**Add `validate_and_parse_llm_json`** — wraps `json.loads` + Pydantic validation.
Returns `(parsed_dict, None)` on success, `(None, ParseFailureReason)` on failure.
The caller decides to emit fallback or raise.

**Update `build_decision_fallback`** — accept `parse_failure_reason: ParseFailureReason`
and embed it in metadata JSON as `parse_failure_reason` key so it is queryable.

### Test cases

```python
# LLM-03-T1: valid JSON parses cleanly
def test_valid_decision_json_parses():
    raw = '{"selected_candidate_id":"a","confidence":0.9,"rationale_summary":"ok",...}'
    result, err = validate_and_parse_llm_json(raw, trace_id="t", ...)
    assert err is None and result["confidence"] == 0.9

# LLM-03-T2: invalid JSON → ParseFailureReason.INVALID_JSON, no crash
def test_invalid_json_returns_fallback():
    result, err = validate_and_parse_llm_json("not json", ...)
    assert err == ParseFailureReason.INVALID_JSON

# LLM-03-T3: confidence out of range → CONFIDENCE_RANGE
def test_confidence_out_of_range():
    raw = '{"confidence": 1.5, ...}'
    _, err = validate_and_parse_llm_json(raw, ...)
    assert err == ParseFailureReason.CONFIDENCE_RANGE

# LLM-03-T4: missing required field → MISSING_REQUIRED
def test_missing_required_field():
    raw = '{"confidence": 0.5}'   # no selected_candidate_id
    _, err = validate_and_parse_llm_json(raw, ...)
    assert err == ParseFailureReason.MISSING_REQUIRED

# LLM-03-T5: fallback metadata is queryable (parse_failure_reason present)
def test_fallback_metadata_has_reason():
    fb = build_decision_fallback(..., parse_failure_reason=ParseFailureReason.INVALID_JSON)
    meta = json.loads(fb["metadata"])
    assert meta["parse_failure_reason"] == "invalid_json"
    assert meta["fallback_used"] is True
```

---

## LLM-05: Rationale Quality Standards

### Files

| File | Change |
|------|--------|
| `sdk/core.py` | Add `validate_rationale(text: str) -> list[str]` returning violation list |
| `sdk/core.py` | Add `RATIONALE_PROMPT_TEMPLATE` constant with the rubric embedded |

### Rubric (enforced in `validate_rationale`)

1. Length ≥ 10 chars, ≤ 512 chars (already enforced by Pydantic, now explicit error message).
2. Starts with an action verb from `RATIONALE_ACTION_VERBS = {"route", "delegate", "select", "dispatch", "reject", "approve", "escalate", "retry", "skip", "generate"}`.
3. Does not contain filler phrases: `"as an ai"`, `"i will"`, `"please note"`.
4. No PII-shaped patterns (email regex, 16-digit card regex) — lightweight check.

`validate_rationale` returns a list of violation strings (empty = passes).
The emit path logs violations as a warning but does **not** block emission
(violations become queryable metadata, not hard errors).

### Test cases

```python
# LLM-05-T1: good rationale → no violations
def test_rationale_passes_rubric():
    assert validate_rationale("Route to reviewer because both artifacts are ready.") == []

# LLM-05-T2: too short → violation
def test_rationale_too_short():
    assert any("length" in v for v in validate_rationale("ok"))

# LLM-05-T3: no action verb → violation
def test_rationale_no_action_verb():
    violations = validate_rationale("The pipeline has completed both tasks successfully.")
    assert any("action verb" in v for v in violations)

# LLM-05-T4: filler phrase → violation
def test_rationale_filler():
    violations = validate_rationale("As an AI I will route to the next agent.")
    assert any("filler" in v for v in violations)

# LLM-05-T5: violations become metadata, not crash
def test_rationale_violation_does_not_crash_emit():
    # build_span / emit_decision_event must complete even on rubric failure
    decision_id = emit_decision_event(
        ..., rationale_summary="bad", emit_fn=lambda x: None
    )
    assert decision_id  # returned a UUID, did not raise
```

---

## LLM-06: LLM Metadata Redaction

### Files

| File | Change |
|------|--------|
| `sdk/core.py` | Apply `redact_sensitive` to `meta["input_text"]` inside `build_span`; add `_REDACT_PATTERNS` for regex-based PII (email, phone, card); extend `redact_sensitive` to run patterns on string values |
| `sdk/core.py` | Add `add_redact_key(key: str)` hook for runtime extension |

### Implementation detail

Currently `build_span` stores `input_text` raw. Add:
```python
if input_text:
    meta["input_text"] = truncate_text(
        json.dumps(redact_sensitive({"v": str(input_text)})["v"], default=str), 1000
    )
```

Add regex patterns:
```python
_REDACT_PATTERNS = [
    (re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'), '[EMAIL]'),
    (re.compile(r'\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b'),              '[CARD]'),
    (re.compile(r'\bBearer\s+[A-Za-z0-9\-._~+/]+=*\b'),                   '[BEARER]'),
]
```

Apply patterns inside `redact_sensitive` when value is a string.

### Test cases

```python
# LLM-06-T1: dict key in REDACT_KEYS → [REDACTED]
def test_dict_key_redaction():
    out = redact_sensitive({"api_key": "sk-abc", "model": "gpt-4"})
    assert out["api_key"] == "[REDACTED]"
    assert out["model"] == "gpt-4"

# LLM-06-T2: nested dict redaction
def test_nested_dict_redaction():
    out = redact_sensitive({"auth": {"token": "secret", "user": "alice"}})
    assert out["auth"]["token"] == "[REDACTED]"
    assert out["auth"]["user"] == "alice"

# LLM-06-T3: email in string value → [EMAIL]
def test_email_pattern_redaction():
    out = redact_sensitive({"msg": "contact user@example.com for support"})
    assert "[EMAIL]" in out["msg"]
    assert "user@example.com" not in out["msg"]

# LLM-06-T4: Bearer token in string → [BEARER]
def test_bearer_token_redaction():
    out = redact_sensitive({"header": "Authorization: Bearer sk-abc123"})
    assert "[BEARER]" in out["header"]

# LLM-06-T5: TRACE_REDACT_KEYS env var extends redaction
def test_custom_redact_key(monkeypatch):
    monkeypatch.setenv("TRACE_REDACT_KEYS", "ssn")
    # re-parse REDACT_KEYS (or call add_redact_key)
    add_redact_key("ssn")
    out = redact_sensitive({"ssn": "123-45-6789"})
    assert out["ssn"] == "[REDACTED]"

# LLM-06-T6: build_span redacts input_text
def test_span_input_text_redacted():
    ctx = begin_span(agent_name="a", trace_id="t", vector_clock={}, parent_span_id="")
    span = build_span(
        ctx=ctx, agent_name="a", event_type="llm_call",
        state={"_input_text": "email me at secret@corp.com", "_trace_id": "t"},
        result={}, error=None,
    )
    meta = json.loads(span.metadata)
    assert "secret@corp.com" not in meta.get("input_text", "")
```

---

## LLM-09: Semantic Failure Taxonomy

### Files

| File | Change |
|------|--------|
| `sdk/taxonomy.py` | NEW — `SemanticFailureType` enum + `classify_error(error, span_meta) -> SemanticFailureType` |
| `sdk/core.py` | In `build_span`, when `error is not None`, call `classify_error` and add `semantic_failure_type` to metadata |

### `SemanticFailureType` enum

```python
class SemanticFailureType(str, Enum):
    HALLUCINATED_IMPORT    = "hallucinated_import"
    BAD_DELEGATION         = "bad_delegation"       # agent_handoff to invalid target
    WRONG_TOOL_SELECTED    = "wrong_tool_selected"
    JSON_PARSE_FAILURE     = "json_parse_failure"
    TIMEOUT                = "timeout"
    CONTEXT_OVERFLOW       = "context_overflow"     # token limit exceeded
    DEPENDENCY_FAILURE     = "dependency_failure"   # upstream agent failed
    UNKNOWN_ERROR          = "unknown_error"
```

### `classify_error` heuristics (string matching on error repr)

| Match | Type |
|-------|------|
| `"import"` or `"ModuleNotFound"` or `"hallucinated"` | `HALLUCINATED_IMPORT` |
| `"timeout"` or `"timed out"` | `TIMEOUT` |
| `"context_length"` or `"maximum context"` or `"too many tokens"` | `CONTEXT_OVERFLOW` |
| `"json"` or `"JSONDecodeError"` | `JSON_PARSE_FAILURE` |
| `"delegation"` or `"no such agent"` | `BAD_DELEGATION` |
| default | `UNKNOWN_ERROR` |

### Test cases

```python
# LLM-09-T1: hallucinated import classified correctly
def test_hallucinated_import_classified():
    err = RuntimeError("Hallucinated import: `from anthropic import GalaxyBrain`")
    assert classify_error(err, {}) == SemanticFailureType.HALLUCINATED_IMPORT

# LLM-09-T2: timeout classified correctly
def test_timeout_classified():
    err = TimeoutError("Request timed out after 30s")
    assert classify_error(err, {}) == SemanticFailureType.TIMEOUT

# LLM-09-T3: context overflow classified
def test_context_overflow():
    err = RuntimeError("maximum context length exceeded: 16385 tokens")
    assert classify_error(err, {}) == SemanticFailureType.CONTEXT_OVERFLOW

# LLM-09-T4: unknown error falls back gracefully
def test_unknown_error_fallback():
    err = ValueError("something weird")
    assert classify_error(err, {}) == SemanticFailureType.UNKNOWN_ERROR

# LLM-09-T5: semantic_failure_type appears in span metadata
def test_span_metadata_has_semantic_type():
    ctx = begin_span(agent_name="a", trace_id="t", vector_clock={}, parent_span_id="")
    err = RuntimeError("Hallucinated import: GalaxyBrain")
    span = build_span(ctx=ctx, agent_name="a", event_type="llm_call",
                      state={}, result=None, error=err)
    meta = json.loads(span.metadata)
    assert meta["semantic_failure_type"] == "hallucinated_import"
```

---

## LLM-01: Decision Coverage Completion

### Files

| File | Change |
|------|--------|
| `demo/pipeline.py` | Add `_maybe_emit_decision` call in the coder error path (before raising); add decision at reviewer entry for `request_rework` branch; add `route_branch` decision in any conditional exit |
| `sdk/core.py` | Add `DECISION_COVERAGE_POINTS` registry dict `{point_id: description}` and `register_coverage_point(point_id)` / `mark_covered(point_id, trace_id)` for the CI gate (LLM-10) |

### Missing decision points in `demo/pipeline.py`

1. **Coder error path** — when `random.random() < DEMO_FAILURE_RATE`, emit a decision
   with `decision_type="route_branch"`, `selected_candidate_id="error_halt"`,
   `confidence=1.0`, `rationale_summary="Reject: hallucinated import detected."` **before** `raise`.

2. **Reviewer `request_rework` branch** — currently only emits the LLM-chosen candidate.
   When `selected_candidate_id == "request_rework"`, emit a second clarifying decision
   event with `decision_type="agent_handoff"` pointing back to the coder.

3. **Orchestrator parallel dispatch** — already covered. ✓

4. **Research tool selection** — already covered. ✓

### Test cases

```python
# LLM-01-T1: coder error path emits a decision before raising
def test_coder_error_emits_decision():
    emitted = []
    # patch emit_fn
    with patch("sdk.instrument.emit_decision", side_effect=emitted.append):
        with pytest.raises(RuntimeError):
            coder({"_trace_id": "t", "_vector_clock": {}, "_parent_span_id": "p"})
    assert any(
        d.get("selected_candidate_id") == "error_halt" for d in emitted
    ), "error path must emit a decision before raising"

# LLM-01-T2: all 4 required coverage points are hit in a successful run
def test_decision_coverage_full_pipeline():
    covered = set()
    with patch("sdk.core.mark_covered", side_effect=lambda pid, _: covered.add(pid)):
        run_once(task="test")
    required = {"orchestrator_dispatch", "research_tool_select",
                "coder_tool_select", "reviewer_route"}
    assert required.issubset(covered), f"Missing: {required - covered}"

# LLM-01-T3: error-path coverage point registered
def test_error_path_coverage_point_registered():
    assert "coder_error_halt" in DECISION_COVERAGE_POINTS
```

---

## LLM-02: decide_then_act Contract Enforcement

### Files

| File | Change |
|------|--------|
| `sdk/instrument.py` | Add `decide_then_act(decision_fn, action_fn, *, actor_agent_id, decision_type)` wrapper |
| `sdk/core.py` | Add `_pending_decisions: dict[str, str]` thread-local tracker (span_id → decision_id) used by the wrapper; add `assert_decision_precedes_action(span_id)` |

### `decide_then_act` contract

```python
def decide_then_act(
    decision_fn: Callable[[Dict], Dict],
    action_fn: Callable[[Dict], Any],
    *,
    actor_agent_id: str,
    decision_type: str,
) -> Callable[[Dict], Any]:
    def wrapped(state):
        payload = decision_fn(state)          # 1. decide
        emit_decision(...)                    # 2. emit (with decision_id)
        _record_decision_emitted(span_id)     # 3. register
        return action_fn(state)               # 4. act
    return wrapped
```

The `_record_decision_emitted` / `assert_decision_precedes_action` pair is used
by tests and optionally by instrumented nodes to verify ordering at runtime.

### Test cases

```python
# LLM-02-T1: decision is emitted before action executes
def test_decision_before_action():
    call_log = []
    def decision_fn(s):
        call_log.append("decide")
        return {"selected_candidate_id": "x", "confidence": 0.8,
                "rationale_summary": "Select x because it is faster."}
    def action_fn(s):
        call_log.append("act")
        return {}
    wrapped = decide_then_act(decision_fn, action_fn,
                               actor_agent_id="test", decision_type="tool_select")
    wrapped({"_trace_id": "t", "_parent_span_id": "p", "_vector_clock": {}})
    assert call_log.index("decide") < call_log.index("act")

# LLM-02-T2: if decision_fn raises, action_fn never executes
def test_action_not_called_if_decision_fails():
    action_called = []
    def bad_decision(s): raise ValueError("cannot decide")
    def action(s): action_called.append(True)
    wrapped = decide_then_act(bad_decision, action,
                               actor_agent_id="a", decision_type="tool_select")
    with pytest.raises(ValueError):
        wrapped({"_trace_id": "t", "_parent_span_id": "p", "_vector_clock": {}})
    assert not action_called

# LLM-02-T3: action result is returned unchanged
def test_action_result_passthrough():
    result = decide_then_act(
        lambda s: {"selected_candidate_id": "x", "confidence": 0.9,
                   "rationale_summary": "Select x because coverage is better."},
        lambda s: {"output": 42},
        actor_agent_id="a", decision_type="tool_select",
    )({"_trace_id": "t", "_parent_span_id": "p", "_vector_clock": {}})
    assert result["output"] == 42
```

---

## LLM-07: Multi-Provider Compatibility

### Files

| File | Change |
|------|--------|
| `sdk/core.py` | Add `normalize_llm_response(raw: dict, provider: str) -> dict` that returns `{"content": str, "_input_tokens": int, "_output_tokens": int}` for `"openai"`, `"anthropic"`, `"gemini"` |
| `demo/pipeline.py` | Replace inline `_call_openai_compatible` token extraction with `normalize_llm_response` |

### Provider normalization map

| Provider | `input_tokens` path | `output_tokens` path | `content` path |
|----------|--------------------|--------------------|---------------|
| `openai` | `usage.prompt_tokens` | `usage.completion_tokens` | `choices[0].message.content` |
| `anthropic` | `usage.input_tokens` | `usage.output_tokens` | `content[0].text` |
| `gemini` | `usageMetadata.promptTokenCount` | `usageMetadata.candidatesTokenCount` | `candidates[0].content.parts[0].text` |

Unknown provider → `UNKNOWN_ERROR` in `SemanticFailureType`, returns zeros for tokens.

### Test cases

```python
# LLM-07-T1: OpenAI response normalized correctly
def test_openai_normalization():
    raw = {"choices": [{"message": {"content": "hello"}}],
           "usage": {"prompt_tokens": 10, "completion_tokens": 20}}
    out = normalize_llm_response(raw, "openai")
    assert out == {"content": "hello", "_input_tokens": 10, "_output_tokens": 20}

# LLM-07-T2: Anthropic response normalized correctly
def test_anthropic_normalization():
    raw = {"content": [{"text": "world"}],
           "usage": {"input_tokens": 5, "output_tokens": 15}}
    out = normalize_llm_response(raw, "anthropic")
    assert out == {"content": "world", "_input_tokens": 5, "_output_tokens": 15}

# LLM-07-T3: Gemini response normalized correctly
def test_gemini_normalization():
    raw = {"candidates": [{"content": {"parts": [{"text": "hi"}]}}],
           "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 7}}
    out = normalize_llm_response(raw, "gemini")
    assert out == {"content": "hi", "_input_tokens": 3, "_output_tokens": 7}

# LLM-07-T4: unknown provider returns zeros, does not crash
def test_unknown_provider_graceful():
    out = normalize_llm_response({}, "unknown_llm_corp")
    assert out["_input_tokens"] == 0 and out["_output_tokens"] == 0
```

---

## LLM-08: Token/Latency Accuracy Audit

### Files

| File | Change |
|------|--------|
| `scripts/token_audit.py` | NEW — reads spans from ClickHouse, compares reported vs re-counted tokens, flags discrepancies |

### Audit logic

1. Pull spans where `event_type = "llm_call"` and `input_tokens > 0` from `tracing.raw_spans`.
2. Re-count tokens using `tiktoken` (cl100k_base) on `metadata.input_text` if present.
3. Compute `discrepancy_pct = abs(reported - counted) / max(reported, 1) * 100`.
4. Flag spans where `discrepancy_pct > THRESHOLD_PCT` (default 10%).
5. Report: total audited, flagged count, p50/p95 discrepancy.

### Test cases (mock, no ClickHouse)

```python
# LLM-08-T1: zero discrepancy when counts match
def test_audit_no_discrepancy():
    spans = [{"input_tokens": 10, "metadata": json.dumps({"input_text": "hello world"})}]
    # mock tiktoken to return 2 tokens → discrepancy vs 10 → flagged
    # This tests the flagging logic, not tiktoken itself
    result = audit_spans(spans, threshold_pct=5.0, count_fn=lambda t: len(t.split()))
    assert result["flagged"] == 1  # "hello world" → 2, reported 10, large gap

# LLM-08-T2: no input_text → skipped, not flagged
def test_audit_skips_missing_input_text():
    spans = [{"input_tokens": 10, "metadata": "{}"}]
    result = audit_spans(spans, threshold_pct=5.0, count_fn=lambda t: len(t.split()))
    assert result["skipped"] == 1 and result["flagged"] == 0

# LLM-08-T3: discrepancy within threshold → not flagged
def test_audit_within_threshold():
    spans = [{"input_tokens": 10, "metadata": json.dumps({"input_text": "a " * 10})}]
    result = audit_spans(spans, threshold_pct=5.0, count_fn=lambda t: len(t.split()))
    assert result["flagged"] == 0  # 10 words, 10 reported → 0% discrepancy
```

---

## LLM-04: Confidence Calibration

### Files

| File | Change |
|------|--------|
| `sdk/calibration.py` | NEW — `calibrate(decisions, outcomes) -> CalibrationReport` |
| `scripts/calibration_report.py` | NEW — queries ClickHouse, calls calibrate, prints report |

### Calibration logic

Group decisions by `confidence` bucket (0.0–0.5, 0.5–0.7, 0.7–0.9, 0.9–1.0).
For each bucket, compute `observed_success_rate` = fraction of traces where the
selected agent had `error_count == 0`.
`calibration_error = |mean_confidence - observed_success_rate|` per bucket.
A well-calibrated system has calibration_error < 0.1 per bucket.

### Test cases

```python
# LLM-04-T1: perfectly calibrated decisions
def test_perfect_calibration():
    decisions = [{"confidence": 0.8, "trace_id": f"t{i}"} for i in range(100)]
    outcomes = {f"t{i}": True for i in range(80)} | {f"t{i}": False for i in range(80, 100)}
    report = calibrate(decisions, outcomes)
    bucket = next(b for b in report.buckets if b.label == "0.7-0.9")
    assert bucket.calibration_error < 0.1

# LLM-04-T2: overconfident decisions flagged
def test_overconfident_flagged():
    decisions = [{"confidence": 0.95, "trace_id": f"t{i}"} for i in range(100)]
    outcomes = {f"t{i}": (i < 50) for i in range(100)}  # only 50% success
    report = calibrate(decisions, outcomes)
    assert report.overall_calibration_error > 0.3
    assert report.verdict == "overconfident"

# LLM-04-T3: empty input → no crash
def test_calibration_empty():
    report = calibrate([], {})
    assert report.buckets == [] and report.overall_calibration_error == 0.0
```

---

## LLM-10: Decision Coverage CI Gate

### Files

| File | Change |
|------|--------|
| `scripts/coverage_gate.py` | NEW — checks a trace for required decision point IDs; exits 1 if any missing |
| `sdk/core.py` | `DECISION_COVERAGE_POINTS` registry populated by `register_coverage_point(point_id)` |

### Required coverage points (registered in `demo/pipeline.py` after LLM-01)

```
orchestrator_dispatch
research_tool_select
coder_tool_select
coder_error_halt
reviewer_route
```

### Coverage gate logic

```python
# coverage_gate.py
# Usage: python scripts/coverage_gate.py --trace-id <id>
# Queries /traces/{id}/decisions, checks required point IDs are present.
# Exit 0 = covered, Exit 1 = missing points.
```

### Test cases

```python
# LLM-10-T1: all required points present → returns empty missing set
def test_gate_passes_when_all_covered():
    decisions = [
        {"metadata": json.dumps({"coverage_point": "orchestrator_dispatch"})},
        {"metadata": json.dumps({"coverage_point": "research_tool_select"})},
        {"metadata": json.dumps({"coverage_point": "coder_tool_select"})},
        {"metadata": json.dumps({"coverage_point": "reviewer_route"})},
    ]
    missing = check_coverage(decisions, required={"orchestrator_dispatch",
                                                   "research_tool_select",
                                                   "coder_tool_select",
                                                   "reviewer_route"})
    assert missing == set()

# LLM-10-T2: missing point → returns non-empty set
def test_gate_fails_when_missing():
    decisions = [{"metadata": json.dumps({"coverage_point": "orchestrator_dispatch"})}]
    missing = check_coverage(decisions, required={"orchestrator_dispatch",
                                                   "coder_tool_select"})
    assert "coder_tool_select" in missing

# LLM-10-T3: no decisions at all → all required points missing
def test_gate_fails_empty_decisions():
    required = {"orchestrator_dispatch", "coder_tool_select"}
    missing = check_coverage([], required=required)
    assert missing == required
```

---

## Summary: New Files

| File | Purpose |
|------|---------|
| `sdk/taxonomy.py` | `SemanticFailureType` enum + `classify_error` |
| `sdk/calibration.py` | `calibrate()`, `CalibrationReport` dataclass |
| `sdk/tests_llm.py` | All 30+ tests above, runnable with `python -m sdk.tests_llm` |
| `scripts/token_audit.py` | Token/latency accuracy audit (LLM-08) |
| `scripts/calibration_report.py` | Calibration report CLI (LLM-04) |
| `scripts/coverage_gate.py` | CI gate for decision coverage (LLM-10) |

## Modified Files

| File | What changes |
|------|-------------|
| `sdk/core.py` | `ParseFailureReason`, `validate_and_parse_llm_json`, `validate_rationale`, redaction regex patterns, `normalize_llm_response`, coverage registry |
| `sdk/instrument.py` | `decide_then_act` wrapper |
| `demo/pipeline.py` | Coder error-path decision, coverage point registration, `normalize_llm_response` in real mode |

## Running all tests

```bash
cd trace-aggregator
python -m sdk.tests_llm          # 30+ pure-Python tests, no infra required
python -m engine.tests           # existing engine determinism corpus
python scripts/coverage_gate.py --help   # CI gate (needs API running)
python scripts/calibration_report.py    # needs ClickHouse + trace corpus
python scripts/token_audit.py          # needs ClickHouse with real traces
```
