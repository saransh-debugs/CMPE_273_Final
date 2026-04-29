# Distributed Trace Aggregator - Team TODO Backlog

This backlog is structured for team self-selection.
Each task includes scope, dependencies, deliverables, and acceptance criteria.

## Priority / Roadmap

1. Ingestion durability and backpressure
2. Decision coverage and decision quality
3. Deterministic causal reconstruction and attribution quality
4. Query/API and realtime UI improvements
5. Alerting/SLO/security/governance
6. Benchmark proof and runbook hardening

---

## LLM-Specific Work

### LLM-01: Decision Coverage Completion
- **Scope:** Emit `DecisionEvent` for every non-trivial branch/handoff/tool select.
- **Dependencies:** None.
- **Deliverables:** Decision map + missing emits added in orchestration code.
- **Acceptance:** For sampled traces, all major DAG branching points have linked decisions.

### LLM-02: Decide->Act Contract Enforcement
- **Scope:** Ensure decisions are logged before action execution.
- **Dependencies:** LLM-01.
- **Deliverables:** Reusable `decide_then_act` wrapper/decorator.
- **Acceptance:** No covered action executes without a prior recorded decision.

### LLM-03: Structured Decision JSON Reliability
- **Scope:** Harden model output parsing/validation and fallback behavior.
- **Dependencies:** None.
- **Deliverables:** Strict schema validation, fallback taxonomy, parse-failure metadata.
- **Acceptance:** Malformed model outputs never crash flow; fallback is explicit and queryable.

### LLM-04: Confidence Calibration
- **Scope:** Make decision confidence meaningful against observed outcomes.
- **Dependencies:** LLM-03 + trace corpus.
- **Deliverables:** Calibration report and updated confidence policy.
- **Acceptance:** Confidence bands correlate with real success/error rates.

### LLM-05: Rationale Quality Standards
- **Scope:** Standardize concise, actionable rationale summaries.
- **Dependencies:** None.
- **Deliverables:** Rationale rubric + prompt updates.
- **Acceptance:** Most sampled decisions meet rubric and aid triage.

### LLM-06: LLM Metadata Redaction
- **Scope:** Prevent sensitive prompt/context leakage in metadata.
- **Dependencies:** SDK redaction hooks.
- **Deliverables:** Redaction policy + test cases.
- **Acceptance:** Sensitive keys are always redacted before persistence.

### LLM-07: Multi-Provider Compatibility
- **Scope:** Verify instrumentation works across multiple OpenAI-compatible providers.
- **Dependencies:** None.
- **Deliverables:** Provider matrix + env examples + normalization notes.
- **Acceptance:** Same trace semantics across all tested providers.

### LLM-08: Token/Latency Accuracy Audit
- **Scope:** Verify token and latency fidelity in real mode.
- **Dependencies:** Real mode execution.
- **Deliverables:** Audit script + discrepancy report.
- **Acceptance:** Metric discrepancies within agreed threshold.

### LLM-09: Semantic Failure Taxonomy
- **Scope:** Label error classes (bad delegation, wrong tool, hallucinated output, etc.).
- **Dependencies:** Trace corpus.
- **Deliverables:** Failure enum/taxonomy and tagging logic.
- **Acceptance:** Error traces are filterable by semantic failure type.

### LLM-10: Decision Coverage CI Gate
- **Scope:** Prevent regressions in decision observability coverage.
- **Dependencies:** LLM-01.
- **Deliverables:** CI checker for expected decision points.
- **Acceptance:** PR fails when required decision points lose instrumentation.

---

## Software Engineering Work

### ENG-01: Collector Durability Semantics [DONE]
- **Scope:** Define explicit ACK/reject behavior and loss policy.
- **Dependencies:** None.
- **Deliverables:** Ingestion contract doc + reject path implementation. (Completed)
- **Acceptance:** No silent drops; all rejection paths are observable. (Met)

