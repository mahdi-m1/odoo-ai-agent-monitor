"""Optional social / LinkedIn monitor (disabled by default).

Activated via env:
  SOCIAL_ENABLED=true        -> fetch public social links stored on partners ([SOCIAL] in comment)
  SOCIAL_PUBLIC_FETCH=true   -> allow fetching public pages
  LINKEDIN_ENABLED=true + LINKEDIN_ACCESS_TOKEN=... -> reserved for official API integration
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List

from agent.tools.odoo_tools import OdooTools
from agent.tools.search_tools import SearchTools

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s<>\"']+")


def _flag(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in ("1", "true", "yes", "on")


class SocialMonitor:
    def __init__(self, odoo: OdooTools | None = None, search: SearchTools | None = None):
        self.odoo = odoo or OdooTools()
        self.search = search or SearchTools()
        self.social_enabled = _flag("SOCIAL_ENABLED")
        self.public_fetch = _flag("SOCIAL_PUBLIC_FETCH")
        self.linkedin_enabled = _flag("LINKEDIN_ENABLED") and bool(os.getenv("LINKEDIN_ACCESS_TOKEN"))
        self.active = self.social_enabled or self.linkedin_enabled

    @staticmethod
    def _social_links(partner: Dict[str, Any]) -> List[str]:
        text = " ".join(str(partner.get(k) or "") for k in ("comment", "website"))
        links = _URL_RE.findall(text)
        return [l for l in links if any(d in l for d in ("linkedin.com", "x.com", "twitter.com", "instagram.com"))]

    def run(self, limit_entities: int = 40) -> List[Dict[str, Any]]:
        if not self.active:
            logger.info("SocialMonitor disabled (SOCIAL_ENABLED / LINKEDIN_ENABLED not set)")
            return []
        hits: List[Dict[str, Any]] = []
        for p in self.odoo.list_monitored(limit=limit_entities):
            for link in self._social_links(p):
                item = {"partner_id": p.get("id"), "partner_name": p.get("name"), "link": link}
                if self.public_fetch:
                    item["excerpt"] = self.search.fetch_page_text(link, max_chars=1500)
                hits.append(item)
        return hits

    def run_and_log(self, limit_entities: int = 40) -> Dict[str, Any]:
        if not self.active:
            return {"active": False, "hits": 0, "logged": 0, "note": "معطّل — فعّل SOCIAL_ENABLED أو LINKEDIN_ENABLED"}
        hits = self.run(limit_entities=limit_entities)
        logged = 0
        for h in hits[:50]:
            if not h.get("excerpt"):
                continue
            try:
                self.odoo.log_event(h["partner_id"], f"[تواصل] {h['link'][:100]}", h["excerpt"][:400], as_activity=False)
                logged += 1
            except Exception as e:
                logger.warning("Failed to log social event for %s: %s", h.get("partner_id"), e)
        return {"active": True, "hits": len(hits), "logged": logged, "sample": hits[:5]}
