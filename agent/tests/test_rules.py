"""rules.py 單元測試 — 不依賴外部資料,只測規則邏輯。"""
from __future__ import annotations

from agent import rules as rules_mod
from agent.data_loader import TickerData


def _td(**overrides) -> TickerData:
    base = dict(ticker="TEST", sector="Technology")
    base.update(overrides)
    return TickerData(**base)


def test_load_rules_structure():
    r = rules_mod.load_rules()
    assert "core_rules" in r
    assert "hard_disqualifiers" in r
    assert "soft_bonuses" in r
    assert "scoring" in r
    assert len(r["core_rules"]) == 10
    assert len(r["hard_disqualifiers"]) == 4
    assert len(r["soft_bonuses"]) == 6


def test_r1_roe_passed():
    rules = rules_mod.load_rules()
    r1 = rules["core_rules"][0]  # R1: ROE >= 15%
    td = _td(roe=0.20)
    result = rules_mod.evaluate_rule(r1, td)
    assert result.passed is True
    assert result.points == 15
    assert result.actual == 0.20


def test_r1_roe_failed():
    rules = rules_mod.load_rules()
    r1 = rules["core_rules"][0]
    td = _td(roe=0.05)
    result = rules_mod.evaluate_rule(r1, td)
    assert result.passed is False
    assert result.points == 0


def test_r1_roe_skipped_when_missing():
    rules = rules_mod.load_rules()
    r1 = rules["core_rules"][0]
    td = _td(roe=None)
    result = rules_mod.evaluate_rule(r1, td)
    assert result.skipped is True
    assert result.points == 0


def test_r8_or_pe_passed():
    rules = rules_mod.load_rules()
    r8 = next(r for r in rules["core_rules"] if r["id"] == "R8")
    # PE < 25 → 通過
    td = _td(fwd_pe=20, peg=2.0)
    result = rules_mod.evaluate_rule(r8, td)
    assert result.passed is True


def test_r8_or_peg_passed():
    rules = rules_mod.load_rules()
    r8 = next(r for r in rules["core_rules"] if r["id"] == "R8")
    # PEG < 1.5 → 通過(即使 PE 高)
    td = _td(fwd_pe=30, peg=1.0)
    result = rules_mod.evaluate_rule(r8, td)
    assert result.passed is True


def test_r10_berkshire_holds():
    rules = rules_mod.load_rules()
    r10 = next(r for r in rules["core_rules"] if r["id"] == "R10")
    td = _td(berkshire_holds=True)
    result = rules_mod.evaluate_rule(r10, td)
    assert result.passed is True
    assert result.points == 10


# ---------- disqualifiers ----------

def test_d1_high_leverage():
    rules = rules_mod.load_rules()
    td = _td(ticker="X", debt_equity=3.0)
    disqs = rules_mod.evaluate_disqualifiers(td, rules)
    d1 = next(d for d in disqs if d.rule_id == "D1")
    assert d1.triggered is True


def test_d1_financial_sector_relaxed():
    rules = rules_mod.load_rules()
    td = _td(ticker="X", sector="Financial Services", debt_equity=3.0)
    disqs = rules_mod.evaluate_disqualifiers(td, rules)
    d1 = next(d for d in disqs if d.rule_id == "D1")
    # 金融業放寬至 5.0,3.0 不觸發
    assert d1.triggered is False


def test_d1_berkshire_verified_relaxed():
    """MCO 等 Berkshire 持股應放寬 D/E 限制。"""
    rules = rules_mod.load_rules()
    td = _td(ticker="MCO", debt_equity=3.0)
    disqs = rules_mod.evaluate_disqualifiers(td, rules)
    d1 = next(d for d in disqs if d.rule_id == "D1")
    assert d1.triggered is False


def test_d3_ticker_blacklist():
    rules = rules_mod.load_rules()
    for t in ("TSLA", "COIN", "GME"):
        td = _td(ticker=t)
        disqs = rules_mod.evaluate_disqualifiers(td, rules)
        d3 = next(d for d in disqs if d.rule_id == "D3")
        assert d3.triggered, f"{t} should be blacklisted"


def test_d3_sector_blacklist():
    rules = rules_mod.load_rules()
    td = _td(ticker="X", sector="量子電腦")
    disqs = rules_mod.evaluate_disqualifiers(td, rules)
    d3 = next(d for d in disqs if d.rule_id == "D3")
    assert d3.triggered is True


def test_d4_eps_negative():
    rules = rules_mod.load_rules()
    td = _td(ticker="X", eps_3y_negative=True)
    disqs = rules_mod.evaluate_disqualifiers(td, rules)
    d4 = next(d for d in disqs if d.rule_id == "D4")
    assert d4.triggered is True


# ---------- bonuses ----------

