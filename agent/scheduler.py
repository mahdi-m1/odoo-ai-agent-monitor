"""
Weekly report scheduler using APScheduler.
Run: python -m agent.scheduler
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

from agent.reports.weekly_report import WeeklyReportGenerator
from agent.monitors.news_monitor import NewsMonitor
from agent.monitors.appointments_monitor import AppointmentsMonitor
from agent.tools.odoo_tools import OdooTools

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("scheduler")


def job_weekly_report():
    logger.info("Starting weekly report job...")
    gen = WeeklyReportGenerator()
    result = gen.run_full_cycle()
    logger.info("Weekly report done: %s", result)


def job_daily_scan():
    logger.info("Starting daily news + appointments scan...")
    odoo = OdooTools()
    try:
        n = NewsMonitor(odoo).run_and_log(limit_entities=25)
        a = AppointmentsMonitor(odoo).run_and_log()
        logger.info("Daily scan: news=%s appointments=%s", n, a)
    except Exception as e:
        logger.exception("Daily scan failed: %s", e)


def main():
    day = os.getenv("WEEKLY_REPORT_DAY", "sunday").lower()
    hour = int(os.getenv("WEEKLY_REPORT_HOUR", "8"))
    tz = os.getenv("TIMEZONE", "Asia/Bahrain")

    day_map = {
        "monday": "mon",
        "tuesday": "tue",
        "wednesday": "wed",
        "thursday": "thu",
        "friday": "fri",
        "saturday": "sat",
        "sunday": "sun",
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
        CronTrigger(hour=7, minute=0),
        id="daily_scan",
        replace_existing=True,
    )

    logger.info(
        "Scheduler started. Weekly: %s %02d:00 (%s). Daily scan: 07:00. Now=%s",
        cron_day,
        hour,
        tz,
        datetime.now().isoformat(),
    )
    scheduler.start()


if __name__ == "__main__":
    main()
