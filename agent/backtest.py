"""回測歷史準確率 (Phase 5 P2-2)。

目的:把「buffet agent 的判斷準不準」變成可量化問題,而非哲學辯論。
對映 [[失誤與認錯]] — Buffett 自己最重視的紀律,agent 也該有這層。

機制:
1. 讀 output/daily_*.json 所有歷史掃描
2. 對每個掃描日期,抽出當天的 BUY 籃子 (前 N 檔)
3. 用 yfinance 抓 horizon 後 (30/90/180 天) 的 close price
4. 計算籃子平均報酬 vs SPX (^GSPC) 的相對 alpha
5. 滾動聚合:近 N 週 BUY 籃子的 1m alpha 平均
6. Regression detection:連 3 週 1m alpha < -5% → 觸發 regression alert

輸出 output/backtest.json,Netlify backtest.html 視覺化。

設計原則:
- 不依賴歷史掃描數量(可從少量開始,逐週累積)
- 缺資料的 horizon 標 ready=false (例 7 天前的掃描還沒滿 30 天)
- yfinance 抓不到時 graceful skip
- 純 stdlib (json, statistics, urllib via yfinance)
"""
from __future__ import annotations

import json
import logging
import os
import statistics
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "output"
DEFAULT_HORIZONS = (30, 90, 180)
DEFAULT_TOP_N = 10           # BUY 籃子取分數最高的前 N 檔
BENCHMARK = "^GSPC"          # SPX

# Regression detection thresholds
REGRESSION_LOOKBACK_WEEKS = 3
REGRESSION_ALPHA_THRESHOLD = -0.05    # -5% 連 3 週


@dataclass
class HorizonResult:
    horizon_days: int
    ready: bool                   # 是否已過 horizon (有資料可比)
    days_elapsed: int
    basket_return: float | None = None       # 籃子平均 return
    benchmark_return: float | None = None    # SPX return
    alpha: float | None = None               # basket - benchmark
    hit_rate: float | None = None            # basket 中正報酬比例
    constituents: list[dict] = field(default_factory=list)   # ticker → return

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScanBacktest:
    scan_date: str
    buy_tickers: list[str]
    horizons: dict[int, HorizonResult] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scan_date": self.scan_date,
            "buy_count": len(self.buy_tickers),
            "buy_tickers": self.buy_tickers,
            "horizons": {str(k): v.to_dict() for k, v in self.horizons.items()},
        }


@dataclass
class RollingSummary:
    weeks_with_30d_data: int = 0
    avg_alpha_30d: float | None = None
    avg_alpha_90d: float | None = None
    avg_alpha_180d: float | None = None
    avg_hit_rate_30d: float | None = None
    consecutive_underperforming_weeks: int = 0
    regression_alert: bool = False
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------- 歷史掃描載入 ----------

def load_historical_scans(output_dir: Path = OUTPUT_DIR,
                          max_lookback_days: int = 365) -> list[dict]:
    """讀 output/daily_*.json,按日期升序。"""
    if not output_dir.exists():
        return []
    scans: list[dict] = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_lookback_days)).date()
    for p in sorted(output_dir.glob("daily_*.json")):
        date_str = p.stem.replace("daily_", "")
        try:
            scan_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if scan_date < cutoff:
            continue
        try:
            scans.append(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as e:
            log.warning("skip bad scan %s: %s", p, e)
            continue
    return scans


def buy_basket(scan_payload: dict, top_n: int = DEFAULT_TOP_N) -> list[str]:
    """從 scan payload 抽出 BUY 籃子 (按 score 降序前 N)。"""
    verdicts = scan_payload.get("verdicts", [])
    buys = [v for v in verdicts if v.get("bias") == "BUY"]
    buys.sort(key=lambda v: -v.get("score", 0))
    return [v["ticker"] for v in buys[:top_n]]


# ---------- 歷史價格 ----------

def fetch_close_at_or_after(ticker: str, target_date: datetime) -> float | None:
    """抓 target_date 當天或之後最近交易日的 close。

    target_date 為 timezone-aware UTC datetime;
    yfinance 用 dayfirst auto-parse。
    """
    try:
        import yfinance as yf
    except ImportError:
        return None

    start = target_date.strftime("%Y-%m-%d")
    end = (target_date + timedelta(days=10)).strftime("%Y-%m-%d")
    try:
        hist = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=False)
    except Exception as e:  # noqa: BLE001
        log.debug("yfinance history %s [%s] failed: %s", ticker, start, e)
        return None
    if hist is None or hist.empty:
        return None
    try:
        return float(hist["Close"].iloc[0])
    except (KeyError, IndexError, ValueError):
        return None


