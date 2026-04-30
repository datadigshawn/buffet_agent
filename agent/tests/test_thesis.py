"""thesis.py 單元測試 — 不依賴外部資料 / 網路。"""
from __future__ import annotations

import pytest
from pathlib import Path

from agent import thesis


@pytest.fixture(autouse=True)
def _isolated_theses_dir(tmp_path, monkeypatch):
    """每個測試用獨立 tmp 目錄,避免影響真 output/theses/。"""
    monkeypatch.setattr(thesis, "THESES_DIR", tmp_path / "theses")
    yield


def _verdict(ticker: str, bias: str = "BUY", score: int = 80, **extra) -> dict:
    base = {
        "ticker": ticker, "bias": bias, "score": score,
        "passed_rules": ["R1", "R2"], "earned_bonuses": ["B1"],
        "coverage_pct": 95, "berkshire_holds": False,
        "roe_consistency_10y": None, "intrinsic_value_per_share": None,
        "margin_of_safety_pct": None, "industry_class": "general",
    }
    base.update(extra)
    return base


# ---------- Condition checking ----------

def test_check_condition_passes_when_above_threshold():
    c = thesis.Condition("score", ">=", 60, "min score")
    assert thesis._check_condition(c, {"score": 80}) is True


def test_check_condition_fails_when_below():
    c = thesis.Condition("score", ">=", 60, "min score")
    assert thesis._check_condition(c, {"score": 40}) is False


def test_check_condition_missing_value_returns_true():
    """缺值不應觸發 thesis_broken (避免抓不到資料就誤觸發)。"""
    c = thesis.Condition("roe_consistency_10y", ">=", 0.6, "ROE")
    assert thesis._check_condition(c, {"score": 80}) is True


def test_check_condition_string_eq():
    c = thesis.Condition("bias", "!=", "OUT_OF_CIRCLE", "no disqualifier")
    assert thesis._check_condition(c, {"bias": "BUY"}) is True
    assert thesis._check_condition(c, {"bias": "OUT_OF_CIRCLE"}) is False


# ---------- default_conditions ----------

def test_default_conditions_minimum_set():
    """每個 thesis 至少含 score / bias 三條基本條件。"""
    v = _verdict("X")
    conds = thesis.default_conditions(v)
    metrics = {c.metric for c in conds}
    assert "score" in metrics
    assert "bias" in metrics  # 含 != OUT 與 != INSUFFICIENT
    assert sum(1 for c in conds if c.metric == "bias") == 2


def test_default_conditions_adds_roe_when_high():
    """ROE 持續性 >= 80% 時,加進 thesis 當條件。"""
    v = _verdict("X", roe_consistency_10y=0.95)
    conds = thesis.default_conditions(v)
    roe_c = next(c for c in conds if c.metric == "roe_consistency_10y")
    assert roe_c.op == ">="
    assert roe_c.value == 0.6


def test_default_conditions_skips_roe_when_low():
    """ROE < 0.8 時不加 (沒當底線可守)。"""
    v = _verdict("X", roe_consistency_10y=0.5)
    conds = thesis.default_conditions(v)
    roe_metrics = [c for c in conds if c.metric == "roe_consistency_10y"]
    assert roe_metrics == []


def test_default_conditions_adds_berkshire_when_held():
    v = _verdict("X", berkshire_holds=True)
    conds = thesis.default_conditions(v)
    brk = next(c for c in conds if c.metric == "berkshire_holds")
    assert brk.value is True


# ---------- process / verify ----------

def test_first_buy_creates_new_thesis():
    v = _verdict("AAPL", bias="BUY", score=85, roe_consistency_10y=1.0)
    status = thesis.process(v, llm_writer=None)
    assert status.state == "new"
    assert status.thesis is not None
    assert status.thesis.ticker == "AAPL"
    assert status.thesis.score_at_buy == 85
    assert status.thesis.written_by == "default"   # 沒給 LLM
    # 檔案有寫
    assert thesis.thesis_path("AAPL").exists()


def test_first_non_buy_skipped():
    v = _verdict("X", bias="HOLD")
    status = thesis.process(v)
    assert status.state == "skipped"
    assert status.thesis is None
    assert not thesis.thesis_path("X").exists()


def test_existing_thesis_still_valid():
    """既有 thesis 對符合條件的 verdict 應 valid。"""
    v_buy = _verdict("AAPL", bias="BUY", score=85)
    thesis.process(v_buy)
    # 第二天還是 BUY,分數沒變
    status = thesis.process(v_buy)
    assert status.state == "valid"
    assert status.broken_conditions == []


def test_existing_thesis_breaks_on_score_drop():
    """分數從 85 跌到 50 → 違反 score >= 60。"""
    v_buy = _verdict("AAPL", bias="BUY", score=85)
    thesis.process(v_buy)
    v_drop = _verdict("AAPL", bias="WATCH", score=50)
    status = thesis.process(v_drop)
    assert status.state == "broken"
    assert any("score" in c for c in status.broken_conditions)


def test_existing_thesis_breaks_on_disqualifier():
    """進入 OUT_OF_CIRCLE → 違反 bias != OUT。"""
    v_buy = _verdict("AAPL", bias="BUY", score=85)
    thesis.process(v_buy)
    v_out = _verdict("AAPL", bias="OUT_OF_CIRCLE", score=0)
    status = thesis.process(v_out)
    assert status.state == "broken"
    assert any("OUT_OF_CIRCLE" in c for c in status.broken_conditions)


def test_thesis_persists_across_loads():
    """寫入後從硬碟讀回來內容一致。"""
    v = _verdict("X", bias="BUY", score=70, berkshire_holds=True)
    status1 = thesis.process(v)
    loaded = thesis.load_thesis("X")
    assert loaded is not None
    assert loaded.score_at_buy == 70
    assert any(c.metric == "berkshire_holds" for c in loaded.required_conditions)


# ---------- LLM writer wiring ----------

def test_llm_writer_used_when_provided():
    """提供 llm_writer 應拿到 LLM 文字而非 template。"""
    def fake_llm(ticker, ctx):
        return f"LLM thesis for {ticker}: 護城河強..."
    v = _verdict("AAPL", bias="BUY", score=85)
    status = thesis.process(v, llm_writer=fake_llm)
    assert status.thesis.written_by == "llm"
    assert "LLM thesis for AAPL" in status.thesis.thesis_text


def test_llm_writer_failure_falls_back_to_template():
    """LLM 拋例外應 fallback,不影響 thesis 建立。"""
    def bad_llm(ticker, ctx):
        raise RuntimeError("api down")
    v = _verdict("X", bias="BUY", score=70)
    status = thesis.process(v, llm_writer=bad_llm)
    assert status.thesis.written_by == "default"  # template fallback
    assert status.state == "new"
    assert status.thesis.thesis_text  # 有內容


def test_llm_writer_returns_none_falls_back():
    """LLM 回 None 也要 fallback。"""
    def empty_llm(ticker, ctx):
        return None
    v = _verdict("X", bias="BUY", score=70)
    status = thesis.process(v, llm_writer=empty_llm)
    assert status.thesis.written_by == "default"
