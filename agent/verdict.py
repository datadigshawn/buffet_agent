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
from . import valuation as valuation_mod
from . import management as management_mod
from . import moat as moat_mod
from . import news_signals as news_mod
from . import insider_signals as insider_mod
from .sources import sec as sec_api
from .sources import news as news_src


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
    valuation: valuation_mod.EnsembleValuation | None = None
    management: management_mod.ManagementProfile | None = None
    moat: moat_mod.MoatProfile | None = None
    news: news_mod.NewsSignals | None = None
    insider: insider_mod.InsiderSignals | None = None
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
            "valuation": self.valuation.to_dict() if self.valuation else None,
            "management": self.management.to_dict() if self.management else None,
            "moat": self.moat.to_dict() if self.moat else None,
            "news": self.news.to_dict() if self.news else None,
            "insider": self.insider.to_dict() if self.insider else None,
            "qualitative": self.qualitative.to_dict() if self.qualitative else None,
        }


def _build_rationale_md(s: screener.Score, kb: dict,
                        intrinsic: dcf_mod.IntrinsicValue | None = None,
                        qualitative: llm_mod.QualitativeJudgment | None = None,
                        valuation: valuation_mod.EnsembleValuation | None = None,
                        management: management_mod.ManagementProfile | None = None,
                        moat: moat_mod.MoatProfile | None = None,
                        news: news_mod.NewsSignals | None = None,
                        insider: insider_mod.InsiderSignals | None = None) -> str:
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

    # P0-3 估值 ensemble (三模型平均)
    if valuation and valuation.method_count > 0:
        consensus_label = {
            "very_cheap": "🟢🟢 極便宜",
            "cheap": "🟢 便宜",
            "fair": "🟡 接近合理",
            "expensive": "🔴 偏貴",
            "very_expensive": "🔴🔴 嚴重高估",
            "uncertain": "⚪ 不確定",
        }.get(valuation.consensus, "⚪ 不確定")
        parts.append(f"\n## 💰 內在價值估算 (Ensemble × {valuation.method_count} 模型)\n")
        parts.append(f"- Consensus: **{consensus_label}**")
        if valuation.intrinsic_low is not None:
            parts.append(
                f"- 估值帶: ${valuation.intrinsic_low:.2f} (low) / "
                f"**${valuation.intrinsic_mid:.2f}** (mid) / "
                f"${valuation.intrinsic_high:.2f} (high)"
            )
        if valuation.current_price:
            parts.append(f"- 目前股價: ${valuation.current_price:.2f}")
        if valuation.mos_mid is not None:
            parts.append(
                f"- 安全邊際 (mid intrinsic): **{valuation.mos_mid*100:+.1f}%**"
            )
        parts.append("\n各模型細節:")
        for c in valuation.contributors:
            if c.intrinsic_per_share is not None:
                parts.append(
                    f"  - **{c.method}**: ${c.intrinsic_per_share:.2f}"
                    + (f" (MOS {c.margin_of_safety*100:+.1f}%)"
                       if c.margin_of_safety is not None else "")
                    + f" — {c.note}"
                )
            else:
                parts.append(f"  - **{c.method}**: (略過 — {c.note})")
    elif intrinsic:
        # Fallback: 沒 ensemble 但有單獨 DCF (相容性)
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

    # P1-3.5: Insider 交易訊號
    if insider and insider.transactions_count > 0:
        parts.append(f"\n## 🤵 Insider 交易訊號 (近 {insider.lookback_days} 天)\n")
        parts.append(
            f"- 交易筆數: {insider.transactions_count} "
            f"(賣出 ${insider.total_sell_value/1e6:.1f}M / 買入 ${insider.total_buy_value/1e6:.1f}M)"
        )
        if insider.exec_sell_value > 0:
            parts.append(f"- C-level 賣出: **${insider.exec_sell_value/1e6:.1f}M**")
        if insider.top_seller:
            t = insider.top_seller
            parts.append(
                f"- 最大賣家: **{t.get('name','?')}** ({t.get('position','')}) "
                f"${t.get('value',0)/1e6:.2f}M @ {t.get('date','')}"
            )
        if insider.top_buyer:
            t = insider.top_buyer
            parts.append(
                f"- 最大買家: {t.get('name','?')} ({t.get('position','')}) "
                f"${t.get('value',0)/1e6:.2f}M @ {t.get('date','')}"
            )
        if insider.sched_13d_count > 0:
            parts.append(
                f"- 13D filings (5%+ 活躍機構): **{insider.sched_13d_count}** 筆 "
                f"({', '.join(insider.recent_13d_dates[:3])})"
            )
        if insider.sched_13g_count > 0:
            parts.append(f"- 13G filings (5%+ 被動機構): {insider.sched_13g_count} 筆")
        if insider.form_8k_count > 0:
            parts.append(f"- 8-K 重大事件公告: {insider.form_8k_count} 筆")
        if insider.alert_type:
            label = {
                "insider_selling_spike": "🔴 內部人大量賣出",
                "insider_buying_signal": "🟢 內部人買入訊號",
                "activist_filing": "⚠️ 活躍股東介入 (13D)",
            }.get(insider.alert_type, insider.alert_type)
            parts.append(f"- ⚠️ Insider alert: **{label}**")

    # P1-3: 新聞訊號區塊
    if news and news.article_count_7d > 0:
        trend_label = {
            "rising": "📈 上升",
            "falling": "📉 下降",
            "stable": "➡️ 穩定",
            "unknown": "?",
        }.get(news.sentiment_trend, news.sentiment_trend)
        parts.append(f"\n## 📰 新聞訊號 (近 7 天)\n")
        parts.append(f"- 文章數: **{news.article_count_7d}** 篇 (含 {news.flash_count_7d} 重要快訊)")
        if news.sentiment_avg_7d is not None:
            parts.append(f"- 平均 sentiment: **{news.sentiment_avg_7d:+.2f}** (-1 ~ +1 scale)")
        parts.append(f"- 情緒趨勢: {trend_label}")
        if news.sentiment_recent_3d is not None and news.sentiment_older_4d is not None:
            parts.append(
                f"  - 近 3 天 {news.sentiment_recent_3d:+.2f} vs 前 4 天 {news.sentiment_older_4d:+.2f}"
            )
        if news.top_topics:
            parts.append(f"- 熱門主題: {', '.join(news.top_topics)}")
        if news.alert_type:
            alert_label = {
                "news_negative_spike": "🔴 負面新聞集中",
                "news_positive_spike": "🟢 正面新聞集中",
                "material_event": "⚡ 重要事件 (≥3 重要快訊)",
            }.get(news.alert_type, news.alert_type)
            parts.append(f"- ⚠️ 新聞 alert: **{alert_label}**")
        if news.material_events:
            parts.append("\n重要事件 (近 7 天):")
            for ev in news.material_events[:3]:
                src = ev.get("category") or ev.get("source") or ""
                sent = ev.get("sentiment")
                sent_str = f" [{sent:+.1f}]" if sent is not None else ""
                parts.append(f"  - **{src}**{sent_str} {ev.get('title','')[:80]}")

    # P1-2: 護城河結構化評分
    if moat and moat.overall_score > 0:
        type_label = {
            "intangible_assets": "無形資產 (品牌/專利)",
            "switching_costs": "轉換成本",
            "network_effects": "網路效應",
            "cost_advantage": "成本優勢",
            "efficient_scale": "效率規模",
        }
        strength_label = {
            "strong": "🟢 強",
            "moderate": "🟡 中等",
            "weak": "🔴 弱",
        }.get(moat.overall_strength, moat.overall_strength)
        trend_label = {
            "widening": "📈 擴張中",
            "stable": "➡️ 穩定",
            "narrowing": "📉 收窄中",
        }.get(moat.trend, moat.trend)

        parts.append(f"\n## 🏰 護城河結構化評分 (P1-2)\n")
        parts.append(
            f"- 整體強度: **{strength_label}** ({moat.overall_score:.1f}/10)"
        )
        if moat.dominant_types:
            dom_str = ", ".join(type_label.get(t, t) for t in moat.dominant_types)
            parts.append(f"- 主要類型: **{dom_str}**")
        parts.append(f"- 多年趨勢: {trend_label}")
        if moat.trend_evidence:
            parts.append(f"  - {' / '.join(moat.trend_evidence)}")
        parts.append("\n5 類型分數:")
        for c in moat.components:
            parts.append(
                f"  - **{type_label.get(c.moat_type, c.moat_type)}**: "
                f"{c.score:.1f}/10 — {c.rationale}"
            )

    # P1-1: 管理層 capital allocation 評估 — 放 LLM 之前(LLM 會引用這些數據)
    if management and (management.bvps_cagr_5y is not None or management.ceo_name):
        parts.append(f"\n## 👔 管理層評估 (Capital Allocation)\n")
        if management.ceo_name:
            parts.append(f"- CEO: **{management.ceo_name}** ({management.ceo_title or 'CEO'})")
        if management.bvps_cagr_5y is not None:
            parts.append(
                f"- BVPS 5y CAGR: **{management.bvps_cagr_5y*100:+.1f}%**"
            )
        if management.retention_efficiency is not None:
            parts.append(
                f"- 留存效率 (equity 成長 / 留存盈餘): **{management.retention_efficiency:.2f}**"
            )
        if management.dividend_payout_ratio_5y is not None:
            parts.append(
                f"- 5y 平均股利配發率: **{management.dividend_payout_ratio_5y*100:.0f}%**"
            )
        if management.grade and management.grade != "?":
            parts.append(f"- Buffett 風格分級: **{management.grade}**")
        if management.grade_reasons:
            parts.append("\n判斷依據:")
            for r in management.grade_reasons:
                parts.append(f"  - {r}")

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

    # 估值 (ensemble: DCF + Shiller PE + OE yield):只對非 OUT/INSUFFICIENT 算
    intrinsic = None
    valuation = None
    management = None
    if s.bias not in ("OUT_OF_CIRCLE", "INSUFFICIENT_DATA"):
        try:
            current_price = s.data.price if s.data else None
            market_cap = s.data.market_cap if s.data else None
            ind_class = s.data.industry_class if s.data else "general"
            # 既有 DCF (相容性,單獨保留 intrinsic)
            intrinsic = dcf_mod.estimate(
                s.ticker, current_price=current_price, industry_class=ind_class,
            )
            # 新 ensemble (含 DCF + Shiller PE + OE yield)
            valuation = valuation_mod.estimate(
                s.ticker, current_price=current_price,
                market_cap=market_cap, industry_class=ind_class,
            )
        except Exception:  # noqa: BLE001
            pass
        # P1-1: 管理層 capital allocation 評估 (BVPS CAGR、留存效率、grade)
        try:
            management = management_mod.evaluate(s.ticker)
        except Exception:  # noqa: BLE001
            pass

    # P1-2: 護城河結構化 (5 種類型評分 + 趨勢) — 對所有非 OUT/INSUFFICIENT 都跑
    moat = None
    if s.bias not in ("OUT_OF_CIRCLE", "INSUFFICIENT_DATA") and s.data is not None:
        try:
            td_dict = s.data.to_dict()
            facts = sec_api.get_facts(s.ticker)
            moat = moat_mod.evaluate(td_dict, facts)
        except Exception:  # noqa: BLE001
            pass

    # P1-3: 新聞訊號 (近 7 天 sentiment/事件) — 對 BUY/HOLD 才跑(省查詢成本)
    news = None
    if s.bias in ("BUY", "HOLD") and news_src.is_available():
        try:
            articles = news_src.fetch_recent_news(s.ticker, days=7, max_articles=20)
            news = news_mod.compute_signals(s.ticker, articles)
        except Exception:  # noqa: BLE001
            pass

    # P1-3.5: insider 交易訊號 (Form 4 + 13D/G + 8-K)
    insider = None
    if s.bias in ("BUY", "HOLD"):
        try:
            insider = insider_mod.evaluate(s.ticker, lookback_days=60)
        except Exception:  # noqa: BLE001
            pass

    # LLM 定性判斷:只對 BUY/HOLD 跑 (省成本),C1 階段 backend=none 永遠回空
    qualitative = None
    if s.bias in ("BUY", "HOLD"):
        try:
            llm_context = _build_llm_context(s, kb, intrinsic, valuation, management, moat, news, insider)
            qualitative = llm_mod.judge(s.ticker, llm_context)
        except Exception:  # noqa: BLE001
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
        rationale_md=_build_rationale_md(s, kb, intrinsic, qualitative, valuation, management, moat, news, insider),
        intrinsic=intrinsic,
        valuation=valuation,
        management=management,
        moat=moat,
        news=news,
        insider=insider,
        qualitative=qualitative,
    )


