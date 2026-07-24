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


# ---------- P6: franchise/EPV 專用抽取器 ----------
# 不共用 _annual_series:companyfacts 的 fy/fp 是「申報期」不是「事實所屬期」
# (FY2026 10-K 的 FY2024/25 比較期資料全標 fy=2026, fp=FY),且 get_concept_units
# 的 first-match 在公司中途換 tag 時會斷代。此處按事實的 end 日期鍵定年份、
# 跨別名逐年合併,並可用後期申報的比較期資料回填缺年。B2/R5 沿用舊路徑不受影響。

_P6_TAGS: dict[str, list[str]] = {
    "op_income": ["OperatingIncomeLoss"],
    "income_tax": ["IncomeTaxExpenseBenefit"],
    "net_income": ["NetIncomeLoss"],
    "equity": [
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "StockholdersEquity",
    ],
    "lt_debt": [
        "LongTermDebt", "LongTermDebtNoncurrent", "LongTermNotesPayable",
        # ASU 租賃準則後部分公司 (KO FY2025+) 改用含租賃義務的合併 tag
        "LongTermDebtAndCapitalLeaseObligations",
    ],
    "st_debt": [
        "DebtCurrent", "NotesPayableCurrent", "LongTermDebtCurrent",
        "LongTermDebtAndCapitalLeaseObligationsCurrent", "ShortTermBorrowings",
    ],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
}


def _series_by_end(facts_json: dict, tags: list[str], flow: bool) -> dict[int, float]:
    """{end年: value}。flow=True 要求 duration ≈ 1 年;False 取 instant (無 start)。

    同年多筆取 filed 最新;跨 tag 依 tags 順序,後面的 tag 只回填缺年。
    instant (資產負債表) 限 10-K 系 form,擋掉 10-Q 季末餘額按 end 年混入。
    """
    from datetime import date
    gaap = (facts_json or {}).get("facts", {}).get("us-gaap", {})
    out: dict[int, tuple[str, float]] = {}   # year -> (filed, val)
    for rank, tag in enumerate(tags):
        units = gaap.get(tag, {}).get("units", {})
        for f in units.get("USD", []):
            start, end = f.get("start"), f.get("end")
            if not end or f.get("val") is None:
                continue
            if flow:
                if not start:
                    continue
                try:
                    dur = (date.fromisoformat(end) - date.fromisoformat(start)).days
                except ValueError:
                    continue
                if not 330 <= dur <= 380:
                    continue
            else:
                if start:
                    continue
                if not (f.get("form") or "").startswith("10-K"):
                    continue
            year = int(end[:4])
            filed = f.get("filed") or ""
            prior = out.get(year)
            # 首選 tag 覆蓋一切;次選 tag 只補缺年
            if prior is None or (rank == 0 and filed > prior[0]):
                if prior is not None and rank > 0:
                    continue
                out[year] = (filed, float(f["val"]))
    return {y: v for y, (_, v) in out.items()}


def roic_true_series(facts_json: dict, n: int = 8) -> list[tuple[int, float]]:
    """真 ROIC 年度序列:NOPAT / 投入資本。與 B2 的 ROE 粗算並行 (A/B 對比用)。

    NOPAT_t = OperatingIncome_t × (1 − 有效稅率_t)
      有效稅率 = IncomeTax / (NetIncome + IncomeTax),夾在 [0, 0.35];缺項用 0.21。
      缺 OperatingIncome 的年份跳過 (不退回 NetIncome,避免混口徑)。
    IC_t = TotalEquity_t + LongTermDebt_t + ShortTermDebt_t − Cash_t
      短債/現金缺項視為 0;IC ≤ 0 的年份跳過 (回購扭曲權益時 ROE 無意義,這正是本口徑的價值)。
    """
    op = _series_by_end(facts_json, _P6_TAGS["op_income"], flow=True)
    if not op:
        return []
    tax = _series_by_end(facts_json, _P6_TAGS["income_tax"], flow=True)
    ni = _series_by_end(facts_json, _P6_TAGS["net_income"], flow=True)
    eq = _series_by_end(facts_json, _P6_TAGS["equity"], flow=False)
    ltd = _series_by_end(facts_json, _P6_TAGS["lt_debt"], flow=False)
    std = _series_by_end(facts_json, _P6_TAGS["st_debt"], flow=False)
    cash = _series_by_end(facts_json, _P6_TAGS["cash"], flow=False)
    out: list[tuple[int, float]] = []
    for y in sorted(op):
        if y not in eq:
            continue
        # 覆蓋斷裂防呆:前一年有債務資料、今年兩者皆缺 → 多半是公司換 tag,
        # 算出來的 IC 會低估、ROIC 虛高,寧缺勿錯 (KO FY2025 換租賃合併 tag 即此型)。
        if ltd.get(y) is None and std.get(y) is None and (
            ltd.get(y - 1) is not None or std.get(y - 1) is not None
        ):
            continue
        t, n_i = tax.get(y), ni.get(y)
        if t is not None and n_i is not None and (n_i + t) > 0:
            rate = min(max(t / (n_i + t), 0.0), 0.35)
        else:
            rate = 0.21
        nopat = op[y] * (1 - rate)
        ic = eq[y] + ltd.get(y, 0.0) + std.get(y, 0.0) - cash.get(y, 0.0)
        if ic <= 0:
            continue
        out.append((y, nopat / ic))
    return out[-n:]


def roic_true_5y_avg(facts_json: dict, n: int = 5) -> float | None:
    """影子指標:真 ROIC 近 N 年平均。落檔進 latest.json 供日後 A/B 對比回測。"""
    series = roic_true_series(facts_json)
    if not series:
        return None
    return _avg([v for _, v in series[-n:]])


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
