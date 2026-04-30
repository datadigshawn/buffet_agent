"""SEC EDGAR XBRL client.

模式參考 stockAnalysis_bot_MultiAgent/src/api/sec_api.py,但獨立實作避免跨 repo 依賴。
- 純 stdlib (urllib),不引入 httpx
- 兩級快取:CIK map (30 天) + companyfacts (7 天)
- 快取檔案位於 data/sec_cache/ (會 commit 進 repo,GH Actions 不需重抓)
- 限流 1 req/s (SEC 政策上限 10/s,保守處理)
- User-Agent 必填 (SEC 要求附 contact email)
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CACHE_DIR = REPO_ROOT / "data" / "sec_cache"

CIK_MAP_PATH = CACHE_DIR / "_cik_map.json"
CIK_MAP_TTL_DAYS = 30
FACTS_TTL_DAYS = 7

SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT",
    "buffetAgent contact@datadigshawn.local",
)
HEADERS = {"User-Agent": SEC_USER_AGENT, "Accept": "application/json"}

# SEC 限流:政策 10 req/s,保守 1 req/s
SEC_REQUEST_INTERVAL_SEC = 1.0
_last_request_at = 0.0


def _ensure_cache_dir() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _file_age_days(path: Path) -> float:
    if not path.exists():
        return float("inf")
    return (time.time() - path.stat().st_mtime) / 86400


def _http_get_json(url: str, timeout: int = 30) -> dict | None:
    """發送 GET 並解析 JSON,失敗回 None。內含 SEC 限流。"""
    global _last_request_at
    elapsed = time.time() - _last_request_at
    if elapsed < SEC_REQUEST_INTERVAL_SEC:
        time.sleep(SEC_REQUEST_INTERVAL_SEC - elapsed)

    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            data = resp.read()
        _last_request_at = time.time()
        return json.loads(data.decode("utf-8"))
    except urllib.error.HTTPError as e:
        _last_request_at = time.time()
        if e.code == 404:
            return None
        log.warning("SEC %s HTTP %s: %s", url, e.code, e.reason)
        return None
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        _last_request_at = time.time()
        log.warning("SEC %s failed: %s", url, e)
        return None


# ---------- CIK map ----------

def load_cik_map(force_refresh: bool = False) -> dict[str, str]:
    """Ticker → 10-digit zero-padded CIK。30 天本地 cache。"""
    _ensure_cache_dir()
    if (
        CIK_MAP_PATH.exists()
        and not force_refresh
        and _file_age_days(CIK_MAP_PATH) < CIK_MAP_TTL_DAYS
    ):
        try:
            return json.loads(CIK_MAP_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass  # 壞檔重新抓

    raw = _http_get_json("https://www.sec.gov/files/company_tickers.json")
    if not raw:
        # 抓不到 → 若有舊 cache 就拿來用,沒有就空 dict
        if CIK_MAP_PATH.exists():
            try:
                return json.loads(CIK_MAP_PATH.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return {}
        return {}

    parsed = _parse_cik_map(raw)
    CIK_MAP_PATH.write_text(json.dumps(parsed, ensure_ascii=False), encoding="utf-8")
    return parsed


def _parse_cik_map(raw: dict) -> dict[str, str]:
    """Raw JSON 是 row-indexed,扁平化成 {TICKER: CIK10}。"""
    out: dict[str, str] = {}
    for entry in raw.values():
        ticker = (entry.get("ticker") or "").upper()
        cik = entry.get("cik_str")
        if ticker and cik is not None:
            out[ticker] = f"{int(cik):010d}"
    return out


def get_cik(ticker: str) -> str | None:
    """Ticker → 10 位 CIK 字串,找不到回 None。"""
    return load_cik_map().get(ticker.upper().strip())


# ---------- companyfacts ----------

def _facts_cache_path(cik: str) -> Path:
    return CACHE_DIR / f"{cik}.json"


def fetch_company_facts(cik: str, force_refresh: bool = False) -> dict | None:
    """抓 companyfacts JSON,7 天 cache。404 (公司沒申報) 回 None。"""
    _ensure_cache_dir()
    cache_path = _facts_cache_path(cik)

    if (
        cache_path.exists()
        and not force_refresh
        and _file_age_days(cache_path) < FACTS_TTL_DAYS
    ):
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    data = _http_get_json(url)
    if data:
        cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data
    # 抓失敗但有舊 cache → 用舊的
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
    return None


def get_facts(ticker: str) -> dict | None:
    """高階入口:ticker → companyfacts。沒 CIK 或沒申報回 None。"""
    cik = get_cik(ticker)
    if not cik:
        return None
    return fetch_company_facts(cik)


# ---------- 概念對映 ----------
# 同一個經濟意義在不同公司 XBRL 用的 concept 可能不同,逐個試。
CONCEPT_ALTERNATES: dict[str, list[str]] = {
    "Revenues": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ],
    "NetIncome": ["NetIncomeLoss"],
    "OperatingCashFlow": ["NetCashProvidedByUsedInOperatingActivities"],
    "Capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
        "PaymentsToAcquireRealEstate",
        "PaymentsToAcquireBuildings",
        "PaymentsToDevelopRealEstateAssets",
    ],
    "LongTermDebt": [
        "LongTermDebt",
        "LongTermDebtNoncurrent",
    ],
    "TotalEquity": [
        # 「Including NCI」放第一位:某些公司 (V) 早期用 StockholdersEquity 但 2012+ 改用此名,
        # 我們的「first match」邏輯會卡在舊概念。放此名第一可拿到較完整年份序列。
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "StockholdersEquity",
    ],
    "TotalAssets": ["Assets"],
    "TotalLiabilities": ["Liabilities"],
    "SharesOutstanding": [
        "CommonStockSharesOutstanding",
        "EntityCommonStockSharesOutstanding",
        # 部分公司 (KO/MCO) 不直接報 outstanding,改用 weighted average diluted
        "WeightedAverageNumberOfDilutedSharesOutstanding",
        "WeightedAverageNumberOfSharesOutstandingBasic",
    ],
    "DividendsPerShare": [
        "CommonStockDividendsPerShareDeclared",
        "CommonStockDividendsPerShareCashPaid",
    ],
    "Dividends": [
        "PaymentsOfDividendsCommonStock",
        "PaymentsOfDividends",
    ],
    "StockRepurchase": [
        "PaymentsForRepurchaseOfCommonStock",
        "TreasuryStockValueAcquiredCostMethod",
    ],
    # P2-3 industry-specific
    "Depreciation": [
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "DepreciationAndAmortization",
        "Depreciation",
    ],
    "InterestIncome": [
        "InterestAndDividendIncomeOperating",
        "InterestAndDividendIncomeOperatingNet",
        "InterestIncomeOperating",
    ],
    "InterestExpense": [
        "InterestExpense",
        "InterestExpenseOperating",
    ],
}


def get_concept_units(facts_json: dict, our_name: str) -> dict | None:
    """從 us-gaap facts 中拿出某個 concept 的 units dict。試多個替代名,取第一個有資料的。"""
    if not facts_json:
        return None
    facts = facts_json.get("facts", {}).get("us-gaap", {})
    for alt in CONCEPT_ALTERNATES.get(our_name, [our_name]):
        entry = facts.get(alt)
        if not entry:
            continue
        units = entry.get("units", {})
        if units:
            return units
    return None
