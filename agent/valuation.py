"""估值 ensemble (Phase 5 P0-3) — 三模型平均,降低 DCF 單一敏感度。

Buffett 自己:「DCF 模型很敏感,我們只用大略估計」。
單獨 DCF 對 AAPL/KO 算出 -185%/-207% MOS,但 Buffett 仍持有 — 顯示單一模型誤判嚴重。

三個模型:
  1. dcf_two_stage     — 既有 dcf.estimate(),保留為一個 contributor
  2. shiller_pe        — 5 年平均 EPS × 公允 PE 帶 (12/17/22),Shiller / Graham 風格
  3. owner_earnings_yield — 5y avg OE / market cap vs 10y treasury + 風險溢酬

Ensemble:
  - low = min,mid = median,high = max
  - MOS:相對 mid intrinsic 計算
  - consensus: very_cheap / cheap / fair / expensive / very_expensive / uncertain
"""
from __future__ import annotations

import logging
import os
import statistics
from dataclasses import dataclass, field, asdict
from typing import Any

from .sources import sec as sec_api
from . import sec_metrics
from . import dcf as dcf_mod

log = logging.getLogger(__name__)

# Shiller / Graham 風格的公允 PE 帶
# 這是個粗略 anchor,可依市場情緒調(現在偏保守)
FAIR_PE_LOW = 12      # 低估區
FAIR_PE_MID = 17      # 公允區 (歷史長期均值)
FAIR_PE_HIGH = 22     # 高估區 (近年科技股高 PE 環境)

# Owner earnings yield 的公允基準
# Buffett 公開講過:他要 OE yield > 10y treasury + 6% 風險溢酬
TREASURY_10Y_DEFAULT = float(os.environ.get("BUFFET_10Y_TREASURY", "0.044"))
RISK_PREMIUM = 0.06   # Buffett 公開要求的最低風險溢酬

# Consensus 門檻
MOS_VERY_CHEAP = 0.30
MOS_CHEAP = 0.10
MOS_EXPENSIVE = -0.20
MOS_VERY_EXPENSIVE = -0.50


@dataclass
class ValuationContributor:
    """單一估值模型的輸出。"""
    method: str
    intrinsic_per_share: float | None = None
    margin_of_safety: float | None = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EnsembleValuation:
    """三模型 ensemble 結果。"""
    current_price: float | None
    contributors: list[ValuationContributor] = field(default_factory=list)
    intrinsic_low: float | None = None
    intrinsic_mid: float | None = None
    intrinsic_high: float | None = None
    mos_low: float | None = None      # 最悲觀 (用 low intrinsic)
    mos_mid: float | None = None      # 中位
    mos_high: float | None = None     # 最樂觀 (用 high intrinsic)
    consensus: str = "uncertain"      # very_cheap / cheap / fair / expensive / very_expensive / uncertain
    method_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_price": self.current_price,
            "intrinsic_low": _round(self.intrinsic_low),
            "intrinsic_mid": _round(self.intrinsic_mid),
            "intrinsic_high": _round(self.intrinsic_high),
            "mos_low": _round(self.mos_low, 4),
            "mos_mid": _round(self.mos_mid, 4),
            "mos_high": _round(self.mos_high, 4),
            "consensus": self.consensus,
            "method_count": self.method_count,
            "contributors": [c.to_dict() for c in self.contributors],
        }


def _round(x: float | None, digits: int = 2) -> float | None:
    return round(x, digits) if x is not None else None


# ---------- 模型 1: Shiller PE 風格 ----------

