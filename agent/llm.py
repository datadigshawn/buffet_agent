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
- 管理層 (capital allocation 紀錄):context 若有 management 區塊就以那為主
  · BVPS 5y CAGR > 12% = Buffett 標準級複利機器
  · 留存效率 < 0.8 = 留存被消耗或大量買回 (Berkshire 重倉股例外)
  · 內建分級 A-D 是基於量化資料,LLM 自己的 management_grade 應該對齊或解釋差異
- Insider:context 若有 `insider` 區塊就引用 — Buffett 公開講過會看 Form 4
  · CEO/CFO 大量賣出 (insider_selling_spike) 是 yellow flag,行動建議要降信心
  · 內部人買入 (insider_buying_signal) 是 rare 正面訊號 — 比新聞更重要
  · 13D 出現 (activist_filing) 表示 5%+ 活躍機構介入,需要進一步研究
- 新聞:context 若有 `news` 區塊(近 7 天文章 sentiment + 重大事件)就引用具體事件
  · sentiment_trend=falling 是警訊;rising 是動能訊號
  · alert_type=news_negative_spike → 行動建議要加風險提示
  · 不要憑空講「最近新聞看好/看壞」 — 引用 material_events 裡的實際標題
- 護城河:context 若有 `moat` 結構化評分(5 類型 × 0-10 分 + 趨勢)就以那為主
  · 整體強度 ≥ 7 = 強;4-7 = 中等;< 4 = 弱
  · trend=widening 是強買進訊號(護城河擴張中)
  · trend=narrowing 是警訊(護城河收窄,Buffett 會避開)
  · LLM 自己的 moat_strength / moat_description 應對齊量化評分,不要憑空吐
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
- 行動建議考量目前股價/安全邊際 (context 裡有)
- 估值若有 ensemble (3 模型) 結果,優先看 consensus + mid 安全邊際,不要被單一極端值帶偏
  · 「3 模型都說便宜」訊號比「DCF 一個說便宜」強得多
  · 各模型差距大時 (low/high 差 50% 以上),提醒不確定性"""


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
    # P0-3: 優先用 ensemble (3 模型平均) 給 LLM 較不偏的估值視角
    ens = context.get("valuation_ensemble")
    if ens and ens.get("method_count", 0) > 0:
        parts.append(f"\n## 內在價值 (Ensemble × {ens['method_count']} 模型)")
        parts.append(f"- Consensus: **{ens['consensus']}**")
        low = ens.get("intrinsic_low")
        mid = ens.get("intrinsic_mid")
        high = ens.get("intrinsic_high")
        if mid is not None:
            parts.append(
                f"- 估值帶: low=${low:.2f} / mid=${mid:.2f} / high=${high:.2f}"
            )
        mos_mid = ens.get("mos_mid")
        if mos_mid is not None:
            parts.append(f"- 安全邊際 (mid): {mos_mid*100:+.1f}%")
        parts.append("- 各模型:")
        for c in ens.get("contributors", []):
            if c.get("intrinsic") is not None:
                parts.append(
                    f"  - {c['method']}: ${c['intrinsic']:.2f} ({c.get('note','')})"
                )
    else:
        # Fallback: 單一 DCF
        parts.append(f"\n## DCF 內在價值")
        iv = context.get("intrinsic_per_share")
        mos = context.get("margin_of_safety_pct")
        if iv is not None:
            parts.append(f"- 每股內在價值: ${iv:.2f}")
        if mos is not None:
            parts.append(f"- 安全邊際: {mos*100:+.1f}% ({'便宜' if mos > 0 else '偏貴'})")
    # P1-3.5: Insider 交易訊號 (Form 4 + 13D/G + 8-K)
    insider = context.get("insider")
    if insider and insider.get("transactions_count", 0) > 0:
        parts.append(f"\n## Insider 交易 (近 {insider.get('lookback_days', 60)} 天)")
        parts.append(
            f"- {insider['transactions_count']} 筆,"
            f"賣 ${insider.get('total_sell_value',0)/1e6:.1f}M / "
            f"買 ${insider.get('total_buy_value',0)/1e6:.1f}M"
        )
        if insider.get("exec_sell_value", 0) > 0:
            parts.append(f"- C-level 賣出: ${insider['exec_sell_value']/1e6:.1f}M")
        if insider.get("top_seller"):
            t = insider["top_seller"]
            parts.append(f"- 最大賣家: {t.get('name')} ({t.get('position')}) ${t.get('value',0)/1e6:.1f}M")
        if insider.get("sched_13d_count", 0) > 0:
            parts.append(f"- 🔥 13D 活躍機構介入: {insider['sched_13d_count']} 筆")
        if insider.get("alert_type"):
            parts.append(f"- ⚠️ alert: {insider['alert_type']}")

    # P1-3: 新聞訊號 + 重大事件
    news = context.get("news")
    if news and news.get("article_count_7d", 0) > 0:
        parts.append(f"\n## 近 7 天新聞訊號")
        parts.append(
            f"- 文章 {news['article_count_7d']} 篇,"
            f"其中重要快訊 {news.get('flash_count_7d', 0)} 篇"
        )
        if news.get("sentiment_avg_7d") is not None:
            parts.append(
                f"- 平均 sentiment: {news['sentiment_avg_7d']:+.2f} "
                f"({news.get('sentiment_trend','?')})"
            )
        if news.get("top_topics"):
            parts.append(f"- 熱門主題: {', '.join(news['top_topics'])}")
        if news.get("alert_type"):
            parts.append(f"- ⚠️ alert: {news['alert_type']}")
        if news.get("material_events"):
            parts.append("- 近期重要事件:")
            for ev in news["material_events"][:3]:
                title = (ev.get("title") or "")[:80]
                sent = ev.get("sentiment")
                sent_str = f" [{sent:+.1f}]" if sent is not None else ""
                parts.append(f"  · {title}{sent_str}")

    # P1-2: 護城河結構化評分 (LLM 應引用 5 類型分數 + 趨勢)
    moat = context.get("moat")
    if moat:
        parts.append(f"\n## 護城河結構化評分")
        parts.append(
            f"- 整體強度: {moat.get('overall_strength','?')} "
            f"({moat.get('overall_score', 0):.1f}/10)"
        )
        dom = moat.get("dominant_types") or []
        if dom:
            parts.append(f"- 主要類型: {', '.join(dom)}")
        parts.append(f"- 趨勢: {moat.get('trend','?')}")
        if moat.get("trend_evidence"):
            parts.append(f"  · {' / '.join(moat['trend_evidence'])}")
        for c in moat.get("components") or []:
            if c.get("score", 0) >= 3:    # 只列有意義的分數
                parts.append(
                    f"  · {c['moat_type']}: {c['score']:.1f}/10 ({c.get('rationale','')})"
                )

    # P1-1: 管理層 capital allocation 量化 (LLM 應引用,不要憑空猜測)
    mgmt = context.get("management")
    if mgmt:
        parts.append(f"\n## 管理層 (Capital Allocation 紀錄)")
        if mgmt.get("ceo_name"):
            parts.append(f"- CEO: {mgmt['ceo_name']} ({mgmt.get('ceo_title') or 'CEO'})")
        if mgmt.get("bvps_cagr_5y") is not None:
            parts.append(
                f"- BVPS 5 年 CAGR: {mgmt['bvps_cagr_5y']*100:+.1f}%"
            )
        if mgmt.get("retention_efficiency") is not None:
            parts.append(
                f"- 留存效率: {mgmt['retention_efficiency']:.2f}"
            )
        if mgmt.get("dividend_payout_ratio_5y") is not None:
            parts.append(
                f"- 5y 平均股利配發率: {mgmt['dividend_payout_ratio_5y']*100:.0f}%"
            )
        if mgmt.get("grade") and mgmt["grade"] != "?":
            parts.append(f"- Buffett 風格分級 (量化版): **{mgmt['grade']}**")
        for r in (mgmt.get("grade_reasons") or [])[:3]:
            parts.append(f"  · {r}")

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
            "max_tokens": 1500,
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


# ---------- Phase 5 P0-2: thesis writer ----------

THESIS_SYSTEM_PROMPT = """你是巴菲特紀律的投資論文 (thesis) 寫手。
任務:給定一檔股票的量化 + 定性資料,**寫出 200 字內的 BUY thesis**。
這是一份「之後要被自己檢驗的論文」 — 寫下今天為什麼值得買,以後若條件破了就要重新評估。

