#!/usr/bin/env bash
# Start all trace-aggregator services.
# Usage:
#   ./run.sh          — start everything (collector, engine, API, UI)
#   ./run.sh --demo   — also run demo.pipeline after services are up
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

DEMO=false
for arg in "$@"; do
  [[ "$arg" == "--demo" ]] && DEMO=true
done

LOGDIR="$ROOT/.logs"
mkdir -p "$LOGDIR"

# ── helpers ────────────────────────────────────────────────────────────────────
log()  { printf '\033[1;32m→\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m⚠\033[0m  %s\n' "$*"; }
die()  { printf '\033[1;31m✗\033[0m  %s\n' "$*" >&2; exit 1; }

wait_for_port() {
  local port=$1 label=$2 tries=0
  printf '  waiting for %s on :%s ' "$label" "$port"
  until lsof -i :"$port" -sTCP:LISTEN -t &>/dev/null; do
    sleep 0.5
    printf '.'
    (( tries++ )) && (( tries > 60 )) && { echo; die "$label did not start (port $port)"; }
  done
  echo ' ready'
}

cleanup() {
  echo
  log "Shutting down background processes..."
  [[ ${#PIDS[@]} -gt 0 ]] && kill "${PIDS[@]}" 2>/dev/null || true
  wait 2>/dev/null || true
  log "Done. ClickHouse is still running; use './stop.sh' to bring it down."
}
PIDS=()
trap cleanup EXIT INT TERM

# ── 1. ClickHouse ──────────────────────────────────────────────────────────────
log "Starting ClickHouse (docker compose)..."
docker compose up -d
log "Waiting for ClickHouse health check..."
for i in $(seq 1 40); do
  docker inspect --format='{{.State.Health.Status}}' trace_aggregator_db 2>/dev/null \
    | grep -q "healthy" && break
  sleep 1
  (( i == 40 )) && die "ClickHouse did not become healthy in time"
done
log "ClickHouse healthy."

# ── 2. Schema init (idempotent) ───────────────────────────────────────────────
log "Ensuring schema is up to date..."
python -m db.init_db || die "db.init_db failed — check ClickHouse logs"

# ── 3. gRPC Collector ─────────────────────────────────────────────────────────
log "Starting collector (port 50051)..."
python -m collector.server >"$LOGDIR/collector.log" 2>&1 &
PIDS+=($!)
wait_for_port 50051 collector

# ── 4. Causal Engine worker ────────────────────────────────────────────────────
log "Starting engine worker..."
python -m engine.worker >"$LOGDIR/engine.log" 2>&1 &
PIDS+=($!)

# ── 5. FastAPI query layer ─────────────────────────────────────────────────────
log "Starting API (port 8000)..."
uvicorn api.main:app --port 8000 >"$LOGDIR/api.log" 2>&1 &
PIDS+=($!)
wait_for_port 8000 api

# ── 6. Vite UI ────────────────────────────────────────────────────────────────
log "Starting UI (port 5173)..."
(cd ui && npm run dev --silent) >"$LOGDIR/ui.log" 2>&1 &
PIDS+=($!)
wait_for_port 5173 ui

echo
log "All services up."
echo "  UI  → http://localhost:5173"
echo "  API → http://localhost:8000"
echo "  Logs in $LOGDIR/"
echo "  Press Ctrl-C to stop."
echo

# ── 7. Optional demo ──────────────────────────────────────────────────────────
if $DEMO; then
  log "Running demo pipeline (DEMO_MODE=true)..."
  DEMO_MODE=true python -m demo.pipeline
fi

# Keep script alive so trap fires on Ctrl-C
wait "${PIDS[@]}"
