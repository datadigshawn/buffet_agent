"""valuation.py 單元測試 — fixture-based,不依賴網路。"""
from __future__ import annotations

import pytest

from agent import valuation


def _facts(concepts: dict[str, dict]) -> dict:
    return {"facts": {"us-gaap": {n: {"units": u} for n, u in concepts.items()}}}


def _row(year: int, val: float, *, form: str = "10-K", fp: str = "FY",
         filed: str | None = None) -> dict:
    return {
        "fy": year, "val": val, "form": form, "fp": fp,
        "filed": filed or f"{year + 1}-02-01",
        "start": f"{year}-01-01", "end": f"{year}-12-31",
    }


def _basic_facts() -> dict:
    """5 年穩定資料,各模型都能算。"""
    return _facts({
        "NetCashProvidedByUsedInOperatingActivities": {
            "USD": [_row(2020 + i, 200_000_000) for i in range(5)]
        },
        "PaymentsToAcquirePropertyPlantAndEquipment": {
            "USD": [_row(2020 + i, 50_000_000) for i in range(5)]
        },
        "RevenueFromContractWithCustomerExcludingAssessedTax": {
            "USD": [_row(2020 + i, 1_000_000_000) for i in range(5)]
        },
        "NetIncomeLoss": {
            "USD": [_row(2020 + i, 100_000_000) for i in range(5)]
        },
        "CommonStockSharesOutstanding": {
            "shares": [_row(2020 + i, 50_000_000) for i in range(5)]
        },
    })


# ---------- Shiller PE ----------

def test_shiller_pe_basic():
    """avg EPS = 100M / 50M = $2,fair PE 17 → intrinsic = $34。"""
    facts = _basic_facts()
    c = valuation.shiller_pe_estimate(facts, current_price=30.0)
    assert c.method == "shiller_pe"
    assert c.intrinsic_per_share == pytest.approx(34.0)
    assert c.margin_of_safety == pytest.approx((34 - 30) / 34, rel=1e-3)


def test_shiller_pe_negative_eps_returns_none():
    facts = _facts({
        "NetIncomeLoss": {"USD": [_row(2020 + i, -100) for i in range(5)]},
        "CommonStockSharesOutstanding": {
            "shares": [_row(2020 + i, 1_000_000) for i in range(5)]
        },
    })
    c = valuation.shiller_pe_estimate(facts, current_price=10.0)
    assert c.intrinsic_per_share is None
    assert "為負" in c.note


def test_shiller_pe_insufficient_years():
    """只有 2 年資料 < 3 → 不估。"""
    facts = _facts({
        "NetIncomeLoss": {"USD": [_row(2023, 100), _row(2024, 100)]},
        "CommonStockSharesOutstanding": {"shares": [_row(2024, 50)]},
    })
    c = valuation.shiller_pe_estimate(facts, current_price=10.0)
    assert c.intrinsic_per_share is None


# ---------- Owner Earnings Yield ----------

def test_owner_earnings_yield_basic():
    """5y avg OE = 150M;market_cap = 1B → yield 15% > fair 10.4% → undervalued。"""
    facts = _basic_facts()
    c = valuation.owner_earnings_yield_estimate(
        facts, market_cap=1_000_000_000, current_price=10.0,
    )
    assert c.method == "owner_earnings_yield"
    # intrinsic_market_cap = 150M / 0.104 ≈ 1.44B
    # intrinsic_per_share = 1.44B / 50M ≈ $28.8
    assert c.intrinsic_per_share == pytest.approx(150_000_000 / 0.104 / 50_000_000, rel=1e-3)
    assert c.margin_of_safety > 0  # under valued


def test_owner_earnings_yield_no_market_cap():
    facts = _basic_facts()
    c = valuation.owner_earnings_yield_estimate(facts, market_cap=None, current_price=10.0)
    assert c.intrinsic_per_share is None
    assert "market_cap" in c.note


def test_owner_earnings_yield_for_bank_uses_net_income():
    """銀行業 (industry_class=bank) 用 NetIncome 當 OE proxy。"""
    facts = _facts({
        "NetIncomeLoss": {"USD": [_row(2020 + i, 1_000_000_000) for i in range(5)]},
        "CommonStockSharesOutstanding": {"shares": [_row(2024, 100_000_000)]},
    })
    c = valuation.owner_earnings_yield_estimate(
        facts, market_cap=10_000_000_000, current_price=50.0,
        industry_class="bank",
    )
    # 銀行版 OE = NetIncome 1B/年,5y avg = 1B
    # intrinsic_mc = 1B / 0.104 ≈ 9.6B → intrinsic_per_share ≈ $96
    assert c.intrinsic_per_share == pytest.approx(1_000_000_000 / 0.104 / 100_000_000, rel=1e-3)


# ---------- Ensemble ----------

