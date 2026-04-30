"""sec_metrics.py 單元測試 — 用合成 companyfacts fixture,不依賴網路。"""
from __future__ import annotations

import pytest

from agent import sec_metrics


def _facts(concepts: dict[str, dict]) -> dict:
    """Build a minimal companyfacts JSON。

    concepts: {raw_concept_name: {unit: [{fy, val, form, filed, ...}, ...]}}
    """
    return {
        "facts": {
            "us-gaap": {
                name: {"units": units}
                for name, units in concepts.items()
            }
        }
    }


def _row(year: int, val: float, *, form: str = "10-K", fp: str = "FY",
         filed: str | None = None, start: str | None = None, end: str | None = None) -> dict:
    return {
        "fy": year, "val": val, "form": form, "fp": fp,
        "filed": filed or f"{year + 1}-02-01",
        "start": start or f"{year}-01-01",
        "end": end or f"{year}-12-31",
    }


# ---------- long_term_debt_to_equity ----------

def test_long_term_debt_to_equity_basic():
    facts = _facts({
        "LongTermDebt": {"USD": [_row(2024, 100), _row(2023, 90)]},
        "StockholdersEquity": {"USD": [_row(2024, 500), _row(2023, 450)]},
    })
    de = sec_metrics.long_term_debt_to_equity(facts)
    assert de == pytest.approx(0.20)


def test_long_term_debt_to_equity_uses_latest_common_year():
    """債在 2024 有資料但權益只到 2023 → 應抓 2023。"""
    facts = _facts({
        "LongTermDebt": {"USD": [_row(2024, 100), _row(2023, 90)]},
        "StockholdersEquity": {"USD": [_row(2023, 450)]},
    })
    de = sec_metrics.long_term_debt_to_equity(facts)
    assert de == pytest.approx(0.2)  # 90/450


def test_long_term_debt_to_equity_missing_returns_none():
    facts = _facts({"LongTermDebt": {"USD": [_row(2024, 100)]}})
    assert sec_metrics.long_term_debt_to_equity(facts) is None


# ---------- owner_earnings_margin ----------

def test_owner_earnings_margin_5y_avg():
    """OE = (OCF - capex) / Rev,5 年平均。"""
    facts = _facts({
        "NetCashProvidedByUsedInOperatingActivities": {
            "USD": [_row(2020 + i, 200 + i * 10) for i in range(5)]
        },
        "PaymentsToAcquirePropertyPlantAndEquipment": {
            "USD": [_row(2020 + i, 50 + i * 5) for i in range(5)]
        },
        "RevenueFromContractWithCustomerExcludingAssessedTax": {
            "USD": [_row(2020 + i, 1000 + i * 100) for i in range(5)]
        },
    })
    m = sec_metrics.owner_earnings_margin(facts, n=5)
    # 2024: (240-70)/1400 = 0.1214; 2020: (200-50)/1000 = 0.15
    assert m is not None
    assert 0.10 < m < 0.16


def test_owner_earnings_margin_missing_data():
    facts = _facts({})
    assert sec_metrics.owner_earnings_margin(facts) is None


# ---------- buyback_yield ----------

def test_buyback_yield_positive_when_shares_decrease():
    facts = _facts({
        "CommonStockSharesOutstanding": {
            "shares": [_row(2023, 1_000_000_000), _row(2024, 950_000_000)]
        }
    })
    y = sec_metrics.buyback_yield(facts)
    assert y == pytest.approx(0.05)


def test_buyback_yield_negative_when_shares_increase():
    """有增發 → buyback yield 為負。"""
    facts = _facts({
        "CommonStockSharesOutstanding": {
            "shares": [_row(2023, 1_000_000_000), _row(2024, 1_100_000_000)]
        }
    })
    y = sec_metrics.buyback_yield(facts)
    assert y == pytest.approx(-0.10)


def test_buyback_yield_one_year_returns_none():
    facts = _facts({
        "CommonStockSharesOutstanding": {"shares": [_row(2024, 1_000_000_000)]}
    })
    assert sec_metrics.buyback_yield(facts) is None


# ---------- roic_5y_avg ----------

def test_roic_5y_avg_basic():
    facts = _facts({
        "NetIncomeLoss": {"USD": [_row(2020 + i, 100) for i in range(5)]},
        "Assets": {"USD": [_row(2020 + i, 1000) for i in range(5)]},
        "Liabilities": {"USD": [_row(2020 + i, 500) for i in range(5)]},
    })
    # 100 / (1000-500) = 0.20 each year → avg 0.20
    r = sec_metrics.roic_5y_avg(facts, n=5)
    assert r == pytest.approx(0.20)


# ---------- dividend_growth_streak ----------

def test_dividend_growth_streak_consecutive():
    """連續 4 年股利上升 → streak 4。"""
    facts = _facts({
        "CommonStockDividendsPerShareDeclared": {
            "USD/shares": [_row(2020 + i, 1.0 + i * 0.1) for i in range(5)]
        }
    })
    s = sec_metrics.dividend_growth_streak(facts)
    assert s == 4  # 5 個值之間有 4 個成長間隔


def test_dividend_growth_streak_break():
    """中間有一年下降 → streak 從最新回算。"""
    facts = _facts({
        "CommonStockDividendsPerShareDeclared": {
            # 2020:1.0, 2021:1.1, 2022:1.0(下降), 2023:1.2, 2024:1.3
            "USD/shares": [
                _row(2020, 1.0), _row(2021, 1.1), _row(2022, 1.0),
                _row(2023, 1.2), _row(2024, 1.3),
            ]
        }
    })
    s = sec_metrics.dividend_growth_streak(facts)
    # 從 2024 往回:2024>2023 ✓, 2023>2022 ✓, 2022>2021 ✗ → streak = 2
    assert s == 2


