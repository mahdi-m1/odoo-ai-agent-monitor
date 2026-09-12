"""Optional social / LinkedIn monitor (disabled by default).

Configured from the web UI (data/agent_settings.json → "social") with .env fallback:
  SOCIAL_ENABLED / SOCIAL_PUBLIC_FETCH / LINKEDIN_ENABLED + LINKEDIN_ACCESS_TOKEN
Per-partner accounts are stored on the Odoo partner as `[SOCIAL] <url>` lines (see OdooTools.set_social_links).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

import httpx

from agent import agent_settings
from agent.memory import get_memory
from agent.tools.odoo_tools import OdooTools
from agent.tools.search_tools import SearchTools

logger = logging.getLogger(__name__)

PLATFORM_DOMAINS = {
    "linkedin": ("linkedin.com",),
    "x": ("x.com", "twitter.com"),
    "instagram": ("instagram.com",),
    "facebook": ("facebook.com", "fb.com"),
    "youtube": ("youtube.com", "youtu.be"),
    "tiktok": ("tiktok.com",),
}


def platform_of(url: str) -> str:
    u = (url or "").lower()
    for name, domains in PLATFORM_DOMAINS.items():
        if any(d in u for d in domains):
            return name
    return "other"


def verify_linkedin_token(token: str) -> Dict[str, Any]:
    """Validate a LinkedIn OAuth token against the userinfo endpoint (OpenID Connect scope)."""
    if not token:
        return {"ok": False, "error": "لا يوجد token"}
    try:
        r = httpx.get("https://api.linkedin.com/v2/userinfo", headers={"Authorization": f"Bearer {token}"}, timeout=15)
        if r.status_code == 200:
            j = r.json()
            return {"ok": True, "name": j.get("name"), "email": j.get("email"), "sub": j.get("sub")}
        return {"ok": False, "status": r.status_code, "error": r.text[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class SocialMonitor:
    def __init__(self, odoo: OdooTools | None = None, search: SearchTools | None = None):
        self.odoo = odoo or OdooTools()
        self.search = search or SearchTools()
        cfg = agent_settings.get_social()
        self.social_enabled = cfg["social_enabled"]
        self.public_fetch = cfg["public_fetch"]
        self.linkedin_enabled = cfg["linkedin_enabled"] and bool(cfg["linkedin_token"])
        self.platforms = set(cfg["platforms"])
        self.active = self.social_enabled or self.linkedin_enabled

    def run(self, limit_entities: int = 40) -> List[Dict[str, Any]]:
        if not self.active:
            logger.info("SocialMonitor disabled")
            return []
        hits: List[Dict[str, Any]] = []
        for p in self.odoo.list_monitored(limit=limit_entities):
            for link in self.odoo.get_social_links(p):
                plat = platform_of(link)
                if plat != "other" and plat not in self.platforms:
                    continue
                item = {"partner_id": p.get("id"), "partner_name": p.get("name"), "platform": plat, "link": link}
                if self.public_fetch:
                    item["excerpt"] = self.search.fetch_page_text(link, max_chars=1500)
                hits.append(item)
        return hits

    def run_and_log(self, limit_entities: int = 40) -> Dict[str, Any]:
        if not self.active:
            return {"active": False, "hits": 0, "logged": 0, "note": "معطّل — فعّل قنوات التواصل من صفحة «المصادر والقنوات»"}
        hits = self.run(limit_entities=limit_entities)
        memory = get_memory()
        logged = 0
        for h in hits[:50]:
            if not h.get("excerpt"):
                continue
            # same page content → same memory (hash de-dup); log to Odoo only when the content changed
            if memory.remember_event(f"تواصل/{h['platform']}", h.get("partner_name", ""), h["link"][:100], h["excerpt"][:300],
                                     url=f"{h['link']}#{hash(h['excerpt'][:1500])}", source=h["platform"], importance=0.4) is None:
                continue
            try:
                self.odoo.log_event(h["partner_id"], f"[تواصل/{h['platform']}] {h['link'][:100]}", h["excerpt"][:400], as_activity=False)
                logged += 1
            except Exception as e:
                logger.warning("Failed to log social event for %s: %s", h.get("partner_id"), e)
        return {"active": True, "hits": len(hits), "logged": logged, "sample": hits[:5]}
