#!/usr/bin/env bash
# First-run setup. Call this once after cloning. Idempotent.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "→ Installing Python deps..."
pip install -r requirements.txt

echo "→ Compiling proto..."
bash scripts/compile_proto.sh

echo "→ Starting ClickHouse..."
docker compose up -d

echo "→ Initializing schema..."
python -m db.init_db

echo
echo "✅ Bootstrap complete. Open three terminals and run:"
echo "   1) python -m collector.server"
echo "   2) python -m engine.worker"
echo "   3) uvicorn api.main:app --reload --port 8000"
echo
echo "   Then in a fourth terminal: python -m demo.pipeline"
