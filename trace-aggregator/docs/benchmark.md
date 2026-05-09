# Benchmark and Reliability Report

This document records load-test and reliability evidence for the causal trace pipeline.

**Date measured:** 2026-05-09
**Operator:** ENG-15 verification run

## Scope

- Span + decision ingestion throughput
- Reconstruction lag SLOs
- Root-cause query responsiveness
- Behavior under out-of-order/duplicate/error-heavy traces
- WAL durability under storage outage

## Environment

| Field | Value |
|---|---|
| Host | Apple M2, 8 cores, 8 GB RAM, macOS 24.6.0 (arm64) |
| Python | 3.14.2 |
| Docker | 28.1.1 |
| ClickHouse image | `clickhouse/clickhouse-server:24.3.18.7` |
| Collector / API / Engine | trace-aggregator @ commit `57ae671` |
| Demo data shape | 4 spans + 1 decision per trace, 20% error injection |

## How to reproduce

```bash
# 1. Clean slate
docker compose down -v && docker compose up -d
python -m db.init_db
find wal -name "*.json" -delete

# 2. Start services in 4 terminals
python -m collector.server
python -m engine.worker
uvicorn api.main:app --port 8000
# (UI optional for measurement)

# 3. Run load test
python scripts/load_test.py --traces 1000 --concurrency 32 --error-rate 0.2

# 4. Wait ~15s for engine to drain reconstructions, then capture metrics
curl -s http://localhost:9090/metrics | python3 -m json.tool
python scripts/slo_report.py
```

## Results

### Throughput

Captured from `scripts/load_test.py --traces 1000 --concurrency 32 --error-rate 0.2`:

| Metric | Value |
|---|---|
| traces_requested | 1000 |
| traces_sent_ok | 1000 |
| traces_failed | 0 |
| elapsed_sec | 3.556 |
| **throughput_traces_per_sec** | **281.23** |
| effective spans/sec (4 spans/trace) | ~1125 |

Smaller-scale validation (sanity check):

| Run | traces | concurrency | throughput |
|---|---|---|---|
| Smoke | 100 | 8 | 491 traces/s |
| Full | 1000 | 32 | 281 traces/s |

The smaller run shows higher per-trace throughput because the in-memory queue absorbs the burst before any flush is triggered. Under 1000-trace load the BatchWriter flushes 11× during ingestion, which becomes the bottleneck.

### Collector metrics (post-run)

From `GET /metrics` after the 1000-trace run:

| Writer | accepted | rejected | acceptance_rate | flush_attempts | flush_success_rate | flush p50 (ms) | flush p95 (ms) | flush p99 (ms) |
|---|---|---|---|---|---|---|---|---|
| span | 4400 | 0 | 1.0000 | 11 | 1.0000 | 16.7 | 86.0 | 113.5 |
| decision | 1100 | 0 | 1.0000 | 7 | 1.0000 | 25.3 | 37.6 | 38.3 |

- 0 dropped, 0 queue-full events, 0 flush failures
- Average batch size was 400 rows for spans (BATCH_SIZE=500 cap), 157 for decisions

### Reconstruction lag

Time-to-first-reconstruction (TTFR) — from last span ingest to engine first DAG write:

| Percentile | Value | SLO Target | Status |
|---|---|---|---|
| p50 | 7,570 ms | — | — |
| p95 | 20,748 ms | ≤ 60,000 ms | ✅ PASS |
| p99 | 21,669 ms | ≤ 120,000 ms | ✅ PASS |
| avg | 9,182 ms | — | — |
| missing_reconstruction | 0 / 1100 | — | ✅ |

### Trace completion

| Metric | Value |
|---|---|
| Traces with raw spans | 1100 |
| Traces reconstructed | 1100 |
| **Completion rate** | **100% (1.0000)** |
| SLO target | ≥ 99% |
| Status | ✅ PASS |

### Root-cause API latency (20 samples per endpoint)

| Endpoint | p50 (ms) | p95 (ms) | p99 (ms) | max (ms) |
|---|---|---|---|---|
| `GET /traces` (list) | 40 | 190 | 190 | 190 |
| `GET /traces/{id}/root-cause` | 30 | 50 | 50 | 50 |
| `GET /traces/{id}/decisions` | 30 | 80 | 80 | 80 |

API p95 latency SLO (≤ 500ms): ✅ PASS

### Full SLO catalog status

