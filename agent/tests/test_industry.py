"""industry_metrics + industry_specific_rules 測試 (P2-3)。"""
from __future__ import annotations

import pytest

from agent import industry_metrics
from agent import rules as rules_mod
from agent.data_loader import TickerData, classify_industry


def _facts(concepts: dict[str, dict]) -> dict:
    return {"facts": {"us-gaap": {n: {"units": u} for n, u in concepts.items()}}}


def _row(year: int, val: float) -> dict:
    return {
        "fy": year, "val": val, "form": "10-K", "fp": "FY",
        "filed": f"{year + 1}-02-01",
        "start": f"{year}-01-01", "end": f"{year}-12-31",
    }


# ---------- classify_industry: REIT ----------

def test_classify_industry_reit():
    assert classify_industry("Real Estate", "REIT—Diversified") == "reit"
    assert classify_industry(None, "REIT—Industrial") == "reit"
    assert classify_industry("不動產", None) == "reit"
    # 非 REIT 不該誤判
    assert classify_industry("Technology", "Software") == "general"


# ---------- bank_roa ----------

def test_bank_roa_basic():
    facts = _facts({
        "NetIncomeLoss": {"USD": [_row(2024, 30_000_000_000)]},
        "Assets": {"USD": [_row(2024, 3_000_000_000_000)]},
    })
    roa = industry_metrics.bank_roa(facts)
    assert roa == pytest.approx(0.01)   # 1%


def test_bank_roa_missing():
    assert industry_metrics.bank_roa(_facts({})) is None


# ---------- efficiency_ratio ----------

def test_efficiency_ratio_basic():
    """rev=100, ni=20 each year × 5 → ratio = (100-20)/100 = 0.80。"""
    facts = _facts({
        "RevenueFromContractWithCustomerExcludingAssessedTax": {
            "USD": [_row(2020 + i, 100) for i in range(5)]
        },
        "NetIncomeLoss": {"USD": [_row(2020 + i, 20) for i in range(5)]},
    })
    er = industry_metrics.efficiency_ratio(facts)
    assert er == pytest.approx(0.80)


# ---------- capex_dep_ratio ----------

def test_capex_dep_ratio_growing():
    """capex 1.5×depreciation 5 年 → 1.5。"""
    facts = _facts({
        "PaymentsToAcquirePropertyPlantAndEquipment": {
            "USD": [_row(2020 + i, 150) for i in range(5)]
        },
        "DepreciationDepletionAndAmortization": {
            "USD": [_row(2020 + i, 100) for i in range(5)]
        },
    })
    r = industry_metrics.capex_dep_ratio(facts)
    assert r == pytest.approx(1.5)


# ---------- ffo_margin ----------

def test_ffo_margin_basic():
    """FFO = NI + D&A;此處 FFO/Rev = (50+30)/200 = 0.40。"""
    facts = _facts({
        "NetIncomeLoss": {"USD": [_row(2020 + i, 50) for i in range(5)]},
        "DepreciationDepletionAndAmortization": {
            "USD": [_row(2020 + i, 30) for i in range(5)]
        },
        "RevenueFromContractWithCustomerExcludingAssessedTax": {
            "USD": [_row(2020 + i, 200) for i in range(5)]
        },
    })
    m = industry_metrics.ffo_margin(facts)
    assert m == pytest.approx(0.40)


# ---------- evaluate (整合) ----------

def test_evaluate_general_returns_empty(monkeypatch):
    monkeypatch.setattr(industry_metrics.sec_api, "get_facts", lambda t: None)
    m = industry_metrics.evaluate("X", "general")
    assert m.bank_roa is None and m.ffo_margin is None


def test_evaluate_bank_populates_only_bank_fields(monkeypatch):
    facts = _facts({
        "NetIncomeLoss": {"USD": [_row(2020 + i, 100) for i in range(5)]},
        "Assets": {"USD": [_row(2024, 10000)]},
        "RevenueFromContractWithCustomerExcludingAssessedTax": {
            "USD": [_row(2020 + i, 200) for i in range(5)]
        },
    })
    monkeypatch.setattr(industry_metrics.sec_api, "get_facts", lambda t: facts)
    m = industry_metrics.evaluate("X", "bank")
    assert m.bank_roa is not None
    assert m.efficiency_ratio is not None
    assert m.capex_dep_ratio is None    # 不是 utility


# ---------- industry_specific_rules 套用 ----------

def test_industry_specific_bank_pass():
    """銀行 ROA 1.2% → BNK1 過。"""
    rules = rules_mod.load_rules()
    td = TickerData(ticker="X", industry_class="bank", bank_roa=0.012)
    bonuses = rules_mod.evaluate_bonuses(td, rules)
    bnk1 = next(b for b in bonuses if b.rule_id == "BNK1")
    assert bnk1.earned is True
    assert bnk1.points == 5


def test_industry_specific_bank_fail():
    """銀行 ROA 0.5% → BNK1 不過。"""
    rules = rules_mod.load_rules()
    td = TickerData(ticker="X", industry_class="bank", bank_roa=0.005)
    bonuses = rules_mod.evaluate_bonuses(td, rules)
    bnk1 = next(b for b in bonuses if b.rule_id == "BNK1")
    assert bnk1.earned is False


def test_industry_specific_general_no_bnk_bonuses():
    """general 公司不應收到 BNK/UTL/REIT bonuses。"""
    rules = rules_mod.load_rules()
    td = TickerData(ticker="X", industry_class="general", bank_roa=0.05)
    bonuses = rules_mod.evaluate_bonuses(td, rules)
    rids = {b.rule_id for b in bonuses}
    assert "BNK1" not in rids
    assert "UTL1" not in rids
    assert "REIT1" not in rids


def test_industry_specific_utility():
    rules = rules_mod.load_rules()
    td = TickerData(ticker="X", industry_class="utility", capex_dep_ratio=1.5)
    bonuses = rules_mod.evaluate_bonuses(td, rules)
    utl1 = next(b for b in bonuses if b.rule_id == "UTL1")
    assert utl1.earned is True
    assert utl1.points == 5


def test_industry_specific_reit():
    rules = rules_mod.load_rules()
    td = TickerData(ticker="X", industry_class="reit", ffo_margin=0.40)
    bonuses = rules_mod.evaluate_bonuses(td, rules)
    reit1 = next(b for b in bonuses if b.rule_id == "REIT1")
    assert reit1.earned is True


def test_industry_specific_missing_data_no_credit():
    """ROA 缺資料 → 不過,但不爆。"""
    rules = rules_mod.load_rules()
    td = TickerData(ticker="X", industry_class="bank", bank_roa=None)
    bonuses = rules_mod.evaluate_bonuses(td, rules)
    bnk1 = next(b for b in bonuses if b.rule_id == "BNK1")
    assert bnk1.earned is False
    assert bnk1.points == 0
