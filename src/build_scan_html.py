"""每週掃 watchlist 跑 Buffett agent,產出靜態 HTML 報告。

輸出:
- simple-html/scan.html              — 排行榜總覽
- simple-html/scan/<TICKER>.html     — 每檔完整報告

執行:
    python src/build_scan_html.py [--limit N]

CI:
    GitHub Actions 週一 22:00 UTC (US 收盤後) 自動跑。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

import markdown as md_lib

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import verdict as v_mod  # noqa: E402
from agent.sources import top_movers as _top_movers  # noqa: E402

OUT_DIR = ROOT / "simple-html"
SCAN_DIR = OUT_DIR / "scan"
JSON_OUT_DIR = ROOT / "output"
WATCHLIST_JSON = ROOT / "config" / "watchlist.json"
# stockTracker CSV — 不存在則 fallback 到 watchlist.json
def _default_stocktracker_csv() -> Path:
    candidates = [
        Path.home() / "autobot" / "stockTracker" / "data" / "latest_prices.csv",
        Path.home() / "Projects" / "stockTracker" / "data" / "latest_prices.csv",
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


STOCKTRACKER_CSV = Path(
    os.environ.get(
        "BUFFET_STOCKTRACKER_CSV",
        str(_default_stocktracker_csv()),
    )
)

WIKILINK_RE = re.compile(r"\[\[([^\[\]]+?)\]\]")

# 對映檔名 → 子目錄 (用來 resolve wikilink)
KB_DIRS = ["02-投資概念", "03-公司檔案", "04-人物檔案", "01-信件"]

# 巴菲特紅 + 奧馬哈白主題色,與 simple-html 保持一致
THEME_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;700&family=Noto+Serif+TC:wght@500;700&display=swap');
:root{--bg:#faf8f5;--fg:#2a2521;--muted:#6c6258;--accent:#8b3a2f;--accent-2:#b87333;--line:#e5e0d4;--highlight:rgba(184,115,51,0.15);--code-bg:#f0ece3;--buy:#2d7a4f;--hold:#7a652d;--watch:#7a4f2d;--avoid:#7a2d3a;--out:#555;}
@media (prefers-color-scheme: dark){:root{--bg:#1a1816;--fg:#ebe6db;--muted:#a09787;--accent:#d4815a;--accent-2:#e0a070;--line:#3a352e;--highlight:rgba(212,129,90,0.18);--code-bg:#2a2521;--buy:#52c285;--hold:#c2a052;--watch:#c27a52;--avoid:#c25268;--out:#888;}}
*{box-sizing:border-box;}
html,body{margin:0;padding:0;background:var(--bg);color:var(--fg);font-family:'Noto Sans TC',-apple-system,sans-serif;line-height:1.6;font-size:16px;}
.wrap{max-width:1100px;margin:0 auto;padding:24px 18px 60px;}
header{margin-bottom:24px;border-bottom:2px solid var(--accent);padding-bottom:12px;}
header h1{font-family:'Noto Serif TC',serif;font-size:28px;margin:0 0 6px;color:var(--accent);}
header .meta{font-size:13px;color:var(--muted);}
.bias-badge{display:inline-block;padding:2px 10px;border-radius:12px;font-weight:700;font-size:12px;color:#fff;}
.bias-BUY{background:var(--buy);}
.bias-HOLD{background:var(--hold);}
.bias-WATCH{background:var(--watch);}
.bias-AVOID{background:var(--avoid);}
.bias-OUT_OF_CIRCLE{background:var(--out);}
.bias-INSUFFICIENT_DATA{background:#7a8a9c;}
.summary-bar{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0 24px;}
.summary-bar span{background:var(--code-bg);padding:6px 12px;border-radius:6px;font-size:13px;}
table{border-collapse:collapse;width:100%;font-size:14px;}
th,td{border:1px solid var(--line);padding:10px;text-align:left;vertical-align:top;}
th{background:var(--code-bg);color:var(--accent);font-weight:700;font-family:'Noto Serif TC',serif;cursor:pointer;user-select:none;}
th:hover{background:var(--highlight);}
tr:hover td{background:var(--highlight);}
td.score{font-variant-numeric:tabular-nums;font-weight:700;}
td.ticker a{color:var(--accent);text-decoration:none;font-weight:700;}
td.ticker a:hover{text-decoration:underline;}
.brk-yes{color:var(--accent);font-size:18px;}
.brk-no{color:var(--muted);}
a.back{color:var(--muted);text-decoration:none;font-size:13px;}
a.back:hover{color:var(--accent);}
.wikilink{color:var(--accent);background:var(--highlight);padding:2px 4px;border-radius:3px;text-decoration:none;}
.wikilink:hover{background:var(--accent-2);color:var(--bg);}
.wikilink-broken{color:var(--muted);text-decoration:line-through;}
h1,h2,h3{font-family:'Noto Serif TC',serif;line-height:1.4;}
h2{color:var(--accent);font-size:22px;margin-top:1.6em;}
h3{color:var(--accent-2);font-size:18px;}
blockquote{border-left:4px solid var(--accent-2);background:var(--highlight);padding:10px 14px;margin:14px 0;border-radius:0 4px 4px 0;}
code{background:var(--code-bg);padding:2px 6px;border-radius:3px;font-size:0.92em;}
ul,ol{padding-left:24px;}
li{margin:4px 0;}
footer{margin-top:60px;padding-top:18px;border-top:1px solid var(--line);text-align:center;font-size:12px;color:var(--muted);}
@media (max-width:768px){.wrap{padding:16px 12px;}table{font-size:12px;}th,td{padding:6px 4px;}}
/* mobile drawer (re-use site convention) */
.nav-back{position:fixed;top:12px;left:12px;z-index:120;background:var(--accent);color:var(--bg);padding:6px 12px;border-radius:8px;text-decoration:none;font-size:13px;box-shadow:0 2px 8px rgba(0,0,0,0.25);}
"""

