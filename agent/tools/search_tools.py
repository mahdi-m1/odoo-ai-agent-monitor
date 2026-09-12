"""
Lightweight search helpers: RSS + basic HTTP fetch.
LinkedIn/social are structured for extension (official APIs or manual sources).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse

import feedparser
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DEFAULT_FEEDS = [
    {"name": "Al-Bilad (البلاد)", "url": "https://albiladpress.com/rss", "region": "Bahrain"},
    {"name": "Arab News", "url": "https://www.arabnews.com/rss.xml", "region": "GCC"},
    {"name": "Gulf Business", "url": "https://gulfbusiness.com/feed/", "region": "GCC"},
    {"name": "Reuters Business", "url": "https://www.reutersagency.com/feed/?taxonomy=best-topics&post_type=best", "region": "Global"},
]

# Search channels: "{q}" is replaced by the monitored entity's name — far more precise than scanning fixed feeds.
DEFAULT_SEARCH_FEEDS = [
    {"name": "Google News (عربي)", "url": "https://news.google.com/rss/search?q={q}&hl=ar&gl=BH&ceid=BH:ar", "region": "GCC"},
    {"name": "Google News (English)", "url": "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en", "region": "Global"},
    {"name": "Bing News", "url": "https://www.bing.com/news/search?q={q}&format=rss", "region": "Global"},
]

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, text/html, */*",
    "Accept-Language": "ar,en;q=0.8",
}


def is_challenge_page(text: str) -> bool:
    head = (text or "")[:1500].lower()
    return "just a moment" in head or "cf-challenge" in head or "attention required" in head or "captcha" in head


def fill_query(template: str, query: str) -> str:
    return template.replace("{q}", quote(query))


def entity_query(partner: Dict[str, Any]) -> str:
    """Search phrase for a partner: exact name, plus the company for people (disambiguates common first names)."""
    name = (partner.get("name") or "").strip()
    q = f'"{name}"' if " " in name else name
    parent = partner.get("parent_id")
    parent_name = parent[1] if isinstance(parent, (list, tuple)) and len(parent) > 1 else ""
    if not partner.get("is_company") and parent_name:
        q += f' "{parent_name}"'
    return q


class SearchTools:
    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout
        self.client = httpx.Client(timeout=timeout, follow_redirects=True, headers=BROWSER_HEADERS)

    def close(self) -> None:
        self.client.close()

    def fetch_feed(self, feed_url: str) -> Dict[str, Any]:
        """Fetch + parse a feed; returns {"entries": [...], "error": str|None, "title": str}."""
        try:
            r = self.client.get(feed_url)
            if r.status_code >= 400:
                err = "محجوب بحماية Cloudflare/بوت (challenge page)" if is_challenge_page(r.text) else f"HTTP {r.status_code}"
                return {"entries": [], "error": err, "title": ""}
            parsed = feedparser.parse(r.content)
            if not parsed.entries:
                err = "الرد ليس خلاصة RSS/Atom (صفحة HTML)" if "<html" in r.text[:500].lower() else str(parsed.get("bozo_exception") or "خلاصة فارغة")
                return {"entries": [], "error": err, "title": parsed.feed.get("title", "")}
            return {"entries": parsed.entries, "error": None, "title": parsed.feed.get("title", "")}
        except Exception as e:
            return {"entries": [], "error": str(e), "title": ""}

    def fetch_rss(self, feed_url: str, limit: int = 15) -> List[Dict[str, Any]]:
        try:
            parsed = self.fetch_feed(feed_url)
            if parsed["error"]:
                logger.warning("RSS fetch failed %s: %s", feed_url, parsed["error"])
                return []
            items = []
            for entry in parsed["entries"][:limit]:
                items.append(
                    {
                        "title": getattr(entry, "title", ""),
                        "link": getattr(entry, "link", ""),
                        "summary": BeautifulSoup(getattr(entry, "summary", "") or "", "lxml").get_text(" ", strip=True)[:500],
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
        search_feeds: Optional[List[Dict]] = None,
        query: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        query = query or entity_name
        feeds = feeds if feeds is not None else DEFAULT_FEEDS
        keywords = keywords or []
        query_terms = [entity_name.lower()] + [k.lower() for k in keywords]
        results: List[Dict[str, Any]] = []
        seen_links = set()

        # Search channels return results already scoped to the entity.
        for feed in (search_feeds if search_feeds is not None else DEFAULT_SEARCH_FEEDS):
            for item in self.fetch_rss(fill_query(feed["url"], query), limit=limit_per_feed):
                if item["link"] in seen_links:
                    continue
                seen_links.add(item["link"])
                item["matched_entity"] = entity_name
                item["feed_name"] = feed.get("name", "")
                item["region"] = feed.get("region", "")
                item["via"] = "search"
                results.append(item)

        for feed in feeds:
            items = self.fetch_rss(feed["url"], limit=limit_per_feed)
            for item in items:
                text = f"{item['title']} {item['summary']}".lower()
                if any(term in text for term in query_terms) and item["link"] not in seen_links:
                    seen_links.add(item["link"])
                    item["matched_entity"] = entity_name
                    item["feed_name"] = feed.get("name", "")
                    item["region"] = feed.get("region", "")
                    item["via"] = "feed"
                    results.append(item)
        return results

    def fetch_page_text(self, url: str, max_chars: int = 8000) -> str:
        try:
            r = self.client.get(url)
            r.raise_for_status()
            if is_challenge_page(r.text):
                logger.warning("Page fetch blocked by bot protection: %s", url)
                return ""
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
