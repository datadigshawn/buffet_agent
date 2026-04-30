"""變動偵測 (Phase 5 P0-1)。

比對昨日 vs 今日 scan,產出 actionable 變動清單。

6 種變動類型:
  - bias_changed:        bias 升降級 (HOLD→BUY、BUY→HOLD/AVOID/OUT_OF_CIRCLE)
  - new_top10:           今天首次擠進 BUY 前 10 名
  - dropped_from_top10:  昨天在 BUY 前 10、今天跌出
  - mos_first_positive:  DCF 安全邊際首次轉正(便宜進場訊號)
  - disqualifier_triggered: 從 BUY/HOLD 跌到 OUT_OF_CIRCLE
  - thesis_broken:       已建立 thesis 的 ticker 條件被違反 (P0-2)
  - news_alert:          近 7 天新聞 sentiment / 重大事件異常 (P1-3)
  - regression_detected:  回測連 N 週 alpha < threshold (P2-2)

不算變動的事(避免噪音):
  - 分數小幅升降(<5 分)但 bias 沒變
  - 新 ticker 第一次出現(沒昨天可比就不算變動)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# bias 嚴重度排序(用來判斷升降級方向)
BIAS_RANK = {
    "BUY": 5,
    "HOLD": 4,
    "WATCH": 3,
    "AVOID": 2,
    "OUT_OF_CIRCLE": 1,
    "INSUFFICIENT_DATA": 0,
}

TOP_N = 10


@dataclass
class Alert:
    ticker: str
    type: str           # 對應上述 5 種變動類型
    severity: str       # "high" / "medium" / "low"
    summary: str        # 一句話摘要
    yesterday: dict[str, Any] = field(default_factory=dict)
    today: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _index_by_ticker(scan: dict) -> dict[str, dict]:
    return {v["ticker"]: v for v in scan.get("verdicts", [])}


def _top_n_buys(scan: dict, n: int = TOP_N) -> list[str]:
    buys = [v for v in scan.get("verdicts", []) if v.get("bias") == "BUY"]
    buys.sort(key=lambda v: -v.get("score", 0))
    return [v["ticker"] for v in buys[:n]]


def _direction(old_bias: str, new_bias: str) -> str:
    """回 'up' / 'down' / 'lateral'。"""
    o = BIAS_RANK.get(old_bias, 0)
    n = BIAS_RANK.get(new_bias, 0)
    if n > o:
        return "up"
    if n < o:
        return "down"
    return "lateral"


def detect(yesterday: dict | None, today: dict) -> list[Alert]:
    """主入口:比對兩個 scan dict,回傳 alerts。

    yesterday 為 None 時(首日無對比)回空 list。
    """
    if not yesterday:
        return []

    y_idx = _index_by_ticker(yesterday)
    t_idx = _index_by_ticker(today)

    y_top = set(_top_n_buys(yesterday))
    t_top = set(_top_n_buys(today))

    alerts: list[Alert] = []

    # 1. bias_changed + disqualifier_triggered
    for ticker, t_v in t_idx.items():
        y_v = y_idx.get(ticker)
        if not y_v:
            continue  # 新 ticker 不算變動

        old_bias = y_v.get("bias", "")
        new_bias = t_v.get("bias", "")
        if old_bias == new_bias:
            continue

        # OUT_OF_CIRCLE 從 BUY/HOLD 觸發 → 高嚴重度單獨標
        if new_bias == "OUT_OF_CIRCLE" and old_bias in ("BUY", "HOLD"):
            alerts.append(Alert(
                ticker=ticker,
                type="disqualifier_triggered",
                severity="high",
                summary=f"{ticker}: {old_bias} → OUT_OF_CIRCLE ({t_v.get('triggered_disqualifier','觸發 disqualifier')})",
                yesterday={"bias": old_bias, "score": y_v.get("score")},
                today={"bias": new_bias, "score": t_v.get("score"),
                       "trigger": t_v.get("triggered_disqualifier")},
            ))
            continue

        direction = _direction(old_bias, new_bias)
        # 只關注 BUY/HOLD 邊界附近的升降級
        # (WATCH↔AVOID 之間移動訊號弱,跳過)
        if old_bias in ("BUY", "HOLD") or new_bias in ("BUY", "HOLD"):
            severity = "high" if direction == "up" and new_bias == "BUY" else "medium"
            arrow = "↑" if direction == "up" else "↓"
            alerts.append(Alert(
                ticker=ticker,
                type="bias_changed",
                severity=severity,
                summary=f"{ticker}: {old_bias} {arrow} {new_bias} (score {y_v.get('score','?')}→{t_v.get('score','?')})",
                yesterday={"bias": old_bias, "score": y_v.get("score")},
                today={"bias": new_bias, "score": t_v.get("score"),
                       "recommendation": t_v.get("recommendation")},
            ))

    # 2. new_top10 + dropped_from_top10
    new_in = t_top - y_top
    dropped = y_top - t_top
    for ticker in sorted(new_in):
        t_v = t_idx.get(ticker, {})
        alerts.append(Alert(
            ticker=ticker,
            type="new_top10",
            severity="medium",
            summary=f"{ticker} 新進 BUY 前 {TOP_N} 名 (score {t_v.get('score')})",
            today={"score": t_v.get("score"), "rank": _top_n_buys(today).index(ticker) + 1
                   if ticker in _top_n_buys(today) else None,
                   "recommendation": t_v.get("recommendation")},
        ))
    for ticker in sorted(dropped):
        y_v = y_idx.get(ticker, {})
        t_v = t_idx.get(ticker, {})
        alerts.append(Alert(
            ticker=ticker,
            type="dropped_from_top10",
            severity="low",
            summary=f"{ticker} 跌出 BUY 前 {TOP_N} 名 (score {y_v.get('score')}→{t_v.get('score','?')})",
            yesterday={"score": y_v.get("score")},
            today={"score": t_v.get("score"), "bias": t_v.get("bias")},
        ))

    # 3. mos_first_positive
    for ticker, t_v in t_idx.items():
        y_v = y_idx.get(ticker)
        if not y_v:
            continue
        y_mos = y_v.get("margin_of_safety_pct")
        t_mos = t_v.get("margin_of_safety_pct")
        if t_mos is None or y_mos is None:
            continue
        # 從非正轉正才算
        if y_mos <= 0 and t_mos > 0:
            alerts.append(Alert(
                ticker=ticker,
                type="mos_first_positive",
                severity="high",
                summary=f"{ticker} DCF 安全邊際首次轉正 ({y_mos*100:+.1f}% → {t_mos*100:+.1f}%)",
                yesterday={"mos": y_mos},
                today={"mos": t_mos, "score": t_v.get("score"),
                       "recommendation": t_v.get("recommendation")},
            ))

    # 嚴重度排序: high > medium > low,同 severity 內保留原順序
    severity_rank = {"high": 0, "medium": 1, "low": 2}
    alerts.sort(key=lambda a: (severity_rank.get(a.severity, 3), a.ticker))
    return alerts


def news_alerts_from_verdicts(verdicts_dicts: list[dict]) -> list[Alert]:
    """從每筆 verdict 的 news 欄位產生 news 級別 alert (P1-3)。

    輸入是 daily_*.json 的 verdicts list (dict 形式)。
    觸發條件:news.alert_type 不為 null。
    """
    out: list[Alert] = []
    for v in verdicts_dicts:
        news = (v or {}).get("news")
        if not news:
            continue
        alert_type = news.get("alert_type")
        if not alert_type:
            continue
        ticker = v.get("ticker", "?")
        sentiment = news.get("sentiment_avg_7d")
        count = news.get("article_count_7d", 0)
        flash = news.get("flash_count_7d", 0)
        # 嚴重度: negative_spike / material_event = high; positive_spike = medium
        severity = "high" if alert_type != "news_positive_spike" else "medium"
        if alert_type == "news_negative_spike":
            label = f"負面新聞集中 (sentiment {sentiment:+.2f},{count} 篇)"
        elif alert_type == "news_positive_spike":
            label = f"正面新聞集中 (sentiment {sentiment:+.2f},{count} 篇)"
        elif alert_type == "material_event":
            label = f"{flash} 條重要快訊"
        else:
            label = alert_type
        out.append(Alert(
            ticker=ticker, type="news_alert", severity=severity,
            summary=f"{ticker}: {label}",
            today={"news": news},
        ))
    return out


def regression_alert_from_backtest(backtest_payload: dict | None) -> list[Alert]:
    """讀 output/backtest.json 的 rolling_summary,觸發時產 regression_detected alert。"""
    if not backtest_payload:
        return []
    summary = backtest_payload.get("rolling_summary") or {}
    if not summary.get("regression_alert"):
        return []
    weeks = summary.get("consecutive_underperforming_weeks", 0)
    avg_alpha = summary.get("avg_alpha_30d")
    avg_str = (
        f"30d 平均 alpha {avg_alpha*100:+.2f}%"
        if avg_alpha is not None
        else "30d alpha 連續為負"
    )
    return [Alert(
        ticker="(SYSTEM)",
        type="regression_detected",
        severity="high",
        summary=f"⚠️ 回測 regression:{weeks} 週連續 30d alpha 偏低,{avg_str},需檢視 rules.json",
        today={
            "consecutive_underperforming_weeks": weeks,
            "avg_alpha_30d": avg_alpha,
            "avg_alpha_90d": summary.get("avg_alpha_90d"),
            "note": summary.get("note"),
        },
    )]


def thesis_broken_alerts(thesis_statuses: list) -> list[Alert]:
    """從 thesis.process_verdicts() 結果產 thesis_broken alerts。

    thesis_statuses: list[ThesisStatus] (來自 agent/thesis.py)
    每個 broken 的 thesis 產一條 high severity alert。
    """
    out: list[Alert] = []
    for st in thesis_statuses:
        if st.state != "broken" or not st.thesis:
            continue
        broken_summary = "; ".join(st.broken_conditions[:2]) or "條件違反"
        out.append(Alert(
            ticker=st.ticker,
            type="thesis_broken",
            severity="high",
            summary=(
                f"{st.ticker}: 投資 thesis 條件被違反 — {broken_summary}"
            ),
            today={
                "thesis_age_days": st.thesis.thesis_age_days,
                "broken_conditions": st.broken_conditions,
                "first_buy_date": st.thesis.first_buy_date,
                "score_at_buy": st.thesis.score_at_buy,
            },
        ))
    return out


def write_alerts_json(alerts: list[Alert], path: Path) -> None:
    """寫 alerts.json。"""
    payload = {
        "total": len(alerts),
        "by_type": _count_by_type(alerts),
        "by_severity": _count_by_severity(alerts),
        "alerts": [a.to_dict() for a in alerts],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def _count_by_type(alerts: list[Alert]) -> dict[str, int]:
    out: dict[str, int] = {}
    for a in alerts:
        out[a.type] = out.get(a.type, 0) + 1
    return out


def _count_by_severity(alerts: list[Alert]) -> dict[str, int]:
    out: dict[str, int] = {}
    for a in alerts:
        out[a.severity] = out.get(a.severity, 0) + 1
    return out


def find_yesterday_scan(output_dir: Path, today_date: str) -> dict | None:
    """從 output/ 找昨日的 daily_*.json (按日期排序、跳過今天)。"""
    candidates = sorted(
        output_dir.glob("daily_*.json"), reverse=True,
    )
    for path in candidates:
        if path.stem == f"daily_{today_date}":
            continue
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            log.warning("skip bad daily file %s: %s", path, e)
            continue
    return None
