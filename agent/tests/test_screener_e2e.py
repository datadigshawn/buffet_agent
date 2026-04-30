"""5+5 ground-truth ticker 端對端回測。

需要網路 (yfinance) — 用 pytest -m e2e 標記避免 CI 必跑。
"""
from __future__ import annotations

import pytest

from agent import screener


# 預期 BUY 或 HOLD (Berkshire 公開重倉)
EXPECTED_GOOD = ["AAPL", "KO", "AXP", "MCO"]
ACCEPTED_GOOD_BIAS = {"BUY", "HOLD"}

# 預期 OUT_OF_CIRCLE (能力圈黑名單 + 結構性問題)
EXPECTED_BAD = ["TSLA", "COIN", "GME", "AMC", "IBM"]
ACCEPTED_BAD_BIAS = {"OUT_OF_CIRCLE", "AVOID"}


@pytest.mark.e2e
@pytest.mark.parametrize("ticker", EXPECTED_GOOD)
def test_buffett_holdings_score_ok(ticker):
    s = screener.score(ticker)
    assert s.bias in ACCEPTED_GOOD_BIAS, f"{ticker} got {s.bias} (score={s.total})"
    assert s.total >= 50, f"{ticker} score too low: {s.total}"


@pytest.mark.e2e
@pytest.mark.parametrize("ticker", EXPECTED_BAD)
def test_avoided_tickers_blocked(ticker):
    s = screener.score(ticker)
    assert s.bias in ACCEPTED_BAD_BIAS, f"{ticker} should be blocked but got {s.bias}"


@pytest.mark.e2e
def test_watchlist_runs_without_error():
    """跑完 stockTracker 全 watchlist 不可拋異常。"""
    scores = screener.score_watchlist()
    assert len(scores) > 0
    for s in scores:
        assert s.bias in {"BUY", "HOLD", "WATCH", "AVOID", "OUT_OF_CIRCLE", "INSUFFICIENT_DATA"}
