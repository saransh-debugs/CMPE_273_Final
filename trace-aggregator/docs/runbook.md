# Deployment and Operations Runbook

This runbook covers first-time setup, normal operation, failure recovery, and troubleshooting for the distributed trace aggregator platform. A new operator should be able to deploy, validate, and recover using this document alone.

---

## Prerequisites

| Requirement | Minimum version | Check |
|---|---|---|
| Docker Desktop | 4.x | `docker --version` |
| Python | 3.11+ | `python --version` |
| Node.js | 18+ | `node --version` |
| gRPC tools | via pip | installed by bootstrap |

All Python commands below assume you are inside `trace-aggregator/` with the venv activated:

```bash
cd trace-aggregator
source .venv/bin/activate
```

---

## First-Time Setup

Run once after cloning. Idempotent — safe to rerun.

```bash
cd trace-aggregator
bash scripts/bootstrap.sh
cd ui && npm install
```

`bootstrap.sh` does four things in order:
1. `pip install -r requirements.txt`
2. `bash scripts/compile_proto.sh` — compiles `proto/tracing.proto` → `generated/`
3. `docker compose up -d` — starts ClickHouse on ports 8123 (HTTP) and 9000 (TCP)
4. `python -m db.init_db` — creates all six tables (idempotent)

---

## Startup Sequence

**Order matters.** ClickHouse must be ready before the collector, engine, or API start.

```
ClickHouse → db.init_db → collector → engine → API → UI
```

Open five terminals, all in `trace-aggregator/` with venv activated:

**Terminal 1 — ClickHouse** (if not already running):
```bash
docker compose up -d
# Verify it is healthy:
curl -s http://localhost:8123/ping   # should return "Ok."
```

**Terminal 2 — Collector** (gRPC :50051 + metrics HTTP :9090):
```bash
python -m collector.server
```
Expected startup log:
```
Collector listening on [::]:50051
Metrics server on http://0.0.0.0:9090/metrics
```

**Terminal 3 — Engine** (DAG reconstruction, polls every 2s):
```bash
python -m engine.worker
```
Expected startup log:
```
Causal engine started. Polling every 2.0s.
```

**Terminal 4 — API** (FastAPI :8000):
```bash
uvicorn api.main:app --reload --port 8000
```

**Terminal 5 — UI** (Vite :5173):
```bash
cd ui && npm run dev
```

**Optional workers** (independent, start any time):
```bash
python -m slo.worker        # SLO evaluation every 60s
python -m alerting.worker   # alert rules (runaway tokens, error bursts, stuck traces, SLO breach)
```

---

## Health Verification

After startup, validate each layer:

```bash
# 1. ClickHouse
curl -s http://localhost:8123/ping
# Expected: Ok.

# 2. Collector metrics
curl -s http://localhost:9090/healthz
# Expected: ok

curl -s http://localhost:9090/metrics | python3 -m json.tool | grep acceptance_rate
# Expected: "acceptance_rate": 1.0  (drops to <1.0 only under backpressure)

# 3. API (returns 503 if ClickHouse unreachable)
curl -s http://localhost:8000/health
# Expected: {"ok":true}

# 4. End-to-end: emit traces and check they appear
python -m demo.pipeline
sleep 5
curl -s http://localhost:8000/traces | python3 -m json.tool | head -20
# Expected: list with at least one trace entry

# 5. UI
open http://localhost:5173    # or visit in browser
```

---

## Normal Operations

### Emitting traces (demo / development)

```bash
# DEMO_MODE=true (default) — mocks LLM calls, no API key needed
python -m demo.pipeline

# Real LLM mode
DEMO_MODE=false OPENAI_API_KEY=sk-... python -m demo.pipeline
```

### Checking SLO status

```bash
python scripts/slo_report.py          # print PASS/FAIL table
python scripts/slo_report.py --json   # machine-readable output
python -m slo.worker --once           # one-shot evaluation + persist to tracing.slo_status
```

### Checking decision coverage

```bash
# Check last 10 traces for missing coverage points
python scripts/coverage_gate.py --recent 10

# Offline smoke test (no ClickHouse needed)
python scripts/coverage_gate.py --mock
```

### Running load test

```bash
python scripts/load_test.py --traces 500 --concurrency 16 --collector localhost:50051
```

### Checking WAL backlog

```bash
# Count files waiting to be flushed
find ./wal -type f -name "*.json" | wc -l

# Spans vs decisions
ls ./wal/span | wc -l
ls ./wal/decision | wc -l
```

### Running tests

```bash
python -m engine.tests          # DAG/blame/proto tests — no external deps
python -m sdk.tests_llm         # 57 LLM observability tests — no ClickHouse/gRPC needed
python -m slo.test_regression   # SLO regression tests
```

---

## Failure Scenarios and Recovery

### Scenario 1: ClickHouse is down

