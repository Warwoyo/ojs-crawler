#!/usr/bin/env bash
set -euo pipefail

# Public (unauthenticated) normal-browsing runner.
# Runnable from anywhere; resolves the repo root from this script's location.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

CRAWLER="src/normal_activity_crawler.py"

START_URL="${1:-http://10.34.100.102:8033/index.php/javd-journal}"
SCOPE_PREFIX="${2:-$START_URL}"
OUT_JSONL="${OUT_JSONL:-datasets/dataset_ojs_normal.jsonl}"
OUT_CSV="${OUT_CSV:-datasets/dataset_ojs_normal.csv}"
PYTHON_BIN="${PYTHON_BIN:-}"

if [[ -z "$PYTHON_BIN" && -x ".venv/bin/python" ]]; then
  PYTHON_BIN=".venv/bin/python"
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"

"$PYTHON_BIN" "$CRAWLER" \
  --start-url "$START_URL" \
  --scope-prefix "$SCOPE_PREFIX" \
  --sessions 20 \
  --max-steps 30 \
  --delay-min 1.5 \
  --delay-max 4.0 \
  --enable-search \
  --search-terms "cybersecurity,ojs,security,article,journal" \
  --out-jsonl "$OUT_JSONL" \
  --out-csv "$OUT_CSV"
