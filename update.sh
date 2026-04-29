#!/usr/bin/env bash
# 一鍵更新流程:從 content/ 重新渲染 → 預覽 → push 觸發 Netlify 重新部署
set -euo pipefail

cd "$(dirname "$0")"

echo "📦 [1/4] 確保 Python 依賴..."
/Users/apple/miniforge3/bin/python3 -m pip install -q -r scripts/requirements.txt

echo "🔨 [2/4] 渲染 content/ → simple-html/ ..."
/Users/apple/miniforge3/bin/python3 scripts/render.py --clean

echo "🔍 [3/4] 預覽於 http://localhost:8765 (Ctrl+C 結束預覽)..."
echo "       新分頁打開 http://localhost:8765 確認後, 按 Enter 繼續推送..."
/Users/apple/miniforge3/bin/python3 -m http.server -d simple-html 8765 &
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
