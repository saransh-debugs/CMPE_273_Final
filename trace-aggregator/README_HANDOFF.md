# Team Handoff README

This document splits remaining work into independent tracks so teammates can continue in parallel.

## Current baseline (already working)

- End-to-end path works: `sdk` -> `collector` -> `engine` -> `api` -> `ui`.
- Demo emits traces with intentional failure injection (`DEMO_MODE=true`) and supports real LLM mode (`DEMO_MODE=false`).
- `/traces` deduplicates by latest reconstruction per `trace_id`.
- First-class decision observability is implemented:
  - decision events in `raw_decisions`
  - decision impact edges in `decision_edges`
  - API endpoints: `/traces/{id}/decisions`, `/traces/{id}/root-cause`
- UI pages are live:
  - Trace list
  - Trace detail (timeline + DAG + blame + Decision Chain)
  - Global blame leaderboard

## Ground rules

- **Primary contract:** `proto/tracing.proto` is the cross-team interface.
- Do not break backward compatibility in proto without team sync.
- Keep components independently runnable via existing bootstrap flow.
- Prefer additive changes to API payloads; avoid removing existing fields.

## Parallel workstreams

## 1) SDK hardening and portability (`sdk/`)

- **Owner suggestion:** Teammate A
- **Goal:** make instrumentation safer and less framework-coupled.
- **Scope:**
  - Add explicit redaction hooks for metadata fields.
  - Add config for sampling, metadata limits, and disable switches.
  - Improve docs for non-LangGraph integration patterns.
- **Deliverables:**
  - Config object + env var support.
  - Tests for redaction/sampling behavior.
  - Updated usage examples.
- **Acceptance criteria:**
  - Existing demo still works unchanged.
  - No blocking behavior when collector is unavailable.

## 2) Collector reliability + ops metrics (`collector/`)

- **Owner suggestion:** Teammate B
- **Goal:** production-ready ingestion behavior under burst load.
- **Scope:**
  - Add queue depth, flush success/failure, dropped span counters.
  - Add graceful shutdown flush.
  - Add explicit backpressure handling policy.
- **Deliverables:**
  - Internal metrics endpoint or structured logs.
  - Retry/failure policy documentation.
- **Acceptance criteria:**
  - No span loss during normal shutdown.
  - Batch flushing remains bounded under sustained load.

## 3) Causal engine precision (`engine/`)

- **Owner suggestion:** Teammate C
- **Goal:** improve DAG fidelity for join semantics and trace repair.
- **Scope:**
  - Represent join edges more explicitly (multi-parent or inferred edge set).
  - Add deterministic policy for ambiguous parent selection.
  - Expand tests for parallel fan-out/fan-in and missing parent cases.
- **Deliverables:**
  - Updated reconstruction output schema (if needed, additive only).
  - Unit tests covering join edge behavior.
- **Acceptance criteria:**
  - Existing traces still reconstruct.
  - Reviewer join reflects branch dependencies accurately.

## 4) API query expansion (`api/`)

- **Owner suggestion:** Teammate D
- **Goal:** provide richer filtering/search for UI and external tooling.
- **Scope:**
  - Add filters: `agent_id`, `event_type`, `from_ts`, `to_ts`, `min_tokens`, `max_latency`, `decision_type`, `confidence`.
  - Add metadata search endpoint (safe, indexed pattern).
  - Add pagination contract (`cursor` or `offset` + `limit`).
- **Deliverables:**
  - Endpoint docs with request/response examples.
  - Backward-compatible API changes.
- **Acceptance criteria:**
  - Existing UI routes still work.
  - New filters validated with sample queries.

## 5) UI completion and polish (`ui/`)

- **Owner suggestion:** Teammate E
- **Goal:** complete product-facing observability experience.
- **Scope:**
  - Add advanced filter controls matching new API filters.
  - Improve DAG/timeline/decision interaction (hover sync, highlight causal path, decision-to-edge linking).
  - Add error-focused view and empty/loading states for all pages.
- **Deliverables:**
  - Updated pages/components with typed API integration.
  - Short UX walkthrough in `ui/README.md`.
- **Acceptance criteria:**
  - Can diagnose one failing trace from UI alone.
  - Filter and drill-down flow works without manual URL edits.

## 6) Alerting service (new component)

- **Owner suggestion:** Teammate F
- **Goal:** detect and notify on operationally important trace anomalies.
- **Scope:**
  - Rules: stuck agent, repeated tool failure, runaway token usage.
  - Polling worker over ClickHouse tables.
  - Notification sinks: log/webhook first, Slack optional.
- **Deliverables:**
  - `alerting/` service with config and run command.
  - Rule definitions and severity mapping.
- **Acceptance criteria:**
  - Alerts trigger on synthetic bad traces.
  - No duplicate alert storm for same incident window.

## 7) Benchmark and scale proof (`scripts/` + docs)

