import os
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel

from app.core.db import get_session
from app.modules.hunter.run import run_hunter
from app.modules.profiler.run import run_profiler
from app.modules.craft.run import run_craft
from app.modules.outreach.run import run_outreach
from app.modules.crm.digest import run_daily_digest

router = APIRouter(prefix="/api/pipeline", tags=["orchestration"])

def verify_api_key(x_api_key: Optional[str] = Header(None)):
    secret = os.environ.get("CONTRACTOR_API_SECRET")
    if secret and x_api_key != secret:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    elif not secret and x_api_key:
        raise HTTPException(status_code=401, detail="API Key provided but not configured on server")
    
    if not secret:
        raise HTTPException(status_code=500, detail="CONTRACTOR_API_SECRET is not configured on the server")
    return x_api_key

class RunRequest(BaseModel):
    lead_ids: Optional[List[int]] = None

@router.post("/hunter/run")
async def api_run_hunter(api_key: str = Depends(verify_api_key)):
    state = await run_hunter({})
    return {
        "status": "success",
        "processed": state.get("hunter_processed", 0),
        "successful": state.get("hunter_successful", 0)
    }

@router.post("/profiler/run")
async def api_run_profiler(req: RunRequest = None, api_key: str = Depends(verify_api_key)):
    state_in = {}
    if req and req.lead_ids:
        state_in["lead_ids"] = req.lead_ids
    state = await run_profiler(state_in)
    return {
        "status": "success",
        "processed": state.get("profiler_processed", 0),
        "successful": state.get("profiler_successful", 0)
    }

@router.post("/craft/run")
async def api_run_craft(req: RunRequest = None, api_key: str = Depends(verify_api_key)):
    state_in = {}
    if req and req.lead_ids:
        state_in["lead_ids"] = req.lead_ids
    state = await run_craft(state_in)
    return {
        "status": "success",
        "processed": state.get("craft_processed", 0),
        "successful": state.get("craft_successful", 0)
    }

@router.post("/outreach/run")
async def api_run_outreach(api_key: str = Depends(verify_api_key)):
    state = await run_outreach({})
    return {
        "status": "success",
        "emails_sent": state.get("emails_sent", 0)
    }

@router.post("/crm/digest")
async def api_run_digest(api_key: str = Depends(verify_api_key)):
    async with get_session() as session:
        await run_daily_digest(session)
    return {"status": "success", "message": "Daily digest sent"}
