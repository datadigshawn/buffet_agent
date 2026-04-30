"""insider_signals + sec_filings 測試 (P1-3.5)。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from agent import insider_signals
from agent.sources import sec_filings


# ---------- 內部工具 ----------

def test_is_exec():
    assert insider_signals._is_exec("Chief Executive Officer") is True
    assert insider_signals._is_exec("Chief Financial Officer") is True
    assert insider_signals._is_exec("President") is True
    assert insider_signals._is_exec("Officer") is False
    assert insider_signals._is_exec("Director") is False
    assert insider_signals._is_exec(None) is False


def test_is_sell_buy():
    assert insider_signals._is_sell("Sale at price 100") is True
    assert insider_signals._is_sell("Sold under 10b5-1") is True
    assert insider_signals._is_buy("Open market purchase") is True
    assert insider_signals._is_buy("Bought back") is True
    assert insider_signals._is_sell("") is False


# ---------- evaluate ----------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _tx(insider: str, position: str, value: float | None, days_ago: int,
        action: str = "Sale at price 100") -> dict:
    return {
        "date": (_now() - timedelta(days=days_ago)).strftime("%Y-%m-%d"),
        "insider": insider,
        "position": position,
        "shares": 1000,
        "value": value,
        "text": action,
    }


def test_evaluate_no_transactions(monkeypatch):
    monkeypatch.setattr(insider_signals, "fetch_insider_transactions", lambda *a, **kw: [])
    sig = insider_signals.evaluate("X")
    assert sig.transactions_count == 0
    assert sig.alert_type is None


def test_evaluate_exec_sell_spike(monkeypatch):
    """CEO 賣 $6M → exec_sell_value > $5M threshold → spike alert。"""
    txs = [_tx("Tim Cook", "Chief Executive Officer", 6_000_000, days_ago=10)]
    monkeypatch.setattr(insider_signals, "fetch_insider_transactions", lambda *a, **kw: txs)
    monkeypatch.setattr(
        sec_filings, "filing_counts",
        lambda t, days=60: None,
    )
    sig = insider_signals.evaluate("X")
    assert sig.alert_type == "insider_selling_spike"
    assert sig.exec_sell_value == 6_000_000
    assert sig.top_seller["name"] == "Tim Cook"


def test_evaluate_total_sell_spike_no_exec(monkeypatch):
    """Officer 賣 $25M → total_sell > $20M → 仍應 spike。"""
    txs = [
        _tx("Officer A", "Officer", 15_000_000, days_ago=5),
        _tx("Officer B", "Director", 10_000_000, days_ago=15),
    ]
    monkeypatch.setattr(insider_signals, "fetch_insider_transactions", lambda *a, **kw: txs)
    monkeypatch.setattr(sec_filings, "filing_counts", lambda t, days=60: None)
    sig = insider_signals.evaluate("X")
    assert sig.alert_type == "insider_selling_spike"
    assert sig.total_sell_value == 25_000_000
    assert sig.exec_sell_value == 0   # 不是 exec


def test_evaluate_buy_signal(monkeypatch):
    """買入 > 賣出 → insider_buying_signal。"""
    txs = [
        _tx("CEO", "Chief Executive Officer", 1_000_000, days_ago=10,
            action="Open market purchase"),
        _tx("CFO", "CFO", 500_000, days_ago=20,
            action="Bought back shares"),
    ]
    monkeypatch.setattr(insider_signals, "fetch_insider_transactions", lambda *a, **kw: txs)
    monkeypatch.setattr(sec_filings, "filing_counts", lambda t, days=60: None)
    sig = insider_signals.evaluate("X")
    assert sig.alert_type == "insider_buying_signal"
    assert sig.total_buy_value == 1_500_000
    assert sig.top_buyer["name"] == "CEO"


def test_evaluate_activist_filing(monkeypatch):
    """無顯著買賣但有 13D → activist_filing alert。"""
    monkeypatch.setattr(insider_signals, "fetch_insider_transactions",
                        lambda *a, **kw: [_tx("X", "Officer", 100_000, days_ago=5)])

    fc = sec_filings.FilingCounts(cik="0", days=60, sched_13d_count=1,
                                   recent_13d_dates=["2026-04-15"])
    monkeypatch.setattr(sec_filings, "filing_counts", lambda t, days=60: fc)
    sig = insider_signals.evaluate("X")
    assert sig.alert_type == "activist_filing"
    assert sig.sched_13d_count == 1


def test_evaluate_quiet_no_alert(monkeypatch):
    """賣 $1M (低於門檻) + 無 13D → 不 alert。"""
    txs = [_tx("Officer", "Officer", 1_000_000, days_ago=5)]
    monkeypatch.setattr(insider_signals, "fetch_insider_transactions", lambda *a, **kw: txs)
    monkeypatch.setattr(sec_filings, "filing_counts", lambda t, days=60: None)
    sig = insider_signals.evaluate("X")
    assert sig.alert_type is None


# ---------- diff.insider_alerts_from_verdicts ----------

def test_insider_alerts_dispatch():
    from agent import diff
    verdicts = [
        {"ticker": "X", "insider": {
            "alert_type": "insider_selling_spike",
            "total_sell_value": 25_000_000,
            "exec_sell_value": 6_000_000,
        }},
        {"ticker": "Y", "insider": {
            "alert_type": "insider_buying_signal",
            "total_buy_value": 1_500_000,
        }},
        {"ticker": "Z", "insider": {
            "alert_type": "activist_filing",
            "sched_13d_count": 2,
        }},
        {"ticker": "Q", "insider": None},   # 無資料
    ]
    alerts = diff.insider_alerts_from_verdicts(verdicts)
    types = {a.ticker: a.severity for a in alerts}
    assert types["X"] == "high"      # selling_spike
    assert types["Y"] == "medium"    # buying_signal
    assert types["Z"] == "high"      # activist
    assert "Q" not in types


def test_insider_alerts_skips_when_no_alert_type():
    from agent import diff
    verdicts = [
        {"ticker": "X", "insider": {
            "alert_type": None,
            "total_sell_value": 1_000_000,
        }},
    ]
    assert diff.insider_alerts_from_verdicts(verdicts) == []
