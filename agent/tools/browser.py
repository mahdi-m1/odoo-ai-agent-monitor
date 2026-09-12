"""Optional JavaScript page renderer (Playwright, headless Chromium).

Used only for sources that need JS to expose their content (some official gazettes / portals are SPAs).
This is a plain renderer — it identifies itself with a normal User-Agent, respects a rate limit, and
does NOT attempt any anti-bot evasion (no stealth, no fingerprint spoofing, no proxy rotation). If a
site returns a real bot challenge, rendering fails honestly and the source is reported as blocked.

Enable per source with render_js=true. Availability:
  BROWSER_RENDER=auto (default: use if Playwright+Chromium installed) | on | off
Install once:  python -m playwright install chromium
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
DOC_RE = re.compile(r"\.(pdf|docx?|xlsx?)(\?|#|$)", re.I)
_min_interval = float(os.getenv("BROWSER_MIN_INTERVAL", "2.0"))  # polite pacing between renders


class BrowserRenderer:
    """Lazy, serialized headless renderer. One page at a time (safe on small RAM)."""

    def __init__(self):
        self.mode = os.getenv("BROWSER_RENDER", "auto").strip().lower()
        self._lock = threading.Lock()
        self._checked = False
        self._available = False
        self._last = 0.0

    def _check(self) -> None:
        if self._checked:
            return
        self._checked = True
        if self.mode == "off":
            return
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
            self._available = True
        except Exception as e:
            logger.info("Browser rendering unavailable (%s) — install: python -m playwright install chromium", e)

    def available(self) -> bool:
        self._check()
        return self._available

    def status(self) -> Dict[str, Any]:
        return {"mode": self.mode, "available": self.available(), "engine": "playwright/chromium"}

    def render(self, url: str, wait_ms: int = 1500, timeout_ms: int = 45000, max_chars: int = 8000) -> Dict[str, Any]:
        """Return {ok, text, documents:[{url,label}], links_count, error}. Serialized + rate-limited."""
        if not self.available():
            return {"ok": False, "error": "محرك المتصفح غير مثبت (python -m playwright install chromium)", "text": "", "documents": []}
        with self._lock:
            gap = time.time() - self._last
            if gap < _min_interval:
                time.sleep(_min_interval - gap)
            try:
                result = self._render_once(url, wait_ms, timeout_ms, max_chars)
            except Exception as e:
                logger.warning("render failed %s: %s", url, e)
                result = {"ok": False, "error": str(e)[:300], "text": "", "documents": []}
            finally:
                self._last = time.time()
            return result

    def _render_once(self, url: str, wait_ms: int, timeout_ms: int, max_chars: int) -> Dict[str, Any]:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
            try:
                ctx = browser.new_context(user_agent=UA, locale="ar")
                page = ctx.new_page()
                page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                try:
                    page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 15000))
                except Exception:
                    pass  # networkidle is best-effort; some pages keep long-poll connections open
                if wait_ms:
                    page.wait_for_timeout(wait_ms)
                title = page.title()
                anchors = page.eval_on_selector_all(
                    "a[href]", "els => els.map(e => ({href: e.href, text: (e.innerText||e.textContent||'').trim().slice(0,120)}))"
                )
                body_text = page.eval_on_selector("body", "el => el.innerText") or ""
            finally:
                browser.close()
        docs, seen = [], set()
        for a in anchors:
            href = a.get("href", "")
            if href and DOC_RE.search(href) and href not in seen:
                seen.add(href)
                docs.append({"url": href, "label": a.get("text") or urlparse(href).path.rsplit("/", 1)[-1]})
        return {"ok": True, "title": title, "text": re.sub(r"\n{3,}", "\n\n", body_text)[:max_chars],
                "documents": docs, "links_count": len(anchors), "error": None}

    def find_documents(self, url: str, keywords: Optional[List[str]] = None) -> Dict[str, Any]:
        """Render a page and return its document links (PDF/doc/xls), optionally filtered by keyword."""
        r = self.render(url, max_chars=2000)
        if not r["ok"]:
            return r
        docs = r["documents"]
        if keywords:
            kl = [k.lower() for k in keywords]
            docs = [d for d in docs if any(k in (d["url"] + d["label"]).lower() for k in kl)]
        return {"ok": True, "title": r.get("title", ""), "documents": docs, "count": len(docs)}


_renderer: Optional[BrowserRenderer] = None


def get_renderer() -> BrowserRenderer:
    global _renderer
    if _renderer is None:
        _renderer = BrowserRenderer()
    return _renderer
