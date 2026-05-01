#!/usr/bin/env python
"""跑回測 (Phase 5 P2-2) — CLI runner。

用法:
    python scripts/run_backtest.py                     # 預設 top 10、30/90/180 horizon
    python scripts/run_backtest.py --top-n 20          # 改 top 20
    python scripts/run_backtest.py --horizons 7,30,90  # 自訂 horizon
    python scripts/run_backtest.py --quiet             # 少印 log

輸出:
    output/backtest.json       — 完整回測結果
    output/backtest_summary.md — 人類可讀摘要(也會印到 stdout)

Cron 跑法:每週日 23:00 UTC (避開 daily scan 22:30 UTC),由
.github/workflows/backtest.yml 排程。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import backtest  # noqa: E402

OUT_DIR = ROOT / "output"
HTML_OUT = ROOT / "simple-html" / "backtest.html"


def format_summary(payload: dict) -> str:
    lines = []
    lines.append(f"# Buffett Agent 回測報告 ({payload['as_of']})")
    lines.append("")
    lines.append(f"- 歷史掃描數: {payload['scan_count']}")
    lines.append(f"- BUY 籃子: top {payload['top_n']} 檔 vs {payload['benchmark']}")
    lines.append(f"- Horizons: {', '.join(str(h) + 'd' for h in payload['horizons'])}")
    lines.append("")

    s = payload["rolling_summary"]
    lines.append("## 滾動指標")
    lines.append(f"- 已有 30d 資料的 scan 數: **{s['weeks_with_30d_data']}**")
    if s.get("avg_alpha_30d") is not None:
        lines.append(f"- 平均 30d alpha: **{s['avg_alpha_30d']*100:+.2f}%**")
    if s.get("avg_alpha_90d") is not None:
        lines.append(f"- 平均 90d alpha: **{s['avg_alpha_90d']*100:+.2f}%**")
    if s.get("avg_alpha_180d") is not None:
        lines.append(f"- 平均 180d alpha: **{s['avg_alpha_180d']*100:+.2f}%**")
    if s.get("avg_hit_rate_30d") is not None:
        lines.append(f"- 30d 平均命中率 (>0): **{s['avg_hit_rate_30d']*100:.0f}%**")
    if s.get("regression_alert"):
        lines.append("")
        lines.append(f"⚠️ **{s.get('note', 'Regression alert triggered')}**")
    elif s.get("note"):
        lines.append(f"- 備註: {s['note']}")
    lines.append("")

    # 各 scan 詳情(只顯示最近 5 個 + 最舊 1 個)
    by_date = payload.get("by_scan_date", {})
    if by_date:
        sorted_dates = sorted(by_date.keys(), reverse=True)
        show = sorted_dates[:5]
        if len(sorted_dates) > 5:
            show.append(sorted_dates[-1])

        lines.append("## 近期 scan 表現")
        lines.append("")
        lines.append("| Scan Date | BUY 數 | 30d Alpha | 30d 命中率 | 90d Alpha | 180d Alpha |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for d in show:
            r = by_date[d]
            h30 = r["horizons"].get("30", {})
            h90 = r["horizons"].get("90", {})
            h180 = r["horizons"].get("180", {})
            def fmt_alpha(h: dict) -> str:
                if not h.get("ready"):
                    return f"⏳ {h.get('days_elapsed', 0)}/{h.get('horizon_days', '?')}"
                a = h.get("alpha")
                return f"{a*100:+.2f}%" if a is not None else "—"
            def fmt_hit(h: dict) -> str:
                if not h.get("ready"):
                    return "—"
                hr = h.get("hit_rate")
                return f"{hr*100:.0f}%" if hr is not None else "—"
            lines.append(
                f"| {d} | {r['buy_count']} | {fmt_alpha(h30)} | "
                f"{fmt_hit(h30)} | {fmt_alpha(h90)} | {fmt_alpha(h180)} |"
            )

    return "\n".join(lines)


def render_html(payload: dict) -> str:
    """Netlify 公開頁:simple-html/backtest.html。"""
    s = payload["rolling_summary"]
    by_date = payload.get("by_scan_date", {})
    sorted_dates = sorted(by_date.keys(), reverse=True)

    def cell_alpha(h: dict) -> str:
        if not h.get("ready"):
            return f'<span class="pending">⏳ {h.get("days_elapsed",0)}/{h.get("horizon_days","?")}</span>'
        a = h.get("alpha")
        if a is None:
            return "—"
        cls = "pos" if a > 0 else "neg"
        return f'<span class="{cls}">{a*100:+.2f}%</span>'

    def cell_hit(h: dict) -> str:
        hr = h.get("hit_rate")
        if hr is None:
            return "—"
        return f"{hr*100:.0f}%"

    rows_html = []
    for d in sorted_dates:
        r = by_date[d]
        h30 = r["horizons"].get("30", {})
        h90 = r["horizons"].get("90", {})
        h180 = r["horizons"].get("180", {})
        rows_html.append(
            f'<tr><td>{d}</td><td>{r["buy_count"]}</td>'
            f'<td>{cell_alpha(h30)}</td><td>{cell_hit(h30)}</td>'
            f'<td>{cell_alpha(h90)}</td><td>{cell_alpha(h180)}</td></tr>'
        )

    regression_banner = ""
    if s.get("regression_alert"):
        regression_banner = (
            f'<div class="alert">⚠️ <strong>{s.get("note","Regression alert")}</strong></div>'
        )

    avg30 = s.get("avg_alpha_30d")
    avg90 = s.get("avg_alpha_90d")
    avg180 = s.get("avg_alpha_180d")
    hit30 = s.get("avg_hit_rate_30d")

    return f"""<!DOCTYPE html>
