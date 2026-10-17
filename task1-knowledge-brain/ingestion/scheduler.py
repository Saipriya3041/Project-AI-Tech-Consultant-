import logging
import os
import time

from ingestion.crawler import run_ingestion


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


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    run_daily(int(os.getenv("INGESTION_INTERVAL_SECONDS", "86400")))