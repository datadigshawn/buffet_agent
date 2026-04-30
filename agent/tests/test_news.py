"""news 來源 + 訊號計算測試 (P1-3)。

用 in-memory SQLite 模擬 news.db,不依賴實際 newsCrawler_bot。
"""
from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent import news_signals
from agent.sources import news as news_src


def _make_news_db(rows: list[dict]) -> Path:
    """建一個臨時 SQLite DB,寫入 rows 後回傳路徑。"""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    path = Path(tmp.name)
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE articles (
            id INTEGER PRIMARY KEY,
            url_hash TEXT, url TEXT, source_name TEXT, source_type TEXT,
            category TEXT, lang TEXT, title TEXT, description TEXT,
            full_content TEXT, published_at TEXT, fetched_at TEXT,
            summary_zh TEXT, tickers TEXT, sentiment REAL,
            topic_tags TEXT, enriched_at TEXT, image_url TEXT, author TEXT
        )
    """)
    for r in rows:
        conn.execute(
            """INSERT INTO articles
            (url_hash, url, source_name, source_type, category, lang, title,
             description, fetched_at, summary_zh, tickers, sentiment, topic_tags)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                r.get("url_hash", "h" + str(r.get("id", 0))),
                r.get("url", "https://x.com"),
                r.get("source_name", "test"),
                r.get("source_type", "rss"),
                r.get("category", "finance"),
                r.get("lang", "zh-tw"),
                r["title"],
                r.get("description"),
                r["fetched_at"],
                r.get("summary_zh"),
                r["tickers"],
                r.get("sentiment"),
                r.get("topic_tags"),
            ),
        )
    conn.commit()
    conn.close()
    return path


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ---------- sources/news.py ----------

def test_fetch_recent_news_basic(monkeypatch):
    rows = [
        {"title": "AAPL beats", "tickers": "AAPL", "sentiment": 0.8,
         "fetched_at": _iso(_now() - timedelta(hours=1))},
        {"title": "MSFT update", "tickers": "MSFT", "sentiment": 0.3,
         "fetched_at": _iso(_now() - timedelta(hours=2))},
        {"title": "Old news AAPL", "tickers": "AAPL", "sentiment": 0.1,
         "fetched_at": _iso(_now() - timedelta(days=10))},  # 超過 7 天
    ]
    db = _make_news_db(rows)
    monkeypatch.setattr(news_src, "NEWS_DB", db)
    articles = news_src.fetch_recent_news("AAPL", days=7)
    titles = [a.title for a in articles]
    assert "AAPL beats" in titles
    assert "Old news AAPL" not in titles   # 超過 7 天篩掉
    assert "MSFT update" not in titles      # ticker 不符


def test_fetch_recent_news_no_db(monkeypatch, tmp_path):
    monkeypatch.setattr(news_src, "NEWS_DB", tmp_path / "missing.db")
    assert news_src.fetch_recent_news("AAPL") == []


def test_fetch_recent_news_flash_priority(monkeypatch):
    """flash_cn 應該排在前面 (即使 fetched_at 較舊)。"""
    rows = [
        {"title": "Regular news", "tickers": "AAPL", "category": "finance",
         "fetched_at": _iso(_now() - timedelta(hours=1))},
        {"title": "FLASH important", "tickers": "AAPL", "category": "flash_cn",
         "fetched_at": _iso(_now() - timedelta(hours=5))},   # 較舊
    ]
    db = _make_news_db(rows)
    monkeypatch.setattr(news_src, "NEWS_DB", db)
    articles = news_src.fetch_recent_news("AAPL")
    assert articles[0].title == "FLASH important"
    assert articles[0].category == "flash_cn"


# ---------- news_signals.py ----------

def test_compute_signals_empty():
    sig = news_signals.compute_signals("X", [])
    assert sig.article_count_7d == 0
    assert sig.alert_type is None


