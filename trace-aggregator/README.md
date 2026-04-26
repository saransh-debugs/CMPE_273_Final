# Distributed Trace Aggregator for Multi-Agent LLM Pipelines

Observability platform built natively for multi-agent AI workflows. Captures every span across agent boundaries, reconstructs the causal DAG using vector clocks, and surfaces a Blame View that ranks agents by their contribution to latency, token cost, and errors.

## Quick start

Requires Docker and Python 3.11+.

```bash
git clone <this-repo> trace-aggregator
cd trace-aggregator
bash scripts/bootstrap.sh
```

That installs deps, compiles the proto, starts ClickHouse, and creates the schema.

Then open five terminals:

```bash
# T1: ingestion server
python -m collector.server

# T2: causal engine worker
python -m engine.worker

# T3: query API
uvicorn api.main:app --reload --port 8000

# T4: UI dashboard (one-time: cd ui && npm install)
cd ui && npm run dev

# T5: emit some traces
python -m demo.pipeline
```

UI: http://localhost:5173 — API: http://localhost:8000

### Demo toggle (safe)

```bash
# default offline synthetic mode
DEMO_MODE=true python -m demo.pipeline

# real LLM mode (OpenAI-compatible API)
DEMO_MODE=false OPENAI_API_KEY=... OPENAI_BASE_URL=https://api.openai.com/v1 OPENAI_MODEL=gpt-4o-mini python -m demo.pipeline
```

Hit the API:

```bash
curl http://localhost:8000/traces                       # recent traces
curl http://localhost:8000/traces/<trace_id>            # full DAG + blame
curl http://localhost:8000/traces/<trace_id>/decisions  # decision events for a trace
curl http://localhost:8000/traces/<trace_id>/root-cause # decision impact chain
curl http://localhost:8000/agents/blame?hours=1         # global leaderboard
```

## Architecture

```
LangGraph agents                  
   │  @instrument_node decorator emits gRPC spans  
   ▼                                          
Collector (async gRPC, port 50051)            
   │  buffers + batches inserts (spans + decisions)               
   ▼                                          
ClickHouse (raw_spans + raw_decisions tables)                  
   │                                          
Causal Engine worker                          
   │  reconstructs DAG via vector clocks      
   │  computes per-agent blame + decision impact edges               
   ▼                                          
ClickHouse (reconstructed_traces + decision_edges tables)       
   │                                          
FastAPI query layer (port 8000) ──▶ UI (Vite, port 5173)  
```

## Repository layout

| Directory     | What lives here                                              | Suggested owner |
|---------------|--------------------------------------------------------------|-----------------|
| `proto/`      | The shared `.proto` contract — single source of truth        | shared          |
| `db/`         | ClickHouse schema init (`init_db.py`)                         | shared          |
| `sdk/`        | `@instrument_node` decorator + non-blocking gRPC client      | Person 1        |
| `collector/`  | Async gRPC server + batch ClickHouse writer                  | Person 2        |
| `engine/`     | Vector clock DAG reconstruction + blame computation          | Person 3        |
| `api/`        | FastAPI query API (reads ClickHouse)                         | Person 4        |
| `demo/`       | Runnable 4-agent LangGraph that exercises the whole pipeline | shared          |

## How it actually works

### 1. Instrumentation

The SDK is one decorator. Wrap any LangGraph node:

```python
from sdk import instrument_node, new_trace_context

@instrument_node("research_agent")
def research(state):
    # ... your normal node logic ...
    return {"research_findings": "...", "_input_tokens": 200, "_output_tokens": 100}
```

Initialize tracing fields on entry:

```python
state = {"messages": ["go"], **new_trace_context()}
app.invoke(state)
```

The decorator handles vector clock progression, span generation, and shipment to the collector. Spans are sent on a background thread — agent latency is unaffected even if the collector is offline.

### 2. Causal reconstruction

Spans cross process boundaries and arrive out of order. The engine fetches all spans for a trace, then for each span:

1. If the explicit `parent_span_id` exists in the batch, use it.
2. Otherwise, find the causally-latest span whose vector clock strictly precedes ours and call it the inferred parent.

Vector clock A precedes B iff `A[k] <= B[k]` for every agent `k` and `A != B`. Concurrent (sibling) clocks fail the test, which is exactly what we want for parallel branches.

The engine flags inferred parents in the output so the UI can mark them as repaired.

### 3. Blame

For each agent in a trace we compute share of latency, share of tokens, and error count, then weight them into a 0–100 `blame_score`. Default weights live in `engine/blame.py` — tune for your team's priorities.

### 4. Decision observability

Decision events are first-class records (`DecisionEvent`) emitted by orchestrator/reviewer/tool-routing logic. They are stored in `raw_decisions`, linked to downstream impact in `decision_edges`, and exposed via:

- `/traces/{trace_id}/decisions`
- `/traces/{trace_id}/root-cause`

In the UI, these appear in the **Decision Chain** panel on the trace detail page.

## Development

Smoke-test the engine without the rest of the stack:

```bash
python -m engine.tests
```

Recompile the proto whenever `tracing.proto` changes:

```bash
bash scripts/compile_proto.sh
```

Reset everything:

```bash
docker compose down -v   # nukes the ClickHouse volume too
```

## Roadmap

- [x] Phase 1 — DB, proto, schema, bootstrap
- [x] Phase 2 — gRPC collector with batch writer
- [x] Phase 3 — SDK `@instrument_node` for LangGraph
- [x] Phase 4 — Causal engine: vector clock DAG + blame
- [x] Phase 5 — FastAPI query layer
- [ ] Phase 5b — UI: Trace Timeline (waterfall), DAG view, Blame leaderboard
- [ ] Phase 6 — Alerting on stuck agents / runaway token usage
- [ ] Phase 7 — Load test (simulate 100s of concurrent traces)
