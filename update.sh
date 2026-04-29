#!/usr/bin/env bash
# 一鍵更新流程:從 content/ 重新渲染 → 預覽 → push 觸發 Netlify 重新部署
#
# Python 解析優先序:
#   1. $PYTHON 環境變數 (例 PYTHON=/path/to/python ./update.sh)
#   2. ./venv/bin/python
#   3. /Users/apple/miniforge3/bin/python3 (此 Mac 預設)
#   4. python3 from PATH
set -euo pipefail

cd "$(dirname "$0")"

# ---------- pick python ----------
if [[ -n "${PYTHON:-}" ]] && [[ -x "$PYTHON" ]]; then
  PY="$PYTHON"
elif [[ -x "./venv/bin/python" ]]; then
  PY="./venv/bin/python"
elif [[ -x "/Users/apple/miniforge3/bin/python3" ]]; then
  PY="/Users/apple/miniforge3/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
  PY="$(command -v python3)"
else
  echo "❌ 找不到可用的 python3" >&2
  exit 1
fi
echo "🐍 using $PY"

echo "📦 [1/4] 確保 Python 依賴..."
"$PY" -m pip install -q -r scripts/requirements.txt

echo "🔨 [2/4] 渲染 content/ → simple-html/ ..."
"$PY" scripts/render.py --clean

echo "🔍 [3/4] 預覽於 http://localhost:8765 (Ctrl+C 結束預覽)..."
echo "       新分頁打開 http://localhost:8765 確認後, 按 Enter 繼續推送..."
"$PY" -m http.server -d simple-html 8765 &
SERVER_PID=$!
trap "kill $SERVER_PID 2>/dev/null || true" EXIT
read -r _

kill $SERVER_PID 2>/dev/null || true

echo "🚀 [4/4] commit + push..."
git add simple-html/ scripts/ update.sh
if git diff --cached --quiet; then
  echo "(no changes — nothing to commit)"
else
  git commit -m "Update site: regenerate from content/"
  git push
  echo "✅ 已推送, Netlify 會在 ~30 秒後完成重新部署"
fi
