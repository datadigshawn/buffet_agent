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

Bias = Literal["BUY", "HOLD", "WATCH", "AVOID", "OUT_OF_CIRCLE"]


@dataclass
class Score:
    ticker: str
    bias: Bias
    total: int                       # 0-110
    base: int                        # 0-100
    bonus: int                       # 0-18
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
            total=0, base=0, bonus=0,
            rule_results=[], disqualifiers=disqs, bonuses=[],
            triggered_disqualifier=f"{triggered.rule_id}: {triggered.reason}",
            data=td,
        )

    # Step 2: 跑核心規則
    rule_results = [rules_mod.evaluate_rule(r, td) for r in rules["core_rules"]]
    base = sum(r.points for r in rule_results)

    # Step 3: 跑加分項
    bonuses = rules_mod.evaluate_bonuses(td, rules)
    bonus = sum(b.points for b in bonuses)

    total = min(base + bonus, 110)

    # Step 4: 對映 bias
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
        bias=bias, total=total, base=base, bonus=bonus,
        rule_results=rule_results, disqualifiers=disqs, bonuses=bonuses,
        data=td,
    )


def score_watchlist() -> list[Score]:
    """對 stockTracker watchlist 全部跑一遍。"""
    return [score(t) for t in data_loader.watchlist()]
