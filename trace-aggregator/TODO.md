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

### ENG-03: Idempotent Writes and Dedupe [DONE]

- **Scope:** Ensure duplicate events do not corrupt final trace state.
- **Dependencies:** ENG-02.
- **Deliverables:** Idempotency keys and dedupe policy in storage/processing. (Completed)
- **Acceptance:** Re-ingesting same events yields stable outputs. (Met)

#### ENG-03 Implementation Details

**Changes Made:**
1. **Schema Updates** (`db/init_db.py`):
   - Added `idempotency_key` columns to `raw_spans` and `raw_decisions` tables
   - Column stores trace_id:span_id for spans, trace_id:decision_id for decisions

2. **Collector Updates** (`collector/server.py` & `collector/writer.py`):
   - Generate idempotency_key = trace_id:span_id in gRPC handler
   - Generate idempotency_key = trace_id:decision_id for decisions
   - Include key in WAL serialization for durability
   - Support replay with idempotency_key intact

3. **Engine Worker** (`engine/worker.py`):
   - `fetch_spans()`: Fetches all rows with idempotency_key, deduplicates in Python
   - `fetch_decisions()`: Same pattern for decisions
   - `_extract_input_text()`: Simplified query without problematic GROUP BY
   - Dedup logic: Keep most recent row (highest ingested_at) per key

4. **API Layer** (`api/main.py`):
   - `get_raw_spans()`: Fetches all rows, client-side dedupe before return
   - `_query_trace_decisions()`: Dedupe after query, apply limit/offset after
   - `get_traces()`: Uses deduplicated decisions from above
   - `aggregate_blame()`: Dedupe spans before aggregation by agent

5. **Alerting** (`alerting/worker.py`):
   - `_rule_error_burst()`: Dedupe raw spans before counting errors
   - `_rule_stuck_traces()`: Dedupe spans to find last ingestion per trace
   - Added `datetime` import for time calculations

6. **SLO Reporting** (`scripts/slo_report.py`):
   - Dedupe spans before computing lag metrics
   - Prevents duplicate inflation in SLO calculations

**ClickHouse Compatibility Fix:**
- Original approach used `argMax()` aggregate with `GROUP BY` (incompatible with CH 24.3.18.7)
- Solution: Moved dedup logic to Python after query, avoiding ILLEGAL_AGGREGATION error
- All queries now use simple SELECT without GROUP BY + window functions

#### ENG-03 Verification Steps (Manual Testing)

1. **Empty database:**
   ```bash
   python << 'EOF'
   import clickhouse_connect
   client = clickhouse_connect.get_client(host="localhost", port=8123, username="default", password="")
   for table in ["raw_spans", "raw_decisions", "reconstructed_traces"]:
       client.query(f"TRUNCATE TABLE tracing.{table}")
       print(f"✓ Truncated tracing.{table}")
   EOF
   ```

2. **Emit initial traces:**
   ```bash
   python -m demo.pipeline
   ```

3. **Capture baseline (first run):**
   ```bash
   python << 'EOF'
   import clickhouse_connect
   client = clickhouse_connect.get_client(host="localhost", port=8123, username="default", password="")
   trace_id = "b387eafc-ccd2-480f-8805-35dc56c6ff3a"
   result = client.query(f"""
       SELECT COUNT(*), COUNT(DISTINCT idempotency_key)
       FROM tracing.raw_spans WHERE trace_id = '{trace_id}'
   """).result_rows
   print(f"Raw rows (before duplicates): {result[0][0]}")
   print(f"Unique keys: {result[0][1]}")
   EOF
   ```

4. **Insert duplicate spans directly:**
   ```bash
   python << 'EOF'
   import clickhouse_connect
   client = clickhouse_connect.get_client(host="localhost", port=8123, username="default", password="")
   trace_id = "b387eafc-ccd2-480f-8805-35dc56c6ff3a"
   spans = client.query(f"""
       SELECT span_id, agent_id, event_type, input_tokens, output_tokens, latency_ms, start_time_ms
       FROM tracing.raw_spans WHERE trace_id = '{trace_id}' ORDER BY start_time_ms ASC
   """).result_rows
   for s in spans:
       query = f"INSERT INTO tracing.raw_spans (trace_id, span_id, agent_id, event_type, input_tokens, output_tokens, latency_ms, start_time_ms, idempotency_key) VALUES ('{trace_id}', '{s[0]}', '{s[1]}', '{s[2]}', {s[3]}, {s[4]}, {s[5]}, {s[6]}, '{trace_id}:{s[0]}')"
       client.query(query)
   print(f"✓ Inserted {len(spans)} duplicate spans")
   EOF
   ```