### ENG-02: Durable Buffer Layer (Broker or WAL) [DONE]
- **Scope:** Add durable buffering between ingest and storage.
- **Dependencies:** ENG-01.
- **Deliverables:** Broker/WAL path + replay command. (Completed)
- **Acceptance:** Restart/recovery does not lose accepted events. (Met)

#### ENG-01/ENG-02 Verification Steps (Manual)
1. Start services except ClickHouse path for failure test:
	- Run collector: `python -m collector.server`
2. Simulate storage outage:
	- Stop ClickHouse: `docker compose down`
3. Emit workload:
	- Run: `DEMO_MODE=true python -m demo.pipeline`
4. Validate durable buffering:
	- Run: `find ./wal -type f -name "*.json" | wc -l`
	- Expected: value > 0
	- Run: `ls ./wal/span | wc -l` and `ls ./wal/decision | wc -l`
	- Expected: both > 0 after demo run
5. Restore storage:
	- Run: `docker compose up -d`
	- Run: `python -m db.init_db`
6. Replay buffered events:
	- Run: `python -m collector.replay_wal`
	- Expected output contains inserted counts for spans/decisions
7. Confirm WAL drained:
	- Run: `find ./wal -type f | wc -l`
	- Expected: 0 (or near 0 if new traffic arrives concurrently)
8. Confirm persistence in ClickHouse:
	- Run: `curl -s http://127.0.0.1:8123 -d "SELECT count() FROM tracing.raw_spans"`
	- Run: `curl -s http://127.0.0.1:8123 -d "SELECT count() FROM tracing.raw_decisions"`
	- Expected: both counts increase compared to pre-replay values

### ENG-03: Idempotent Writes and Dedupe
- **Scope:** Ensure duplicate events do not corrupt final trace state.
- **Dependencies:** ENG-02.
- **Deliverables:** Idempotency keys and dedupe policy in storage/processing.
- **Acceptance:** Re-ingesting same events yields stable outputs.

### ENG-04: Backpressure and Collector Metrics
- **Scope:** Expose queue depth, accept/reject counts, flush latency, retries.
- **Dependencies:** ENG-01.
- **Deliverables:** Metrics endpoint and structured operational logs.
- **Acceptance:** Overload behavior is measurable and auditable.

### ENG-05: Engine Determinism Corpus
- **Scope:** Golden tests for fan-out/fan-in, out-of-order, missing parent, duplicate spans.
- **Dependencies:** None.
- **Deliverables:** Deterministic test corpus and checks.
- **Acceptance:** Repeated runs yield deterministic DAG output.

### ENG-06: Root-Cause Attribution Model v2
- **Scope:** Improve attribution beyond descendant aggregation.
- **Dependencies:** ENG-05.
- **Deliverables:** Versioned impact model with uncertainty propagation.
- **Acceptance:** Ranking quality improves on labeled incident traces.

### ENG-07: Query API Filter Expansion
- **Scope:** Add richer forensic filters for traces/decisions.
- **Dependencies:** None.
- **Deliverables:** New query params + docs + validations.
- **Acceptance:** Common forensic questions answerable without raw log scraping.

### ENG-08: Cursor Pagination for Large Datasets
- **Scope:** Add scalable cursor-based pagination.
- **Dependencies:** ENG-07.
- **Deliverables:** Cursor contract and backward-compatible fallback.
- **Acceptance:** High-cardinality queries complete without timeout/memory spikes.

### ENG-09: Realtime Trace Streaming (SSE/WebSocket)
- **Scope:** Stream active trace updates to the UI.
- **Dependencies:** ENG-02 preferred.
- **Deliverables:** Streaming backend endpoint + frontend live mode.
- **Acceptance:** Active traces update incrementally without page refresh.

### ENG-10: Forensic UI Linking Completion
- **Scope:** Link decision -> DAG path -> timeline segment interactions.
- **Dependencies:** ENG-09 optional.
- **Deliverables:** Synchronized selection/highlight behavior.
- **Acceptance:** Operator can follow root-cause chain end-to-end in UI.

