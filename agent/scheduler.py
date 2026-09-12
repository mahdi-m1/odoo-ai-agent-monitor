"""
Weekly + daily monitoring scheduler.
Reads config/schedule.env overrides if present.
Run: python -m agent.scheduler
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

from agent import backup as backup_mod
from agent import push as push_mod
from agent.claude_cli import ClaudeCLI
from agent.memory import get_memory
from agent.reports.weekly_report import WeeklyReportGenerator
from agent.monitors.full_cycle import run_full_monitoring
from agent.tools.odoo_tools import OdooTools

load_dotenv()
SCHEDULE_FILE = Path(__file__).resolve().parent.parent / "config" / "schedule.env"
if SCHEDULE_FILE.exists():
    load_dotenv(SCHEDULE_FILE, override=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("scheduler")


def job_weekly_report():
    logger.info("Weekly report job...")
    result = WeeklyReportGenerator().run_full_cycle()
    logger.info("Weekly report done: %s", result)


def job_daily_scan():
    logger.info("Full monitoring cycle (news/deals/appointments/legislation/social)...")
    try:
        result = run_full_monitoring(OdooTools())
        logger.info("Daily full scan: %s", result)
        _notify_new_events(result)
    except Exception as e:
        logger.exception("Daily scan failed: %s", e)


def _notify_new_events(result: dict) -> None:
    """Push a notification summarizing genuinely-new monitoring hits (deduped by the monitors)."""
    try:
        steps = (result or {}).get("steps", {})
        news = steps.get("news_deals_finance", {}).get("new", 0) or 0
        appts = steps.get("appointments_promotions", {}).get("new", 0) or 0
        legis = steps.get("legislation", {}).get("documents_new", 0) or 0
        total = news + appts + legis
        if total <= 0:
            return
        parts = []
        if appts: parts.append(f"{appts} تعيين/ترقية")
        if news: parts.append(f"{news} خبر")
        if legis: parts.append(f"{legis} وثيقة تشريعية")
        push_mod.send_push("رصد جديد من الوكيل", "، ".join(parts), url="/dashboard", tag="scan", throttle_s=300)
    except Exception as e:
        logger.warning("push notify failed: %s", e)


def job_backup_tick():
    """Every few minutes: run a backup if the user's schedule says one is due (settings can change without restart)."""
    try:
        if backup_mod.is_due():
            logger.info("Scheduled backup due...")
            result = backup_mod.create_backup()
            logger.info("Backup: %s", {k: result.get(k) for k in ("file", "bytes", "drive", "warning")})
    except Exception as e:
        logger.exception("Backup tick failed: %s", e)


def job_memory_consolidate():
    logger.info("Memory consolidation...")
    try:
        logger.info("Consolidation: %s", get_memory().consolidate(claude=ClaudeCLI()))
    except Exception as e:
        logger.exception("Consolidation failed: %s", e)


def main():
    day = os.getenv("WEEKLY_REPORT_DAY", "sunday").lower()
    hour = int(os.getenv("WEEKLY_REPORT_HOUR", "8"))
    daily_hour = int(os.getenv("DAILY_SCAN_HOUR", "7"))
    tz = os.getenv("TIMEZONE", "Asia/Bahrain")
    day_map = {
        "monday": "mon", "tuesday": "tue", "wednesday": "wed",
        "thursday": "thu", "friday": "fri", "saturday": "sat", "sunday": "sun",
    }
    cron_day = day_map.get(day, "sun")
    scheduler = BlockingScheduler(timezone=tz)
    scheduler.add_job(
        job_weekly_report,
        CronTrigger(day_of_week=cron_day, hour=hour, minute=0),
        id="weekly_report",
        replace_existing=True,
    )
    scheduler.add_job(
        job_daily_scan,
        CronTrigger(hour=daily_hour, minute=0),
        id="daily_scan",
        replace_existing=True,
    )
    scheduler.add_job(job_backup_tick, "interval", minutes=5, id="backup_tick", replace_existing=True)
    scheduler.add_job(job_memory_consolidate, CronTrigger(hour=3, minute=30), id="memory_consolidate", replace_existing=True)
    logger.info(
        "Scheduler up. Weekly=%s %02d:00 daily_scan=%02d:00 tz=%s now=%s",
        cron_day, hour, daily_hour, tz, datetime.now().isoformat(),
    )
    scheduler.start()


if __name__ == "__main__":
    main()