def compute_return(ticker: str, scan_date: str, horizon_days: int) -> float | None:
    """ticker 在 scan_date 到 scan_date+horizon 的 return (decimal)。"""
    try:
        d = datetime.strptime(scan_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    horizon_d = d + timedelta(days=horizon_days)
    if horizon_d > datetime.now(timezone.utc):
        return None    # 還沒到 horizon

    p_start = fetch_close_at_or_after(ticker, d)
    p_end = fetch_close_at_or_after(ticker, horizon_d)
    if p_start is None or p_end is None or p_start <= 0:
        return None
    return (p_end - p_start) / p_start


# ---------- 單次 scan 回測 ----------

def backtest_scan(scan_payload: dict,
                  horizons: tuple[int, ...] = DEFAULT_HORIZONS,
                  top_n: int = DEFAULT_TOP_N,
                  benchmark: str = BENCHMARK) -> ScanBacktest:
    """對單一歷史 scan 計算各 horizon 的籃子 return + alpha。"""
    scan_date = scan_payload.get("scan_date", "")
    tickers = buy_basket(scan_payload, top_n=top_n)
    sb = ScanBacktest(scan_date=scan_date, buy_tickers=tickers)

    if not scan_date:
        return sb

    try:
        d = datetime.strptime(scan_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return sb

    now = datetime.now(timezone.utc)
    for h in horizons:
        elapsed = (now - d).days
        if elapsed < h:
            sb.horizons[h] = HorizonResult(
                horizon_days=h, ready=False, days_elapsed=max(0, elapsed),
            )
            continue

        constituents: list[dict] = []
        ticker_returns: list[float] = []
        for t in tickers:
            r = compute_return(t, scan_date, h)
            if r is not None:
                constituents.append({"ticker": t, "return": round(r, 4)})
                ticker_returns.append(r)
            else:
                constituents.append({"ticker": t, "return": None})

        bench_r = compute_return(benchmark, scan_date, h)
        basket_r = (
            statistics.mean(ticker_returns) if ticker_returns else None
        )
        alpha = (
            (basket_r - bench_r)
            if basket_r is not None and bench_r is not None
            else None
        )
        hit_rate = (
            sum(1 for x in ticker_returns if x > 0) / len(ticker_returns)
            if ticker_returns
            else None
        )

        sb.horizons[h] = HorizonResult(
            horizon_days=h, ready=True, days_elapsed=elapsed,
            basket_return=round(basket_r, 4) if basket_r is not None else None,
            benchmark_return=round(bench_r, 4) if bench_r is not None else None,
            alpha=round(alpha, 4) if alpha is not None else None,
            hit_rate=round(hit_rate, 4) if hit_rate is not None else None,
            constituents=constituents,
        )
    return sb


# ---------- 滾動聚合 + regression detection ----------

def aggregate(scan_results: list[ScanBacktest]) -> RollingSummary:
    """彙整所有歷史 scan 結果,計算 rolling 指標 + regression alert。"""
    summary = RollingSummary()
    if not scan_results:
        summary.note = "無歷史 scan 資料"
        return summary

    alpha_30 = [s.horizons[30].alpha for s in scan_results
                if 30 in s.horizons and s.horizons[30].alpha is not None]
    alpha_90 = [s.horizons[90].alpha for s in scan_results
                if 90 in s.horizons and s.horizons[90].alpha is not None]
    alpha_180 = [s.horizons[180].alpha for s in scan_results
                 if 180 in s.horizons and s.horizons[180].alpha is not None]
    hit_30 = [s.horizons[30].hit_rate for s in scan_results
              if 30 in s.horizons and s.horizons[30].hit_rate is not None]

    summary.weeks_with_30d_data = len(alpha_30)
    if alpha_30:
        summary.avg_alpha_30d = round(statistics.mean(alpha_30), 4)
    if alpha_90:
        summary.avg_alpha_90d = round(statistics.mean(alpha_90), 4)
    if alpha_180:
        summary.avg_alpha_180d = round(statistics.mean(alpha_180), 4)
    if hit_30:
        summary.avg_hit_rate_30d = round(statistics.mean(hit_30), 4)

    # Regression detection: 取最近 N 週 1m alpha
    sorted_by_date = sorted(
        scan_results, key=lambda s: s.scan_date, reverse=True,
    )
    recent_alpha_30 = []
    for s in sorted_by_date:
        if 30 in s.horizons and s.horizons[30].alpha is not None:
            recent_alpha_30.append(s.horizons[30].alpha)
        if len(recent_alpha_30) >= REGRESSION_LOOKBACK_WEEKS:
            break
    if len(recent_alpha_30) >= REGRESSION_LOOKBACK_WEEKS:
        consec = 0
        for a in recent_alpha_30:
            if a < REGRESSION_ALPHA_THRESHOLD:
                consec += 1
            else:
                break
        summary.consecutive_underperforming_weeks = consec
        if consec >= REGRESSION_LOOKBACK_WEEKS:
            summary.regression_alert = True
            summary.note = (
                f"⚠️ Regression alert:近 {consec} 週 30 天 alpha 連續 < "
                f"{REGRESSION_ALPHA_THRESHOLD*100:.0f}%,需要檢視 rules.json"
            )

    return summary


# ---------- 主入口 ----------

def run(top_n: int = DEFAULT_TOP_N,
        horizons: tuple[int, ...] = DEFAULT_HORIZONS,
        max_lookback_days: int = 365) -> dict[str, Any]:
    """主入口:讀歷史 scans → 回測 → 聚合 → 回 dict。"""
    scans = load_historical_scans(max_lookback_days=max_lookback_days)
    if not scans:
        return {
            "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "horizons": list(horizons),
            "scan_count": 0,
            "by_scan_date": {},
            "rolling_summary": RollingSummary(note="無歷史掃描").to_dict(),
        }

    results: list[ScanBacktest] = []
    for scan in scans:
        try:
            results.append(backtest_scan(scan, horizons=horizons, top_n=top_n))
        except Exception as e:  # noqa: BLE001
            log.warning("backtest scan %s failed: %s", scan.get("scan_date"), e)

    summary = aggregate(results)
    return {
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "horizons": list(horizons),
        "scan_count": len(results),
        "top_n": top_n,
        "benchmark": BENCHMARK,
        "by_scan_date": {r.scan_date: r.to_dict() for r in results},
        "rolling_summary": summary.to_dict(),
    }


def write_backtest_json(payload: dict[str, Any],
                        out_path: Path = OUTPUT_DIR / "backtest.json") -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
