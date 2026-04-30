"""newsCrawler_bot 整合 (Phase 5 P1-3)。

讀 ~/autobot/newsCrawler_bot/data/news.db 抓近 N 天的個股新聞,
餵給 LLM context + 用來計算事件訊號 (sentiment 趨勢、關鍵事件)。

設計原則:
- 純讀取 (read-only),不寫 newsCrawler_bot 的 DB
- DB 不存在或路徑不對 → 回空 list,不阻塞 verdict
- 用 substring match 抓 tickers 欄位(逗號分隔字串)
- 環境變數 BUFFET_NEWS_DB 可 override 路徑
"""
from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

NEWS_DB = Path(
    os.environ.get(
        "BUFFET_NEWS_DB",
        str(Path.home() / "autobot" / "newsCrawler_bot" / "data" / "news.db"),
    )
)


@dataclass
class NewsArticle:
    """精簡新聞物件,給 LLM context + 信號計算用。"""
    id: int
    title: str
    summary_zh: str | None
    sentiment: float | None
    published_at: str | None
    fetched_at: str
    source_name: str | None
    category: str | None
    topic_tags: str | None
    url: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def fetch_recent_news(ticker: str, days: int = 7, max_articles: int = 20) -> list[NewsArticle]:
    """抓近 N 天某 ticker 的新聞 (按 fetched_at 降序)。

    優先序:
      1. 重要快訊 (category=flash_cn 或 jin10) → 永遠保留
      2. 其他類別 → 限 max_articles - flash 數量
    """
    if not NEWS_DB.exists():
        log.debug("news.db not found at %s", NEWS_DB)
        return []

    try:
        with sqlite3.connect(f"file:{NEWS_DB}?mode=ro", uri=True, timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            ticker_pattern = f"%{ticker.upper()}%"
            rows = conn.execute(
                """
                SELECT id, title, summary_zh, sentiment, published_at, fetched_at,
                       source_name, category, topic_tags, url
                FROM articles
                WHERE tickers LIKE ?
                  AND fetched_at > datetime('now', ?)
                ORDER BY
                  CASE WHEN category IN ('flash_cn','jin10') THEN 0 ELSE 1 END,
                  fetched_at DESC
                LIMIT ?
                """,
                (ticker_pattern, f"-{days} days", max_articles),
            ).fetchall()
    except sqlite3.Error as e:
        log.warning("news.db query failed for %s: %s", ticker, e)
        return []

    return [
        NewsArticle(
            id=r["id"],
            title=r["title"],
            summary_zh=r["summary_zh"],
            sentiment=r["sentiment"],
            published_at=r["published_at"],
            fetched_at=r["fetched_at"],
            source_name=r["source_name"],
            category=r["category"],
            topic_tags=r["topic_tags"],
            url=r["url"],
        )
        for r in rows
    ]


def is_available() -> bool:
    """快速檢查 news.db 是否可用 (供 verdict 決定是否跑 news 分析)。"""
    return NEWS_DB.exists() and NEWS_DB.stat().st_size > 0