**Symptom:** Collector logs `Flush failed (N spans left on WAL)`. WAL file count grows. `flush_success_rate` drops below 1.0 in `/metrics`.

**What happens automatically:** The collector still ACKs spans and decisions — they are durably written to WAL files in `./wal/span/` and `./wal/decision/` before any ACK is sent. No data is lost.

**Recovery:**
```bash
# 1. Restart ClickHouse
docker compose up -d

# 2. Re-run schema (idempotent — safe even if schema already exists)
python -m db.init_db

# 3. Option A: let the running collector auto-replay (happens on next flush cycle)
#    Watch collector logs for "Flushed N spans to ClickHouse"

# 3. Option B: replay manually (use when collector is not running)
python -m collector.replay_wal

# 4. Verify WAL drained
find ./wal -type f -name "*.json" | wc -l
# Expected: 0

# 5. Verify data in ClickHouse
curl -s http://localhost:8123 -d "SELECT count() FROM tracing.raw_spans"
curl -s http://localhost:8123 -d "SELECT count() FROM tracing.raw_decisions"
```

### Scenario 2: Collector crashes mid-run

**Symptom:** gRPC calls from SDK fail. Spans are queued in the SDK's in-memory buffer (10k cap) and dropped if the cap is exceeded.

**Recovery:**
```bash
# Restart the collector — it auto-replays any WAL files on startup
python -m collector.server
```

WAL files written before the crash are automatically replayed into ClickHouse on startup via `_replay_wal_into_queue()` in `collector/writer.py`.

### Scenario 3: Engine stops reconstructing traces

**Symptom:** New spans appear in `raw_spans` but `reconstructed_traces` is not updating. Engine logs no activity.

**Diagnosis:**
```bash
# Check if engine is running
ps aux | grep engine.worker

# Check ClickHouse connectivity from engine
curl -s http://localhost:8123 -d "SELECT 1"

# Check if spans are within the lookback window (300s default)
curl -s http://localhost:8123 \
  -d "SELECT count() FROM tracing.raw_spans WHERE ingested_at > now() - INTERVAL 300 SECOND"
```

**Common causes:**
- ClickHouse is down → restart CH, engine will reconnect on next poll
- Spans are older than `LOOKBACK_SEC` (300s) → engine ignores them by design; use `python -m collector.replay_wal` to re-ingest
- Engine process died → restart with `python -m engine.worker`

**Recovery:**
```bash
python -m engine.worker
```

The engine reconnects to ClickHouse automatically after failures (`_connect()` is called on each loop error).

### Scenario 4: API returns 500 or empty responses

**Symptom:** `curl http://localhost:8000/traces` returns an error or empty list when traces exist.

**Diagnosis:**
```bash
# Check API is running and CH-reachable
curl -s http://localhost:8000/health
# {"ok":true} → healthy; HTTP 503 → ClickHouse unreachable from API

# Check ClickHouse is reachable
curl -s http://localhost:8123/ping

# Check reconstructed_traces has data
curl -s http://localhost:8123 \
  -d "SELECT count() FROM tracing.reconstructed_traces"
```

**Common causes:**
- ClickHouse not running → `docker compose up -d`
- Engine hasn't reconstructed yet → wait 2-5s and retry
- Schema missing → `python -m db.init_db`

### Scenario 5: Large WAL backlog (disk pressure)

**Symptom:** `find ./wal -type f | wc -l` shows thousands of files. Disk filling.

**Recovery:**
```bash
# Ensure ClickHouse is running first
docker compose up -d && python -m db.init_db

# Replay all WAL files directly (bypasses running collector)
python -m collector.replay_wal

# Verify
find ./wal -type f -name "*.json" | wc -l   # should be 0
```

### Scenario 6: SLO alert firing repeatedly (alert storm)

**Symptom:** `alerting.worker` logs repeated `ALERT HIGH | slo_breach` for the same SLO.

**Cause:** Stale `slo_status` rows from a previous outage are keeping the K-of-N (3-of-5 default) threshold triggered.

**Recovery:**
```bash
# Truncate old SLO history (use POST — ClickHouse rejects TRUNCATE over HTTP GET)
python3 -c "
import clickhouse_connect
client = clickhouse_connect.get_client(host='localhost', port=8123, username='default', password='')
client.command('TRUNCATE TABLE tracing.slo_status')
print('slo_status cleared')
"

# Re-evaluate fresh
python -m slo.worker --once
```

### Scenario 7: Proto stubs out of date

**Symptom:** `ImportError` in `generated/tracing_pb2.py`, or collector rejects spans with unexpected field errors.

**Recovery:**
```bash
bash scripts/compile_proto.sh
# Then restart collector and engine
```

---

## Troubleshooting Matrix

