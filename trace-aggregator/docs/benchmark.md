# Benchmark and Reliability Report

This document records load-test and reliability evidence for the causal trace pipeline.

## Scope

- Span + decision ingestion throughput
- Reconstruction lag SLOs
- Root-cause query responsiveness
- Behavior under out-of-order/duplicate/error-heavy traces

## Environment

- Host:
- Python version:
- Docker Desktop version:
- ClickHouse image:
- Collector/API/Engine versions:

## Load test command

```bash
python scripts/load_test.py --traces 1000 --concurrency 32 --collector localhost:50051
```

## SLO report command

```bash
python scripts/slo_report.py --minutes 60
```

## Results

### Throughput

- traces_requested:
- traces_sent_ok:
- traces_failed:
- elapsed_sec:
- throughput_traces_per_sec:

### Reconstruction lag

- reconstruct_lag_ms_avg:
- reconstruct_lag_ms_p95:
- reconstruct_lag_ms_p99:
- missing_reconstruction:

### Root-cause API (manual/curl test)

- `/traces/{id}/root-cause` p95 (ms):
- `/traces/{id}/decisions` p95 (ms):

## Reliability scenarios

- [ ] Out-of-order span arrivals
- [ ] Missing parent spans
- [ ] Duplicate span IDs
- [ ] Collector restart during ingestion
- [ ] High error-rate traces

## Bottlenecks observed

- TBD

## Tuning actions

- TBD

