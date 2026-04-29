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


@dataclass
class Verdict:
    ticker: str
    bias: str                 # BUY / HOLD / WATCH / AVOID / OUT_OF_CIRCLE
    confidence: int           # 0-100
    score: screener.Score
    company_file: kb_retriever.KBNode | None
    related_concepts: list[kb_retriever.KBNode]
    guidebook: kb_retriever.KBNode | None
    opposing_flags: list[str]
    rationale_md: str

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
        }


def _build_rationale_md(s: screener.Score, kb: dict) -> str:
    parts = []
    parts.append(f"# 巴菲特 Agent — {s.ticker}\n")
    parts.append(f"**Bias**: {s.bias}  ·  **Score**: {s.total}/110 (base {s.base} + bonus {s.bonus})\n")
    if s.data:
        parts.append(f"**Sector**: {s.data.sector or '未知'}  ·  **資料來源**: {s.data.source}\n")

    # OUT_OF_CIRCLE 直接顯示原因
    if s.bias == "OUT_OF_CIRCLE":
        parts.append(f"\n## ❌ 觸發硬性 disqualifier\n\n**{s.triggered_disqualifier}**\n")
        parts.append("\n依巴菲特原則,此 ticker 不在能力圈內或結構性風險過高,不予評估。\n")
        return "\n".join(parts)

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

    # confidence: total + 缺值懲罰
    skipped_count = sum(1 for r in s.rule_results if r.skipped)
    confidence_penalty = skipped_count * 5
    confidence = max(0, min(100, s.total - confidence_penalty))
    if s.bias == "OUT_OF_CIRCLE":
        confidence = 0

    return Verdict(
        ticker=s.ticker,
        bias=s.bias,
        confidence=confidence,
        score=s,
        company_file=kb.get("company"),
        related_concepts=kb.get("concepts") or [],
        guidebook=kb.get("guidebook"),
        opposing_flags=flags,
        rationale_md=_build_rationale_md(s, kb),
    )
