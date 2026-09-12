"""Web UI + API for the AI Agent (LXC)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")
sf = ROOT / "config" / "schedule.env"
if sf.exists():
    load_dotenv(sf, override=True)

from agent import agent_settings
from agent.main import AIAgent
from agent.monitors.social_monitor import verify_linkedin_token
from agent.sources import SOURCE_TYPES, discover_feeds
from agent.tools.odoo_tools import OdooTools
from agent.reports.weekly_report import WeeklyReportGenerator
from agent.monitors.full_cycle import run_full_monitoring
from agent.smoke_test import run_smoke

app = FastAPI(title="Odoo AI Agent Monitor", version="1.3.0")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

agent = AIAgent()
odoo_tools = OdooTools()


class ChatRequest(BaseModel):
    message: str


class CompanyIn(BaseModel):
    name: str
    country: str = "Bahrain"
    website: str = ""
    email: str = ""
    phone: str = ""
    people: List[str] = Field(default_factory=list)


class PersonIn(BaseModel):
    name: str
    company_name: str = ""
    company_id: Optional[int] = None
    job_title: str = ""
    email: str = ""
    phone: str = ""


class PartnerUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    function: Optional[str] = None
    comment: Optional[str] = None


class ConfirmAction(BaseModel):
    confirm: bool = False
    cascade_people: bool = False


class SourceIn(BaseModel):
    name: str = ""
    url: str
    type: str = "rss"
    region: str = ""
    enabled: bool = True


class SourceUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    type: Optional[str] = None
    region: Optional[str] = None
    enabled: Optional[bool] = None


class DiscoverIn(BaseModel):
    url: str


class ModelIn(BaseModel):
    model: str = ""
    effort: Optional[str] = None


class SocialIn(BaseModel):
    social_enabled: Optional[bool] = None
    public_fetch: Optional[bool] = None
    linkedin_enabled: Optional[bool] = None
    linkedin_token: Optional[str] = None
    platforms: Optional[List[str]] = None


class LinksIn(BaseModel):
    links: List[str] = Field(default_factory=list)


def _social_public() -> dict:
    cfg = agent_settings.get_social()
    return {**{k: v for k, v in cfg.items() if k != "linkedin_token"}, "linkedin_token_set": bool(cfg["linkedin_token"])}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    health = odoo_tools.health()
    tree = {"companies": [], "orphan_people": []}
    try:
        tree = odoo_tools.list_monitoring_tree()
    except Exception:
        pass
    return templates.TemplateResponse(
        request,
        "index.html",
        {"request": request, "health": health, "tree": tree, "claude_ok": agent.claude.available(), "claude": agent.claude.status(),
         "sources_total": len(agent.sources.list()), "sources_enabled": len(agent.sources.list(enabled=True)), "social": _social_public()},
    )


@app.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request):
    return templates.TemplateResponse(request, "chat.html", {"request": request, "models": agent_settings.MODELS, "efforts": agent_settings.EFFORTS})


@app.get("/sources", response_class=HTMLResponse)
def sources_page(request: Request):
    tree = {"companies": [], "orphan_people": []}
    try:
        tree = odoo_tools.list_monitoring_tree()
    except Exception:
        pass
    return templates.TemplateResponse(
        request,
        "sources.html",
        {"request": request, "sources": agent.sources.list(), "types": SOURCE_TYPES, "social": _social_public(),
         "platforms": agent_settings.SOCIAL_PLATFORMS, "tree": tree, "get_links": odoo_tools.get_social_links},
    )


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    tree = {"companies": []}
    try:
        tree = odoo_tools.list_monitoring_tree()
    except Exception:
        pass
    reports_dir = ROOT / "reports_output"
    reports = sorted(reports_dir.glob("*.md"), reverse=True)[:15] if reports_dir.exists() else []
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"request": request, "tree": tree, "reports": [r.name for r in reports], "schedule": agent._read_schedule()},
    )


@app.post("/api/chat")
def api_chat(body: ChatRequest):
    return {"reply": agent.handle(body.message), "claude": agent.claude.last, "model": agent.claude.model}


@app.get("/api/chat/history")
def api_chat_history():
    return {"history": agent.history, "session_id": agent.claude.session_id}


@app.post("/api/chat/reset")
def api_chat_reset():
    agent.claude.reset_session()
    agent.history.clear()
    return {"ok": True}


# ---- agent status / model ----
@app.get("/api/agent/status")
def api_agent_status():
    return agent.status()


@app.post("/api/agent/model")
def api_agent_model(body: ModelIn):
    try:
        out = {"model": agent.claude.set_model(body.model)}
        if body.effort is not None:
            out["effort"] = agent_settings.set_effort(body.effort)
        return out
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# ---- sources / channels ----
@app.get("/api/sources")
def api_sources(type: Optional[str] = None, enabled: Optional[bool] = None):
    return {"sources": agent.sources.list(type=type, enabled=enabled)}


@app.post("/api/sources")
def api_add_source(body: SourceIn):
    try:
        row = agent.sources.add(body.name, body.url, body.type, body.region, body.enabled)
        return {"source": row, "test": agent.sources.test(row["id"])}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.patch("/api/sources/{source_id}")
def api_update_source(source_id: int, body: SourceUpdate):
    try:
        return agent.sources.update(source_id, **body.model_dump(exclude_none=True))
    except (KeyError, ValueError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.delete("/api/sources/{source_id}")
def api_delete_source(source_id: int):
    return {"ok": agent.sources.remove(source_id)}


@app.post("/api/sources/{source_id}/test")
def api_test_source(source_id: int):
    try:
        return agent.sources.test(source_id)
    except KeyError as e:
        return JSONResponse({"error": str(e)}, status_code=404)


@app.post("/api/sources/test-all")
def api_test_all_sources():
    return {"results": agent.sources.test_all()}


@app.post("/api/sources/discover")
def api_discover(body: DiscoverIn):
    return discover_feeds(body.url)


@app.post("/api/sources/reset")
def api_reset_sources():
    return {"sources": agent.sources.reset_defaults()}


# ---- social channels ----
@app.get("/api/social")
def api_social():
    return _social_public()


@app.post("/api/social")
def api_social_set(body: SocialIn):
    changes = body.model_dump(exclude_none=True)
    verify = None
    if changes.get("linkedin_token"):
        # Only enable the LinkedIn API when the token actually authenticates.
        verify = verify_linkedin_token(changes["linkedin_token"])
        changes["linkedin_enabled"] = bool(verify["ok"]) and changes.get("linkedin_enabled", True)
    elif changes.get("linkedin_enabled") and not agent_settings.get_social()["linkedin_token"]:
        changes["linkedin_enabled"] = False
        verify = {"ok": False, "error": "لا يوجد LinkedIn token محفوظ"}
    agent_settings.set_social(**changes)
    return {**_social_public(), "linkedin_verify": verify}


@app.post("/api/social/linkedin/verify")
def api_linkedin_verify():
    return verify_linkedin_token(agent_settings.get_social()["linkedin_token"])


@app.get("/api/partners/{partner_id}/social-links")
def api_get_links(partner_id: int):
    return {"id": partner_id, "links": odoo_tools.list_social_links(partner_id)}


@app.put("/api/partners/{partner_id}/social-links")
def api_set_links(partner_id: int, body: LinksIn):
    return odoo_tools.set_social_links(partner_id, body.links)


@app.get("/api/partners")
def api_partners():
    try:
        return odoo_tools.list_monitoring_tree()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/companies")
def api_add_company(body: CompanyIn):
    try:
        return odoo_tools.add_company(
            name=body.name, country=body.country, website=body.website,
            email=body.email, phone=body.phone, people=body.people or [],
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/people")
def api_add_person(body: PersonIn):
    try:
        return odoo_tools.add_person(
            name=body.name, company_name=body.company_name, company_id=body.company_id,
            job_title=body.job_title, email=body.email, phone=body.phone,
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.patch("/api/partners/{partner_id}")
def api_update_partner(partner_id: int, body: PartnerUpdate):
    try:
        return odoo_tools.update_partner(partner_id, **body.model_dump(exclude_none=True))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/partners/{partner_id}/remove-monitor")
def api_remove_monitor(partner_id: int, body: ConfirmAction):
    return odoo_tools.remove_from_monitor(partner_id, confirm=body.confirm)


@app.post("/api/partners/{partner_id}/delete")
def api_delete_partner(partner_id: int, body: ConfirmAction):
    return odoo_tools.delete_partner(partner_id, confirm=body.confirm, cascade_people=body.cascade_people)


@app.post("/api/report")
def api_report(focus: str = Form(default="")):
    try:
        gen = WeeklyReportGenerator(odoo=odoo_tools, claude=agent.claude)
        result = gen.run_full_cycle(custom_focus=focus)
        text = ""
        path = result.get("path")
        if path and Path(path).exists():
            text = Path(path).read_text(encoding="utf-8")
        return {"result": result, "report": text}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/scan")
def api_scan():
    try:
        return run_full_monitoring(odoo_tools)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/smoke")
def api_smoke(keep: bool = False):
    report = run_smoke(keep_records=keep, skip_network_search=True)
    return report.to_dict()


@app.get("/api/health")
def api_health():
    return {"odoo": odoo_tools.health(), "claude_cli": agent.claude.available(), "claude": agent.claude.status(), "schedule": agent._read_schedule()}


@app.get("/api/schedule")
def api_get_schedule():
    return agent._read_schedule()


@app.post("/api/schedule")
def api_set_schedule(day: str = Form(None), hour: int = Form(None), daily_hour: int = Form(None)):
    parts = ["جدول"]
    if day:
        parts.append(f"يوم={day}")
    if hour is not None:
        parts.append(f"ساعة={hour}")
    if daily_hour is not None:
        parts.append(f"مسح={daily_hour}")
    return {"reply": agent.handle(" ".join(parts)), "schedule": agent._read_schedule()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("web.app:app", host=os.getenv("WEB_HOST", "0.0.0.0"), port=int(os.getenv("WEB_PORT", "8080")), reload=True)
