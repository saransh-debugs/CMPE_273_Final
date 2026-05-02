"""SLO definition and reporting for the trace aggregator.

The package owns three things:
  - spec:      named SLOs and their target/threshold values
  - evaluator: pulls live signals from ClickHouse + collector /metrics
  - worker:    periodic loop that writes status rows to tracing.slo_status

Read order for new readers: spec.py -> evaluator.py -> worker.py.
"""