<html lang="zh-TW"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>📊 Buffett Agent 回測</title>
<style>
:root{{--bg:#faf8f5;--fg:#2a2521;--accent:#8b3a2f;--line:#e5e0d4;--pos:#2d7a4f;--neg:#7a2d3a;--pend:#7a652d;}}
@media (prefers-color-scheme: dark){{:root{{--bg:#1a1816;--fg:#ebe6db;--accent:#d4815a;--line:#3a352e;--pos:#52c285;--neg:#c25268;--pend:#c2a052;}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font-family:'Noto Sans TC',-apple-system,sans-serif;line-height:1.6;}}
.wrap{{max-width:1100px;margin:0 auto;padding:24px 18px 60px;}}
header{{margin-bottom:24px;border-bottom:2px solid var(--accent);padding-bottom:12px;}}
header h1{{color:var(--accent);font-size:28px;margin:0;}}
.meta{{color:#888;font-size:13px;margin-top:6px;}}
.alert{{background:rgba(194,82,104,0.15);border-left:4px solid var(--neg);padding:12px 16px;margin:18px 0;border-radius:0 4px 4px 0;}}
.kpi-row{{display:flex;flex-wrap:wrap;gap:14px;margin:20px 0;}}
.kpi{{flex:1;min-width:180px;background:rgba(139,58,47,0.06);padding:14px;border-radius:8px;}}
.kpi .label{{font-size:12px;color:#888;text-transform:uppercase;}}
.kpi .val{{font-size:24px;font-weight:700;margin-top:4px;}}
.kpi .pos{{color:var(--pos);}} .kpi .neg{{color:var(--neg);}}
table{{border-collapse:collapse;width:100%;font-size:14px;margin-top:14px;}}
th,td{{border:1px solid var(--line);padding:10px;text-align:left;}}
th{{background:rgba(139,58,47,0.06);color:var(--accent);font-weight:700;}}
.pos{{color:var(--pos);font-weight:600;}}
.neg{{color:var(--neg);font-weight:600;}}
.pending{{color:var(--pend);font-size:12px;}}
footer{{margin-top:40px;padding-top:16px;border-top:1px solid var(--line);text-align:center;font-size:12px;color:#888;}}
a{{color:var(--accent);}}
.site-nav{{position:fixed;top:12px;right:12px;z-index:130;display:flex;gap:6px;
  background:rgba(250,248,245,0.92);border:1px solid var(--line);border-radius:8px;
  padding:4px;box-shadow:0 2px 8px rgba(0,0,0,0.15);backdrop-filter:blur(6px);}}
@media (prefers-color-scheme:dark){{.site-nav{{background:rgba(26,24,22,0.92);}}}}
.site-nav a{{font-size:18px;padding:4px 9px;text-decoration:none;border-radius:5px;
  color:var(--fg);transition:all .15s;line-height:1.2;}}
.site-nav a:hover{{background:var(--accent);color:var(--bg);}}
.site-nav a[aria-current="page"]{{background:var(--accent);color:var(--bg);}}
@media (max-width:768px){{.site-nav{{top:8px;right:8px;padding:3px;gap:3px;}}
  .site-nav a{{font-size:16px;padding:3px 6px;}}}}
</style></head><body>
<nav class="site-nav" aria-label="網站導覽">
<a href="https://buffetagent.netlify.app/scan.html" title="📊 Scan 排行榜">📊</a>
<a href="https://buffetagent.netlify.app/backtest.html" aria-current="page" title="📈 回測報告">📈</a>
<a href="https://buffetagent.netlify.app/index.html" title="📚 巴菲特知識庫">📚</a>
<a href="https://war-room.shawny-project42.com/chat" target="_blank" rel="noopener"
  title="💬 戰情室 (新分頁開啟)">💬</a>
</nav>
<div class="wrap">
<header>
  <h1>📊 Buffett Agent 回測</h1>
  <div class="meta">As of {payload['as_of']} · 歷史掃描 {payload['scan_count']} 次 · top {payload['top_n']} BUY 籃子 vs {payload['benchmark']}</div>
</header>
{regression_banner}
<div class="kpi-row">
  <div class="kpi"><div class="label">30d 平均 Alpha</div><div class="val {'pos' if avg30 and avg30>0 else 'neg' if avg30 else ''}">{f'{avg30*100:+.2f}%' if avg30 is not None else '—'}</div></div>
  <div class="kpi"><div class="label">90d 平均 Alpha</div><div class="val {'pos' if avg90 and avg90>0 else 'neg' if avg90 else ''}">{f'{avg90*100:+.2f}%' if avg90 is not None else '—'}</div></div>
  <div class="kpi"><div class="label">180d 平均 Alpha</div><div class="val {'pos' if avg180 and avg180>0 else 'neg' if avg180 else ''}">{f'{avg180*100:+.2f}%' if avg180 is not None else '—'}</div></div>
  <div class="kpi"><div class="label">30d 命中率</div><div class="val">{f'{hit30*100:.0f}%' if hit30 is not None else '—'}</div></div>
</div>

<h2>各次掃描表現</h2>
<table>
<thead><tr><th>掃描日</th><th>BUY 數</th><th>30d α</th><th>30d 命中</th><th>90d α</th><th>180d α</th></tr></thead>
<tbody>
{''.join(rows_html) or '<tr><td colspan="6">尚無資料</td></tr>'}
</tbody>
</table>

<footer>
🏗️ <a href="https://github.com/datadigshawn/buffet_agent">buffet_agent</a> · 每週日 23:00 UTC 自動更新 ·
<a href="scan.html">回 Scan</a> · <a href="index.html">知識庫</a>
</footer>
</div></body></html>
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-n", type=int, default=backtest.DEFAULT_TOP_N)
    parser.add_argument("--horizons", default="30,90,180",
                        help="逗號分隔,例 7,30,90,180")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    horizons = tuple(int(h) for h in args.horizons.split(",") if h.strip())

    if not args.quiet:
        print(f"📊 跑回測:top {args.top_n} BUY 籃子,horizons={horizons}",
              flush=True)

    payload = backtest.run(top_n=args.top_n, horizons=horizons)
    backtest.write_backtest_json(payload)

    summary_md = format_summary(payload)
    (OUT_DIR / "backtest_summary.md").write_text(summary_md, encoding="utf-8")

    HTML_OUT.parent.mkdir(parents=True, exist_ok=True)
    HTML_OUT.write_text(render_html(payload), encoding="utf-8")

    if not args.quiet:
        print(summary_md)
        print()
        print(f"✅ 寫入 output/backtest.json + output/backtest_summary.md")
        print(f"🌐 簡易視覺化 → simple-html/backtest.html")

    return 0


if __name__ == "__main__":
    sys.exit(main())