PWA_HEAD = """<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="manifest" href="{prefix}manifest.webmanifest">
<meta name="theme-color" content="#8b3a2f" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#1a1816" media="(prefers-color-scheme: dark)">
<link rel="icon" type="image/svg+xml" href="{prefix}icon.svg">
<link rel="apple-touch-icon" href="{prefix}icon.svg">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="巴菲特">"""

SUMMARY_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
{pwa}
<title>📊 巴菲特 Scan | 巴菲特股東信知識庫</title>
<style>{css}</style>
</head>
<body>
<a class="nav-back" href="index.html">← 知識庫</a>
<div class="wrap">
<header>
<h1>📊 巴菲特 Scan</h1>
<div class="meta">最近掃描: {timestamp_local} · 共 {total} 檔 · 資料源 yfinance + 13F · 規則 v{rules_version}</div>
</header>

<div class="summary-bar">
{summary_badges}
</div>

<table id="scan-table">
<thead><tr>
  <th>#</th>
  <th>Ticker</th>
  <th>Bias</th>
  <th>Score</th>
  <th>BRK</th>
  <th>通過</th>
  <th>主要備註</th>
</tr></thead>
<tbody>
{rows}
</tbody>
</table>

<footer>
🏗️ <a href="https://github.com/datadigshawn/buffet_agent" target="_blank">buffet_agent</a> · 規則來源 <a href="02-投資概念/巴菲特量化篩選清單.html">巴菲特量化篩選清單</a> · 自動每週一刷新
</footer>
</div>
</body>
</html>
"""

DETAIL_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
{pwa}
<title>{ticker} | 巴菲特 Scan</title>
<style>{css}</style>
</head>
<body>
<a class="nav-back" href="../scan.html">← 排行榜</a>
<div class="wrap">
{body_html}
<footer>
資料時點: {timestamp_local} · <a href="../scan.html">回 scan</a> · <a href="../index.html">知識庫首頁</a>
</footer>
</div>
</body>
</html>
"""

