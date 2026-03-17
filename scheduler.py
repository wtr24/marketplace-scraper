"""APScheduler-based job scheduler — fires every 5 minutes per active job."""
import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

# Module-level scheduler instance (shared with main.py)
scheduler = AsyncIOScheduler(timezone="UTC")
_db = None
_broadcast_fn = None  # optional: async fn(event_dict) for WebSocket push


def init_scheduler(db, broadcast_fn=None):
    """Call once at startup, passing the Database instance."""
    global _db, _broadcast_fn
    _db = db
    _broadcast_fn = broadcast_fn


def start():
    if not scheduler.running:
        scheduler.add_job(
            _run_all_active_jobs,
            trigger=IntervalTrigger(minutes=5, timezone="UTC"),
            id="scraper_main",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.start()
        logger.info("Scheduler started — polling every 5 minutes")


def stop():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")


def next_run_time() -> str | None:
    job = scheduler.get_job("scraper_main")
    if job and job.next_run_time:
        return job.next_run_time.isoformat()
    return None


async def run_job_now(job_id: int):
    """Manually trigger a single job immediately (called from API)."""
    if _db is None:
        raise RuntimeError("Scheduler not initialised — call init_scheduler() first")

    job = await _db.get_job(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")

    asyncio.create_task(_execute_job(job))


async def _run_all_active_jobs():
    if _db is None:
        return

    jobs = await _db.get_jobs(active_only=True)
    if not jobs:
        logger.debug("No active jobs to run")
        return

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    due = []
    for job in jobs:
        interval = job.interval_minutes or 60
        if job.last_run is None:
            due.append(job)
        else:
            elapsed = (now - job.last_run).total_seconds() / 60
            if elapsed >= interval:
                due.append(job)
            else:
                logger.debug(
                    f"[scheduler] job={job.id} skipped — "
                    f"{elapsed:.1f}m elapsed, interval={interval}m"
                )

    if not due:
        logger.debug("No jobs due to run")
        return

    logger.info(f"Running {len(due)}/{len(jobs)} due job(s)")
    tasks = [_execute_job(job) for job in due]
    await asyncio.gather(*tasks, return_exceptions=True)


async def _execute_job(job):
    from scrapers import get_engine

    sites = json.loads(job.sites) if isinstance(job.sites, str) else job.sites
    engine = get_engine(job.engine)

    for site in sites:
        start = datetime.utcnow()
        try:
            logger.info(f"[scheduler] job={job.id} engine={job.engine} site={site} term='{job.search_term}'")
            result = await engine.scrape(site, job.search_term)

            items_new = await _db.upsert_listings(result.listings, job_id=job.id)

            sr = await _db.save_scraper_result({
                "job_id": job.id,
                "engine": job.engine,
                "site": site,
                "run_at": start,
                "duration_ms": result.duration_ms,
                "items_found": len(result.listings),
                "items_new": items_new,
                "success": result.success,
                "error_message": result.error_message,
                "memory_mb": result.memory_mb,
            })

            logger.info(
                f"[scheduler] job={job.id} site={site}: "
                f"found={len(result.listings)} new={items_new} "
                f"time={result.duration_ms}ms"
            )

            if _broadcast_fn and items_new > 0:
                await _broadcast_fn({
                    "type": "new_listings",
                    "job_id": job.id,
                    "site": site,
                    "count": items_new,
                })

        except Exception as exc:
            logger.error(f"[scheduler] job={job.id} site={site} error: {exc}", exc_info=True)
            await _db.save_scraper_result({
                "job_id": job.id,
                "engine": job.engine,
                "site": site,
                "run_at": start,
                "duration_ms": 0,
                "items_found": 0,
                "items_new": 0,
                "success": False,
                "error_message": str(exc),
            })

    await _db.mark_job_run(job.id)
