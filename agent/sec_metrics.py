"""從 SEC companyfacts JSON 萃取巴菲特關心的指標。

設計原則:
- 每個函式都接受 raw companyfacts dict (sources/sec.py::get_facts() 的輸出)
- 缺資料一律回 None,呼叫端決定如何 fallback
- 多年序列回 list[(year, value)] (按年遞增排列)
- 比率欄位 normalize 到 0-1 scale (與 data_loader.TickerData 一致)

涵蓋的 Buffett 規則:
- R5 D/E:用 LongTermDebt / TotalEquity (純長期負債,不含營業負債)
- R6 FCF margin:多年 owner earnings = (OperatingCF - Capex) / Revenue 平均
- R7 buyback yield:近 1 年 shares outstanding 變動率
- B2 ROIC 5y:NetIncome / (TotalAssets - TotalLiabilities) 連 5 年平均
- B5 dividend growth:DividendsPerShare 連續成長年數
- 持續性:任何 metric × N 年通過門檻的比例 (ratio_above_threshold)
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from .sources import sec as sec_api

log = logging.getLogger(__name__)


# ---------- 通用工具 ----------

def _annual_series(facts_json: dict, our_name: str, unit_pref: tuple[str, ...] = ("USD",)) -> list[tuple[int, float]]:
    """把某個 concept 攤平成 [(fiscal_year, value)] 並按年排序去重。

    取 fp=FY (full year) 的 facts (含 10-K 與 restated 10-Q),不限 form。
    某些公司 (NVDA Capex) 後續年份只在 10-Q YTD 出現,strict 10-K 會丟失資料。
    去重邏輯:同 fiscal_year 多筆 → 取最新 filed 的那一筆。
    """
    units = sec_api.get_concept_units(facts_json, our_name)
    if not units:
        return []
    # 偏好順序的單位
    chosen = None
    for u in unit_pref:
        if u in units:
            chosen = units[u]
            break
    if chosen is None:
        return []

    # 同年取最新 filed (form=10-K 優先)
    by_year: dict[int, dict] = {}
    for f in chosen:
        if f.get("fp") != "FY":
            continue
        year = f.get("fy")
        if not isinstance(year, int):
            continue
        prior = by_year.get(year)
        # 取 latest filed;同 filed 日期則 form=10-K 優先 (更權威)
        if prior is None:
            by_year[year] = f
        else:
            new_filed = f.get("filed") or ""
            old_filed = prior.get("filed") or ""
            if new_filed > old_filed:
                by_year[year] = f
            elif new_filed == old_filed and f.get("form") == "10-K":
                by_year[year] = f
    return sorted(
        ((y, float(f["val"])) for y, f in by_year.items() if f.get("val") is not None),
        key=lambda x: x[0],
    )


def _last_n(series: list[tuple[int, float]], n: int) -> list[tuple[int, float]]:
    return series[-n:] if series else []


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


# ---------- 指標 ----------

def long_term_debt_to_equity(facts_json: dict) -> float | None:
    """R5 真實版:LongTermDebt / StockholdersEquity (取最新一年)。"""
    debt = _annual_series(facts_json, "LongTermDebt")
    equity = _annual_series(facts_json, "TotalEquity")
    if not debt or not equity:
        return None
    # 取兩者都有的最新一年
    debt_d = dict(debt)
    eq_d = dict(equity)
    common_years = sorted(set(debt_d) & set(eq_d), reverse=True)
    if not common_years:
        return None
    y = common_years[0]
    if eq_d[y] <= 0:
        return None
    return debt_d[y] / eq_d[y]


def owner_earnings_margin(facts_json: dict, n: int = 5) -> float | None:
    """R6 真實版:近 N 年 (OperatingCF - Capex) / Revenue 平均。

    Buffett 的 owner earnings 應該再扣 maintenance capex,但 SEC 沒分,我們用全 capex 保守估計。
    """
    ocf = _annual_series(facts_json, "OperatingCashFlow")
    capex = _annual_series(facts_json, "Capex")
    rev = _annual_series(facts_json, "Revenues")
    if not ocf or not capex or not rev:
        return None
    ocf_d = dict(ocf)
    cap_d = dict(capex)
    rev_d = dict(rev)
    common_years = sorted(set(ocf_d) & set(cap_d) & set(rev_d), reverse=True)
    if not common_years:
        return None
    margins = []
    for y in common_years[:n]:
        if rev_d[y] <= 0:
            continue
        # capex 在 cash flow statement 通常為正數 (流出),用 ocf - capex 即 owner earnings
        oe = ocf_d[y] - cap_d[y]
        margins.append(oe / rev_d[y])
    return _avg(margins)


def buyback_yield(facts_json: dict) -> float | None:
    """R7 真實版:近一年 shares outstanding YoY 縮減率。

    >0 表示有回購;<0 表示有增發 (稀釋)。
    """
    shares = _annual_series(facts_json, "SharesOutstanding", unit_pref=("shares",))
    if len(shares) < 2:
        return None
    last = shares[-1][1]
    prev = shares[-2][1]
    if prev <= 0:
        return None
    # 縮減率:前期股數 → 本期股數,變化的相對值
    return (prev - last) / prev


def roic_5y_avg(facts_json: dict, n: int = 5) -> float | None:
    """B2:NetIncome / (TotalAssets - TotalLiabilities) 連 N 年平均。

    這實際上是 ROE 不是嚴格的 ROIC (沒扣現金、沒加長期負債);Buffett 公開講的多半是這種粗算。
    """
    ni = _annual_series(facts_json, "NetIncome")
    assets = _annual_series(facts_json, "TotalAssets")
    liab = _annual_series(facts_json, "TotalLiabilities")
    if not ni or not assets or not liab:
        return None
    ni_d = dict(ni)
    a_d = dict(assets)
    l_d = dict(liab)
    common_years = sorted(set(ni_d) & set(a_d) & set(l_d), reverse=True)
    if not common_years:
        return None
    rocs = []
    for y in common_years[:n]:
        invested = a_d[y] - l_d[y]
        if invested <= 0:
            continue
        rocs.append(ni_d[y] / invested)
    return _avg(rocs)


def dividend_growth_streak(facts_json: dict) -> int:
    """B5:CommonStockDividendsPerShareDeclared 連續成長年數 (從最新往回看)。"""
    series = _annual_series(facts_json, "DividendsPerShare", unit_pref=("USD/shares",))
    if len(series) < 2:
        return 0
    streak = 0
    # 從最新往回看
    for i in range(len(series) - 1, 0, -1):
        if series[i][1] > series[i - 1][1]:
            streak += 1
        else:
            break
    return streak


def consistency(facts_json: dict, our_name: str, threshold: float, n: int = 10,
                ratio_field: str | None = None) -> float | None:
    """持續性:過去 N 年中,該 metric > threshold 的比例 (0-1)。

    如果 ratio_field 提供了 (例如 ROE = NetIncome/Equity),會做比例計算。
    若是絕對值序列直接比。
    """
    if ratio_field:
        # 計算逐年比率序列
        num = _annual_series(facts_json, our_name)
        den = _annual_series(facts_json, ratio_field)
        if not num or not den:
            return None
        num_d = dict(num)
        den_d = dict(den)
        common = sorted(set(num_d) & set(den_d), reverse=True)
        ratios = []
        for y in common[:n]:
            if den_d[y] > 0:
                ratios.append(num_d[y] / den_d[y])
        if not ratios:
            return None
        passed = sum(1 for r in ratios if r > threshold)
        return passed / len(ratios)
    else:
        series = _annual_series(facts_json, our_name)
        if not series:
            return None
        recent = _last_n(series, n)
        if not recent:
            return None
        passed = sum(1 for _, v in recent if v > threshold)
        return passed / len(recent)


def years_of_data(facts_json: dict) -> int:
    """有多少年的 10-K 申報資料 (拿 NetIncome 或 Revenue 都行)。"""
    ni = _annual_series(facts_json, "NetIncome")
    rev = _annual_series(facts_json, "Revenues")
    return max(len(ni), len(rev))


# ---------- 整合 ----------

def extract_buffett_metrics(ticker: str) -> dict[str, Any]:
    """主入口:給 ticker,回傳一包 SEC 萃取的指標。

    回傳字典含:
        long_term_de:        R5 真實 D/E (None 表沒資料)
        owner_earnings_5y:   R6 5 年平均 owner earnings margin
        buyback_yield:       R7 回購率
        roic_5y_avg:         B2 5 年平均 ROIC
        div_growth_streak:   B5 連續股利成長年數
        roe_consistency_10y: 過去 10 年 ROE > 15% 的比例
        years_available:     有多少年 10-K 資料
        source:              "sec" / "sec_partial" / "no_data"
    """
    facts = sec_api.get_facts(ticker)
    if not facts:
        return {
            "long_term_de": None,
            "owner_earnings_5y": None,
            "buyback_yield": None,
            "roic_5y_avg": None,
            "div_growth_streak": 0,
            "roe_consistency_10y": None,
            "years_available": 0,
            "source": "no_data",
        }
    return {
        "long_term_de": long_term_debt_to_equity(facts),
        "owner_earnings_5y": owner_earnings_margin(facts, n=5),
        "buyback_yield": buyback_yield(facts),
        "roic_5y_avg": roic_5y_avg(facts, n=5),
        "div_growth_streak": dividend_growth_streak(facts),
        "roe_consistency_10y": consistency(
            facts, "NetIncome", 0.15, n=10, ratio_field="TotalEquity"
        ),
        "years_available": years_of_data(facts),
        "source": "sec" if years_of_data(facts) >= 5 else "sec_partial",
    }
