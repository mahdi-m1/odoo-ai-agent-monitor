"""Interactive AI Agent — text commands + Claude CLI fallback."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv

from agent.claude_cli import ClaudeCLI
from agent.tools.odoo_tools import OdooTools
from agent.monitors.news_monitor import NewsMonitor
from agent.monitors.appointments_monitor import AppointmentsMonitor
from agent.monitors.legislation_monitor import LegislationMonitor
from agent.monitors.social_monitor import SocialMonitor
from agent.monitors.full_cycle import run_full_monitoring
from agent.reports.weekly_report import WeeklyReportGenerator
from agent.smoke_test import run_smoke

load_dotenv()
logger = logging.getLogger(__name__)
SCHEDULE_FILE = Path(__file__).resolve().parent.parent / "config" / "schedule.env"


class AIAgent:
    def __init__(self):
        self.odoo = OdooTools()
        self.claude = ClaudeCLI()

    def handle(self, message: str) -> str:
        msg = (message or "").strip()
        if not msg:
            return "أدخل أمراً أو اكتب «مساعدة»."
        local = self._try_local_commands(msg)
        if local is not None:
            return local
        try:
            partners = self.odoo.list_monitored(limit=15)
            ctx = "الجهات المراقبة:\n" + "\n".join(
                f"- #{p.get('id')} {p.get('name')} ({'شركة' if p.get('is_company') else 'شخص'})"
                for p in partners
            )
        except Exception as e:
            ctx = f"(تعذر قراءة Odoo: {e})"
        prompt = f"أنت وكيل مراقبة أعمال فوق Odoo. أجب بالعربية باختصار عملي.\n{ctx}\n\nطلب المستخدم: {msg}"
        try:
            return self.claude.complete(prompt)
        except Exception as e:
            return f"تعذر استدعاء Claude CLI: {e}\nاستخدم الأوامر المحلية (مساعدة)."

    def _try_local_commands(self, msg: str) -> Optional[str]:
        lower = msg.lower().strip()

        if lower in ("مساعدة", "help", "?"):
            return (
                "الأوامر المتاحة:\n"
                "- قائمة / list\n"
                "- أضف شركة: الاسم، الدولة ؛ شخص | منصب\n"
                "- أضف شخص: الاسم، الشركة، المنصب\n"
                "- عدّل ID: name=... , phone=...\n"
                "- أزل من المراقبة ID تأكيد\n"
                "- احذف نهائياً ID تأكيد\n"
                "- احذف شركة ID تأكيد مع الأشخاص\n"
                "- تقرير / weekly\n"
                "- راقب الكل / full scan\n"
                "- راقب أخبار | تعيينات | تشريعات | تواصل\n"
                "- جدول | جدول يوم=sunday ساعة=8\n"
                "- حالة / status\n"
                "- اختبار / smoke : اختبار دخان بدون توكنات Claude"
            )

        if lower in ("حالة", "status"):
            return json.dumps(
                {"odoo": self.odoo.health(), "claude_cli": self.claude.available(), "schedule": self._read_schedule()},
                ensure_ascii=False, indent=2,
            )

        if lower in ("قائمة", "list", "المراقبة"):
            try:
                tree = self.odoo.list_monitoring_tree()
                lines = []
                for block in tree.get("companies") or []:
                    c = block["company"]
                    ready = "جاهزة" if block["monitoring_ready"] else "بدون شخصيات!"
                    lines.append(f"#{c['id']} شركة | {c.get('name')} | شخصيات: {block['people_count']} | {ready}")
                    for pe in block.get("people") or []:
                        lines.append(f"   └ #{pe['id']} {pe.get('name')} | {pe.get('function') or '—'}")
                for pe in tree.get("orphan_people") or []:
                    lines.append(f"#{pe['id']} شخص (بدون شركة) | {pe.get('name')}")
                return "قائمة المراقبة:\n" + ("\n".join(lines) if lines else "فارغة — أضف شركة مع شخصيات.")
            except Exception as e:
                return f"تعذر قراءة القائمة: {e}"

        m = re.match(r"أضف\s+شركة\s*[:：]\s*(.+)", msg, re.I | re.S)
        if m:
            raw = m.group(1).strip()
            if "؛" in raw or ";" in raw:
                sep = "؛" if "؛" in raw else ";"
                chunks = [c.strip() for c in raw.split(sep) if c.strip()]
                head, people_lines = chunks[0], chunks[1:]
            else:
                head, people_lines = raw, []
            parts = [x.strip() for x in head.split(",")]
            res = self.odoo.add_company(
                name=parts[0],
                country=parts[1] if len(parts) > 1 else "Bahrain",
                website=parts[2] if len(parts) > 2 else "",
                people=people_lines,
            )
            return f"نتيجة إضافة الشركة مع الشخصيات:\n{json.dumps(res, ensure_ascii=False, default=str, indent=2)}"

        m = re.match(r"أضف\s+شخص\s*[:：]\s*(.+)", msg, re.I)
        if m:
            parts = [p.strip() for p in m.group(1).split(",")]
            res = self.odoo.add_person(
                name=parts[0],
                company_name=parts[1] if len(parts) > 1 else "",
                job_title=parts[2] if len(parts) > 2 else "",
            )
            return f"نتيجة إضافة الشخص:\n{json.dumps(res, ensure_ascii=False, default=str, indent=2)}"

        m = re.match(r"عد[ّ]?ل\s+(\d+)\s*[:：]\s*(.+)", msg)
        if m:
            fields: Dict[str, Any] = {}
            for part in m.group(2).split(","):
                if "=" in part:
                    k, v = part.split("=", 1)
                    fields[k.strip()] = v.strip()
            return json.dumps(self.odoo.update_partner(int(m.group(1)), **fields), ensure_ascii=False, indent=2)

        m = re.match(r"أزل\s+من\s+المراقبة\s+(\d+)(?:\s+(تأكيد|confirm))?", msg, re.I)
        if m:
            return json.dumps(self.odoo.remove_from_monitor(int(m.group(1)), confirm=bool(m.group(2))), ensure_ascii=False, indent=2)

        m = re.match(r"احذف\s+شركة\s+(\d+)(?:\s+(تأكيد|confirm))?(?:\s+مع\s+الأشخاص)?", msg, re.I)
        if m and "شركة" in msg:
            return json.dumps(self.odoo.delete_partner(int(m.group(1)), confirm=bool(m.group(2)), cascade_people=True), ensure_ascii=False, indent=2)

        m = re.match(r"احذف\s+نهائياً?\s+(\d+)(?:\s+(تأكيد|confirm))?", msg, re.I)
        if m:
            return json.dumps(self.odoo.delete_partner(int(m.group(1)), confirm=bool(m.group(2))), ensure_ascii=False, indent=2)

        if lower in ("تقرير", "weekly", "تقرير أسبوعي"):
            gen = WeeklyReportGenerator(odoo=self.odoo, claude=self.claude)
            return f"تم إنشاء التقرير.\n{json.dumps(gen.run_full_cycle(), ensure_ascii=False, indent=2, default=str)}"

        if msg.startswith("تقرير") and len(msg) > 8:
            gen = WeeklyReportGenerator(odoo=self.odoo, claude=self.claude)
            result = gen.run_full_cycle(custom_focus=msg)
            path = result.get("path")
            text = Path(path).read_text(encoding="utf-8")[:3500] if path and Path(path).exists() else ""
            return f"تقرير مخصص:\n{text}\n\n---\n{json.dumps(result, ensure_ascii=False, default=str)}"

        if lower in ("راقب الكل", "full scan", "مراقبة كاملة"):
            return json.dumps(run_full_monitoring(self.odoo), ensure_ascii=False, indent=2, default=str)[:4000]

        if lower in ("راقب أخبار", "scan news", "أخبار"):
            return json.dumps(NewsMonitor(self.odoo).run_and_log(), ensure_ascii=False, default=str, indent=2)[:2500]

        if lower in ("راقب تعيينات", "scan appointments", "تعيينات"):
            return json.dumps(AppointmentsMonitor(self.odoo).run_and_log(), ensure_ascii=False, default=str, indent=2)[:2500]

        if lower in ("راقب تشريعات", "scan legislation", "تشريعات", "قوانين"):
            return json.dumps(LegislationMonitor(self.odoo).run_and_log(), ensure_ascii=False, default=str, indent=2)[:2500]

        if lower in ("راقب تواصل", "scan social", "linkedin", "تواصل"):
            return json.dumps(SocialMonitor(self.odoo).run_and_log(), ensure_ascii=False, default=str, indent=2)[:2500]

        if lower.startswith("جدول"):
            return self._handle_schedule(msg)

        if lower in ("اختبار", "smoke", "smoke test", "اختبار دخان"):
            return run_smoke(keep_records=False, skip_network_search=True).format_ar()

        return None

    def _read_schedule(self) -> Dict[str, str]:
        data = {
            "WEEKLY_REPORT_DAY": os.getenv("WEEKLY_REPORT_DAY", "sunday"),
            "WEEKLY_REPORT_HOUR": os.getenv("WEEKLY_REPORT_HOUR", "8"),
            "DAILY_SCAN_HOUR": os.getenv("DAILY_SCAN_HOUR", "7"),
            "TIMEZONE": os.getenv("TIMEZONE", "Asia/Bahrain"),
        }
        if SCHEDULE_FILE.exists():
            for line in SCHEDULE_FILE.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    data[k.strip()] = v.strip()
        return data

    def _handle_schedule(self, msg: str) -> str:
        sched = self._read_schedule()
        if msg.strip() in ("جدول", "schedule"):
            return "الجدول الحالي:\n" + json.dumps(sched, ensure_ascii=False, indent=2)
        day = re.search(r"يوم\s*=\s*(\w+)", msg, re.I)
        hour = re.search(r"ساعة\s*=\s*(\d+)", msg, re.I)
        daily = re.search(r"مسح\s*=\s*(\d+)", msg, re.I)
        if day:
            sched["WEEKLY_REPORT_DAY"] = day.group(1).lower()
        if hour:
            sched["WEEKLY_REPORT_HOUR"] = str(int(hour.group(1)))
        if daily:
            sched["DAILY_SCAN_HOUR"] = str(int(daily.group(1)))
        SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
        SCHEDULE_FILE.write_text("\n".join(f"{k}={v}" for k, v in sched.items()) + "\n", encoding="utf-8")
        for k, v in sched.items():
            os.environ[k] = v
        return "تم تحديث config/schedule.env:\n" + json.dumps(sched, ensure_ascii=False, indent=2) + "\nأعد تشغيل scheduler."


def main():
    agent = AIAgent()
    print("Odoo AI Agent — اكتب «مساعدة» أو «خروج».")
    while True:
        try:
            user = input("\nأنت> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nوداعاً.")
            break
        if user.lower() in ("خروج", "exit", "quit"):
            break
        print(f"\nالوكيل>\n{agent.handle(user)}")


if __name__ == "__main__":
    main()
