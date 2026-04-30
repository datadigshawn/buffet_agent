"""日級 yfinance 快取層。

避免同一天重複跑 ticker 浪費 API 配額(retry / 多入口呼叫)。
- TTL: 當天 (UTC date) 內有效,跨日自動失效
- 後端: JSON 檔 (簡單、可 git ignore)
- 路徑: $BUFFET_CACHE_DIR > <repo>/.cache
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = Path(os.environ.get("BUFFET_CACHE_DIR", str(REPO_ROOT / ".cache")))


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _cache_path(namespace: str) -> Path:
    return CACHE_DIR / f"{namespace}_{_today_utc()}.json"


def _load(namespace: str) -> dict[str, Any]:
    path = _cache_path(namespace)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("cache load failed (%s): %s", path, e)
        return {}


def _save(namespace: str, data: dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(namespace)
    try:
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        log.warning("cache save failed (%s): %s", path, e)


def get(namespace: str, key: str) -> Any | None:
    """讀快取。命中回 value,沒命中回 None。"""
    data = _load(namespace)
    return data.get(key)


def set_(namespace: str, key: str, value: Any) -> None:
    """寫快取。當日內生效。"""
    data = _load(namespace)
    data[key] = value
    _save(namespace, data)


def clear_old(keep_days: int = 7) -> int:
    """清掉超過 keep_days 的舊快取檔。回傳刪除數。"""
    if not CACHE_DIR.exists():
        return 0
    today = datetime.now(timezone.utc).date()
    deleted = 0
    for f in CACHE_DIR.glob("*_*.json"):
        try:
            date_str = f.stem.rsplit("_", 1)[1]
            file_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            if (today - file_date).days > keep_days:
                f.unlink()
                deleted += 1
        except (ValueError, IndexError, OSError):
            continue
    return deleted