# ---------- Watchlist 載入 ----------

def load_watchlist(top_n_movers: int = 50, include_top_movers: bool = True) -> tuple[list[str], dict[str, list[str]]]:
    """組成當日掃描清單。

    回傳 (tickers, source_map):
      tickers: 去重後的順序清單 (watchlist 在前,Top movers 在後)
      source_map: ticker → ["watchlist"|"top_50_volume"|...] 用來追溯來源
    """
    source_map: dict[str, list[str]] = {}
    watchlist_tickers: list[str] = []

    if STOCKTRACKER_CSV.exists():
        import csv
        with STOCKTRACKER_CSV.open(encoding="utf-8") as f:
            r = csv.reader(f)
            next(r)
            for row in r:
                if row:
                    watchlist_tickers.append(row[0])

    if WATCHLIST_JSON.exists():
        cfg = json.loads(WATCHLIST_JSON.read_text(encoding="utf-8"))
        for grp in cfg.get("groups", {}).values():
            watchlist_tickers.extend(grp.get("tickers", []))

    # 去重保序
    seen, out = set(), []
    for t in watchlist_tickers:
        t = t.upper().strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
            source_map[t] = ["watchlist"]

    # 合併 Top N 成交量
    if include_top_movers:
        movers = _top_movers.fetch_top_movers(top_n_movers)
        for t in movers:
            t = t.upper().strip()
            if not t:
                continue
            if t in seen:
                source_map[t].append("top_50_volume")
            else:
                seen.add(t)
                out.append(t)
                source_map[t] = ["top_50_volume"]

    return out, source_map


# ---------- Wikilink 解析 ----------

def resolve_wikilink(target: str, anchor: str | None, prefix: str) -> str | None:
    """target → 相對路徑 (含 prefix); 找不到回 None。"""
    target = target.strip()
    for d in KB_DIRS:
        # search recursively
        cand = list((ROOT / "content" / d).rglob(f"{target}.md"))
        if cand:
            rel = cand[0].relative_to(ROOT / "content")
            href = f"{prefix}{rel.with_suffix('.html').as_posix()}"
            if anchor:
                href += f"#{anchor.strip()}"
            return href
    return None


def md_to_html(md_text: str, prefix: str = "../") -> str:
    """Markdown → HTML,含 [[wikilink]] 解析。prefix 預設 '../' (在 scan/ 子目錄)。"""
    def sub(m: re.Match) -> str:
        raw = m.group(1)
        target_part, _, display = raw.partition("|")
        display = display.strip() or target_part.strip()
        target, _, anchor = target_part.partition("#")
        href = resolve_wikilink(target, anchor or None, prefix)
        if href:
            return f'<a class="wikilink" href="{href}">{display}</a>'
        return f'<span class="wikilink-broken">{display}</span>'
    text = WIKILINK_RE.sub(sub, md_text)
    return md_lib.markdown(text, extensions=["tables", "fenced_code"], output_format="html5")


# ---------- 主流程 ----------

def render_summary_row(rank: int, v) -> str:
    s = v.score
    brk = '<span class="brk-yes">✓</span>' if s.data and s.data.berkshire_holds else '<span class="brk-no">—</span>'
    if v.bias == "OUT_OF_CIRCLE":
        passed_txt = "—"
        note = s.triggered_disqualifier or ""
    elif v.bias == "INSUFFICIENT_DATA":
        passed_txt = "—"
        note = f"涵蓋率 {s.coverage_pct}% &lt; 50%"
    else:
        passed = sum(1 for r in s.rule_results if r.passed)
        total = len(s.rule_results)
        passed_txt = f"{passed}/{total}"
        # 主要 fail 規則前 2 個
        fails = [r.rule_id for r in s.rule_results if not r.passed and not r.skipped][:2]
        note = "未過: " + ", ".join(fails) if fails else "全通過"
    return f"""<tr>
<td>{rank}</td>
<td class="ticker"><a href="scan/{v.ticker}.html">{v.ticker}</a></td>
<td><span class="bias-badge bias-{v.bias}">{v.bias}</span></td>
<td class="score">{s.total}</td>
<td>{brk}</td>
<td>{passed_txt}</td>
<td>{note}</td>
</tr>"""


