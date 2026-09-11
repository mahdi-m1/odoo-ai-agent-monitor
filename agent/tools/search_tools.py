"""
Lightweight search helpers: RSS + basic HTTP fetch.
LinkedIn/social are structured for extension (official APIs or manual sources).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import feedparser
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DEFAULT_FEEDS = [
    {"name": "Arab News", "url": "https://www.arabnews.com/rss.xml", "region": "GCC"},
    {"name": "Gulf Business", "url": "https://gulfbusiness.com/feed/", "region": "GCC"},
    {"name": "Reuters Business", "url": "https://www.reutersagency.com/feed/?taxonomy=best-topics&post_type=best", "region": "Global"},
]


class SearchTools:
    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout
        self.client = httpx.Client(timeout=timeout, follow_redirects=True)

    def close(self) -> None:
        self.client.close()

    def fetch_rss(self, feed_url: str, limit: int = 15) -> List[Dict[str, Any]]:
        try:
            parsed = feedparser.parse(feed_url)
            items = []
            for entry in parsed.entries[:limit]:
                items.append(
                    {
                        "title": getattr(entry, "title", ""),
                        "link": getattr(entry, "link", ""),
                        "summary": getattr(entry, "summary", "")[:500],
                        "published": getattr(entry, "published", ""),
                        "source": feed_url,
                    }
                )
            return items
        except Exception as e:
            logger.warning("RSS fetch failed %s: %s", feed_url, e)
            return []

    def search_news_for_entity(
        self,
        entity_name: str,
        feeds: Optional[List[Dict]] = None,
        keywords: Optional[List[str]] = None,
        limit_per_feed: int = 10,
    ) -> List[Dict[str, Any]]:
        feeds = feeds or DEFAULT_FEEDS
        keywords = keywords or []
        query_terms = [entity_name.lower()] + [k.lower() for k in keywords]
        results: List[Dict[str, Any]] = []

        for feed in feeds:
            items = self.fetch_rss(feed["url"], limit=limit_per_feed)
            for item in items:
                text = f"{item['title']} {item['summary']}".lower()
                if any(term in text for term in query_terms):
                    item["matched_entity"] = entity_name
                    item["feed_name"] = feed.get("name", "")
                    item["region"] = feed.get("region", "")
                    results.append(item)
        return results

    def fetch_page_text(self, url: str, max_chars: int = 8000) -> str:
        try:
            r = self.client.get(url)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")
            for tag in soup(["script", "style", "nav", "footer"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            return text[:max_chars]
        except Exception as e:
            logger.warning("Page fetch failed %s: %s", url, e)
            return ""

    def filter_by_keywords(
        self,
        items: List[Dict[str, Any]],
        keywords: List[str],
    ) -> List[Dict[str, Any]]:
        keys = [k.lower() for k in keywords]
        out = []
        for item in items:
            text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
            if any(k in text for k in keys):
                out.append(item)
        return out

    @staticmethod
    def domain_of(url: str) -> str:
        try:
            return urlparse(url).netloc
        except Exception:
            return ""