5. **Verify duplicates in raw table:**
   ```bash
   python << 'EOF'
   import clickhouse_connect
   client = clickhouse_connect.get_client(host="localhost", port=8123, username="default", password="")
   trace_id = "b387eafc-ccd2-480f-8805-35dc56c6ff3a"
   result = client.query(f"""
       SELECT COUNT(*), COUNT(DISTINCT idempotency_key)
       FROM tracing.raw_spans WHERE trace_id = '{trace_id}'
   """).result_rows
   print(f"Raw rows (with duplicates): {result[0][0]} (should be ~8)")
   print(f"Unique keys: {result[0][1]} (should be ~4)")
   print(f"✓ Duplicates stored correctly" if result[0][0] > result[0][1] else "✗ No duplicates")
   EOF
   ```

6. **Wait for engine to process (5-10 seconds)** and verify dedup:
   ```bash
   python << 'EOF'
   import clickhouse_connect
   client = clickhouse_connect.get_client(host="localhost", port=8123, username="default", password="")
   trace_id = "b387eafc-ccd2-480f-8805-35dc56c6ff3a"
   recon = client.query(f"""
       SELECT span_count FROM tracing.reconstructed_traces 
       WHERE trace_id = '{trace_id}' ORDER BY reconstructed_at DESC LIMIT 1
   """).result_rows
   if recon:
       print(f"Span count (engine deduped): {recon[0][0]}")
       print(f"Expected: 4 (NOT 8)")
       print(f"✓ DEDUPLICATION SUCCESSFUL!" if recon[0][0] == 4 else "✗ Dedup failed")
   EOF
   ```

**Expected Test Results:**
- Step 3: Raw rows=4, Unique keys=4 (baseline)
- Step 5: Raw rows=8, Unique keys=4 (duplicates present)
- Step 6: Span count=4 (deduped correctly) ✅

### ENG-04: Backpressure and Collector Metrics [DONE]

- **Scope:** Expose queue depth, accept/reject counts, flush latency, retries.
- **Dependencies:** ENG-01.
- **Deliverables:** Metrics endpoint and structured operational logs. (Completed)
- **Acceptance:** Overload behavior is measurable and auditable. (Met)

#### ENG-04 Implementation Details

**New modules:**
- `collector/metrics.py` — singleton `METRICS` (CollectorMetrics) with per-writer
  `WriterMetrics` (counters: accepted/rejected/queue_full/flush_attempts/
  flush_success/flush_failures/rows_flushed/replay_enqueued; gauges: queue_depth,
  wal_backlog, acceptance_rate, flush_success_rate; histograms: flush_latency_ms,
  flush_batch_size with bounded ring buffer and p50/p95/p99/max).
- `collector/metrics_server.py` — stdlib-only async HTTP server (no new deps).
  Exposes:
  - `GET /metrics` JSON snapshot (machine readable, also consumed by SLO evaluator)
  - `GET /metrics/prom` Prometheus text exposition (counters + gauges)
  - `GET /healthz` readiness

**Wiring:**
- `collector/writer.py` — every WAL write/queue-full/replay enqueue and every
  flush attempt updates `WriterMetrics`. Flush latency is timed around
  `client.insert`. `attach_queue_depth` and `attach_wal_backlog` give live gauges.
- `collector/server.py` — servicer increments `METRICS.spans_received/dropped`
  and `decisions_received/dropped` mirrors. `_ops_log_loop` emits a structured
  one-line summary every `OPS_LOG_INTERVAL_SEC` (default 30s).
- `start_metrics_server()` is started alongside the gRPC server and shut down
  on signal.

**Tunables (env vars):**
- `METRICS_BIND_HOST` (default `0.0.0.0`)
- `METRICS_BIND_PORT` (default `9090`)
- `METRICS_HIST_WINDOW` (default `1024`) — observations kept per histogram
- `OPS_LOG_INTERVAL_SEC` (default `30`)

#### ENG-04 Verification Steps (Manual)

1. Start collector: `python -m collector.server`
2. Emit traffic: `python -m demo.pipeline`
3. Read JSON snapshot: `curl -s http://localhost:9090/metrics | jq .`
   - Expected: `writers.span.accepted` rises, `acceptance_rate` ~1.0,
     `flush_success_rate` ~1.0 once ClickHouse is up.
4. Read Prometheus exposition:
   `curl -s http://localhost:9090/metrics/prom | head -20`
5. Backpressure test: stop ClickHouse, emit traffic, watch the collector
   logs for the periodic `ops/span ... flush_fail=N wal_backlog=M` line.
   `acceptance_rate` should stay 1.0 (WAL still accepts), `flush_success_rate`
   should drop, `wal_backlog` should grow.