def shiller_pe_estimate(facts: dict, current_price: float | None,
                        shares_fallback: float | None = None) -> ValuationContributor:
    """Shiller/Graham 風格:5 年平均 EPS × 公允 PE。

    intrinsic_low/mid/high = avg_eps × {12, 17, 22}
    回傳的 ValuationContributor 用 mid 當代表值。
    """
    contributor = ValuationContributor(method="shiller_pe")

    ni = sec_metrics._annual_series(facts, "NetIncome")
    sh = sec_metrics._annual_series(facts, "SharesOutstanding", unit_pref=("shares",))
    if not ni:
        contributor.note = "缺 NetIncome"
        return contributor

    ni_d = dict(ni)

    # 若 SEC 沒 SharesOutstanding (V/MA 多類股) 但有 fallback,用最新 fallback 推算所有年度 EPS
    # (近似:假設股數變動不大,雖然 V 持續回購,這比完全跳過 valuation 好)
    if sh:
        sh_d = dict(sh)
        common = sorted(set(ni_d) & set(sh_d), reverse=True)[:5]
        if len(common) < 3:
            contributor.note = f"年度資料不足 ({len(common)} 年)"
            return contributor
        eps_series = [ni_d[y] / sh_d[y] for y in common if sh_d[y] > 0]
    elif shares_fallback and shares_fallback > 0:
        common = sorted(ni_d.keys(), reverse=True)[:5]
        if len(common) < 3:
            contributor.note = f"NetIncome 年度不足 ({len(common)} 年)"
            return contributor
        eps_series = [ni_d[y] / shares_fallback for y in common]
        contributor.note = f"shares fallback ({shares_fallback/1e6:.0f}M) "
    else:
        contributor.note = "缺 SharesOutstanding (含 fallback)"
        return contributor
    if not eps_series:
        contributor.note = "無法計算 EPS"
        return contributor

    avg_eps = sum(eps_series) / len(eps_series)
    if avg_eps <= 0:
        contributor.note = f"5y avg EPS 為負 ({avg_eps:.2f})"
        return contributor

    intrinsic_mid = avg_eps * FAIR_PE_MID
    contributor.intrinsic_per_share = intrinsic_mid
    contributor.note = (
        contributor.note
        + f"5y avg EPS=${avg_eps:.2f} × fair PE {FAIR_PE_MID}"
    )
    if current_price and current_price > 0:
        contributor.margin_of_safety = (intrinsic_mid - current_price) / intrinsic_mid
    return contributor


# ---------- 模型 2: Owner Earnings Yield ----------

def owner_earnings_yield_estimate(
    facts: dict,
    market_cap: float | None,
    current_price: float | None,
    treasury_10y: float = TREASURY_10Y_DEFAULT,
    industry_class: str = "general",
    shares_fallback: float | None = None,
) -> ValuationContributor:
    """Buffett 自己用的方法:

    fair_yield = treasury + risk_premium (預設 4.4% + 6% = 10.4%)
    intrinsic_market_cap = 5y_avg_OE / fair_yield
    intrinsic_per_share = intrinsic_market_cap / shares_outstanding

    > Buffett: 「我們把任何投資都拿來和零息債券比較」
    """
    contributor = ValuationContributor(method="owner_earnings_yield")
    if not market_cap or market_cap <= 0:
        contributor.note = "缺 market_cap"
        return contributor

    oe_series = dcf_mod._owner_earnings_series(
        facts, n=5, industry_class=industry_class,
    )
    if len(oe_series) < 3:
        contributor.note = f"OE 序列不足 ({len(oe_series)} 年)"
        return contributor

    oe_values = [v for _, v in oe_series]
    avg_oe = sum(oe_values) / len(oe_values)
    if avg_oe <= 0:
        contributor.note = f"5y 平均 OE 為負 ({avg_oe/1e9:.2f}B)"
        return contributor

    fair_yield = treasury_10y + RISK_PREMIUM
    intrinsic_market_cap = avg_oe / fair_yield

    shares = dcf_mod._shares_outstanding_latest(facts, fallback=shares_fallback)
    if not shares or shares <= 0:
        contributor.note = "缺 shares_outstanding (含 fallback)"
        return contributor

    intrinsic_per_share = intrinsic_market_cap / shares
    current_oe_yield = avg_oe / market_cap
    contributor.intrinsic_per_share = intrinsic_per_share
    contributor.note = (
        f"5y OE/MarketCap yield={current_oe_yield*100:.2f}% vs "
        f"fair yield={fair_yield*100:.1f}%"
    )
    if current_price and current_price > 0:
        contributor.margin_of_safety = (
            (intrinsic_per_share - current_price) / intrinsic_per_share
        )
    return contributor