def test_compute_signals_basic_aggregates(monkeypatch):
    arts = [
        news_src.NewsArticle(
            id=i, title=f"a{i}", summary_zh=None, sentiment=s,
            published_at=None, fetched_at=_iso(_now() - timedelta(hours=h)),
            source_name="x", category="finance", topic_tags="ai,tech", url="u",
        )
        for i, (s, h) in enumerate([(0.5, 1), (0.6, 2), (0.4, 3)])
    ]
    sig = news_signals.compute_signals("X", arts)
    assert sig.article_count_7d == 3
    assert sig.sentiment_avg_7d == pytest.approx(0.50, abs=0.01)
    assert "ai" in sig.top_topics


def test_compute_signals_negative_spike(monkeypatch):
    """近 3 天 sentiment -0.7,前 4 天 -0.3 → falling + negative_spike (avg < -0.20)。"""
    arts = []
    for i in range(6):    # 近 3 天 6 篇 (-0.7)
        arts.append(news_src.NewsArticle(
            id=i, title=f"recent{i}", summary_zh=None, sentiment=-0.7,
            published_at=None,
            fetched_at=_iso(_now() - timedelta(hours=12 + i)),
            source_name="x", category="finance", topic_tags="lawsuit", url="u",
        ))
    for i in range(6, 12):   # 前 4 天 6 篇 (-0.3)
        arts.append(news_src.NewsArticle(
            id=i, title=f"old{i}", summary_zh=None, sentiment=-0.3,
            published_at=None,
            fetched_at=_iso(_now() - timedelta(days=4, hours=i)),
            source_name="x", category="finance", topic_tags="earnings", url="u",
        ))
    sig = news_signals.compute_signals("X", arts)
    assert sig.sentiment_trend == "falling"
    assert sig.alert_type == "news_negative_spike"


def test_compute_signals_material_event_via_flash():
    """flash_cn ≥ 3 → material_event alert。"""
    arts = [
        news_src.NewsArticle(
            id=i, title=f"flash{i}", summary_zh=None, sentiment=0.1,
            published_at=None, fetched_at=_iso(_now() - timedelta(hours=i)),
            source_name="jin10", category="flash_cn", topic_tags="macro", url="u",
        )
        for i in range(3)
    ]
    sig = news_signals.compute_signals("X", arts)
    assert sig.alert_type == "material_event"
    assert sig.flash_count_7d == 3


def test_compute_signals_material_keyword_in_title():
    """關鍵字 'lawsuit' / '訴訟' 應觸發 material event 收錄。"""
    arts = [
        news_src.NewsArticle(
            id=1, title="X faces antitrust lawsuit", summary_zh=None,
            sentiment=-0.7, published_at=None,
            fetched_at=_iso(_now()),
            source_name="x", category="finance", topic_tags="legal", url="u",
        ),
    ]
    sig = news_signals.compute_signals("X", arts)
    assert len(sig.material_events) == 1
    assert "lawsuit" in sig.material_events[0]["title"]


# ---------- diff.news_alerts_from_verdicts ----------

def test_news_alerts_from_verdicts_high_for_negative():
    from agent import diff
    verdicts = [
        {"ticker": "X", "news": {
            "alert_type": "news_negative_spike",
            "sentiment_avg_7d": -0.5, "article_count_7d": 10,
            "flash_count_7d": 2,
        }},
        {"ticker": "Y", "news": {
            "alert_type": "news_positive_spike",
            "sentiment_avg_7d": 0.6, "article_count_7d": 8,
            "flash_count_7d": 0,
        }},
        {"ticker": "Z", "news": None},   # 沒新聞 → 不應 alert
    ]
    alerts = diff.news_alerts_from_verdicts(verdicts)
    types = {(a.ticker, a.severity) for a in alerts}
    assert ("X", "high") in types
    assert ("Y", "medium") in types
    assert all(a.ticker != "Z" for a in alerts)
