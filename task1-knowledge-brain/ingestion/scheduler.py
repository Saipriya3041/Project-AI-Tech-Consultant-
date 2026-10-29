import logging
import os
import time

from app.auth import list_due_sitemaps, mark_sitemap_checked
from ingestion.crawler import finalize_sitemap_scan, run_ingestion


logger = logging.getLogger(__name__)


def run_daily_pass() -> dict:
    while True:
        report = run_ingestion(limit=1000)
        logger.info("Knowledge ingestion batch: %s", report)
        if report["complete"] or report["failed"] or report["disallowed"]:
            return report


def run_daily(interval_seconds: int = 86400) -> None:
    while True:
        try:
            report = run_daily_pass()
            logger.info("Daily knowledge ingestion pass ended: %s", report)
        except Exception:
            logger.exception("Daily knowledge ingestion failed; retrying after the configured interval")
        time.sleep(max(60, interval_seconds))


def run_scheduled_pass() -> list[dict]:
    reports = []
    seen_sitemap_urls: set[str] = set()
    for source in list_due_sitemaps():
        mark_sitemap_checked(source["id"])
        try:
            report = run_ingestion(
                limit=None,
                sitemap_url=source["url"],
                deactivate_missing=False,
                refresh_existing=True,
                exclude_urls=seen_sitemap_urls,
            )
            seen_sitemap_urls.update(report.get("sitemap_urls", []))
            report = finalize_sitemap_scan(
                source["id"],
                report,
                source["retention_checks"],
            )
            report["sitemap_url"] = source["url"]
            report["schedule"] = source["schedule"]
            reports.append(report)
            logger.info("Scheduled sitemap recheck completed: %s", report)
        except Exception:
            logger.exception("Scheduled sitemap recheck failed for %s", source["url"])
            reports.append({
                "sitemap_url": source["url"],
                "schedule": source["schedule"],
                "complete": False,
                "error": "Sitemap recheck failed; see service log",
            })
    return reports


def run_scheduler(poll_interval_seconds: int = 60) -> None:
    while True:
        try:
            run_scheduled_pass()
        except Exception:
            logger.exception("Scheduled sitemap polling failed")
        time.sleep(max(10, poll_interval_seconds))


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    run_scheduler(int(os.getenv("SITEMAP_SCHEDULE_POLL_SECONDS", "60")))