- **Owner suggestion:** Teammate G
- **Goal:** quantify throughput and latency under concurrent traces.
- **Scope:**
  - Build load generator for 100-1000 concurrent trace runs.
  - Capture ingest latency, reconstruction lag, API p95/p99.
  - Publish reproducible benchmark report.
- **Deliverables:**
  - `scripts/load_test.*`
  - `docs/benchmark.md` with methodology + results.
- **Acceptance criteria:**
  - Reproducible runbook with fixed parameters.
  - Report includes bottlenecks and tuning recommendations.

## Integration checkpoints

- **Checkpoint 1 (schema):** proto and any API/schema changes reviewed by all owners.
- **Checkpoint 2 (contract):** UI + API pair integration with real responses.
- **Checkpoint 3 (ops):** collector/engine/alerting run together for 30+ minutes without manual intervention.
- **Checkpoint 4 (scale):** benchmark run completed and documented.

## Suggested branch strategy

- One branch per workstream:
  - `feat/sdk-hardening`
  - `feat/collector-reliability`
  - `feat/engine-join-fidelity`
  - `feat/api-advanced-filters`
  - `feat/ui-polish`
  - `feat/alerting-service`
  - `feat/load-benchmark`
- Keep PRs small and additive; merge behind flags when uncertain.

## Quick ownership map (minimal team)

- 4-person team:
  - Person 1: SDK + Collector
  - Person 2: Engine
  - Person 3: API + Alerting
  - Person 4: UI + Benchmark/report

## Definition of done for project completion

- Alerting service running and validated.
- Advanced filters available end-to-end (API + UI).
- Join-aware DAG behavior documented and tested.
- Benchmark report published with measured performance.
- Real-mode decision capture documented and reproducible with at least one OpenAI-compatible provider.
- README and architecture docs updated to reflect final behavior.

## Reference TODO backlog (use alongside team TODOs)

Use this section as a shared reference list so anyone joining can immediately pick a workstream.

### A) Decision coverage expansion (highest priority)

- [ ] Add explicit `DecisionEvent` emission for every non-trivial branch, handoff, and tool-routing point.
- [ ] Enforce **Decide -> Act** pattern across orchestrator flow (decision always logged before action).
- [ ] Ensure both success and failure paths emit decisions (not only happy path).
- [ ] Add trace-level check that major DAG edges have corresponding decision entries.

### B) Decision quality and contract enforcement

- [ ] Add strict schema validation (Pydantic) for model-produced decision JSON.
- [ ] Define fallback policy for invalid JSON (`fallback_used`, parse error reason, safe defaults).
- [ ] Bound rationale/evidence lengths and add redaction rules for sensitive content.
- [ ] Add unit tests for malformed decision payloads and fallback behavior.

### C) Engine root-cause semantics

- [ ] Improve decision impact attribution beyond descendant aggregation.
- [ ] Add deterministic ranking for reason-chain ordering.
- [ ] Add stronger failure explanation output (`which decision`, `why`, `downstream impact`).
- [ ] Expand tests for parallel fan-out/fan-in + ambiguous causal paths.

### D) API query expansion

- [ ] Add filters to decision endpoints: `decision_type`, `actor_agent_id`, `confidence_min/max`, `from_ts`, `to_ts`.
- [ ] Add metadata query support for decisions.
- [ ] Add pagination/cursor contracts for high-cardinality traces.
- [ ] Add API examples for forensic queries in docs.

### E) UI forensic workflow completion

- [ ] Add decision filters and confidence controls in trace detail UI.
- [ ] Implement click-through linking: decision -> DAG edge/node -> timeline segment.
- [ ] Add "reason chain" sorting options (impact, time, error influence).
- [ ] Improve no-data/error states for decision panels.

### F) Alerting service (new component)

- [ ] Create `alerting/` worker polling ClickHouse.
- [ ] Implement initial rules: stuck agent, runaway tokens, repeated tool failure.
- [ ] Add dedupe/cooldown to avoid alert storms.
- [ ] Add webhook/log sinks (Slack optional).

### G) Scale proof and benchmark

- [ ] Build load harness for 100-1000 concurrent traces with mixed decision density.
- [ ] Measure ingest lag, reconstruction lag, API p95/p99, and drop rate.
- [ ] Publish reproducible benchmark report (`docs/benchmark.md`).
- [ ] Add tuning recommendations and bottleneck notes.

### H) Framework portability and production controls

- [ ] Provide at least one non-LangGraph integration example.
- [ ] Add auth/tenancy plan for API exposure.
- [ ] Add retention/TTL guidance for span/decision tables.
- [ ] Document metadata governance policy (what can/cannot be stored).


## Sprint exit checklist

- [ ] Every major edge has a decision record.
- [ ] Root-cause endpoint explains one success trace and one failure trace.
- [ ] UI can diagnose a failure from Decision Chain + DAG + Timeline without raw logs.
- [ ] Alerting triggers on synthetic incidents.
- [ ] Benchmark report is committed and reproducible.
