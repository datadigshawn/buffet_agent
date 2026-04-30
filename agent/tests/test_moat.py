"""moat.py 單元測試 — fixture-based。"""
from __future__ import annotations

import pytest

from agent import moat


def _td(**kwargs) -> dict:
    """構造一個 TickerData 風格的 dict (供 moat.evaluate 用)。"""
    base = {
        "gross_margin": None, "net_margin": None,
        "roe": None, "roe_consistency_10y": None,
        "roic_5y_avg": None, "fcf_margin": None,
        "rev_growth": None, "market_cap": None,
        "buyback_yield": None, "industry_class": "general",
    }
    base.update(kwargs)
    return base


# ---------- 五種類型 ----------

def test_intangible_assets_high_gross_margin():
    """品牌型公司 (KO/MCO):高毛利 + 高 ROE 持續性 → 高分。"""
    td = _td(gross_margin=0.60, net_margin=0.25, roe_consistency_10y=0.95)
    c = moat.score_intangible_assets(td)
    assert c.score >= 7
    assert "品牌定價權" in c.rationale or "毛利率" in c.rationale


def test_intangible_assets_low_margin_no_score():
    """低毛利 + 無 ROE 資料 → 低分。"""
    td = _td(gross_margin=0.10, net_margin=0.02)
    c = moat.score_intangible_assets(td)
    assert c.score <= 1


def test_switching_costs_high_roe_persistence():
    """軟體 / 服務 (高 ROE 持續性) → 高轉換成本分。"""
    td = _td(roe_consistency_10y=0.95, roic_5y_avg=0.30, fcf_margin=0.25)
    c = moat.score_switching_costs(td)
    assert c.score >= 8


def test_switching_costs_no_roic():
    td = _td(roe_consistency_10y=0.5, roic_5y_avg=None, fcf_margin=0.05)
    c = moat.score_switching_costs(td)
    assert c.score <= 3


def test_network_effects_growth_with_margin():
    """大公司 + 高成長 + 高毛利 → 網路效應分。"""
    td = _td(rev_growth=0.20, gross_margin=0.50, roic_5y_avg=0.30,
             market_cap=200_000_000_000)
    c = moat.score_network_effects(td)
    assert c.score >= 6


def test_network_effects_no_growth_signal():
    td = _td(market_cap=1_000_000_000)
    c = moat.score_network_effects(td)
    assert c.score == 0


def test_cost_advantage_scale_plus_buyback():
    """大規模 + 高毛利 + 持續回購 → 成本優勢 / 現金過剩。"""
    td = _td(market_cap=500_000_000_000, gross_margin=0.40, buyback_yield=0.04)
    c = moat.score_cost_advantage(td)
    assert c.score >= 6


def test_efficient_scale_utility_high_score():
    """公用事業 + 穩定 ROE + 大市值 → 滿分區。"""
    td = _td(industry_class="utility", roe=0.12, market_cap=80_000_000_000)
    c = moat.score_efficient_scale(td)
    assert c.score >= 6


def test_efficient_scale_general_zero():
    """非適用行業 (general) → 0 分。"""
    td = _td(industry_class="general", roe=0.20)
    c = moat.score_efficient_scale(td)
    assert c.score == 0


# ---------- 趨勢 ----------

def _facts(concepts: dict[str, dict]) -> dict:
    return {"facts": {"us-gaap": {n: {"units": u} for n, u in concepts.items()}}}


def _row(year: int, val: float) -> dict:
    return {
        "fy": year, "val": val, "form": "10-K", "fp": "FY",
        "filed": f"{year + 1}-02-01",
        "start": f"{year}-01-01", "end": f"{year}-12-31",
    }


def test_trend_widening():
    """近 5 年 ROE 比前 5 年高 15% 以上 → widening。"""
    facts = _facts({
        # 前 5 年 ROE 10%,後 5 年 ROE 20%
        "NetIncomeLoss": {"USD":
            [_row(2015 + i, 100) for i in range(5)]
            + [_row(2020 + i, 200) for i in range(5)]
        },
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": {
            "USD": [_row(2015 + i, 1000) for i in range(10)]
        },
    })
    trend, evidence = moat.analyze_trend(facts)
    assert trend == "widening"


def test_trend_narrowing():
    """近 5 年 ROE 跌 → narrowing。"""
    facts = _facts({
        "NetIncomeLoss": {"USD":
            [_row(2015 + i, 200) for i in range(5)]
            + [_row(2020 + i, 100) for i in range(5)]
        },
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": {
            "USD": [_row(2015 + i, 1000) for i in range(10)]
        },
    })
    trend, _ = moat.analyze_trend(facts)
    assert trend == "narrowing"


def test_trend_stable_when_no_change():
    facts = _facts({
        "NetIncomeLoss": {"USD": [_row(2015 + i, 150) for i in range(10)]},
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": {
            "USD": [_row(2015 + i, 1000) for i in range(10)]
        },
    })
    trend, _ = moat.analyze_trend(facts)
    assert trend == "stable"


def test_trend_insufficient_data():
    facts = _facts({"NetIncomeLoss": {"USD": [_row(2024, 100)]}})
    trend, evidence = moat.analyze_trend(facts)
    assert trend == "stable"
    assert any(("不足" in e) or ("缺" in e) for e in evidence)


# ---------- evaluate 整合 ----------

def test_evaluate_strong_moat_company():
    """KO / MCO 類:高毛利 + 高 ROE 持續性 → strong。"""
    td = _td(
        gross_margin=0.60, net_margin=0.25,
        roe=0.30, roe_consistency_10y=0.95, roic_5y_avg=0.30,
        fcf_margin=0.25, market_cap=300_000_000_000,
    )
    profile = moat.evaluate(td, facts=None)
    assert profile.overall_strength == "strong"
    assert "intangible_assets" in profile.dominant_types or \
           "switching_costs" in profile.dominant_types


def test_evaluate_weak_moat():
    """商品化 / 低毛利公司 → weak。"""
    td = _td(gross_margin=0.10, roe=0.05, market_cap=1_000_000_000)
    profile = moat.evaluate(td, facts=None)
    assert profile.overall_strength == "weak"


def test_evaluate_utility():
    """公用事業 → efficient_scale 主導。"""
    td = _td(
        industry_class="utility", roe=0.12, gross_margin=0.30,
        market_cap=80_000_000_000,
    )
    profile = moat.evaluate(td, facts=None)
    assert "efficient_scale" in profile.dominant_types


def test_to_dict_serializable():
    import json
    td = _td(gross_margin=0.50, roe_consistency_10y=0.9, roic_5y_avg=0.25,
             market_cap=100_000_000_000)
    profile = moat.evaluate(td, facts=None)
    json.dumps(profile.to_dict())   # 可序列化即過
