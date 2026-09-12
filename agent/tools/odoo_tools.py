"""High-level Odoo tools for the AI agent (Odoo 20 Enterprise / JSON-2)."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from agent.odoo_client import OdooClient

logger = logging.getLogger(__name__)
MONITOR_TAG = "[AI-MONITOR]"


class OdooTools:
    def __init__(self, client: Optional[OdooClient] = None):
        self.client = client or OdooClient()

    def health(self) -> Dict[str, Any]:
        try:
            return self.client.health_check()
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def list_monitored(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self.client.get_monitored_partners(limit=limit)

    def list_monitoring_tree(self, limit: int = 100) -> Dict[str, Any]:
        partners = self.list_monitored(limit=limit)
        companies = [p for p in partners if p.get("is_company")]
        people = [p for p in partners if not p.get("is_company")]
        by_parent: Dict[int, List[Dict]] = {}
        orphans: List[Dict] = []
        for pe in people:
            parent = pe.get("parent_id")
            pid = parent[0] if isinstance(parent, (list, tuple)) else parent
            if pid:
                by_parent.setdefault(int(pid), []).append(pe)
            else:
                orphans.append(pe)
        tree = []
        for c in companies:
            kids = by_parent.get(int(c["id"]), [])
            tree.append({
                "company": c,
                "people": kids,
                "people_count": len(kids),
                "monitoring_ready": len(kids) > 0,
            })
        return {"companies": tree, "orphan_people": orphans, "total_partners": len(partners)}

    def list_company_people(self, company_name: str = "", company_id: Optional[int] = None) -> List[Dict[str, Any]]:
        if company_id:
            domain = [("parent_id", "=", company_id), ("is_company", "=", False)]
        elif company_name:
            company = self.client.find_partner_by_name(company_name, is_company=True)
            if not company:
                return []
            domain = [("parent_id", "=", company["id"]), ("is_company", "=", False)]
        else:
            return []
        return self.client.search_partners(domain=domain, limit=100)

    def add_company(
        self, name: str, country: str = "Bahrain", website: str = "",
        comment: str = "", email: str = "", phone: str = "",
        people: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        existing = self.client.find_partner_by_name(name, is_company=True)
        if existing:
            company_result = {"action": "exists", "partner": existing, "id": existing.get("id")}
            company_id = existing.get("id")
        else:
            vals: Dict[str, Any] = {
                "name": name, "is_company": True,
                "comment": f"{comment or ''}\n{MONITOR_TAG}\nCountry: {country}".strip(),
            }
            if website:
                vals["website"] = website
            if email:
                vals["email"] = email
            if phone:
                vals["phone"] = phone
            company_id = self.client.create_partner(vals)
            partner = self.client.search_partners(domain=[("id", "=", company_id)], limit=1)
            company_result = {"action": "created", "id": company_id, "partner": partner[0] if partner else None}

        people_results = []
        normalized = self._normalize_people_list(people or [])
        for person in normalized:
            people_results.append(self.add_person(
                name=person["name"], company_name=name,
                job_title=person.get("job_title", ""),
                email=person.get("email", ""), phone=person.get("phone", ""),
                comment=person.get("comment", ""),
            ))
        incomplete = len(normalized) == 0 and not self.list_company_people(company_id=company_id)
        return {
            "company": company_result, "people": people_results,
            "people_count": len(people_results), "monitoring_ready": not incomplete,
            "warning": (
                "الشركة بدون شخصيات. أضف شخصيات لمراقبة التعيينات/الترقيات."
                if incomplete else None
            ),
        }

    def add_person(
        self, name: str, company_name: str = "", job_title: str = "",
        email: str = "", phone: str = "", comment: str = "",
        company_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        parent_id = company_id
        if not parent_id and company_name:
            company = self.client.find_partner_by_name(company_name, is_company=True)
            if company:
                parent_id = company["id"]
            else:
                created = self.add_company(name=company_name, people=[])
                parent_id = created["company"]["id"]
        existing = self.client.find_partner_by_name(name, is_company=False)
        if existing and parent_id:
            ep = existing.get("parent_id")
            eid = ep[0] if isinstance(ep, (list, tuple)) else ep
            if eid and int(eid) == int(parent_id):
                return {"action": "exists", "id": existing["id"], "partner": existing}
        vals: Dict[str, Any] = {
            "name": name, "is_company": False,
            "comment": f"{comment or ''}\n{MONITOR_TAG}".strip(),
        }
        if parent_id:
            vals["parent_id"] = int(parent_id)
        if job_title:
            vals["function"] = job_title
        if email:
            vals["email"] = email
        if phone:
            vals["phone"] = phone
        pid = self.client.create_partner(vals)
        partner = self.client.search_partners(domain=[("id", "=", pid)], limit=1)
        return {"action": "created", "id": pid, "partner": partner[0] if partner else None}

    @staticmethod
    def _normalize_people_list(people: List[Any]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for item in people:
            if not item:
                continue
            if isinstance(item, str):
                line = item.strip()
                if not line:
                    continue
                if "|" in line:
                    parts = [p.strip() for p in line.split("|", 1)]
                elif "،" in line:
                    parts = [p.strip() for p in line.split("،", 1)]
                elif "," in line:
                    parts = [p.strip() for p in line.split(",", 1)]
                else:
                    parts = [line]
                out.append({"name": parts[0], "job_title": parts[1] if len(parts) > 1 else ""})
            elif isinstance(item, dict) and item.get("name"):
                out.append({
                    "name": str(item["name"]).strip(),
                    "job_title": str(item.get("job_title") or item.get("function") or "").strip(),
                    "email": str(item.get("email") or "").strip(),
                    "phone": str(item.get("phone") or "").strip(),
                    "comment": str(item.get("comment") or "").strip(),
                })
        return out

    def update_partner(self, partner_id: int, **fields: Any) -> Dict[str, Any]:
        allowed = {"name", "email", "phone", "website", "comment", "function", "parent_id", "is_company", "street", "city"}
        vals = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not vals:
            return {"ok": False, "error": "لا توجد حقول مسموحة للتحديث"}
        if "comment" in vals and MONITOR_TAG not in str(vals["comment"]):
            vals["comment"] = f"{vals['comment']}\n{MONITOR_TAG}"
        ok = self.client.write_partner(int(partner_id), vals)
        rows = self.client.search_partners(domain=[("id", "=", int(partner_id))], limit=1)
        return {"ok": bool(ok), "id": partner_id, "partner": rows[0] if rows else None, "written": vals}

    def remove_from_monitor(self, partner_id: int, confirm: bool = False) -> Dict[str, Any]:
        if not confirm:
            return {"ok": False, "need_confirm": True, "message": "يحتاج تأكيد"}
        rows = self.client.search_partners(domain=[("id", "=", int(partner_id))], limit=1)
        if not rows:
            return {"ok": False, "error": "غير موجود"}
        comment = str(rows[0].get("comment") or "").replace(MONITOR_TAG, "").strip()
        self.client.write_partner(int(partner_id), {"comment": comment or " "})
        return {"ok": True, "action": "removed_from_monitor", "id": partner_id}

    def delete_partner(self, partner_id: int, confirm: bool = False, cascade_people: bool = False) -> Dict[str, Any]:
        if not confirm:
            return {"ok": False, "need_confirm": True, "message": "الحذف النهائي يحتاج تأكيد"}
        pid = int(partner_id)
        rows = self.client.search_partners(domain=[("id", "=", pid)], limit=1)
        if not rows:
            return {"ok": False, "error": "غير موجود"}
        deleted_people = []
        if cascade_people and rows[0].get("is_company"):
            for k in self.list_company_people(company_id=pid):
                try:
                    self.client.unlink("res.partner", k["id"])
                    deleted_people.append(k["id"])
                except Exception as e:
                    logger.warning("cascade: %s", e)
        ok = self.client.unlink("res.partner", pid)
        return {"ok": bool(ok), "action": "deleted", "id": pid, "deleted_people": deleted_people}

    def log_event(self, partner_id: int, summary: str, body: str = "", as_activity: bool = True) -> Dict[str, Any]:
        results = {}
        try:
            if as_activity:
                results["activity_id"] = self.client.create_activity("res.partner", int(partner_id), summary, body)
            results["message_id"] = self.client.post_message("res.partner", int(partner_id), f"<b>{summary}</b><br/>{body}")
        except Exception as e:
            results["error"] = str(e)
        return results
