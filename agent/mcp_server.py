"""
MCP server exposing Odoo AI Agent tools to Claude / other AI clients.

Run:
  python -m agent.mcp_server

Requires: pip install mcp
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp_odoo_agent")


def _tools_impl():
    from agent.tools.odoo_tools import OdooTools
    from agent.reports.weekly_report import WeeklyReportGenerator
    from agent.monitors.news_monitor import NewsMonitor
    from agent.monitors.appointments_monitor import AppointmentsMonitor

    odoo = OdooTools()

    def list_monitored(limit: int = 50) -> str:
        return json.dumps(odoo.list_monitored(limit=limit), ensure_ascii=False, default=str)

    def add_company(name: str, country: str = "Bahrain", website: str = "") -> str:
        return json.dumps(odoo.add_company(name=name, country=country, website=website), ensure_ascii=False, default=str)

    def add_person(name: str, company_name: str = "", job_title: str = "") -> str:
        return json.dumps(odoo.add_person(name=name, company_name=company_name, job_title=job_title), ensure_ascii=False, default=str)

    def update_partner(partner_id: int, **fields) -> str:
        return json.dumps(odoo.update_partner(partner_id, **fields), ensure_ascii=False, default=str)

    def search(query: str, limit: int = 20) -> str:
        return json.dumps(odoo.search_partners(query, limit=limit), ensure_ascii=False, default=str)

    def health() -> str:
        return json.dumps(odoo.health(), ensure_ascii=False, default=str)

    def weekly_report(focus: str = "") -> str:
        gen = WeeklyReportGenerator(odoo=odoo)
        result = gen.run_full_cycle(custom_focus=focus)
        path = result.get("path")
        text = ""
        if path and Path(path).exists():
            text = Path(path).read_text(encoding="utf-8")[:4000]
        return json.dumps({"meta": result, "report_preview": text}, ensure_ascii=False)

    def scan_news() -> str:
        return json.dumps(NewsMonitor(odoo).run_and_log(), ensure_ascii=False, default=str)

    def scan_appointments() -> str:
        return json.dumps(AppointmentsMonitor(odoo).run_and_log(), ensure_ascii=False, default=str)

    return {
        "list_monitored": list_monitored,
        "add_company": add_company,
        "add_person": add_person,
        "update_partner": update_partner,
        "search": search,
        "health": health,
        "weekly_report": weekly_report,
        "scan_news": scan_news,
        "scan_appointments": scan_appointments,
    }


def main():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        print("Install MCP SDK: pip install mcp", file=sys.stderr)
        sys.exit(1)

    mcp = FastMCP("odoo-ai-agent-monitor")
    impl = _tools_impl()

    @mcp.tool()
    def odoo_list_monitored(limit: int = 50) -> str:
        """List companies and people marked for AI monitoring in Odoo."""
        return impl["list_monitored"](limit)

    @mcp.tool()
    def odoo_add_company(name: str, country: str = "Bahrain", website: str = "") -> str:
        """Add a company to Odoo monitoring list."""
        return impl["add_company"](name, country, website)

    @mcp.tool()
    def odoo_add_person(name: str, company_name: str = "", job_title: str = "") -> str:
        """Add a person linked to a company for monitoring."""
        return impl["add_person"](name, company_name, job_title)

    @mcp.tool()
    def odoo_update_partner(partner_id: int, email: str = "", phone: str = "", website: str = "") -> str:
        """Update partner fields in Odoo by id."""
        fields = {k: v for k, v in {"email": email, "phone": phone, "website": website}.items() if v}
        return impl["update_partner"](partner_id, **fields)

    @mcp.tool()
    def odoo_search(query: str, limit: int = 20) -> str:
        """Search partners by name or comment."""
        return impl["search"](query, limit)

    @mcp.tool()
    def odoo_health() -> str:
        """Check Odoo JSON-2 connection health."""
        return impl["health"]()

    @mcp.tool()
    def agent_weekly_report(focus: str = "") -> str:
        """Generate weekly monitoring report."""
        return impl["weekly_report"](focus)

    @mcp.tool()
    def agent_scan_news() -> str:
        """Scan news feeds for monitored entities."""
        return impl["scan_news"]()

    @mcp.tool()
    def agent_scan_appointments() -> str:
        """Scan appointments/promotions/resignations."""
        return impl["scan_appointments"]()

    logger.info("Starting MCP server: odoo-ai-agent-monitor")
    mcp.run()


if __name__ == "__main__":
    main()
