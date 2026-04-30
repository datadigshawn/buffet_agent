"""新聞訊號計算 (Phase 5 P1-3)。

從近 N 天的新聞列表抽取:
  - article_count_7d:量能
  - sentiment_avg:平均情緒
  - sentiment_trend:近 3 天 vs 前 4 天的差值 (上升/下降/穩定)
  - top_topics:最常出現的 topic_tags
  - material_events:重要事件清單 (從 flash_cn / earnings / 重大關鍵字抽)

也檢測新聞層級的 alert 類型 (negative_spike / positive_spike / material_event)。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Any

from .sources.news import NewsArticle

log = logging.getLogger(__name__)


# 重大事件關鍵字 (中英都列;命中即列入 material_events)
MATERIAL_KEYWORDS = (
    "earnings", "guidance", "lawsuit", "investigation", "antitrust",
    "merger", "acquisition", "spin-off", "bankruptcy", "restructur",
    "downgrade", "upgrade", "buyback", "dividend",
    # 中文
    "財報", "下修", "上修", "收購", "併購", "訴訟", "調查", "破產",
    "裁員", "罷工", "監管", "停牌",
)


@dataclass
class NewsSignals:
    """新聞層級的彙整訊號。"""
    ticker: str
    article_count_7d: int = 0
    sentiment_avg_7d: float | None = None
    sentiment_recent_3d: float | None = None
    sentiment_older_4d: float | None = None
    sentiment_trend: str = "stable"        # rising / falling / stable / unknown
    top_topics: list[str] = field(default_factory=list)
    material_events: list[dict] = field(default_factory=list)  # {title, sentiment, fetched_at, source}
    flash_count_7d: int = 0                 # 重要快訊數
    alert_type: str | None = None           # news_negative_spike / news_positive_spike / material_event

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_topic_tags(tag_str: str | None) -> list[str]:
    if not tag_str:
        return []
    return [t.strip() for t in tag_str.split(",") if t.strip()]


def _is_material(article: NewsArticle) -> bool:
    """判斷單篇新聞是否屬於「重大事件」。"""
    if (article.category or "") in ("flash_cn", "jin10"):
        return True
    text = f"{article.title or ''} {article.summary_zh or ''}".lower()
    return any(kw.lower() in text for kw in MATERIAL_KEYWORDS)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        # ISO with possible 'Z' suffix
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        try:
            return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None


def compute_signals(ticker: str, articles: list[NewsArticle]) -> NewsSignals:
    """主入口:從文章 list 計算訊號。"""
    sig = NewsSignals(ticker=ticker.upper())
    if not articles:
        return sig

    sig.article_count_7d = len(articles)

    # Sentiment 統計
    sentiments = [a.sentiment for a in articles if a.sentiment is not None]
    if sentiments:
        sig.sentiment_avg_7d = round(sum(sentiments) / len(sentiments), 2)

    # Topic tags 統計
    topic_count: dict[str, int] = {}
    for a in articles:
        for t in _parse_topic_tags(a.topic_tags):
            topic_count[t] = topic_count.get(t, 0) + 1
    sig.top_topics = sorted(topic_count, key=lambda k: -topic_count[k])[:5]

    # Sentiment trend (近 3 天 vs 前 4 天)
    cutoff_recent = _now_utc() - timedelta(days=3)
    recent = [a.sentiment for a in articles
              if a.sentiment is not None
              and (_parse_dt(a.fetched_at) or _now_utc()) >= cutoff_recent]
    older = [a.sentiment for a in articles
             if a.sentiment is not None
             and (_parse_dt(a.fetched_at) or _now_utc()) < cutoff_recent]
    if recent:
        sig.sentiment_recent_3d = round(sum(recent) / len(recent), 2)
    if older:
        sig.sentiment_older_4d = round(sum(older) / len(older), 2)
    if sig.sentiment_recent_3d is not None and sig.sentiment_older_4d is not None:
        delta = sig.sentiment_recent_3d - sig.sentiment_older_4d
        if delta >= 0.20:
            sig.sentiment_trend = "rising"
        elif delta <= -0.20:
            sig.sentiment_trend = "falling"
        else:
            sig.sentiment_trend = "stable"
    elif sig.sentiment_recent_3d is not None or sig.sentiment_older_4d is not None:
        sig.sentiment_trend = "stable"
    else:
        sig.sentiment_trend = "unknown"

    # 重大事件 (最多 5 條)
    materials: list[dict] = []
    flash_count = 0
    for a in articles:
        if (a.category or "") in ("flash_cn", "jin10"):
            flash_count += 1
        if _is_material(a) and len(materials) < 5:
            materials.append({
                "title": a.title,
                "summary_zh": (a.summary_zh or "")[:120] if a.summary_zh else None,
                "sentiment": a.sentiment,
                "category": a.category,
                "fetched_at": a.fetched_at,
                "url": a.url,
            })
    sig.material_events = materials
    sig.flash_count_7d = flash_count

    # alert_type 判定:訊號夠強才標
    if (sig.sentiment_trend == "falling"
            and sig.sentiment_avg_7d is not None
            and sig.sentiment_avg_7d < -0.20
            and sig.article_count_7d >= 5):
        sig.alert_type = "news_negative_spike"
    elif (sig.sentiment_trend == "rising"
            and sig.sentiment_avg_7d is not None
            and sig.sentiment_avg_7d > 0.30
            and sig.article_count_7d >= 5):
        sig.alert_type = "news_positive_spike"
    elif sig.flash_count_7d >= 3:
        sig.alert_type = "material_event"

    return sig