### ENG-11: Alerting Incident Model
- **Scope:** Add dedupe/cooldown and lifecycle (`open`, `ack`, `resolved`).
- **Dependencies:** Existing alert worker.
- **Deliverables:** Incident model and dedupe key strategy.
- **Acceptance:** Repeated anomalies do not create alert storms.

### ENG-12: SLO Definition and Reporting
- **Scope:** Track ingest success, reconstruction lag, API latency, completion rates.
- **Dependencies:** ENG-04.
- **Deliverables:** SLO spec + periodic reporting pipeline.
- **Acceptance:** SLO status is visible and alert-driven.

### ENG-13: Security Baseline (Auth + Tenancy)
- **Scope:** Add authentication/authorization and tenant scoping.
- **Dependencies:** API/SDK schema updates.
- **Deliverables:** Tenant-aware ingest/query enforcement.
- **Acceptance:** Cross-tenant reads are blocked by integration tests.

### ENG-14: Governance and Retention
- **Scope:** TTL, data-class policy, metadata allowlist/redaction governance.
- **Dependencies:** ENG-13 recommended.
- **Deliverables:** Retention config, migration scripts, governance doc.
- **Acceptance:** Retention and policy rules enforced in production tables.

### ENG-15: Benchmark Completion (Measured)
- **Scope:** Replace benchmark template placeholders with real measurements.
- **Dependencies:** ENG-04, ENG-12.
- **Deliverables:** Reproducible benchmark report with bottlenecks and tuning.
- **Acceptance:** Another teammate can rerun and reproduce within tolerance.

### ENG-16: Deployment and Runbook Hardening
- **Scope:** Operational docs for startup, failure handling, recovery, replay.
- **Dependencies:** Major reliability tasks.
- **Deliverables:** End-to-end runbook and troubleshooting matrix.
- **Acceptance:** New operator can deploy, validate, and recover using docs only.

---

## Cross-Cutting QA / DX

### QA-01: End-to-End Contract Tests
- **Scope:** Validate proto -> collector -> engine -> api -> ui compatibility.
- **Dependencies:** None.
- **Deliverables:** Contract test suite.
- **Acceptance:** Schema drift breaks CI early.

### QA-02: Failure/Chaos Scenarios
- **Scope:** Collector restarts, engine lag, duplicates, delayed spans.
- **Dependencies:** ENG-02 and ENG-05 preferred.
- **Deliverables:** Chaos scripts/checklist and expected outcomes.
- **Acceptance:** Behavior matches documented failure modes.

### QA-03: Regression Metrics in CI
- **Scope:** Track key perf/correctness deltas per branch.
- **Dependencies:** ENG-12 and ENG-15.
- **Deliverables:** CI artifact with trend/delta summary.
- **Acceptance:** Regressions detectable pre-merge.

---

## Team Pickup Lanes

- **LLM/Prompt:** LLM-01, LLM-02, LLM-03, LLM-04, LLM-05, LLM-09, LLM-10
- **Reliability/Infra:** ENG-01, ENG-02, ENG-03, ENG-04, ENG-11
- **Causal/Algorithms:** ENG-05, ENG-06
- **API/Data:** ENG-07, ENG-08, ENG-12, ENG-14
- **Frontend/UX:** ENG-09, ENG-10
- **Security/Prod:** ENG-13, ENG-16
- **Perf/Benchmark:** ENG-15, QA-03

---

## Suggested Execution Order (Critical Path)

1. ENG-01 -> ENG-02 -> ENG-03 -> ENG-04  
2. LLM-01 -> LLM-02 -> LLM-03  
3. ENG-05 -> ENG-06  
4. ENG-07 -> ENG-08 -> ENG-09 -> ENG-10  
5. ENG-11 + ENG-12 + ENG-13 + ENG-14  
6. ENG-15 + ENG-16 + QA-02 + QA-03

