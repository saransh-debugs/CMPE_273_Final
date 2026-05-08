# Implementation Gaps vs Proposal

## Legend
- ✅ Done — merged and verified
- ⚠️ Partial — scaffolded but incomplete
- ❌ Missing — not started

---

## Core Architecture Components

| Component | Status | Notes |
|-----------|--------|-------|
| Instrumentation SDK (gRPC emit, vector clock, decorator) | ✅ | `sdk/` |
| Span Collector (gRPC server, WAL, backpressure) | ✅ | ENG-01/02/03/04 |
| Causal Engine (vector clock DAG reconstruction) | ✅ | `engine/dag.py`, `engine/worker.py` |
| ClickHouse Storage Layer | ✅ | `db/init_db.py`, tables: raw_spans, raw_decisions, reconstructed_traces |
| Query API (FastAPI) | ⚠️ | Basic endpoints exist; missing filter expansion and pagination |
| Visualization UI (4 views) | ⚠️ | See UI section below |
| Alerting Service | ⚠️ | Rules exist; missing incident lifecycle (dedup/cooldown/ack) |
| SLO Reporting | ✅ | ENG-12 |

---

## Proposal Features Not Yet Implemented

### Causal Engine
- ❌ **Heuristic gap-filling for broken/incomplete traces** — proposal promises repair of partial DAGs; current engine infers orphan roots but does no heuristic stitching of missing middle spans.
- ❌ **ENG-06: Root-cause attribution model v2** — current blame is descendant-aggregate; proposal claims uncertainty propagation and versioned impact model.

### Query / API
- ❌ **ENG-07: Filter expansion** — proposal says queries by agent ID, time range, token usage, tool call type, error flag, custom metadata. Current API has basic filters only.
- ❌ **ENG-08: Cursor pagination** — current API uses offset pagination; breaks on large datasets.

### Visualization UI
- ❌ **ENG-09: Realtime streaming** — proposal says "real time"; UI polls only, no SSE/WebSocket live mode.
- ❌ **ENG-10: Forensic linking** — decision → DAG path → timeline segment cross-navigation. Components exist separately but are not wired together.
- ⚠️ **Search and Filter view** — `TraceListPage` exists but the proposal treats Search as a distinct 4th view with drill-down by agent, tool, model, time range, and performance metric. Current filter surface is minimal.

### Alerting
- ❌ **ENG-11: Incident model** — no dedup key, no cooldown, no lifecycle states (`open`/`ack`/`resolved`). Current alerting fires on every poll cycle.

### Security / Governance
- ❌ **ENG-13: Auth + tenancy** — no authentication, no tenant scoping on ingest or query.
- ❌ **ENG-14: Retention + governance** — no TTL policy, no metadata allowlist enforcement, no redaction pipeline at storage boundary.

### LLM-Specific Observability

- ✅ **LLM-01**: Full decision coverage — all 4 agents emit decisions with `coverage_point` tags; coder error-path emits before `raise`; `DECISION_COVERAGE_POINTS` registry in `sdk/core.py`; 57-test suite passes.
- ✅ **LLM-02**: `decide_then_act` wrapper in `sdk/instrument.py` — decision_fn runs and emits before action_fn executes; if decision_fn raises, action_fn never runs.
- ✅ **LLM-03**: `ParseFailureReason` enum + `validate_and_parse_llm_json` in `sdk/core.py`; `build_decision_fallback` embeds `parse_failure_reason` as queryable metadata field.
- ✅ **LLM-04**: `sdk/calibration.py` — `calibrate()` + `CalibrationReport`; `scripts/calibration_report.py` CLI with `--mock` and `--json` modes.
- ✅ **LLM-05**: `validate_rationale` in `sdk/core.py` — action-verb, filler-phrase, and length checks; violations stored as `rationale_violations` in decision metadata (non-blocking).
- ✅ **LLM-06**: `redact_sensitive` extended with regex PII patterns (email `[EMAIL]`, card `[CARD]`, Bearer `[BEARER]`); `input_text` in span metadata redacted before storage; `add_redact_key` hook.
- ✅ **LLM-07**: `normalize_llm_response` in `sdk/core.py` — normalizes OpenAI, Anthropic, and Gemini response schemas to `{content, _input_tokens, _output_tokens}`; unknown providers return zeros.
- ✅ **LLM-08**: `scripts/token_audit.py` — compares reported `input_tokens` against word-count estimate; reports p50/p95 discrepancy; `--mock` mode for CI.
- ✅ **LLM-09**: `sdk/taxonomy.py` — `SemanticFailureType` enum + `classify_error` heuristics; `build_span` embeds `semantic_failure_type` in all error span metadata.
- ✅ **LLM-10**: `scripts/coverage_gate.py` — reads `coverage_point` tags from decision metadata; exit 0 = all covered, exit 1 = missing; supports `--trace-id`, `--recent N`, `--mock`.

### Benchmarks / Ops
- ❌ **ENG-15**: Benchmark report — `docs/benchmark.md` has placeholders, no real measurements.
- ❌ **ENG-16**: Deployment runbook — `README_HANDOFF.md` exists but is not an operator runbook.

### QA / CI
- ❌ **QA-01**: End-to-end contract tests (proto → collector → engine → api → ui).
- ❌ **QA-02**: Chaos / failure scenario scripts.
- ❌ **QA-03**: Regression metrics in CI.

---

## Summary

**Implemented (proposal promise → code):** Ingestion pipeline, causal DAG reconstruction, basic blame scoring, SLO tracking, alerting rules, and a working 4-page UI. SDK refactored: core logic extracted from `instrument.py` into `sdk/core.py`; redaction and decision validation exist.

**LLM observability:** ✅ All 10 tasks complete. 57 pure-Python tests pass (`python -m sdk.tests_llm`). New files: `sdk/taxonomy.py`, `sdk/calibration.py`, `scripts/token_audit.py`, `scripts/calibration_report.py`, `scripts/coverage_gate.py`.

**Other gaps:** Real-time streaming, forensic UI linking, security/auth, governance/retention, measured benchmarks, and CI contract/chaos coverage remain absent.