| Symptom | First check | Likely cause | Fix |
|---|---|---|---|
| `Ok.` not returned from `:8123/ping` | `docker ps` | ClickHouse container stopped | `docker compose up -d` |
| `acceptance_rate` < 1.0 in `/metrics` | WAL backlog count | ClickHouse flush failing | Restart CH, replay WAL |
| WAL file count growing | `flush_success_rate` in `/metrics` | ClickHouse unreachable | `docker compose up -d && python -m collector.replay_wal` |
| `/traces` returns empty list | `raw_spans` count in CH | Engine not running or lookback window expired | `python -m engine.worker` |
| Engine logs nothing after startup | ClickHouse connectivity | CH not ready | Wait 5s, check `curl :8123/ping` |
| `ImportError: generated.tracing_pb2` | Proto compiled? | Stubs missing | `bash scripts/compile_proto.sh` |
| UI shows no data | API health, CH, engine | Any layer down | Follow startup sequence |
| SLO worker shows all FAIL | CH connectivity, collector running? | Collector metrics not reachable | Start collector: `python -m collector.server` |
| Alert storm on slo_breach | `slo_status` row count | Stale history | Truncate `tracing.slo_status` |
| `reconstruct_traces` not updating | Engine logs, spans age | Spans outside LOOKBACK_SEC | Restart engine, check span timestamps |
| ClickHouse `ILLEGAL_AGGREGATION` error | Query type | Unsupported `argMax` + GROUP BY in CH 24.3 | Move dedup to Python (already done in engine/API) |

---

## Full Reset (Clean Slate)

```bash
# Wipe ClickHouse volume and all WAL files
docker compose down -v
rm -rf ./wal/span/*.json ./wal/decision/*.json

# Restart
docker compose up -d
python -m db.init_db

# Verify schema
curl -s http://localhost:8123 -d "SHOW TABLES FROM tracing"
```

---

## Key Ports and Endpoints

| Service | Port | Key endpoints |
|---|---|---|
| ClickHouse HTTP | 8123 | `/ping`, raw SQL via POST body |
| ClickHouse TCP | 9000 | Native driver (clickhouse-connect) |
| Collector gRPC | 50051 | `RecordSpan`, `RecordDecision` |
| Collector metrics | 9090 | `/metrics` (JSON), `/metrics/prom` (Prometheus), `/healthz` |
| FastAPI | 8000 | `/health`, `/traces`, `/traces/{id}`, `/slo`, `/agents/blame` |
| React UI | 5173 | Web dashboard |

---

## Environment Variables Reference

| Variable | Default | Effect |
|---|---|---|
| `TRACE_COLLECTOR` | `localhost:50051` | Collector endpoint used by SDK |
| `TRACE_WAL_DIR` | `./wal` | WAL directory root |
| `DEMO_MODE` | `true` | `false` enables real LLM in demo |
| `OPENAI_API_KEY` | — | Required when `DEMO_MODE=false` |
| `BATCH_SIZE` | `500` | Collector flush threshold (rows) |
| `FLUSH_INTERVAL_SEC` | `1.0` | Collector flush interval (seconds) |
| `QUEUE_MAX` | `50000` | Collector in-memory queue cap |
| `METRICS_BIND_PORT` | `9090` | Collector metrics HTTP port |
| `ENGINE_RECON_MAX_WORKERS` | `4` | Parallel DAG reconstruction threads |
| `SLO_POLL_INTERVAL_SEC` | `60` | SLO worker cadence |
| `ALERT_COOLDOWN_SEC` | `300` | Per-key alert dedup window |
| `ALERT_WEBHOOK_URL` | — | Webhook URL for alerts (log-only if empty) |
| `TRACE_REDACT_KEYS` | — | Comma-separated keys to redact from span metadata |

---

## Architecture Quick Reference

```
SDK (@instrument_node / emit_decision)
  │
  │ gRPC :50051
  ▼
collector.server
  │ WAL write (./wal/span/*.json, ./wal/decision/*.json)
  │ ACK sent after WAL write — durability boundary
  ▼
BatchWriter queue → ClickHouse (tracing.raw_spans, tracing.raw_decisions)
  │
  │ polls every 2s
  ▼
engine.worker
  │ reconstruct_dag() → vector clock DAG
  │ compute_blame() → weighted latency/token/error score
  ▼
tracing.reconstructed_traces (ReplacingMergeTree)
tracing.decision_edges
tracing.decision_reason_chains
  │
  ▼
FastAPI :8000 ← React UI :5173
  │
slo.worker → tracing.slo_status
alerting.worker → webhook / logs
collector metrics :9090 → /metrics (JSON) + /metrics/prom (Prometheus)
```

**Idempotency**: Every span and decision carries an `idempotency_key` (`trace_id:span_id`). The engine and API deduplicate in Python on read — re-ingesting the same event is safe.

**ReplacingMergeTree**: `reconstructed_traces` keeps the row with the highest `reconstructed_at` after background merges. Query with `FINAL` or use the API (which collapses duplicates in Python) for consistent results.

**WAL atomicity**: WAL files are written using a `.tmp` rename pattern — a file is either fully written or absent. Partial writes cannot occur.
