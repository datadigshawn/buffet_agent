"""DCF + LLM stub 測試 — 不依賴網路。"""
from __future__ import annotations

import pytest

from agent import dcf, llm


def _facts(concepts: dict[str, dict]) -> dict:
    return {"facts": {"us-gaap": {n: {"units": u} for n, u in concepts.items()}}}


def _row(year: int, val: float, *, form: str = "10-K", fp: str = "FY",
         filed: str | None = None) -> dict:
    return {
        "fy": year, "val": val, "form": form, "fp": fp,
        "filed": filed or f"{year + 1}-02-01",
        "start": f"{year}-01-01", "end": f"{year}-12-31",
    }


# ---------- DCF ----------

def test_dcf_basic_estimate(monkeypatch):
    """完整資料 → 應給出 intrinsic_per_share。"""
    facts = _facts({
        "NetCashProvidedByUsedInOperatingActivities": {
            "USD": [_row(2020 + i, 200 + i * 20) for i in range(5)]
        },
        "PaymentsToAcquirePropertyPlantAndEquipment": {
            "USD": [_row(2020 + i, 50) for i in range(5)]
        },
        "CommonStockSharesOutstanding": {
            "shares": [_row(2024, 1_000_000_000)]
        },
    })
    monkeypatch.setattr(dcf.sec_api, "get_facts", lambda t: facts)
    iv = dcf.estimate("X", current_price=100.0)
    assert iv is not None
    # OE = (200-50)+(220-50)+(240-50)+(260-50)+(280-50) = 150+170+190+210+230, avg = 190
    assert iv.base_owner_earnings == pytest.approx(190.0)
    assert iv.intrinsic_per_share > 0
    assert iv.margin_of_safety is not None


def test_dcf_returns_none_no_facts(monkeypatch):
    monkeypatch.setattr(dcf.sec_api, "get_facts", lambda t: None)
    assert dcf.estimate("X", current_price=10.0) is None


def test_dcf_returns_none_negative_owner_earnings(monkeypatch):
    """5 年平均 OE 為負 (虧損公司) → DCF 不適用。"""
    facts = _facts({
        "NetCashProvidedByUsedInOperatingActivities": {
            "USD": [_row(2020 + i, -100) for i in range(5)]
        },
        "PaymentsToAcquirePropertyPlantAndEquipment": {
            "USD": [_row(2020 + i, 50) for i in range(5)]
        },
        "CommonStockSharesOutstanding": {
            "shares": [_row(2024, 1_000_000_000)]
        },
    })
    monkeypatch.setattr(dcf.sec_api, "get_facts", lambda t: facts)
    assert dcf.estimate("X", current_price=10.0) is None


def test_dcf_growth_capped_at_max(monkeypatch):
    """成長率超過上限 (15%) 應 cap 到上限。"""
    facts = _facts({
        # OE 每年翻倍 → CAGR ~100%
        "NetCashProvidedByUsedInOperatingActivities": {
            "USD": [_row(2020 + i, 100 * 2**i) for i in range(5)]
        },
        "PaymentsToAcquirePropertyPlantAndEquipment": {
            "USD": [_row(2020 + i, 0) for i in range(5)]
        },
        "CommonStockSharesOutstanding": {
            "shares": [_row(2024, 1_000_000_000)]
        },
    })
    monkeypatch.setattr(dcf.sec_api, "get_facts", lambda t: facts)
    iv = dcf.estimate("X", current_price=10.0)
    assert iv is not None
    assert iv.stage1_growth == dcf.MAX_STAGE1_GROWTH


def test_dcf_margin_of_safety_calc(monkeypatch):
    """確認 MOS 計算公式: (intrinsic - price) / intrinsic。"""
    facts = _facts({
        "NetCashProvidedByUsedInOperatingActivities": {
            "USD": [_row(2020 + i, 100) for i in range(5)]
        },
        "PaymentsToAcquirePropertyPlantAndEquipment": {
            "USD": [_row(2020 + i, 0) for i in range(5)]
        },
        "CommonStockSharesOutstanding": {
            "shares": [_row(2024, 1_000_000)]
        },
    })
    monkeypatch.setattr(dcf.sec_api, "get_facts", lambda t: facts)
    iv = dcf.estimate("X", current_price=500.0)
    assert iv is not None
    expected_mos = (iv.intrinsic_per_share - 500.0) / iv.intrinsic_per_share
    assert iv.margin_of_safety == pytest.approx(expected_mos)


# ---------- LLM stub ----------

def test_llm_default_backend_returns_empty_judgment(monkeypatch):
    """預設 backend = none → 永遠回空 (不打 API)。"""
    monkeypatch.delenv("BUFFET_LLM_BACKEND", raising=False)
    llm.reset_backend()
    j = llm.judge("AAPL", {})
    assert j.management_grade is None
    assert j.moat_description is None
    assert j.in_circle_of_competence is None
    assert j.is_available is False
    assert j.backend == "none"