風格:
- 直接、具體、有出處 (引用 SEC 持續性、DCF 結果、Berkshire 持股等可驗證事實)
- 結構建議:1) 護城河本質 2) 量化證據 3) 風險與條件 4) 為何現在/或為何等待
- 用繁體中文
- **僅輸出 thesis 純文字,不要 JSON、不要 markdown、不要前後文字**"""


def write_thesis(ticker: str, verdict: dict) -> str | None:
    """LLM 寫初次 BUY 的投資論文。失敗回 None,呼叫端走 template fallback。"""
    backend = get_backend()
    if backend.name == "none":
        return None

    if not isinstance(backend, OpenRouterBackend):
        return None

    if not backend.api_key:
        return None

    user_msg = _build_thesis_prompt(ticker, verdict)
    body = {
        "model": backend.model,
        "messages": [
            {"role": "system", "content": THESIS_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.3,
        "max_tokens": 600,
        "reasoning": {"exclude": True},
    }
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {backend.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://buffetagent.netlify.app",
            "X-Title": "buffetAgent",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=backend.timeout) as resp:  # noqa: S310
            raw = resp.read()
    except (urllib.error.URLError, OSError) as e:
        log.warning("write_thesis network error for %s: %s", ticker, e)
        return None

    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return None

    content = (
        data.get("choices", [{}])[0].get("message", {}).get("content", "")
    )
    text = (content or "").strip()
    # 去掉可能的 markdown / quote
    text = re.sub(r"^```[a-z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text or None


def _build_thesis_prompt(ticker: str, verdict: dict) -> str:
    parts = [f"標的: **{ticker}** ({verdict.get('sector') or '未知產業'})"]
    parts.append(f"今天首次拿到 {verdict.get('bias','BUY')} 評分 {verdict.get('score','?')}/110")
    parts.append(f"分類: {verdict.get('industry_class', 'general')}")
    parts.append(f"\n## 量化")
    parts.append(f"- 通過規則: {', '.join(verdict.get('passed_rules') or [])}")
    parts.append(f"- 加分項: {', '.join(verdict.get('earned_bonuses') or []) or '無'}")
    parts.append(f"- 涵蓋率: {verdict.get('coverage_pct', '?')}%")
    if verdict.get("roe_consistency_10y") is not None:
        parts.append(f"- ROE 10 年持續達 15% 比例: {verdict['roe_consistency_10y']*100:.0f}%")
    parts.append(f"\n## 內在價值 (DCF)")
    if verdict.get("intrinsic_value_per_share") is not None:
        parts.append(f"- 每股內在價值: ${verdict['intrinsic_value_per_share']:.2f}")
    if verdict.get("current_price") is not None:
        parts.append(f"- 目前股價: ${verdict['current_price']:.2f}")
    if verdict.get("margin_of_safety_pct") is not None:
        parts.append(f"- 安全邊際: {verdict['margin_of_safety_pct']*100:+.1f}%")
    if verdict.get("berkshire_holds"):
        pct = verdict.get("berkshire_position_pct")
        if pct:
            parts.append(f"\n## Berkshire 持有 ({pct*100:.1f}% 組合)")
    if verdict.get("qualitative"):
        q = verdict["qualitative"]
        if q.get("moat_description"):
            parts.append(f"\n## 護城河 (LLM 已評估)")
            parts.append(q["moat_description"])
    parts.append("\n請寫 200 字以內的 BUY thesis,純文字無 JSON。")
    return "\n".join(parts)
