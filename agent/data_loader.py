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
import random
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from . import cache as _cache

log = logging.getLogger(__name__)

# yfinance retry/backoff 參數 (環境可調)
YF_MAX_RETRIES = int(os.environ.get("BUFFET_YF_RETRIES", "3"))
YF_BACKOFF_BASE = float(os.environ.get("BUFFET_YF_BACKOFF", "2.0"))

# stockTracker 資料路徑 — 不存在則自動 fallback 到 yfinance
# 可用 BUFFET_STOCKTRACKER_DATA 環境變數 override
def _default_stocktracker_data() -> Path:
    """偵測本機 stockTracker data 目錄。

    優先序:
      1. ~/autobot/stockTracker/data (本機 autobot 佈局)
      2. ~/Projects/stockTracker/data (legacy 佈局)
    都不存在就回前者,後續 .exists() 會判 false → fallback 到 yfinance。
    """
    candidates = [
        Path.home() / "autobot" / "stockTracker" / "data",
        Path.home() / "Projects" / "stockTracker" / "data",
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


STOCKTRACKER_DATA = Path(
    os.environ.get(
        "BUFFET_STOCKTRACKER_DATA",
        str(_default_stocktracker_data()),
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


def classify_industry(sector: str | None, industry: str | None) -> str:
    """從 sector / industry 字串映射到 buffetAgent 內部分類。

    回傳:"bank" / "insurance" / "utility" / "reit" / "general"

    這個分類驅動 industry_overrides + industry_specific_rules —
    Buffett 對銀行/保險/公用事業/REITs 有不同預期。
    """
    sec_l = (sector or "").lower()
    ind_l = (industry or "").lower()
    if "insurance" in ind_l or "insurance" in sec_l or "保險" in sec_l:
        return "insurance"
    if "bank" in ind_l or ("financial" in sec_l and "bank" in ind_l):
        return "bank"
    # 純 industry 是 banks 的也算
    if ind_l in ("banks—diversified", "banks—regional", "banks - diversified",
                 "banks - regional", "banks"):
        return "bank"
    if "utilit" in sec_l or "utilit" in ind_l or "公用" in sec_l:
        return "utility"
    # P2-3: REITs (sector=Real Estate 或 industry 含 reit)
    if "reit" in ind_l or "real estate" in sec_l or "不動產" in sec_l:
        return "reit"
    return "general"


@dataclass
class TickerData:
    """規範化後的 ticker 基本面快照。所有比率 0-1 (例 0.15 = 15%)。"""
    ticker: str
    sector: str | None = None
    industry: str | None = None
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
    # SEC 持續性 / bonus 額外欄位 (B1 萃取,B2 注入)
    roe_consistency_10y: float | None = None   # 過去 10 年 ROE > 15% 比例 (0-1)
    roic_5y_avg: float | None = None           # B2 用
    div_growth_streak: int = 0                  # B5 用
    sec_years_available: int = 0                # SEC 10-K 年數
    # 行業分類 (B4 + P2-3) — 驅動 industry_overrides + industry_specific_rules
    industry_class: str = "general"             # general / bank / insurance / utility / reit
    # P2-3 行業特化指標
    bank_roa: float | None = None               # bank: NI/Assets latest
    efficiency_ratio: float | None = None       # bank: 1 - NetMargin 5y avg
    capex_dep_ratio: float | None = None        # utility: Capex/Depreciation 5y avg
    ffo_margin: float | None = None             # reit: (NI + Dep) / Revenue 5y avg
    # 來源追蹤
    source: str = "unknown"   # "csv" / "yfinance" / "csv+yfinance" / "+sec"
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
        industry=row.get("industry") or None,
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

def _fetch_yf_info(ticker: str) -> dict | None:
    """打 yfinance .info,含 retry/backoff + 當日 cache。"""
    cached = _cache.get("yf_info", ticker)
    if cached is not None:
        return cached if cached else None  # 空 dict = 之前抓失敗,當日不重試

    try:
        import yfinance as yf
    except ImportError:
        log.warning("yfinance not installed, cannot fetch %s", ticker)
        return None

    last_err: Exception | None = None
    for attempt in range(YF_MAX_RETRIES):
        try:
            t = yf.Ticker(ticker)
            info = t.info or {}
            if info and info.get("regularMarketPrice") is not None:
                _cache.set_("yf_info", ticker, info)
                return info
            # 抓到但無價格,可能 ticker 已下市,當日記空避免重打
            _cache.set_("yf_info", ticker, {})
            return None
        except Exception as e:
            last_err = e
            if attempt < YF_MAX_RETRIES - 1:
                wait = YF_BACKOFF_BASE ** attempt + random.uniform(0, 0.5)
                log.warning("yfinance %s attempt %d failed (%s); retry in %.1fs",
                            ticker, attempt + 1, e, wait)
                time.sleep(wait)
    log.warning("yfinance fetch failed for %s after %d retries: %s",
                ticker, YF_MAX_RETRIES, last_err)
    _cache.set_("yf_info", ticker, {})
    return None


def from_yfinance(ticker: str) -> TickerData | None:
    """yfinance 即時抓取,失敗回 None。"""
    info = _fetch_yf_info(ticker)
    if not info:
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
        industry=info.get("industry"),
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


# ---------- SEC 整合 ----------

# 環境變數 BUFFET_DISABLE_SEC=1 可關閉 SEC 整合 (例如本機測試 / 速度考量)
SEC_ENABLED = os.environ.get("BUFFET_DISABLE_SEC", "0") != "1"


def merge_sec(td: TickerData) -> TickerData:
    """把 SEC 真實指標填入 TickerData。

    優先序: SEC 有值 → 覆寫 yfinance/CSV 的對應欄位 (因為更精確)
            SEC 無值 → 維持原值 (yfinance fallback)

    覆寫的欄位:
      - debt_equity:      SEC LongTermDebt/Equity (純長期負債,符合 Buffett 定義)
      - fcf_margin:       SEC 5 年平均 owner earnings margin
      - buyback_yield:    SEC SharesOutstanding YoY 縮減率
      - eps_3y_negative:  SEC NetIncome 近 3 年都 < 0
    新增欄位:
      - roe_consistency_10y / roic_5y_avg / div_growth_streak / sec_years_available
    """
    if not SEC_ENABLED:
        return td
    try:
        from . import sec_metrics
    except ImportError:
        return td

    metrics = sec_metrics.extract_buffett_metrics(td.ticker)
    if metrics["source"] == "no_data":
        return td

    # 覆寫核心欄位 (SEC 更精確)
    if metrics["long_term_de"] is not None:
        td.debt_equity = metrics["long_term_de"]
    if metrics["owner_earnings_5y"] is not None:
        td.fcf_margin = metrics["owner_earnings_5y"]
    if metrics["buyback_yield"] is not None:
        td.buyback_yield = metrics["buyback_yield"]

    # eps_3y_negative: 從 SEC 近 3 年 NetIncome 算 (取代 yfinance 單期 trailingEps)
    eps_neg = _sec_3y_eps_negative(td.ticker)
    if eps_neg is not None:
        td.eps_3y_negative = eps_neg

    # 新增欄位 (持續性、bonus 用)
    td.roe_consistency_10y = metrics["roe_consistency_10y"]
    td.roic_5y_avg = metrics["roic_5y_avg"]
    td.div_growth_streak = metrics["div_growth_streak"] or 0
    td.sec_years_available = metrics["years_available"]

    # source 標記
    td.source = (td.source + "+sec") if td.source not in ("unknown", "empty") else "sec"
    # missing_fields 動態更新
    td.missing_fields = [
        f for f in td.missing_fields
        if getattr(td, f) is None
    ]
    return td


def _sec_3y_eps_negative(ticker: str) -> bool | None:
    """檢查 SEC 近 3 年 NetIncome 是否都 < 0 (D4 用)。沒資料回 None。"""
    try:
        from . import sec_metrics
        from .sources import sec as sec_api
    except ImportError:
        return None
    facts = sec_api.get_facts(ticker)
    if not facts:
        return None
    series = sec_metrics._annual_series(facts, "NetIncome")
    if len(series) < 3:
        return None
    last_3 = series[-3:]
    return all(v < 0 for _, v in last_3)


# ---------- 主入口 ----------

def load_ticker(ticker: str) -> TickerData:
    """主入口:回傳完整的 TickerData。

    管線:
      1. CSV (stockTracker) 或 yfinance 拿基本面
      2. yfinance 補 CSV 沒抓的欄位 (debt_equity, fcf_margin)
      3. SEC EDGAR 覆寫 R5/R6/R7 真實值 + 加上持續性指標
      4. 13F 補 Berkshire / 其他價值投資人持股
    """
    ticker = ticker.upper().strip()
    csv_data = load_csv()
    if ticker in csv_data:
        td = from_csv_row(ticker, csv_data[ticker])
    else:
        td = from_yfinance(ticker) or TickerData(ticker=ticker, source="empty")
    td = merge_yfinance(td)
    td = merge_sec(td)
    td = annotate_13f(td)
    # B4: 行業分類 (依 sector + industry 字串)
    td.industry_class = classify_industry(td.sector, td.industry)
    # P2-3: 行業專屬指標
    if SEC_ENABLED and td.industry_class in ("bank", "utility", "reit"):
        try:
            from . import industry_metrics
            ind_m = industry_metrics.evaluate(ticker, td.industry_class)
            td.bank_roa = ind_m.bank_roa
            td.efficiency_ratio = ind_m.efficiency_ratio
            td.capex_dep_ratio = ind_m.capex_dep_ratio
            td.ffo_margin = ind_m.ffo_margin
        except Exception:  # noqa: BLE001
            pass
    return td


def watchlist() -> list[str]:
    """回傳 stockTracker CSV 中所有 ticker。"""
    return sorted(load_csv().keys())
