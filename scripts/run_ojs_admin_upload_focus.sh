#!/usr/bin/env bash
set -euo pipefail

# Main / recommended runner: admin login -> recon -> self-learning simulate crawl,
# with multi-extension dummy upload and persistent per-target notes.
# Runnable from anywhere; resolves the repo root from this script's location.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

CRAWLER="src/normal_activity_crawler.py"

DEFAULT_START_URL="${DEFAULT_START_URL:-http://10.34.100.110:8031/index.php/publicknowledge/}"
START_URL="${1:-$DEFAULT_START_URL}"
SCOPE_PREFIX="${2:-${START_URL%/}}"
# Leave FOCUS_SEED_URLS empty to let the crawler self-learn focus paths from recon.
FOCUS_SEED_URLS="${3:-}"
OJS_USERNAME="${OJS_USERNAME:-admin}"
OJS_PASSWORD="${OJS_PASSWORD:-admin}"
SESSIONS="${SESSIONS:-5}"
MAX_STEPS="${MAX_STEPS:-25}"
FOCUS_PROB="${FOCUS_PROB:-0.8}"
LEARN="${LEARN:-1}"
RECON_STEPS="${RECON_STEPS:-40}"
NOTES_FILE="${NOTES_FILE:-datasets/.crawler_notes.json}"
RESET_NOTES="${RESET_NOTES:-0}"
ENABLE_DUMMY_UPLOAD="${ENABLE_DUMMY_UPLOAD:-1}"
SUBMIT_DUMMY_UPLOAD="${SUBMIT_DUMMY_UPLOAD:-1}"
MAX_DUMMY_UPLOADS_PER_SESSION="${MAX_DUMMY_UPLOADS_PER_SESSION:-1}"
UPLOAD_SCAN_WAIT_MS="${UPLOAD_SCAN_WAIT_MS:-800}"
DUMMY_UPLOAD_DIR="${DUMMY_UPLOAD_DIR:-datasets/.dummy_uploads}"
OUT_JSONL="${OUT_JSONL:-datasets/dataset_ojs_admin_upload_focus.jsonl}"
OUT_CSV="${OUT_CSV:-datasets/dataset_ojs_admin_upload_focus.csv}"
export OJS_PASSWORD
PYTHON_BIN="${PYTHON_BIN:-}"

if [[ -z "$PYTHON_BIN" && -x ".venv/bin/python" ]]; then
  PYTHON_BIN=".venv/bin/python"
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"

EXTRA_ARGS=()
if [[ "$ENABLE_DUMMY_UPLOAD" == "1" ]]; then
  EXTRA_ARGS+=(--enable-dummy-upload)
  EXTRA_ARGS+=(--max-dummy-uploads-per-session "$MAX_DUMMY_UPLOADS_PER_SESSION")
  EXTRA_ARGS+=(--upload-scan-wait-ms "$UPLOAD_SCAN_WAIT_MS")
  EXTRA_ARGS+=(--dummy-upload-dir "$DUMMY_UPLOAD_DIR")
fi

if [[ "$SUBMIT_DUMMY_UPLOAD" == "1" ]]; then
  EXTRA_ARGS+=(--submit-dummy-upload)
fi

if [[ "$LEARN" == "1" ]]; then
  EXTRA_ARGS+=(--learn --recon-steps "$RECON_STEPS")
else
  EXTRA_ARGS+=(--no-learn)
fi

EXTRA_ARGS+=(--notes-file "$NOTES_FILE")
if [[ "$RESET_NOTES" == "1" ]]; then
  EXTRA_ARGS+=(--reset-notes)
fi

# Only pin focus seeds if the caller explicitly provided them; otherwise self-learn.
if [[ -n "$FOCUS_SEED_URLS" ]]; then
  EXTRA_ARGS+=(--focus-seed-urls "$FOCUS_SEED_URLS" --admin-start-url "$FOCUS_SEED_URLS")
fi

"$PYTHON_BIN" "$CRAWLER" \
  --start-url "$START_URL" \
  --scope-prefix "$SCOPE_PREFIX" \
  --username "$OJS_USERNAME" \
  --password-env OJS_PASSWORD \
  --focus-admin-upload \
  --focus-prob "$FOCUS_PROB" \
  --sessions "$SESSIONS" \
  --max-steps "$MAX_STEPS" \
  --delay-min 1.5 \
  --delay-max 4.0 \
  --enable-search \
  --search-prob 0.1 \
  --out-jsonl "$OUT_JSONL" \
  --out-csv "$OUT_CSV" \
  "${EXTRA_ARGS[@]}"