6. Recovery: `docker compose start clickhouse && python -m collector.replay_wal`.
   `wal_backlog` should drain to 0, `raw_spans` count should grow by the
   buffered amount.

**Validated end-to-end on 2026-05-02:**
- 18 spans + 18 decisions accepted, 0 rejected, 8+7 flushes succeeded,
  flush p95 ~140ms, all rates at 1.0000 on healthy system.
- Failure injection: `accepted=36`, `rejected=0`, `wal_backlog=18`,
  `flush_success_rate=0.53` while CH was down.
- Recovery: WAL drained to 0 via `collector.replay_wal`, `raw_spans` count
  grew from 18 → 216 across the demo + replay.

### ENG-05: Engine Determinism Corpus [DONE]

- **Scope:** Golden tests for fan-out/fan-in, out-of-order, missing parent, duplicate spans.
- **Dependencies:** None.
- **Deliverables:** Deterministic test corpus and checks. (Completed)
- **Acceptance:** Repeated runs yield deterministic DAG output. (Met)

#### ENG-05 Implementation Details

Added 12 new tests to `engine/tests.py` (determinism corpus section):

- `test_out_of_order_arrival` — all 6 permutations of a 3-span chain produce identical structure
- `test_three_way_fan_in` — 3 parallel branches merging to one collector; verifies `explicit_plus_fanin` resolution and 3 parent_ids
- `test_orphan_parent` — `parent_span_id` pointing to a missing span falls back to vector clock inference
- `test_concurrent_spans_are_roots` — two causally unrelated spans (neither clock precedes the other) are both roots
- `test_single_span_trace` — trivial edge case; root with no children, `parent_resolution = "root"`
- `test_deep_linear_chain` — 10-hop chain; every parent→child link and root verified
- `test_determinism_repeated` — `serialize_dag(reconstruct_dag(spans))` called 50× on the fanout pattern; all results bit-identical
- `test_fanout_fanin_golden` — explicit golden assertions for the canonical orchestrator→parallel→merge pattern including `parent_resolution` values
- `test_blame_error_weight` — agent with 3 errors gets higher blame than agent with equal latency/tokens but no errors
- `test_blame_single_agent` — one agent owning all spans; `blame_score = 80.0` (all latency + all tokens, no errors)
- `test_blame_zero_activity` — spans with 0 latency/tokens; no crash, all scores = 0
- `test_serialize_dag_stable_ordering` — two spans with identical `start_time_ms`; span_id tiebreak is stable across 30 calls

Run: `python -m engine.tests`

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

### ENG-12: SLO Definition and Reporting [DONE]

- **Scope:** Track ingest success, reconstruction lag, API latency, completion rates.
- **Dependencies:** ENG-04.
- **Deliverables:** SLO spec + periodic reporting pipeline. (Completed)
- **Acceptance:** SLO status is visible and alert-driven. (Met)

#### ENG-12 Implementation Details

**New package: `slo/`**
- `slo/spec.py` — typed `SLOSpec` dataclass + the `SLOS` catalog. Six SLOs:
  - `ingest_acceptance` ≥ 99.9% (15m, source: collector `/metrics`)
  - `flush_success` ≥ 99% (15m, source: collector `/metrics`)
  - `reconstruction_lag_p95` ≤ 60,000ms (60m)
  - `reconstruction_lag_p99` ≤ 120,000ms (60m)
  - `trace_completion` ≥ 99% (engine-aligned hot window, default 300s)
  - `api_latency_p95` ≤ 500ms (5m, synthetic probe)
- `slo/evaluator.py` — pulls signals from collector `/metrics` (acceptance,
  flush success), ClickHouse (reconstruction lags + completion), and a synthetic
  probe against the FastAPI server. Returns `SLOStatus` per spec.
- `slo/worker.py` — periodic loop that evaluates and writes status rows to
  `tracing.slo_status`. Supports `--once` for cron mode.

**Reconstruction lag measurement** — non-obvious, documented here:

`engine.worker` re-reconstructs every "active" trace on every 2s tick for up
to `LOOKBACK_SEC` after its last span. A naive `max(reconstructed_at) -
max(ingested_at)` therefore measures engine churn (often 60–80s), not the
user-visible time-to-first-reconstruction (TTFR). To fix this:

1. Schema: added immutable `first_reconstructed_at DateTime64(3)` column to
   `tracing.reconstructed_traces` (default `toDateTime64(0,3,'UTC')` so old
   rows are detectable as "never populated").
