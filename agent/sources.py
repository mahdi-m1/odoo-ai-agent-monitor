"""Manage the channels (RSS feeds / pages / legislation sites) the agent searches.

Stored in data/sources.json; seeded from search_tools.DEFAULT_FEEDS + config/settings.yaml.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import httpx
import yaml
from bs4 import BeautifulSoup

from agent.config_store import ROOT, JsonStore
from agent.tools.search_tools import BROWSER_HEADERS, SearchTools, fill_query, is_challenge_page

logger = logging.getLogger(__name__)

SOURCE_TYPES = ("search", "rss", "page", "legislation")
SAMPLE_QUERY = "Bahrain"
COMMON_FEED_PATHS = ("/feed", "/feed/", "/rss", "/rss.xml", "/feed.xml", "/atom.xml", "/rss/", "/index.xml", "/feeds/posts/default")


def _seed() -> List[Dict[str, Any]]:
    from agent.tools.search_tools import DEFAULT_FEEDS, DEFAULT_SEARCH_FEEDS

    items: List[Dict[str, Any]] = []
    for f in DEFAULT_SEARCH_FEEDS:
        items.append({"name": f["name"], "url": f["url"], "type": "search", "region": f.get("region", ""), "enabled": True})
    for f in DEFAULT_FEEDS:
        items.append({"name": f["name"], "url": f["url"], "type": "rss", "region": f.get("region", ""), "enabled": True})
    try:
        cfg = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8")) or {}
        for region, lst in (cfg.get("sources") or {}).items():
            if region == "legislation":
                for country, sub in (lst or {}).items():
                    for s in sub or []:
                        items.append({"name": s["name"], "url": s["url"], "type": "legislation", "region": country.title(), "enabled": True})
            else:
                for s in lst or []:
                    items.append({"name": s["name"], "url": s["url"], "type": "page", "region": region.replace("_", " ").title(), "enabled": False})
    except Exception as e:
        logger.warning("settings.yaml seed failed: %s", e)
    seen, out = set(), []
    for i, it in enumerate(items, 1):
        if it["url"] in seen:
            continue
        seen.add(it["url"])
        it.setdefault("render_js", False)
        it.update({"id": i, "added_at": datetime.utcnow().isoformat() + "Z", "last_check": None, "last_status": None, "last_items": 0})
        out.append(it)
    return out


class SourceStore:
    def __init__(self):
        self.store = JsonStore("sources.json", {"next_id": 1, "sources": []})
        data = self.store.load()
        if not data.get("sources") and not self.store.path.exists():
            seeded = _seed()
            self.store.save({"next_id": len(seeded) + 1, "sources": seeded})

    # ---- read ----
    def list(self, type: Optional[str] = None, enabled: Optional[bool] = None) -> List[Dict[str, Any]]:
        rows = self.store.load().get("sources", [])
        if type:
            rows = [r for r in rows if r.get("type") == type]
        if enabled is not None:
            rows = [r for r in rows if bool(r.get("enabled")) == enabled]
        return rows

    def get(self, source_id: int) -> Optional[Dict[str, Any]]:
        return next((r for r in self.list() if int(r["id"]) == int(source_id)), None)

    def enabled_feeds(self) -> List[Dict[str, Any]]:
        """Shape expected by SearchTools.search_news_for_entity()."""
        return [{"name": r["name"], "url": r["url"], "region": r.get("region", "")} for r in self.list(type="rss", enabled=True)]

    def enabled_search(self) -> List[Dict[str, Any]]:
        return [{"name": r["name"], "url": r["url"], "region": r.get("region", "")} for r in self.list(type="search", enabled=True)]

    def enabled_legislation(self) -> List[Dict[str, Any]]:
        return self.list(type="legislation", enabled=True)

    def enabled_pages(self) -> List[Dict[str, Any]]:
        return self.list(type="page", enabled=True)

    # ---- write ----
    def add(self, name: str, url: str, type: str = "rss", region: str = "", enabled: bool = True, render_js: bool = False) -> Dict[str, Any]:
        url = (url or "").strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError("الرابط يجب أن يبدأ بـ http:// أو https://")
        if type not in SOURCE_TYPES:
            raise ValueError(f"النوع يجب أن يكون أحد: {', '.join(SOURCE_TYPES)}")
        if type == "search" and "{q}" not in url:
            raise ValueError("مصدر البحث يجب أن يحتوي على {q} مكان كلمة البحث")
        data = self.store.load()
        if any(r["url"] == url for r in data["sources"]):
            raise ValueError("هذا المصدر موجود مسبقاً")
        row = {
            "id": data["next_id"], "name": (name or urlparse(url).netloc).strip(), "url": url, "type": type,
            "region": region or "", "enabled": bool(enabled), "render_js": bool(render_js),
            "added_at": datetime.utcnow().isoformat() + "Z", "last_check": None, "last_status": None, "last_items": 0,
        }
        data["sources"].append(row)
        data["next_id"] += 1
        self.store.save(data)
        return row

    def update(self, source_id: int, **fields: Any) -> Dict[str, Any]:
        data = self.store.load()
        for r in data["sources"]:
            if int(r["id"]) == int(source_id):
                for k, v in fields.items():
                    if v is None:
                        continue
                    if k == "type" and v not in SOURCE_TYPES:
                        raise ValueError("نوع غير صالح")
                    if k in ("name", "url", "type", "region", "enabled", "render_js", "last_check", "last_status", "last_items"):
                        r[k] = v
                self.store.save(data)
                return r
        raise KeyError(f"لا يوجد مصدر بالمعرف {source_id}")

    def remove(self, source_id: int) -> bool:
        data = self.store.load()
        before = len(data["sources"])
        data["sources"] = [r for r in data["sources"] if int(r["id"]) != int(source_id)]
        self.store.save(data)
        return len(data["sources"]) < before

    def reset_defaults(self) -> List[Dict[str, Any]]:
        seeded = _seed()
        self.store.save({"next_id": len(seeded) + 1, "sources": seeded})
        return seeded

    # ---- probe / discover ----
    def test(self, source_id: int) -> Dict[str, Any]:
        row = self.get(source_id)
        if not row:
            raise KeyError(f"لا يوجد مصدر بالمعرف {source_id}")
        result = probe_url(row["url"], row.get("type", "rss"), render_js=bool(row.get("render_js")))
        self.update(source_id, last_check=datetime.utcnow().isoformat() + "Z", last_status="ok" if result["ok"] else f"error: {result.get('error', '')[:80]}", last_items=result.get("items", 0))
        return result

    def test_all(self) -> List[Dict[str, Any]]:
        return [{"id": r["id"], "name": r["name"], **self.test(r["id"])} for r in self.list()]


def probe_url(url: str, type: str = "rss", render_js: bool = False) -> Dict[str, Any]:
    """Fetch a source once and report whether it yields content."""
    try:
        if render_js and type in ("page", "legislation"):
            from agent.tools.browser import get_renderer
            renderer = get_renderer()
            if not renderer.available():
                return {"ok": False, "items": 0, "error": "محرك المتصفح غير مثبت — python -m playwright install chromium"}
            r = renderer.render(url, max_chars=2000)
            if not r["ok"]:
                return {"ok": False, "items": 0, "error": r.get("error", "render failed")}
            return {"ok": len(r.get("text", "")) > 100 or bool(r.get("documents")), "items": len(r.get("documents", [])) or 1,
                    "title": r.get("title", ""), "documents": r.get("documents", []), "sample": r.get("text", "")[:300], "rendered": True}
        if type in ("rss", "search"):
            if type == "search":
                url = fill_query(url, SAMPLE_QUERY)
            st = SearchTools()
            try:
                parsed = st.fetch_feed(url)
            finally:
                st.close()
            if parsed["error"]:
                return {"ok": False, "items": 0, "error": parsed["error"]}
            entries = parsed["entries"]
            return {"ok": True, "items": len(entries), "title": parsed["title"],
                    "sample": [{"title": getattr(e, "title", ""), "link": getattr(e, "link", "")} for e in entries[:3]]}
        r = httpx.get(url, timeout=20, follow_redirects=True, headers=BROWSER_HEADERS)
        if r.status_code >= 400:
            return {"ok": False, "items": 0, "error": "محجوب بحماية Cloudflare/بوت" if is_challenge_page(r.text) else f"HTTP {r.status_code}"}
        soup = BeautifulSoup(r.text, "lxml")
        text = soup.get_text(" ", strip=True)
        return {"ok": len(text) > 200, "items": 1 if len(text) > 200 else 0, "chars": len(text), "title": (soup.title.string if soup.title else "") or "", "sample": text[:300]}
    except Exception as e:
        return {"ok": False, "items": 0, "error": str(e)}


def discover_feeds(site_url: str) -> Dict[str, Any]:
    """Find RSS/Atom feeds for a website: <link rel=alternate>, in-page links, common paths."""
    site_url = site_url.strip()
    if not site_url.startswith(("http://", "https://")):
        site_url = "https://" + site_url
    found: List[Dict[str, Any]] = []
    seen = set()

    def _add(u: str, how: str, title: str = ""):
        u = urljoin(site_url, u)
        if u in seen:
            return
        seen.add(u)
        p = probe_url(u, "rss")
        if p["ok"]:
            found.append({"url": u, "title": title or p.get("title", ""), "items": p["items"], "found_by": how})

    page_title = ""
    try:
        r = httpx.get(site_url, timeout=20, follow_redirects=True, headers=BROWSER_HEADERS)
        r.raise_for_status()
        if is_challenge_page(r.text):
            return {"site": site_url, "title": "", "feeds": [], "page_ok": False, "error": "الموقع محجوب بحماية Cloudflare/بوت من هذا الخادم"}
        soup = BeautifulSoup(r.text, "lxml")
        page_title = (soup.title.string.strip() if soup.title and soup.title.string else "")
        for link in soup.find_all("link", rel=lambda v: v and "alternate" in v):
            t = (link.get("type") or "").lower()
            if "rss" in t or "atom" in t or "xml" in t:
                _add(link.get("href", ""), "link-tag", link.get("title", ""))
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if re.search(r"(rss|feed|atom)(\.xml|/|$)", href, re.I):
                _add(href, "anchor", a.get_text(strip=True)[:60])
    except Exception as e:
        logger.info("discover: page fetch failed for %s: %s", site_url, e)
    if not found:
        base = f"{urlparse(site_url).scheme}://{urlparse(site_url).netloc}"
        for path in COMMON_FEED_PATHS:
            _add(base + path, "common-path")
            if found:
                break
    return {"site": site_url, "title": page_title, "feeds": found, "page_ok": bool(page_title) or probe_url(site_url, "page")["ok"]}
