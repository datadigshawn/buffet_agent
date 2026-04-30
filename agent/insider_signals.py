"""Insider 交易訊號 (Phase 5 P1-3.5)。

資料源:
- yfinance.Ticker.insider_transactions:近期 Form 4 結構化資料
- agent/sources/sec_filings.py:13D/13G/8-K 計數補充

Buffett 風格訊號:
- CEO/CFO 大量賣出 (>$5M / 60 天) → 警訊 (insider_selling_spike)
- 任何內部人買入 → 正面訊號 (insider_buying_signal,稀有)
- 13D 出現 → 5%+ 活躍機構介入 (positive 或 negative,看內容)

不做的事:
- 不解析 Form 4 XML (yfinance 已給)
- 不判斷 13D 是否友善 (需讀文字)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Any

log = logging.getLogger(__name__)

# Threshold:CEO/CFO/President 等 C-level 賣出超過 $X 算 spike
EXEC_SELL_THRESHOLD_USD = 5_000_000     # $5M / 60d
TOTAL_SELL_THRESHOLD_USD = 20_000_000   # $20M / 60d (任何 insider)
LOOKBACK_DAYS = 60

EXEC_TITLES = (
    "chief executive", "ceo",
    "chief financial", "cfo",
    "chief operating", "coo",
    "president",
)


@dataclass
class InsiderSignals:
    """Insider 交易訊號彙整。"""
    ticker: str
    lookback_days: int = LOOKBACK_DAYS
    transactions_count: int = 0
    total_sell_value: float = 0.0
    total_buy_value: float = 0.0
    net_value: float = 0.0                 # buy - sell
    exec_sell_value: float = 0.0           # 只算 C-level sells
    exec_buy_value: float = 0.0
    top_seller: dict | None = None         # {name, position, value}
    top_buyer: dict | None = None
    alert_type: str | None = None          # insider_selling_spike / insider_buying_signal
    # SEC 13D/13G/8-K 計數 (補充)
    sched_13d_count: int = 0
    sched_13g_count: int = 0
    form_8k_count: int = 0
    recent_13d_dates: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_exec(position: str | None) -> bool:
    if not position:
        return False
    p = position.lower()
    return any(t in p for t in EXEC_TITLES)


def _is_sell(text: str | None) -> bool:
    if not text:
        return False
    t = text.lower()
    return "sale" in t or "sold" in t or "sell" in t


def _is_buy(text: str | None) -> bool:
    if not text:
        return False
    t = text.lower()
    return "purchase" in t or "bought" in t or "buy" in t


def fetch_insider_transactions(ticker: str, lookback_days: int = LOOKBACK_DAYS) -> list[dict]:
    """從 yfinance 抓 insider_transactions。回傳 list of dict。"""
    try:
        import yfinance as yf
    except ImportError:
        return []
    try:
        df = yf.Ticker(ticker).insider_transactions
    except Exception as e:  # noqa: BLE001
        log.debug("yfinance insider_transactions failed for %s: %s", ticker, e)
        return []
    if df is None or df.empty:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    out: list[dict] = []
    for _, row in df.iterrows():
        date = row.get("Start Date")
        if hasattr(date, "to_pydatetime"):
            dt = date.to_pydatetime()
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt < cutoff:
                continue
            date_str = dt.strftime("%Y-%m-%d")
        else:
            date_str = str(date)[:10] if date else ""

        value = row.get("Value")
        try:
            value = float(value) if value == value else None  # NaN check
        except (TypeError, ValueError):
            value = None

        out.append({
            "date": date_str,
            "insider": row.get("Insider"),
            "position": row.get("Position"),
            "shares": int(row.get("Shares") or 0),
            "value": value,
            "text": row.get("Text") or "",
        })
    return out


def evaluate(ticker: str, lookback_days: int = LOOKBACK_DAYS) -> InsiderSignals:
    """主入口:組裝 InsiderSignals。"""
    sig = InsiderSignals(ticker=ticker.upper(), lookback_days=lookback_days)
    txs = fetch_insider_transactions(ticker, lookback_days)
    sig.transactions_count = len(txs)

    top_seller_val = 0.0
    top_buyer_val = 0.0
    for tx in txs:
        val = tx.get("value")
        if val is None:
            continue
        is_exec = _is_exec(tx.get("position"))
        if _is_sell(tx.get("text")):
            sig.total_sell_value += val
            if is_exec:
                sig.exec_sell_value += val
            if val > top_seller_val:
                top_seller_val = val
                sig.top_seller = {
                    "name": tx.get("insider"),
                    "position": tx.get("position"),
                    "value": val,
                    "date": tx.get("date"),
                }
        elif _is_buy(tx.get("text")):
            sig.total_buy_value += val
            if is_exec:
                sig.exec_buy_value += val
            if val > top_buyer_val:
                top_buyer_val = val
                sig.top_buyer = {
                    "name": tx.get("insider"),
                    "position": tx.get("position"),
                    "value": val,
                    "date": tx.get("date"),
                }

    sig.net_value = sig.total_buy_value - sig.total_sell_value

    # 13D/13G/8-K 計數補充 (從 SEC submissions)
    try:
        from .sources import sec_filings
        fc = sec_filings.filing_counts(ticker, days=lookback_days)
        if fc:
            sig.sched_13d_count = fc.sched_13d_count
            sig.sched_13g_count = fc.sched_13g_count
            sig.form_8k_count = fc.form_8k_count
            sig.recent_13d_dates = list(fc.recent_13d_dates)
    except Exception:  # noqa: BLE001
        pass

    # Alert 判定
    if (sig.exec_sell_value >= EXEC_SELL_THRESHOLD_USD
            or sig.total_sell_value >= TOTAL_SELL_THRESHOLD_USD):
        sig.alert_type = "insider_selling_spike"
    elif sig.total_buy_value > 0 and sig.total_buy_value > sig.total_sell_value:
        # 內部人 net buy 是稀有正面訊號
        sig.alert_type = "insider_buying_signal"
    elif sig.sched_13d_count >= 1:
        # 出現 13D = 活躍機構 5%+ 介入
        sig.alert_type = "activist_filing"

    return sig
