import os
import asyncio
import sqlite3
import functools

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from sqlalchemy import event as sa_event
from app.core.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Retry decorator — scoped to scheduler-triggered write paths only
# ---------------------------------------------------------------------------
RETRY_DELAYS = [0.1, 0.3, 0.9]  # 100ms, 300ms, 900ms


def with_db_retry(func):
    """
    Lightweight retry wrapper for the specific case of
    `sqlite3.OperationalError: database is locked`.

    3 attempts with backoff (100ms, 300ms, 900ms).
    Only applied to APScheduler job callbacks that perform writes —
    NOT blanket-wrapped around every DB call.
    """

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        last_exc = None
        for attempt, delay in enumerate(RETRY_DELAYS, start=1):
            try:
                return await func(*args, **kwargs)
            except Exception as exc:
                # Check if it's the specific "database is locked" error.
                # aiosqlite wraps sqlite3.OperationalError, so check the chain.
                is_locked = False
                check = exc
                while check is not None:
                    if isinstance(check, sqlite3.OperationalError) and "database is locked" in str(check):
                        is_locked = True
                        break
                    check = getattr(check, "__cause__", None) or getattr(check, "__context__", None)
                    if check is exc:
                        break  # avoid infinite loop

                if not is_locked:
                    raise  # Not a lock error — don't retry

                last_exc = exc
                logger.warning(
                    f"[db_retry] {func.__name__} hit 'database is locked' "
                    f"(attempt {attempt}/{len(RETRY_DELAYS)}), retrying in {delay}s..."
                )
                await asyncio.sleep(delay)

        # Exhausted all retries
        logger.error(f"[db_retry] {func.__name__} failed after {len(RETRY_DELAYS)} retries")
        raise last_exc

    return wrapper


# ---------------------------------------------------------------------------
# Sync DB URL for APScheduler's SQLAlchemyJobStore
# ---------------------------------------------------------------------------
def _get_sync_db_url():
    # APScheduler 3.x SQLAlchemyJobStore requires a sync engine.
    # Convert sqlite+aiosqlite:///... to sqlite:///...
    url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/contractor_os.db")
    return url.replace("sqlite+aiosqlite", "sqlite")


def _set_sqlite_pragmas_sync(dbapi_connection, connection_record):
    """PRAGMAs for the APScheduler jobstore's own sync engine."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA synchronous=NORMAL;")
    cursor.execute("PRAGMA busy_timeout=5000;")
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.close()


def get_scheduler() -> AsyncIOScheduler:
    jobstore = SQLAlchemyJobStore(url=_get_sync_db_url(), tablename='apscheduler_jobs')
    # Hook PRAGMAs onto the jobstore's engine so its connections also use WAL/busy_timeout
    sa_event.listen(jobstore.engine, "connect", _set_sqlite_pragmas_sync)
    jobstores = {'default': jobstore}
    scheduler = AsyncIOScheduler(jobstores=jobstores)
    return scheduler


# ---------------------------------------------------------------------------
# Job functions — scheduler-triggered write paths get @with_db_retry
# ---------------------------------------------------------------------------
@with_db_retry
async def run_full_cycle():
    from app.modules.orchestrator.graph import run_full_cycle as orchestrator_run
    await orchestrator_run()


@with_db_retry
async def check_due_followups():
    # Will be imported and replaced, this is just a fallback stub if not provided
    from app.modules.outreach.run import check_due_followups_job
    await check_due_followups_job()


@with_db_retry
async def poll_inbox():
    from app.modules.outreach.reply_detector import poll_inbox_job
    await poll_inbox_job()


async def run_daily_digest():
    from app.modules.crm.digest import run_daily_digest as crm_digest
    from app.core.db import get_session
    async with get_session() as session:
        await crm_digest(session)


async def run_daily_backup():
    """APScheduler job: run the online backup + retention cleanup."""
    from scripts.backup_db import run_backup
    run_backup()


@with_db_retry
async def run_reddit_signals():
    from app.modules.signals.reddit_listener import poll_reddit_signals
    from app.core.db import get_session
    async with get_session() as session:
        await poll_reddit_signals(session)

def register_jobs(scheduler: AsyncIOScheduler, cfg):
    """
    Registers the required recurring jobs.
    Uses replace_existing=True to avoid duplicate registrations on restart.
    """
    cycle_interval = cfg.system.cycle_interval_hours if hasattr(cfg.system, 'cycle_interval_hours') else 6

    scheduler.add_job(
        run_full_cycle,
        'interval',
        hours=cycle_interval,
        id='main_cycle',
        replace_existing=True
    )

    scheduler.add_job(
        check_due_followups,
        'interval',
        hours=1,
        id='followup_check',
        replace_existing=True
    )

    scheduler.add_job(
        poll_inbox,
        'interval',
        hours=2,
        id='inbox_poll',
        replace_existing=True
    )

    scheduler.add_job(
        run_daily_digest,
        'cron',
        hour=8,
        id='daily_digest',
        replace_existing=True
    )

    scheduler.add_job(
        run_daily_backup,
        'cron',
        hour=2,
        minute=0,
        id='daily_backup',
        replace_existing=True
    )

    if hasattr(cfg.system, "signals") and cfg.system.signals and cfg.system.signals.reddit.enabled:
        scheduler.add_job(
            run_reddit_signals,
            'interval',
            minutes=cfg.system.signals.reddit.poll_interval_minutes,
            id='reddit_signals',
            replace_existing=True
        )

    logger.info("Scheduler jobs registered (including daily_backup at 02:00).")
