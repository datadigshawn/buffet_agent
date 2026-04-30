"""LLM 定性判斷介面層 (C1: stub,C3: 實接 OpenRouter / Minimax M2.5)。

目的:
- 對 BUY/HOLD 候選人,要求 LLM 讀我們組好的量化 + KB context,輸出:
  1. 管理層評分 (A/B/C/D)
  2. 護城河描述 + 強度 (strong / moderate / weak)
  3. 能力圈判定 (in / out / unsure)
  4. 自然語言行動建議 (繁中 1-2 句,給戰情室 lobby 卡用)

設計理由:
- 抽出到獨立 module,verdict.py 只透過 protocol 呼叫,future-proof
- BUFFET_LLM_BACKEND env 切換 (none / openrouter)
- 預設 "none" — CI / 本機測試永遠跑得動,不會被網路 / API key 卡住
- OpenRouter backend 沿用 war-room 的 minimax/minimax-m2.5 預設模型
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

log = logging.getLogger(__name__)


@dataclass
class QualitativeJudgment:
    """LLM 定性判斷結果。"""
    management_grade: str | None = None   # "A" / "B" / "C" / "D"
    moat_description: str | None = None    # 一段話
    moat_strength: str | None = None       # "strong" / "moderate" / "weak"
    in_circle_of_competence: bool | None = None
    recommendation: str | None = None      # 繁中行動建議 (1-2 句,C4)
    confidence: float | None = None        # 0-1
    reasoning: str | None = None           # LLM 推理
    backend: str = "none"                  # 哪個 backend 產的 (debug 用)
    model: str | None = None               # 實際模型名 (e.g. minimax/minimax-m2.5)
    cost_usd: float | None = None          # 該次呼叫的 token 成本 (有則填)
    error: str | None = None               # 失敗時的錯誤訊息

    def to_dict(self) -> dict[str, Any]:
        return {
            "management_grade": self.management_grade,
            "moat_description": self.moat_description,
            "moat_strength": self.moat_strength,
            "in_circle_of_competence": self.in_circle_of_competence,
            "recommendation": self.recommendation,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "backend": self.backend,
            "model": self.model,
            "error": self.error,
        }

    @property
    def is_available(self) -> bool:
        return self.management_grade is not None or self.moat_description is not None


class LLMBackend(Protocol):
    """所有 backend 要實作這個介面。"""
    name: str

    def judge(self, ticker: str, context: dict) -> QualitativeJudgment:
        ...


class NullBackend:
    """預設 backend — 回空結果 (永遠不打 API)。

    C1 階段所有 ticker 都走這條,verdict 仍能完整輸出,只是 qualitative 為 null。
    C3 切換成 HermesBackend / MinimaxBackend 時這個依然作為 fallback。
    """
    name = "none"

    def judge(self, ticker: str, context: dict) -> QualitativeJudgment:
        return QualitativeJudgment(backend=self.name)


SYSTEM_PROMPT = """你是巴菲特量化篩選 agent 的定性判斷 LLM。
給定一檔股票的量化分析結果與基本面 context,你要嚴格用巴菲特投資哲學評估:
- 管理層 (Buffett 看 capital allocation 紀錄、誠信、是否把股東當合夥人)
- 護城河 (品牌定價權、規模經濟、轉換成本、網路效應、政府特許 — 任一即可)
- 能力圈 (是否屬於可預測的生意 — 巴菲特避開高科技、生物科技投機、加密)
- 行動建議 (給戰情室 lobby 卡用,2-3 句繁中,務實具體)

回應格式必須是嚴格 JSON,不要任何前後文字、不要 markdown code block:

{
  "management_grade": "A" | "B" | "C" | "D",
  "moat_description": "一段話描述護城河 (繁中,30-60 字)",
  "moat_strength": "strong" | "moderate" | "weak",
  "in_circle_of_competence": true | false,
  "recommendation": "繁中 2-3 句行動建議,務實具體",
  "confidence": 0.0 ~ 1.0,
  "reasoning": "繁中 1-2 句說明你的整體判斷"
}

