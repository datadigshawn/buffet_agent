"""簡化版 DCF (Discounted Cash Flow) 內在價值估算。

Buffett 公開講過的版本:
  intrinsic_value = Σ (owner_earnings_t / (1+r)^t) for t = 1..N + terminal

我們的實作:
- owner_earnings_base = SEC 5 年平均 owner earnings (絕對值,USD)
- 兩階段成長:
  - Stage 1 (年 1-10): 用近 5 年 owner earnings CAGR 推 (上限 15%,避免過度樂觀)
  - Stage 2 (terminal): 用永續成長 2.5% (~長期 GDP)
- 折現率 r = 10y treasury yield + 6% 風險溢酬 (Buffett 公開要求)
  簡化:固定 r = 10% (treasury 4% + 風險溢酬 6%)
- intrinsic_per_share = total_intrinsic / shares_outstanding
- margin_of_safety = (intrinsic - price) / intrinsic

設計原則:
- 缺資料一律回 None,呼叫端決定是否進 verdict
- 保守估計:成長率取 min(實際 CAGR, 15%);如果有疑慮就 floor 到 5%
- Buffett 自己也說「DCF 模型很敏感,我們只用大略估計」— 我們不追求精度,追求方向性
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .sources import sec as sec_api
from . import sec_metrics

log = logging.getLogger(__name__)

# 折現率組成:10y treasury (~4%) + Buffett 風險溢酬 (~6%)
# 環境變數可調 (BUFFET_DCF_DISCOUNT_RATE)
import os
DEFAULT_DISCOUNT_RATE = float(os.environ.get("BUFFET_DCF_DISCOUNT_RATE", "0.10"))

# 保守上限:Stage 1 成長率封頂避免過度樂觀
MAX_STAGE1_GROWTH = 0.15
MIN_STAGE1_GROWTH = 0.025  # 至少 2.5% (通膨水準)
TERMINAL_GROWTH = 0.025

STAGE1_YEARS = 10


@dataclass
class IntrinsicValue:
    """DCF 估值結果。"""
    intrinsic_total: float        # 公司總內在價值 (USD)
    intrinsic_per_share: float    # 每股內在價值
    current_price: float | None   # 目前股價
    margin_of_safety: float | None  # (intrinsic - price) / intrinsic, > 0 = 便宜
    stage1_growth: float          # 用的 stage 1 成長率
    base_owner_earnings: float    # 起點 owner earnings (USD)
    discount_rate: float
    method: str = "two_stage_dcf"
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "intrinsic_total": self.intrinsic_total,
            "intrinsic_per_share": round(self.intrinsic_per_share, 2),
            "current_price": self.current_price,
            "margin_of_safety_pct": (
                round(self.margin_of_safety, 4) if self.margin_of_safety is not None else None
            ),
            "stage1_growth": round(self.stage1_growth, 4),
            "base_owner_earnings": self.base_owner_earnings,
            "discount_rate": self.discount_rate,
            "method": self.method,
            "note": self.note,
        }


def _owner_earnings_series(facts: dict, n: int = 5,
                           industry_class: str = "general") -> list[tuple[int, float]]:
    """取得 (年, owner_earnings) 序列 (絕對值,USD)。

    一般版: OE = OperatingCashFlow - Capex
    銀行/保險版: OE ≈ NetIncome (這類公司無實體 capex,NI 即可視為可分配現金)
    """
    if industry_class in ("bank", "insurance"):
        ni = sec_metrics._annual_series(facts, "NetIncome")
        if not ni:
            return []
        return ni[-n:]

    ocf = sec_metrics._annual_series(facts, "OperatingCashFlow")
    cap = sec_metrics._annual_series(facts, "Capex")
    if not ocf:
        return []
    if not cap:
        # 沒 capex 資料 → 退而用 NetIncome (與銀行邏輯相同)
        ni = sec_metrics._annual_series(facts, "NetIncome")
        if ni:
            return ni[-n:]
        return []
    ocf_d = dict(ocf)
    cap_d = dict(cap)
    common = sorted(set(ocf_d) & set(cap_d), reverse=True)[:n]
    return [(y, ocf_d[y] - cap_d[y]) for y in sorted(common)]


def _shares_outstanding_latest(facts: dict) -> float | None:
    """取最新一年 shares outstanding (絕對值,股數)。"""
    series = sec_metrics._annual_series(
        facts, "SharesOutstanding", unit_pref=("shares",)
    )
    if not series:
        return None
    return series[-1][1]


def _cagr(values: list[float]) -> float | None:
    """近 N 年的年化成長率。第一個和最後一個值。"""
    if len(values) < 2:
        return None
    start, end = values[0], values[-1]
    if start <= 0 or end <= 0:
        return None
    n = len(values) - 1
    return (end / start) ** (1.0 / n) - 1.0


def estimate(ticker: str, current_price: float | None = None,
             discount_rate: float | None = None,
             industry_class: str = "general") -> IntrinsicValue | None:
    """主入口:回傳 ticker 的 DCF 估值,缺資料回 None。

    Parameters
    ----------
    ticker : 美股 ticker
    current_price : 若已知,直接傳入避免重抓 (data_loader 已有)
    discount_rate : 覆寫預設 10% 折現率
    industry_class : 銀行 / 保險業改用 NetIncome 當 OE proxy
    """
    facts = sec_api.get_facts(ticker)
    if not facts:
        return None

    oe_series = _owner_earnings_series(facts, n=5, industry_class=industry_class)
    if len(oe_series) < 3:
        # 5 年 OE 至少要有 3 年才估,否則太雜訊
        return None
    oe_values = [v for _, v in oe_series]
    base_oe = sum(oe_values) / len(oe_values)
    if base_oe <= 0:
        # 連 5 年平均 OE 為負 → DCF 沒意義
        return None

    cagr = _cagr(oe_values)
    if cagr is None:
        stage1_g = MIN_STAGE1_GROWTH
    else:
        stage1_g = max(MIN_STAGE1_GROWTH, min(MAX_STAGE1_GROWTH, cagr))

    r = discount_rate if discount_rate is not None else DEFAULT_DISCOUNT_RATE
    if r <= TERMINAL_GROWTH:
        log.warning("discount rate %s <= terminal growth %s for %s",
                    r, TERMINAL_GROWTH, ticker)
        return None

    # Stage 1: 10 年明確現金流折現
    pv_stage1 = 0.0
    oe = base_oe
    for t in range(1, STAGE1_YEARS + 1):
        oe = oe * (1 + stage1_g)
        pv_stage1 += oe / (1 + r) ** t

    # Stage 2: 終值 (Gordon growth model)
    terminal_oe = oe * (1 + TERMINAL_GROWTH)
    terminal_value = terminal_oe / (r - TERMINAL_GROWTH)
    pv_terminal = terminal_value / (1 + r) ** STAGE1_YEARS

    intrinsic_total = pv_stage1 + pv_terminal

    shares = _shares_outstanding_latest(facts)
    if not shares or shares <= 0:
        return None
    intrinsic_per_share = intrinsic_total / shares

    mos = None
    if current_price and current_price > 0 and intrinsic_per_share > 0:
        mos = (intrinsic_per_share - current_price) / intrinsic_per_share

    note_parts = [f"5y OE avg={base_oe/1e9:.2f}B", f"CAGR={cagr*100:.1f}%" if cagr else "no CAGR"]
    return IntrinsicValue(
        intrinsic_total=intrinsic_total,
        intrinsic_per_share=intrinsic_per_share,
        current_price=current_price,
        margin_of_safety=mos,
        stage1_growth=stage1_g,
        base_owner_earnings=base_oe,
        discount_rate=r,
        note="; ".join(note_parts),
    )
