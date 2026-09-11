"""Separate web interface for the AI Agent (runs inside LXC). FastAPI + HTML templates."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from agent.main import AIAgent
from agent.tools.odoo_tools import OdooTools
from agent.reports.weekly_report import WeeklyReportGenerator

app = FastAPI(title="Odoo AI Agent Monitor", version="1.1.0")
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


class PersonIn(BaseModel):
    name: str
    company_name: str = ""
    job_title: str = ""
    email: str = ""
    phone: str = ""


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    health = odoo_tools.health()
    partners = []
    try:
        partners = odoo_tools.list_monitored(limit=50)
    except Exception:
        pass
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "health": health, "partners": partners, "claude_ok": agent.claude.available()},
    )


@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    return templates.TemplateResponse("chat.html", {"request": request})


@app.post("/api/chat")
async def api_chat(body: ChatRequest):
    return {"reply": agent.handle(body.message)}


@app.get("/api/partners")
async def api_partners():
    try:
        return {"partners": odoo_tools.list_monitored(limit=100)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/companies")
async def api_add_company(body: CompanyIn):
    try:
        return odoo_tools.add_company(
            name=body.name, country=body.country, website=body.website,
            email=body.email, phone=body.phone,
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/people")
async def api_add_person(body: PersonIn):
    try:
        return odoo_tools.add_person(
            name=body.name, company_name=body.company_name, job_title=body.job_title,
            email=body.email, phone=body.phone,
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


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


@app.get("/api/health")
async def api_health():
    return {"odoo": odoo_tools.health(), "claude_cli": agent.claude.available()}


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    partners = []
    try:
        partners = odoo_tools.list_monitored(limit=80)
    except Exception:
        pass
    reports_dir = ROOT / "reports_output"
    reports = sorted(reports_dir.glob("*.md"), reverse=True)[:10] if reports_dir.exists() else []
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "partners": partners, "reports": [r.name for r in reports]},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("web.app:app", host=os.getenv("WEB_HOST", "0.0.0.0"), port=int(os.getenv("WEB_PORT", "8080")), reload=True)