def test_b1_berkshire_position():
    rules = rules_mod.load_rules()
    td = _td(berkshire_position_pct=0.05)
    bonuses = rules_mod.evaluate_bonuses(td, rules)
    b1 = next(b for b in bonuses if b.rule_id == "B1")
    assert b1.earned is True
    assert b1.points == 5


def test_b4_other_value_investors():
    rules = rules_mod.load_rules()
    td = _td(other_value_investors=["Bill Ackman / Pershing"])
    bonuses = rules_mod.evaluate_bonuses(td, rules)
    b4 = next(b for b in bonuses if b.rule_id == "B4")
    assert b4.earned is True


def test_b2_roic_5y_avg_threshold():
    rules = rules_mod.load_rules()
    # >20% 過
    td = _td(roic_5y_avg=0.25)
    bonuses = rules_mod.evaluate_bonuses(td, rules)
    b2 = next(b for b in bonuses if b.rule_id == "B2")
    assert b2.earned is True
    assert b2.points == 5
    # 20% 邊界 (>20 不含等於)
    td2 = _td(roic_5y_avg=0.20)
    b2 = next(b for b in rules_mod.evaluate_bonuses(td2, rules) if b.rule_id == "B2")
    assert b2.earned is False


def test_b2_roic_missing_no_credit():
    rules = rules_mod.load_rules()
    td = _td(roic_5y_avg=None)
    b2 = next(b for b in rules_mod.evaluate_bonuses(td, rules) if b.rule_id == "B2")
    assert b2.earned is False
    assert b2.points == 0


def test_b5_dividend_growth_streak():
    rules = rules_mod.load_rules()
    # 10 年連續 → 過
    td = _td(div_growth_streak=10)
    b5 = next(b for b in rules_mod.evaluate_bonuses(td, rules) if b.rule_id == "B5")
    assert b5.earned is True
    # 9 年不過
    td2 = _td(div_growth_streak=9)
    b5 = next(b for b in rules_mod.evaluate_bonuses(td2, rules) if b.rule_id == "B5")
    assert b5.earned is False


# ---------- B4: industry_overrides ----------

def test_classify_industry():
    from agent.data_loader import classify_industry
    assert classify_industry("Financial Services", "Banks—Diversified") == "bank"
    assert classify_industry("Financial Services", "Insurance—Diversified") == "insurance"
    assert classify_industry("Utilities", "Utilities—Regulated Electric") == "utility"
    assert classify_industry("Technology", "Software—Application") == "general"
    assert classify_industry(None, None) == "general"


def test_r1_general_threshold_applies():
    """非銀行 ticker 用預設 0.15 門檻。"""
    rules = rules_mod.load_rules()
    r1 = rules["core_rules"][0]
    td = _td(roe=0.12, industry_class="general")
    result = rules_mod.evaluate_rule(r1, td, rules)
    assert result.passed is False
    assert result.threshold == 0.15


def test_r1_bank_uses_override_threshold():
    """銀行 ROE 12% 在預設 15% 門檻下會 fail,但 bank override 0.10 應 pass。"""
    rules = rules_mod.load_rules()
    r1 = rules["core_rules"][0]
    td = _td(roe=0.12, industry_class="bank")
    result = rules_mod.evaluate_rule(r1, td, rules)
    assert result.passed is True
    assert result.threshold == 0.10
    assert "industry=bank" in result.note


def test_r5_bank_relaxed_de():
    """銀行 D/E 1.2 在預設 0.5 門檻下 fail,但 bank 1.5 應 pass。"""
    rules = rules_mod.load_rules()
    r5 = next(r for r in rules["core_rules"] if r["id"] == "R5")
    td = _td(debt_equity=1.2, industry_class="bank")
    result = rules_mod.evaluate_rule(r5, td, rules)
    assert result.passed is True
    assert result.threshold == 1.5


def test_r1_insurance_threshold():
    rules = rules_mod.load_rules()
    r1 = rules["core_rules"][0]
    td = _td(roe=0.13, industry_class="insurance")
    result = rules_mod.evaluate_rule(r1, td, rules)
    assert result.passed is True  # >= 0.12
    assert result.threshold == 0.12


# ---------- T-2: 外國 issuer 偵測 ----------

def test_foreign_issuer_marked_insufficient_data():
    """yfinance 有資料、SEC 嘗試但 0 年 → INSUFFICIENT_DATA (TSM 等)。"""
    from unittest.mock import patch
    from agent import screener
    td = TickerData(
        ticker="TSM", sector="晶片製造",
        roe=0.30, gross_margin=0.55, net_margin=0.40, earn_growth=0.20,
        debt_equity=0.2, fwd_pe=22, peg=1.2, w52_pos=0.5,
        price=200.0, market_cap=1e12,
        sec_years_available=0,
        source="csv+yfinance+sec",  # 嘗試過 SEC
    )
    with patch("agent.screener.data_loader.load_ticker", return_value=td):
        s = screener.score("TSM")
    assert s.bias == "INSUFFICIENT_DATA"


