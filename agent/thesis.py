"""個股 thesis tracking (Phase 5 P0-2)。

Buffett 紀律:「**決策前寫下理由,之後檢驗那個理由還在不在**」。

機制:
1. 每檔第一次拿到 BUY 時,LLM 寫一份 thesis (200 字) → 存 output/theses/<TICKER>.json
2. 每天 scan 時,對所有有 thesis 的 ticker 做機械式檢驗 (條件式比對)
3. 任一 condition 被違反 → 觸發 thesis_broken alert (Buffett 著名的「賣出時機」之一)

機械式檢驗的好處:
- 不必每天打 LLM (省成本)
- 條件可預測 / 可審計
- LLM 只在第一次 BUY 時介入,寫下「為什麼這檔現在值得 BUY」的論文

required_conditions 範例:
  - score >= 60                     # 不能跌到 HOLD 之下
  - bias not OUT_OF_CIRCLE          # 沒觸發硬性 disqualifier
  - roe_consistency_10y >= 0.6      # 持續性沒破
  - berkshire_holds 仍 True (若當初進場是 BRK 持股)
  - margin_of_safety_pct > -0.50    # DCF 不能變成嚴重高估 (用一個寬容上限)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
THESES_DIR = REPO_ROOT / "output" / "theses"


# ---------- 資料結構 ----------

@dataclass
class Condition:
    """一個 thesis 條件式。違反就觸發 thesis_broken。"""
    metric: str           # ticker 量化欄位 (e.g. score, roe_consistency_10y)
    op: str               # ">=" "<=" "==" "!=" ">" "<"
    value: Any
    description: str      # 人話解釋

    def to_dict(self) -> dict:
        return asdict(self)


OPS = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    ">":  lambda a, b: a > b,
    "<":  lambda a, b: a < b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


def _check_condition(cond: Condition, ticker_data: dict) -> bool:
    """True = 條件成立(thesis 仍有效)。
    缺值視為條件成立(避免暫時抓不到資料就誤觸發 thesis_broken)。
    """
    actual = _resolve_metric(cond.metric, ticker_data)
    if actual is None:
        return True
    op_fn = OPS.get(cond.op)
    if op_fn is None:
        log.warning("unknown op %s in condition", cond.op)
        return True
    try:
        return op_fn(actual, cond.value)
    except (TypeError, ValueError):
        return True


def _resolve_metric(name: str, data: dict) -> Any:
    """從 verdict JSON 取出 metric 值。支援巢狀 (qualitative.management_grade)。"""
    if "." in name:
        parts = name.split(".")
        cur: Any = data
        for p in parts:
            if isinstance(cur, dict):
                cur = cur.get(p)
            else:
                return None
        return cur
    return data.get(name)


@dataclass
class Thesis:
    """Buffett-style 投資論文。每檔 BUY 第一次出現時建立,之後每日驗證。"""
    ticker: str
    first_buy_date: str               # ISO date (UTC)
    bias_at_buy: str                  # BUY (通常)
    score_at_buy: int
    thesis_text: str                  # LLM 寫的 ~200 字理由
    required_conditions: list[Condition]
    key_metrics_at_buy: dict[str, Any]
    last_verified_date: str
    last_verified_status: str = "valid"   # valid / broken
    broken_conditions: list[str] = field(default_factory=list)
    thesis_age_days: int = 0
    written_by: str = "unknown"       # LLM backend / "default" (規則模板)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["required_conditions"] = [c.to_dict() if not isinstance(c, dict) else c
                                    for c in self.required_conditions]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Thesis":
        conds = [Condition(**c) if isinstance(c, dict) else c
                 for c in d.get("required_conditions", [])]
        return cls(
            ticker=d["ticker"],
            first_buy_date=d["first_buy_date"],
            bias_at_buy=d.get("bias_at_buy", "BUY"),
            score_at_buy=d.get("score_at_buy", 0),
            thesis_text=d.get("thesis_text", ""),
            required_conditions=conds,
            key_metrics_at_buy=d.get("key_metrics_at_buy", {}),
            last_verified_date=d.get("last_verified_date", ""),
            last_verified_status=d.get("last_verified_status", "valid"),
            broken_conditions=d.get("broken_conditions", []),
            thesis_age_days=d.get("thesis_age_days", 0),
            written_by=d.get("written_by", "unknown"),
        )


@dataclass
class ThesisStatus:
    """每日 process() 的輸出。"""
    ticker: str
    state: str                # "new" / "valid" / "broken" / "skipped"
    thesis: Thesis | None = None
    broken_conditions: list[str] = field(default_factory=list)


# ---------- 預設條件模板 ----------

def default_conditions(verdict_today: dict) -> list[Condition]:
    """根據今天的 verdict,為一個 BUY 候選人合成預設條件。

    通用條件:
      - score >= 60          # 不可跌到 HOLD 以下
      - bias != OUT_OF_CIRCLE  # 不可觸發硬性 disqualifier
      - bias != INSUFFICIENT_DATA  # 不可變成資料不足

    動態條件 (有資料才加):
      - 若當前有 ROE 持續性 >= 0.8,要求未來不可低於 0.6
      - 若當前 berkshire_holds=True,要求保持 True (Berkshire 賣出 = 重大訊號)
      - 若當前 industry_class 不是 INSUFFICIENT,要求保持
    """
    conds: list[Condition] = [
        Condition(metric="score", op=">=", value=60,
                  description="量化分數不可跌到 HOLD 以下"),
        Condition(metric="bias", op="!=", value="OUT_OF_CIRCLE",
                  description="不可觸發硬性 disqualifier"),
        Condition(metric="bias", op="!=", value="INSUFFICIENT_DATA",
                  description="資料完整度不可下降"),
    ]
    roe_c = verdict_today.get("qualitative") or {}
    # 取自 SEC 持續性 (如果有,當作底線指標)
    roe10 = verdict_today.get("roe_consistency_10y")
    if roe10 is not None and roe10 >= 0.8:
        conds.append(Condition(
            metric="roe_consistency_10y", op=">=", value=0.6,
            description=f"ROE 10 年持續性 (進場時 {roe10:.0%}) 不可跌破 60%",
        ))
    if verdict_today.get("berkshire_holds"):
        conds.append(Condition(
            metric="berkshire_holds", op="==", value=True,
            description="Berkshire 持有狀態不可變化 (賣出 = 重大訊號)",
        ))
    return conds


# ---------- 驗證邏輯 ----------

def verify(thesis: Thesis, verdict_today: dict, today_iso: str) -> ThesisStatus:
    """檢驗 thesis 對今天 verdict 是否還成立。"""
    broken = []
    for cond in thesis.required_conditions:
        if not _check_condition(cond, verdict_today):
            actual = _resolve_metric(cond.metric, verdict_today)
            broken.append(
                f"{cond.metric} {cond.op} {cond.value} (現在 = {actual}) — {cond.description}"
            )

    # 計算天數差
    try:
        first = datetime.fromisoformat(thesis.first_buy_date)
        today = datetime.fromisoformat(today_iso)
        thesis.thesis_age_days = max(0, (today - first).days)
    except (ValueError, TypeError):
        pass

    thesis.last_verified_date = today_iso
    if broken:
        thesis.last_verified_status = "broken"
        thesis.broken_conditions = broken
        return ThesisStatus(
            ticker=thesis.ticker, state="broken",
            thesis=thesis, broken_conditions=broken,
        )
    thesis.last_verified_status = "valid"
    thesis.broken_conditions = []
    return ThesisStatus(ticker=thesis.ticker, state="valid", thesis=thesis)


# ---------- 檔案 IO ----------

def thesis_path(ticker: str) -> Path:
    return THESES_DIR / f"{ticker.upper()}.json"


def load_thesis(ticker: str) -> Thesis | None:
    p = thesis_path(ticker)
    if not p.exists():
        return None
    try:
        return Thesis.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError, KeyError) as e:
        log.warning("cannot read thesis for %s: %s", ticker, e)
        return None


def save_thesis(thesis: Thesis) -> None:
    THESES_DIR.mkdir(parents=True, exist_ok=True)
    thesis_path(thesis.ticker).write_text(
        json.dumps(thesis.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------- 主流程 ----------

def process(verdict_today: dict, llm_writer=None) -> ThesisStatus:
    """每檔 verdict 跑一次:

    - 沒 thesis + 今天是 BUY → 寫新 thesis (透過 llm_writer,沒接 LLM 就用模板)
    - 有 thesis + 今天是 BUY/HOLD → 驗證
    - 有 thesis + 今天 OUT_OF_CIRCLE/AVOID → 驗證 (這些都會違反條件 → broken)
    - 沒 thesis + 今天非 BUY → skipped

    llm_writer: callable(ticker, context) -> str | None,給 LLM 寫 thesis_text。
                None 時用 default 模板。
    """
    ticker = verdict_today["ticker"]
    today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    bias = verdict_today.get("bias", "")
    existing = load_thesis(ticker)

    if existing is None:
        # 沒 thesis
        if bias != "BUY":
            return ThesisStatus(ticker=ticker, state="skipped")
        # 今天首次 BUY → 寫 thesis
        thesis = _create_thesis(verdict_today, today_iso, llm_writer)
        save_thesis(thesis)
        return ThesisStatus(ticker=ticker, state="new", thesis=thesis)

    # 既有 thesis → 驗證
    status = verify(existing, verdict_today, today_iso)
    save_thesis(status.thesis)  # 更新 last_verified_*
    return status


def _create_thesis(verdict: dict, today_iso: str, llm_writer) -> Thesis:
    """合成新 thesis (LLM 或 template fallback)。"""
    ticker = verdict["ticker"]
    score = verdict.get("score", 0)
    bias = verdict.get("bias", "BUY")
    conds = default_conditions(verdict)

    # 截取 key metrics
    key_metrics = {
        k: verdict.get(k) for k in (
            "score", "coverage_pct", "berkshire_holds",
            "roe_consistency_10y", "intrinsic_value_per_share",
            "margin_of_safety_pct", "industry_class",
        ) if verdict.get(k) is not None
    }

    thesis_text = ""
    written_by = "default"
    if llm_writer is not None:
        try:
            generated = llm_writer(ticker, verdict)
            if generated:
                thesis_text = generated
                written_by = "llm"
        except Exception as e:
            log.warning("LLM thesis write failed for %s: %s", ticker, e)

    if not thesis_text:
        # Fallback 模板
        passed = ", ".join((verdict.get("passed_rules") or [])[:5])
        bonuses = ", ".join((verdict.get("earned_bonuses") or []))
        thesis_text = (
            f"{ticker} 在 {today_iso} 首次拿到 {bias} 評分 {score}/110。"
            f"通過規則: {passed}。"
            f"加分項: {bonuses or '無'}。"
            f"覆蓋率 {verdict.get('coverage_pct', '?')}%。"
            f"持有理由維持有效的條件:量化分數不下滑、未觸發 disqualifier、"
            f"關鍵持續性指標不衰退、Berkshire 持股(若有)維持。"
        )

    return Thesis(
        ticker=ticker,
        first_buy_date=today_iso,
        bias_at_buy=bias,
        score_at_buy=int(score),
        thesis_text=thesis_text,
        required_conditions=conds,
        key_metrics_at_buy=key_metrics,
        last_verified_date=today_iso,
        last_verified_status="valid",
        thesis_age_days=0,
        written_by=written_by,
    )


def process_verdicts(verdicts: list[dict], llm_writer=None) -> list[ThesisStatus]:
    """批次處理一日所有 verdicts。"""
    return [process(v, llm_writer=llm_writer) for v in verdicts]