def render_summary(verdicts: list, timestamp_local: str, rules_version: str) -> str:
    bias_count = Counter(v.bias for v in verdicts)
    badges = []
    for b in ["BUY", "HOLD", "WATCH", "AVOID", "OUT_OF_CIRCLE", "INSUFFICIENT_DATA"]:
        n = bias_count.get(b, 0)
        badges.append(f'<span><span class="bias-badge bias-{b}">{b}</span> {n}</span>')

    # 排序: 先 bias 優先級 (BUY > HOLD > WATCH > AVOID > OUT_OF_CIRCLE > INSUFFICIENT_DATA), 同 bias 內 score desc
    bias_order = {"BUY": 0, "HOLD": 1, "WATCH": 2, "AVOID": 3,
                  "OUT_OF_CIRCLE": 4, "INSUFFICIENT_DATA": 5}
    sorted_v = sorted(verdicts, key=lambda v: (bias_order[v.bias], -v.score.total))
    rows = "\n".join(render_summary_row(i + 1, v) for i, v in enumerate(sorted_v))

    return SUMMARY_HTML_TEMPLATE.format(
        pwa=PWA_HEAD.format(prefix=""),
        css=THEME_CSS,
        timestamp_local=timestamp_local,
        total=len(verdicts),
        rules_version=rules_version,
        summary_badges="\n".join(badges),
        rows=rows,
    )


def render_detail(v, timestamp_local: str) -> str:
    body_html = md_to_html(v.rationale_md, prefix="../")
    return DETAIL_HTML_TEMPLATE.format(
        pwa=PWA_HEAD.format(prefix="../"),
        css=THEME_CSS,
        ticker=v.ticker,
        body_html=body_html,
        timestamp_local=timestamp_local,
    )


def _verdict_to_json(v, sources: list[str]) -> dict:
    s = v.score
    intrinsic_psh = v.intrinsic.intrinsic_per_share if v.intrinsic else None
    mos = v.intrinsic.margin_of_safety if v.intrinsic else None
    return {
        "ticker": v.ticker,
        "bias": v.bias,
        "score": s.total,
        "base": s.base,
        "raw_base": s.raw_base,
        "bonus": s.bonus,
        "coverage_pct": s.coverage_pct,
        "confidence": v.confidence,
        "source_groups": sources,
        "passed_rules": [r.rule_id for r in s.rule_results if r.passed],
        "failed_rules": [r.rule_id for r in s.rule_results if not r.passed and not r.skipped],
        "skipped_rules": [r.rule_id for r in s.rule_results if r.skipped],
        "earned_bonuses": [b.rule_id for b in s.bonuses if b.earned],
        "triggered_disqualifier": s.triggered_disqualifier,
        "berkshire_holds": s.data.berkshire_holds if s.data else False,
        "berkshire_position_pct": s.data.berkshire_position_pct if s.data else None,
        "current_price": s.data.price if s.data else None,
        "sector": s.data.sector if s.data else None,
        "industry_class": s.data.industry_class if s.data else None,
        "data_source": s.data.source if s.data else None,
        # DCF (C1)
        "intrinsic_value_per_share": (
            round(intrinsic_psh, 2) if intrinsic_psh is not None else None
        ),
        "margin_of_safety_pct": (
            round(mos, 4) if mos is not None else None
        ),
        # LLM 定性 (C3) — 含 management_grade / moat_* / in_circle / recommendation
        "qualitative": v.qualitative.to_dict() if v.qualitative else None,
        # Top-level convenience field — 戰情室 lobby 卡用
        "recommendation": (
            v.qualitative.recommendation
            if v.qualitative and v.qualitative.recommendation
            else None
        ),
        # Phase D 才會填
        "alerts": [],
    }