# ---------- consistency ----------

def test_consistency_roe_above_threshold():
    """10 年 ROE 都 > 15% → 1.0;一半達標 → 0.5。"""
    facts = _facts({
        "NetIncomeLoss": {"USD": [_row(2015 + i, 200) for i in range(10)]},
        "StockholdersEquity": {"USD": [_row(2015 + i, 1000) for i in range(10)]},
    })
    # 每年 ROE = 200/1000 = 20% > 15% → 全部過 → 1.0
    c = sec_metrics.consistency(
        facts, "NetIncome", 0.15, n=10, ratio_field="TotalEquity"
    )
    assert c == pytest.approx(1.0)


def test_consistency_partial():
    """5/10 年達標 → 0.5。"""
    facts = _facts({
        # 前 5 年 ROE 10% (不過),後 5 年 30% (過)
        "NetIncomeLoss": {
            "USD": [_row(2015 + i, 100) for i in range(5)]
                   + [_row(2020 + i, 300) for i in range(5)]
        },
        "StockholdersEquity": {"USD": [_row(2015 + i, 1000) for i in range(10)]},
    })
    c = sec_metrics.consistency(
        facts, "NetIncome", 0.15, n=10, ratio_field="TotalEquity"
    )
    assert c == pytest.approx(0.5)


# ---------- 整合 ----------

def test_extract_buffett_metrics_returns_no_data_for_unknown(monkeypatch):
    """no SEC data → 全 None,source=no_data。"""
    monkeypatch.setattr(sec_metrics.sec_api, "get_facts", lambda t: None)
    out = sec_metrics.extract_buffett_metrics("UNKNOWN")
    assert out["source"] == "no_data"
    assert out["long_term_de"] is None
    assert out["years_available"] == 0


def test_merge_sec_overwrites_yfinance_values(monkeypatch):
    """SEC 有值就覆寫 yfinance/CSV 的對應欄位。"""
    from agent import data_loader
    monkeypatch.setattr(data_loader, "SEC_ENABLED", True)
    fake_metrics = {
        "long_term_de": 0.42,
        "owner_earnings_5y": 0.18,
        "buyback_yield": 0.03,
        "roic_5y_avg": 0.22,
        "div_growth_streak": 8,
        "roe_consistency_10y": 0.9,
        "years_available": 12,
        "source": "sec",
    }
    monkeypatch.setattr(
        data_loader, "_sec_3y_eps_negative", lambda t: False
    )
    monkeypatch.setattr(
        "agent.sec_metrics.extract_buffett_metrics",
        lambda t: fake_metrics,
    )
    td = data_loader.TickerData(
        ticker="X", debt_equity=0.99, fcf_margin=0.10,
        buyback_yield=None, source="yfinance",
    )
    out = data_loader.merge_sec(td)
    assert out.debt_equity == 0.42       # SEC 覆寫
    assert out.fcf_margin == 0.18         # SEC 覆寫
    assert out.buyback_yield == 0.03      # SEC 補進
    assert out.roe_consistency_10y == 0.9
    assert out.roic_5y_avg == 0.22
    assert out.div_growth_streak == 8
    assert out.sec_years_available == 12
    assert "sec" in out.source


def test_merge_sec_no_data_keeps_yfinance(monkeypatch):
    """SEC 沒資料就維持原值,不覆寫。"""
    from agent import data_loader
    monkeypatch.setattr(data_loader, "SEC_ENABLED", True)
    monkeypatch.setattr(
        "agent.sec_metrics.extract_buffett_metrics",
        lambda t: {
            "long_term_de": None, "owner_earnings_5y": None,
            "buyback_yield": None, "roic_5y_avg": None,
            "div_growth_streak": 0, "roe_consistency_10y": None,
            "years_available": 0, "source": "no_data",
        },
    )
    td = data_loader.TickerData(
        ticker="X", debt_equity=0.99, fcf_margin=0.10, source="yfinance",
    )
    out = data_loader.merge_sec(td)
    assert out.debt_equity == 0.99       # 維持原值
    assert out.fcf_margin == 0.10
    assert "sec" not in out.source       # 沒覆寫,source 不加 +sec


def test_extract_buffett_metrics_with_full_data(monkeypatch):
    facts = _facts({
        "LongTermDebt": {"USD": [_row(2020 + i, 100) for i in range(5)]},
        "StockholdersEquity": {"USD": [_row(2020 + i, 500) for i in range(5)]},
        "NetIncomeLoss": {"USD": [_row(2020 + i, 100) for i in range(5)]},
        "Assets": {"USD": [_row(2020 + i, 1000) for i in range(5)]},
        "Liabilities": {"USD": [_row(2020 + i, 500) for i in range(5)]},
        "NetCashProvidedByUsedInOperatingActivities": {
            "USD": [_row(2020 + i, 200) for i in range(5)]
        },
        "PaymentsToAcquirePropertyPlantAndEquipment": {
            "USD": [_row(2020 + i, 50) for i in range(5)]
        },
        "RevenueFromContractWithCustomerExcludingAssessedTax": {
            "USD": [_row(2020 + i, 1000) for i in range(5)]
        },
    })
    monkeypatch.setattr(sec_metrics.sec_api, "get_facts", lambda t: facts)
    out = sec_metrics.extract_buffett_metrics("ANY")
    assert out["source"] == "sec"
    assert out["long_term_de"] == pytest.approx(0.20)
    assert out["owner_earnings_5y"] == pytest.approx(0.15)
    assert out["roic_5y_avg"] == pytest.approx(0.20)
    assert out["years_available"] == 5
