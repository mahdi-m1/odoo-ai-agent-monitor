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
    except Exception as e:
        logger.exception("Daily scan failed: %s", e)


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
    logger.info(
        "Scheduler up. Weekly=%s %02d:00 daily_scan=%02d:00 tz=%s now=%s",
        cron_day, hour, daily_hour, tz, datetime.now().isoformat(),
    )
    scheduler.start()


if __name__ == "__main__":
    main()
