"""Web UI + API for the AI Agent (LXC)."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
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
from agent import backup as backup_mod
from agent import memory as memory_mod
from agent import push as push_mod
from agent.gdrive import GDrive, GDriveError, save_credentials, unlink_drive
from agent.tools.browser import get_renderer
from agent.main import AIAgent
from agent.monitors.social_monitor import verify_linkedin_token
from agent.sources import SOURCE_TYPES, discover_feeds
from agent.tools.odoo_tools import OdooTools
from agent.reports.weekly_report import WeeklyReportGenerator
from agent.reports import store as report_store
from agent.reports import export as report_export
from agent.monitors.full_cycle import run_full_monitoring
from agent.smoke_test import run_smoke

app = FastAPI(title="Odoo AI Agent Monitor", version="1.4.0")


@app.middleware("http")
async def _no_cache_html(request, call_next):
    resp = await call_next(request)
    # HTML pages carry inline JS; never let the browser serve a stale copy.
    if resp.headers.get("content-type", "").startswith("text/html"):
        resp.headers["Cache-Control"] = "no-store, must-revalidate"
    return resp
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

agent = AIAgent()
odoo_tools = OdooTools()
agent.memory.embedder.warmup_async()  # load the embedding model in the background so the first chat isn't slow


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
    render_js: bool = False


class SourceUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    type: Optional[str] = None
    region: Optional[str] = None
    enabled: Optional[bool] = None
    render_js: Optional[bool] = None


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


class MemoryIn(BaseModel):
    text: str
    entity: str = ""
    kind: str = "fact"
    importance: float = 1.0


class MemoryUpdate(BaseModel):
    text: Optional[str] = None
    entity: Optional[str] = None
    importance: Optional[float] = None
    tier: Optional[str] = None


class MemorySettingsIn(BaseModel):
    qa_cache: Optional[bool] = None
    qa_threshold: Optional[float] = None
    qa_ttl_hours: Optional[float] = None
    recall_k: Optional[int] = None
    recall_max_chars: Optional[int] = None
    hot_days: Optional[int] = None
    archive_days: Optional[int] = None
    consolidate_with_claude: Optional[bool] = None
    consolidate_model: Optional[str] = None


class BackupSettingsIn(BaseModel):
    frequency: Optional[str] = None
    hour: Optional[int] = None
    day: Optional[str] = None
    keep_local: Optional[int] = None
    keep_remote: Optional[int] = None
    passphrase: Optional[str] = None
    share_email: Optional[str] = None
    include_reports: Optional[bool] = None


class RestoreIn(BaseModel):
    name: Optional[str] = None       # local file name
    drive_id: Optional[str] = None   # or a Google Drive file id
    passphrase: Optional[str] = None
    restore_env: bool = False
    confirm: bool = False


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
         "platforms": agent_settings.SOCIAL_PLATFORMS, "tree": tree, "get_links": odoo_tools.get_social_links, "browser": get_renderer().status()},
    )


@app.get("/memory", response_class=HTMLResponse)
def memory_page(request: Request):
    return templates.TemplateResponse(request, "memory.html", {"request": request, "stats": agent.memory.stats(), "kinds": memory_mod.KINDS,
                                                               "tiers": memory_mod.TIERS, "recent": agent.memory.list(limit=30)})


@app.get("/backup", response_class=HTMLResponse)
def backup_page(request: Request):
    return templates.TemplateResponse(request, "backup.html", {"request": request, "settings": backup_mod.public_settings(), "drive": GDrive().status(),
                                                               "local": backup_mod.list_local(), "frequencies": backup_mod.FREQUENCIES, "days": backup_mod.DAYS})


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    tree = {"companies": []}
    try:
        tree = odoo_tools.list_monitoring_tree()
    except Exception:
        pass
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"request": request, "tree": tree, "reports": report_store.list_reports(), "report_stats": report_store.stats(), "schedule": agent._read_schedule()},
    )


@app.get("/sw.js")
def service_worker():
    # Served from root so the SW scope is the whole site (not /static/).
    return FileResponse(str(static_dir / "sw.js"), media_type="application/javascript",
                        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})


@app.get("/manifest.webmanifest")
def web_manifest():
    return FileResponse(str(static_dir / "manifest.webmanifest"), media_type="application/manifest+json")


@app.get("/offline.html", response_class=HTMLResponse)
def offline_page(request: Request):
    return templates.TemplateResponse(request, "offline.html", {"request": request})


@app.post("/api/chat")
def api_chat(body: ChatRequest):
    reply = agent.handle(body.message)
    task_id = None
    m = re.match(r"\x00TASK:([0-9a-f]+)\x00(.*)", reply, re.S)
    if m:
        task_id, reply = m.group(1), m.group(2)
    return {"reply": reply, "task_id": task_id, "claude": agent.claude.last, "model": agent.claude.model}


@app.get("/api/chat/task/{task_id}")
def api_chat_task(task_id: str):
    t = agent.task_status(task_id)
    if not t:
        return JSONResponse({"error": "مهمة غير معروفة"}, status_code=404)
    return t


@app.get("/api/chat/tasks")
def api_chat_tasks():
    return {"tasks": agent.list_tasks()}


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
        row = agent.sources.add(body.name, body.url, body.type, body.region, body.enabled, render_js=body.render_js)
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
    # Report generation runs a full monitoring scan + Claude synthesis (can exceed the ~100s edge
    # timeout), so run it as a background task and let the page poll /api/chat/task/{id}.
    focus = (focus or "").strip()
    kind = "مخصص" if focus else "أسبوعي"
    tid = agent.start_task("report", f"إنشاء تقرير {kind}",
        lambda progress, f=focus: (lambda r: {"ok": r.get("ok", True), "summary": f"تم إنشاء التقرير: {r.get('title', r.get('name'))}", "report": r})(
            WeeklyReportGenerator(odoo=odoo_tools, claude=agent.claude).run_full_cycle(custom_focus=f, progress=progress)))
    return {"task_id": tid, "kind": kind}


@app.get("/api/reports")
def api_reports(kind: Optional[str] = None, include_archived: bool = True):
    return {"reports": report_store.list_reports(kind=kind, include_archived=include_archived), "stats": report_store.stats()}


@app.get("/api/reports/{name}")
def api_report_get(name: str):
    r = report_store.get(name)
    if not r:
        return JSONResponse({"error": "تقرير غير موجود"}, status_code=404)
    return r


@app.get("/api/reports/{name}/download")
def api_report_download(name: str):
    p = report_store.path_of(name)
    if not p:
        return JSONResponse({"error": "غير موجود"}, status_code=404)
    return FileResponse(str(p), filename=p.name, media_type="text/markdown")


@app.get("/api/reports/{name}/export")
def api_report_export(name: str, format: str = "pdf"):
    meta = report_store.get(name)
    if not meta:
        return JSONResponse({"error": "تقرير غير موجود"}, status_code=404)
    fmt = "docx" if format.lower() in ("docx", "word", "doc") else "pdf"
    out = report_store.OUT_DIR / "exports"
    out.mkdir(parents=True, exist_ok=True)
    stem = Path(name).stem
    try:
        if fmt == "pdf":
            path = report_export.to_pdf(meta["content"], out / f"{stem}.pdf", title=meta.get("title", "تقرير"))
            return FileResponse(str(path), filename=f"{stem}.pdf", media_type="application/pdf")
        path = report_export.to_docx(meta["content"], out / f"{stem}.docx", title=meta.get("title", "تقرير"))
        return FileResponse(str(path), filename=f"{stem}.docx",
                            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    except Exception as e:
        return JSONResponse({"error": f"تعذّر التصدير: {e}"}, status_code=500)


@app.post("/api/reports/{name}/archive")
def api_report_archive(name: str):
    try:
        return report_store.set_archived(name, True)
    except KeyError as e:
        return JSONResponse({"error": str(e)}, status_code=404)


@app.post("/api/reports/{name}/unarchive")
def api_report_unarchive(name: str):
    try:
        return report_store.set_archived(name, False)
    except KeyError as e:
        return JSONResponse({"error": str(e)}, status_code=404)


@app.delete("/api/reports/{name}")
def api_report_delete(name: str):
    return {"ok": report_store.delete(name)}


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


# ---- memory ----
@app.get("/api/memory/stats")
def api_memory_stats():
    return agent.memory.stats()


@app.get("/api/memory/search")
def api_memory_search(q: str, k: int = 10, kind: Optional[str] = None, entity: Optional[str] = None):
    return {"results": agent.memory.search(q, k=k, kinds=[kind] if kind else None, entity=entity, touch=False)}


@app.get("/api/memory/list")
def api_memory_list(kind: Optional[str] = None, tier: Optional[str] = None, entity: Optional[str] = None, limit: int = 50, offset: int = 0):
    return {"items": agent.memory.list(kind=kind, tier=tier, entity=entity, limit=limit, offset=offset)}


@app.post("/api/memory")
def api_memory_add(body: MemoryIn):
    try:
        mid = agent.memory.add(body.kind, body.text, entity=body.entity, source="user", importance=body.importance)
        return agent.memory.get(mid)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.patch("/api/memory/{memory_id}")
def api_memory_update(memory_id: int, body: MemoryUpdate):
    try:
        return agent.memory.update(memory_id, **body.model_dump(exclude_none=True))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/memory/consolidate")
def api_memory_consolidate():
    return agent.memory.consolidate(claude=agent.claude)


@app.get("/api/memory/settings")
def api_memory_settings():
    return memory_mod.get_settings()


@app.post("/api/memory/settings")
def api_memory_settings_set(body: MemorySettingsIn):
    return memory_mod.set_settings(**body.model_dump(exclude_none=True))


# ---- backup / restore ----
@app.get("/api/backup/status")
def api_backup_status():
    return {"settings": backup_mod.public_settings(), "drive": GDrive().status(), "local": backup_mod.list_local()}


@app.post("/api/backup/settings")
def api_backup_settings(body: BackupSettingsIn):
    try:
        return backup_mod.set_settings(**body.model_dump(exclude_none=True))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/backup/now")
def api_backup_now():
    return backup_mod.create_backup()


@app.get("/api/backup/download/{name}")
def api_backup_download(name: str):
    p = backup_mod.BACKUP_DIR / Path(name).name
    if not p.exists() or not p.name.startswith("odoo-ai-agent-"):
        return JSONResponse({"error": "غير موجود"}, status_code=404)
    return FileResponse(str(p), filename=p.name, media_type="application/octet-stream")


@app.post("/api/backup/upload")
def api_backup_upload(file: UploadFile = File(...)):
    """Upload an archive (e.g. downloaded earlier) so it can be inspected/restored."""
    name = Path(file.filename or "").name
    if not name.startswith("odoo-ai-agent-") or ".tar.gz" not in name:
        return JSONResponse({"error": "اسم الملف يجب أن يكون نسخة odoo-ai-agent-*.tar.gz[.enc]"}, status_code=400)
    backup_mod.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dest = backup_mod.BACKUP_DIR / name
    dest.write_bytes(file.file.read())
    return {"ok": True, "name": name, "bytes": dest.stat().st_size}


@app.post("/api/backup/inspect")
def api_backup_inspect(body: RestoreIn):
    try:
        path = backup_mod.download_from_drive(body.drive_id) if body.drive_id else backup_mod.BACKUP_DIR / Path(body.name or "").name
        if not path.exists():
            return JSONResponse({"error": "النسخة غير موجودة"}, status_code=404)
        return {"name": path.name, **backup_mod.inspect_backup(path, body.passphrase or backup_mod.get_settings()["passphrase"] or None)}
    except (ValueError, GDriveError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/backup/restore")
def api_backup_restore(body: RestoreIn):
    if not body.confirm:
        return JSONResponse({"error": "الاسترجاع يستبدل الذاكرة والإعدادات الحالية — أرسل confirm=true"}, status_code=400)
    try:
        path = backup_mod.download_from_drive(body.drive_id) if body.drive_id else backup_mod.BACKUP_DIR / Path(body.name or "").name
        if not path.exists():
            return JSONResponse({"error": "النسخة غير موجودة"}, status_code=404)
        result = backup_mod.restore_backup(path, body.passphrase or backup_mod.get_settings()["passphrase"] or None, restore_env=body.restore_env)
        agent.memory = memory_mod.get_memory()
        return result
    except (ValueError, GDriveError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/api/backup/drive")
def api_drive_status():
    gd = GDrive()
    st = gd.status()
    if st["linked"]:
        try:
            st["backups"] = gd.list_backups()
        except GDriveError as e:
            st["error"] = str(e)
    return st


@app.post("/api/backup/drive/credentials")
def api_drive_credentials(file: UploadFile = File(...), share_email: str = Form(default="")):
    """Link Google Drive by uploading the credentials JSON. Service account → linked immediately;
    OAuth client → returns a device code the user enters once at google.com/device."""
    try:
        info = save_credentials(file.file.read())
        if share_email:
            backup_mod.set_settings(share_email=share_email)
        gd = GDrive()
        if info["kind"] == "service_account":
            fid = gd.ensure_folder(share_email or backup_mod.get_settings()["share_email"] or None)
            return {"ok": True, **info, "folder_id": fid, "shared_with": gd.token.get("shared_with"),
                    "note": "تم الربط. المجلد OdooAIAgent-Backups سيظهر في «تمت مشاركته معي» في حسابك" if gd.token.get("shared_with") else
                            "تم الربط. أدخل بريدك في «مشاركة مع» ليظهر المجلد في حسابك"}
        flow = gd.start_device_flow()
        return {"ok": True, **info, "device": flow, "note": f"افتح {flow['verification_url']} وأدخل الرمز {flow['user_code']} ثم اضغط «تحقق»"}
    except GDriveError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/backup/drive/poll")
def api_drive_poll():
    gd = GDrive()
    r = gd.poll_device_flow()
    if r.get("ok"):
        try:
            r["folder_id"] = gd.ensure_folder()
        except GDriveError as e:
            r["warning"] = str(e)
    return r


@app.post("/api/backup/drive/share")
def api_drive_share(email: str = Form(...)):
    try:
        backup_mod.set_settings(share_email=email)
        return GDrive().share_folder(email)
    except GDriveError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/backup/drive/unlink")
def api_drive_unlink():
    unlink_drive()
    return {"ok": True}


@app.post("/api/backup/drive/test")
def api_drive_test():
    try:
        gd = GDrive()
        if not gd.linked():
            return {"ok": False, "error": "غير مرتبط"}
        return {"ok": True, "folder_id": gd.ensure_folder(), "backups": len(gd.list_backups()), "account": gd.status().get("account")}
    except GDriveError as e:
        return {"ok": False, "error": str(e)}


# ---- Web Push notifications ----
@app.get("/api/push/vapid")
def api_push_vapid():
    return {"publicKey": push_mod.public_key(), "state": push_mod.get_state()}


@app.post("/api/push/subscribe")
def api_push_subscribe(sub: dict):
    try:
        return push_mod.subscribe(sub)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/push/unsubscribe")
def api_push_unsubscribe(body: dict):
    return push_mod.unsubscribe(body.get("endpoint", ""))


@app.post("/api/push/test")
def api_push_test():
    return push_mod.send_push("وكيل Odoo — اختبار", "الإشعارات تعمل بنجاح ✅", url="/dashboard", respect_enabled=False)


@app.get("/api/push/state")
def api_push_state():
    return push_mod.get_state()


@app.post("/api/push/state")
def api_push_state_set(body: dict):
    return push_mod.set_enabled(bool(body.get("enabled", True)))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("web.app:app", host=os.getenv("WEB_HOST", "0.0.0.0"), port=int(os.getenv("WEB_PORT", "8080")), reload=True)
