"""抓美股當日成交量 Top N。

策略 (按優先序):
1. yfinance 的 most_actives screener (官方,免 API key)
2. Finviz HTML 解析 (退路,網站結構偶爾變動需注意)
3. 失敗時回空 list,呼叫端決定 fallback

當日結果走 cache (namespace="top_movers"),避免重複打。
"""
from __future__ import annotations

import logging
import re
from typing import Iterable

from .. import cache

log = logging.getLogger(__name__)

CACHE_NS = "top_movers"
CACHE_KEY = "tickers"


def _from_yfinance(top_n: int) -> list[str]:
    """yfinance 1.x: yf.screen('most_actives', count=N)。"""
    try:
        import yfinance as yf
    except ImportError:
        log.warning("yfinance not installed")
        return []

    # yfinance 1.x screen()
    if hasattr(yf, "screen"):
        try:
            result = yf.screen("most_actives", count=min(top_n, 250))
            quotes = result.get("quotes", []) if isinstance(result, dict) else []
            tickers = [q.get("symbol") for q in quotes if q.get("symbol")]
            if tickers:
                return tickers[:top_n]
        except Exception as e:
            log.debug("yf.screen('most_actives') failed: %s", e)

    # 舊版 PredefinedScreener (legacy)
    if hasattr(yf, "Screener"):
        try:
            s = yf.Screener()
            s.set_predefined_body("most_actives")
            result = s.response
            quotes = result.get("quotes", []) if isinstance(result, dict) else []
            tickers = [q.get("symbol") for q in quotes if q.get("symbol")]
            if tickers:
                return tickers[:top_n]
        except Exception as e:
            log.debug("yf.Screener() failed: %s", e)

    return []


def _from_finviz(top_n: int) -> list[str]:
    """Finviz HTML scrape — 退路。"""
    try:
        import urllib.request
    except ImportError:
        return []

    url = (
        "https://finviz.com/screener.ashx"
        "?v=111&s=ta_mostactive&o=-volume&f=geo_usa,sh_avgvol_o500"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        log.warning("Finviz fetch failed: %s", e)
        return []

    # Ticker 出現在 <a class="screener-link-primary">TICKER</a>
    matches = re.findall(r'class="screener-link-primary"[^>]*>([A-Z][A-Z0-9.\-]{0,9})<', html)
    seen, out = set(), []
    for t in matches:
        if t not in seen:
            seen.add(t)
            out.append(t)
        if len(out) >= top_n:
            break
    return out


def fetch_top_movers(top_n: int = 50, use_cache: bool = True) -> list[str]:
    """取得當日成交量 Top N ticker。"""
    if use_cache:
        cached = cache.get(CACHE_NS, CACHE_KEY)
        if cached and len(cached) >= top_n:
            log.info("top_movers cache hit (%d tickers)", len(cached))
            return cached[:top_n]

    tickers = _from_yfinance(top_n)
    source = "yfinance"
    if not tickers:
        log.info("yfinance most_actives returned 0, trying Finviz")
        tickers = _from_finviz(top_n)
        source = "finviz"

    if tickers:
        log.info("top_movers fetched %d tickers from %s", len(tickers), source)
        if use_cache:
            cache.set_(CACHE_NS, CACHE_KEY, tickers)
    else:
        log.warning("top_movers: all sources failed, returning empty")

    return tickers


def merge_with_watchlist(watchlist: Iterable[str], top_n: int = 50) -> list[str]:
    """把 Top N 合併進 watchlist,去重保序。watchlist 在前,新增的 Top movers 在後。"""
    seen, out = set(), []
    for t in watchlist:
        t = t.upper().strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    for t in fetch_top_movers(top_n):
        t = t.upper().strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out
