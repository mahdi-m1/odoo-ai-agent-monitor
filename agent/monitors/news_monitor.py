"""Monitor news feeds for monitored companies and people."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from agent.tools.odoo_tools import OdooTools
from agent.sources import SourceStore
from agent.tools.search_tools import SearchTools, entity_query

logger = logging.getLogger(__name__)


class NewsMonitor:
    def __init__(self, odoo: OdooTools | None = None, search: SearchTools | None = None):
        self.odoo = odoo or OdooTools()
        self.search = search or SearchTools()
        self.sources = SourceStore()

    def run(self, limit_entities: int = 40) -> List[Dict[str, Any]]:
        partners = self.odoo.list_monitored(limit=limit_entities)
        feeds = self.sources.enabled_feeds()
        search_feeds = self.sources.enabled_search()
        all_hits: List[Dict[str, Any]] = []

        for p in partners:
            name = p.get("name") or ""
            if not name:
                continue
            hits = self.search.search_news_for_entity(name, feeds=feeds, search_feeds=search_feeds, query=entity_query(p))
            for h in hits:
                h["partner_id"] = p.get("id")
                h["partner_name"] = name
                h["is_company"] = p.get("is_company")
            all_hits.extend(hits)
            logger.info("News for %s: %d hits", name, len(hits))

        return all_hits

    def run_and_log(self, limit_entities: int = 30) -> Dict[str, Any]:
        hits = self.run(limit_entities=limit_entities)
        logged = 0
        for h in hits[:50]:
            pid = h.get("partner_id")
            if not pid:
                continue
            title = f"[خبر] {h.get('title', '')[:120]}"
            body = (
                f"المصدر: {h.get('feed_name', '')}\n"
                f"الرابط: {h.get('link', '')}\n"
                f"ملخص: {h.get('summary', '')[:400]}"
            )
            try:
                self.odoo.log_event(pid, title, body, as_activity=True)
                logged += 1
            except Exception as e:
                logger.warning("Failed to log event for partner %s: %s", pid, e)
        return {"hits": len(hits), "logged": logged, "channels_used": len(self.sources.enabled_feeds()) + len(self.sources.enabled_search()), "sample": hits[:5]}
