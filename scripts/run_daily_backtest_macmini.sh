#!/usr/bin/env bash
# Mac mini daily Buffett backtest.
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

export BUFFET_PUBLIC_BASE_URL="${BUFFET_PUBLIC_BASE_URL:-https://buffetagent.shawny-project42.com}"

PY="${PYTHON:-venv/bin/python}"
TOP_N="${BUFFET_BACKTEST_TOP_N:-10}"
HORIZONS="${BUFFET_BACKTEST_HORIZONS:-30,90,180}"

"$PY" scripts/run_backtest.py --quiet --top-n "$TOP_N" --horizons "$HORIZONS"

