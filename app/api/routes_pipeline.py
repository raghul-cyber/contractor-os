from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.db import get_session
from app.core.models import PipelineStage, Lead
from app.modules.crm.pipeline_api import book_meeting, send_proposal, mark_won, mark_lost
from sqlalchemy import select

router = APIRouter(prefix="/api/leads", tags=["pipeline"])

class PipelineActionRequest(BaseModel):
    stage: str
    contract_value: Optional[float] = None
    notes: Optional[str] = None

@router.post("/{lead_id}/pipeline")
async def update_pipeline_stage(lead_id: int, request: PipelineActionRequest):
    async with get_session() as session:
        # Validate lead
        lead_res = await session.execute(select(Lead).where(Lead.id == lead_id))
        if not lead_res.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Lead not found")

        try:
            if request.stage == PipelineStage.MEETING_BOOKED.value:
                await book_meeting(lead_id, request.notes, session)
            elif request.stage == PipelineStage.PROPOSAL_SENT.value:
                await send_proposal(lead_id, request.contract_value or 0.0, request.notes, session)
            elif request.stage == PipelineStage.WON.value:
                await mark_won(lead_id, request.contract_value or 0.0, session)
            elif request.stage == PipelineStage.LOST.value:
                await mark_lost(lead_id, request.notes, session)
            else:
                raise HTTPException(status_code=400, detail=f"Unsupported manual stage transition: {request.stage}")
                
            await session.commit()
            return {"status": "success", "stage": request.stage}
        except Exception as e:
            await session.rollback()
            raise HTTPException(status_code=500, detail=str(e))
