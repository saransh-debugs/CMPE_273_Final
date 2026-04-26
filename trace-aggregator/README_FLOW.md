# Core Flow README (Current Behavior)

This document explains the **actual runtime flow** of the system today, including where behavior is hardcoded versus dynamically produced.

## Short answer to your question

- **Parts are hardcoded only in `DEMO_MODE=true` (synthetic mode).**
- In `DEMO_MODE=false`, token/latency/decision content come from real model calls.
- The platform core (ingest, reconstruction, blame, API, UI wiring) is real and dynamic.
- The "agent rationale text" is only as real as what each node emits.

---

## 1) End-to-end runtime path

1. `demo/pipeline.py` runs LangGraph nodes.
2. Each node wrapped with `@instrument_node(...)` emits an `AgentSpan` via SDK gRPC client.
3. Optional decision events are emitted via `emit_decision(...)`.
4. `collector/server.py` receives spans/decisions and batch-writes to ClickHouse:
   - `tracing.raw_spans`
   - `tracing.raw_decisions`
5. `engine/worker.py` polls traces, reconstructs DAG, computes blame, writes:
   - `tracing.reconstructed_traces`
   - `tracing.decision_edges` (decision impact links)
6. `api/main.py` serves:
   - `/traces`, `/traces/{id}`, `/traces/{id}/spans`
   - `/traces/{id}/decisions`, `/traces/{id}/root-cause`
   - `/agents/blame`
7. UI consumes these endpoints and renders:
   - Timeline
   - DAG
   - Blame
   - Decision Chain panel

---

## 2) What is hardcoded right now

## In demo orchestration

- `DEMO_MODE=true`: `demo/pipeline.py` uses mock behavior (`_mock_llm`) for latency/token simulation.
- `DEMO_MODE=true`: `coder_agent` uses configurable synthetic failure injection (`DEMO_CODER_FAILURE_RATE`).
- `DEMO_MODE=false`: calls real OpenAI-compatible `chat/completions` endpoint and parses real token usage.
- `DEMO_MODE=false`: reviewer decision payload is generated from model output (JSON parse + fallback guards).

Example of what is currently hardcoded in demo decision emission:
- decision type: `route_branch`
- selected candidate: `review`
- rationale summary text
- candidate list and scores

This means "why" text is synthetic in demo mode, and model-produced in real mode.

## In engine decision impact

- Decision impact is currently computed from DAG descendants of `source_span_id`.
- Impact fields are aggregate downstream values (`latency/tokens/errors`), not a full causal counterfactual.

---

## 3) What is dynamic and real now

- gRPC transport and non-blocking SDK emission are real.
- ClickHouse persistence for spans/decisions is real.
- DAG reconstruction from vector clocks + parent links is real.
- Blame scoring from observed spans is real.
- Root-cause edges are generated from actual trace graph structure.
- API/UI rendering uses actual stored data.

---

## 4) Current decision data contract

Decision events are first-class in proto and include:
- trace/decision IDs
- source span and actor agent
- decision type
- selected candidate
- confidence
- rationale summary
- evidence references
- candidate list
- metadata blob

Collector stores raw decision rows, engine materializes `decision_edges` for query and UI.

---

## 5) Why you don’t yet see rich agent self-talk by default

The system does **not** automatically capture hidden chain-of-thought from model internals.
It only captures what your code explicitly emits as decision artifacts.

So if you want logs like:
"I should validate output with a python command before invoking coding agent",
you must implement a decision-generation step that returns this summary and emit it.

---

## 6) How to move from hardcoded to real decision reasoning

Implement a strict **Decide -> Act** pattern:

1. Add a decision node before branch/tool/handoff.
2. Ask model for structured JSON (schema-constrained).
3. Validate JSON with Pydantic.
4. `emit_decision(...)` with returned rationale/candidates/confidence.
5. Execute selected action.
6. Optionally emit post-action decision update.

When done, decision text becomes model-generated (but bounded and auditable), not static code text.

---

## 7) Safe toggle now available

`demo/pipeline.py` now supports a safe runtime toggle:

- `DEMO_MODE=true` (default):
  - fully offline synthetic behavior
  - deterministic-safe for local demos
  - no API key needed
- `DEMO_MODE=false`:
  - real OpenAI-compatible `chat/completions` calls
  - real token usage from provider response metadata
  - real decision JSON generation path
  - fails with a clear error if key is missing

### Environment variables

- `DEMO_MODE=true|false`
- `OPENAI_API_KEY=<key>` (required in real mode)
- `OPENAI_BASE_URL=<openai-compatible-base-url>` (default `https://api.openai.com/v1`)
- `OPENAI_MODEL=<model-name>` (default `gpt-4o-mini`)
- `DEMO_CODER_FAILURE_RATE=0.5` (demo-only failure injection)
- `LLM_TIMEOUT_SEC=30`

### Example commands

```bash
# Offline/synthetic mode (default)
DEMO_MODE=true python -m demo.pipeline

# Real mode against an OpenAI-compatible endpoint
DEMO_MODE=false \
OPENAI_API_KEY=... \
OPENAI_BASE_URL=https://api.openai.com/v1 \
OPENAI_MODEL=gpt-4o-mini \
python -m demo.pipeline
```

---

## 8) Free/low-cost ways to run real mode

All options below work by setting `OPENAI_BASE_URL` and `OPENAI_MODEL` if they provide an OpenAI-compatible API.

1. **Ollama local (free, best for zero-cost)**
   - Run model on your machine with an OpenAI-compatible endpoint.
   - Typical base URL: `http://localhost:11434/v1` (depending on bridge/runtime setup).
   - Pros: no token costs, private local runtime.
   - Tradeoff: lower quality/speed on small laptops.

2. **Groq free tier (fast, cloud-hosted)**
   - Use Groq API key and OpenAI-compatible URL.
   - Typical base URL: `https://api.groq.com/openai/v1`
   - Good for quick testing with minimal spend.

3. **OpenRouter free models**
   - Route through free model offerings.
   - Typical base URL: `https://openrouter.ai/api/v1`

4. **Gemini free tier via adapter/proxy**
   - If using an OpenAI-compatible proxy layer, wire via `OPENAI_BASE_URL`.

Operational recommendation:
- keep `DEMO_MODE=true` as fallback in scripts
- run `DEMO_MODE=false` only when key/model endpoint are confirmed
- use low-cost/small models for routine testing

---

## 9) Operational checks to confirm flow is active

Run and verify:

- `GET /health` -> `ok:true`
- `GET /traces` -> non-empty
- ClickHouse `tracing.raw_decisions` -> non-zero
- `GET /traces/{id}/decisions` -> decision payloads
- `GET /traces/{id}/root-cause` -> impact edges
- UI trace detail -> Decision Chain panel populated

---

## 10) Known limitations (current)

- Demo decision coverage is still partial (not every branch emits explicit decisions).
- Decision rationale quality depends on emitter logic.
- Root-cause ranking is impact-based, not full causal proof/counterfactual.
- API filter depth for decisions is basic and can be expanded.

---

## 11) Practical summary

- Core platform functionality is implemented and working.
- Decision observability pipeline is implemented.
- Decision semantics are currently **partly hardcoded in demo logic**.
- Full "agent reasoning narrative" requires explicit, structured decision generation in orchestration code.
