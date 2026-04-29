"""buffetAgent — 依巴菲特投資哲學評估美股的 AI agent。

Phase 2 模組:
- data_loader: 從 stockTracker CSV / 13F JSON 載入,缺則 yfinance 備援
- rules:       套用 rules.json 中的 10 條量化規則 + 4 disqualifier + 5 bonus
- screener:    對 ticker 算 0-110 分數
- kb_retriever: 從 knowledge_base/ 找相關概念與公司檔
- verdict:     合成 BUY/HOLD/WATCH/AVOID/OUT_OF_CIRCLE + rationale
- cli:         `python -m agent AAPL`

CLI 用法:
    python -m agent AAPL
    python -m agent --watchlist
    python -m agent AAPL --json
"""
__version__ = "0.2.0"