# ---------- 模型 3: DCF (既有,封裝成 contributor) ----------

def dcf_estimate_as_contributor(
    ticker: str, current_price: float | None, industry_class: str = "general",
    shares_fallback: float | None = None,
) -> ValuationContributor:
    iv = dcf_mod.estimate(
        ticker, current_price=current_price, industry_class=industry_class,
        shares_fallback=shares_fallback,
    )
    if not iv:
        return ValuationContributor(method="dcf_two_stage", note="DCF 計算不出")
    return ValuationContributor(
        method="dcf_two_stage",
        intrinsic_per_share=iv.intrinsic_per_share,
        margin_of_safety=iv.margin_of_safety,
        note=iv.note,
    )


# ---------- Ensemble ----------

def _classify_consensus(mos_mid: float | None) -> str:
    if mos_mid is None:
        return "uncertain"
    if mos_mid >= MOS_VERY_CHEAP:
        return "very_cheap"
    if mos_mid >= MOS_CHEAP:
        return "cheap"
    if mos_mid > MOS_EXPENSIVE:           # > 嚴格,讓 -0.20 落到 expensive 區
        return "fair"
    if mos_mid > MOS_VERY_EXPENSIVE:
        return "expensive"
    return "very_expensive"


def estimate(
    ticker: str,
    current_price: float | None,
    market_cap: float | None,
    industry_class: str = "general",
) -> EnsembleValuation:
    """主入口:組合三模型 + ensemble。"""
    out = EnsembleValuation(current_price=current_price)
    facts = sec_api.get_facts(ticker)
    if not facts:
        out.consensus = "uncertain"
        return out

    # T-1: 多類股 (V/MA) SEC 沒 SharesOutstanding → 用 yfinance market_cap/price 推 fallback
    shares_fallback: float | None = None
    if market_cap and current_price and current_price > 0:
        shares_fallback = market_cap / current_price

    contributors = [
        dcf_estimate_as_contributor(
            ticker, current_price, industry_class, shares_fallback=shares_fallback,
        ),
        shiller_pe_estimate(facts, current_price, shares_fallback=shares_fallback),
        owner_earnings_yield_estimate(
            facts, market_cap, current_price, industry_class=industry_class,
            shares_fallback=shares_fallback,
        ),
    ]
    out.contributors = contributors

    valid = [c for c in contributors if c.intrinsic_per_share is not None]
    out.method_count = len(valid)
    if not valid:
        out.consensus = "uncertain"
        return out

    intrinsics = sorted(c.intrinsic_per_share for c in valid)
    out.intrinsic_low = intrinsics[0]
    out.intrinsic_high = intrinsics[-1]
    out.intrinsic_mid = (
        intrinsics[len(intrinsics) // 2]
        if len(intrinsics) % 2 == 1
        else (intrinsics[len(intrinsics) // 2 - 1] + intrinsics[len(intrinsics) // 2]) / 2
    )

    if current_price and current_price > 0:
        if out.intrinsic_low > 0:
            out.mos_low = (out.intrinsic_low - current_price) / out.intrinsic_low
        if out.intrinsic_mid > 0:
            out.mos_mid = (out.intrinsic_mid - current_price) / out.intrinsic_mid
        if out.intrinsic_high > 0:
            out.mos_high = (out.intrinsic_high - current_price) / out.intrinsic_high

    out.consensus = _classify_consensus(out.mos_mid)
    return out
