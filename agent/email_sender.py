"""Send reports by email over SMTP (stdlib only).

Settings live in data/email_settings.json; the SMTP password is kept in a SEPARATE file
(data/email_secret.json) so it is only ever included in an *encrypted* backup — same rule the
project already applies to .env. The API never returns the password (only `password_set`).
"""

from __future__ import annotations

import logging
import re
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent import schedule_util
from agent.config_store import JsonStore
from agent.reports import export as report_export
from agent.reports import store as report_store

logger = logging.getLogger(__name__)

SECURITY = ("ssl", "starttls", "none")
FREQUENCIES = schedule_util.FREQUENCIES
DAYS = schedule_util.DAYS
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")

PRESETS = {
    "gmail": {"host": "smtp.gmail.com", "port": 465, "security": "ssl", "note": "يتطلّب App Password (تحقّق بخطوتين)"},
    "outlook": {"host": "smtp.office365.com", "port": 587, "security": "starttls", "note": "استخدم بريد Microsoft وكلمة مرور تطبيق"},
}

DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "host": "", "port": 465, "security": "ssl",
    "username": "", "from_name": "وكيل Odoo للمراقبة", "from_addr": "",
    "recipients": [],
    "attach_pdf": True, "attach_docx": True,
    "on_weekly": True, "on_any_report": False,
    "frequency": "off", "hour": 8, "day": "sunday", "schedule_action": "latest",
    "last_sent": None, "last_result": None,
}

_store = JsonStore("email_settings.json", DEFAULTS)
_secret = JsonStore("email_secret.json", {})


# ---------------------------------------------------------------- settings
def get_settings() -> Dict[str, Any]:
    return {**DEFAULTS, **_store.load()}


def _password() -> str:
    return _secret.load().get("password", "") or ""


def public_settings() -> Dict[str, Any]:
    s = get_settings()
    return {**s, "password_set": bool(_password()), "next_due": next_due(s), "presets": PRESETS}


def _normalize_recipients(value: Any) -> List[str]:
    if isinstance(value, str):
        parts = re.split(r"[,;\n\s]+", value)
    else:
        parts = list(value or [])
    out: List[str] = []
    for p in parts:
        p = (p or "").strip()
        if not p:
            continue
        if not _EMAIL_RE.match(p):
            raise ValueError(f"بريد غير صالح: {p}")
        if p not in out:
            out.append(p)
    return out


def set_settings(**changes: Any) -> Dict[str, Any]:
    s = get_settings()
    if changes.get("password"):
        _secret.save({"password": str(changes["password"])})
    for k, v in changes.items():
        if v is None or k not in DEFAULTS:
            continue
        if k == "security" and v not in SECURITY:
            raise ValueError(f"نوع الأمان يجب أن يكون أحد: {', '.join(SECURITY)}")
        if k == "frequency" and v not in FREQUENCIES:
            raise ValueError(f"التكرار يجب أن يكون أحد: {', '.join(FREQUENCIES)}")
        if k == "day" and v not in DAYS:
            raise ValueError("يوم غير صالح")
        if k == "schedule_action" and v not in ("latest", "generate"):
            raise ValueError("schedule_action يجب أن يكون latest أو generate")
        if k == "port":
            v = int(v)
            if not (1 <= v <= 65535):
                raise ValueError("منفذ غير صالح")
        if k == "hour":
            v = max(0, min(23, int(v)))
        if k == "recipients":
            v = _normalize_recipients(v)
        if k in ("from_addr", "username") and v and not _EMAIL_RE.match(str(v).strip()):
            raise ValueError(f"بريد غير صالح: {v}")
        s[k] = v
    _store.save(s)
    return public_settings()


def _record(result: Dict[str, Any]) -> None:
    s = get_settings()
    if result.get("ok"):
        s["last_sent"] = datetime.now().isoformat(timespec="seconds")
    s["last_result"] = {k: v for k, v in result.items() if k != "content"}
    _store.save(s)


def is_due(s: Optional[Dict[str, Any]] = None) -> bool:
    s = s or get_settings()
    return bool(s.get("enabled")) and schedule_util.is_due(s, "last_sent")


def next_due(s: Optional[Dict[str, Any]] = None) -> Optional[str]:
    return schedule_util.next_due(s or get_settings(), "last_sent")


def status() -> Dict[str, Any]:
    s = get_settings()
    return {"enabled": bool(s["enabled"]), "configured": bool(s["host"] and s["from_addr"] and _password()),
            "recipients": len(s["recipients"]), "last_sent": s["last_sent"], "next_due": next_due(s)}


# ---------------------------------------------------------------- transport
def _connect(s: Dict[str, Any]):
    host, port, sec = s["host"], int(s["port"]), s["security"]
    if not host:
        raise ValueError("لم يُضبط مضيف SMTP")
    if sec == "ssl":
        srv = smtplib.SMTP_SSL(host, port, timeout=30, context=ssl.create_default_context())
    else:
        srv = smtplib.SMTP(host, port, timeout=30)
        srv.ehlo()
        if sec == "starttls":
            srv.starttls(context=ssl.create_default_context())
            srv.ehlo()
    if s["username"] and _password():
        srv.login(s["username"], _password())
    return srv


