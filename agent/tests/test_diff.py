"""diff.py 單元測試 — 不依賴外部資料。"""
from __future__ import annotations

from agent import diff


def _verdict(ticker: str, bias: str, score: int = 0,
             mos: float | None = None,
             recommendation: str | None = None,
             trigger: str | None = None) -> dict:
    return {
        "ticker": ticker, "bias": bias, "score": score,
        "margin_of_safety_pct": mos,
        "recommendation": recommendation,
        "triggered_disqualifier": trigger,
    }


def _scan(verdicts: list[dict]) -> dict:
    return {"verdicts": verdicts}


# ---------- 基本路徑 ----------

def test_no_yesterday_returns_empty():
    today = _scan([_verdict("AAPL", "BUY", 85)])
    assert diff.detect(None, today) == []


def test_no_changes_returns_empty():
    s = _scan([_verdict("AAPL", "BUY", 85), _verdict("KO", "HOLD", 70)])
    assert diff.detect(s, s) == []


# ---------- bias_changed ----------

def test_bias_upgrade_to_buy_is_high_severity():
    """HOLD → BUY 應為 high severity bias_changed alert。

    同時也會產生 new_top10 衍生 alert,只要 bias_changed 是 high 就算過。
    """
    yesterday = _scan([_verdict("TSM", "HOLD", 70)])
    today = _scan([_verdict("TSM", "BUY", 84, recommendation="加碼建議")])
    alerts = diff.detect(yesterday, today)
    bc = next(a for a in alerts if a.type == "bias_changed")
    assert bc.severity == "high"
    assert "↑" in bc.summary
    assert bc.today["recommendation"] == "加碼建議"


def test_bias_downgrade_from_buy_is_medium():
    """BUY → HOLD 是 medium。"""
    yesterday = _scan([_verdict("V", "BUY", 99)])
    today = _scan([_verdict("V", "HOLD", 70)])
    alerts = diff.detect(yesterday, today)
    a = next(a for a in alerts if a.type == "bias_changed")
    assert a.severity == "medium"
    assert "↓" in a.summary


def test_watch_to_avoid_no_alert():
    """WATCH ↔ AVOID 邊界外移動不應 alert(訊號弱)。"""
    yesterday = _scan([_verdict("X", "WATCH", 45)])
    today = _scan([_verdict("X", "AVOID", 35)])
    alerts = diff.detect(yesterday, today)
    bias_alerts = [a for a in alerts if a.type == "bias_changed"]
    assert bias_alerts == []


# ---------- disqualifier_triggered ----------

def test_disqualifier_from_buy_is_high():
    """BUY → OUT_OF_CIRCLE 應特別標 disqualifier_triggered + high。

    同時也可能產生 dropped_from_top10 衍生 alert(BUY 跌走自然跌出榜)。
    """
    yesterday = _scan([_verdict("X", "BUY", 80)])
    today = _scan([_verdict("X", "OUT_OF_CIRCLE", 0,
                            trigger="D3: 加密貨幣交易所")])
    alerts = diff.detect(yesterday, today)
    dq = next(a for a in alerts if a.type == "disqualifier_triggered")
    assert dq.severity == "high"
    assert "D3" in dq.summary
    # 不應額外產 bias_changed alert(disqualifier_triggered 已涵蓋)
    bc = [a for a in alerts if a.type == "bias_changed"]
    assert bc == []


def test_disqualifier_from_watch_no_alert():
    """WATCH → OUT_OF_CIRCLE 不單獨標 disqualifier_triggered。"""
    yesterday = _scan([_verdict("X", "WATCH", 45)])
    today = _scan([_verdict("X", "OUT_OF_CIRCLE", 0)])
    alerts = diff.detect(yesterday, today)
    disq_alerts = [a for a in alerts if a.type == "disqualifier_triggered"]
    assert disq_alerts == []


# ---------- new_top10 / dropped_from_top10 ----------

