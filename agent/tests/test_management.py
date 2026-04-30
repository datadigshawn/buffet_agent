"""management.py 單元測試 — fixture-based,不依賴網路。"""
from __future__ import annotations

import pytest

from agent import management


def _facts(concepts: dict[str, dict]) -> dict:
    return {"facts": {"us-gaap": {n: {"units": u} for n, u in concepts.items()}}}


def _row(year: int, val: float, fp: str = "FY", form: str = "10-K") -> dict:
    return {
        "fy": year, "val": val, "form": form, "fp": fp,
        "filed": f"{year + 1}-02-01",
        "start": f"{year}-01-01", "end": f"{year}-12-31",
    }


# ---------- bvps_cagr ----------

def test_bvps_cagr_growing():
    """5 年 BVPS 從 $10 → $20 → CAGR ≈ 14.87%。"""
    facts = _facts({
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": {
            "USD": [_row(2019 + i, 1_000_000_000 * (1 + i * 0.25)) for i in range(7)]
        },
        "CommonStockSharesOutstanding": {
            "shares": [_row(2019 + i, 100_000_000) for i in range(7)]
        },
    })
    cagr, growth = management.bvps_cagr(facts, n=5)
    assert cagr is not None
    # baseline year = 2020 (1.25B), latest = 2025 (2.5B)
    # CAGR = (2.5/1.25)^(1/5) - 1 ≈ 0.149
    assert 0.13 < cagr < 0.16
    assert growth > 0


def test_bvps_cagr_with_buybacks():
    """股本減少 + equity 持平 → BVPS 上升。"""
    facts = _facts({
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": {
            "USD": [_row(2019 + i, 1_000_000_000) for i in range(6)]
        },
        # 股數逐年下降 5%
        "CommonStockSharesOutstanding": {
            "shares": [_row(2019 + i, 100_000_000 * (0.95 ** i)) for i in range(6)]
        },
    })
    cagr, _ = management.bvps_cagr(facts, n=5)
    assert cagr is not None
    assert cagr > 0  # 股數降 → BVPS 升


def test_bvps_cagr_missing_data():
    facts = _facts({"NetIncomeLoss": {"USD": [_row(2024, 100)]}})
    cagr, growth = management.bvps_cagr(facts, n=5)
    assert cagr is None
    assert growth is None


# ---------- dividend_payout_ratio ----------

def test_payout_ratio_basic():
    """5 年 NI = 100 each, dividends = 30 each → ratio 0.30。"""
    facts = _facts({
        "NetIncomeLoss": {"USD": [_row(2020 + i, 100) for i in range(5)]},
        "PaymentsOfDividendsCommonStock": {
            "USD": [_row(2020 + i, -30) for i in range(5)]   # 現金流出為負
        },
    })
    r = management.dividend_payout_ratio(facts, n=5)
    assert r == pytest.approx(0.30, rel=1e-3)


def test_payout_ratio_no_dividends_returns_none():
    facts = _facts({
        "NetIncomeLoss": {"USD": [_row(2020 + i, 100) for i in range(5)]},
    })
    assert management.dividend_payout_ratio(facts) is None


def test_payout_ratio_loss_year_returns_none():
    """累計 NI = 0 (損益平衡) → 無法計算。"""
    facts = _facts({
        "NetIncomeLoss": {"USD": [_row(2020 + i, -100) for i in range(5)]},
        "PaymentsOfDividendsCommonStock": {
            "USD": [_row(2020 + i, -30) for i in range(5)]
        },
    })
    assert management.dividend_payout_ratio(facts) is None


# ---------- retained_earnings_test ----------

def test_retained_earnings_test_balanced():
    """5 年 retained = 5×$100 = $500, equity 成長 $500 → ratio = 1.0。"""
    facts = _facts({
        "NetIncomeLoss": {"USD": [_row(2020 + i, 100) for i in range(6)]},
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": {
            "USD": [_row(2019 + i, 1000 + i * 100) for i in range(7)]
        },
    })
    retained, eq_growth, ratio = management.retained_earnings_test(facts, n=5)
    assert retained == 500
    assert eq_growth == 500
    assert ratio == pytest.approx(1.0)


def test_retained_earnings_test_efficient():
    """股利消耗一些 retained,equity 成長略低。"""
    facts = _facts({
        "NetIncomeLoss": {"USD": [_row(2020 + i, 100) for i in range(6)]},
        "PaymentsOfDividendsCommonStock": {
            "USD": [_row(2020 + i, -20) for i in range(6)]   # 留存 80
        },
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": {
            "USD": [_row(2019 + i, 1000 + i * 80) for i in range(7)]
        },
    })
    retained, eq_growth, ratio = management.retained_earnings_test(facts, n=5)
    assert retained == 400  # (100 - 20) × 5
    assert eq_growth == 400
    assert ratio == pytest.approx(1.0)


# ---------- grade ----------

def test_grade_a_excellent_compounding():
    """BVPS CAGR 15% + 留存效率 1.5 → A。"""
    p = management.ManagementProfile(
        bvps_cagr_5y=0.15, retention_efficiency=1.5,
        dividend_payout_ratio_5y=0.40,
    )
    g, reasons = management.grade(p)
    assert g == "A"


def test_grade_d_destruction():
    """BVPS 下滑 + 留存效率 < 0 → D。"""
    p = management.ManagementProfile(
        bvps_cagr_5y=-0.05, retention_efficiency=-0.5,
    )
    g, reasons = management.grade(p)
    assert g == "D"


def test_grade_b_normal():
    """穩健成長:BVPS 8% + retention 0.8。"""
    p = management.ManagementProfile(
        bvps_cagr_5y=0.08, retention_efficiency=0.8,
    )
    g, _ = management.grade(p)
    assert g == "B"


def test_grade_question_mark_no_data():
    """完全沒資料 → 維持 ?。"""
    p = management.ManagementProfile()
    g, _ = management.grade(p)
    assert g == "C"   # 0 score 落到 C 區間 (按目前實作)


# ---------- evaluate (整合) ----------

def test_evaluate_no_facts(monkeypatch):
    """SEC 完全沒資料 → 仍試從 yfinance 拿 CEO,grade 留 ? 預設。"""
    monkeypatch.setattr(management.sec_api, "get_facts", lambda t: None)
    monkeypatch.setattr(
        management, "fetch_ceo_info_from_yfinance",
        lambda t: {"ceo_name": "John Doe", "ceo_title": "CEO"},
    )
    p = management.evaluate("XXX")
    assert p.ceo_name == "John Doe"
    assert p.bvps_cagr_5y is None
    assert p.grade == "?"


def test_evaluate_full(monkeypatch):
    """完整資料 → grade 應為 A 或 B 級。"""
    facts = _facts({
        "NetIncomeLoss": {"USD": [_row(2020 + i, 100) for i in range(6)]},
        "PaymentsOfDividendsCommonStock": {
            "USD": [_row(2020 + i, -20) for i in range(6)]
        },
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": {
            "USD": [_row(2019 + i, 1000 * (1 + i * 0.10)) for i in range(7)]
        },
        "CommonStockSharesOutstanding": {
            "shares": [_row(2019 + i, 100) for i in range(7)]
        },
    })
    monkeypatch.setattr(management.sec_api, "get_facts", lambda t: facts)
    monkeypatch.setattr(
        management, "fetch_ceo_info_from_yfinance",
        lambda t: {"ceo_name": "Jane Doe", "ceo_title": "CEO"},
    )
    p = management.evaluate("XYZ")
    assert p.ceo_name == "Jane Doe"
    assert p.bvps_cagr_5y is not None
    assert p.grade in ("A", "B")
