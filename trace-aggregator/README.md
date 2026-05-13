# Distributed Trace Aggregator for Multi-Agent LLM Pipelines

An end-to-end observability platform built natively for multi-agent AI workflows. It captures every span across agent boundaries, reconstructs the causal DAG using vector clocks, and surfaces a **Blame View** that ranks agents by their contribution to latency, token cost, and errors. Decision events — which candidate an agent chose, why, with what confidence — are first-class records throughout the system.

---

## Table of Contents

1. [Team](#team)
2. [Features](#features)
3. [Architecture](#architecture)
4. [Prerequisites](#prerequisites)
5. [Quick Start](#quick-start)
6. [Running the Demo Pipeline](#running-the-demo-pipeline)
7. [Running Tests](#running-tests)
8. [API Reference](#api-reference)
9. [UI Dashboard](#ui-dashboard)
10. [Module Guide](#module-guide)
11. [How It Works](#how-it-works)
12. [Configuration](#configuration)
13. [Development Guide](#development-guide)

---

## Team

| Name | GitHub | Role |
|------|--------|------|
| Saransh Soni | [saransh-debugs](https://github.com/saransh-debugs) | SDK instrumentation, pipeline integration |
| Nihad KP | [nndas11](https://github.com/nndas11) | Collector, WAL, metrics server |
| Aishwarya Madhave | [AishwaryaM2412](https://github.com/AishwaryaM2412) | Governance, SLO, security |
| Vandan Shah | [Vandan Sanket Shah](https://github.com/vandanshah17) | Engine, DAG reconstruction, blame scoring |

---

## Features

| Feature | Description |
|---------|-------------|
| **Span ingestion** | Async gRPC collector batches spans into ClickHouse; WAL ensures no data loss on crash |
| **Vector clock DAG** | Causal order reconstructed even when spans arrive out of order or across processes |
| **Blame View (V1 + V2)** | Per-agent latency/token/error share; V2 adds confidence intervals and error amplification |
| **Decision observability** | `emit_decision()` records which candidate an agent chose, why, and with what confidence |
| **Root-cause chains** | Decision events linked to downstream latency/token/error impact via `decision_edges` |
| **SLO evaluation** | Declarative SLO specs with periodic evaluator; violations surfaced in the UI |
| **Incident alerting** | Stuck-agent and runaway-token detectors; incidents exposed with ack/resolve actions |
| **Governance** | Metadata redaction patterns, TTL policy, allowlist enforcement (`shared/governance.py`) |
| **Tenant isolation** | Request-header-based tenant resolution; e2e isolation tests included |
| **Prometheus metrics** | Collector and engine expose `/metrics` for scraping |
| **React dashboard** | Timeline waterfall, causal DAG tree, Decision Chain panel, Blame leaderboard, SLO + Incident pages |
| **Demo mode** | Fully offline synthetic pipeline — no API key required |

---

## Architecture

```
demo/pipeline.py  (LangGraph 4-agent pipeline)
   │  @instrument_node → sdk/core.py
   │     vector clock progression, span lifecycle
   │  emit_decision    → sdk/core.py
   │     decision events with candidates + rationale
   ▼
sdk/client.py  (non-blocking gRPC stub, background thread)
   ▼
collector/server.py  (async gRPC, :50051)
   │  collector/writer.py  — batched ClickHouse inserts
   │  wal/                 — write-ahead log; replayed on startup
   │  collector/metrics_server.py — Prometheus metrics
   ▼
ClickHouse
   ├── tracing.raw_spans
   └── tracing.raw_decisions
   ▼
engine/worker.py  (polls raw_spans, per-trace reconstruction)
   │  engine/dag.py      — vector clock DAG reconstruction
   │  engine/blame.py    — V1: point-estimate blame scores
   │  engine/blame_v2.py — V2: CI bounds, std-dev, error amplification
   ▼
ClickHouse
   ├── tracing.reconstructed_traces  (dag_json, blame_json, blame_v2_json)
   └── tracing.decision_edges
   ▼
api/main.py  (FastAPI, :8000)
   │  CORS enabled for Vite dev origin (:5173)
   ▼
ui/  (Vite + React + TypeScript + Tailwind, :5173)
   ├── TraceListPage     — live SSE-backed list with filter bar
   ├── TraceDetailPage   — Timeline waterfall + DAG + Decision Chain + Blame panel
   ├── BlamePage         — cross-trace agent leaderboard (1h/6h/24h/7d)
   ├── SLOPage           — SLO dashboard
   └── IncidentsPage     — incident list with ack/resolve actions

slo/worker.py      — periodic SLO evaluator
alerting/worker.py — stuck-agent / runaway-token detector
shared/governance.py  — metadata redaction + TTL + allowlist
shared/trace_auth.py  — tenant resolution from request headers
```

**Key invariants:**
- `_trace_id`, `_vector_clock`, `_parent_span_id` are injected by `new_trace_context()` and propagated by `@instrument_node`.
- Spans may arrive out of order; the engine resolves parents by vector clock precedence when `parent_span_id` is absent or unresolvable.
- `reconstructed_traces` stores `dag_json`, `blame_json`, and `blame_v2_json` — the single source of truth the API reads.
- Decisions in `raw_decisions` are linked to their downstream impact in `decision_edges` by the engine.

---

## Prerequisites

- **Docker** (for ClickHouse)
- **Python 3.11+**
- **Node.js 18+** (for the UI)

---

## Quick Start

```bash
# 1. Clone and enter the project
git clone <this-repo> trace-aggregator
cd trace-aggregator

# 2. Bootstrap: installs Python deps, compiles proto, starts ClickHouse, initialises schema
bash scripts/bootstrap.sh

# 3. Start all services at once (collector + engine + API + UI)
./run.sh

# 4. (Optional) also fire the demo pipeline immediately
./run.sh --demo
```

**Service ports:**

| Service | Address |
|---------|---------|
| UI (Vite) | http://localhost:5173 |
| API (FastAPI) | http://localhost:8000 |
| Collector (gRPC) | :50051 |
| ClickHouse HTTP | :8123 |
| ClickHouse native | :9000 |

To stop everything including ClickHouse:

```bash
./stop.sh
```

### Manual startup (if you prefer separate terminals)

```bash
# T1 – ingestion collector
python -m collector.server

# T2 – causal engine worker
python -m engine.worker

# T3 – query API
uvicorn api.main:app --reload --port 8000

# T4 – UI (first time: cd ui && npm install)
cd ui && npm run dev

# T5 – demo traces
python -m demo.pipeline
```

> All Python commands must be run from `trace-aggregator/` with the `.venv` active.

---

## Running the Demo Pipeline

```bash
# Offline / demo mode — no API key required (default)
DEMO_MODE=true python -m demo.pipeline

# Real LLM mode — reads from .env
set -a && source .env && set +a && python -m demo.pipeline
```

The `.env` file holds `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`, and `DEMO_MODE`.

`DEMO_CODER_FAILURE_RATE` (default `0.5`) controls how often the coder agent raises a hallucination error — intentional for exercising the Blame View.

The demo pipeline is a 4-agent LangGraph graph: **orchestrator → researcher → coder → reviewer**. Each node is wrapped with `@instrument_node` and emits decision events at key branch points.

---

## Running Tests

```bash
# Full test suite (no ClickHouse or gRPC required for most tests)
python -m pytest tests/

# Single test file
python -m pytest tests/test_engine.py -v

# Engine smoke test (pure Python, no stack needed)
python -m engine.tests

# With coverage report
python -m pytest tests/ --cov=. --cov-report=term-missing
```

### Test coverage

| File | What it tests |
|------|---------------|
| `test_engine.py` | DAG reconstruction across all span orderings, fan-in/fan-out, vector clock blame |
| `test_collector.py` | gRPC span/decision ingestion, WAL write-through |
| `test_collector_metrics.py` | Prometheus metrics emission from the collector |
| `test_api.py` / `test_api_main.py` | REST endpoints, SSE stream, filtering, error handling |
| `test_governance.py` / `test_governance_tasks.py` | Metadata redaction, TTL enforcement, allowlist |
| `test_slo.py` / `test_slo_spec.py` / `test_slo_evaluator_unit.py` | SLO spec parsing, evaluation logic, edge cases |
| `test_alerting.py` / `test_alerting_incidents.py` / `test_alerting_worker.py` | Incident creation, deduplication, ack/resolve |
| `test_e2e_tenant_isolation.py` | Cross-tenant data isolation end-to-end |
| `test_metrics_server.py` | Metrics HTTP server smoke test |
| `test_scripts_smoke.py` | Bootstrap and helper scripts sanity check |
| `test_integration_optional.py` | Optional integration tests requiring a live stack |

---

## API Reference

All endpoints served at `http://localhost:8000`. Interactive docs at `/docs`.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/traces` | Paginated list of recent reconstructed traces |
| `GET` | `/traces/{trace_id}` | Full trace: DAG, blame V1 + V2, metadata |
| `GET` | `/traces/{trace_id}/decisions` | All decision events for a trace |
| `GET` | `/traces/{trace_id}/root-cause` | Decisions ranked by downstream latency/token/error impact |
| `GET` | `/agents/blame` | Cross-trace agent leaderboard (`?hours=1|6|24|168`) |
| `GET` | `/traces/stream` | SSE stream — pushes new trace IDs as they are reconstructed |
| `GET` | `/slo/status` | Current SLO evaluation results |
| `GET` | `/incidents` | Open/acked/resolved incidents |
| `POST` | `/incidents/{id}/ack` | Acknowledge an incident |
| `POST` | `/incidents/{id}/resolve` | Resolve an incident |
| `GET` | `/metrics` | Prometheus metrics (collector-side at `:9090`) |

Example calls:

```bash
curl http://localhost:8000/traces
curl http://localhost:8000/traces/<trace_id>
curl http://localhost:8000/traces/<trace_id>/decisions
curl http://localhost:8000/traces/<trace_id>/root-cause
curl http://localhost:8000/agents/blame?hours=24
```

---

## UI Dashboard

Open **http://localhost:5173** after starting the UI.

### Pages

**Trace List** (`/`)
- Live-polling list backed by SSE; refreshes automatically as new traces arrive.
- Filter bar: all / errors only / clean.

**Trace Detail** (`/traces/:id`)
- **Timeline waterfall** — per-span horizontal bars with latency, token counts, and error badges.
- **Causal DAG tree** — nodes connected by explicit or inferred (repaired) edges; inferred edges are visually distinguished.
- **Decision Chain** — collapsible cards per decision showing agent, type, rationale, candidates with pros/cons, confidence score, and evidence references.
- **Root-cause tab** — decisions ranked by downstream impact (latency Δ, token Δ, errors triggered).
- **Blame panel** — per-agent blame scores (latency share, token share, error count, weighted total).

**Blame Leaderboard** (`/blame`)
- Cross-trace agent blame aggregated over 1h / 6h / 24h / 7d time windows.

**SLO Dashboard** (`/slo`)
- Current SLO evaluation results with pass/fail status and violation details.

**Incidents** (`/incidents`)
- Incident list with ack and resolve actions inline.

---

## Module Guide

| Module | File(s) | Description |
|--------|---------|-------------|
| **SDK core** | `sdk/core.py` | `@instrument_node`, `begin_span` / `build_span` / `emit_span`, `emit_decision`, coverage registry |
| **SDK client** | `sdk/client.py` | Non-blocking gRPC stub (background thread); agent latency unaffected if collector is down |
| **SDK taxonomy** | `sdk/taxonomy.py` | Canonical event type strings |
| **SDK calibration** | `sdk/calibration.py` | Token estimation helpers |
| **Collector server** | `collector/server.py` | Async gRPC server; accepts `IngestSpan` and `IngestDecision` RPCs |
| **Collector writer** | `collector/writer.py` | Batched ClickHouse inserts with WAL replay on startup |
| **Collector metrics** | `collector/metrics_server.py` | Prometheus metrics HTTP server |
| **Engine DAG** | `engine/dag.py` | Vector clock DAG reconstruction; flags inferred parents |
| **Engine blame V1** | `engine/blame.py` | Point-estimate latency/token/error blame scores |
| **Engine blame V2** | `engine/blame_v2.py` | V1 + confidence intervals, std-dev, error amplification factor |
| **Engine worker** | `engine/worker.py` | Polls `raw_spans`, writes `reconstructed_traces` and `decision_edges` |
| **API** | `api/main.py` | All REST endpoints + inline SSE stream; CORS enabled |
| **SLO spec** | `slo/spec.py` | Declarative SLO model |
| **SLO evaluator** | `slo/evaluator.py` | Evaluates specs against live trace data |
| **SLO worker** | `slo/worker.py` | Periodic evaluation loop |
| **Alerting incidents** | `alerting/incidents.py` | Incident model + deduplication |
| **Alerting worker** | `alerting/worker.py` | Stuck-agent / runaway-token detector |
| **Governance** | `shared/governance.py` | Metadata redaction patterns, TTL policy, allowlist |
| **Auth** | `shared/trace_auth.py` | Tenant resolution from request headers; `DEFAULT_TENANT_ID = "default"` |
| **DB init** | `db/init_db.py` | Idempotent schema creation for all ClickHouse tables |
| **Proto** | `proto/tracing.proto` | Single source of truth for the gRPC contract |

---

## How It Works

### 1. Instrumentation

Wrap any LangGraph node with one decorator:

```python
from sdk import instrument_node, new_trace_context

@instrument_node("research_agent")
def research(state):
    return {"research_findings": "...", "_input_tokens": 200, "_output_tokens": 100}

# Initialize tracing context on graph entry
state = {"messages": ["go"], **new_trace_context()}
app.invoke(state)
```

For non-LangGraph code use the framework-agnostic API directly:

```python
from sdk import begin_span, build_span, emit_span

ctx = begin_span(
    agent_name="custom_worker",
    trace_id=payload.get("_trace_id"),
    vector_clock=payload.get("_vector_clock"),
    parent_span_id=payload.get("_parent_span_id"),
)
# ... run your logic ...
span = build_span(ctx=ctx, agent_name="custom_worker", event_type="tool_use",
                  state=state, result=result, error=err)
emit_span(span)
```

### 2. Decision observability

```python
from sdk import emit_decision

emit_decision(state, {
    "decision_type": "model_selection",
    "rationale": "gpt-4o-mini is cheaper and sufficient for summarisation",
    "chosen": "gpt-4o-mini",
    "candidates": [
        {"id": "gpt-4o",      "pros": ["better quality"], "cons": ["10x cost"]},
        {"id": "gpt-4o-mini", "pros": ["cheap", "fast"],  "cons": ["weaker reasoning"]},
    ],
    "confidence": 0.85,
})
```

### 3. Causal DAG reconstruction

The engine fetches all spans for a trace and, for each span:

1. If `parent_span_id` is present and resolves to a span in the batch → **explicit** edge.
2. Otherwise, finds the causally-latest span whose vector clock strictly precedes this one → **inferred** edge (flagged in output).

Vector clock A strictly precedes B iff `A[k] <= B[k]` for every agent `k` and `A ≠ B`. Concurrent clocks correctly produce sibling edges.

### 4. Blame scoring

For each agent in a trace:

- **Latency share** = agent wall time / total trace duration
- **Token share** = agent tokens / total trace tokens
- **Error count** = number of error spans

Weighted into a 0–100 `blame_score` (weights tunable in `engine/blame.py`).

**V2** adds per-agent confidence intervals via bootstrap resampling, standard deviation, and an error amplification factor that lifts the blame score for agents whose errors caused downstream retries.

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DEMO_MODE` | `true` | Use synthetic LLM responses; no API key needed |
| `OPENAI_API_KEY` | — | Required when `DEMO_MODE=false` |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible endpoint |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model name passed to the LLM |
| `DEMO_CODER_FAILURE_RATE` | `0.5` | Fraction of coder agent runs that raise a hallucination error |

Copy `.env.local` to `.env` and fill in values for real-LLM mode.

---

## Development Guide

### Recompile protobuf

```bash
bash scripts/compile_proto.sh
```

Run whenever `proto/tracing.proto` changes. Outputs land in `generated/`.

### Reinitialise the schema (idempotent)

```bash
python -m db.init_db
```

### Reset ClickHouse (destroys all data)

```bash
docker compose down -v
```

### Engine smoke test (no stack required)

```bash
python -m engine.tests
```

### Lint / format

```bash
ruff check .
ruff format .
```

### UI development

```bash
cd ui
npm install        # first time only
npm run dev        # starts Vite at :5173 with /api/* proxy to :8000
npm run build      # production build
npm run typecheck  # TypeScript strict check
```

`ui/src/types.ts` mirrors the FastAPI response shapes exactly — update both together when the API schema changes.

### Protobuf warning

The installed protobuf gencode (5.27.2) is older than the runtime (5.28.2). This produces a `UserWarning` on startup but does not affect functionality. Regenerate with `compile_proto.sh` to silence it.

### macOS SSL note

`urllib` does not use the macOS system keychain. When `DEMO_MODE=false`, the pipeline uses `certifi` for SSL. If you see `CERTIFICATE_VERIFY_FAILED`, run `pip install certifi`.
