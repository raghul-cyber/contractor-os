import os
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from app.core.logger import get_logger

logger = get_logger(__name__)

def _get_sync_db_url():
    # APScheduler 3.x SQLAlchemyJobStore requires a sync engine.
    # Convert sqlite+aiosqlite:///... to sqlite:///...
    url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/contractor_os.db")
    return url.replace("sqlite+aiosqlite", "sqlite")

def get_scheduler() -> AsyncIOScheduler:
    jobstores = {
        'default': SQLAlchemyJobStore(url=_get_sync_db_url(), tablename='apscheduler_jobs')
    }
    scheduler = AsyncIOScheduler(jobstores=jobstores)
    return scheduler

# Stubs for jobs
async def run_full_cycle():
    from app.modules.orchestrator.graph import run_full_cycle as orchestrator_run
    await orchestrator_run()

async def check_due_followups():
    # Will be imported and replaced, this is just a fallback stub if not provided
    from app.modules.outreach.run import check_due_followups_job
    await check_due_followups_job()

async def poll_inbox():
    from app.modules.outreach.reply_detector import poll_inbox_job
    await poll_inbox_job()

async def run_daily_digest():
    from app.modules.crm.digest import run_daily_digest as crm_digest
    from app.core.db import get_session
    async with get_session() as session:
        await crm_digest(session)


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
    
    logger.info("Scheduler jobs registered.")
