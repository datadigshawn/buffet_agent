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

def _resolve_threshold(rule: dict, td: TickerData, rules_root: dict | None = None) -> Any:
    """套用 industry_overrides:若 td.industry_class 在 rules.industry_overrides[<class>][<rule_id>]
    有 threshold,用該值取代。沒命中回原 threshold。"""
    if rules_root is None:
        return rule.get("threshold")
    ind_class = getattr(td, "industry_class", "general")
    if ind_class == "general":
        return rule.get("threshold")
    overrides = rules_root.get("industry_overrides", {}).get(ind_class, {})
    rule_override = overrides.get(rule["id"], {})
    return rule_override.get("threshold", rule.get("threshold"))


def evaluate_rule(rule: dict, td: TickerData, rules_root: dict | None = None) -> RuleResult:
    rid = rule["id"]
    name = rule["name"]
    weight = rule["weight"]
    source = rule.get("source_concept", "")

    # R8 是 OR 條件
    if rule.get("op") == "or":
        actual_text = []
        passed = False
        any_data = False
        for cond in rule["conditions"]:
            v = getattr(td, cond["field"], None)
            actual_text.append(f"{cond['field']}={v}")
            if v is not None:
                any_data = True
                if _apply_op(v, cond["op"], cond["threshold"]):
                    passed = True
                    break
        # 全部條件都缺資料 → skipped (避免 ETF 之類的 ticker 拿這條當「失敗」)
        if not any_data:
            return RuleResult(
                rule_id=rid, name=name, field=rule["field"], op="or",
                threshold=0, weight=weight, actual=None,
                passed=False, points=0, skipped=True,
                source_concept=source, note="缺值",
            )
        return RuleResult(
            rule_id=rid, name=name, field=rule["field"], op="or",
            threshold=0, weight=weight, actual=None,
            passed=passed, points=weight if passed else 0,
            source_concept=source, note="; ".join(actual_text),
        )

    field = rule["field"]
    actual = getattr(td, field, None)
    threshold = _resolve_threshold(rule, td, rules_root)
    if actual is None:
        return RuleResult(
            rule_id=rid, name=name, field=field, op=rule["op"],
            threshold=threshold, weight=weight, actual=None,
            passed=False, points=0, skipped=True,
            source_concept=source, note="缺值",
        )
    passed = _apply_op(actual, rule["op"], threshold)
    note = ""
    if threshold != rule.get("threshold"):
        note = f"industry={td.industry_class} override threshold={threshold}"
    return RuleResult(
        rule_id=rid, name=name, field=field, op=rule["op"],
        threshold=threshold, weight=weight,
        actual=float(actual) if not isinstance(actual, bool) else actual,
        passed=passed, points=weight if passed else 0,
        source_concept=source, note=note,
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

def _evaluate_industry_specific(td: TickerData, rules: dict) -> list[BonusResult]:
    """P2-3:依 td.industry_class 啟用對應 bonus 規則。

    一般 (general) / 保險業 (insurance,目前無專屬規則) → 回空 list。
    """
    out: list[BonusResult] = []
    ind = getattr(td, "industry_class", "general")
    industry_rules = (rules.get("industry_specific_rules") or {}).get(ind, [])
    for r in industry_rules:
        rid = r["id"]
        name = r["name"]
        weight = r["weight"]
        source = r.get("source_concept", f"industry_{ind}")
        actual = getattr(td, r["field"], None)
        if actual is None:
            out.append(BonusResult(
                rule_id=rid, name=name, earned=False, points=0,
                source_concept=source,
            ))
            continue
        op = OPS.get(r["op"])
        try:
            passed = bool(op and op(float(actual), float(r["threshold"])))
        except (TypeError, ValueError):
            passed = False
        out.append(BonusResult(
            rule_id=rid, name=name, earned=passed,
            points=weight if passed else 0,
            source_concept=source,
        ))
    return out


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

        elif rid == "B2":  # ROIC 5y avg > 20%
            if td.roic_5y_avg is not None and td.roic_5y_avg > 0.20:
                earned = True

        elif rid == "B4":  # 其他價值名家持有
            if td.other_value_investors:
                earned = True

        elif rid == "B5":  # 連續 10 年股利成長
            if td.div_growth_streak >= 10:
                earned = True

        elif rid == "B6":  # ROE 10 年持續性 >= 0.8
            if td.roe_consistency_10y is not None and td.roe_consistency_10y >= 0.8:
                earned = True

        # B3 (CEO 持股) 仍未實作 — 需 SEC Form 4

        out.append(BonusResult(
            rule_id=rid, name=name, earned=earned,
            points=points if earned else 0, source_concept=source,
        ))
    # P2-3: 行業專屬 bonus
    out.extend(_evaluate_industry_specific(td, rules))
    return out