def test_llm_backend_env_override(monkeypatch):
    """unknown backend name 應 fallback 到 NullBackend。"""
    monkeypatch.setenv("BUFFET_LLM_BACKEND", "unknown_backend_xyz")
    llm.reset_backend()
    j = llm.judge("AAPL", {})
    assert j.backend == "none"


def test_llm_judgment_to_dict():
    j = llm.QualitativeJudgment(
        management_grade="A",
        moat_description="strong network effects",
        moat_strength="strong",
        in_circle_of_competence=True,
        confidence=0.85,
        backend="openrouter",
    )
    d = j.to_dict()
    assert d["management_grade"] == "A"
    assert d["moat_strength"] == "strong"
    assert d["backend"] == "openrouter"
    assert j.is_available is True


# ---------- OpenRouter backend ----------

def _fake_openrouter_response(content: str) -> bytes:
    """模擬 OpenRouter chat/completions response。"""
    import json as _json
    return _json.dumps({
        "choices": [{"message": {"content": content}}],
        "usage": {"total_tokens": 200},
    }).encode("utf-8")


class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def read(self): return self._body


def test_openrouter_parses_json_response(monkeypatch):
    """LLM 回 valid JSON → 對應欄位填入 QualitativeJudgment。"""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("BUFFET_LLM_BACKEND", "openrouter")
    llm.reset_backend()

    fake_content = (
        '{"management_grade":"A",'
        '"moat_description":"網路效應 + 規模經濟",'
        '"moat_strength":"strong",'
        '"in_circle_of_competence":true,'
        '"recommendation":"目前估值偏高,等回檔 10% 再考慮加碼。",'
        '"confidence":0.85,'
        '"reasoning":"持續性 1.0、ROIC 35%,經典 Buffett 標的"}'
    )
    monkeypatch.setattr(
        llm.urllib.request, "urlopen",
        lambda *a, **kw: _FakeResp(_fake_openrouter_response(fake_content)),
    )

    j = llm.judge("V", {"score": 99, "bias": "BUY"})
    assert j.management_grade == "A"
    assert j.moat_strength == "strong"
    assert j.in_circle_of_competence is True
    assert "回檔" in j.recommendation
    assert j.confidence == 0.85
    assert j.backend == "openrouter"


def test_openrouter_handles_markdown_fenced_json(monkeypatch):
    """LLM 把 JSON 包在 ```json fence 裡 → 也要能解析。"""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("BUFFET_LLM_BACKEND", "openrouter")
    llm.reset_backend()

    fake_content = '```json\n{"management_grade":"B","recommendation":"觀察。"}\n```'
    monkeypatch.setattr(
        llm.urllib.request, "urlopen",
        lambda *a, **kw: _FakeResp(_fake_openrouter_response(fake_content)),
    )
    j = llm.judge("X", {"score": 60, "bias": "HOLD"})
    assert j.management_grade == "B"
    assert j.recommendation == "觀察。"


def test_openrouter_no_api_key_returns_error(monkeypatch):
    """沒設 OPENROUTER_API_KEY → error,不打 API。"""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("BUFFET_LLM_BACKEND", "openrouter")
    llm.reset_backend()
    j = llm.judge("X", {})
    assert j.error and "API_KEY" in j.error
    assert j.management_grade is None


def test_openrouter_handles_unparseable_response(monkeypatch):
    """LLM 回不是 JSON → error 但不爆炸。"""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("BUFFET_LLM_BACKEND", "openrouter")
    llm.reset_backend()
    monkeypatch.setattr(
        llm.urllib.request, "urlopen",
        lambda *a, **kw: _FakeResp(_fake_openrouter_response("這不是 JSON")),
    )
    j = llm.judge("X", {})
    assert j.error and "JSON" in j.error
    assert j.management_grade is None


def test_build_user_prompt_contains_key_fields():
    """確認 prompt 包含關鍵 context。"""
    ctx = {
        "ticker": "AAPL",
        "sector": "Technology",
        "industry_class": "general",
        "score": 85,
        "bias": "BUY",
        "passed_rules": ["R1", "R2"],
        "failed_rules": ["R5"],
        "roe_consistency_10y": 1.0,
        "owner_earnings_5y": 0.25,
        "intrinsic_per_share": 184.0,
        "margin_of_safety_pct": -0.47,
        "company_kb_excerpt": "蘋果是巴菲特組合最大持股...",
        "related_concepts": ["經濟商譽", "安全邊際"],
    }
    prompt = llm._build_user_prompt("AAPL", ctx)
    assert "AAPL" in prompt
    assert "85/110" in prompt
    assert "R1, R2" in prompt
    assert "100%" in prompt  # ROE consistency
    assert "184" in prompt  # intrinsic
    assert "經濟商譽" in prompt
