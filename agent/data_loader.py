"""讀取 ticker 基本面資料。

優先順序:
1. stockTracker latest_prices.csv 中既有 row → 用該 row
2. CSV 找不到的 ticker → yfinance 即時抓
3. CSV 缺特定欄位 (debt_equity / fcf_margin / buyback_yield) → 從 yfinance 補

附帶:
- Berkshire 13F 持股查詢 (CUSIP→ticker 映射 + 公司名 fallback)
- 結果以 dataclass 回傳,缺值統一用 None,讓 rules 那邊 graceful skip
"""
from __future__ import annotations

import csv
import json
import logging
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# stockTracker 資料路徑 — 不存在則自動 fallback 到 yfinance
# 可用 BUFFET_STOCKTRACKER_DATA 環境變數 override
STOCKTRACKER_DATA = Path(
    os.environ.get(
        "BUFFET_STOCKTRACKER_DATA",
        str(Path.home() / "Projects" / "stockTracker" / "data"),
    )
)
CSV_PATH = STOCKTRACKER_DATA / "latest_prices.csv"
F13F_PATH = STOCKTRACKER_DATA / "funds_13f.json"

# 行業黑名單(D3 能力圈外) — sector 級別
SECTOR_BLACKLIST = {
    "Crypto", "SPAC", "Biotech-Speculative", "Quantum-Computing",
    "加密貨幣", "量子電腦", "純題材生技", "SPAC公司",
}

# Industry 級別黑名單(yfinance 提供的 industry 比 sector 更精細)
INDUSTRY_BLACKLIST = {
    "Capital Markets",       # COIN, MARA(部分加密)
    "Software—Application",  # 過廣,先不加
}.symmetric_difference({"Software—Application"})  # 暫時只啟用 Capital Markets

# Ticker 級別黑名單(明確違反巴菲特原則的個股)
TICKER_BLACKLIST = {
    "TSLA",  # 巴菲特長期避開:[[能力圈]]外 + 估值極高
    "COIN",  # 加密貨幣交易所:[[能力圈]]外 + 投機性
    "MARA", "RIOT", "MSTR",  # 加密相關
    "GME", "AMC",  # 雪茄煙蒂陷阱範例
    "IONQ", "QBTS", "RGTI",  # 量子計算純題材
}

# Berkshire 已驗證的 ticker(放寬 D1/D2 — 因為 Buffett 已親自過篩)
BERKSHIRE_VERIFIED = {
    "AAPL", "AXP", "BAC", "CVX", "KO", "MCO", "OXY",
    "KHC", "V", "MA", "VRSN", "DVA", "KR", "SIRI",
    "WFC", "MMC",
}

# Berkshire 持股 CUSIP → ticker 映射(常見大型股)
CUSIP_TO_TICKER = {
    "037833100": "AAPL",   # Apple
    "025816109": "AXP",    # American Express
    "060505104": "BAC",    # Bank of America
    "166764100": "CVX",    # Chevron
    "191216100": "KO",     # Coca-Cola
    "615369105": "MCO",    # Moody's
    "674599105": "OXY",    # Occidental
    "500754106": "KHC",    # Kraft Heinz
    "92826C839": "V",      # Visa
    "57636Q104": "MA",     # Mastercard
    "92345Y106": "VRSN",   # Verisign
    "23918K108": "DVA",    # DaVita
    "501044101": "KR",     # Kroger
    "829933100": "SIRI",   # Sirius XM
    "02079K305": "GOOGL",  # Alphabet (if held)
    "H1467J104": "CHTR",   # Charter
    "530909308": "LLYVK",  # Liberty Live
}