def test_connection() -> Dict[str, Any]:
    s = get_settings()
    try:
        srv = _connect(s)
        try:
            srv.noop()
        finally:
            srv.quit()
        return {"ok": True, "detail": f"{s['host']}:{s['port']} ({s['security']}) — تم تسجيل الدخول بنجاح"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"}


def _send(msg: EmailMessage) -> Dict[str, Any]:
    s = get_settings()
    srv = _connect(s)
    try:
        srv.send_message(msg)
    finally:
        try:
            srv.quit()
        except Exception:
            pass
    return {"ok": True, "to": msg["To"]}


def _from_header(s: Dict[str, Any]) -> str:
    addr = s["from_addr"] or s["username"]
    return f"{s['from_name']} <{addr}>" if s.get("from_name") else addr


def _guard(s: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not s["host"] or not (s["from_addr"] or s["username"]):
        return {"ok": False, "error": "إعدادات SMTP غير مكتملة (المضيف/المُرسِل)"}
    if not s["recipients"]:
        return {"ok": False, "error": "لا يوجد مستلمون"}
    return None


def send_test() -> Dict[str, Any]:
    s = get_settings()
    bad = _guard(s)
    if bad:
        return bad
    msg = EmailMessage()
    msg["Subject"] = "وكيل Odoo — رسالة تجريبية"
    msg["From"] = _from_header(s)
    msg["To"] = ", ".join(s["recipients"])
    msg.set_content("رسالة تجريبية من وكيل Odoo للمراقبة. إعدادات البريد تعمل بنجاح ✅")
    msg.add_alternative("<div dir='rtl' style='font-family:sans-serif'>"
                        "<h2 style='color:#a63f08'>وكيل Odoo للمراقبة</h2>"
                        "<p>رسالة تجريبية — إعدادات البريد تعمل بنجاح ✅</p></div>", subtype="html")
    try:
        r = _send(msg)
        _record(r)
        return r
    except Exception as e:
        r = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"}
        _record(r)
        return r


def send_report(name: str, extra_note: str = "") -> Dict[str, Any]:
    """Email one stored report: formatted RTL HTML body + PDF/DOCX attachments."""
    s = get_settings()
    bad = _guard(s)
    if bad:
        return bad
    meta = report_store.get(name)
    if not meta:
        return {"ok": False, "error": f"تقرير غير موجود: {name}"}

    md = meta.get("content") or ""
    title = meta.get("title") or name
    kind_ar = "أسبوعي" if meta.get("kind") == "weekly" else "مخصّص"

    msg = EmailMessage()
    msg["Subject"] = f"تقرير {kind_ar} — {title}"[:180]
    msg["From"] = _from_header(s)
    msg["To"] = ", ".join(s["recipients"])
    plain = re.sub(r"[#*`]", "", md)
    msg.set_content((extra_note + "\n\n" if extra_note else "") + plain[:20000])
    body_html = report_export.to_html_doc(md, title)
    if extra_note:
        body_html = body_html.replace("<body>", f"<body><p dir='rtl'><b>{extra_note}</b></p>", 1)
    msg.add_alternative(body_html, subtype="html")

    exports = report_store.OUT_DIR / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    stem = Path(name).stem
    attached: List[str] = []
    if s["attach_pdf"]:
        try:
            p = report_export.to_pdf(md, exports / f"{stem}.pdf", title=title)
            msg.add_attachment(p.read_bytes(), maintype="application", subtype="pdf", filename=p.name)
            attached.append(p.name)
        except Exception as e:
            logger.warning("PDF attach failed: %s", e)
    if s["attach_docx"]:
        try:
            p = report_export.to_docx(md, exports / f"{stem}.docx", title=title)
            msg.add_attachment(p.read_bytes(), maintype="application",
                               subtype="vnd.openxmlformats-officedocument.wordprocessingml.document", filename=p.name)
            attached.append(p.name)
        except Exception as e:
            logger.warning("DOCX attach failed: %s", e)

    try:
        r = _send(msg)
        r.update({"report": name, "kind": meta.get("kind"), "attachments": attached})
        _record(r)
        logger.info("Report emailed: %s → %s", name, r["to"])
        return r
    except Exception as e:
        r = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}", "report": name}
        _record(r)
        return r


def maybe_send_report(result: Dict[str, Any]) -> Dict[str, Any]:
    """Called after every report generation; decides from the trigger settings."""
    s = get_settings()
    if not s["enabled"]:
        return {"ok": False, "skipped": "البريد معطّل"}
    kind = result.get("kind")
    if not (s["on_any_report"] or (s["on_weekly"] and kind == "weekly")):
        return {"ok": False, "skipped": "لا يوجد مُشغِّل مطابق"}
    name = result.get("name")
    if not name:
        return {"ok": False, "skipped": "لا يوجد اسم تقرير"}
    return send_report(name, extra_note="أُرسل تلقائياً عند إنشاء التقرير.")


def send_scheduled() -> Dict[str, Any]:
    """Independent email schedule: send the latest report, or generate a fresh one first."""
    s = get_settings()
    if s["schedule_action"] == "generate":
        from agent.reports.weekly_report import WeeklyReportGenerator
        res = WeeklyReportGenerator().run_full_cycle()  # its own hook may already email; guarded below
        if res.get("name") and not s["on_any_report"] and not (s["on_weekly"] and res.get("kind") == "weekly"):
            return send_report(res["name"], extra_note="تقرير مجدول بالبريد.")
        _record({"ok": True, "note": "أُرسل عبر هوك التوليد"})
        return {"ok": True, "note": "أُرسل عبر هوك التوليد"}
    rows = report_store.list_reports(include_archived=False)
    if not rows:
        return {"ok": False, "error": "لا توجد تقارير لإرسالها"}
    return send_report(rows[0]["name"], extra_note="تقرير مجدول بالبريد.")
