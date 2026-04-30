"""SEC EDGAR submissions API — 抓 recent filings 計數 (Phase 5 P1-3.5)。

不解析 Form 內文(那需要 XBRL parsing),只看 form/filingDate 統計。
用途:13D/13G/8-K 計數 → 重大股權異動 / 重大事件訊號。

Insider 交易 (Form 4) 雖然這 endpoint 也列得到,但 yfinance.insider_transactions
直接給結構化 DataFrame,我們在 insider_signals.py 用 yfinance,這 module 補充
yfinance 沒給的 13D/13G/8-K 計數。
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import sec as sec_api

log = logging.getLogger(__name__)

# 重複用同一 cache 機制,但 submissions API 是輕量(<200KB),TTL 1 天就夠
SUBMISSIONS_TTL_DAYS = 1


@dataclass
class FilingCounts:
    """recent N 天的 filing 計數。"""
    cik: str
    days: int
    form_4_count: int = 0          # insider transactions (Form 4)
    form_3_count: int = 0          # initial insider statement
    form_5_count: int = 0          # annual insider statement
    sched_13d_count: int = 0       # 5%+ activist holder
    sched_13g_count: int = 0       # 5%+ passive holder
    form_8k_count: int = 0         # material event
    def_14a_count: int = 0         # proxy statement
    recent_13d_dates: list[str] = field(default_factory=list)
    recent_13g_dates: list[str] = field(default_factory=list)
    recent_8k_dates: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _submissions_url(cik: str) -> str:
    return f"https://data.sec.gov/submissions/CIK{cik}.json"


def _cache_path(cik: str) -> Path:
    sec_api._ensure_cache_dir()
    return sec_api.CACHE_DIR / f"submissions_{cik}.json"


def fetch_submissions(cik: str, force_refresh: bool = False) -> dict | None:
    """抓 SEC submissions JSON,1 天 cache。"""
    cp = _cache_path(cik)
    if (
        cp.exists()
        and not force_refresh
        and sec_api._file_age_days(cp) < SUBMISSIONS_TTL_DAYS
    ):
        try:
            return json.loads(cp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    data = sec_api._http_get_json(_submissions_url(cik))
    if data:
        cp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    elif cp.exists():
        try:
            return json.loads(cp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
    return data


def filing_counts(ticker: str, days: int = 90) -> FilingCounts | None:
    """主入口:近 N 天 form 統計。"""
    cik = sec_api.get_cik(ticker)
    if not cik:
        return None
    submissions = fetch_submissions(cik)
    if not submissions:
        return None

    fc = FilingCounts(cik=cik, days=days)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    recent = (submissions.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []

    for form, date in zip(forms, dates):
        if not date or date < cutoff:
            continue
        f = form.upper()
        if f == "4":
            fc.form_4_count += 1
        elif f == "3":
            fc.form_3_count += 1
        elif f == "5":
            fc.form_5_count += 1
        elif "13D" in f:
            fc.sched_13d_count += 1
            if len(fc.recent_13d_dates) < 5:
                fc.recent_13d_dates.append(date)
        elif "13G" in f:
            fc.sched_13g_count += 1
            if len(fc.recent_13g_dates) < 5:
                fc.recent_13g_dates.append(date)
        elif f == "8-K":
            fc.form_8k_count += 1
            if len(fc.recent_8k_dates) < 5:
                fc.recent_8k_dates.append(date)
        elif "14A" in f:
            fc.def_14a_count += 1
    return fc
