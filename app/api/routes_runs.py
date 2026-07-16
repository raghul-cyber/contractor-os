from datetime import datetime, date
from fastapi import APIRouter, HTTPException, BackgroundTasks
from sqlalchemy import select, func

from app.core.db import get_session
from app.core.models import Run, Lead, EmailEvent, Pipeline
from app.modules.orchestrator.graph import run_full_cycle

router = APIRouter(tags=["runs"])

@router.post("/api/run/trigger")
async def trigger_run(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_full_cycle)
    return {"status": "started", "message": "Orchestrator cycle triggered in background"}

@router.get("/api/runs/{run_id}")
async def get_run(run_id: int):
    async with get_session() as session:
        result = await session.execute(select(Run).where(Run.id == run_id))
        run = result.scalar_one_or_none()
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
            
        return {
            "id": run.id,
            "started_at": run.started_at,
            "completed_at": run.completed_at,
            "leads_scraped": run.leads_scraped,
            "leads_researched": run.leads_researched,
            "emails_sent": run.emails_sent,
            "replies_received": run.replies_received,
            "errors": run.errors
        }

@router.get("/api/stats/today")
async def get_stats_today():
    today_str = datetime.utcnow().date().isoformat()
    
    async with get_session() as session:
        # Leads added today
        leads_added = await session.execute(
            select(func.count(Lead.id)).where(Lead.created_at >= today_str)
        )
        
        # Emails sent today
        emails_sent = await session.execute(
            select(func.count(EmailEvent.id))
            .where(EmailEvent.event_type == "sent")
            .where(EmailEvent.timestamp >= today_str)
        )
        
        # Replies today
        replies_received = await session.execute(
            select(func.count(EmailEvent.id))
            .where(EmailEvent.event_type == "replied")
            .where(EmailEvent.timestamp >= today_str)
        )
        
        # Active Deals (in pipeline, not WON/LOST/PAUSED)
        active_deals_result = await session.execute(
            select(func.count(Pipeline.id))
            .where(Pipeline.stage.notin_(["WON", "LOST", "PAUSED"]))
        )
        
        # Total Pipeline Value (active deals only)
        pipeline_val_result = await session.execute(
            select(func.sum(Pipeline.contract_value))
            .where(Pipeline.stage.notin_(["WON", "LOST", "PAUSED"]))
        )
        
        return {
            "leads_added_today": leads_added.scalar_one() or 0,
            "emails_sent_today": emails_sent.scalar_one() or 0,
            "replies_received_today": replies_received.scalar_one() or 0,
            "active_deals": active_deals_result.scalar_one() or 0,
            "pipeline_value": pipeline_val_result.scalar_one() or 0.0
        }
