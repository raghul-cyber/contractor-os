from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.models import Pipeline, PipelineStage, ActivityLog

async def _update_pipeline_stage(lead_id: int, stage: str, notes: str, contract_value: float, session: AsyncSession):
    pipe_res = await session.execute(select(Pipeline).where(Pipeline.lead_id == lead_id))
    pipeline = pipe_res.scalar_one_or_none()
    
    if pipeline:
        pipeline.stage = stage
        if notes is not None:
            pipeline.notes = notes
        if contract_value is not None:
            pipeline.contract_value = contract_value
    else:
        pipeline = Pipeline(
            lead_id=lead_id, 
            stage=stage,
            notes=notes,
            contract_value=contract_value
        )
        session.add(pipeline)
        
    return pipeline

async def book_meeting(lead_id: int, notes: str, session: AsyncSession):
    await _update_pipeline_stage(lead_id, PipelineStage.MEETING_BOOKED.value, notes, None, session)
    session.add(ActivityLog(lead_id=lead_id, actor="manual", action="Meeting booked", detail=notes))

async def send_proposal(lead_id: int, contract_value: float, notes: str, session: AsyncSession):
    await _update_pipeline_stage(lead_id, PipelineStage.PROPOSAL_SENT.value, notes, contract_value, session)
    session.add(ActivityLog(lead_id=lead_id, actor="manual", action="Proposal sent", detail=f"Value: {contract_value}. {notes}"))

async def mark_won(lead_id: int, contract_value: float, session: AsyncSession):
    await _update_pipeline_stage(lead_id, PipelineStage.WON.value, None, contract_value, session)
    session.add(ActivityLog(lead_id=lead_id, actor="manual", action="Marked won", detail=f"Value: {contract_value}"))

async def mark_lost(lead_id: int, notes: str, session: AsyncSession):
    await _update_pipeline_stage(lead_id, PipelineStage.LOST.value, notes, None, session)
    session.add(ActivityLog(lead_id=lead_id, actor="manual", action="Marked lost", detail=notes))
