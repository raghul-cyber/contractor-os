import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.core.logger import get_logger
from app.core.config_loader import start_config_watcher, get_config
from app.core.scheduler import get_scheduler, register_jobs
from app.api import routes_leads, routes_pipeline, routes_runs, routes_ui

logger = get_logger(__name__)

# Global instances
scheduler = get_scheduler()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Start Config Watcher
    logger.info("Starting config watcher")
    start_config_watcher()
    
    # 2. Register & Start Scheduler
    logger.info("Starting APScheduler")
    cfg = get_config()
    register_jobs(scheduler, cfg)
    scheduler.start()
    
    yield
    
    # Shutdown
    logger.info("Shutting down APScheduler")
    scheduler.shutdown(wait=False)


app = FastAPI(
    title="ContractorOS Dashboard",
    lifespan=lifespan
)

# Include Routers
app.include_router(routes_ui.router)
app.include_router(routes_leads.router)
app.include_router(routes_pipeline.router)
app.include_router(routes_runs.router)
