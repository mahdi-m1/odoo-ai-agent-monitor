"""
Smoke test — zero Claude token usage.

Verifies Odoo CRM write path, list/update/delete-with-confirm, monitors import,
schedule config, and optional Claude CLI *binary presence only* (no API call).

Usage:
  python -m agent.smoke_test
  python -m agent.smoke_test --keep
  From chat:  اختبار   |  smoke
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sf = ROOT / "config" / "schedule.env"
if sf.exists():
    load_dotenv(sf, override=True)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("smoke")

TEST_PREFIX = "[SMOKE-TEST]"


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""
    ms: float = 0.0


@dataclass
class SmokeReport:
    results: List[CheckResult] = field(default_factory=list)
    started: str = ""
    finished: str = ""
    tokens_used: int = 0

    def add(self, name: str, ok: bool, detail: str = "", ms: float = 0.0) -> None:
        self.results.append(CheckResult(name, ok, detail, ms))

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.ok)

    @property
    def ok(self) -> bool:
        return self.failed == 0 and len(self.results) > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "passed": self.passed,
            "failed": self.failed,
            "claude_tokens_used": self.tokens_used,
            "started": self.started,
            "finished": self.finished,
            "checks": [
                {"name": r.name, "ok": r.ok, "detail": r.detail, "ms": round(r.ms, 1)}
                for r in self.results
            ],
        }

    def format_ar(self) -> str:
        lines = [
            "═══ اختبار دخان المشروع (بدون استهلاك توكنات Claude) ═══",
            f"البداية: {self.started}",
            f"النهاية: {self.finished}",
            f"توكنات Claude المستخدمة: {self.tokens_used} (مقصود = 0)",
            "",
        ]
        for r in self.results:
            mark = "✅" if r.ok else "❌"
            lines.append(f"{mark} {r.name} ({r.ms:.0f}ms)")
            if r.detail:
                lines.append(f"    {r.detail}")
        lines += [
            "",
            f"النتيجة: {self.passed} نجح / {self.failed} فشل — "
            + ("المشروع مستقر على المسار الحرج." if self.ok else "يوجد خلل — راجع التفاصيل."),
        ]
        return "\n".join(lines)


def _timed(fn: Callable[[], Any]):
    t0 = time.perf_counter()
    out = fn()
    return out, (time.perf_counter() - t0) * 1000


def run_smoke(keep_records: bool = False, skip_network_search: bool = True) -> SmokeReport:
    report = SmokeReport(started=datetime.now().isoformat(), tokens_used=0)

    try:
        def _imp():
            from agent.odoo_client import OdooClient
            from agent.tools.odoo_tools import OdooTools
            from agent.monitors.news_monitor import NewsMonitor
            from agent.monitors.appointments_monitor import AppointmentsMonitor
            from agent.monitors.legislation_monitor import LegislationMonitor
            from agent.monitors.social_monitor import SocialMonitor
            from agent.monitors.full_cycle import run_full_monitoring
            from agent.reports.weekly_report import WeeklyReportGenerator
            from agent.claude_cli import ClaudeCLI
            return True
        _, ms = _timed(_imp)
        report.add("استيراد الحزم", True, "agent + monitors + tools", ms)
    except Exception as e:
        report.add("استيراد الحزم", False, f"{e}", 0)
        report.finished = datetime.now().isoformat()
        return report

    from agent.tools.odoo_tools import OdooTools
    from agent.claude_cli import ClaudeCLI

    try:
        cli = ClaudeCLI()
        avail = cli.available()
        report.add(
            "Claude CLI (وجود البرنامج فقط)",
            True,
            "مثبت" if avail else "غير مثبت — التقارير الذكية لن تعمل حتى تثبيته (لا يوقف اختبار Odoo)",
        )
    except Exception as e:
        report.add("Claude CLI (وجود البرنامج فقط)", True, f"تخطي: {e}")

    odoo = OdooTools()
    try:
        health, ms = _timed(lambda: odoo.health())
        ok = bool(health.get("ok"))
        report.add("اتصال Odoo (JSON-2)", ok, json.dumps(health, ensure_ascii=False)[:300], ms)
        if not ok:
            report.finished = datetime.now().isoformat()
            return report
    except Exception as e:
        report.add("اتصال Odoo (JSON-2)", False, str(e))
        report.finished = datetime.now().isoformat()
        return report

    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    company_name = f"{TEST_PREFIX} شركة {stamp}"
    person_name = f"{TEST_PREFIX} شخص {stamp}"
    company_id = None
    person_id = None

    try:
        def _create():
            return odoo.add_company(
                name=company_name,
                country="Bahrain",
                comment=f"{TEST_PREFIX} auto",
                people=[f"{person_name} | مدير اختبار"],
            )
        res, ms = _timed(_create)
        company_id = (res.get("company") or {}).get("id")
        people = res.get("people") or []
        if people:
            person_id = people[0].get("id")
        ok = bool(company_id) and res.get("monitoring_ready") is True and person_id
        report.add(
            "إنشاء شركة + شخصية في CRM",
            bool(ok),
            f"company_id={company_id} person_id={person_id} ready={res.get('monitoring_ready')}",
            ms,
        )
    except Exception as e:
        report.add("إنشاء شركة + شخصية في CRM", False, traceback.format_exc()[-500:])
        report.finished = datetime.now().isoformat()
        return report

    try:
        tree, ms = _timed(lambda: odoo.list_monitoring_tree(limit=200))
        names = []
        for b in tree.get("companies") or []:
            names.append((b.get("company") or {}).get("name"))
            for pe in b.get("people") or []:
                names.append(pe.get("name"))
        found_c = company_name in names
        found_p = person_name in names
        report.add(
            "قراءة قائمة المراقبة من Odoo",
            found_c and found_p,
            f"شركة={found_c} شخص={found_p} إجمالي_كتل={len(tree.get('companies') or [])}",
            ms,
        )
    except Exception as e:
        report.add("قراءة قائمة المراقبة من Odoo", False, str(e))

    if person_id:
        try:
            def _upd():
                return odoo.update_partner(person_id, function="مدير اختبار محدّث", phone="+97300000000")
            ur, ms = _timed(_upd)
            report.add("تعديل شخصية في CRM", bool(ur.get("ok")), json.dumps(ur.get("written"), ensure_ascii=False), ms)
        except Exception as e:
            report.add("تعديل شخصية في CRM", False, str(e))

    try:
        r1 = odoo.remove_from_monitor(person_id or 0, confirm=False)
        r2 = odoo.delete_partner(person_id or 0, confirm=False)
        ok = r1.get("need_confirm") and r2.get("need_confirm")
        report.add("بوابة التأكيد (رفض بدون تأكيد)", bool(ok), "remove+delete require confirm")
    except Exception as e:
        report.add("بوابة التأكيد (رفض بدون تأكيد)", False, str(e))

    try:
        from agent.monitors.news_monitor import NewsMonitor
        from agent.monitors.appointments_monitor import AppointmentsMonitor
        from agent.monitors.legislation_monitor import LegislationMonitor
        from agent.monitors.social_monitor import SocialMonitor
        n, a, l, s = NewsMonitor(odoo), AppointmentsMonitor(odoo), LegislationMonitor(odoo), SocialMonitor(odoo)
        report.add("تهيئة وحدات المراقبة (بدون Claude)", True, f"social_active={s.active}")
    except Exception as e:
        report.add("تهيئة وحدات المراقبة (بدون Claude)", False, str(e))

    try:
        day = os.getenv("WEEKLY_REPORT_DAY", "sunday")
        hour = os.getenv("WEEKLY_REPORT_HOUR", "8")
        report.add("قراءة إعدادات الجدول", True, f"day={day} hour={hour} tz={os.getenv('TIMEZONE','Asia/Bahrain')}")
    except Exception as e:
        report.add("قراءة إعدادات الجدول", False, str(e))

    if keep_records:
        report.add("تنظيف سجلات الاختبار", True, f"تم الإبقاء --keep company={company_id} person={person_id}")
    else:
        try:
            def _clean():
                out = {}
                if company_id:
                    out["company"] = odoo.delete_partner(company_id, confirm=True, cascade_people=True)
                elif person_id:
                    out["person"] = odoo.delete_partner(person_id, confirm=True)
                return out
            cr, ms = _timed(_clean)
            ok = True
            if company_id and not (cr.get("company") or {}).get("ok"):
                ok = False
            report.add("تنظيف سجلات الاختبار (حذف مؤكد)", ok, json.dumps(cr, ensure_ascii=False, default=str)[:400], ms)
        except Exception as e:
            report.add("تنظيف سجلات الاختبار (حذف مؤكد)", False, str(e))

    report.finished = datetime.now().isoformat()
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Odoo AI Agent smoke test (0 Claude tokens)")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--with-rss", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = run_smoke(keep_records=args.keep, skip_network_search=not args.with_rss)
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(report.format_ar())
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
