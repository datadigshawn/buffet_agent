#!/usr/bin/env bash
# Mac mini daily production scan.
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

export BUFFET_PUBLIC_BASE_URL="${BUFFET_PUBLIC_BASE_URL:-https://buffetagent.shawny-project42.com}"
export BUFFET_YF_RETRIES="${BUFFET_YF_RETRIES:-3}"
export BUFFET_YF_BACKOFF="${BUFFET_YF_BACKOFF:-2.0}"
export SEC_USER_AGENT="${SEC_USER_AGENT:-buffetAgent contact@datadigshawn.local}"
export BUFFET_LLM_MODEL="${BUFFET_LLM_MODEL:-minimax/minimax-m2.5}"

if [[ -n "${OPENROUTER_API_KEY:-}" ]]; then
  export BUFFET_LLM_BACKEND="${BUFFET_LLM_BACKEND:-openrouter}"
else
  export BUFFET_LLM_BACKEND="${BUFFET_LLM_BACKEND:-none}"
fi

PY="${PYTHON:-venv/bin/python}"
TOP_MOVERS="${BUFFET_TOP_MOVERS:-50}"

"$PY" src/build_scan_html.py --quiet --top-movers "$TOP_MOVERS"
"$PY" scripts/notify_warroom.py

