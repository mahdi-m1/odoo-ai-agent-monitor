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

from agent.main import AIAgent
from agent.tools.odoo_tools import OdooTools
from agent.reports.weekly_report import WeeklyReportGenerator
from agent.monitors.full_cycle import run_full_monitoring
from agent.smoke_test import run_smoke

app = FastAPI(title="Odoo AI Agent Monitor", version="1.2.0")
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


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    health = odoo_tools.health()
    tree = {"companies": [], "orphan_people": []}
    try:
        tree = odoo_tools.list_monitoring_tree()
    except Exception:
        pass
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "health": health, "tree": tree, "claude_ok": agent.claude.available()},
    )


@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    return templates.TemplateResponse("chat.html", {"request": request})


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    tree = {"companies": []}
    try:
        tree = odoo_tools.list_monitoring_tree()
    except Exception:
        pass
    reports_dir = ROOT / "reports_output"
    reports = sorted(reports_dir.glob("*.md"), reverse=True)[:15] if reports_dir.exists() else []
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "tree": tree, "reports": [r.name for r in reports], "schedule": agent._read_schedule()},
    )


@app.post("/api/chat")
async def api_chat(body: ChatRequest):
    return {"reply": agent.handle(body.message)}


@app.get("/api/partners")
async def api_partners():
    try:
        return odoo_tools.list_monitoring_tree()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/companies")
async def api_add_company(body: CompanyIn):
    try:
        return odoo_tools.add_company(
            name=body.name, country=body.country, website=body.website,
            email=body.email, phone=body.phone, people=body.people or [],
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/people")
async def api_add_person(body: PersonIn):
    try:
        return odoo_tools.add_person(
            name=body.name, company_name=body.company_name, company_id=body.company_id,
            job_title=body.job_title, email=body.email, phone=body.phone,
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.patch("/api/partners/{partner_id}")
async def api_update_partner(partner_id: int, body: PartnerUpdate):
    try:
        return odoo_tools.update_partner(partner_id, **body.model_dump(exclude_none=True))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/partners/{partner_id}/remove-monitor")
async def api_remove_monitor(partner_id: int, body: ConfirmAction):
    return odoo_tools.remove_from_monitor(partner_id, confirm=body.confirm)


@app.post("/api/partners/{partner_id}/delete")
async def api_delete_partner(partner_id: int, body: ConfirmAction):
    return odoo_tools.delete_partner(partner_id, confirm=body.confirm, cascade_people=body.cascade_people)


@app.post("/api/report")
async def api_report(focus: str = Form(default="")):
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
async def api_scan():
    try:
        return run_full_monitoring(odoo_tools)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/smoke")
async def api_smoke(keep: bool = False):
    report = run_smoke(keep_records=keep, skip_network_search=True)
    return report.to_dict()


@app.get("/api/health")
async def api_health():
    return {"odoo": odoo_tools.health(), "claude_cli": agent.claude.available(), "schedule": agent._read_schedule()}


@app.get("/api/schedule")
async def api_get_schedule():
    return agent._read_schedule()


@app.post("/api/schedule")
async def api_set_schedule(day: str = Form(None), hour: int = Form(None), daily_hour: int = Form(None)):
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
