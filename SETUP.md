# 新機器 Setup（首次 clone 後做一次）

> 從 GitHub clone 後到能跑 `python -m agent AAPL`、`./update.sh`、`python src/build_scan_html.py` 全綠的最短路徑。

---

## 1. Clone

```bash
mkdir -p ~/autobot/agent && cd ~/autobot/agent
git clone https://github.com/datadigshawn/buffet_agent.git buffetAgent
cd buffetAgent
```

## 2. Python 環境（推薦：venv，不污染系統）

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r scripts/requirements.txt
pip install yfinance pytest    # agent 與 scan 需要
```

要永久生效（之後每個 shell 自動啟用）：

```bash
echo 'cd ~/autobot/agent/buffetAgent && source venv/bin/activate' >> ~/.zshrc
```

或者用既有的 `python3` 直接裝：

```bash
python3 -m pip install --user yfinance markdown python-frontmatter pytest
```

## 3. 確認能跑

```bash
# 單元測試（不需網路）
python -m pytest agent/tests/test_rules.py -q
# 期望: 15 passed

# CLI（需網路,yfinance 抓即時資料）
python -m agent AAPL
# 期望: 看到 "Bias: HOLD" 之類完整 markdown 報告
```

## 4. （選用）連 stockTracker 的本機資料

本機 stockTracker 路徑為 `~/autobot/stockTracker/data`：

```bash
export BUFFET_STOCKTRACKER_DATA=~/autobot/stockTracker/data
python -m agent --watchlist
```

或寫進 `~/.zshrc`：

```bash
echo 'export BUFFET_STOCKTRACKER_DATA=$HOME/autobot/stockTracker/data' >> ~/.zshrc
```

**沒有 stockTracker 也沒關係** — agent 會自動退回 `config/watchlist.json` + yfinance。

## 5. （選用）git push 設定

如果你還沒在這台機器設過 git identity：

```bash
git config user.email "woodrownono@gmail.com"
git config user.name "你的名字"

# 確認 SSH key 或 PAT 已設定
gh auth status   # 用 gh CLI 最方便
# 或:ssh -T git@github.com
```

如果沒裝 `gh`：

```bash
# macOS
brew install gh

# Linux
sudo apt install gh
```

接著 `gh auth login` 跟著互動指示走。

---

## 一鍵腳本（懶得讀的版本）

把這串貼進 terminal：

```bash
mkdir -p ~/autobot/agent && cd ~/autobot/agent
git clone https://github.com/datadigshawn/buffet_agent.git buffetAgent
cd buffetAgent
python3 -m venv venv
source venv/bin/activate
pip install -r scripts/requirements.txt yfinance pytest
python -m pytest agent/tests/test_rules.py -q && \
  python -m agent AAPL && \
  echo "✅ 環境 OK,可以開始改了"
```

---

## 檢查表

- [ ] `python -m agent AAPL` 能跑出完整 markdown 報告
- [ ] `python -m pytest agent/tests/test_rules.py` 15 passed
- [ ] `git remote -v` 顯示 `https://github.com/datadigshawn/buffet_agent.git`
- [ ] `git config user.email` 顯示 woodrownono@gmail.com
- [ ] `git push` 能成功（試 push 一個小修改）

---

## 之後改完要 push

```bash
# 改完想看效果?
./update.sh             # 自動渲染 + 預覽 + commit + push

# 或手動
git add .
git commit -m "what you changed"
git push
```

Netlify 30 秒後自動部署 → 在手機看 <https://buffetagent.netlify.app>

---

## 兩台機器同步流程

| 場景 | 流程 |
|---|---|
| 在 A 機改完 → 切到 B 機 | A 機 `git push`,B 機 `git pull` 即可 |
| 兩台同時改了不同檔 | git 會自動 merge;若衝突就手動解 |
| 兩台同時改了同一行 | 後 push 的會被 reject,要先 pull/merge/rebase |

最佳實務：**先 pull 再改**。

```bash
cd ~/autobot/agent/buffetAgent
git pull         # 必做
# 開始改...
git add . && git commit -m "..." && git push
```

---

## 故障排除

### `ModuleNotFoundError: No module named 'agent'`

```bash
cd ~/autobot/agent/buffetAgent   # ⚠️ 必須在 repo 根目錄
python -m agent AAPL
```

### `yfinance` 抓不到 / 429 錯誤

換時段重試。也可改抓特定 ticker 而非全 watchlist。

### Netlify 沒重新部署

去 <https://app.netlify.com> 看 build log。多半是 push 沒成功 → `git status` 確認。

### 完整 runbook

詳見 Obsidian vault 中的 `30_Investment/Projects/buffetAgent/docs/操作手冊.md`。

---

## 相關文件

- `README.md` — 專案總覽
- `DEPLOY.md` — 原始部署選項
- Obsidian vault `30_Investment/Projects/buffetAgent/` — 完整專案文件