def write_json_output(verdicts, source_map: dict[str, list[str]],
                      rules_version: str, scan_date: str, scan_time_utc: str) -> Path:
    """寫 output/daily_YYYY-MM-DD.json + latest.json (給戰情室拉)。"""
    from collections import Counter
    bias_count = Counter(v.bias for v in verdicts)
    payload = {
        "scan_date": scan_date,
        "scan_time_utc": scan_time_utc,
        "rules_version": rules_version,
        "total_scanned": len(verdicts),
        "summary": {
            "BUY": bias_count.get("BUY", 0),
            "HOLD": bias_count.get("HOLD", 0),
            "WATCH": bias_count.get("WATCH", 0),
            "AVOID": bias_count.get("AVOID", 0),
            "OUT_OF_CIRCLE": bias_count.get("OUT_OF_CIRCLE", 0),
            "INSUFFICIENT_DATA": bias_count.get("INSUFFICIENT_DATA", 0),
        },
        "verdicts": [
            _verdict_to_json(v, source_map.get(v.ticker, []))
            for v in sorted(verdicts, key=lambda x: -x.score.total)
        ],
    }
    JSON_OUT_DIR.mkdir(parents=True, exist_ok=True)
    daily_path = JSON_OUT_DIR / f"daily_{scan_date}.json"
    latest_path = JSON_OUT_DIR / "latest.json"
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    daily_path.write_text(text, encoding="utf-8")
    latest_path.write_text(text, encoding="utf-8")
    return daily_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 檔(除錯用)")
    parser.add_argument("--quiet", action="store_true", help="少印 log")
    parser.add_argument("--top-movers", type=int, default=50,
                        help="當日成交量 Top N 加入掃描 (0 = 不加)")
    args = parser.parse_args()

    tickers, source_map = load_watchlist(
        top_n_movers=args.top_movers,
        include_top_movers=args.top_movers > 0,
    )
    if args.limit:
        tickers = tickers[: args.limit]
        source_map = {t: source_map[t] for t in tickers if t in source_map}

    if not args.quiet:
        n_watch = sum(1 for s in source_map.values() if "watchlist" in s)
        n_movers = sum(1 for s in source_map.values() if "top_50_volume" in s)
        print(f"📊 掃 {len(tickers)} 檔 (watchlist={n_watch}, top_movers={n_movers})...",
              flush=True)

    verdicts = []
    for i, t in enumerate(tickers, 1):
        try:
            v = v_mod.evaluate(t)
            verdicts.append(v)
            if not args.quiet:
                print(f"  [{i}/{len(tickers)}] {t:6} {v.bias:18} {v.score.total}", flush=True)
        except Exception as e:
            print(f"  [{i}/{len(tickers)}] {t:6} ERROR: {e}", flush=True)

    # 取規則版本
    rules_data = json.loads((ROOT / "agent" / "rules.json").read_text(encoding="utf-8"))
    rules_version = rules_data.get("version", "?")

    # 時戳
    now_utc = datetime.now(timezone.utc)
    scan_date = now_utc.strftime("%Y-%m-%d")
    scan_time_utc = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    tz = timezone(timedelta(hours=8))
    timestamp_local = datetime.now(tz).strftime("%Y-%m-%d %H:%M %Z")

    # 寫檔
    SCAN_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "scan.html").write_text(
        render_summary(verdicts, timestamp_local, rules_version), encoding="utf-8"
    )
    for v in verdicts:
        (SCAN_DIR / f"{v.ticker}.html").write_text(
            render_detail(v, timestamp_local), encoding="utf-8"
        )

    json_path = write_json_output(verdicts, source_map, rules_version, scan_date, scan_time_utc)

    print(f"✅ 完成 {len(verdicts)} 份報告 → simple-html/scan.html + simple-html/scan/")
    print(f"📦 JSON contract → {json_path.relative_to(ROOT)} + output/latest.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
