"""管理層深度評估 (Phase 5 P1-1) — Buffett 風格的 capital allocation 紀錄。

Buffett 自己在股東信反覆強調的管理層 KPI:

  > 「我們希望每一塊留存的盈餘,至少能在市場上創造一塊的價值。」

這個 module 用 SEC 資料計算 4 個量化 KPI:

  1. retained_earnings_test:過去 5 年留存盈餘 vs book value 成長
  2. bvps_cagr_5y:每股淨值年化成長率 (Buffett 自己用的核心指標)
  3. dividend_payout_ratio_5y:5 年平均股利配發率 (低 = 偏好內部複利)
  4. capital_allocation_grade:A/B/C/D 綜合分級

也記錄 CEO 名字 / 任期(從 yfinance 取),供 LLM 引用。

不做的事 (留給後續):
  - DEF 14A 全文 LLM 解讀(P1.5+)
  - SEC Form 4 insider 交易紀錄(B3 落地)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from typing import Any

from .sources import sec as sec_api
from . import sec_metrics

log = logging.getLogger(__name__)


@dataclass
class ManagementProfile:
    """管理層 capital allocation 的量化評分。"""
    ceo_name: str | None = None
    ceo_title: str | None = None
    ceo_tenure_years: int | None = None
    bvps_cagr_5y: float | None = None              # 每股淨值 5 年 CAGR
    dividend_payout_ratio_5y: float | None = None  # 0-1
    retained_earnings_5y: float | None = None       # USD
    book_equity_growth_5y: float | None = None      # USD
    retention_efficiency: float | None = None       # equity_growth / retained_earnings,~1 = 健康
    grade: str = "?"                                # A / B / C / D / ?
    grade_reasons: list[str] = None                 # 評等理由

    def __post_init__(self):
        if self.grade_reasons is None:
            self.grade_reasons = []

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------- 各個指標 ----------

def bvps_cagr(facts: dict, n: int = 5) -> tuple[float | None, float | None]:
    """每股淨值 N 年 CAGR + N 年 book value 成長(USD)。

    Buffett 公開講過:這是他最看重的單一管理層 KPI(Berkshire 自己每年披露這項)。
    """
    eq = sec_metrics._annual_series(facts, "TotalEquity")
    sh = sec_metrics._annual_series(facts, "SharesOutstanding", unit_pref=("shares",))
    if not eq:
        return None, None
    eq_d = dict(eq)

    # Shares 缺資料 → 只算總 equity 成長(無 BVPS)
    if not sh:
        years = sorted(eq_d.keys())
        if len(years) < n + 1:
            return None, None
        latest = years[-1]
        baseline = years[-(n + 1)]
        if eq_d[baseline] <= 0:
            return None, eq_d[latest] - eq_d[baseline]
        return None, eq_d[latest] - eq_d[baseline]

    sh_d = dict(sh)
    common = sorted(set(eq_d) & set(sh_d))
    if len(common) < n + 1:
        return None, None
    latest = common[-1]
    baseline = common[-(n + 1)]
    bvps_latest = eq_d[latest] / sh_d[latest] if sh_d[latest] > 0 else None
    bvps_baseline = eq_d[baseline] / sh_d[baseline] if sh_d[baseline] > 0 else None
    if bvps_latest is None or bvps_baseline is None or bvps_baseline <= 0:
        return None, eq_d[latest] - eq_d[baseline]
    cagr = (bvps_latest / bvps_baseline) ** (1.0 / n) - 1.0
    return cagr, eq_d[latest] - eq_d[baseline]


def dividend_payout_ratio(facts: dict, n: int = 5) -> float | None:
    """N 年平均股利配發率 = sum(dividends) / sum(net_income)。

    Buffett 偏好低配發率(內部複利更有效率),除非 ROIC 已下降。
    """
    div = sec_metrics._annual_series(facts, "Dividends")
    ni = sec_metrics._annual_series(facts, "NetIncome")
    if not div or not ni:
        return None
    div_d = dict(div)
    ni_d = dict(ni)
    common = sorted(set(div_d) & set(ni_d), reverse=True)[:n]
    total_div = sum(div_d[y] for y in common)
    total_ni = sum(ni_d[y] for y in common if ni_d[y] > 0)
    if total_ni <= 0:
        return None
    return abs(total_div) / total_ni    # dividends 通常是負數(現金流出)


def retained_earnings_test(
    facts: dict, n: int = 5
) -> tuple[float | None, float | None, float | None]:
    """過去 N 年留存盈餘 vs book equity 成長。

    回傳 (retained_earnings, equity_growth, ratio):
      - retained_earnings: sum(NI - Dividends) over n years
      - equity_growth:     book equity_now - book equity_{n_years_ago}
      - ratio:             equity_growth / retained_earnings

    Buffett 標準:ratio 越高代表留存越有效率(每留 $1 創造 >$1 帳面價值)。
    < 1: 留存被買回庫藏股或減值消耗(視情況可能是好事或壞事)
    > 1: 帳面價值複利效率高
    """
    ni = sec_metrics._annual_series(facts, "NetIncome")
    div = sec_metrics._annual_series(facts, "Dividends")
    eq = sec_metrics._annual_series(facts, "TotalEquity")
    if not ni or not eq:
        return None, None, None
    ni_d = dict(ni)
    eq_d = dict(eq)
    div_d = dict(div) if div else {}

    eq_years = sorted(eq_d.keys())
    if len(eq_years) < n + 1:
        return None, None, None
    latest = eq_years[-1]
    baseline = eq_years[-(n + 1)]
    equity_growth = eq_d[latest] - eq_d[baseline]

    # 累計這 n 年的 retained earnings
    period_years = [y for y in eq_years if baseline < y <= latest]
    retained = 0.0
    for y in period_years:
        if y in ni_d:
            ni_y = ni_d[y]
            div_y = abs(div_d.get(y, 0.0))
            retained += ni_y - div_y

    if retained == 0:
        return retained, equity_growth, None
    ratio = equity_growth / retained
    return retained, equity_growth, ratio


# ---------- 綜合分級 ----------

def grade(profile: ManagementProfile) -> tuple[str, list[str]]:
    """A/B/C/D 綜合分級 + 理由。

    A: 多項指標亮眼(BVPS CAGR > 12%、payout 適中、留存效率 > 0.8)
    B: 正常公司
    C: 有警訊(留存效率低、BVPS 停滯)
    D: 明顯資本破壞(BVPS 下滑或留存效率 < 0)
    """
    reasons: list[str] = []
    score = 0    # 0=平均;+/-

    if profile.bvps_cagr_5y is not None:
        if profile.bvps_cagr_5y >= 0.12:
            score += 2
            reasons.append(f"BVPS 5y CAGR {profile.bvps_cagr_5y*100:.1f}% (Buffett 標準級)")
        elif profile.bvps_cagr_5y >= 0.07:
            score += 1
            reasons.append(f"BVPS 5y CAGR {profile.bvps_cagr_5y*100:.1f}% (穩健)")
        elif profile.bvps_cagr_5y >= 0:
            reasons.append(f"BVPS 5y CAGR {profile.bvps_cagr_5y*100:.1f}% (慢)")
        else:
            score -= 2
            reasons.append(
                f"BVPS 5y CAGR {profile.bvps_cagr_5y*100:.1f}% (帳面價值縮水,警訊)"
            )

    if profile.retention_efficiency is not None:
        if profile.retention_efficiency >= 1.5:
            score += 1
            reasons.append(f"留存效率 {profile.retention_efficiency:.2f} (高效複利)")
        elif profile.retention_efficiency >= 0.8:
            reasons.append(f"留存效率 {profile.retention_efficiency:.2f} (合格)")
        elif profile.retention_efficiency >= 0:
            score -= 1
            reasons.append(
                f"留存效率 {profile.retention_efficiency:.2f} (留存被消耗或大量買回)"
            )
        else:
            score -= 2
            reasons.append(
                f"留存效率 {profile.retention_efficiency:.2f} (資本破壞警訊)"
            )

    if profile.dividend_payout_ratio_5y is not None:
        # 過低 (<10%) 不一定好(可能不發是因賠錢);過高 (>80%) 也警示
        if 0.20 <= profile.dividend_payout_ratio_5y <= 0.60:
            reasons.append(
                f"股利配發 {profile.dividend_payout_ratio_5y*100:.0f}% (健康)"
            )
        elif profile.dividend_payout_ratio_5y > 0.80:
            score -= 1
            reasons.append(
                f"股利配發 {profile.dividend_payout_ratio_5y*100:.0f}% 偏高 (內部投資不足?)"
            )

    if score >= 3:
        return "A", reasons
    if score >= 1:
        return "B", reasons
    if score >= -1:
        return "C", reasons
    return "D", reasons


# ---------- yfinance CEO ----------

def fetch_ceo_info_from_yfinance(ticker: str) -> dict[str, Any]:
    """從 yfinance .info 取 CEO 基本資料。"""
    try:
        from . import data_loader
        info = data_loader._fetch_yf_info(ticker)
    except Exception:
        return {}
    if not info:
        return {}
    officers = info.get("companyOfficers") or []
    # 找 CEO/President
    ceo = None
    for o in officers:
        title = (o.get("title") or "").lower()
        if any(k in title for k in ["ceo", "chief executive"]):
            ceo = o
            break
    if not ceo and officers:
        ceo = officers[0]   # 沒明確 CEO 就取第一位
    if not ceo:
        return {}

    out: dict[str, Any] = {
        "ceo_name": ceo.get("name"),
        "ceo_title": ceo.get("title"),
    }
    # tenure proxy: yfinance 沒直接給,留白
    return out


# ---------- 主入口 ----------

def evaluate(ticker: str) -> ManagementProfile:
    """主入口:組裝 ManagementProfile。"""
    profile = ManagementProfile()
    facts = sec_api.get_facts(ticker)
    if not facts:
        # 沒 SEC 資料 → 至少從 yfinance 拿 CEO 名字
        info = fetch_ceo_info_from_yfinance(ticker)
        profile.ceo_name = info.get("ceo_name")
        profile.ceo_title = info.get("ceo_title")
        return profile

    cagr, equity_growth_total = bvps_cagr(facts, n=5)
    profile.bvps_cagr_5y = cagr
    payout = dividend_payout_ratio(facts, n=5)
    profile.dividend_payout_ratio_5y = payout

    retained, eq_growth, ratio = retained_earnings_test(facts, n=5)
    profile.retained_earnings_5y = retained
    profile.book_equity_growth_5y = eq_growth
    profile.retention_efficiency = ratio

    # CEO 資訊
    info = fetch_ceo_info_from_yfinance(ticker)
    profile.ceo_name = info.get("ceo_name")
    profile.ceo_title = info.get("ceo_title")

    # 分級
    profile.grade, profile.grade_reasons = grade(profile)
    return profile