風格:
- 直接,不給「不構成投資建議」類廢話
- 沒把握就降 confidence,不要編造
- 行動建議考量目前股價/安全邊際 (context 裡有)"""


def _build_user_prompt(ticker: str, context: dict) -> str:
    """把 context dict 序列化成 LLM prompt user message。"""
    parts = [f"標的: **{ticker}** ({context.get('sector') or '未知'})"]
    parts.append(f"行業分類: {context.get('industry_class', 'general')}")
    parts.append(f"\n## 量化分數")
    parts.append(f"- Buffett score: {context.get('score')}/110, bias: {context.get('bias')}")
    parts.append(f"- 通過規則: {', '.join(context.get('passed_rules') or [])}")
    parts.append(f"- 未過規則: {', '.join(context.get('failed_rules') or []) or '無'}")
    parts.append(f"\n## SEC 持續性")
    roe10 = context.get("roe_consistency_10y")
    if roe10 is not None:
        parts.append(f"- ROE 10 年達 15% 比例: {roe10*100:.0f}%")
    oe = context.get("owner_earnings_5y")
    if oe is not None:
        parts.append(f"- 5 年平均 owner earnings margin: {oe*100:.1f}%")
    parts.append(f"\n## DCF 內在價值")
    iv = context.get("intrinsic_per_share")
    mos = context.get("margin_of_safety_pct")
    if iv is not None:
        parts.append(f"- 每股內在價值: ${iv:.2f}")
    if mos is not None:
        parts.append(f"- 安全邊際: {mos*100:+.1f}% ({'便宜' if mos > 0 else '偏貴'})")
    if context.get("company_kb_excerpt"):
        parts.append(f"\n## 公司檔案摘要 (來自巴菲特知識庫)")
        parts.append(context["company_kb_excerpt"])
    if context.get("related_concepts"):
        parts.append(f"\n## 相關 Buffett 概念: {', '.join(context['related_concepts'])}")
    parts.append("\n請輸出 JSON。")
    return "\n".join(parts)


def _parse_json_response(content: str) -> dict | None:
    """從 LLM 回應抽出 JSON dict。容忍前後雜訊與 ```json fence。"""
    if not content:
        return None
    # 清掉 markdown code fence
    s = content.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    # 嘗試直接 parse
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # 退一步: 找第一個 { 到最後一個 }
    start = s.find("{")
    end = s.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(s[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


class OpenRouterBackend:
    """經 OpenRouter 呼叫,預設 minimax/minimax-m2.5。

    Env vars:
      OPENROUTER_API_KEY   (required)
      BUFFET_LLM_MODEL     (default: minimax/minimax-m2.5)
      BUFFET_LLM_TIMEOUT   (default: 60 秒)
    """
    name = "openrouter"

    def __init__(self) -> None:
        self.api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        self.model = os.environ.get("BUFFET_LLM_MODEL", "minimax/minimax-m2.5").strip()
        self.timeout = int(os.environ.get("BUFFET_LLM_TIMEOUT", "60"))

    def judge(self, ticker: str, context: dict) -> QualitativeJudgment:
        if not self.api_key:
            return QualitativeJudgment(
                backend=self.name, model=self.model,
                error="OPENROUTER_API_KEY not set",
            )

        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(ticker, context)},
            ],
            "temperature": 0.3,
            "max_tokens": 800,
            # MiniMax M2.5 預設會吐 reasoning,要 exclude 才直接拿 content
            "reasoning": {"exclude": True},
        }
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://buffetagent.netlify.app",
                "X-Title": "buffetAgent",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                raw = resp.read()
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")[:400]
            return QualitativeJudgment(
                backend=self.name, model=self.model,
                error=f"HTTP {e.code}: {err_body}",
            )
        except (urllib.error.URLError, OSError) as e:
            return QualitativeJudgment(
                backend=self.name, model=self.model, error=f"network: {e}",
            )

        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as e:
            return QualitativeJudgment(
                backend=self.name, model=self.model, error=f"json decode: {e}",
            )

        content = (
            data.get("choices", [{}])[0].get("message", {}).get("content", "")
        )
        parsed = _parse_json_response(content)
        if not parsed:
            return QualitativeJudgment(
                backend=self.name, model=self.model,
                error="LLM did not return parseable JSON",
                reasoning=content[:300] if content else None,
            )

        # 解析欄位 (LLM 可能漏欄位,寬鬆處理)
        return QualitativeJudgment(
            management_grade=parsed.get("management_grade"),
            moat_description=parsed.get("moat_description"),
            moat_strength=parsed.get("moat_strength"),
            in_circle_of_competence=parsed.get("in_circle_of_competence"),
            recommendation=parsed.get("recommendation"),
            confidence=parsed.get("confidence"),
            reasoning=parsed.get("reasoning"),
            backend=self.name,
            model=self.model,
        )


_BACKEND: LLMBackend | None = None


def get_backend() -> LLMBackend:
    """取目前的 LLM backend (lazy init,根據環境變數)。"""
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND
    backend_name = os.environ.get("BUFFET_LLM_BACKEND", "none").lower()
    if backend_name == "openrouter":
        _BACKEND = OpenRouterBackend()
    else:
        # 預設 / 不認識的 backend → null (測試 / CI / 沒 API key 都安全)
        _BACKEND = NullBackend()
    return _BACKEND


def reset_backend() -> None:
    """測試用:重設快取的 backend 實例。"""
    global _BACKEND
    _BACKEND = None


def judge(ticker: str, context: dict) -> QualitativeJudgment:
    """主入口:給 ticker + 上下文,要 backend 做定性判斷。

    `context` 預期內容(C3 才會真用到):
        - sector / industry_class
        - 量化分數 (score, bias, passed_rules)
        - SEC 持續性指標 (roe_consistency_10y, owner_earnings_5y)
        - DCF 結果 (intrinsic_per_share, margin_of_safety)
        - KB 概念清單 (related_concepts)
        - 公司檔內容 (company_kb_excerpt)
        - 最近 10-K MD&A 段落 (10k_excerpt) — C3 才注入
    """
    return get_backend().judge(ticker, context)