def test_us_issuer_with_sec_data_not_marked():
    """US 有 SEC 資料的不該被當外國 issuer。"""
    from unittest.mock import patch
    from agent import screener
    td = TickerData(
        ticker="AAPL", sector="Technology",
        roe=0.30, gross_margin=0.45, net_margin=0.25, earn_growth=0.15,
        debt_equity=1.0, fcf_margin=0.20, fwd_pe=25, peg=1.5, w52_pos=0.7,
        price=270.0, market_cap=4e12,
        sec_years_available=17,
        source="yfinance+sec",
    )
    with patch("agent.screener.data_loader.load_ticker", return_value=td):
        s = screener.score("AAPL")
    assert s.bias != "INSUFFICIENT_DATA"


def test_no_sec_attempted_not_marked_foreign():
    """SEC 沒嘗試 (純 yfinance) 不該觸發 foreign issuer 邏輯。"""
    from unittest.mock import patch
    from agent import screener
    td = TickerData(
        ticker="X", sector="Tech",
        roe=0.30, gross_margin=0.50, net_margin=0.20, earn_growth=0.10,
        debt_equity=0.3, w52_pos=0.5, price=100.0,
        sec_years_available=0,
        source="yfinance",  # 純 yfinance,沒嘗試 SEC
    )
    with patch("agent.screener.data_loader.load_ticker", return_value=td):
        s = screener.score("X")
    # 不應該因為 sec_years=0 就誤判 (純 yfinance 路徑可能因 SEC disabled)
    assert s.bias != "INSUFFICIENT_DATA" or s.coverage_pct < 50


def test_b6_roe_consistency_threshold():
    rules = rules_mod.load_rules()
    # 0.8 含 → 過
    td = _td(roe_consistency_10y=0.8)
    b6 = next(b for b in rules_mod.evaluate_bonuses(td, rules) if b.rule_id == "B6")
    assert b6.earned is True
    assert b6.points == 5
    # 0.79 → 不過
    td2 = _td(roe_consistency_10y=0.79)
    b6 = next(b for b in rules_mod.evaluate_bonuses(td2, rules) if b.rule_id == "B6")
    assert b6.earned is False
    # None → 不過
    td3 = _td(roe_consistency_10y=None)
    b6 = next(b for b in rules_mod.evaluate_bonuses(td3, rules) if b.rule_id == "B6")
    assert b6.earned is False


# ---------- screener: 涵蓋率 / INSUFFICIENT_DATA ----------

def test_screener_insufficient_data_for_etf_like():
    """ETF / 冷門股: 大量規則 skip → 應判 INSUFFICIENT_DATA 而非 AVOID。"""
    from unittest.mock import patch
    from agent import screener
    # 全空 TickerData (模擬 yfinance 拿不到資料的 ticker)
    bare = TickerData(ticker="EMPTY", sector=None)
    with patch("agent.screener.data_loader.load_ticker", return_value=bare):
        s = screener.score("EMPTY")
    assert s.bias == "INSUFFICIENT_DATA"
    # R10 (Berkshire bool) 永遠可評估 → coverage 不會是 0,但會 < 50
    assert s.coverage_pct < 50


def test_screener_normalized_score_high_coverage():
    """資料齊全 + 多數規則通過: bias 應為 BUY 或 HOLD,base 經 normalize。"""
    from unittest.mock import patch
    from agent import screener
    td = TickerData(
        ticker="GOODCO", sector="Technology",
        roe=0.20, gross_margin=0.50, net_margin=0.25, earn_growth=0.15,
        debt_equity=0.3, fcf_margin=0.20, buyback_yield=0.02,
        fwd_pe=18, peg=1.2, w52_pos=0.6,
        berkshire_holds=False, eps_3y_negative=False,
    )
    with patch("agent.screener.data_loader.load_ticker", return_value=td):
        s = screener.score("GOODCO")
    assert s.bias in ("BUY", "HOLD")
    assert s.coverage_pct >= 90
    # 9 條過 + 1 條 R10 不過(沒 BRK 持有) → earned=90, available=100, normalized=90
    assert s.base >= 80


def test_screener_partial_data_above_threshold():
    """有 6 條規則資料(>=50% coverage): 應給 bias 不 INSUFFICIENT_DATA。"""
    from unittest.mock import patch
    from agent import screener
    td = TickerData(
        ticker="MIDCO", sector="Technology",
        roe=0.20, gross_margin=0.40, net_margin=0.15, earn_growth=0.12,
        debt_equity=0.4, w52_pos=0.5,
        # fcf_margin / buyback_yield / fwd_pe / peg / berkshire 全缺
    )
    with patch("agent.screener.data_loader.load_ticker", return_value=td):
        s = screener.score("MIDCO")
    assert s.bias != "INSUFFICIENT_DATA"
    assert 50 <= s.coverage_pct < 100
