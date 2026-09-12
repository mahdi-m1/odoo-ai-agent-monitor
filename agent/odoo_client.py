"""Odoo external API client — JSON-2 first (Odoo 19 / 20+)."""

from __future__ import annotations

import logging
import os
import xmlrpc.client
from typing import Any, Dict, List, Optional, Union

import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class OdooClient:
    def __init__(
        self,
        url: Optional[str] = None,
        db: Optional[str] = None,
        api_key: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        protocol: Optional[str] = None,
        timeout: float = 60.0,
    ):
        self.url = (url or os.getenv("ODOO_URL", "")).rstrip("/")
        self.db = db or os.getenv("ODOO_DB", "")
        self.api_key = api_key or os.getenv("ODOO_API_KEY", "")
        self.username = username or os.getenv("ODOO_USER", "")
        self.password = password or os.getenv("ODOO_PASSWORD", "") or self.api_key
        self.protocol = (protocol or os.getenv("ODOO_PROTOCOL", "json2")).lower()
        self.timeout = timeout
        self.uid: Optional[int] = None
        self._http: Optional[httpx.Client] = None
        self._common: Optional[xmlrpc.client.ServerProxy] = None
        self._models: Optional[xmlrpc.client.ServerProxy] = None

    def connect(self) -> Any:
        if self.protocol == "json2":
            return self._connect_json2()
        return self._connect_xmlrpc()

    def ensure_connected(self) -> None:
        if self.protocol == "json2":
            if self._http is None:
                self._connect_json2()
        else:
            if self.uid is None or self._models is None:
                self._connect_xmlrpc()

    def _connect_json2(self) -> Dict[str, Any]:
        if not self.url:
            raise ValueError("ODOO_URL is required")
        if not self.api_key and not self.password:
            raise ValueError("JSON-2 requires ODOO_API_KEY")
        key = self.api_key or self.password
        headers = {
            "Authorization": f"bearer {key}",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "odoo-ai-agent-monitor/1.1",
        }
        if self.db:
            headers["X-Odoo-Database"] = self.db
        self._http = httpx.Client(base_url=self.url, headers=headers, timeout=self.timeout)
        try:
            ctx = self._json2("res.users", "context_get", {})
            self.uid = (ctx or {}).get("uid") if isinstance(ctx, dict) else None
            return {"ok": True, "protocol": "json2", "uid": self.uid, "context": ctx}
        except Exception as e:
            return {"ok": True, "protocol": "json2", "uid": None, "warning": str(e)}

    def _connect_xmlrpc(self) -> int:
        if not all([self.url, self.db, self.username, self.password]):
            raise ValueError("XML-RPC needs ODOO_URL, ODOO_DB, ODOO_USER, ODOO_PASSWORD")
        self._common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common")
        self.uid = self._common.authenticate(self.db, self.username, self.password, {})
        if not self.uid:
            raise ConnectionError("XML-RPC authentication failed")
        self._models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object")
        return self.uid

    def close(self) -> None:
        if self._http:
            self._http.close()
            self._http = None

    def _json2(self, model: str, method: str, payload: Dict[str, Any]) -> Any:
        self.ensure_connected()
        assert self._http is not None
        r = self._http.post(f"/json/2/{model}/{method}", json=payload)
        if r.status_code >= 400:
            raise RuntimeError(f"Odoo JSON-2 {r.status_code} {model}.{method}: {r.text[:800]}")
        if not r.content:
            return None
        try:
            return r.json()
        except Exception:
            return r.text

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        if self.protocol == "json2":
            return self._json2(model, method, self._map_args_to_json2(method, args, kwargs))
        self.ensure_connected()
        assert self._models is not None and self.uid is not None
        return self._models.execute_kw(self.db, self.uid, self.password, model, method, list(args), kwargs or {})

    @staticmethod
    def _map_args_to_json2(method: str, args: tuple, kwargs: dict) -> Dict[str, Any]:
        payload = dict(kwargs)
        if method in ("search", "search_count") and args:
            payload.setdefault("domain", args[0] if args else [])
        elif method == "search_read":
            if args:
                payload.setdefault("domain", args[0])
            if len(args) > 1:
                payload.setdefault("fields", args[1])
        elif method == "read" and args:
            payload.setdefault("ids", args[0] if isinstance(args[0], list) else [args[0]])
            if len(args) > 1:
                payload.setdefault("fields", args[1])
        elif method == "create" and args:
            v = args[0]
            payload.setdefault("vals_list", v if isinstance(v, list) else [v])
        elif method == "write" and args:
            ids = args[0]
            payload.setdefault("ids", ids if isinstance(ids, list) else [ids])
            if len(args) > 1:
                payload.setdefault("vals", args[1])
        elif method == "unlink" and args:
            ids = args[0]
            payload.setdefault("ids", ids if isinstance(ids, list) else [ids])
        return payload

    def search_partners(self, domain=None, fields=None, limit: int = 80) -> List[Dict[str, Any]]:
        domain = domain or []
        fields = fields or ["id", "name", "is_company", "email", "phone", "website", "country_id", "comment", "function", "parent_id", "category_id"]
        if self.protocol == "json2":
            return self._json2("res.partner", "search_read", {"domain": domain, "fields": fields, "limit": limit}) or []
        ids = self.execute("res.partner", "search", domain, limit=limit)
        return self.execute("res.partner", "read", ids, fields) if ids else []

    def get_monitored_partners(self, limit: int = 200) -> List[Dict[str, Any]]:
        domain = ["|", ("category_id.name", "ilike", "monitor"), ("comment", "ilike", "[AI-MONITOR]")]
        result = self.search_partners(domain=domain, limit=limit)
        return result or self.search_partners(domain=[("is_company", "=", True)], limit=min(50, limit))

    def create_partner(self, values: Dict[str, Any]) -> int:
        if self.protocol == "json2":
            res = self._json2("res.partner", "create", {"vals_list": [values]})
            if isinstance(res, list) and res:
                return int(res[0])
            if isinstance(res, int):
                return res
            raise RuntimeError(f"Unexpected create response: {res}")
        return self.execute("res.partner", "create", values)

    def write_partner(self, partner_id: int, values: Dict[str, Any]) -> bool:
        if self.protocol == "json2":
            return bool(self._json2("res.partner", "write", {"ids": [partner_id], "vals": values}))
        return self.execute("res.partner", "write", [partner_id], values)

    def find_partner_by_name(self, name: str, is_company: Optional[bool] = None) -> Optional[Dict]:
        domain: List = [("name", "ilike", name)]
        if is_company is not None:
            domain.append(("is_company", "=", is_company))
        rows = self.search_partners(domain=domain, limit=5)
        return rows[0] if rows else None

    def create_activity(self, res_model: str, res_id: int, summary: str, note: str = "", activity_type_id: int = 1) -> int:
        vals = {"res_model": res_model, "res_id": res_id, "summary": summary, "note": note, "activity_type_id": activity_type_id}
        try:
            if self.protocol == "json2":
                res = self._json2("mail.activity", "create", {"vals_list": [vals]})
                return int(res[0] if isinstance(res, list) else res)
            return self.execute("mail.activity", "create", vals)
        except Exception as e:
            logger.warning("activity failed: %s", e)
            return self.create_note(f"{summary}\n\n{note}")

    def create_note(self, memo: str, color: int = 4) -> int:
        try:
            if self.protocol == "json2":
                res = self._json2("note.note", "create", {"vals_list": [{"memo": memo, "color": color}]})
                return int(res[0] if isinstance(res, list) else res)
            return self.execute("note.note", "create", {"memo": memo, "color": color})
        except Exception:
            return -1

    def post_message(self, res_model: str, res_id: int, body: str) -> int:
        vals = {"model": res_model, "res_id": res_id, "body": body, "message_type": "comment", "subtype_id": 1}
        if self.protocol == "json2":
            res = self._json2("mail.message", "create", {"vals_list": [vals]})
            return int(res[0] if isinstance(res, list) else res)
        return self.execute("mail.message", "create", vals)

    def search_read(self, model: str, domain=None, fields=None, limit: int = 80) -> List[Dict[str, Any]]:
        domain = domain or []
        if self.protocol == "json2":
            return self._json2(model, "search_read", {"domain": domain, "fields": fields or [], "limit": limit}) or []
        ids = self.execute(model, "search", domain, limit=limit)
        return self.execute(model, "read", ids, fields or []) if ids else []

    def create(self, model: str, values: Dict[str, Any]) -> int:
        if self.protocol == "json2":
            res = self._json2(model, "create", {"vals_list": [values]})
            return int(res[0] if isinstance(res, list) else res)
        return self.execute(model, "create", values)

    def write(self, model: str, ids: Union[int, List[int]], values: Dict[str, Any]) -> bool:
        if isinstance(ids, int):
            ids = [ids]
        if self.protocol == "json2":
            return bool(self._json2(model, "write", {"ids": ids, "vals": values}))
        return self.execute(model, "write", ids, values)

    def unlink(self, model: str, ids: Union[int, List[int]]) -> bool:
        if isinstance(ids, int):
            ids = [ids]
        if self.protocol == "json2":
            return bool(self._json2(model, "unlink", {"ids": ids}))
        return self.execute(model, "unlink", ids)

    def health_check(self) -> Dict[str, Any]:
        try:
            info = self.connect()
            if self.protocol == "json2":
                return {"ok": True, "protocol": "json2", "uid": self.uid, "url": self.url, "db": self.db or "(from host)", "detail": info if isinstance(info, dict) else {}}
            version = self._common.version() if self._common else {}
            return {"ok": True, "protocol": "xmlrpc", "uid": self.uid, "version": version}
        except Exception as e:
            return {"ok": False, "protocol": self.protocol, "error": str(e)}
