#!/usr/bin/env bash
# Stop all trace-aggregator services including ClickHouse.
# Pass --volumes / -v to also wipe the ClickHouse data volume.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

VOLUMES=false
for arg in "$@"; do
  [[ "$arg" == "--volumes" || "$arg" == "-v" ]] && VOLUMES=true
done

log() { printf '\033[1;32m→\033[0m %s\n' "$*"; }

# Kill Python processes started by run.sh (collector, engine, uvicorn)
for pat in "collector.server" "engine.worker" "uvicorn api.main"; do
  pids=$(pgrep -f "$pat" 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    log "Stopping: $pat"
    echo "$pids" | xargs kill 2>/dev/null || true
  fi
done

# Kill Vite dev server
pids=$(pgrep -f "vite" 2>/dev/null || true)
if [[ -n "$pids" ]]; then
  log "Stopping: vite"
  echo "$pids" | xargs kill 2>/dev/null || true
fi

log "Stopping ClickHouse..."
if $VOLUMES; then
  docker compose down -v
  log "ClickHouse stopped and volume removed."
else
  docker compose down
  log "ClickHouse stopped (data volume preserved; use -v to also remove it)."
fi
