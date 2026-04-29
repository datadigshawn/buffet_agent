"""載入 rules.json 並對單一 TickerData 套用規則。

每條規則的輸出 = RuleResult(passed, value, points_earned, ...)。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .data_loader import TickerData, SECTOR_BLACKLIST, TICKER_BLACKLIST, BERKSHIRE_VERIFIED

RULES_PATH = Path(__file__).resolve().parent / "rules.json"


@dataclass
class RuleResult:
    rule_id: str           # "R1"
    name: str              # "ROE >= 15%"
    field: str             # "roe"
    op: str                # ">="
    threshold: float | bool
    weight: int            # 滿分
    actual: float | None   # 實際值
    passed: bool
    points: int            # 取得的分數
    skipped: bool = False  # 缺值
    source_concept: str = ""
    note: str = ""


@dataclass
class DisqualifierResult:
    rule_id: str
    name: str
    triggered: bool
    reason: str = ""
    source_concept: str = ""


@dataclass
class BonusResult:
    rule_id: str
    name: str
    earned: bool
    points: int
    source_concept: str = ""


def load_rules() -> dict[str, Any]:
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))


# ---------- 比較 ops ----------

OPS: dict[str, Callable[[float, float], bool]] = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
}


def _apply_op(actual: float | bool | None, op: str, threshold: float | bool) -> bool:
    if actual is None:
        return False
    if isinstance(threshold, bool):
        return bool(actual) == threshold
    return OPS[op](float(actual), float(threshold))


# ---------- 核心規則 ----------

def evaluate_rule(rule: dict, td: TickerData) -> RuleResult:
    rid = rule["id"]
    name = rule["name"]
    weight = rule["weight"]
    source = rule.get("source_concept", "")

    # R8 是 OR 條件
    if rule.get("op") == "or":
        actual_text = []
        passed = False
        for cond in rule["conditions"]:
            v = getattr(td, cond["field"], None)
            actual_text.append(f"{cond['field']}={v}")
            if v is not None and _apply_op(v, cond["op"], cond["threshold"]):
                passed = True
                break
        return RuleResult(
            rule_id=rid, name=name, field=rule["field"], op="or",
            threshold=0, weight=weight, actual=None,
            passed=passed, points=weight if passed else 0,
            source_concept=source, note="; ".join(actual_text),
        )

    field = rule["field"]
    actual = getattr(td, field, None)
    if actual is None:
        return RuleResult(
            rule_id=rid, name=name, field=field, op=rule["op"],
            threshold=rule["threshold"], weight=weight, actual=None,
            passed=False, points=0, skipped=True,
            source_concept=source, note="缺值",
        )
    passed = _apply_op(actual, rule["op"], rule["threshold"])
    return RuleResult(
        rule_id=rid, name=name, field=field, op=rule["op"],
        threshold=rule["threshold"], weight=weight, actual=float(actual) if not isinstance(actual, bool) else actual,
        passed=passed, points=weight if passed else 0,
        source_concept=source,
    )


# ---------- 硬性 disqualifier ----------

def evaluate_disqualifiers(td: TickerData, rules: dict) -> list[DisqualifierResult]:
    out: list[DisqualifierResult] = []
    for d in rules["hard_disqualifiers"]:
        rid = d["id"]
        name = d["name"]
        source = d.get("source_concept", "")
        triggered = False
        reason = ""

        if rid == "D1":  # 槓桿過高
            de = td.debt_equity
            sector_lower = (td.sector or "").lower()
            is_financial = any(k in sector_lower for k in ["financial", "insurance", "bank", "金融"])
            limit = 5.0 if is_financial else 2.0
            # Berkshire 已驗證的個股放寬(MCO 因高 buyback 致 D/E 帳面爆表是已知例外)
            if td.ticker in BERKSHIRE_VERIFIED:
                limit = 5.0
            if de is not None and de > limit:
                triggered = True
                reason = f"D/E={de:.2f} > {limit}"

        elif rid == "D3":  # 能力圈外:sector + ticker 雙重過濾
            if td.ticker in TICKER_BLACKLIST:
                triggered = True
                reason = f"ticker={td.ticker} 在巴菲特能力圈黑名單"
            elif td.sector and td.sector in SECTOR_BLACKLIST:
                triggered = True
                reason = f"sector={td.sector} (能力圈外)"

        elif rid == "D4":  # 連續虧損
            if td.eps_3y_negative is True:
                triggered = True
                reason = "近 3 年 EPS 持續為負"

        # D2 (衍生品) 暫無資料來源,Phase 3 補

        out.append(DisqualifierResult(
            rule_id=rid, name=name, triggered=triggered,
            reason=reason, source_concept=source,
        ))
    return out


# ---------- 軟性 bonus ----------

def evaluate_bonuses(td: TickerData, rules: dict) -> list[BonusResult]:
    out: list[BonusResult] = []
    for b in rules["soft_bonuses"]:
        rid = b["id"]
        name = b["name"]
        points = b["points"]
        source = b.get("source_concept", "")
        earned = False

        if rid == "B1":  # Berkshire 重倉 > 1%
            if td.berkshire_position_pct and td.berkshire_position_pct > 0.01:
                earned = True

        elif rid == "B4":  # 其他價值名家持有
            if td.other_value_investors:
                earned = True

        # B2/B3/B5 需要額外資料,Phase 3 補

        out.append(BonusResult(
            rule_id=rid, name=name, earned=earned,
            points=points if earned else 0, source_concept=source,
        ))
    return out
