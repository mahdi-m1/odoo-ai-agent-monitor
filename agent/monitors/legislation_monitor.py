"""Monitor legislation / official decisions that may affect monitored entities."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

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
    def __init__(self, search: SearchTools | None = None):
        self.search = search or SearchTools()

    def scan_sources(self) -> List[Dict[str, Any]]:
        findings: List[Dict[str, Any]] = []
        for src in LEGISLATION_SOURCES:
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
