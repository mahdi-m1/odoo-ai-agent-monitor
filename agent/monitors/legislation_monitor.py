"""Monitor legislation / official decisions that may affect monitored entities."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from agent.sources import SourceStore
from agent.tools.odoo_tools import OdooTools
from agent.tools.search_tools import SearchTools

logger = logging.getLogger(__name__)

LEGISLATION_SOURCES = [
    {
        "name": "Bahrain LLOC",
        "url": "https://www.lloc.gov.bh",
        "region": "Bahrain",
        "notes": "Legislation & Legal Opinion Commission",
    },
    {
        "name": "Saudi Official Gazette (Um Al-Qura)",
        "url": "https://uqn.gov.sa",
        "region": "Saudi Arabia",
    },
]


class LegislationMonitor:
    def __init__(self, odoo: OdooTools | None = None, search: SearchTools | None = None):
        self.odoo = odoo or OdooTools()
        self.search = search or SearchTools()
        self.sources = SourceStore()

    def scan_sources(self) -> List[Dict[str, Any]]:
        findings: List[Dict[str, Any]] = []
        for src in self.sources.enabled_legislation() or LEGISLATION_SOURCES:
            text = self.search.fetch_page_text(src["url"], max_chars=4000)
            if text:
                findings.append(
                    {
                        "source": src["name"],
                        "region": src.get("region"),
                        "url": src["url"],
                        "excerpt": text[:1500],
                        "fetched_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
                    }
                )
            else:
                logger.info("No text from %s", src["name"])
        return findings

    def match_entities(self, findings: List[Dict], entity_names: List[str]) -> List[Dict]:
        matched = []
        lower_names = [n.lower() for n in entity_names if n]
        for f in findings:
            excerpt_l = (f.get("excerpt") or "").lower()
            for name, raw in zip(lower_names, entity_names):
                if name and name in excerpt_l:
                    item = dict(f)
                    item["matched_entity"] = raw
                    matched.append(item)
        return matched

    def run_and_log(self, limit_entities: int = 100) -> Dict[str, Any]:
        partners = self.odoo.list_monitored(limit=limit_entities)
        findings = self.scan_sources()
        matched = self.match_entities(findings, [p.get("name") or "" for p in partners])
        by_name = {p.get("name"): p.get("id") for p in partners}
        logged = 0
        for m in matched[:50]:
            pid = by_name.get(m.get("matched_entity"))
            if not pid:
                continue
            try:
                self.odoo.log_event(
                    pid,
                    f"[تشريع] {m.get('source', '')} — {m.get('region', '')}",
                    f"الرابط: {m.get('url', '')}\nمقتطف: {(m.get('excerpt') or '')[:400]}",
                    as_activity=True,
                )
                logged += 1
            except Exception as e:
                logger.warning("Failed to log legislation for partner %s: %s", pid, e)
        return {"sources": len(findings), "matched": len(matched), "logged": logged, "sample": matched[:5]}
