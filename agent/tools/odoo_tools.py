"""High-level tools the agent uses to read/write Odoo data."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from agent.odoo_client import OdooClient

logger = logging.getLogger(__name__)


class OdooTools:
    def __init__(self, client: Optional[OdooClient] = None):
        self.client = client or OdooClient()

    def list_monitored(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self.client.get_monitored_partners(limit=limit)

    def add_company(
        self,
        name: str,
        country: str = "Bahrain",
        website: str = "",
        comment: str = "",
        email: str = "",
        phone: str = "",
    ) -> Dict[str, Any]:
        existing = self.client.find_partner_by_name(name, is_company=True)
        if existing:
            return {"action": "exists", "partner": existing}

        vals: Dict[str, Any] = {
            "name": name,
            "is_company": True,
            "comment": (comment or "") + "\n[AI-MONITOR]\nCountry: " + country,
        }
        if website:
            vals["website"] = website
        if email:
            vals["email"] = email
        if phone:
            vals["phone"] = phone

        pid = self.client.create_partner(vals)
        partner = self.client.search_partners(domain=[("id", "=", pid)], limit=1)
        return {"action": "created", "id": pid, "partner": partner[0] if partner else None}

    def add_person(
        self,
        name: str,
        company_name: str = "",
        job_title: str = "",
        email: str = "",
        phone: str = "",
        comment: str = "",
    ) -> Dict[str, Any]:
        existing = self.client.find_partner_by_name(name, is_company=False)
        if existing:
            return {"action": "exists", "partner": existing}

        parent_id = False
        if company_name:
            company = self.client.find_partner_by_name(company_name, is_company=True)
            if company:
                parent_id = company["id"]

        vals: Dict[str, Any] = {
            "name": name,
            "is_company": False,
            "function": job_title,
            "comment": (comment or "") + "\n[AI-MONITOR]",
        }
        if parent_id:
            vals["parent_id"] = parent_id
        if email:
            vals["email"] = email
        if phone:
            vals["phone"] = phone

        pid = self.client.create_partner(vals)
        partner = self.client.search_partners(domain=[("id", "=", pid)], limit=1)
        return {"action": "created", "id": pid, "partner": partner[0] if partner else None}

    def update_partner(self, partner_id: int, **fields: Any) -> Dict[str, Any]:
        allowed = {"name", "email", "phone", "website", "comment", "function", "street", "city"}
        values = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not values:
            return {"ok": False, "error": "No valid fields to update"}
        ok = self.client.write_partner(partner_id, values)
        return {"ok": bool(ok), "id": partner_id, "updated": values}

    def log_event(self, partner_id: int, title: str, body: str, as_activity: bool = True) -> Dict[str, Any]:
        if as_activity:
            aid = self.client.create_activity(
                res_model="res.partner", res_id=partner_id, summary=title, note=body
            )
            return {"type": "activity", "id": aid}
        mid = self.client.post_message(
            "res.partner", partner_id, f"<p><b>{title}</b></p><p>{body}</p>"
        )
        return {"type": "message", "id": mid}

    def search_partners(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        return self.client.search_partners(
            domain=["|", ("name", "ilike", query), ("comment", "ilike", query)],
            limit=limit,
        )

    def health(self) -> Dict[str, Any]:
        return self.client.health_check()
