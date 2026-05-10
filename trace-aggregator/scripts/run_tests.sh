#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
python -m engine.tests
python -m sdk.tests_llm
python -m unittest discover -s . -p 'test_*.py' -v

UI_DIR="$ROOT/ui"
if [[ -d "$UI_DIR/node_modules/vitest" ]]; then
  echo "Running UI tests (vitest)..."
  (cd "$UI_DIR" && npm test)
else
  echo "Skipping UI tests: run 'cd ui && npm install && npm test'"
fi
