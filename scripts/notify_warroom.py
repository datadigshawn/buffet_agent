"""讀 output/latest.json 寫摘要到 war-room lobby（role='buffett_scan'）。

跑法:
- 本機 (Mac mini) launchd cron 每天美東收盤後 30–60 分跑
- 也可手動觸發: python scripts/notify_warroom.py

模式參考 stockAnalysis_bot_MultiAgent/src/jobs/notify_morning_report.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LATEST_JSON = REPO_ROOT / "output" / "latest.json"
ALERTS_JSON = REPO_ROOT / "output" / "alerts.json"
WARROOM_DB = Path(
    os.environ.get(
        "WARROOM_DB",
        "/Users/shawnclaw/autobot/war-room/data/war-room.db",
    )
)
LOG_FILE = REPO_ROOT / "output" / "notify_warroom.log"

LOBBY_ROLE = "buffett_scan"
ALERT_ROLE = "buffett_alert"   # 高訊號變動單獨推一張紅卡


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_scan() -> dict | None:
    if not LATEST_JSON.exists():
        _log(f"[err] {LATEST_JSON} not found — buffetAgent cron 可能尚未跑完")
        return None
    try:
        return json.loads(LATEST_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        _log(f"[err] read latest.json failed: {e}")
        return None


def format_lobby_message(scan: dict) -> str:
    """格式參考 stock_analysis 的 morning report — 緊湊但資訊密度高。"""
    summary = scan.get("summary", {})
    verdicts = scan.get("verdicts", [])
    date = scan.get("scan_date", "?")
    total = scan.get("total_scanned", len(verdicts))

    # 過濾 BUY / HOLD,score 由高到低
    buys = [v for v in verdicts if v.get("bias") == "BUY"][:5]
    holds = [v for v in verdicts if v.get("bias") == "HOLD"][:5]

    # bias 分布
    counts_order = ["BUY", "HOLD", "WATCH", "AVOID", "OUT_OF_CIRCLE", "INSUFFICIENT_DATA"]
    counts_line = " · ".join(
        f"{k} {summary.get(k, 0)}" for k in counts_order if summary.get(k, 0) > 0
    )

    parts = [f"📚 [Buffett] {date} 掃描（{total} 檔）"]
    parts.append(f"分布: {counts_line}")

    if buys:
        buy_line = " · ".join(f"{v['ticker']} {v['score']}" for v in buys)
        parts.append(f"🎯 BUY: {buy_line}")

    if holds:
        hold_line = " · ".join(f"{v['ticker']} {v['score']}" for v in holds)
        parts.append(f"📌 HOLD: {hold_line}")

    # Berkshire 持有清單(最多 3 個)
    brk = [v for v in verdicts if v.get("berkshire_holds")][:3]
    if brk:
        brk_line = " · ".join(f"{v['ticker']} {v['score']}" for v in brk)
        parts.append(f"🏛️ BRK: {brk_line}")

    # LLM 行動建議(取分數最高且有 recommendation 的 1-2 檔)
    with_rec = [v for v in verdicts if v.get("recommendation")][:2]
    if with_rec:
        parts.append("")
        for v in with_rec:
            parts.append(f"💡 {v['ticker']}: {v['recommendation']}")

    # P0-2: thesis 統計 (僅顯示 new / broken,valid 不噪音)
    new_theses = [v["ticker"] for v in verdicts if v.get("thesis_state") == "new"]
    broken_theses = [v for v in verdicts if v.get("thesis_state") == "broken"]
    if new_theses:
        parts.append(f"📜 新 thesis: {', '.join(new_theses[:5])}"
                     + (f" 等 {len(new_theses)} 筆" if len(new_theses) > 5 else ""))
    if broken_theses:
        parts.append(f"💔 thesis broken: {', '.join(v['ticker'] for v in broken_theses[:5])}")

    parts.append("→ scan: https://buffetagent.netlify.app/scan.html")
    return "\n".join(parts)


def write_warroom_lobby(content: str, role: str = LOBBY_ROLE) -> bool:
    if not WARROOM_DB.exists():
        _log(f"[err] war-room.db not found: {WARROOM_DB}")
        return False
    try:
        with sqlite3.connect(str(WARROOM_DB), timeout=5) as conn:
            conn.execute(
                "INSERT INTO lobby(role, content, created_at) VALUES(?, ?, ?)",
                (role, content, datetime.now(timezone.utc).isoformat()),
            )
        return True
    except Exception as e:
        _log(f"[lobby] write failed: {e}")
        return False


def load_alerts() -> dict | None:
    if not ALERTS_JSON.exists():
        return None
    try:
        return json.loads(ALERTS_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        _log(f"[err] read alerts.json failed: {e}")
        return None


def format_alert_message(alerts_payload: dict) -> str | None:
    """只在有 high/medium 變動時推卡片;low only 不推(避免噪音)。"""
    alerts = alerts_payload.get("alerts", [])
    significant = [a for a in alerts if a.get("severity") in ("high", "medium")]
    if not significant:
        return None

    by_sev = alerts_payload.get("by_severity", {})
    parts = [f"⚡ [Buffett Alert] 今日變動 {len(significant)} 筆 "
             f"(high {by_sev.get('high', 0)} / medium {by_sev.get('medium', 0)})"]

    # 把 high 的全列、medium 的 top 3 列
    high_alerts = [a for a in significant if a["severity"] == "high"]
    medium_alerts = [a for a in significant if a["severity"] == "medium"]

    if high_alerts:
        parts.append("")
        for a in high_alerts:
            parts.append(f"🔥 {a['summary']}")
            today = a.get("today", {})
            if today.get("recommendation"):
                parts.append(f"   💡 {today['recommendation'][:100]}")

    if medium_alerts:
        parts.append("")
        for a in medium_alerts[:3]:
            parts.append(f"📊 {a['summary']}")

    if len(medium_alerts) > 3:
        parts.append(f"   …(另 {len(medium_alerts) - 3} 筆 medium 變動見 scan.html)")

    return "\n".join(parts)


def main() -> int:
    scan = load_scan()
    if scan is None:
        return 1
    msg = format_lobby_message(scan)
    ok = write_warroom_lobby(msg, role=LOBBY_ROLE)
    head = scan.get("verdicts", [{}])[0].get("ticker", "?")
    _log(f"[{'ok' if ok else 'fail'}] lobby posted — top={head} total={scan.get('total_scanned')}")

    # Phase 5 P0-1:有顯著變動就推 alert 副卡
    alerts_payload = load_alerts()
    if alerts_payload:
        alert_msg = format_alert_message(alerts_payload)
        if alert_msg:
            alert_ok = write_warroom_lobby(alert_msg, role=ALERT_ROLE)
            _log(f"[{'ok' if alert_ok else 'fail'}] alert posted — "
                 f"{alerts_payload.get('total', 0)} total, "
                 f"{alerts_payload.get('by_severity', {}).get('high', 0)} high")
        else:
            _log(f"[skip] no significant alerts ({alerts_payload.get('total', 0)} total, all low/none)")

    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
