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
    assert len(r["soft_bonuses"]) == 5


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
