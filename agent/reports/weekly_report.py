"""Weekly report generator — Claude CLI synthesis + Odoo note."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from agent.claude_cli import ClaudeCLI
from agent.memory import get_memory
from agent.tools.odoo_tools import OdooTools
from agent.monitors.full_cycle import run_full_monitoring

logger = logging.getLogger(__name__)
OUT_DIR = Path(__file__).resolve().parent.parent.parent / "reports_output"


class WeeklyReportGenerator:
    def __init__(self, odoo: Optional[OdooTools] = None, claude: Optional[ClaudeCLI] = None):
        self.odoo = odoo or OdooTools()
        self.claude = claude or ClaudeCLI()

    def collect(self) -> Dict[str, Any]:
        tree = self.odoo.list_monitoring_tree()
        scan = run_full_monitoring(self.odoo)
        # Everything learned this week (monitors de-duplicate, so the scan alone would under-report)
        week = [{"when": e.get("event_at", "")[:10], "entity": e.get("entity"), "text": e.get("text", "")[:240], "url": e.get("url", "")}
                for e in get_memory().recent_events(days=7, limit=80)]
        return {
            "generated_at": datetime.now().isoformat(),
            "entities": tree,
            "monitoring": scan,
            "memory_last_7_days": week,
        }

    def render_with_claude(self, data: Dict[str, Any], custom_focus: str = "") -> str:
        focus = custom_focus or "تقرير أسبوعي شامل"
        prompt = f"""اكتب تقريراً أسبوعياً بالعربية منظماً بالعناوين التالية:
1) ملخص تنفيذي
2) التعيينات والترقيات والاستقالات
3) الصفقات والتصريحات المالية/الاستراتيجية
4) التشريعات والقوانين المؤثرة على الشركات أو العاملين
5) جهات تحتاج متابعة
6) توصيات

التركيز: {focus}

البيانات:
{data}
"""
        if self.claude.available():
            try:
                return self.claude.complete(prompt)
            except Exception as e:
                logger.warning("Claude failed: %s", e)
        companies = (data.get("entities") or {}).get("companies") or []
        lines = [
            f"# تقرير أسبوعي — {data.get('generated_at')}",
            f"التركيز: {focus}",
            f"عدد الشركات تحت المراقبة: {len(companies)}",
            "",
            "## نتائج المسح",
            str(data.get("monitoring")),
        ]
        for b in companies:
            c = b.get("company") or {}
            lines.append(f"- {c.get('name')}: {b.get('people_count')} شخصية | ready={b.get('monitoring_ready')}")
        return "\n".join(lines)

    def save(self, text: str) -> Path:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        path = OUT_DIR / f"weekly_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        path.write_text(text, encoding="utf-8")
        return path

    def run_full_cycle(self, custom_focus: str = "") -> Dict[str, Any]:
        data = self.collect()
        text = self.render_with_claude(data, custom_focus=custom_focus)
        path = self.save(text)
        note_id = None
        try:
            note_id = self.odoo.client.create_note(text[:5000])
        except Exception as e:
            logger.warning("note: %s", e)
        try:  # the report itself becomes long-term memory (searchable later, summarised on archive)
            get_memory().add("report", f"تقرير {path.name}: {text[:1500]}", source=str(path.name), importance=0.7, meta={"path": str(path), "focus": custom_focus})
        except Exception as e:
            logger.warning("memory: %s", e)
        return {"path": str(path), "note_id": note_id, "chars": len(text), "entities": data.get("entities")}
