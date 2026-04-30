"""對 ticker 跑完整 Buffett 評分流程。

流程:
1. data_loader 載入 TickerData
2. 跑 4 條 hard disqualifiers → 任一觸發 → OUT_OF_CIRCLE
3. 跑 10 條 core rules → 加總 base 分
4. 跑 5 條 soft bonuses → 加 bonus 分
5. 對映 BUY/HOLD/WATCH/AVOID
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from . import data_loader
from . import rules as rules_mod

Bias = Literal["BUY", "HOLD", "WATCH", "AVOID", "OUT_OF_CIRCLE", "INSUFFICIENT_DATA"]

# 規則涵蓋率門檻 — 低於此值改判 INSUFFICIENT_DATA(避免冷門股 / ETF 被誤判 AVOID)
MIN_COVERAGE_PCT = 50  # 0-100 scale,= sum(available_weight) / sum(all_weight)


@dataclass
class Score:
    ticker: str
    bias: Bias
    total: int                       # 0-110 (normalized base + bonus)
    base: int                        # 0-100 (normalized 後的核心分,= earned/available × 100)
    raw_base: int                    # 0-100 (未 normalize 的原始 earned)
    bonus: int                       # 0-18
    coverage_pct: int                # 0-100 (有資料規則的權重佔比)
    rule_results: list[rules_mod.RuleResult]
    disqualifiers: list[rules_mod.DisqualifierResult]
    bonuses: list[rules_mod.BonusResult]
    triggered_disqualifier: str | None = None  # 第一個觸發的 disqualifier 原因
    data: data_loader.TickerData | None = None


def score(ticker: str) -> Score:
    """主入口:給 ticker 算 Buffett 分數。"""
    td = data_loader.load_ticker(ticker)
    rules = rules_mod.load_rules()

    # Step 1: 硬性篩除
    disqs = rules_mod.evaluate_disqualifiers(td, rules)
    triggered = next((d for d in disqs if d.triggered), None)
    if triggered:
        return Score(
            ticker=ticker.upper(),
            bias="OUT_OF_CIRCLE",
            total=0, base=0, raw_base=0, bonus=0, coverage_pct=0,
            rule_results=[], disqualifiers=disqs, bonuses=[],
            triggered_disqualifier=f"{triggered.rule_id}: {triggered.reason}",
            data=td,
        )

    # Step 2: 跑核心規則 (傳 rules dict 給 evaluate_rule 以便套用 industry_overrides)
    rule_results = [rules_mod.evaluate_rule(r, td, rules) for r in rules["core_rules"]]
    raw_base = sum(r.points for r in rule_results)

    # 涵蓋率: 有資料規則的權重 / 總權重
    total_weight = sum(r.weight for r in rule_results) or 1
    available_weight = sum(r.weight for r in rule_results if not r.skipped)
    coverage_pct = round(available_weight / total_weight * 100)

    # Step 3: 跑加分項
    bonuses = rules_mod.evaluate_bonuses(td, rules)
    bonus = sum(b.points for b in bonuses)

    # Step 4: 對映 bias
    # 涵蓋率太低 → INSUFFICIENT_DATA (ETF / 冷門股)
    if coverage_pct < MIN_COVERAGE_PCT:
        return Score(
            ticker=ticker.upper(),
            bias="INSUFFICIENT_DATA",
            total=raw_base + bonus, base=0, raw_base=raw_base,
            bonus=bonus, coverage_pct=coverage_pct,
            rule_results=rule_results, disqualifiers=disqs, bonuses=bonuses,
            data=td,
        )

    # 正常 path: normalize base 到 0-100 (按可評估規則的比例)
    normalized_base = round(raw_base / available_weight * 100) if available_weight else 0
    total = min(normalized_base + bonus, 110)

    th = rules["scoring"]["thresholds"]
    if total >= th["BUY"]:
        bias: Bias = "BUY"
    elif total >= th["HOLD"]:
        bias = "HOLD"
    elif total >= th["WATCH"]:
        bias = "WATCH"
    else:
        bias = "AVOID"

    return Score(
        ticker=ticker.upper(),
        bias=bias, total=total, base=normalized_base,
        raw_base=raw_base, bonus=bonus, coverage_pct=coverage_pct,
        rule_results=rule_results, disqualifiers=disqs, bonuses=bonuses,
        data=td,
    )


def score_watchlist() -> list[Score]:
    """對 stockTracker watchlist 全部跑一遍。"""
    return [score(t) for t in data_loader.watchlist()]
