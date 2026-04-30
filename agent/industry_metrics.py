"""行業特化指標 (Phase 5 P2-3)。

針對 bank / utility / reit 計算行業專屬 KPI:

  bank:
    - bank_roa            NetIncome / TotalAssets (latest year)
                          優秀銀行 > 1%,Wells Fargo / JPM 平時在這附近
    - efficiency_ratio    NoninterestExpense / Revenue (簡化:OpEx/Revenue)
                          < 60% 算優質銀行

  utility:
    - capex_dep_ratio     Capex / Depreciation (5y avg)
                          > 1 表示在擴張 rate base,Buffett 偏好的「複利機器」型公用事業

  reit:
    - ffo_margin          FFO / Revenue (5y avg)
                          FFO ≈ NetIncome + Depreciation (REIT 標準近似)
                          > 30% 算健康

保險業 combined ratio 在 SEC us-gaap 沒有標準 concept(各家公司用不同擴展命名),
此 module 暫不處理,留給 P3 / 後續。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from typing import Any

from .sources import sec as sec_api
from . import sec_metrics

log = logging.getLogger(__name__)


@dataclass
class IndustryMetrics:
    """行業專屬指標,空欄位代表不適用或無資料。"""
    bank_roa: float | None = None
    efficiency_ratio: float | None = None
    capex_dep_ratio: float | None = None
    ffo_margin: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------- Bank ----------

def bank_roa(facts: dict) -> float | None:
    """最近一年 NetIncome / TotalAssets。"""
    ni = sec_metrics._annual_series(facts, "NetIncome")
    assets = sec_metrics._annual_series(facts, "TotalAssets")
    if not ni or not assets:
        return None
    ni_d = dict(ni)
    a_d = dict(assets)
    common = sorted(set(ni_d) & set(a_d), reverse=True)
    if not common:
        return None
    y = common[0]
    if a_d[y] <= 0:
        return None
    return ni_d[y] / a_d[y]


def efficiency_ratio(facts: dict) -> float | None:
    """簡化 efficiency = (Revenue - NetIncome) / Revenue,5y 平均。

    銀行業真實 efficiency = NoninterestExpense / NetRevenue,但 NoninterestExpense
    在 us-gaap 命名各銀行不一,此處用「(Revenue - NetIncome) / Revenue」當代理
    (相當於 1 - NetMargin 的銀行版),可粗估費用佔比。
    """
    rev = sec_metrics._annual_series(facts, "Revenues")
    ni = sec_metrics._annual_series(facts, "NetIncome")
    if not rev or not ni:
        return None
    rev_d = dict(rev)
    ni_d = dict(ni)
    common = sorted(set(rev_d) & set(ni_d), reverse=True)[:5]
    ratios = []
    for y in common:
        if rev_d[y] > 0:
            ratios.append((rev_d[y] - ni_d[y]) / rev_d[y])
    if not ratios:
        return None
    return sum(ratios) / len(ratios)


# ---------- Utility ----------

def capex_dep_ratio(facts: dict, n: int = 5) -> float | None:
    """近 N 年 Capex / Depreciation 平均,反映 rate base 擴張速度。"""
    cap = sec_metrics._annual_series(facts, "Capex")
    dep = sec_metrics._annual_series(facts, "Depreciation")
    if not cap or not dep:
        return None
    cap_d = dict(cap)
    dep_d = dict(dep)
    common = sorted(set(cap_d) & set(dep_d), reverse=True)[:n]
    ratios = []
    for y in common:
        if dep_d[y] > 0:
            ratios.append(cap_d[y] / dep_d[y])
    if not ratios:
        return None
    return sum(ratios) / len(ratios)


# ---------- REIT ----------

def ffo_margin(facts: dict, n: int = 5) -> float | None:
    """REIT FFO ≈ NetIncome + Depreciation;FFO/Revenue,5y 平均。

    這是 REIT 行業標準近似。實際 FFO 還會調整 gains on sales,但這需要 case-by-case
    解析,我們用近似值做篩選層。
    """
    ni = sec_metrics._annual_series(facts, "NetIncome")
    dep = sec_metrics._annual_series(facts, "Depreciation")
    rev = sec_metrics._annual_series(facts, "Revenues")
    if not ni or not dep or not rev:
        return None
    ni_d = dict(ni)
    dep_d = dict(dep)
    rev_d = dict(rev)
    common = sorted(set(ni_d) & set(dep_d) & set(rev_d), reverse=True)[:n]
    margins = []
    for y in common:
        if rev_d[y] > 0:
            ffo = ni_d[y] + dep_d[y]
            margins.append(ffo / rev_d[y])
    if not margins:
        return None
    avg = sum(margins) / len(margins)
    # Sanity:FFO margin 不應 > 1.0 (FFO 不會超過 Revenue);如果 > 1 表示 Revenue
    # 概念抓到 sub-segment (例 AMT 的 lease revenue 而非 total),不可靠 → 回 None
    if avg > 1.0:
        log.warning("ffo_margin sanity check failed (%.2f);likely concept mismatch", avg)
        return None
    return avg


# ---------- 整合入口 ----------

def evaluate(ticker: str, industry_class: str) -> IndustryMetrics:
    """主入口:依 industry_class 計算對應指標,回傳 IndustryMetrics。

    一般行業 (industry_class == 'general' / 'insurance') 不算,回空 dataclass。
    """
    metrics = IndustryMetrics()
    if industry_class not in ("bank", "utility", "reit"):
        return metrics

    facts = sec_api.get_facts(ticker)
    if not facts:
        return metrics

    if industry_class == "bank":
        metrics.bank_roa = bank_roa(facts)
        metrics.efficiency_ratio = efficiency_ratio(facts)
    elif industry_class == "utility":
        metrics.capex_dep_ratio = capex_dep_ratio(facts)
    elif industry_class == "reit":
        metrics.ffo_margin = ffo_margin(facts)

    return metrics
