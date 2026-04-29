# 📚 buffet_agent

巴菲特股東信知識庫 + AI agent，依其投資哲學評估美股，並產出每週自動更新的排行榜。

| 用途 | 連結 |
|---|---|
| 📚 知識庫首頁（180 節點） | <https://buffetagent.netlify.app/> |
| 📊 Buffett Scan 排行榜 | <https://buffetagent.netlify.app/scan.html> |
| 🍎 個股範例 | <https://buffetagent.netlify.app/scan/AAPL.html> |

---

## 🎯 這個專案做什麼

1. **知識庫**：從巴菲特 1957–2024 共 68 年股東信整理出 180 個節點（35 概念 + 62 公司 + 7 人物 + 64 信件 + 7 索引）
2. **AI agent**：把巴菲特投資邏輯萃取成 10 條量化規則 + 5 項定性檢查
3. **自動掃描**：每週一收盤後跑 watchlist，產出 BUY / HOLD / WATCH / AVOID / OUT_OF_CIRCLE 評分
4. **手機友善**：PWA + 抽屜式導覽，加到主畫面就能隨時查

---

## 📂 目錄結構

```
buffet_agent/
├── README.md                       本文件
├── DEPLOY.md                       原始部署選項說明
├── netlify.toml                    Netlify 部署設定
├── update.sh                       一鍵渲染 + 預覽 + push
│
├── content/                        📝 Obsidian markdown source（180 個 .md）
│   ├── 00-索引/                    MOC × 7
│   ├── 01-信件/                    巴菲特股東信 × 64
│   ├── 02-投資概念/                投資概念 × 35（含 Phase 1 三篇）
│   ├── 03-公司檔案/                公司檔 × 62
│   ├── 04-人物檔案/                人物檔 × 7
│   ├── 05-模板/                    Obsidian templates
│   └── index.md                    首頁 markdown source
│
├── simple-html/                    🌐 Netlify publish 目錄（render.py 產出）
│   ├── index.html                  首頁含 📊 Buffett Scan 入口
│   ├── manifest.webmanifest        PWA manifest
│   ├── icon.svg                    巴菲特紅 + 「巴」字 logo
│   ├── 00-索引/ … 04-人物檔案/    180 個渲染後 HTML
│   ├── scan.html                   ⭐ Buffett Scan 排行榜
│   └── scan/                       ⭐ 83 個個股明細頁
│
├── agent/                          🤖 BuffettAgent Python module（Phase 2）
│   ├── __init__.py / __main__.py
│   ├── rules.json                  10 規則 + 4 disqualifier + 5 bonus
│   ├── data_loader.py              CSV + 13F + yfinance 三段式
│   ├── rules.py                    載入 + 套規則
│   ├── screener.py                 5 步驟評分流程
│   ├── kb_retriever.py             KB 公司/概念檔查找
│   ├── verdict.py                  合成 + markdown rationale
│   ├── cli.py                      python -m agent <TICKER>
│   └── tests/                      15 unit + 10 e2e (pytest)
│
├── config/
│   └── watchlist.json              📋 83 ticker 分 6 群（Buffett 核心 + 你的 stockTracker）
│
├── src/
│   └── build_scan_html.py          🔨 主腳本：watchlist → agent → static HTML
│
├── scripts/                        🛠️ 知識庫渲染相關
│   ├── render.py                   content/*.md → simple-html/*.html（含 wikilink 解析）
│   ├── inject_pwa.py               一次性注入 PWA tags
│   ├── inject_mobile_nav.py        一次性注入手機抽屜
│   └── requirements.txt            markdown / python-frontmatter
│
├── quartz-config-files/            （備用）若日後改用 Quartz 部署
│
└── .github/workflows/
    └── buffet-scan.yml             📅 每週一 22:00 UTC + 手動觸發
```

---

## 🚀 用法（依使用情境）

### A. 我想看股票評分（最常用）

```
打開 https://buffetagent.netlify.app/scan
```

或在手機加到主畫面變 PWA。

### B. 我想看任一 ticker（含 watchlist 外）

```bash
cd ~/Projects/agentS/buffet_agent
/Users/apple/miniforge3/bin/python3 -m agent SMCI       # 範例
/Users/apple/miniforge3/bin/python3 -m agent AAPL --json
/Users/apple/miniforge3/bin/python3 -m agent --watchlist
```

### C. 我加新 ticker 到 watchlist

編 `config/watchlist.json` → push → 下週一 cron 自動含進去。  
要立刻看：

