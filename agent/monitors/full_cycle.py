"""
Full monitoring cycle aligned with graduation plan:
  appointments / promotions / resignations / deals
  financial & strategic statements (news)
  legislation affecting company or employees
  optional social/LinkedIn
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from agent.tools.odoo_tools import OdooTools
from agent.monitors.news_monitor import NewsMonitor
from agent.monitors.appointments_monitor import AppointmentsMonitor
from agent.monitors.legislation_monitor import LegislationMonitor
from agent.monitors.social_monitor import SocialMonitor

logger = logging.getLogger(__name__)


def run_full_monitoring(odoo: Optional[OdooTools] = None, limit: int = 40) -> Dict[str, Any]:
    odoo = odoo or OdooTools()
    out: Dict[str, Any] = {"ok": True, "steps": {}}
    try:
        out["steps"]["news_deals_finance"] = NewsMonitor(odoo).run_and_log(limit_entities=limit)
    except Exception as e:
        logger.exception("news")
        out["steps"]["news_deals_finance"] = {"error": str(e)}
        out["ok"] = False
    try:
        out["steps"]["appointments_promotions"] = AppointmentsMonitor(odoo).run_and_log()
    except Exception as e:
        logger.exception("appointments")
        out["steps"]["appointments_promotions"] = {"error": str(e)}
        out["ok"] = False
    try:
        out["steps"]["legislation"] = LegislationMonitor(odoo).run_and_log()
    except Exception as e:
        logger.exception("legislation")
        out["steps"]["legislation"] = {"error": str(e)}
        out["ok"] = False
    try:
        out["steps"]["social_optional"] = SocialMonitor(odoo).run_and_log(limit_entities=limit)
    except Exception as e:
        out["steps"]["social_optional"] = {"error": str(e)}
    return out
