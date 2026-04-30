"""護城河結構化評分 (Phase 5 P1-2)。

Buffett + Pat Dorsey 框架的 5 種護城河類型,每種有量化代理指標:

  1. intangible_assets:    品牌 / 專利 / 特許 → 高毛利 + 毛利穩定性
  2. switching_costs:      轉換成本 → 高 ROE 持續性 + 高 ROIC
  3. network_effects:      網路效應 → 高 ROE + 規模 + 高 owner earnings
  4. cost_advantage:       成本優勢 → 毛利率 > 同業 + 規模
  5. efficient_scale:      效率規模 / 自然壟斷 → 監管行業 + 穩定 ROE

每種給 0-10 分,加總成整體 moat 強度。多年趨勢:widening / stable / narrowing。

設計原則:
- 每個 component 用我們已有的 SEC / yfinance 資料推
- 不依賴第三方排名(如 Morningstar moat rating)
- LLM 引用結構化結果而非自己亂吐「網路效應強」
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import Any

from .sources import sec as sec_api
from . import sec_metrics

log = logging.getLogger(__name__)

MOAT_TYPES = [
    "intangible_assets",
    "switching_costs",
    "network_effects",
    "cost_advantage",
    "efficient_scale",
]


@dataclass
class MoatComponent:
    moat_type: str       # 上述 5 種之一
    score: float         # 0-10 分
    rationale: str       # 給這個分的依據

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MoatProfile:
    components: list[MoatComponent] = field(default_factory=list)
    overall_score: float = 0.0
    overall_strength: str = "weak"       # strong (≥7) / moderate (4-7) / weak (<4)
    dominant_types: list[str] = field(default_factory=list)   # top 2
    trend: str = "stable"                 # widening / stable / narrowing
    trend_evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "components": [c.to_dict() for c in self.components],
            "overall_score": round(self.overall_score, 2),
            "overall_strength": self.overall_strength,
            "dominant_types": self.dominant_types,
            "trend": self.trend,
            "trend_evidence": self.trend_evidence,
        }


# ---------- 各個護城河類型的量化代理 ----------

def score_intangible_assets(td_data: dict) -> MoatComponent:
    """品牌 / 專利 / 特許 → 高毛利 + 毛利穩定性 + 持續高 ROE。"""
    gm = td_data.get("gross_margin")
    nm = td_data.get("net_margin")
    roe10 = td_data.get("roe_consistency_10y")
    sub: list[str] = []
    score = 0.0

    if gm is not None:
        if gm >= 0.50:
            score += 4
            sub.append(f"毛利率 {gm*100:.0f}% (強品牌定價權)")
        elif gm >= 0.35:
            score += 2
            sub.append(f"毛利率 {gm*100:.0f}%")
        elif gm < 0.20:
            score -= 1
            sub.append(f"毛利率 {gm*100:.0f}% 低,品牌力弱")

    if nm is not None:
        if nm >= 0.20:
            score += 2
            sub.append(f"淨利率 {nm*100:.0f}%")

    if roe10 is not None and roe10 >= 0.8:
        score += 3
        sub.append(f"ROE 持續性 {roe10*100:.0f}% (反映品牌持久力)")

    score = max(0.0, min(10.0, score))
    return MoatComponent(
        moat_type="intangible_assets",
        score=score,
        rationale="; ".join(sub) or "資料不足",
    )


def score_switching_costs(td_data: dict) -> MoatComponent:
    """轉換成本 → ROE 持續性 + ROIC + 高 owner earnings margin。"""
    roe10 = td_data.get("roe_consistency_10y")
    roic = td_data.get("roic_5y_avg")
    oe_margin = td_data.get("fcf_margin")    # 在 SEC 整合後是 5y owner earnings margin
    sub: list[str] = []
    score = 0.0

    if roe10 is not None:
        if roe10 >= 0.9:
            score += 4
            sub.append(f"ROE 10y 持續 {roe10*100:.0f}% (客戶極黏)")
        elif roe10 >= 0.7:
            score += 2

    if roic is not None:
        if roic >= 0.20:
            score += 3
            sub.append(f"ROIC 5y avg {roic*100:.0f}%")
        elif roic >= 0.15:
            score += 1

    if oe_margin is not None and oe_margin >= 0.20:
        score += 2
        sub.append(f"OE margin {oe_margin*100:.0f}% (轉換成本變現)")

    score = max(0.0, min(10.0, score))
    return MoatComponent(
        moat_type="switching_costs",
        score=score,
        rationale="; ".join(sub) or "資料不足",
    )


def score_network_effects(td_data: dict) -> MoatComponent:
    """網路效應 → 收入加速成長 + 高毛利 + 高 ROIC + 規模。"""
    rev_growth = td_data.get("rev_growth")
    gm = td_data.get("gross_margin")
    roic = td_data.get("roic_5y_avg")
    market_cap = td_data.get("market_cap") or 0
    sub: list[str] = []
    score = 0.0

    # 規模門檻:大公司才可能有網路效應
    if market_cap >= 50_000_000_000:    # $50B+
        score += 1

    if rev_growth is not None and rev_growth >= 0.15:
        score += 3
        sub.append(f"營收成長 {rev_growth*100:.0f}% (用戶基數加速)")

    if gm is not None and gm >= 0.40:
        if rev_growth and rev_growth >= 0.10:
            score += 2
            sub.append(f"高毛利 ({gm*100:.0f}%) + 成長共存")

    if roic is not None and roic >= 0.25:
        score += 2
        sub.append(f"ROIC {roic*100:.0f}% (邊際成本低)")

    score = max(0.0, min(10.0, score))
    return MoatComponent(
        moat_type="network_effects",
        score=score,
        rationale="; ".join(sub) or "資料不足或無顯著訊號",
    )


def score_cost_advantage(td_data: dict) -> MoatComponent:
    """成本優勢 → 規模 + 行業領先毛利 + 大量回購(現金充裕)。"""
    gm = td_data.get("gross_margin")
    market_cap = td_data.get("market_cap") or 0
    revenue_proxy = market_cap   # market_cap 當營收規模代理
    buyback = td_data.get("buyback_yield")
    sub: list[str] = []
    score = 0.0

    if revenue_proxy >= 100_000_000_000:    # $100B+
        score += 2
        sub.append("市值百億等級規模")
    elif revenue_proxy >= 10_000_000_000:
        score += 1

    if gm is not None and gm >= 0.30:
        score += 2
        sub.append(f"毛利率 {gm*100:.0f}%")

    # 大量持續回購意味著現金生成 > 內部投資需求 = 成本優勢成熟
    if buyback is not None and buyback >= 0.03:
        score += 2
        sub.append(f"買回率 {buyback*100:.1f}% (現金過剩)")

    score = max(0.0, min(10.0, score))
    return MoatComponent(
        moat_type="cost_advantage",
        score=score,
        rationale="; ".join(sub) or "資料不足",
    )


def score_efficient_scale(td_data: dict) -> MoatComponent:
    """效率規模 / 自然壟斷 → 監管行業 + 穩定 ROE + 大規模。"""
    ind = td_data.get("industry_class", "general")
    roe = td_data.get("roe")
    market_cap = td_data.get("market_cap") or 0
    sub: list[str] = []
    score = 0.0

    if ind == "utility":
        score += 4
        sub.append("公用事業 (監管特許)")
    elif ind == "bank":
        score += 1
        sub.append("銀行業 (部分監管門檻)")
    elif ind == "insurance":
        score += 2
        sub.append("保險業 (進入門檻 + 浮存金)")

    if roe is not None and roe >= 0.10 and ind in ("utility", "bank", "insurance"):
        score += 2
        sub.append(f"監管行業 ROE {roe*100:.0f}% 達標")

    if market_cap >= 50_000_000_000 and ind in ("utility", "bank", "insurance"):
        score += 1
        sub.append("規模門檻已過")

    score = max(0.0, min(10.0, score))
    return MoatComponent(
        moat_type="efficient_scale",
        score=score,
        rationale="; ".join(sub) or "非適用行業",
    )


# ---------- 多年趨勢 ----------

def analyze_trend(facts: dict | None) -> tuple[str, list[str]]:
    """比較最近 5 年 vs 之前 5 年的 ROE 趨勢:widening / stable / narrowing。

    用 NetIncome / TotalEquity 當 ROE 代理。
    """
    if not facts:
        return "stable", ["缺 SEC 資料"]
    ni = sec_metrics._annual_series(facts, "NetIncome")
    eq = sec_metrics._annual_series(facts, "TotalEquity")
    if not ni or not eq:
        return "stable", ["缺 NetIncome 或 TotalEquity"]
    ni_d = dict(ni)
    eq_d = dict(eq)
    common = sorted(set(ni_d) & set(eq_d))
    if len(common) < 8:
        return "stable", [f"年度資料不足 ({len(common)} 年,需要 ≥ 8)"]

    recent_5 = common[-5:]
    older_5 = common[-10:-5] if len(common) >= 10 else common[:-5]
    if not older_5:
        return "stable", ["older period 不足"]

    def avg_roe(years):
        roes = [ni_d[y] / eq_d[y] for y in years if eq_d[y] > 0]
        return sum(roes) / len(roes) if roes else None

    recent = avg_roe(recent_5)
    older = avg_roe(older_5)
    if recent is None or older is None or older <= 0:
        return "stable", ["ROE 計算失敗"]

    delta = (recent - older) / abs(older)
    evidence = [
        f"近 5 年平均 ROE {recent*100:.1f}%",
        f"前 5 年平均 ROE {older*100:.1f}%",
        f"變化 {delta*100:+.0f}%",
    ]
    if delta >= 0.15:
        return "widening", evidence
    if delta <= -0.15:
        return "narrowing", evidence
    return "stable", evidence


# ---------- 整合 ----------

def evaluate(td_data: dict, facts: dict | None = None) -> MoatProfile:
    """主入口:給 TickerData 的 dict 表示 + (可選)SEC facts → MoatProfile。

    td_data 預期含:gross_margin / net_margin / roe / roe_consistency_10y /
                    roic_5y_avg / fcf_margin / rev_growth / market_cap /
                    buyback_yield / industry_class
    """
    components = [
        score_intangible_assets(td_data),
        score_switching_costs(td_data),
        score_network_effects(td_data),
        score_cost_advantage(td_data),
        score_efficient_scale(td_data),
    ]
    # 整體分數:用最高 2 個 component 加權平均(主要護城河)
    sorted_by_score = sorted(components, key=lambda c: -c.score)
    if len(sorted_by_score) >= 2:
        overall_score = (sorted_by_score[0].score * 0.6
                         + sorted_by_score[1].score * 0.4)
    else:
        overall_score = sorted_by_score[0].score if sorted_by_score else 0.0

    if overall_score >= 7:
        strength = "strong"
    elif overall_score >= 4:
        strength = "moderate"
    else:
        strength = "weak"

    dominant = [c.moat_type for c in sorted_by_score[:2] if c.score >= 3]

    trend, evidence = analyze_trend(facts)

    return MoatProfile(
        components=components,
        overall_score=overall_score,
        overall_strength=strength,
        dominant_types=dominant,
        trend=trend,
        trend_evidence=evidence,
    )