@dataclass
class TickerData:
    """規範化後的 ticker 基本面快照。所有比率 0-1 (例 0.15 = 15%)。"""
    ticker: str
    sector: str | None = None
    price: float | None = None
    # 獲利能力
    roe: float | None = None
    gross_margin: float | None = None
    net_margin: float | None = None
    # 成長
    earn_growth: float | None = None     # EPS 5y growth (年化)
    rev_growth: float | None = None
    # 估值
    fwd_pe: float | None = None
    trail_pe: float | None = None
    peg: float | None = None
    pb: float | None = None
    # 結構
    debt_equity: float | None = None     # ⚠️CSV無,yfinance 備援
    fcf_margin: float | None = None      # ⚠️CSV無,yfinance 備援(FCF/revenue)
    buyback_yield: float | None = None   # ⚠️CSV無,yfinance 備援
    # 動能
    w52_pos: float | None = None         # 0-1 (已正規化,CSV 原為 0-100)
    # 其他
    market_cap: float | None = None
    eps_3y_negative: bool | None = None  # 連續 3 年 EPS < 0
    # 13F 資訊
    berkshire_holds: bool = False
    berkshire_value_usd: float | None = None
    berkshire_position_pct: float | None = None  # 占 Berkshire 組合 %
    other_value_investors: list[str] = field(default_factory=list)
    # 來源追蹤
    source: str = "unknown"   # "csv" / "yfinance" / "csv+yfinance"
    missing_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------- CSV 讀取 ----------

def _strip_bom(s: str) -> str:
    return s.lstrip("﻿")


def _to_float(s: str | None) -> float | None:
    if s in (None, "", "N/A"):
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def load_csv() -> dict[str, dict[str, str]]:
    """讀 latest_prices.csv 為 {ticker: row_dict}。"""
    if not CSV_PATH.exists():
        return {}
    with CSV_PATH.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        out = {}
        for row in reader:
            row = {_strip_bom(k): v for k, v in row.items()}
            out[row["ticker"]] = row
    return out


def from_csv_row(ticker: str, row: dict[str, str]) -> TickerData:
    w52 = _to_float(row.get("w52_pos"))
    if w52 is not None and w52 > 1.5:  # 是 0-100 scale
        w52 = w52 / 100.0
    return TickerData(
        ticker=ticker,
        sector=row.get("sector") or None,
        price=_to_float(row.get("price")),
        roe=_to_float(row.get("roe")),
        gross_margin=_to_float(row.get("gross_margin")),
        net_margin=_to_float(row.get("net_margin")),
        earn_growth=_to_float(row.get("earn_growth")),
        rev_growth=_to_float(row.get("rev_growth")),
        fwd_pe=_to_float(row.get("fwd_pe")),
        trail_pe=_to_float(row.get("trail_pe")),
        peg=_to_float(row.get("peg")) or _to_float(row.get("peg_ttm")),
        pb=_to_float(row.get("pb")),
        w52_pos=w52,
        market_cap=_to_float(row.get("market_cap")),
        source="csv",
        missing_fields=["debt_equity", "fcf_margin", "buyback_yield", "eps_3y_negative"],
    )


# ---------- yfinance 備援 ----------

def from_yfinance(ticker: str) -> TickerData | None:
    """yfinance 即時抓取,失敗回 None。"""
    try:
        import yfinance as yf
    except ImportError:
        log.warning("yfinance not installed, cannot fetch %s", ticker)
        return None

    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
    except Exception as e:
        log.warning("yfinance fetch failed for %s: %s", ticker, e)
        return None

    if not info or info.get("regularMarketPrice") is None:
        return None

    # FCF margin
    fcf_margin = None
    fcf = info.get("freeCashflow")
    rev = info.get("totalRevenue")
    if fcf and rev:
        fcf_margin = fcf / rev

    # 52w pos
    high = info.get("fiftyTwoWeekHigh")
    low = info.get("fiftyTwoWeekLow")
    price = info.get("regularMarketPrice") or info.get("currentPrice")
    w52_pos = None
    if high and low and price and high > low:
        w52_pos = (price - low) / (high - low)

    # EPS 連續 3 年負 (粗略檢查)
    eps_neg = None
    eps_ttm = info.get("trailingEps")
    if eps_ttm is not None:
        eps_neg = eps_ttm < 0  # 簡化:只看 TTM

    # 回購率: 用 sharesOutstanding 變化粗估
    buyback_yield = None
    shares_5y_ago = info.get("floatShares") or info.get("sharesOutstanding")
    # yfinance 沒給直接欄位,留 None;Phase 3 從 SEC 補

    return TickerData(
        ticker=ticker,
        sector=info.get("sector"),
        price=price,
        roe=info.get("returnOnEquity"),
        gross_margin=info.get("grossMargins"),
        net_margin=info.get("profitMargins"),
        earn_growth=info.get("earningsGrowth"),
        rev_growth=info.get("revenueGrowth"),
        fwd_pe=info.get("forwardPE"),
        trail_pe=info.get("trailingPE"),
        peg=info.get("trailingPegRatio"),
        pb=info.get("priceToBook"),
        debt_equity=(info.get("debtToEquity") or 0) / 100.0 or None,  # yfinance 給 % scale
        fcf_margin=fcf_margin,
        buyback_yield=buyback_yield,
        w52_pos=w52_pos,
        market_cap=info.get("marketCap"),
        eps_3y_negative=eps_neg,
        source="yfinance",
        missing_fields=[k for k, v in [
            ("buyback_yield", buyback_yield),
        ] if v is None],
    )