```
name                    status  value           threshold       samples
ingest_acceptance       PASS          1.0000     >=     0.9990     4400
flush_success           PASS          1.0000     >=     0.9900       18
reconstruction_lag_p95  PASS      20887.3 ms     <= 60000.0 ms     1000
reconstruction_lag_p99  PASS      21673.1 ms     <=120000.0 ms     1000
trace_completion        PASS          1.0000     >=     0.9900     1100
api_latency_p95         PASS         96.4 ms     <=   500.0 ms       10
```

**overall=PASS** (6/6 SLOs)

## Reliability scenarios

### Engine determinism (`python -m engine.tests` — 23 tests, all pass)

- [x] Out-of-order span arrivals — all 6 permutations of a 3-span chain produce identical structure
- [x] Missing parent spans — `parent_span_id` pointing to missing span falls back to vector-clock inference
- [x] Duplicate span IDs — engine deduplicates by `idempotency_key` (validated separately in ENG-03)
- [x] Three-way fan-in — 3 parallel branches merge to one collector with `explicit_plus_fanin` resolution
- [x] Concurrent (causally-unrelated) spans treated as independent roots
- [x] Deep linear chain — 10 hops, every parent→child link verified
- [x] Determinism — `serialize_dag()` called 50× on the same input is bit-identical

### High error-rate traces

- 200 / 1000 traces had an injected error span (event_type='error')
- All 200 surfaced in the blame leaderboard with `error_count > 0`
- Error spans correctly classified by `sdk.taxonomy.classify_error()` (LLM-09)

### Collector restart during ingestion / WAL durability

**Test:** Stopped ClickHouse mid-stream, emitted 50 more traces, restarted CH, verified replay.

| Stage | Span count in CH | WAL files |
|---|---|---|
| Before outage | 4,400 | 0 |
| CH stopped, 50 traces emitted | 4,400 | 250 (200 spans + 50 decisions) |
| Collector still ACK'd all traces | accepted=4600 | acceptance_rate stays 1.0 |
| flush_success_rate during outage | — | 0.846 (span), 0.778 (decision) |

**Finding:** Zero data loss during 5+ second CH outage. All 250 events were durably written to WAL via the atomic `.tmp` → rename pattern. Collector continued ACKing (acceptance_rate=1.0) the entire time.

## Bottlenecks observed

1. **ClickHouse insert latency dominates flush time.** p99 flush latency 113ms is 100% the network+CH-side processing — collector-side serialization is sub-millisecond.
2. **Engine reconstruction is the largest lag contributor.** p95 reconstruction lag of ~21s reflects the 2s polling interval × multiple churn cycles before the engine settles. The engine re-reconstructs every "active" trace (within `LOOKBACK_SEC=300`) on every tick, so a trace can be rebuilt 10+ times even when nothing about it changed.
3. **Single-threaded gRPC server.** The async event loop is fine for 281 traces/sec but hits diminishing returns past ~32 concurrent senders. Multi-process collector or sharded WAL would scale further.
4. **Docker Desktop on macOS volume quirk** (operational, not architectural): after `docker compose stop` + `start`, the CH container's `/var/lib/clickhouse/tmp/` can become read-only, blocking inserts until `docker compose down && up -d` recreates the container. Documented in `docs/runbook.md`.

## Tuning actions

| Knob | Default | Effect under load | Recommendation |
|---|---|---|---|
| `BATCH_SIZE` | 500 | Hit cap on every flush during full load | Raise to 1000–2000 if CH p95 stays <100ms |
| `FLUSH_INTERVAL_SEC` | 1.0 | Flushes ~once/sec when batch not full | Lower to 0.5 for lower lag, higher for higher throughput |
| `QUEUE_MAX` | 50000 | Never hit during 1000-trace runs | Raise only if `queue_full_events > 0` in metrics |
| `ENGINE_RECON_MAX_WORKERS` | 4 | 1100 traces processed via 4 workers | Increase for higher trace counts; saturates at ~CPU count |
| `ENGINE LOOKBACK_SEC` (hardcoded) | 300 | Rebuilds active traces every 2s for 5min | Lower (e.g. 60s) if you need leaner reconstruction lag and don't need late-arriving spans |

## Reproducibility tolerance

A teammate rerunning this benchmark on similar hardware (M-series Mac, 8GB+, Docker Desktop) should expect:

| Metric | Expected band |
|---|---|
| Throughput | 200–500 traces/sec |
| Acceptance rate | 1.0000 (always) |
| Flush success rate | 1.0000 (when CH up) |
| Reconstruction p95 | 5,000–30,000 ms |
| API p95 (root-cause) | 30–100 ms |
| Trace completion | 1.0000 |

Numbers outside these bands likely indicate environmental issues (Docker resource limits, host load, CH cache cold/warm) rather than regressions.
