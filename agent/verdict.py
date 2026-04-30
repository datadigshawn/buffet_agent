"""合成完整 Buffett 評估結果。

整合:
- screener.score()  → 量化分數
- kb_retriever      → 引用知識庫
- 產出可給人讀的 markdown rationale
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

from . import screener
from . import kb_retriever
from . import dcf as dcf_mod
from . import llm as llm_mod


@dataclass
class Verdict:
    ticker: str
    bias: str                 # BUY / HOLD / WATCH / AVOID / OUT_OF_CIRCLE / INSUFFICIENT_DATA
    confidence: int           # 0-100
    score: screener.Score
    company_file: kb_retriever.KBNode | None
    related_concepts: list[kb_retriever.KBNode]
    guidebook: kb_retriever.KBNode | None
    opposing_flags: list[str]
    rationale_md: str
    intrinsic: dcf_mod.IntrinsicValue | None = None
    qualitative: llm_mod.QualitativeJudgment | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "bias": self.bias,
            "confidence": self.confidence,
            "total_score": self.score.total,
            "base": self.score.base,
            "bonus": self.score.bonus,
            "triggered_disqualifier": self.score.triggered_disqualifier,
            "passed_rules": [r.rule_id for r in self.score.rule_results if r.passed],
            "failed_rules": [r.rule_id for r in self.score.rule_results if not r.passed and not r.skipped],
            "skipped_rules": [r.rule_id for r in self.score.rule_results if r.skipped],
            "earned_bonuses": [b.rule_id for b in self.score.bonuses if b.earned],
            "company_kb_url": self.company_file.online_url if self.company_file else None,
            "related_concepts": [c.title for c in self.related_concepts],
            "opposing_flags": self.opposing_flags,
            "berkshire_holds": self.score.data.berkshire_holds if self.score.data else False,
            "berkshire_value_usd": self.score.data.berkshire_value_usd if self.score.data else None,
            "intrinsic": self.intrinsic.to_dict() if self.intrinsic else None,
            "qualitative": self.qualitative.to_dict() if self.qualitative else None,
        }


def _build_rationale_md(s: screener.Score, kb: dict,
                        intrinsic: dcf_mod.IntrinsicValue | None = None,
                        qualitative: llm_mod.QualitativeJudgment | None = None) -> str:
    parts = []
    parts.append(f"# 巴菲特 Agent — {s.ticker}\n")
    parts.append(f"**Bias**: {s.bias}  ·  **Score**: {s.total}/110 (base {s.base} + bonus {s.bonus})\n")
    if s.bias not in ("OUT_OF_CIRCLE", "INSUFFICIENT_DATA"):
        parts.append(f"**規則涵蓋率**: {s.coverage_pct}%\n")
    if s.data:
        parts.append(f"**Sector**: {s.data.sector or '未知'}  ·  **資料來源**: {s.data.source}\n")

    # OUT_OF_CIRCLE 直接顯示原因
    if s.bias == "OUT_OF_CIRCLE":
        parts.append(f"\n## ❌ 觸發硬性 disqualifier\n\n**{s.triggered_disqualifier}**\n")
        parts.append("\n依巴菲特原則,此 ticker 不在能力圈內或結構性風險過高,不予評估。\n")
        return "\n".join(parts)

    # INSUFFICIENT_DATA: 提示為何無法評
    if s.bias == "INSUFFICIENT_DATA":
        parts.append(f"\n## ⚠️ 資料不足\n\n規則涵蓋率僅 **{s.coverage_pct}%** (低於門檻 50%)。")
        parts.append("\n這通常是 ETF、新上市、或 yfinance 資料缺漏導致 — 巴菲特框架不適用。\n")

    # 通過的規則
    passed = [r for r in s.rule_results if r.passed]
    failed = [r for r in s.rule_results if not r.passed and not r.skipped]
    skipped = [r for r in s.rule_results if r.skipped]

    if passed:
        parts.append("\n## ✅ 通過的規則\n")
        for r in passed:
            actual = "" if r.actual is None else f" (actual={r.actual:.4f})" if isinstance(r.actual, float) else f" (actual={r.actual})"
            concept = f" → [[{r.source_concept}]]" if r.source_concept else ""
            parts.append(f"- **{r.rule_id}** {r.name}{actual} ✓ +{r.points}{concept}")

    if failed:
        parts.append("\n## ❌ 未通過的規則\n")
        for r in failed:
            actual = "" if r.actual is None else f" (actual={r.actual:.4f})"
            concept = f" → [[{r.source_concept}]]" if r.source_concept else ""
            parts.append(f"- **{r.rule_id}** {r.name}{actual}{concept}")

    if skipped:
        parts.append("\n## ⚠️ 缺值跳過\n")
        for r in skipped:
            parts.append(f"- **{r.rule_id}** {r.name} ({r.field}) — 資料源缺欄位")

    earned_bonus = [b for b in s.bonuses if b.earned]
    if earned_bonus:
        parts.append("\n## 🎁 加分項\n")
        for b in earned_bonus:
            parts.append(f"- **{b.rule_id}** {b.name} +{b.points}")

    # DCF 內在價值 (C1)
    if intrinsic:
        parts.append("\n## 💰 內在價值估算 (DCF)\n")
        parts.append(
            f"- 每股內在價值: **${intrinsic.intrinsic_per_share:,.2f}**"
        )
        if intrinsic.current_price:
            parts.append(f"- 目前股價: ${intrinsic.current_price:,.2f}")
        if intrinsic.margin_of_safety is not None:
            mos_pct = intrinsic.margin_of_safety * 100
            label = "🟢 便宜" if mos_pct > 25 else "🟡 接近合理" if mos_pct > 0 else "🔴 偏貴"
            parts.append(f"- 安全邊際: **{mos_pct:+.1f}%** ({label})")
        parts.append(
            f"- 假設: stage 1 成長 {intrinsic.stage1_growth*100:.1f}%、"
            f"折現率 {intrinsic.discount_rate*100:.1f}%、{intrinsic.note}"
        )

    # LLM 定性 (C3) — 在 DCF 之後、Berkshire 之前
    if qualitative and qualitative.is_available:
        parts.append(f"\n## 🧠 定性判斷 ({qualitative.model or qualitative.backend})\n")
        if qualitative.management_grade:
            parts.append(f"- 管理層評分: **{qualitative.management_grade}**")
        if qualitative.moat_strength:
            parts.append(f"- 護城河強度: **{qualitative.moat_strength}**")
        if qualitative.moat_description:
            parts.append(f"- 護城河說明: {qualitative.moat_description}")
        if qualitative.in_circle_of_competence is not None:
            mark = "✅ 在能力圈內" if qualitative.in_circle_of_competence else "⚠️ 能力圈外"
            parts.append(f"- 能力圈: {mark}")
        if qualitative.confidence is not None:
            parts.append(f"- LLM 信心: {qualitative.confidence*100:.0f}%")
        if qualitative.recommendation:
            parts.append(f"\n### 💡 行動建議\n\n> {qualitative.recommendation}\n")

    # Berkshire
    if s.data and s.data.berkshire_holds:
        v = s.data.berkshire_value_usd or 0
        parts.append(f"\n## 🏛️ Berkshire 持有\n\n價值約 **${v/1e9:.2f}B**")
        if s.data.berkshire_position_pct:
            parts.append(f"(占組合 {s.data.berkshire_position_pct*100:.1f}%)")

    # 知識庫引用
    if kb.get("company"):
        c = kb["company"]
        parts.append(f"\n## 📚 公司檔案\n\n[[{c.title}]] — [線上版本]({c.online_url})\n\n> {c.excerpt}\n")

    if kb.get("concepts"):
        parts.append("\n## 💡 相關概念\n")
        for cc in kb["concepts"][:5]:
            parts.append(f"- [[{cc.title}]] — [線上]({cc.online_url})")

    if kb.get("guidebook"):
        g = kb["guidebook"]
        parts.append(f"\n---\n參閱完整邏輯: [[{g.title}]] · [線上版]({g.online_url})\n")

    return "\n".join(parts)


def evaluate(ticker: str) -> Verdict:
    """主入口。"""
    s = screener.score(ticker)

    # 從觸發規則的 source_concept 反查 kb 概念
    if s.bias == "OUT_OF_CIRCLE":
        # 仍給最低限度的 KB 引用
        related_concepts_names = []
    else:
        related_concepts_names = list({r.source_concept for r in s.rule_results if r.source_concept})

    kb = kb_retriever.find_relevant(s.ticker, related_concepts_names)

    # opposing_flags
    flags: list[str] = []
    if s.triggered_disqualifier:
        flags.append(s.triggered_disqualifier)
    for r in s.rule_results:
        if not r.passed and not r.skipped:
            flags.append(f"{r.rule_id}: {r.name}")

    # DCF: 只對非 OUT/INSUFFICIENT 算
    intrinsic = None
    if s.bias not in ("OUT_OF_CIRCLE", "INSUFFICIENT_DATA"):
        try:
            current_price = s.data.price if s.data else None
            ind_class = s.data.industry_class if s.data else "general"
            intrinsic = dcf_mod.estimate(
                s.ticker, current_price=current_price, industry_class=ind_class
            )
        except Exception:  # noqa: BLE001
            # DCF 失敗不阻塞 verdict
            pass

    # LLM 定性判斷:只對 BUY/HOLD 跑 (省成本),C1 階段 backend=none 永遠回空
    qualitative = None
    if s.bias in ("BUY", "HOLD"):
        try:
            llm_context = _build_llm_context(s, kb, intrinsic)
            qualitative = llm_mod.judge(s.ticker, llm_context)
        except Exception as e:  # noqa: BLE001
            pass

    # confidence: 用 coverage 直接驅動 + bias 強度
    # OUT_OF_CIRCLE / INSUFFICIENT_DATA 都給低 confidence
    if s.bias in ("OUT_OF_CIRCLE", "INSUFFICIENT_DATA"):
        confidence = 0
    else:
        # 高分 + 高涵蓋率 = 高 confidence
        # confidence = 0.7 × normalized_total + 0.3 × coverage_pct
        confidence = round(s.total * 0.7 + s.coverage_pct * 0.3)
        confidence = max(0, min(100, confidence))

    return Verdict(
        ticker=s.ticker,
        bias=s.bias,
        confidence=confidence,
        score=s,
        company_file=kb.get("company"),
        related_concepts=kb.get("concepts") or [],
        guidebook=kb.get("guidebook"),
        opposing_flags=flags,
        rationale_md=_build_rationale_md(s, kb, intrinsic, qualitative),
        intrinsic=intrinsic,
        qualitative=qualitative,
    )


def _build_llm_context(
    s: screener.Score,
    kb: dict,
    intrinsic: dcf_mod.IntrinsicValue | None,
) -> dict:
    """組裝給 LLM backend 的上下文 (C3 才會真用到)。"""
    d = s.data
    return {
        "ticker": s.ticker,
        "sector": d.sector if d else None,
        "industry_class": d.industry_class if d else "general",
        "score": s.total,
        "bias": s.bias,
        "passed_rules": [r.rule_id for r in s.rule_results if r.passed],
        "failed_rules": [r.rule_id for r in s.rule_results if not r.passed and not r.skipped],
        "roe_consistency_10y": d.roe_consistency_10y if d else None,
        "owner_earnings_5y": d.fcf_margin if d else None,
        "intrinsic_per_share": (
            intrinsic.intrinsic_per_share if intrinsic else None
        ),
        "margin_of_safety_pct": (
            intrinsic.margin_of_safety if intrinsic else None
        ),
        "company_kb_excerpt": (
            kb["company"].excerpt if kb.get("company") else None
        ),
        "related_concepts": [c.title for c in (kb.get("concepts") or [])],
    }