2. Engine (`engine/worker.py`): on every overwrite, looks up the existing
   `first_reconstructed_at` and carries it forward — only the very first
   reconstruction sets it. Bad historical rows where `first > reconstructed`
   are clamped.
3. Evaluator: TTFR for traces whose last span preceded the first rebuild;
   staleness vs. latest `reconstructed_at` for warm traces that received
   incremental spans after the first rebuild.
4. Hot-window eligibility: `SLO_RECON_ACTIVE_LOOKBACK_SEC` (default 120s)
   excludes old backlog traces from p95 — keeps the SLO sensitive to *now*,
   not historical drag.

**Schema:** `tracing.slo_status` (MergeTree, partitioned by day, ordered by
slo_name+evaluated_at). New column `first_reconstructed_at` on
`tracing.reconstructed_traces`. Both applied idempotently in `db/init_db.py`.

**API:** `GET /slo` returns current statuses + recent history per SLO.
Computed on demand; history pulled from `tracing.slo_status` if the worker
has been running.

**UI:** `/slo` page with overall pass/fail banner, per-SLO cards (value vs.
target, sample count, notes), and a 20-bin sparkline of recent evaluations.
Linked from header nav (`ui/src/pages/SLOPage.tsx`,
`ui/src/components/Header.tsx`).

**Alerting:** New `_rule_slo_breach` in `alerting/worker.py` pages on K-of-N
sustained failures (defaults: 3 of last 5). Tunable via
`ALERT_SLO_LOOKBACK` and `ALERT_SLO_BREACH_THRESHOLD`. Designed so single
transient eval blips don't page.

**Updated:** `scripts/slo_report.py` delegates to `slo.evaluator` and
prints the full catalog with PASS/FAIL per SLO. Supports `--json` and
`--persist`. Self-bootstraps `sys.path` so it runs as both
`python scripts/slo_report.py` and `python -m scripts.slo_report` from the
`trace-aggregator/` root.

**Tunables (env vars):**
- `COLLECTOR_METRICS_URL` (default `http://localhost:9090/metrics`)
- `API_PROBE_URL` (default `http://localhost:8000`)
- `API_PROBE_SAMPLES` (default `5`) — synthetic probe samples per eval
- `SLO_POLL_INTERVAL_SEC` (default `60`) — worker cadence
- `SLO_RECON_ACTIVE_LOOKBACK_SEC` (default `120`) — recon hot-window
- `SLO_TRACE_COMPLETION_ACTIVE_SEC` (default `300`) — match `engine.worker.LOOKBACK_SEC`
- `ALERT_SLO_LOOKBACK` (default `5`), `ALERT_SLO_BREACH_THRESHOLD` (default `3`)

#### ENG-12 Verification Steps (Manual)

> All commands assume `cd trace-aggregator && source .venv/bin/activate`
> (or use `.venv/bin/python` directly).

1. Apply schema (idempotent): `python -m db.init_db`
   - Expected: `slo_status` listed in tables.
2. Start collector (terminal 1), engine (2), API (3), UI (4) per CLAUDE.md.
3. Emit traffic: `python -m demo.pipeline`
4. One-shot evaluation: `python -m slo.worker --once`
   - Expected: 6 lines, each `PASS …` on a healthy system. A row per SLO
     inserted into `tracing.slo_status`.
5. Persistent worker: `python -m slo.worker` (default interval 60s).
6. CLI report: `python scripts/slo_report.py`
   - Returns exit code 0 if all SLOs pass, 1 if any fail. `--json` for
     machine output, `--persist` to also write to `slo_status`.
7. API: `curl -s http://localhost:8000/slo | jq .overall` → `"pass"`.
8. UI: open `http://localhost:5173/slo`. Expected: PASS banner, 6 cards,
   sparklines once the worker has run a few cycles.
9. Failure injection (proves alerting works):
   ```bash
   docker compose stop clickhouse
   python -m demo.pipeline                   # collector still ACKs (WAL durable)
   python -m slo.worker --once               # flush_success drops to 0
   python -m slo.worker --once               # second eval — adds to history
   python -m slo.worker --once               # third — meets 3-of-5 threshold
   python -m alerting.worker                 # → ALERT HIGH | slo_breach …
   docker compose start clickhouse
   python -m collector.replay_wal            # drains buffered spans
   ```
10. Stale history caveat: if you change thresholds or the measurement
    method, old `slo_status` rows can keep firing the K-of-N alert. Truncate
    with `TRUNCATE TABLE tracing.slo_status` (POST via the driver, not
    GET — ClickHouse rejects mutations over HTTP GET).

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
