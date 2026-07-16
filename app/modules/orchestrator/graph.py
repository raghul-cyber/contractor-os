import logging
from typing import TypedDict, List
from datetime import datetime
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type
from langgraph.graph import StateGraph, START, END

from sqlalchemy import select
from app.core.db import get_session
from app.core.config_loader import get_config
from app.core.models import Run, ActivityLog, Lead

# Try importing module runners. If they don't exist yet, we can stub them.
try:
    from app.modules.hunter.run import run_hunter
except ImportError:
    async def run_hunter(state): return state
try:
    from app.modules.profiler.run import run_profiler
except ImportError:
    async def run_profiler(state): return state
try:
    from app.modules.craft.run import run_craft
except ImportError:
    async def run_craft(state): return state
try:
    from app.modules.outreach.run import run_outreach
except ImportError:
    async def run_outreach(state): return state

logger = logging.getLogger(__name__)

class PipelineState(TypedDict):
    run_id: int
    batch_size: int
    lead_ids: List[int]
    errors: int
    hunter_processed: int
    profiler_processed: int
    profiler_successful: int
    craft_processed: int
    craft_successful: int
    outreach_processed: int
    outreach_sent: int

# Retry decorator: 3 attempts, delays approx 2s, 4s, 8s
def resilient_node(func, failure_suffix):
    @retry(wait=wait_exponential(multiplier=2, min=2, max=8), stop=stop_after_attempt(3), reraise=True)
    async def retry_wrapper(state):
        return await func(state)
        
    async def node_wrapper(state: PipelineState):
        try:
            return await retry_wrapper(state)
        except Exception as e:
            logger.error(f"Node {func.__name__} failed unrecoverably: {e}")
            async with get_session() as session:
                for lid in state.get("lead_ids", []):
                    # Find lead and update status to *_FAILED
                    lead_res = await session.execute(select(Lead).where(Lead.id == lid))
                    lead = lead_res.scalar_one_or_none()
                    if lead:
                        lead.status = failure_suffix
                    session.add(ActivityLog(
                        lead_id=lid,
                        actor="orchestrator",
                        action=f"Node {func.__name__} failed",
                        detail=str(e)
                    ))
                await session.commit()
            state["errors"] = state.get("errors", 0) + 1
            return state
            
    return node_wrapper

async def crm_sync_node(state: PipelineState):
    async with get_session() as session:
        run_res = await session.execute(select(Run).where(Run.id == state["run_id"]))
        run = run_res.scalar_one_or_none()
        if run:
            run.completed_at = datetime.utcnow().isoformat()
            run.errors = state.get("errors", 0)
            run.leads_scraped = state.get("hunter_processed", 0)
            run.leads_researched = state.get("profiler_successful", 0)
            run.emails_sent = state.get("outreach_sent", 0)
            await session.commit()
    return state

def build_graph():
    workflow = StateGraph(PipelineState)
    
    workflow.add_node("hunt", resilient_node(run_hunter, "HUNT_FAILED"))
    workflow.add_node("profile", resilient_node(run_profiler, "PROFILE_FAILED"))
    workflow.add_node("craft", resilient_node(run_craft, "CRAFT_FAILED"))
    workflow.add_node("outreach", resilient_node(run_outreach, "OUTREACH_FAILED"))
    workflow.add_node("crm_sync", crm_sync_node)
    
    workflow.add_edge(START, "hunt")
    workflow.add_edge("hunt", "profile")
    workflow.add_edge("profile", "craft")
    workflow.add_edge("craft", "outreach")
    workflow.add_edge("outreach", "crm_sync")
    workflow.add_edge("crm_sync", END)
    
    return workflow.compile()

async def run_full_cycle():
    logger.info("Starting full orchestrator cycle")
    config = get_config()
    batch_size = getattr(config.system, "batch_size", 5)
    
    async with get_session() as session:
        run = Run()
        session.add(run)
        await session.commit()
        run_id = run.id
        
    state: PipelineState = {
        "run_id": run_id,
        "batch_size": batch_size,
        "lead_ids": [],
        "errors": 0,
        "hunter_processed": 0,
        "profiler_processed": 0,
        "profiler_successful": 0,
        "craft_processed": 0,
        "craft_successful": 0,
        "outreach_processed": 0,
        "outreach_sent": 0
    }
    
    app = build_graph()
    try:
        final_state = await app.ainvoke(state)
        logger.info(f"Cycle {run_id} finished with {final_state.get('errors', 0)} errors.")
    except Exception as e:
        logger.error(f"Graph execution failed completely: {e}")
