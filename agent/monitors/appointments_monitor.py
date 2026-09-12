"""Detect appointments, promotions, resignations related to monitored entities."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from agent.tools.odoo_tools import OdooTools
from agent.memory import get_memory
from agent.sources import SourceStore
from agent.tools.search_tools import SearchTools, entity_query

logger = logging.getLogger(__name__)

KEYWORDS_AR = ["تعيين", "ترقية", "استقالة", "منصب", "رئيس تنفيذي", "مدير عام", "عضو مجلس إدارة"]
KEYWORDS_EN = [
    "appointed",
    "appointment",
    "promotion",
    "promoted",
    "resignation",
    "resigns",
    "CEO",
    "board member",
    "named as",
]


class AppointmentsMonitor:
    def __init__(self, odoo: OdooTools | None = None, search: SearchTools | None = None):
        self.odoo = odoo or OdooTools()
        self.search = search or SearchTools()
        self.sources = SourceStore()

    def run(self, limit_entities: int = 40) -> List[Dict[str, Any]]:
        partners = self.odoo.list_monitored(limit=limit_entities)
        feeds = self.sources.enabled_feeds()
        search_feeds = self.sources.enabled_search()
        results: List[Dict[str, Any]] = []
        keys = KEYWORDS_AR + KEYWORDS_EN

        for p in partners:
            name = p.get("name") or ""
            if not name:
                continue
            hits = self.search.search_news_for_entity(name, feeds=feeds, keywords=keys, search_feeds=search_feeds, query=entity_query(p))
            filtered = self.search.filter_by_keywords(hits, keys)
            for h in filtered:
                h["partner_id"] = p.get("id")
                h["partner_name"] = name
                h["event_type"] = "appointment_or_change"
            results.extend(filtered)

        return results

    def run_and_log(self) -> Dict[str, Any]:
        events = self.run()
        memory = get_memory()
        new_events = [e for e in events if not memory.seen(e.get("link", ""))]
        logged = 0
        for e in new_events[:40]:
            pid = e.get("partner_id")
            if not pid:
                continue
            title = f"[تعيين/ترقية/استقالة] {e.get('title', '')[:100]}"
            body = f"{e.get('summary', '')[:500]}\n\nالرابط: {e.get('link', '')}"
            try:
                self.odoo.log_event(pid, title, body)
                logged += 1
            except Exception as ex:
                logger.warning("log failed: %s", ex)
            memory.remember_event("تعيين/ترقية/استقالة", e.get("partner_name", ""), e.get("title", ""), e.get("summary", ""), url=e.get("link", ""),
                                  source=e.get("feed_name", ""), importance=0.8, meta={"partner_id": pid, "published": e.get("published", "")})
        return {"events": len(events), "new": len(new_events), "already_known": len(events) - len(new_events), "logged": logged, "sample": new_events[:5]}