def merge_yfinance(td: TickerData) -> TickerData:
    """若 td 有缺欄位,從 yfinance 補。"""
    needs_fill = any(getattr(td, f) is None for f in ("debt_equity", "fcf_margin"))
    if not needs_fill:
        return td
    yf_td = from_yfinance(td.ticker)
    if yf_td is None:
        return td
    # 合併:CSV 為主,yfinance 補空
    for f in ("debt_equity", "fcf_margin", "buyback_yield", "eps_3y_negative"):
        if getattr(td, f) is None:
            setattr(td, f, getattr(yf_td, f))
    td.source = "csv+yfinance"
    td.missing_fields = [f for f in td.missing_fields if getattr(td, f) is None]
    return td


# ---------- 13F 整合 ----------

_f13f_cache: dict[str, Any] | None = None


def load_13f() -> dict[str, Any]:
    global _f13f_cache
    if _f13f_cache is not None:
        return _f13f_cache
    if not F13F_PATH.exists():
        _f13f_cache = {"funds": []}
        return _f13f_cache
    with F13F_PATH.open(encoding="utf-8") as f:
        _f13f_cache = json.load(f)
    return _f13f_cache


def annotate_13f(td: TickerData) -> TickerData:
    """補 berkshire_holds / berkshire_value / other_value_investors。"""
    data = load_13f()
    funds = data.get("funds", [])

    # CUSIP 反查
    target_cusip = None
    for cusip, ticker in CUSIP_TO_TICKER.items():
        if ticker == td.ticker:
            target_cusip = cusip
            break

    for fund in funds:
        is_berkshire = "berkshire" in fund.get("name", "").lower()
        for holding in fund.get("top_holdings", []):
            cusip = holding.get("cusip")
            name = holding.get("name", "")
            # 比對:CUSIP 直接 OR 名稱包含 ticker(粗略)
            match = (target_cusip and cusip == target_cusip) or (
                td.ticker.upper() in name.upper().split()
            )
            if not match:
                continue
            if is_berkshire:
                td.berkshire_holds = True
                td.berkshire_value_usd = holding.get("value")
                total = fund.get("total_value", 1)
                td.berkshire_position_pct = (holding.get("value") or 0) / total
            else:
                td.other_value_investors.append(fund["name"])
            break
    return td


# ---------- 主入口 ----------

def load_ticker(ticker: str) -> TickerData:
    """主入口:回傳完整的 TickerData。"""
    ticker = ticker.upper().strip()
    csv_data = load_csv()
    if ticker in csv_data:
        td = from_csv_row(ticker, csv_data[ticker])
    else:
        td = from_yfinance(ticker) or TickerData(ticker=ticker, source="empty")
    td = merge_yfinance(td)
    td = annotate_13f(td)
    return td


def watchlist() -> list[str]:
    """回傳 stockTracker CSV 中所有 ticker。"""
    return sorted(load_csv().keys())