def _build_llm_context(
    s: screener.Score,
    kb: dict,
    intrinsic: dcf_mod.IntrinsicValue | None,
    valuation: valuation_mod.EnsembleValuation | None = None,
    management: management_mod.ManagementProfile | None = None,
    moat: moat_mod.MoatProfile | None = None,
    news: news_mod.NewsSignals | None = None,
    insider: insider_mod.InsiderSignals | None = None,
) -> dict:
    """組裝給 LLM backend 的上下文 (C3 才會真用到)。"""
    d = s.data
    ctx = {
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
    # P1-3.5: insider 交易訊號 (60 天)
    if insider and insider.transactions_count > 0:
        ctx["insider"] = {
            "lookback_days": insider.lookback_days,
            "transactions_count": insider.transactions_count,
            "total_sell_value": insider.total_sell_value,
            "total_buy_value": insider.total_buy_value,
            "exec_sell_value": insider.exec_sell_value,
            "exec_buy_value": insider.exec_buy_value,
            "net_value": insider.net_value,
            "top_seller": insider.top_seller,
            "top_buyer": insider.top_buyer,
            "alert_type": insider.alert_type,
            "sched_13d_count": insider.sched_13d_count,
            "sched_13g_count": insider.sched_13g_count,
            "form_8k_count": insider.form_8k_count,
        }

    # P1-3: 新聞訊號 + 重大事件清單 (LLM 應引用,不憑空猜)
    if news and news.article_count_7d > 0:
        ctx["news"] = {
            "article_count_7d": news.article_count_7d,
            "sentiment_avg_7d": news.sentiment_avg_7d,
            "sentiment_trend": news.sentiment_trend,
            "top_topics": news.top_topics,
            "flash_count_7d": news.flash_count_7d,
            "alert_type": news.alert_type,
            "material_events": news.material_events[:3],   # LLM 只需要前 3 條
        }

    # P1-2: 護城河結構化評分 (5 類型 + 趨勢)
    if moat:
        ctx["moat"] = {
            "overall_strength": moat.overall_strength,
            "overall_score": moat.overall_score,
            "dominant_types": moat.dominant_types,
            "trend": moat.trend,
            "trend_evidence": moat.trend_evidence,
            "components": [
                {"moat_type": c.moat_type, "score": c.score, "rationale": c.rationale}
                for c in moat.components
            ],
        }

    # P1-1: 管理層 capital allocation 量化評估
    if management:
        ctx["management"] = {
            "ceo_name": management.ceo_name,
            "ceo_title": management.ceo_title,
            "bvps_cagr_5y": management.bvps_cagr_5y,
            "dividend_payout_ratio_5y": management.dividend_payout_ratio_5y,
            "retention_efficiency": management.retention_efficiency,
            "grade": management.grade,
            "grade_reasons": management.grade_reasons,
        }

    # P0-3: ensemble 三模型結果(讓 LLM 自己權衡,不被單一 DCF 帶偏)
    if valuation and valuation.method_count > 0:
        ctx["valuation_ensemble"] = {
            "method_count": valuation.method_count,
            "consensus": valuation.consensus,
            "intrinsic_low": valuation.intrinsic_low,
            "intrinsic_mid": valuation.intrinsic_mid,
            "intrinsic_high": valuation.intrinsic_high,
            "mos_mid": valuation.mos_mid,
            "contributors": [
                {"method": c.method, "intrinsic": c.intrinsic_per_share,
                 "mos": c.margin_of_safety, "note": c.note}
                for c in valuation.contributors
            ],
        }
    return ctx
