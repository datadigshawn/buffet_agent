"""backtest.py 單元測試 — fixture-based,不依賴 yfinance 網路。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from agent import backtest


def _scan(date: str, buys: list[tuple[str, int]]) -> dict:
    """Helper:建構 scan payload。"""
    return {
        "scan_date": date,
        "verdicts": [
            {"ticker": t, "bias": "BUY", "score": s} for t, s in buys
        ] + [
            {"ticker": "FOO", "bias": "HOLD", "score": 70},
            {"ticker": "BAR", "bias": "AVOID", "score": 30},
        ],
    }


# ---------- buy_basket ----------

def test_buy_basket_top_n_only():
    s = _scan("2026-01-01", [("AAPL", 90), ("KO", 85), ("MCO", 80), ("TSM", 75)])
    assert backtest.buy_basket(s, top_n=2) == ["AAPL", "KO"]


def test_buy_basket_excludes_non_buy():
    s = _scan("2026-01-01", [("AAPL", 90)])
    out = backtest.buy_basket(s)
    assert "FOO" not in out and "BAR" not in out


# ---------- load_historical_scans ----------

def test_load_historical_scans(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "daily_2026-01-15.json").write_text(json.dumps(_scan("2026-01-15", [("X", 80)])))
    (out / "daily_2026-04-01.json").write_text(json.dumps(_scan("2026-04-01", [("Y", 80)])))
    (out / "daily_invalid.json").write_text("not json")
    (out / "other.json").write_text("ignore")
    scans = backtest.load_historical_scans(output_dir=out)
    assert len(scans) == 2
    dates = [s["scan_date"] for s in scans]
    assert dates == ["2026-01-15", "2026-04-01"]   # 升序


# ---------- backtest_scan (with mocked compute_return) ----------

def test_backtest_scan_horizon_not_ready(monkeypatch):
    """昨天的掃描 → 30d horizon 不該 ready。"""
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    s = _scan(yesterday, [("AAPL", 90), ("KO", 85)])
    monkeypatch.setattr(backtest, "compute_return", lambda *a, **kw: None)
    result = backtest.backtest_scan(s, horizons=(30,))
    assert 30 in result.horizons
    assert result.horizons[30].ready is False
    assert result.horizons[30].alpha is None


def test_backtest_scan_horizon_ready_with_alpha(monkeypatch):
    """60 天前的 scan,30d horizon ready 且能算 alpha。"""
    past = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%d")
    s = _scan(past, [("AAPL", 90), ("KO", 85)])

    # mock: ticker AAPL +10%, KO +5%, SPX +3%
    def fake_return(t, scan_date, h):
        return {"AAPL": 0.10, "KO": 0.05, "^GSPC": 0.03}.get(t)
    monkeypatch.setattr(backtest, "compute_return", fake_return)

    result = backtest.backtest_scan(s, horizons=(30,))
    h = result.horizons[30]
    assert h.ready is True
    assert h.basket_return == pytest.approx(0.075, rel=1e-3)   # avg of 0.10 / 0.05
    assert h.benchmark_return == pytest.approx(0.03, rel=1e-3)
    assert h.alpha == pytest.approx(0.045, rel=1e-3)
    assert h.hit_rate == 1.0   # 兩個都正


def test_backtest_scan_partial_data(monkeypatch):
    """有些 ticker 抓不到價格 → 用其他算籃子平均。"""
    past = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%d")
    s = _scan(past, [("AAPL", 90), ("UNKNOWN", 85)])

    def fake_return(t, scan_date, h):
        return {"AAPL": 0.20, "^GSPC": 0.05}.get(t)   # UNKNOWN 抓不到
    monkeypatch.setattr(backtest, "compute_return", fake_return)

    result = backtest.backtest_scan(s, horizons=(30,))
    h = result.horizons[30]
    assert h.basket_return == pytest.approx(0.20)   # 只有 AAPL
    assert h.alpha == pytest.approx(0.15)


# ---------- aggregate ----------

def test_aggregate_no_data():
    s = backtest.aggregate([])
    assert s.weeks_with_30d_data == 0
    assert s.regression_alert is False


def test_aggregate_basic():
    """合成 3 週的 alpha 都 +5%, regression 不該觸發。"""
    sb_list = []
    for i, date in enumerate(["2026-01-01", "2026-01-08", "2026-01-15"]):
        sb = backtest.ScanBacktest(scan_date=date, buy_tickers=["X"])
        sb.horizons[30] = backtest.HorizonResult(
            horizon_days=30, ready=True, days_elapsed=60,
            basket_return=0.08, benchmark_return=0.03, alpha=0.05,
            hit_rate=0.7,
        )
        sb_list.append(sb)
    s = backtest.aggregate(sb_list)
    assert s.weeks_with_30d_data == 3
    assert s.avg_alpha_30d == pytest.approx(0.05)
    assert s.regression_alert is False


def test_aggregate_regression_triggered():
    """連 3 週 30d alpha < -5% → regression alert。"""
    sb_list = []
    for date in ["2026-01-01", "2026-01-08", "2026-01-15"]:
        sb = backtest.ScanBacktest(scan_date=date, buy_tickers=["X"])
        sb.horizons[30] = backtest.HorizonResult(
            horizon_days=30, ready=True, days_elapsed=60,
            basket_return=-0.05, benchmark_return=0.05, alpha=-0.10,
            hit_rate=0.2,
        )
        sb_list.append(sb)
    s = backtest.aggregate(sb_list)
    assert s.regression_alert is True
    assert s.consecutive_underperforming_weeks >= 3
    assert "Regression" in s.note


def test_aggregate_recent_recovery_no_alert():
    """最新一週恢復正 alpha → 不該觸發。"""
    sb_list = []
    # 兩週負,最近一週正
    for date, alpha in [("2026-01-01", -0.10), ("2026-01-08", -0.10), ("2026-01-15", 0.05)]:
        sb = backtest.ScanBacktest(scan_date=date, buy_tickers=["X"])
        sb.horizons[30] = backtest.HorizonResult(
            horizon_days=30, ready=True, days_elapsed=60,
            basket_return=alpha + 0.05, benchmark_return=0.05, alpha=alpha,
            hit_rate=0.5,
        )
        sb_list.append(sb)
    s = backtest.aggregate(sb_list)
    assert s.regression_alert is False
    assert s.consecutive_underperforming_weeks == 0   # 最近一週正,從 0 算起


# ---------- regression_alert_from_backtest (diff.py) ----------

def test_regression_alert_from_backtest_triggers():
    from agent import diff
    payload = {
        "rolling_summary": {
            "regression_alert": True,
            "consecutive_underperforming_weeks": 3,
            "avg_alpha_30d": -0.07,
            "note": "regression",
        }
    }
    alerts = diff.regression_alert_from_backtest(payload)
    assert len(alerts) == 1
    assert alerts[0].type == "regression_detected"
    assert alerts[0].severity == "high"


def test_regression_alert_from_backtest_no_trigger():
    from agent import diff
    payload = {"rolling_summary": {"regression_alert": False}}
    assert diff.regression_alert_from_backtest(payload) == []
    assert diff.regression_alert_from_backtest(None) == []