def test_new_top10_alert():
    """昨天前 10 沒它,今天有 → new_top10。"""
    yesterday = _scan([_verdict(f"S{i}", "BUY", 100 - i) for i in range(10)])
    today_vs = [_verdict(f"S{i}", "BUY", 100 - i) for i in range(10)]
    today_vs[-1] = _verdict("NEW", "BUY", 91)  # NEW 擠掉 S9
    today_vs.append(_verdict("S9", "BUY", 90))
    today = _scan(today_vs)
    alerts = diff.detect(yesterday, today)
    new_top = [a for a in alerts if a.type == "new_top10"]
    assert any(a.ticker == "NEW" for a in new_top)


def test_dropped_from_top10_alert():
    yesterday = _scan([_verdict(f"S{i}", "BUY", 100 - i) for i in range(10)])
    # S9 跌出
    today_vs = [_verdict(f"S{i}", "BUY", 100 - i) for i in range(9)]
    today_vs.append(_verdict("OTHER", "BUY", 91))
    today_vs.append(_verdict("S9", "HOLD", 50))
    today = _scan(today_vs)
    alerts = diff.detect(yesterday, today)
    dropped = [a for a in alerts if a.type == "dropped_from_top10"]
    assert any(a.ticker == "S9" for a in dropped)


# ---------- mos_first_positive ----------

def test_mos_first_positive_from_negative():
    yesterday = _scan([_verdict("BAC", "BUY", 90, mos=-0.05)])
    today = _scan([_verdict("BAC", "BUY", 90, mos=0.03)])
    alerts = diff.detect(yesterday, today)
    a = next(a for a in alerts if a.type == "mos_first_positive")
    assert a.severity == "high"
    assert "+3.0%" in a.summary or "3.0%" in a.summary


def test_mos_already_positive_no_alert():
    """昨天已經是正值,今天還是正,不再觸發。"""
    yesterday = _scan([_verdict("BAC", "BUY", 90, mos=0.02)])
    today = _scan([_verdict("BAC", "BUY", 90, mos=0.05)])
    alerts = diff.detect(yesterday, today)
    mos_alerts = [a for a in alerts if a.type == "mos_first_positive"]
    assert mos_alerts == []


def test_mos_missing_data_no_alert():
    """MOS = None → 不能比較 → 不 alert。"""
    yesterday = _scan([_verdict("X", "BUY", 80, mos=None)])
    today = _scan([_verdict("X", "BUY", 80, mos=0.05)])
    alerts = diff.detect(yesterday, today)
    mos_alerts = [a for a in alerts if a.type == "mos_first_positive"]
    assert mos_alerts == []


# ---------- 整合 ----------

def test_alerts_sorted_by_severity():
    """severity high → medium → low。"""
    yesterday = _scan([
        _verdict("HIGH1", "BUY", 80, mos=-0.05),    # 將觸發 mos_first_positive (high)
        _verdict("MED1", "HOLD", 70),                # 將 bias_changed up to BUY (high)
        _verdict("LOW1", "BUY", 90),                 # 將跌出 top10 (low)
    ])
    today = _scan([
        _verdict("HIGH1", "BUY", 80, mos=0.03),
        _verdict("MED1", "BUY", 85),
        _verdict("LOW1", "HOLD", 50),
    ])
    alerts = diff.detect(yesterday, today)
    assert alerts[0].severity == "high"
    # 至少 2 個 high
    assert sum(1 for a in alerts if a.severity == "high") >= 2


def test_count_helpers():
    alerts = [
        diff.Alert("X", "bias_changed", "high", "x"),
        diff.Alert("Y", "bias_changed", "medium", "y"),
        diff.Alert("Z", "new_top10", "medium", "z"),
    ]
    assert diff._count_by_type(alerts) == {"bias_changed": 2, "new_top10": 1}
    assert diff._count_by_severity(alerts) == {"high": 1, "medium": 2}
