#!/usr/bin/env bash
# Phase 17: run everything. Standalone phase scripts first (they need only
# the app's deps), then the pytest suite with coverage.
# Usage (from apps/api): bash scripts/run_tests.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== Standalone per-phase test scripts =="
for f in tests/test_*.py; do
  echo "--- $f"
  python "$f"
done

echo "== pytest (unit + integration + postgres if available) =="
pytest --cov=app --cov-report=term-missing