def test_ensemble_three_methods(monkeypatch):
    """3 模型都能算 → method_count=3,intrinsic_low/mid/high 不同。"""
    facts = _basic_facts()
    monkeypatch.setattr(valuation.sec_api, "get_facts", lambda t: facts)
    out = valuation.estimate(
        "X", current_price=20.0, market_cap=1_000_000_000,
    )
    assert out.method_count == 3
    assert out.intrinsic_low < out.intrinsic_mid <= out.intrinsic_high
    assert out.consensus in {"very_cheap", "cheap", "fair", "expensive", "very_expensive"}


def test_ensemble_no_facts_returns_uncertain(monkeypatch):
    monkeypatch.setattr(valuation.sec_api, "get_facts", lambda t: None)
    out = valuation.estimate("X", current_price=10.0, market_cap=1e9)
    assert out.method_count == 0
    assert out.consensus == "uncertain"


def test_ensemble_partial_data(monkeypatch):
    """只有 NetIncome + Shares (沒 OCF/Capex) → DCF/OE yield 失敗,只有 Shiller PE 過。"""
    facts = _facts({
        "NetIncomeLoss": {"USD": [_row(2020 + i, 100_000_000) for i in range(5)]},
        "CommonStockSharesOutstanding": {
            "shares": [_row(2020 + i, 50_000_000) for i in range(5)]
        },
    })
    monkeypatch.setattr(valuation.sec_api, "get_facts", lambda t: facts)
    out = valuation.estimate("X", current_price=20.0, market_cap=1e9)
    assert out.method_count == 1
    assert out.intrinsic_mid == out.intrinsic_low == out.intrinsic_high


def test_ensemble_consensus_classification(monkeypatch):
    """股價低於 mid intrinsic 30% 以上 → consensus very_cheap。"""
    facts = _basic_facts()
    monkeypatch.setattr(valuation.sec_api, "get_facts", lambda t: facts)
    out = valuation.estimate(
        "X", current_price=5.0, market_cap=250_000_000,  # 很低估
    )
    assert out.consensus == "very_cheap"


def test_consensus_bucket_boundaries():
    """直接測 _classify_consensus 邊界。"""
    assert valuation._classify_consensus(0.50) == "very_cheap"
    assert valuation._classify_consensus(0.30) == "very_cheap"
    assert valuation._classify_consensus(0.20) == "cheap"
    assert valuation._classify_consensus(0.10) == "cheap"
    assert valuation._classify_consensus(0.00) == "fair"
    assert valuation._classify_consensus(-0.10) == "fair"
    assert valuation._classify_consensus(-0.20) == "expensive"
    assert valuation._classify_consensus(-0.50) == "very_expensive"
    assert valuation._classify_consensus(-1.00) == "very_expensive"
    assert valuation._classify_consensus(None) == "uncertain"


# ---------- T-1: shares_fallback for multi-class structures (V/MA) ----------

def test_shiller_pe_uses_shares_fallback_when_sec_missing():
    """SEC 沒 SharesOutstanding (V 多類股) → 用 yfinance fallback。"""
    facts = _facts({
        "NetIncomeLoss": {
            "USD": [_row(2020 + i, 100_000_000) for i in range(5)]
        },
        # 故意沒 CommonStockSharesOutstanding
    })
    c = valuation.shiller_pe_estimate(
        facts, current_price=20.0, shares_fallback=50_000_000,
    )
    assert c.intrinsic_per_share is not None
    assert "fallback" in c.note


def test_shiller_pe_no_fallback_returns_none():
    """SEC 沒 + fallback 也沒 → 回 None。"""
    facts = _facts({
        "NetIncomeLoss": {
            "USD": [_row(2020 + i, 100_000_000) for i in range(5)]
        },
    })
    c = valuation.shiller_pe_estimate(facts, current_price=20.0, shares_fallback=None)
    assert c.intrinsic_per_share is None


def test_ensemble_uses_market_cap_for_fallback(monkeypatch):
    """估值 ensemble 應該從 market_cap / current_price 推 shares_fallback。"""
    facts = _facts({
        "NetCashProvidedByUsedInOperatingActivities": {
            "USD": [_row(2020 + i, 200_000_000) for i in range(5)]
        },
        "PaymentsToAcquirePropertyPlantAndEquipment": {
            "USD": [_row(2020 + i, 50_000_000) for i in range(5)]
        },
        "NetIncomeLoss": {
            "USD": [_row(2020 + i, 100_000_000) for i in range(5)]
        },
        # 沒 CommonStockSharesOutstanding,模擬 V/MA 情境
    })
    monkeypatch.setattr(valuation.sec_api, "get_facts", lambda t: facts)
    out = valuation.estimate(
        "V", current_price=20.0, market_cap=1_000_000_000,
    )
    # market_cap 1B / price 20 = 50M shares fallback
    # 應該至少 1 個方法跑出來
    assert out.method_count >= 1


def test_ensemble_to_dict_serializable(monkeypatch):
    """to_dict() 結果可序列化成 JSON。"""
    import json
    facts = _basic_facts()
    monkeypatch.setattr(valuation.sec_api, "get_facts", lambda t: facts)
    out = valuation.estimate("X", current_price=20.0, market_cap=1e9)
    d = out.to_dict()
    s = json.dumps(d)
    assert "consensus" in s
    assert "contributors" in s