```bash
/Users/apple/miniforge3/bin/python3 src/build_scan_html.py
git add simple-html/scan* && git commit -m "manual rescan" && git push
```

或在 GitHub Actions 網頁手動 dispatch。

### D. 我修改規則

1. 編 `agent/rules.json`
2. 同步 `content/02-投資概念/巴菲特量化篩選清單.md` 表格
3. `python -m pytest agent/tests/ -v` 確認沒退化
4. 重渲染 + push

完整流程：見 [Obsidian vault `docs/操作手冊.md`](../../Library/Mobile%20Documents/iCloud~md~obsidian/Documents/2nd%20brain/30_Investment/Projects/buffetAgent/docs/操作手冊.md)

---

## 🤖 Agent 怎麼運作

```
你問 → ticker → data_loader (CSV / yfinance / 13F)
                    ↓
                 TickerData (基本面 + 持股 + 缺值標記)
                    ↓
              ┌─────┴──────┐
              ↓            ↓
       hard disqualifier  core rules (10) + bonuses (5)
              │            │
              ↓            ↓
       任一觸發?    base 0-100 + bonus 0-18
              │            │
              ↓            ↓
       OUT_OF_CIRCLE   bias 對映
                       (≥80 BUY / ≥60 HOLD / ≥40 WATCH / <40 AVOID)
                            ↓
                       kb_retriever (引用 KB 概念 + 公司檔)
                            ↓
                       Verdict (markdown rationale + JSON)
```

10 條核心規則 + 4 條 disqualifier 詳見 `agent/rules.json` 與 [`巴菲特量化篩選清單`](https://buffetagent.netlify.app/02-%E6%8A%95%E8%B3%87%E6%A6%82%E5%BF%B5/%E5%B7%B4%E8%8F%B2%E7%89%B9%E9%87%8F%E5%8C%96%E7%AF%A9%E9%81%B8%E6%B8%85%E5%96%AE.html)。

---

## 📅 自動化排程

```yaml
# .github/workflows/buffet-scan.yml
on:
  schedule:
    - cron: '0 22 * * 1'    # 週一 22:00 UTC = 美東 17/18:00（收盤後）
  workflow_dispatch:         # 也可手動
```

每次跑：
1. `pip install yfinance markdown python-frontmatter`
2. `python src/build_scan_html.py`
3. `git add simple-html/scan*` + commit + push
4. Netlify 自動重新部署

---

## ✅ 驗證

```bash
# 單元測試（純邏輯）
/Users/apple/miniforge3/bin/python3 -m pytest agent/tests/test_rules.py -v

# Ground truth 端對端（5 重倉股 + 5 反例）
/Users/apple/miniforge3/bin/python3 -m pytest agent/tests/test_screener_e2e.py -v -m e2e
```

期望：**25 PASSED**。

---

## 🛣️ Roadmap

| 階段 | 狀態 |
|---|---|
| Phase 0：知識庫 + Netlify + PWA | ✅ |
| Phase 1：交易邏輯總綱 + 量化篩選清單 + 定性檢查清單 | ✅ |
| Phase 2：BuffettAgent module + CLI + tests | ✅ |
| Option C：自動化 weekly scan | ✅ |
| Phase 3：接入 [warRoom 戰情室](../warRoom_shawnY06) | ⬜ |

詳見 Obsidian vault：`30_Investment/Projects/buffetAgent/_changelog/CHANGELOG.md`。

---

## 🔧 環境

- Python 3.12+（用 `/Users/apple/miniforge3/bin/python3`，**不**用系統 `python3`）
- 套件：`yfinance markdown python-frontmatter pytest`
- 部署：Netlify（auto-deploy on push）
- CI：GitHub Actions

---

## 📖 相關專案

- [stockTracker](../stockTracker/) — 提供基本面資料與 13F 持股
- [stockAnalysis_bot_MultiAgent](../stockAnalysis_bot_MultiAgent/) — SEC XBRL + 多代理分析
- [warRoom_shawnY06](../warRoom_shawnY06/) — Phase 3 整合目標
- Obsidian vault: `30_Investment/Projects/buffetAgent/` — 完整專案文件 + devlog + TODO

---

## ⚖️ 授權

知識庫內容為個人重新組織與彙整的研究筆記，**沒有逐字翻譯任何信件原文**。  
原信件請見 [berkshirehathaway.com](https://www.berkshirehathaway.com/letters/letters.html)。

agent 程式碼：MIT。
