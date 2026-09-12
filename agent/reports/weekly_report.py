"""Report generator — weekly (full) or custom (focused). Claude CLI synthesis + Odoo note + store."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from agent.claude_cli import ClaudeCLI
from agent.memory import get_memory
from agent.reports import store as report_store
from agent.tools.odoo_tools import OdooTools
from agent.monitors.full_cycle import run_full_monitoring

logger = logging.getLogger(__name__)
OUT_DIR = Path(__file__).resolve().parent.parent.parent / "reports_output"
Progress = Callable[[str, Optional[float]], None]


class WeeklyReportGenerator:
    def __init__(self, odoo: Optional[OdooTools] = None, claude: Optional[ClaudeCLI] = None):
        self.odoo = odoo or OdooTools()
        self.claude = claude or ClaudeCLI()

    def collect(self, progress: Optional[Progress] = None) -> Dict[str, Any]:
        if progress:
            progress("جمع الجهات المراقبة…", 0.15)
        tree = self.odoo.list_monitoring_tree()
        if progress:
            progress("تشغيل دورة المراقبة (أخبار/تعيينات/تشريعات)…", 0.3)
        scan = run_full_monitoring(self.odoo)
        # Everything learned this week (monitors de-duplicate, so the scan alone would under-report)
        week = [{"when": (e.get("event_at") or "")[:10], "entity": e.get("entity"), "text": (e.get("text") or "")[:240], "url": e.get("url", "")}
                for e in get_memory().recent_events(days=7, limit=80)]
        return {"generated_at": datetime.now().isoformat(), "entities": tree, "monitoring": scan, "memory_last_7_days": week}

    @staticmethod
    def _summarize_for_prompt(data: Dict[str, Any]) -> str:
        """Compact, readable summary for the model (instead of dumping huge nested dicts)."""
        ent = data.get("entities") or {}
        companies = ent.get("companies") or []
        lines = [f"عدد الشركات المراقَبة: {len(companies)} (إجمالي الجهات: {ent.get('total_partners', 0)})"]
        steps = (data.get("monitoring") or {}).get("steps", {})
        n = steps.get("news_deals_finance", {})
        a = steps.get("appointments_promotions", {})
        l = steps.get("legislation", {})
        lines.append(f"أخبار جديدة: {n.get('new', 0)} · تعيينات/ترقيات جديدة: {a.get('new', 0)} · وثائق تشريعية جديدة: {l.get('documents_new', 0)}")
        lines.append("\nأحداث آخر 7 أيام:")
        for e in data.get("memory_last_7_days", [])[:40]:
            lines.append(f"- [{e.get('when')}] {e.get('entity') or ''}: {e.get('text')}" + (f" ({e.get('url')})" if e.get('url', '').startswith('http') else ""))
        lines.append("\nالجهات:")
        for b in companies[:60]:
            c = b.get("company") or {}
            people = "، ".join((pe.get("name") or "") for pe in (b.get("people") or [])[:6])
            lines.append(f"- {c.get('name')} — شخصيات: {people or 'لا يوجد'}")
        return "\n".join(lines)

    def render_with_claude(self, data: Dict[str, Any], custom_focus: str = "", progress: Optional[Progress] = None) -> str:
        focus = custom_focus or "تقرير أسبوعي شامل عن كل الجهات المراقبة"
        if progress:
            progress("صياغة التقرير عبر Claude…", 0.65)
        prompt = (
            "اكتب تقريراً احترافياً بالعربية منظّماً بعناوين Markdown، بالأقسام التالية:\n"
            "1) ملخص تنفيذي\n2) التعيينات والترقيات والاستقالات\n3) الصفقات والتصريحات المالية/الاستراتيجية\n"
            "4) التشريعات والقوانين المؤثرة\n5) جهات تحتاج متابعة\n6) توصيات\n\n"
            f"التركيز: {focus}\n\n"
            "اعتمد فقط على البيانات التالية ولا تختلق. اذكر المصادر (الروابط) حين تتوفر:\n\n"
            + self._summarize_for_prompt(data)
        )
        if self.claude.available():
            try:
                text = self.claude.complete(prompt, timeout=300)
                if text and len(text) > 40:
                    return f"# {'تقرير مخصص' if custom_focus else 'تقرير أسبوعي'} — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n" \
                           + (f"**التركيز:** {custom_focus}\n\n" if custom_focus else "\n") + text
            except Exception as e:
                logger.warning("Claude failed: %s", e)
        return self._fallback(data, custom_focus)

    @staticmethod
    def _fallback(data: Dict[str, Any], custom_focus: str) -> str:
        """Readable Markdown even without Claude (no raw dict dumps)."""
        ent = data.get("entities") or {}
        companies = ent.get("companies") or []
        steps = (data.get("monitoring") or {}).get("steps", {})
        n = steps.get("news_deals_finance", {}); a = steps.get("appointments_promotions", {}); l = steps.get("legislation", {})
        out = [
            f"# {'تقرير مخصص' if custom_focus else 'تقرير أسبوعي'} — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            (f"**التركيز:** {custom_focus}\n" if custom_focus else ""),
            "## ملخص تنفيذي",
            f"- الشركات المراقَبة: **{len(companies)}** (إجمالي الجهات {ent.get('total_partners', 0)})",
            f"- أخبار جديدة: **{n.get('new', 0)}** · تعيينات/ترقيات: **{a.get('new', 0)}** · وثائق تشريعية: **{l.get('documents_new', 0)}**",
            "",
            "## أحداث آخر 7 أيام",
        ]
        events = data.get("memory_last_7_days", [])
        if events:
            for e in events[:50]:
                link = f" — [المصدر]({e['url']})" if (e.get("url") or "").startswith("http") else ""
                out.append(f"- **[{e.get('when')}]** {e.get('entity') or ''}: {e.get('text')}{link}")
        else:
            out.append("_لا أحداث جديدة هذا الأسبوع._")
        out += ["", "## الجهات تحت المراقبة"]
        for b in companies:
            c = b.get("company") or {}
            ready = "✅" if b.get("monitoring_ready") else "⚠️ بدون شخصيات"
            out.append(f"- **{c.get('name')}** ({b.get('people_count')} شخصية) {ready}")
        return "\n".join(out)

    def save(self, text: str, custom_focus: str = "") -> Path:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        prefix = "custom" if custom_focus else "weekly"
        path = OUT_DIR / f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        path.write_text(text, encoding="utf-8")
        return path

    def run_full_cycle(self, custom_focus: str = "", progress: Optional[Progress] = None) -> Dict[str, Any]:
        if progress:
            progress("بدء إنشاء التقرير…", 0.05)
        data = self.collect(progress=progress)
        text = self.render_with_claude(data, custom_focus=custom_focus, progress=progress)
        path = self.save(text, custom_focus=custom_focus)
        kind = "custom" if custom_focus else "weekly"
        title = text.splitlines()[0].lstrip("#").strip() if text else path.stem
        report_store.register(path.name, kind, title, focus=custom_focus, chars=len(text))
        if progress:
            progress("حفظ التقرير…", 0.9)
        note_id = None
        try:
            note_id = self.odoo.client.create_note(text[:5000])
        except Exception as e:
            logger.warning("note: %s", e)
        try:  # the report itself becomes long-term memory (searchable later, summarised on archive)
            get_memory().add("report", f"تقرير {path.name}: {text[:1500]}", source=str(path.name), importance=0.7, meta={"path": str(path), "focus": custom_focus, "kind": kind})
        except Exception as e:
            logger.warning("memory: %s", e)
        result = {"ok": True, "path": str(path), "name": path.name, "kind": kind, "title": title, "note_id": note_id, "chars": len(text)}
        try:  # email delivery must never fail report generation
            from agent import email_sender
            mail = email_sender.maybe_send_report(result)
            result["email"] = mail
            if mail.get("ok") and progress:
                progress(f"أُرسل بالبريد إلى {mail.get('to', '')}", 0.98)
        except Exception as e:
            logger.warning("email hook failed: %s", e)
            result["email"] = {"ok": False, "error": str(e)[:200]}
        if progress:
            progress("اكتمل.", 1.0)
        return result
